"""In-process kernel pool. One kernel per resolved notebook path.

Kernels live for the lifetime of the hosting process (e.g. the MCP server).
atexit/SIGTERM/SIGINT handlers ensure clean shutdown.
"""

import atexit
import os
import signal
import sys
import threading
from pathlib import Path

from jupyter_client import KernelManager
from jupyter_client.blocking import BlockingKernelClient

from autonomous_notebooks._log import get_logger

log = get_logger()
_lock = threading.Lock()
_kernels: dict[str, tuple[KernelManager, BlockingKernelClient]] = {}
_shutdown_registered = False


def _key(path: str) -> str:
    return str(Path(path).resolve())


def get_or_start(path: str) -> BlockingKernelClient:
    """Return the client for `path`'s kernel, starting one if needed."""
    _ensure_shutdown_hooks()
    k = _key(path)

    with _lock:
        existing = _kernels.get(k)
        if existing is not None:
            km, client = existing
            if km.is_alive():
                return client
            _kernels.pop(k, None)

    log.info("starting kernel for %s", k)
    km = KernelManager()
    km.start_kernel()
    client = km.client()
    client.start_channels()
    client.wait_for_ready(timeout=30)

    with _lock:
        race = _kernels.get(k)
        if race is not None and race[0].is_alive():
            log.info("kernel race for %s — discarding duplicate", k)
            try:
                client.stop_channels()
                km.shutdown_kernel(now=True)
            except Exception:
                pass
            return race[1]
        _kernels[k] = (km, client)
    log.info("kernel ready for %s", k)
    return client


def is_running(path: str) -> bool:
    with _lock:
        entry = _kernels.get(_key(path))
        return entry is not None and entry[0].is_alive()


def interrupt(path: str) -> None:
    with _lock:
        entry = _kernels.get(_key(path))
    if entry is None:
        raise ValueError(f"no kernel for {path}")
    km, _ = entry
    km.interrupt_kernel()


class KernelDeadError(RuntimeError):
    """Raised when a kernel process has died and can't be reattached to."""


def reset_client(path: str) -> BlockingKernelClient:
    """Rebuild the client's ZMQ channels without touching the kernel.

    Use to recover from an iopub framing desync (e.g. after heavy output):
    the training process stays running, we just re-subscribe.

    Raises KernelDeadError if the kernel process is gone — callers should
    surface that as a distinct failure, not a generic recovery error.
    """
    k = _key(path)
    with _lock:
        entry = _kernels.get(k)
    if entry is None:
        raise ValueError(f"no kernel for {path}")
    km, old_client = entry

    if not km.is_alive():
        log.error("kernel for %s is dead — cannot reset client", k)
        raise KernelDeadError(f"kernel for {path} has died")

    log.warning("resetting client channels for %s (kernel stays alive)", k)
    try:
        old_client.stop_channels()
    except Exception:
        log.exception("error stopping old client channels for %s", path)

    new_client = km.client()
    new_client.start_channels()
    try:
        new_client.wait_for_ready(timeout=10)
    except RuntimeError as exc:
        # wait_for_ready raises RuntimeError on heartbeat timeout — almost
        # always means the kernel died while we were reconnecting.
        log.error("kernel for %s unresponsive after reset: %s", k, exc)
        raise KernelDeadError(f"kernel for {path} unresponsive after reset") from exc
    with _lock:
        _kernels[k] = (km, new_client)
    log.info("client channels reset for %s", k)
    return new_client


def shutdown(path: str) -> bool:
    """Stop the kernel for `path`. Returns True if one was running."""
    with _lock:
        entry = _kernels.pop(_key(path), None)
    if entry is None:
        return False
    km, client = entry
    try:
        client.stop_channels()
    except Exception:
        log.exception("error stopping client channels for %s", path)
    try:
        km.shutdown_kernel(now=True)
    except Exception:
        log.exception("error shutting down kernel for %s", path)
    log.info("kernel stopped for %s", _key(path))
    return True


def shutdown_all() -> int:
    """Stop every kernel. Returns number stopped."""
    with _lock:
        paths = list(_kernels.keys())
    n = 0
    for p in paths:
        if shutdown(p):
            n += 1
    return n


def list_all() -> list[tuple[str, bool, int | None]]:
    """Snapshot of every registered kernel: (resolved_path, is_alive, pid)."""
    with _lock:
        items = list(_kernels.items())
    out: list[tuple[str, bool, int | None]] = []
    for path, (km, _client) in items:
        pid: int | None = None
        try:
            if km.provisioner is not None:
                pid = km.provisioner.pid  # type: ignore[attr-defined]
        except Exception:
            pid = None
        out.append((path, km.is_alive(), pid))
    return out


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _ensure_shutdown_hooks() -> None:
    global _shutdown_registered
    if _shutdown_registered:
        return
    _shutdown_registered = True
    atexit.register(shutdown_all)

    def _handler(signum, _frame):
        shutdown_all()
        sys.exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except ValueError:
            pass
