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

    km = KernelManager()
    km.start_kernel()
    client = km.client()
    client.start_channels()
    client.wait_for_ready(timeout=30)

    with _lock:
        race = _kernels.get(k)
        if race is not None and race[0].is_alive():
            try:
                client.stop_channels()
                km.shutdown_kernel(now=True)
            except Exception:
                pass
            return race[1]
        _kernels[k] = (km, client)
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
        pass
    try:
        km.shutdown_kernel(now=True)
    except Exception:
        pass
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
