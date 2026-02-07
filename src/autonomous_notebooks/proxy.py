"""ZMQ proxy that bridges VS Code to an existing Jupyter kernel.

Launched via a kernelspec: VS Code starts this as if it were a kernel process.
Reads the client's connection file (ports/key from VS Code) and the kernel's
connection info from state.json, then forwards ZMQ messages between them,
re-signing HMAC as needed since each side has a different key.

Usage (via kernelspec argv):
    python -m autonomous_notebooks.proxy {connection_file} --state-dir /path/to/.nb
"""

import argparse
import hashlib
import hmac as hmac_mod
import json
import logging
import signal
import threading
import uuid
from pathlib import Path

import zmq

DELIM = b"<IDS|MSG>"

log = logging.getLogger("nb-proxy")


def load_kernel_info(state_dir: Path) -> dict:
    """Read state.json and return kernel connection info with host-side IPC paths."""
    state_file = state_dir / "state.json"
    state = json.loads(state_file.read_text())

    conn_file = state["connection_file"]
    conn_data = json.loads(Path(conn_file).read_text())

    # for sandboxed kernels, override ip to host-side socket path
    ipc_dir = state.get("ipc_dir")
    if ipc_dir:
        conn_data["ip"] = str(Path(ipc_dir) / "kernel")

    return conn_data


def zmq_addr(info: dict, port_key: str) -> str:
    transport = info.get("transport", "tcp")
    ip = info["ip"]
    port = info[port_key]
    if transport == "ipc":
        return f"ipc://{ip}-{port}"
    return f"tcp://{ip}:{port}"


def make_signer(key: bytes):
    if not key:
        return lambda parts: b""

    def sign(parts):
        h = hmac_mod.new(key, digestmod=hashlib.sha256)
        for p in parts:
            h.update(p)
        return h.hexdigest().encode("ascii")

    return sign


def resign(frames: list, signer) -> list:
    """Re-sign a Jupyter wire protocol message with a different HMAC key."""
    try:
        idx = frames.index(DELIM)
    except ValueError:
        return frames  # not a standard Jupyter message, pass through
    idents = frames[:idx]
    msg_parts = frames[idx + 2 :]  # skip DELIM and old HMAC
    new_hmac = signer(msg_parts[:4])  # sign header, parent, metadata, content
    return idents + [DELIM, new_hmac] + msg_parts


def run_router_dealer(
    ctx, frontend_addr, backend_addr, sign_to_backend, sign_to_frontend, backend_id
):
    """Proxy for shell/control/stdin: ROUTER (bind) <-> DEALER (connect)."""
    frontend = ctx.socket(zmq.ROUTER)
    frontend.bind(frontend_addr)

    backend = ctx.socket(zmq.DEALER)
    backend.identity = backend_id
    backend.connect(backend_addr)

    poller = zmq.Poller()
    poller.register(frontend, zmq.POLLIN)
    poller.register(backend, zmq.POLLIN)

    client_id = None  # ZMQ identity of VS Code's DEALER

    while True:
        try:
            events = dict(poller.poll(1000))
        except zmq.ZMQError:
            break

        if frontend in events:
            frames = frontend.recv_multipart()
            # ROUTER prepends one ZMQ identity frame
            client_id = frames[0]
            msg = resign(frames[1:], sign_to_backend)
            backend.send_multipart(msg)

        if backend in events:
            frames = backend.recv_multipart()
            msg = resign(frames, sign_to_frontend)
            if client_id:
                frontend.send_multipart([client_id] + msg)


def run_iopub(ctx, frontend_addr, backend_addr, sign_to_frontend):
    """Proxy for iopub: SUB (connect to kernel PUB) -> PUB (bind for client SUB)."""
    backend = ctx.socket(zmq.SUB)
    backend.subscribe(b"")
    backend.connect(backend_addr)

    frontend = ctx.socket(zmq.PUB)
    frontend.bind(frontend_addr)

    while True:
        try:
            frames = backend.recv_multipart()
        except zmq.ZMQError:
            break
        msg = resign(frames, sign_to_frontend)
        frontend.send_multipart(msg)


def run_heartbeat(ctx, frontend_addr, backend_addr):
    """Proxy for heartbeat: just forward raw bytes, no HMAC."""
    frontend = ctx.socket(zmq.ROUTER)
    frontend.bind(frontend_addr)

    backend = ctx.socket(zmq.DEALER)
    backend.connect(backend_addr)

    poller = zmq.Poller()
    poller.register(frontend, zmq.POLLIN)
    poller.register(backend, zmq.POLLIN)

    client_id = None

    while True:
        try:
            events = dict(poller.poll(1000))
        except zmq.ZMQError:
            break

        if frontend in events:
            frames = frontend.recv_multipart()
            client_id = frames[0]
            backend.send_multipart(frames[1:])

        if backend in events:
            frames = backend.recv_multipart()
            if client_id:
                frontend.send_multipart([client_id] + frames)


def main():
    p = argparse.ArgumentParser(description="Jupyter kernel ZMQ proxy")
    p.add_argument(
        "connection_file", help="client connection file (written by VS Code)"
    )
    p.add_argument(
        "--state-dir", required=True, help="path to .nb dir containing state.json"
    )
    args = p.parse_args()

    state_dir = Path(args.state_dir)

    # set up file logging
    log_file = state_dir / "proxy.log"
    handler = logging.FileHandler(log_file)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    with open(args.connection_file) as f:
        client_info = json.load(f)

    kernel_info = load_kernel_info(state_dir)
    log.info("loaded kernel info from %s/state.json", state_dir)

    client_key = client_info.get("key", "").encode()
    kernel_key = kernel_info.get("key", "").encode()
    sign_to_backend = make_signer(kernel_key)
    sign_to_frontend = make_signer(client_key)

    ctx = zmq.Context()

    # same ZMQ identity on all backend DEALERs — needed for stdin routing
    # (kernel sends input_request using identity captured from shell channel)
    backend_id = uuid.uuid4().bytes

    threads = []
    for port_key in ("shell_port", "control_port", "stdin_port"):
        t = threading.Thread(
            target=run_router_dealer,
            args=(
                ctx,
                zmq_addr(client_info, port_key),
                zmq_addr(kernel_info, port_key),
                sign_to_backend,
                sign_to_frontend,
                backend_id,
            ),
            daemon=True,
        )
        t.start()
        threads.append(t)

    t = threading.Thread(
        target=run_iopub,
        args=(
            ctx,
            zmq_addr(client_info, "iopub_port"),
            zmq_addr(kernel_info, "iopub_port"),
            sign_to_frontend,
        ),
        daemon=True,
    )
    t.start()
    threads.append(t)

    t = threading.Thread(
        target=run_heartbeat,
        args=(
            ctx,
            zmq_addr(client_info, "hb_port"),
            zmq_addr(kernel_info, "hb_port"),
        ),
        daemon=True,
    )
    t.start()
    threads.append(t)

    log.info("bridging %s <-> %s/state.json", args.connection_file, state_dir)
    print(
        f"proxy: bridging {args.connection_file} <-> {state_dir}/state.json", flush=True
    )

    # stay alive until killed; close ZMQ context for graceful thread exit
    stop = threading.Event()

    def _shutdown(*_args):
        log.info("shutting down")
        stop.set()
        ctx.destroy(linger=0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    stop.wait()


if __name__ == "__main__":
    main()
