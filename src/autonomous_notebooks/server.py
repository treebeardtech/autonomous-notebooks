"""Stdio MCP server exposing notebook tools.

Read/edit tools are kernel-free — they operate on `.ipynb` files directly.
Exec tools fire background jobs that run on per-notebook threads. Kernels
live in-process and die with the server when Claude Code exits.
"""

import anyio
from mcp.server.fastmcp import FastMCP

from autonomous_notebooks import jobs, kernels, nb_io
from autonomous_notebooks._log import get_logger
from autonomous_notebooks.exec_runner import execute_code

log = get_logger()
mcp = FastMCP("autonomous-notebooks")


def _monitor_hint(job_id: str, notebook_path: str) -> str:
    """Ready-to-use Monitor command line so the agent can stream progress."""
    return (
        f"Tail progress with Claude Code's Monitor tool:\n"
        f"  Monitor(command='uv run nb watch --job {job_id} "
        f"--path {notebook_path}', description='nb job {job_id}')"
    )


def _exec_response(
    notebook_path: str, job: jobs.Job, block_for: int, headline: str
) -> str:
    """Block briefly for the job to finish — if it does, return status inline.
    Otherwise return the headline + Monitor hint so the agent can stream.
    """
    if block_for > 0 and job.thread is not None:
        job.thread.join(timeout=block_for)
    if jobs.get_active_job(notebook_path) is None:
        return f"{headline}\n\n{jobs.get_status(notebook_path)}"
    return f"{headline}\n\n{_monitor_hint(job.job_id, notebook_path)}"


# -- read --


@mcp.tool()
def list_cells(notebook_path: str) -> str:
    """List all cells in a notebook (compact, one line per cell with any outputs)."""
    nb_io.ensure_notebook(notebook_path)
    nb = nb_io.read_nb(notebook_path)
    return nb_io.list_cells_text(nb)


@mcp.tool()
def read_cell(
    notebook_path: str,
    index: int | None = None,
    cell_id: str | None = None,
) -> str:
    """Read one cell in full (source + outputs). Provide either index or cell_id."""
    nb_io.ensure_notebook(notebook_path)
    nb = nb_io.read_nb(notebook_path)
    idx = nb_io.resolve_index(nb, index=index, cell_id=cell_id)
    return nb_io.fmt_cell_full(idx, nb.cells[idx])


# -- edit --


@mcp.tool()
def insert_cell(
    notebook_path: str,
    index: int,
    source: str,
    markdown: bool = False,
) -> str:
    """Insert a cell at `index`. Existing cells shift down. Returns the new cell."""
    nb_io.ensure_notebook(notebook_path)
    nb = nb_io.read_nb(notebook_path)
    idx = nb_io.insert_cell(nb, index, source, markdown=markdown)
    nb_io.atomic_write_nb(nb, notebook_path)
    return nb_io.fmt_cell_full(idx, nb.cells[idx])


@mcp.tool()
def set_cell(
    notebook_path: str,
    source: str,
    index: int | None = None,
    cell_id: str | None = None,
    markdown: bool = False,
) -> str:
    """Overwrite a cell's source. Provide either index or cell_id."""
    nb_io.ensure_notebook(notebook_path)
    nb = nb_io.read_nb(notebook_path)
    idx = nb_io.resolve_index(nb, index=index, cell_id=cell_id)
    nb_io.set_cell(nb, idx, source, markdown=markdown)
    nb_io.atomic_write_nb(nb, notebook_path)
    return nb_io.fmt_cell_full(idx, nb.cells[idx])


@mcp.tool()
def delete_cell(
    notebook_path: str,
    index: int | None = None,
    cell_id: str | None = None,
) -> str:
    """Delete a cell. Provide either index or cell_id."""
    nb_io.ensure_notebook(notebook_path)
    nb = nb_io.read_nb(notebook_path)
    idx = nb_io.resolve_index(nb, index=index, cell_id=cell_id)
    nb_io.delete_cell(nb, idx)
    nb_io.atomic_write_nb(nb, notebook_path)
    return f"deleted cell {idx}"


@mcp.tool()
def clear_outputs(notebook_path: str, index: int | None = None) -> str:
    """Clear cell outputs. Omit index to clear every code cell."""
    nb_io.ensure_notebook(notebook_path)
    nb = nb_io.read_nb(notebook_path)
    if index is not None:
        nb_io.resolve_index(nb, index=index)
    count = nb_io.clear_outputs(nb, idx=index)
    nb_io.atomic_write_nb(nb, notebook_path)
    if index is not None:
        return f"cleared outputs from cell {index}"
    return f"cleared outputs from {count} cells"


# -- exec --


@mcp.tool()
async def exec_cell(
    notebook_path: str,
    index: int | None = None,
    cell_id: str | None = None,
    timeout: int = 120,
    block_for: int = 10,
) -> str:
    """Execute one cell on the notebook's kernel (auto-started). Outputs written to disk.

    Waits up to `block_for` seconds for the job to finish so short cells
    return inline. Longer ones return a Monitor-ready command to stream
    progress without blocking the tool slot.
    """
    nb_io.ensure_notebook(notebook_path)
    nb = nb_io.read_nb(notebook_path)
    idx = nb_io.resolve_index(nb, index=index, cell_id=cell_id)
    if nb.cells[idx]["cell_type"] != "code":
        return f"cell {idx} is {nb.cells[idx]['cell_type']}, not code"
    try:
        job = jobs.submit_execution(notebook_path, [idx], timeout=timeout)
    except RuntimeError as exc:
        return str(exc)
    headline = f"executing cell {idx} (job {job.job_id})\n{notebook_path}"
    return await anyio.to_thread.run_sync(
        lambda: _exec_response(notebook_path, job, block_for, headline)
    )


@mcp.tool()
async def exec_range(
    notebook_path: str,
    start: int,
    end: int,
    timeout: int = 120,
    block_for: int = 10,
) -> str:
    """Execute cells [start, end) in order. Stops on first error.

    Blocks up to `block_for` seconds so short jobs return inline.
    """
    nb_io.ensure_notebook(notebook_path)
    nb = nb_io.read_nb(notebook_path)
    n = len(nb.cells)
    start = max(0, start)
    end = min(end, n)
    if start >= end:
        return f"empty range {start}:{end} (notebook has {n} cells)"

    code_indices = [i for i in range(start, end) if nb.cells[i]["cell_type"] == "code"]
    if not code_indices:
        return f"no code cells in range {start}:{end}"

    try:
        job = jobs.submit_execution(notebook_path, code_indices, timeout=timeout)
    except RuntimeError as exc:
        return str(exc)
    headline = (
        f"executing {len(code_indices)} cells (job {job.job_id})\n{notebook_path}"
    )
    return await anyio.to_thread.run_sync(
        lambda: _exec_response(notebook_path, job, block_for, headline)
    )


@mcp.tool()
async def exec_all(notebook_path: str, timeout: int = 120, block_for: int = 10) -> str:
    """Execute every code cell in order. Stops on first error.

    Blocks up to `block_for` seconds so short notebooks return inline.
    """
    nb_io.ensure_notebook(notebook_path)
    nb = nb_io.read_nb(notebook_path)
    code_indices = [i for i, c in enumerate(nb.cells) if c["cell_type"] == "code"]
    if not code_indices:
        return "no code cells to execute"

    try:
        job = jobs.submit_execution(notebook_path, code_indices, timeout=timeout)
    except RuntimeError as exc:
        return str(exc)
    headline = (
        f"executing {len(code_indices)} cells (job {job.job_id})\n{notebook_path}"
    )
    return await anyio.to_thread.run_sync(
        lambda: _exec_response(notebook_path, job, block_for, headline)
    )


@mcp.tool()
async def run_scratch(notebook_path: str, code: str, timeout: int = 120) -> str:
    """Execute `code` on the notebook's kernel without writing it back to the file."""
    nb_io.ensure_notebook(notebook_path)

    def _run() -> str:
        client = kernels.get_or_start(notebook_path)
        outputs = execute_code(
            client,
            code,
            timeout=timeout,
            interrupt_fn=lambda: kernels.interrupt(notebook_path),
            recover_fn=lambda: kernels.reset_client(notebook_path),
        )
        return nb_io.fmt_outputs(outputs, indent="") or "(no output)"

    return await anyio.to_thread.run_sync(_run)


@mcp.tool()
async def insert_and_exec(
    notebook_path: str,
    index: int,
    source: str,
    timeout: int = 120,
    block_for: int = 10,
) -> str:
    """Insert a code cell then execute it. Common enough to be a single tool call.

    Blocks up to `block_for` seconds so short cells return inline.
    """
    nb_io.ensure_notebook(notebook_path)
    nb = nb_io.read_nb(notebook_path)
    idx = nb_io.insert_cell(nb, index, source, markdown=False)
    nb_io.atomic_write_nb(nb, notebook_path)

    try:
        job = jobs.submit_execution(notebook_path, [idx], timeout=timeout)
    except RuntimeError as exc:
        return f"cell inserted at {idx} but execution failed: {exc}"
    headline = f"inserted and executing cell {idx} (job {job.job_id})\n{notebook_path}"
    return await anyio.to_thread.run_sync(
        lambda: _exec_response(notebook_path, job, block_for, headline)
    )


@mcp.tool()
async def exec_status(notebook_path: str) -> str:
    """Check execution progress for a notebook. Shows active or most recent job."""
    nb_io.ensure_notebook(notebook_path)
    return jobs.get_status(notebook_path)


# -- kernel lifecycle --


@mcp.tool()
def interrupt(notebook_path: str) -> str:
    """Interrupt a running cell on the notebook's kernel (sends SIGINT)."""
    if not kernels.is_running(notebook_path):
        return f"no kernel running for {notebook_path}"
    log.info("interrupt requested for %s", notebook_path)
    kernels.interrupt(notebook_path)
    return "interrupt sent"


@mcp.tool()
def shutdown_kernel(notebook_path: str) -> str:
    """Stop the kernel for one notebook. Subsequent exec_* will start a fresh one."""
    stopped = kernels.shutdown(notebook_path)
    return "kernel stopped" if stopped else f"no kernel running for {notebook_path}"


def main() -> None:
    log.info("nb mcp server starting")
    mcp.run()


if __name__ == "__main__":
    main()
