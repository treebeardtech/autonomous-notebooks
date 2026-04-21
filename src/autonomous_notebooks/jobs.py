"""Background execution jobs. One active job per notebook, backed by a daemon thread."""

import enum
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import nbformat

from autonomous_notebooks import kernels
from autonomous_notebooks.exec_runner import (
    exec_cell_to_disk,
    mark_cells_status,
    write_cell_status,
)
from autonomous_notebooks.nb_io import (
    atomic_write_nb,
    read_nb,
)


class CellStatus(enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"


class JobState(enum.Enum):
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class CellProgress:
    index: int
    status: CellStatus = CellStatus.QUEUED
    started_at: float | None = None
    finished_at: float | None = None
    error_summary: str | None = None

    @property
    def elapsed(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at or time.monotonic()
        return end - self.started_at


@dataclass
class Job:
    job_id: str
    notebook_path: str
    cell_indices: list[int]
    state: JobState = JobState.RUNNING
    cells: dict[int, CellProgress] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    thread: threading.Thread | None = field(default=None, repr=False)


_lock = threading.Lock()
_active: dict[str, Job] = {}
_finished: dict[str, Job] = {}


def _nb_key(path: str) -> str:
    from pathlib import Path

    return str(Path(path).resolve())


def submit_execution(
    notebook_path: str,
    cell_indices: list[int],
    timeout: int = 120,
) -> Job:
    """Start a background execution job. Returns immediately."""
    key = _nb_key(notebook_path)

    with _lock:
        existing = _active.get(key)
        if existing is not None:
            raise RuntimeError(
                f"notebook already has a running job ({existing.job_id}). "
                "Wait for it to finish, check exec_status, or interrupt the kernel."
            )

        job = Job(
            job_id=uuid.uuid4().hex[:8],
            notebook_path=notebook_path,
            cell_indices=list(cell_indices),
            cells={idx: CellProgress(index=idx) for idx in cell_indices},
        )
        _active[key] = job

    mark_cells_status(notebook_path, cell_indices, "⏳ Queued")

    t = threading.Thread(
        target=_run_job,
        args=(job, key, timeout),
        daemon=True,
        name=f"nb-exec-{job.job_id}",
    )
    job.thread = t
    t.start()
    return job


def _run_job(job: Job, key: str, timeout: int) -> None:
    """Worker thread: execute cells sequentially, updating notebook and job state."""
    total = len(job.cell_indices)
    try:
        client = kernels.get_or_start(job.notebook_path)

        for pos, idx in enumerate(job.cell_indices):
            cp = job.cells[idx]
            cp.status = CellStatus.RUNNING
            cp.started_at = time.monotonic()

            ts = datetime.now().strftime("%H:%M:%S")
            write_cell_status(
                job.notebook_path,
                idx,
                f"⏳ Running ({pos + 1}/{total})... (started {ts})",
            )

            result = exec_cell_to_disk(client, job.notebook_path, idx, timeout=timeout)

            cp.finished_at = time.monotonic()
            elapsed = cp.elapsed or 0.0

            if result["had_error"]:
                cp.status = CellStatus.ERROR
                cp.error_summary = _extract_error(result["outputs"])
                _append_status_line(job.notebook_path, idx, f"✗ {elapsed:.1f}s")

                for remaining_idx in job.cell_indices[pos + 1 :]:
                    rcp = job.cells[remaining_idx]
                    rcp.status = CellStatus.SKIPPED
                    write_cell_status(
                        job.notebook_path,
                        remaining_idx,
                        "⊘ Skipped (earlier cell errored)",
                    )

                job.state = JobState.ERROR
                return
            else:
                cp.status = CellStatus.DONE
                _append_status_line(job.notebook_path, idx, f"✓ {elapsed:.1f}s")

        job.state = JobState.DONE

    except Exception as exc:
        job.state = JobState.ERROR
        for cp in job.cells.values():
            if cp.status in (CellStatus.QUEUED, CellStatus.RUNNING):
                cp.status = CellStatus.ERROR
                cp.error_summary = str(exc)
    finally:
        job.finished_at = time.time()
        with _lock:
            _active.pop(key, None)
            _finished[key] = job


def _extract_error(outputs: list) -> str:
    for out in outputs:
        if out.get("output_type") == "error":
            return f"{out['ename']}: {out['evalue']}"
    return "unknown error"


def _append_status_line(nb_path: str, idx: int, text: str) -> None:
    """Append a timing/status line to a cell's existing outputs on disk."""
    nb = read_nb(nb_path)
    cell = nb.cells[idx]
    cell["outputs"].append(
        nbformat.v4.new_output("stream", name="stdout", text=f"\n{text}")
    )
    atomic_write_nb(nb, nb_path)


def get_active_job(notebook_path: str) -> Job | None:
    key = _nb_key(notebook_path)
    with _lock:
        return _active.get(key)


def get_status(notebook_path: str) -> str:
    """Human-readable status of the active or most recent job."""
    key = _nb_key(notebook_path)
    with _lock:
        job = _active.get(key) or _finished.get(key)

    if job is None:
        return "no execution history for this notebook"

    total = len(job.cell_indices)
    lines = [f"job {job.job_id}: {job.state.value} ({total} cells)"]

    for idx in job.cell_indices:
        cp = job.cells[idx]
        elapsed_str = f" {cp.elapsed:.1f}s" if cp.elapsed is not None else ""
        err_str = f" — {cp.error_summary}" if cp.error_summary else ""
        lines.append(f"  [{idx}] {cp.status.value}{elapsed_str}{err_str}")

    return "\n".join(lines)
