"""Pure notebook I/O — no kernel, no state. Read/write .ipynb via nbformat."""

import os
import sys
import tempfile
import textwrap
from pathlib import Path

import nbformat


def read_nb(path: str) -> nbformat.NotebookNode:
    return nbformat.read(path, as_version=4)


def atomic_write_nb(nb: nbformat.NotebookNode, path: str) -> None:
    """Write notebook atomically: tempfile in same dir, then rename (POSIX atomic)."""
    p = Path(path)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".ipynb.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            nbformat.write(nb, f)
        os.rename(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def ensure_notebook(path: str) -> None:
    """Create an empty .ipynb at path if it doesn't exist. Validates extension."""
    p = Path(path)
    if p.suffix != ".ipynb":
        raise ValueError(f"not a .ipynb path: {path}")
    if p.exists():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    nbformat.write(nbformat.v4.new_notebook(), str(p))


def find_cell_by_id(
    nb: nbformat.NotebookNode, cell_id: str
) -> tuple[int, nbformat.NotebookNode] | None:
    for i, cell in enumerate(nb.cells):
        if cell.get("id") == cell_id:
            return i, cell
    return None


def resolve_index(
    nb: nbformat.NotebookNode,
    index: int | None = None,
    cell_id: str | None = None,
) -> int:
    """Resolve a cell index from either an index or a cell_id. Exactly one must be set."""
    if cell_id is not None:
        hit = find_cell_by_id(nb, cell_id)
        if hit is None:
            raise ValueError(f"no cell with id '{cell_id}'")
        return hit[0]
    if index is None:
        raise ValueError("provide index or cell_id")
    n = len(nb.cells)
    if index < 0 or index >= n:
        raise ValueError(f"index {index} out of range (0..{n - 1})")
    return index


def fmt_outputs(outputs: list, indent: str = "  ") -> str:
    parts = []
    for out in outputs:
        otype = out.get("output_type", "")
        if otype == "stream":
            parts.append(f"{indent}-> {out['text'].rstrip()}")
        elif otype == "execute_result":
            text = out.get("data", {}).get("text/plain", "")
            if text:
                parts.append(f"{indent}=> {text.rstrip()}")
        elif otype == "display_data":
            text = out.get("data", {}).get("text/plain", "")
            if text:
                parts.append(f"{indent}:: {text.rstrip()}")
            else:
                mimes = list(out.get("data", {}).keys())
                parts.append(f"{indent}:: <{', '.join(mimes)}>")
        elif otype == "error":
            parts.append(f"{indent}!! {out['ename']}: {out['evalue']}")
    return "\n".join(parts)


def fmt_cell_compact(i: int, cell: dict) -> str:
    ctype = cell["cell_type"]
    src = cell["source"]
    first = src.split("\n", 1)[0]
    if len(first) > 70:
        first = first[:67] + "..."

    tag = (
        "md"
        if ctype == "markdown"
        else f"In[{cell.get('execution_count', ' ') or ' '}]"
    )
    line = f"  [{i}] {tag:>8}  {first}"

    out_str = fmt_outputs(cell.get("outputs", []), indent="              ")
    if out_str:
        line += "\n" + out_str
    return line


def fmt_cell_full(i: int, cell: dict) -> str:
    ctype = cell["cell_type"]
    src = cell["source"]
    tag = (
        "md"
        if ctype == "markdown"
        else f"In[{cell.get('execution_count', ' ') or ' '}]"
    )
    line = f"[{i}] {tag}\n{textwrap.indent(src, '  ')}"

    out_str = fmt_outputs(cell.get("outputs", []), indent="  ")
    if out_str:
        line += "\n" + out_str
    return line


def list_cells_text(nb: nbformat.NotebookNode) -> str:
    if not nb.cells:
        return "(no cells)"
    return "\n".join(fmt_cell_compact(i, c) for i, c in enumerate(nb.cells))


# -- mutations returning the updated notebook (caller writes) --


def insert_cell(
    nb: nbformat.NotebookNode, index: int, source: str, markdown: bool = False
) -> int:
    """Insert a cell at index (clamped to [0, len]). Returns actual inserted index."""
    if markdown:
        cell = nbformat.v4.new_markdown_cell(source=source)
    else:
        cell = nbformat.v4.new_code_cell(source=source)
    idx = max(0, min(index, len(nb.cells)))
    nb.cells.insert(idx, cell)
    return idx


def set_cell(
    nb: nbformat.NotebookNode,
    idx: int,
    source: str,
    markdown: bool = False,
) -> None:
    nb.cells[idx]["source"] = source
    if markdown:
        nb.cells[idx]["cell_type"] = "markdown"
        nb.cells[idx].pop("outputs", None)
        nb.cells[idx].pop("execution_count", None)


def delete_cell(nb: nbformat.NotebookNode, idx: int) -> None:
    nb.cells.pop(idx)


def clear_outputs(nb: nbformat.NotebookNode, idx: int | None = None) -> int:
    """Clear outputs. If idx is None, clear all code cells. Returns number cleared."""
    count = 0
    if idx is not None:
        cell = nb.cells[idx]
        if cell["cell_type"] == "code":
            cell["outputs"] = []
            cell["execution_count"] = None
            count = 1
        return count
    for cell in nb.cells:
        if cell["cell_type"] == "code":
            cell["outputs"] = []
            cell["execution_count"] = None
            count += 1
    return count


def warn_if_external_modify(path: str, expected_mtime: float) -> None:
    current = os.path.getmtime(path)
    if abs(current - expected_mtime) > 0.01:
        print(
            f"warning: {path} was modified externally, overwriting anyway",
            file=sys.stderr,
        )
