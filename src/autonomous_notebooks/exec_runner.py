"""Execute code on a kernel. Capture outputs. Stream to disk for cell execs."""

import queue
import time
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
    interrupt_fn: Callable[[], None] | None = None,
    recover_fn: Callable[[], BlockingKernelClient] | None = None,
) -> list:
    """Execute `code` on `client`, return list of nbformat output dicts.

    `timeout` is a **wall-clock deadline** for the whole execution, not the gap
    between messages — a cell that streams output continuously still gets
    killed on schedule. When the deadline fires, `interrupt_fn` is called (if
    given) to tell the kernel to stop, and a timeout marker is appended.

    `recover_fn`, if given, is called when iopub parsing throws unexpectedly
    (ZMQ framing desync under heavy output). It should rebuild the client's
    channels without killing the kernel and return the new client. We allow
    up to 3 such recoveries before giving up.

    If `on_output` is provided, it's called after each new output with the
    full outputs list so far.
    """
    msg_id = client.execute(code)
    outputs: list = []
    deadline = time.monotonic() + timeout
    # Poll at least once per second so we notice the deadline even while the
    # cell is chatty (each get_iopub_msg returns quickly when messages stream).
    poll = 1.0
    max_recovers = 3
    recovers = 0

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            log.warning("cell execution exceeded %ds — interrupting kernel", timeout)
            if interrupt_fn is not None:
                try:
                    interrupt_fn()
                except Exception:
                    log.exception("interrupt_fn raised during timeout handling")
            outputs.append(
                nbformat.v4.new_output(
                    "stream",
                    name="stderr",
                    text=f"[nb mcp] [execution timed out after {timeout}s — kernel interrupted]",
                )
            )
            if on_output:
                on_output(outputs)
            break

        try:
            msg = client.get_iopub_msg(timeout=min(remaining, poll))
        except (TimeoutError, queue.Empty):
            # No message within the poll window — loop to check deadline.
            continue
        except Exception as exc:
            # Typically a ZMQ framing desync: ValueError('<IDS|MSG>' is not in list).
            # Under heavy iopub traffic one bad frame will keep firing unless we
            # rebuild channels. Kernel stays alive; we re-subscribe.
            recovers += 1
            log.warning("iopub read failed (%d/%d): %s", recovers, max_recovers, exc)
            if recover_fn is None or recovers > max_recovers:
                outputs.append(
                    nbformat.v4.new_output(
                        "stream",
                        name="stderr",
                        text=(
                            "[nb mcp] iopub desync — lost connection to kernel. "
                            "Output may be incomplete. The kernel is likely still "
                            "running; use exec_status / read_cell to check."
                        ),
                    )
                )
                if on_output:
                    on_output(outputs)
                break
            try:
                client = recover_fn()
                log.info("iopub recovered — resuming read loop for msg_id=%s", msg_id)
            except Exception:
                log.exception("channel recovery failed")
                outputs.append(
                    nbformat.v4.new_output(
                        "stream",
                        name="stderr",
                        text="[nb mcp] iopub desync — channel recovery failed",
                    )
                )
                if on_output:
                    on_output(outputs)
                break
            continue

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
    interrupt_fn: Callable[[], None] | None = None,
    recover_fn: Callable[[], BlockingKernelClient] | None = None,
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

    outputs = execute_code(
        client,
        source,
        timeout=timeout,
        on_output=_on_output,
        interrupt_fn=interrupt_fn,
        recover_fn=recover_fn,
    )
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
