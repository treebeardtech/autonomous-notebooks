"""Unit tests for pure notebook I/O — no kernel required."""

from pathlib import Path

import nbformat
import pytest

from autonomous_notebooks import nb_io


def _fresh(tmp_path: Path) -> str:
    p = tmp_path / "nb.ipynb"
    nb_io.ensure_notebook(str(p))
    return str(p)


def test_ensure_creates_valid_notebook(tmp_path: Path):
    p = _fresh(tmp_path)
    nb = nb_io.read_nb(p)
    assert nb.cells == []


def test_ensure_rejects_wrong_extension(tmp_path: Path):
    with pytest.raises(ValueError):
        nb_io.ensure_notebook(str(tmp_path / "foo.txt"))


def test_insert_and_read_cell(tmp_path: Path):
    p = _fresh(tmp_path)
    nb = nb_io.read_nb(p)
    idx = nb_io.insert_cell(nb, 0, "x = 1")
    nb_io.atomic_write_nb(nb, p)

    nb2 = nb_io.read_nb(p)
    assert idx == 0
    assert nb2.cells[0]["source"] == "x = 1"
    assert nb2.cells[0]["cell_type"] == "code"


def test_insert_markdown(tmp_path: Path):
    p = _fresh(tmp_path)
    nb = nb_io.read_nb(p)
    nb_io.insert_cell(nb, 0, "# Title", markdown=True)
    nb_io.atomic_write_nb(nb, p)
    assert nb_io.read_nb(p).cells[0]["cell_type"] == "markdown"


def test_insert_clamps_index(tmp_path: Path):
    p = _fresh(tmp_path)
    nb = nb_io.read_nb(p)
    idx = nb_io.insert_cell(nb, 999, "x = 1")
    assert idx == 0


def test_set_cell_overwrites_source(tmp_path: Path):
    p = _fresh(tmp_path)
    nb = nb_io.read_nb(p)
    nb_io.insert_cell(nb, 0, "old")
    nb_io.set_cell(nb, 0, "new")
    assert nb.cells[0]["source"] == "new"


def test_set_cell_markdown_clears_outputs(tmp_path: Path):
    p = _fresh(tmp_path)
    nb = nb_io.read_nb(p)
    nb_io.insert_cell(nb, 0, "print('x')")
    nb.cells[0]["outputs"] = [nbformat.v4.new_output("stream", name="stdout", text="x")]
    nb.cells[0]["execution_count"] = 1
    nb_io.set_cell(nb, 0, "# now md", markdown=True)
    assert nb.cells[0]["cell_type"] == "markdown"
    assert "outputs" not in nb.cells[0]


def test_delete_cell(tmp_path: Path):
    p = _fresh(tmp_path)
    nb = nb_io.read_nb(p)
    nb_io.insert_cell(nb, 0, "a")
    nb_io.insert_cell(nb, 1, "b")
    nb_io.delete_cell(nb, 0)
    assert len(nb.cells) == 1
    assert nb.cells[0]["source"] == "b"


def test_clear_outputs_single_cell(tmp_path: Path):
    p = _fresh(tmp_path)
    nb = nb_io.read_nb(p)
    nb_io.insert_cell(nb, 0, "print('x')")
    nb.cells[0]["outputs"] = [nbformat.v4.new_output("stream", name="stdout", text="x")]
    nb.cells[0]["execution_count"] = 2
    nb_io.clear_outputs(nb, idx=0)
    assert nb.cells[0]["outputs"] == []
    assert nb.cells[0]["execution_count"] is None


def test_clear_outputs_all(tmp_path: Path):
    p = _fresh(tmp_path)
    nb = nb_io.read_nb(p)
    for i in range(3):
        nb_io.insert_cell(nb, i, f"x = {i}")
        nb.cells[i]["outputs"] = [
            nbformat.v4.new_output("stream", name="stdout", text=str(i))
        ]
    count = nb_io.clear_outputs(nb)
    assert count == 3
    assert all(c["outputs"] == [] for c in nb.cells)


def test_resolve_by_index(tmp_path: Path):
    p = _fresh(tmp_path)
    nb = nb_io.read_nb(p)
    nb_io.insert_cell(nb, 0, "a")
    nb_io.insert_cell(nb, 1, "b")
    assert nb_io.resolve_index(nb, index=1) == 1


def test_resolve_by_cell_id(tmp_path: Path):
    p = _fresh(tmp_path)
    nb = nb_io.read_nb(p)
    nb_io.insert_cell(nb, 0, "a")
    nb_io.insert_cell(nb, 1, "b")
    cid = nb.cells[1]["id"]
    assert nb_io.resolve_index(nb, cell_id=cid) == 1


def test_resolve_index_out_of_range(tmp_path: Path):
    p = _fresh(tmp_path)
    nb = nb_io.read_nb(p)
    with pytest.raises(ValueError):
        nb_io.resolve_index(nb, index=0)


def test_resolve_missing_id(tmp_path: Path):
    p = _fresh(tmp_path)
    nb = nb_io.read_nb(p)
    with pytest.raises(ValueError):
        nb_io.resolve_index(nb, cell_id="nope")


def test_atomic_write_roundtrip(tmp_path: Path):
    p = _fresh(tmp_path)
    nb = nb_io.read_nb(p)
    nb_io.insert_cell(nb, 0, "x = 1")
    nb_io.atomic_write_nb(nb, p)
    assert "x = 1" in Path(p).read_text()
