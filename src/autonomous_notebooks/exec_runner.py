"""Execute code on a kernel. Capture outputs. Stream to disk for cell execs."""

import uuid
from collections.abc import Callable

import nbformat
from jupyter_client.blocking import BlockingKernelClient

from autonomous_notebooks._log import get_logger
from autonomous_notebooks.nb_io import (
    atomic_write_nb,
    find_cell_by_id,
    read_nb,
)

log = get_logger()


def write_cell_status(nb_path: str, idx: int, status: str) -> None:
    """Write an nb-mcp status marker as the cell's sole output on disk (stderr-styled)."""
    nb = read_nb(nb_path)
    cell = nb.cells[idx]
    cell["outputs"] = [nbformat.v4.new_output("stream", name="stderr", text=status)]
    cell["execution_count"] = None
    atomic_write_nb(nb, nb_path)


def mark_cells_status(nb_path: str, indices: list[int], status: str) -> None:
    """Write an nb-mcp status marker to multiple cells' outputs in a single disk write."""
    nb = read_nb(nb_path)
    for idx in indices:
        cell = nb.cells[idx]
        cell["outputs"] = [nbformat.v4.new_output("stream", name="stderr", text=status)]
        cell["execution_count"] = None
    atomic_write_nb(nb, nb_path)


def execute_code(
    client: BlockingKernelClient,
    code: str,
    timeout: int = 120,
    on_output: Callable[[list], None] | None = None,
) -> list:
    """Execute `code` on `client`, return list of nbformat output dicts.

    If `on_output` is provided, it's called after each new output with the
    full outputs list so far.
    """
    msg_id = client.execute(code)
    outputs: list = []

    while True:
        try:
            msg = client.get_iopub_msg(timeout=timeout)
        except TimeoutError:
            outputs.append(
                nbformat.v4.new_output(
                    "stream", name="stderr", text="[nb mcp] [execution timed out]"
                )
            )
            if on_output:
                on_output(outputs)
            break

        if msg["parent_header"].get("msg_id") != msg_id:
            continue

        msg_type = msg["msg_type"]
        content = msg["content"]
        new_output = None

        if msg_type == "stream":
            new_output = nbformat.v4.new_output(
                "stream", name=content["name"], text=content["text"]
            )
        elif msg_type in ("display_data", "update_display_data"):
            new_output = nbformat.v4.new_output(
                "display_data",
                data=content["data"],
                metadata=content.get("metadata", {}),
            )
        elif msg_type == "execute_result":
            new_output = nbformat.v4.new_output(
                "execute_result",
                data=content["data"],
                metadata=content.get("metadata", {}),
                execution_count=content["execution_count"],
            )
        elif msg_type == "error":
            new_output = nbformat.v4.new_output(
                "error",
                ename=content["ename"],
                evalue=content["evalue"],
                traceback=content["traceback"],
            )
        elif msg_type == "status" and content["execution_state"] == "idle":
            break

        if new_output is not None:
            outputs.append(new_output)
            if on_output:
                on_output(outputs)

    return outputs


def _with_running_header(outputs: list, running_header: str | None) -> list:
    """Return a disk-copy of outputs prefixed with an nb-mcp running banner (if any)."""
    if not running_header:
        return list(outputs)
    header = nbformat.v4.new_output("stream", name="stderr", text=running_header)
    return [header, *outputs]


def _flush_outputs_to_disk(
    nb_path: str,
    cell_id: str | None,
    outputs: list,
    *,
    set_execution_count: bool = False,
) -> None:
    """Re-read notebook, update the target cell's outputs, atomic-write."""
    nb = read_nb(nb_path)
    target = None
    if cell_id:
        hit = find_cell_by_id(nb, cell_id)
        if hit:
            target = hit[1]
    if target is None:
        log.warning(
            "could not find cell (id=%r) in %s; dropped %d outputs",
            cell_id,
            nb_path,
            len(outputs),
        )
        return
    target["outputs"] = list(outputs)
    if set_execution_count:
        for out in outputs:
            if out.get("execution_count"):
                target["execution_count"] = out["execution_count"]
                break
    atomic_write_nb(nb, nb_path)


def exec_cell_to_disk(
    client: BlockingKernelClient,
    nb_path: str,
    idx: int,
    timeout: int = 120,
    on_output: Callable[[list], None] | None = None,
    running_header: str | None = None,
) -> dict:
    """Run cell at idx, streaming outputs into the file. Returns summary dict.

    `on_output` is called (after the disk flush) on every new output, so callers
    can track activity timestamps for hang detection.

    `running_header`, if given, is written as a stderr-stream banner at the top
    of the cell's outputs for the duration of execution. The final flush removes
    it — callers then append their own completion footer.
    """
    nb = read_nb(nb_path)
    cell = nb.cells[idx]
    if cell["cell_type"] != "code":
        raise ValueError(f"cell {idx} is {cell['cell_type']}, not code")
    # Notebooks written outside the MCP may lack cell ids. Backfill one so
    # streaming flushes (which address cells by id) don't silently no-op.
    cell_id = cell.get("id")
    if not cell_id:
        cell_id = uuid.uuid4().hex[:8]
        cell["id"] = cell_id
        atomic_write_nb(nb, nb_path)
    source = cell["source"]

    # Surface the running banner before the kernel emits anything, so there's no
    # silent gap between the Queued marker and the first cell output.
    if running_header:
        _flush_outputs_to_disk(
            nb_path, cell_id, _with_running_header([], running_header)
        )

    def _on_output(outputs: list) -> None:
        _flush_outputs_to_disk(
            nb_path, cell_id, _with_running_header(outputs, running_header)
        )
        if on_output is not None:
            on_output(outputs)

    outputs = execute_code(client, source, timeout=timeout, on_output=_on_output)
    _flush_outputs_to_disk(nb_path, cell_id, outputs, set_execution_count=True)

    had_error = any(o.get("output_type") == "error" for o in outputs)
    exec_count = None
    for out in outputs:
        if out.get("execution_count"):
            exec_count = out["execution_count"]
            break
    return {
        "index": idx,
        "cell_id": cell_id,
        "outputs": outputs,
        "had_error": had_error,
        "execution_count": exec_count,
    }
