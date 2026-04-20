"""Smoke tests for the MCP tool functions. Call them directly as Python."""

from pathlib import Path

import pytest

from autonomous_notebooks import kernels, server


@pytest.fixture(autouse=True)
def cleanup_kernels():
    yield
    kernels.shutdown_all()


def _nb(tmp_path: Path, name: str = "nb.ipynb") -> str:
    return str(tmp_path / name)


def test_list_cells_on_fresh_file_creates_notebook(tmp_path: Path):
    p = _nb(tmp_path)
    out = server.list_cells(p)
    assert out == "(no cells)"
    assert Path(p).exists()


def test_insert_then_read(tmp_path: Path):
    p = _nb(tmp_path)
    server.insert_cell(p, 0, "x = 1")
    assert "x = 1" in server.read_cell(p, index=0)


def test_set_and_delete(tmp_path: Path):
    p = _nb(tmp_path)
    server.insert_cell(p, 0, "old")
    server.set_cell(p, "new", index=0)
    assert "new" in server.read_cell(p, index=0)
    server.delete_cell(p, index=0)
    assert "(no cells)" in server.list_cells(p)


def test_insert_and_exec_captures_output(tmp_path: Path):
    p = _nb(tmp_path)
    out = server.insert_and_exec(p, 0, "print('hello')")
    assert "hello" in out


def test_exec_all(tmp_path: Path):
    p = _nb(tmp_path)
    server.insert_cell(p, 0, "x = 2")
    server.insert_cell(p, 1, "print(x * 3)")
    server.exec_all(p)
    assert "6" in server.read_cell(p, index=1)


def test_run_scratch_does_not_modify_notebook(tmp_path: Path):
    p = _nb(tmp_path)
    server.insert_cell(p, 0, "a = 1")
    server.exec_cell(p, index=0)
    out = server.run_scratch(p, "a + 41")
    assert "42" in out
    # scratch didn't add a cell
    assert "2 cells" not in server.list_cells(p)


def test_two_notebooks_concurrent(tmp_path: Path):
    p1 = _nb(tmp_path, "a.ipynb")
    p2 = _nb(tmp_path, "b.ipynb")
    server.insert_and_exec(p1, 0, "X = 'a'")
    server.insert_and_exec(p2, 0, "X = 'b'")
    assert "a" in server.run_scratch(p1, "X")
    assert "b" in server.run_scratch(p2, "X")


def test_clear_outputs_single(tmp_path: Path):
    p = _nb(tmp_path)
    server.insert_and_exec(p, 0, "print('hi')")
    assert "-> hi" in server.read_cell(p, index=0)
    server.clear_outputs(p, index=0)
    assert "-> hi" not in server.read_cell(p, index=0)


def test_shutdown_kernel_then_restart(tmp_path: Path):
    p = _nb(tmp_path)
    server.insert_and_exec(p, 0, "X = 1")
    server.shutdown_kernel(p)
    # fresh kernel — X should not exist
    out = server.run_scratch(p, "'X' in dir()")
    assert "False" in out


def test_exec_all_stops_on_error(tmp_path: Path):
    p = _nb(tmp_path)
    server.insert_cell(p, 0, "x = 1")
    server.insert_cell(p, 1, "raise RuntimeError('boom')")
    server.insert_cell(p, 2, "print('should not run')")
    result = server.exec_all(p)
    assert "stopped" in result
    # cell 2 never ran, so no stream output line for it
    assert "-> should not run" not in server.read_cell(p, index=2)


def test_interrupt_with_no_kernel(tmp_path: Path):
    p = _nb(tmp_path)
    out = server.interrupt(p)
    assert "no kernel" in out
