"""Smoke tests for the MCP tool functions. Call them directly as Python."""

import asyncio
import time
from pathlib import Path

import pytest

from autonomous_notebooks import jobs, kernels, server


@pytest.fixture(autouse=True)
def cleanup_kernels():
    yield
    kernels.shutdown_all()


@pytest.fixture(autouse=True)
def cleanup_jobs():
    yield
    with jobs._lock:
        jobs._active.clear()
        jobs._finished.clear()


def _nb(tmp_path: Path, name: str = "nb.ipynb") -> str:
    return str(tmp_path / name)


def _run(coro):
    return asyncio.run(coro)


def _run_and_wait(coro, notebook_path: str, timeout: float = 30):
    """Run an async exec tool, then wait for the background job to finish."""
    result = asyncio.run(coro)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = jobs.get_active_job(notebook_path)
        if job is None:
            break
        if job.thread is not None:
            job.thread.join(timeout=0.5)
    return result


# -- read/edit (sync, unchanged) --


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


# -- exec (async, fire-and-forget) --


def test_insert_and_exec_captures_output(tmp_path: Path):
    p = _nb(tmp_path)
    result = _run_and_wait(server.insert_and_exec(p, 0, "print('hello')"), p)
    assert "executing" in result or "job" in result
    assert "hello" in server.read_cell(p, index=0)


def test_exec_all(tmp_path: Path):
    p = _nb(tmp_path)
    server.insert_cell(p, 0, "x = 2")
    server.insert_cell(p, 1, "print(x * 3)")
    _run_and_wait(server.exec_all(p), p)
    assert "6" in server.read_cell(p, index=1)


def test_run_scratch_does_not_modify_notebook(tmp_path: Path):
    p = _nb(tmp_path)
    server.insert_cell(p, 0, "a = 1")
    _run_and_wait(server.exec_cell(p, index=0), p)
    out = _run(server.run_scratch(p, "a + 41"))
    assert "42" in out
    assert "2 cells" not in server.list_cells(p)


def test_two_notebooks_concurrent(tmp_path: Path):
    p1 = _nb(tmp_path, "a.ipynb")
    p2 = _nb(tmp_path, "b.ipynb")
    _run_and_wait(server.insert_and_exec(p1, 0, "X = 'a'"), p1)
    _run_and_wait(server.insert_and_exec(p2, 0, "X = 'b'"), p2)
    assert "a" in _run(server.run_scratch(p1, "X"))
    assert "b" in _run(server.run_scratch(p2, "X"))


def test_clear_outputs_single(tmp_path: Path):
    p = _nb(tmp_path)
    _run_and_wait(server.insert_and_exec(p, 0, "print('hi')"), p)
    assert "-> hi" in server.read_cell(p, index=0)
    server.clear_outputs(p, index=0)
    assert "-> hi" not in server.read_cell(p, index=0)


def test_shutdown_kernel_then_restart(tmp_path: Path):
    p = _nb(tmp_path)
    _run_and_wait(server.insert_and_exec(p, 0, "X = 1"), p)
    server.shutdown_kernel(p)
    out = _run(server.run_scratch(p, "'X' in dir()"))
    assert "False" in out


def test_exec_all_stops_on_error(tmp_path: Path):
    p = _nb(tmp_path)
    server.insert_cell(p, 0, "x = 1")
    server.insert_cell(p, 1, "raise RuntimeError('boom')")
    server.insert_cell(p, 2, "print('should not run')")
    _run_and_wait(server.exec_all(p), p)
    cell2 = server.read_cell(p, index=2)
    # cell 2 was skipped — no "-> should not run" stream output, has Skipped marker
    assert "-> should not run" not in cell2
    assert "Skipped" in cell2


def test_interrupt_with_no_kernel(tmp_path: Path):
    p = _nb(tmp_path)
    out = server.interrupt(p)
    assert "no kernel" in out


def test_exec_status(tmp_path: Path):
    p = _nb(tmp_path)
    out = _run(server.exec_status(p))
    assert "no execution history" in out


def test_short_job_returns_inline_within_block_for(tmp_path: Path):
    """A quick cell completes inside block_for and returns status, not a Monitor hint."""
    p = _nb(tmp_path)
    server.insert_cell(p, 0, "print('hi')")
    out = _run(server.exec_cell(p, index=0, block_for=10))
    assert "done" in out
    assert "uv run nb watch" not in out
    assert jobs.get_active_job(p) is None


def test_long_job_returns_monitor_hint_after_block_for(tmp_path: Path):
    """A cell still running after block_for seconds falls back to the Monitor hint."""
    p = _nb(tmp_path)
    server.insert_cell(p, 0, "import time; time.sleep(3)")
    out = _run(server.exec_cell(p, index=0, timeout=30, block_for=1))
    assert "uv run nb watch" in out
    assert "--job" in out
    # Drain the active job so fixtures can clean up.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = jobs.get_active_job(p)
        if job is None:
            break
        if job.thread is not None:
            job.thread.join(timeout=0.5)


def test_block_for_zero_never_blocks(tmp_path: Path):
    """block_for=0 should skip the join and always return the Monitor hint."""
    p = _nb(tmp_path)
    server.insert_cell(p, 0, "print('instant')")
    out = _run(server.exec_cell(p, index=0, block_for=0))
    assert "uv run nb watch" in out
    # Drain.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        job = jobs.get_active_job(p)
        if job is None:
            break
        if job.thread is not None:
            job.thread.join(timeout=0.5)


def test_timeout_is_wall_clock_not_idle_gap(tmp_path: Path):
    """A cell that streams output every 0.1s must still timeout at wall-clock deadline."""
    p = _nb(tmp_path)
    # Print every 100ms for up to 10s. Timeout is 2s, so cell should be
    # killed at ~2s even though messages arrive continuously.
    server.insert_cell(
        p,
        0,
        "import time\nfor i in range(100):\n    print(i, flush=True)\n    time.sleep(0.1)",
    )
    start = time.monotonic()
    _run_and_wait(server.exec_cell(p, index=0, timeout=2), p, timeout=15)
    elapsed = time.monotonic() - start
    assert elapsed < 10, f"cell ran for {elapsed:.1f}s, should have been killed at ~2s"
    body = server.read_cell(p, index=0)
    assert "timed out after 2s" in body


def test_progress_lines_written_to_log(tmp_path: Path, monkeypatch):
    """Chatty cells should emit throttled progress log lines for Monitor consumption."""
    import importlib
    import logging

    log_path = tmp_path / "nb_mcp.log"
    monkeypatch.setenv("NB_MCP_LOG_PATH", str(log_path))
    monkeypatch.setenv("NB_MCP_LOG_LEVEL", "INFO")
    # Fast progress emission so the test runs quickly.
    monkeypatch.setenv("NB_MCP_PROGRESS_INTERVAL_SEC", "0.2")

    # Reset the shared logger so it picks up the env vars.
    from autonomous_notebooks import _log

    importlib.reload(_log)
    logger = logging.getLogger("nb_mcp")
    for h in list(logger.handlers):
        logger.removeHandler(h)
    _log._configured = False
    _log.get_logger()
    # Re-attach the reinitialised handlers to the loggers held by jobs/exec_runner.
    from autonomous_notebooks import exec_runner as _er
    from autonomous_notebooks import jobs as _jb

    _er.log = logger
    _jb.log = logger

    p = _nb(tmp_path, "progress.ipynb")
    server.insert_cell(
        p,
        0,
        "import time\nfor i in range(20):\n    print(f'step {i}', flush=True)\n    time.sleep(0.1)",
    )
    _run_and_wait(server.exec_cell(p, index=0, timeout=30, block_for=0), p)

    body = log_path.read_text()
    progress_lines = [line for line in body.splitlines() if " out: step " in line]
    assert len(progress_lines) >= 2, body
    # Throttled: we shouldn't see every one of the 20 prints.
    assert len(progress_lines) < 20, f"progress not throttled: {len(progress_lines)}"


def test_exec_cell_without_id_streams_output(tmp_path: Path):
    """Notebooks written outside the MCP may lack cell ids — exec must still flush outputs."""
    import json

    import nbformat

    p = _nb(tmp_path)
    # Build an ipynb by hand with an id-less code cell.
    nb = nbformat.v4.new_notebook()
    cell = nbformat.v4.new_code_cell(source="print('hi from idless')")
    cell.pop("id", None)
    nb.cells.append(cell)
    Path(p).write_text(json.dumps(nb))

    _run_and_wait(server.exec_cell(p, index=0), p)
    body = server.read_cell(p, index=0)
    assert "hi from idless" in body
    assert "[nb mcp] ✓ Done" in body


def test_exec_conflict(tmp_path: Path):
    p = _nb(tmp_path)
    server.insert_cell(p, 0, "import time; time.sleep(2)")
    server.insert_cell(p, 1, "print('done')")
    # block_for=0 so this returns while the first job is still running.
    _run(server.exec_all(p, block_for=0))
    # second exec on same notebook should report conflict
    result = _run(server.exec_cell(p, index=0, block_for=0))
    assert "already has a running job" in result
    # wait for the first job to finish
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        job = jobs.get_active_job(p)
        if job is None:
            break
        if job.thread is not None:
            job.thread.join(timeout=0.5)
