"""`nb watch` CLI: tails the mcp log, prints filtered events, exits on job end."""

import subprocess
import sys
import threading
import time
from pathlib import Path


def _append(path: Path, line: str) -> None:
    with path.open("a") as f:
        f.write(line + "\n")


def test_watch_prints_matching_events_and_exits(tmp_path: Path) -> None:
    log = tmp_path / "nb_mcp.log"
    log.write_text("")  # empty file up-front so tailing starts cleanly

    env = {
        "NB_MCP_LOG_PATH": str(log),
        # Inherit PATH so `uv` resolves.
        "PATH": "/usr/local/bin:/usr/bin:/bin",
    }
    # Use python -m to invoke the CLI without needing an installed script.
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "autonomous_notebooks.cli",
            "watch",
            "--job",
            "abc123",
            "--path",
            "nbs/foo.ipynb",
            "--startup-timeout",
            "10",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**env, "PYTHONPATH": str(Path(__file__).parent.parent / "src")},
        text=True,
    )

    def writer() -> None:
        # Give the watcher a moment to open and seek.
        time.sleep(0.5)
        _append(
            log,
            "2026-04-22 11:00:00+0000 INFO    nb_mcp: job abc123 submitted: nbs/foo.ipynb (2 cells: [0, 1])",
        )
        _append(
            log,
            "2026-04-22 11:00:00+0000 INFO    nb_mcp: starting kernel for nbs/foo.ipynb",
        )
        _append(
            log,
            "2026-04-22 11:00:00+0000 INFO    nb_mcp: job abc123 cell [0] running (1/2)",
        )
        _append(
            log,
            "2026-04-22 11:00:01+0000 INFO    nb_mcp: job xyz999 cell [0] running (1/1)",
        )  # unrelated job — filtered out
        _append(
            log,
            "2026-04-22 11:00:02+0000 INFO    nb_mcp: job abc123 cell [0] done in 1.2s",
        )
        _append(log, "2026-04-22 11:00:02+0000 INFO    nb_mcp: job abc123 complete")

    t = threading.Thread(target=writer, daemon=True)
    t.start()

    stdout, stderr = proc.communicate(timeout=20)
    assert proc.returncode == 0, f"stderr: {stderr}"

    # Both job-abc123 lines present, xyz999 line absent.
    assert "abc123 submitted" in stdout
    assert "abc123 cell [0] running" in stdout
    assert "abc123 cell [0] done" in stdout
    assert "abc123 complete" in stdout
    assert "xyz999" not in stdout


def test_watch_times_out_if_no_matching_job(tmp_path: Path) -> None:
    log = tmp_path / "nb_mcp.log"
    log.write_text("2026-04-22 10:00:00+0000 INFO    nb_mcp: unrelated content\n")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "autonomous_notebooks.cli",
            "watch",
            "--job",
            "nosuch",
            "--startup-timeout",
            "1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            "NB_MCP_LOG_PATH": str(log),
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
        },
        text=True,
    )
    _, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 3
    assert "no matching job" in stderr
