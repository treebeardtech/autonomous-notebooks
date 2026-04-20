"""Tests for the in-process kernel pool."""

import time
from pathlib import Path

from autonomous_notebooks import kernels, nb_io


def _fresh(tmp_path: Path, name: str = "nb.ipynb") -> str:
    p = tmp_path / name
    nb_io.ensure_notebook(str(p))
    return str(p)


def test_get_or_start_reuses_client(tmp_path: Path):
    p = _fresh(tmp_path)
    try:
        c1 = kernels.get_or_start(p)
        c2 = kernels.get_or_start(p)
        assert c1 is c2
        assert kernels.is_running(p)
    finally:
        kernels.shutdown_all()


def test_two_notebooks_get_separate_kernels(tmp_path: Path):
    p1 = _fresh(tmp_path, "a.ipynb")
    p2 = _fresh(tmp_path, "b.ipynb")
    try:
        c1 = kernels.get_or_start(p1)
        c2 = kernels.get_or_start(p2)
        assert c1 is not c2
        # separate state per kernel
        c1.execute_interactive("x = 'hello'", timeout=10)
        reply = c2.execute_interactive("'x' in dir()", timeout=10)
        assert reply["content"]["status"] == "ok"
    finally:
        kernels.shutdown_all()


def test_shutdown_stops_kernel(tmp_path: Path):
    p = _fresh(tmp_path)
    kernels.get_or_start(p)
    assert kernels.is_running(p)
    assert kernels.shutdown(p) is True
    assert not kernels.is_running(p)


def test_shutdown_all_stops_everything(tmp_path: Path):
    p1 = _fresh(tmp_path, "a.ipynb")
    p2 = _fresh(tmp_path, "b.ipynb")
    kernels.get_or_start(p1)
    kernels.get_or_start(p2)
    stopped = kernels.shutdown_all()
    assert stopped == 2
    assert not kernels.is_running(p1)
    assert not kernels.is_running(p2)


def test_restart_after_shutdown(tmp_path: Path):
    p = _fresh(tmp_path)
    try:
        c1 = kernels.get_or_start(p)
        kernels.shutdown(p)
        # small wait for socket teardown
        time.sleep(0.2)
        c2 = kernels.get_or_start(p)
        assert c1 is not c2
        assert kernels.is_running(p)
    finally:
        kernels.shutdown_all()
