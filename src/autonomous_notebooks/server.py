"""Stdio MCP server exposing notebook tools.

Read/edit tools are kernel-free — they operate on `.ipynb` files directly.
Exec tools auto-start a kernel per notebook_path; kernels live in-process
and die with the server when Claude Code exits.
"""

from mcp.server.fastmcp import FastMCP

from autonomous_notebooks import kernels, nb_io
from autonomous_notebooks.exec_runner import exec_cell_to_disk, execute_code

mcp = FastMCP("autonomous-notebooks")


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
def exec_cell(
    notebook_path: str,
    index: int | None = None,
    cell_id: str | None = None,
    timeout: int = 120,
) -> str:
    """Execute one cell on the notebook's kernel (auto-started). Outputs written to disk."""
    nb_io.ensure_notebook(notebook_path)
    nb = nb_io.read_nb(notebook_path)
    idx = nb_io.resolve_index(nb, index=index, cell_id=cell_id)
    client = kernels.get_or_start(notebook_path)
    exec_cell_to_disk(client, notebook_path, idx, timeout=timeout)
    nb = nb_io.read_nb(notebook_path)
    return nb_io.fmt_cell_full(idx, nb.cells[idx])


@mcp.tool()
def exec_range(
    notebook_path: str,
    start: int,
    end: int,
    timeout: int = 120,
) -> str:
    """Execute cells [start, end) in order. Stops on first error."""
    nb_io.ensure_notebook(notebook_path)
    nb = nb_io.read_nb(notebook_path)
    n = len(nb.cells)
    start = max(0, start)
    end = min(end, n)
    if start >= end:
        return f"empty range {start}:{end} (notebook has {n} cells)"
    client = kernels.get_or_start(notebook_path)
    executed: list[int] = []
    for idx in range(start, end):
        nb = nb_io.read_nb(notebook_path)
        if nb.cells[idx]["cell_type"] != "code":
            continue
        result = exec_cell_to_disk(client, notebook_path, idx, timeout=timeout)
        executed.append(idx)
        if result["had_error"]:
            return f"executed {executed}; stopped with error in cell {idx}"
    return f"executed cells {executed}"


@mcp.tool()
def exec_all(notebook_path: str, timeout: int = 120) -> str:
    """Execute every code cell in order. Stops on first error."""
    nb_io.ensure_notebook(notebook_path)
    nb = nb_io.read_nb(notebook_path)
    code_indices = [i for i, c in enumerate(nb.cells) if c["cell_type"] == "code"]
    if not code_indices:
        return "no code cells to execute"
    client = kernels.get_or_start(notebook_path)
    for idx in code_indices:
        result = exec_cell_to_disk(client, notebook_path, idx, timeout=timeout)
        if result["had_error"]:
            return f"stopped with error in cell {idx}"
    return f"executed {len(code_indices)} cells"


@mcp.tool()
def run_scratch(notebook_path: str, code: str, timeout: int = 120) -> str:
    """Execute `code` on the notebook's kernel without writing it back to the file."""
    nb_io.ensure_notebook(notebook_path)
    client = kernels.get_or_start(notebook_path)
    outputs = execute_code(client, code, timeout=timeout)
    return nb_io.fmt_outputs(outputs, indent="") or "(no output)"


@mcp.tool()
def insert_and_exec(
    notebook_path: str,
    index: int,
    source: str,
    timeout: int = 120,
) -> str:
    """Insert a code cell then execute it. Common enough to be a single tool call."""
    nb_io.ensure_notebook(notebook_path)
    nb = nb_io.read_nb(notebook_path)
    idx = nb_io.insert_cell(nb, index, source, markdown=False)
    nb_io.atomic_write_nb(nb, notebook_path)
    client = kernels.get_or_start(notebook_path)
    exec_cell_to_disk(client, notebook_path, idx, timeout=timeout)
    nb = nb_io.read_nb(notebook_path)
    return nb_io.fmt_cell_full(idx, nb.cells[idx])


# -- kernel lifecycle --


@mcp.tool()
def interrupt(notebook_path: str) -> str:
    """Interrupt a running cell on the notebook's kernel (sends SIGINT)."""
    if not kernels.is_running(notebook_path):
        return f"no kernel running for {notebook_path}"
    kernels.interrupt(notebook_path)
    return "interrupt sent"


@mcp.tool()
def shutdown_kernel(notebook_path: str) -> str:
    """Stop the kernel for one notebook. Subsequent exec_* will start a fresh one."""
    stopped = kernels.shutdown(notebook_path)
    return "kernel stopped" if stopped else f"no kernel running for {notebook_path}"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
