"""Containerised kernel management via podman-hpc.

Runs ipykernel inside a podman-hpc container with:
- --network=none (no outbound network)
- workspace and home bind-mounted at their real paths
- IPC transport (ZMQ over unix domain sockets through a shared tmpdir)

The host-side CLI connects to the kernel via the same IPC sockets.
"""

import os
import subprocess
import tempfile
import time
from pathlib import Path

from jupyter_client import KernelManager
from jupyter_client.blocking import BlockingKernelClient

IMAGE_NAME = "nb-kernel"
CONTAINERFILE = Path(__file__).parent / "Containerfile"


def image_exists() -> bool:
    r = subprocess.run(
        ["podman-hpc", "image", "exists", IMAGE_NAME],
        capture_output=True,
    )
    return r.returncode == 0


def build_image() -> None:
    """Build the kernel container image. Requires network access."""
    subprocess.run(
        [
            "podman-hpc",
            "build",
            "-t",
            IMAGE_NAME,
            "-f",
            str(CONTAINERFILE),
            str(CONTAINERFILE.parent),
        ],
        check=True,
        env={
            "TMPDIR": "/tmp",
            **__import__("os").environ,
        },  # avoid HPC buildah root issues
    )


def ensure_image() -> None:
    if not image_exists():
        build_image()


def write_connection_file(ipc_dir: Path) -> Path:
    """Generate an IPC-transport connection file. Returns path to the file."""
    km = KernelManager()
    km.transport = "ipc"
    km.ip = "/ipc/kernel"  # path inside container
    conn_file = ipc_dir / "kernel.json"
    km.connection_file = str(conn_file)
    km.write_connection_file()
    return conn_file


def start_container(
    ipc_dir: Path,
    workspace: Path,
    *,
    name: str = "nb-kernel",
    read_only_workspace: bool = True,
) -> str:
    """Start ipykernel in a container. Returns container ID.

    Mounts workspace and $HOME at their real paths so tools like uv
    can find configs, caches, and venvs in their expected locations.
    """
    home = Path(os.environ.get("HOME", os.path.expanduser("~")))
    ws_mode = "ro" if read_only_workspace else "rw"
    # walk up from workspace looking for a .venv
    kernel_python = "python3"
    d = workspace
    while d != d.parent:
        venv_python = d / ".venv" / "bin" / "python3"
        if venv_python.exists():
            kernel_python = str(venv_python)
            break
        d = d.parent
    r = subprocess.run(
        [
            "podman-hpc",
            "run",
            "--rm",
            "--network=none",
            "-v",
            f"{ipc_dir}:/ipc",
            "-v",
            f"{home}:{home}:rw",
            "-v",
            f"{workspace}:{workspace}:{ws_mode}",
            "-w",
            str(workspace),
            "-e",
            f"HOME={home}",
            "--name",
            name,
            "-d",
            IMAGE_NAME,
            kernel_python,
            "-m",
            "ipykernel_launcher",
            "--transport=ipc",
            "--ip=/ipc/kernel",
            "-f",
            "/ipc/kernel.json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def wait_for_sockets(ipc_dir: Path, timeout: float = 10.0) -> None:
    """Poll until all 5 IPC socket files appear."""
    deadline = time.monotonic() + timeout
    expected = {f"kernel-{i}" for i in range(1, 6)}
    while time.monotonic() < deadline:
        found = {p.name for p in ipc_dir.iterdir() if p.is_socket()}
        if expected <= found:
            return
        time.sleep(0.2)
    missing = expected - {p.name for p in ipc_dir.iterdir() if p.is_socket()}
    raise TimeoutError(f"Kernel sockets did not appear: missing {missing}")


def connect(ipc_dir: Path, timeout: int = 30) -> BlockingKernelClient:
    """Connect a blocking client to the containerised kernel."""
    conn_file = ipc_dir / "kernel.json"
    client = BlockingKernelClient()
    client.load_connection_file(str(conn_file))
    # override ip to host-side socket path
    client.ip = str(ipc_dir / "kernel")
    client.start_channels()
    client.wait_for_ready(timeout=timeout)
    return client


def stop_container(name: str = "nb-kernel") -> None:
    subprocess.run(["podman-hpc", "stop", name], capture_output=True)
    subprocess.run(["podman-hpc", "rm", name], capture_output=True)


class ContainerKernel:
    """High-level handle for a containerised kernel."""

    def __init__(
        self, workspace: Path, *, name: str | None = None, read_only: bool = True
    ):
        self.workspace = workspace.resolve()
        self.name = name or f"nb-kernel-{self.workspace.name}"
        self.read_only = read_only
        self.ipc_dir: Path | None = None
        self._tmpdir: tempfile.TemporaryDirectory | None = None  # type: ignore[type-arg]
        self.client: BlockingKernelClient | None = None
        self.container_id: str | None = None

    def start(self) -> BlockingKernelClient:
        ensure_image()
        self._tmpdir = tempfile.TemporaryDirectory(prefix="nb-ipc-")
        self.ipc_dir = Path(self._tmpdir.name)
        self.ipc_dir.chmod(0o777)

        write_connection_file(self.ipc_dir)
        self.container_id = start_container(
            self.ipc_dir,
            self.workspace,
            name=self.name,
            read_only_workspace=self.read_only,
        )
        wait_for_sockets(self.ipc_dir)
        self.client = connect(self.ipc_dir)
        return self.client

    def stop(self) -> None:
        if self.client:
            self.client.stop_channels()
            self.client = None
        stop_container(self.name)
        if self._tmpdir:
            self._tmpdir.cleanup()
            self._tmpdir = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
