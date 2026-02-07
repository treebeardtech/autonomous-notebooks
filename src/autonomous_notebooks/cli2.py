"""Headless notebook CLI. Manages kernels directly via jupyter_client, no server needed.

Usage: uv run nb <command> [args]
"""

import argparse
import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from collections.abc import Callable
from pathlib import Path

import nbformat
from jupyter_client import KernelManager, write_connection_file
from jupyter_client.blocking import BlockingKernelClient

STATE_DIR = Path(".nb")
STATE_FILE = STATE_DIR / "state.json"

# minimum interval between incremental disk flushes during execution
FLUSH_INTERVAL = 2.0

log = logging.getLogger("nb")


def _setup_logging():
    """Configure file logging to .nb/cli.log."""
    STATE_DIR.mkdir(exist_ok=True)
    handler = logging.FileHandler(STATE_DIR / "cli.log")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


# -- state persistence --


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    STATE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def require_state() -> dict:
    state = load_state()
    if not state.get("notebook"):
        print("error: no notebook open. run: nb open <path>", file=sys.stderr)
        sys.exit(1)
    return state


# -- kernel management --


def start_kernel(connection_file: Path, kernel_name: str | None = None) -> int:
    """Start a kernel and return its PID. Kernel survives CLI exit.

    When kernel_name is None, launches ipykernel directly via sys.executable
    (no kernelspec lookup needed). When set, uses KernelManager with that spec.
    """
    if kernel_name:
        km = KernelManager(
            kernel_name=kernel_name, connection_file=str(connection_file)
        )
        km.start_kernel(independent=True)
        pid = km.provisioner.pid  # type: ignore[union-attr]
        # prevent __del__ from cleaning up the connection file
        km._connection_file_written = False  # type: ignore[attr-defined]
        return pid

    # default: launch ipykernel directly, no kernelspec needed
    write_connection_file(fname=str(connection_file))
    proc = subprocess.Popen(
        [sys.executable, "-m", "ipykernel_launcher", "-f", str(connection_file)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid


def connect_client(
    connection_file: Path, ipc_dir: str | None = None
) -> BlockingKernelClient:
    client = BlockingKernelClient()
    client.load_connection_file(str(connection_file))
    if ipc_dir:
        # container kernel: override ip to host-side socket path
        client.ip = str(Path(ipc_dir) / "kernel")
    client.start_channels()
    client.wait_for_ready(timeout=30)
    return client


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def container_alive(name: str) -> bool:
    r = subprocess.run(
        ["podman-hpc", "inspect", "--format", "{{.State.Running}}", name],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0 and "true" in r.stdout.lower()


def kernel_alive(state: dict) -> bool:
    """Check if the kernel process is still running."""
    if state.get("sandboxed"):
        return container_alive(state["container_name"])
    pid = state.get("pid")
    if not pid:
        return False
    return pid_alive(pid)


def kill_kernel(state: dict):
    """Kill kernel process if running."""
    if state.get("sandboxed"):
        from autonomous_notebooks.podman_hpc.kernel import stop_container

        stop_container(state["container_name"])
        return
    pid = state.get("pid")
    if pid and pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    conn = state.get("connection_file", "")
    if conn:
        Path(conn).unlink(missing_ok=True)


# -- notebook I/O --


def read_nb(path: str) -> nbformat.NotebookNode:
    return nbformat.read(path, as_version=4)


def read_nb_with_mtime(path: str) -> tuple[nbformat.NotebookNode, float]:
    nb = nbformat.read(path, as_version=4)
    mtime = os.path.getmtime(path)
    return nb, mtime


def atomic_write_nb(nb: nbformat.NotebookNode, path: str):
    """Write notebook atomically: write to temp file then rename (POSIX atomic)."""
    p = Path(path)
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".ipynb.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            nbformat.write(nb, f)
        os.rename(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_nb_if_unchanged(
    nb: nbformat.NotebookNode, path: str, expected_mtime: float
) -> bool:
    """Atomic write, warns if file was modified externally since expected_mtime."""
    current_mtime = os.path.getmtime(path)
    if abs(current_mtime - expected_mtime) > 0.01:
        print(
            f"warning: {path} was modified externally, overwriting anyway",
            file=sys.stderr,
        )
    atomic_write_nb(nb, path)
    return True


def find_cell_by_id(
    nb: nbformat.NotebookNode, cell_id: str
) -> tuple[int, nbformat.NotebookNode] | None:
    """Find a cell by its ID. Returns (index, cell) or None."""
    for i, cell in enumerate(nb.cells):
        if cell.get("id") == cell_id:
            return i, cell
    return None


# -- cell index resolution --


def resolve_cell_index(nb: nbformat.NotebookNode, args: argparse.Namespace) -> int:
    """Resolve cell index from --id or positional index arg."""
    if getattr(args, "id", None):
        result = find_cell_by_id(nb, args.id)
        if result is None:
            print(f"error: no cell with id '{args.id}'", file=sys.stderr)
            sys.exit(1)
        return result[0]
    idx = args.index
    if idx is None:
        print("error: provide cell index or --id", file=sys.stderr)
        sys.exit(1)
    if idx < 0 or idx >= len(nb.cells):
        print(
            f"error: index {idx} out of range (0..{len(nb.cells) - 1})",
            file=sys.stderr,
        )
        sys.exit(1)
    return idx


# -- output capture --


def execute_code(
    client: BlockingKernelClient,
    code: str,
    timeout: int = 120,
    on_output: Callable[[list], None] | None = None,
) -> list:
    """Execute code on kernel, return list of nbformat-style output dicts.
    If on_output is provided, it's called after each new output message
    with the full outputs list so far."""
    msg_id = client.execute(code)
    outputs: list = []

    while True:
        try:
            msg = client.get_iopub_msg(timeout=timeout)
        except TimeoutError:
            outputs.append(
                nbformat.v4.new_output(
                    "stream", name="stderr", text="[execution timed out]"
                )
            )
            if on_output:
                on_output(outputs)
            break

        # only care about messages from our execution
        if msg["parent_header"].get("msg_id") != msg_id:
            continue

        msg_type = msg["msg_type"]
        content = msg["content"]
        new_output = None

        if msg_type == "stream":
            new_output = nbformat.v4.new_output(
                "stream", name=content["name"], text=content["text"]
            )
        elif msg_type in ("display_data", "update_display_data"):
            new_output = nbformat.v4.new_output(
                "display_data",
                data=content["data"],
                metadata=content.get("metadata", {}),
            )
        elif msg_type == "execute_result":
            new_output = nbformat.v4.new_output(
                "execute_result",
                data=content["data"],
                metadata=content.get("metadata", {}),
                execution_count=content["execution_count"],
            )
        elif msg_type == "error":
            new_output = nbformat.v4.new_output(
                "error",
                ename=content["ename"],
                evalue=content["evalue"],
                traceback=content["traceback"],
            )
        elif msg_type == "status" and content["execution_state"] == "idle":
            break

        if new_output is not None:
            outputs.append(new_output)
            if on_output:
                on_output(outputs)

    return outputs


# -- formatting --


def fmt_outputs(outputs: list, indent: str = "  ") -> str:
    parts = []
    for out in outputs:
        otype = out.get("output_type", "")
        if otype == "stream":
            parts.append(f"{indent}-> {out['text'].rstrip()}")
        elif otype == "execute_result":
            text = out.get("data", {}).get("text/plain", "")
            if text:
                parts.append(f"{indent}=> {text.rstrip()}")
        elif otype == "display_data":
            text = out.get("data", {}).get("text/plain", "")
            if text:
                parts.append(f"{indent}:: {text.rstrip()}")
            else:
                mimes = list(out.get("data", {}).keys())
                parts.append(f"{indent}:: <{', '.join(mimes)}>")
        elif otype == "error":
            # show just the last line of traceback for compact view
            tb = out.get("traceback", [])
            if tb:
                # traceback entries have ANSI codes, show ename: evalue instead
                parts.append(f"{indent}!! {out['ename']}: {out['evalue']}")
    return "\n".join(parts)


def fmt_cell_compact(i: int, cell: dict) -> str:
    ctype = cell["cell_type"]
    src = cell["source"]
    first = src.split("\n", 1)[0]
    if len(first) > 70:
        first = first[:67] + "..."

    tag = (
        "md"
        if ctype == "markdown"
        else f"In[{cell.get('execution_count', ' ') or ' '}]"
    )
    line = f"  [{i}] {tag:>8}  {first}"

    out_str = fmt_outputs(cell.get("outputs", []), indent="              ")
    if out_str:
        line += "\n" + out_str
    return line


def fmt_cell_full(i: int, cell: dict) -> str:
    ctype = cell["cell_type"]
    src = cell["source"]
    tag = (
        "md"
        if ctype == "markdown"
        else f"In[{cell.get('execution_count', ' ') or ' '}]"
    )
    line = f"[{i}] {tag}\n{textwrap.indent(src, '  ')}"

    out_str = fmt_outputs(cell.get("outputs", []), indent="  ")
    if out_str:
        line += "\n" + out_str
    return line


def _read_source(raw: str) -> str:
    """Read cell source from stdin if raw is '-', else do \\n replacement."""
    if raw == "-":
        return sys.stdin.read()
    return raw.replace("\\n", "\n")


# -- commands --


def cmd_open(args):
    path = Path(args.path).resolve()
    if not path.exists():
        # create a new empty notebook
        nb = nbformat.v4.new_notebook()
        nbformat.write(nb, str(path))
        print(f"created {path}")
    else:
        # validate it's a readable notebook
        read_nb(str(path))

    old_state = load_state()

    # shut down existing kernel if any
    kill_kernel(old_state)

    # preserve serve_spec across kernel restart — proxy reads state.json
    # dynamically so the kernelspec doesn't need reinstalling
    prev_serve_spec = old_state.get("serve_spec")

    STATE_DIR.mkdir(exist_ok=True)

    if args.sandboxed:
        from autonomous_notebooks.podman_hpc.kernel import (
            ensure_image,
            start_container,
            wait_for_sockets,
            write_connection_file,
        )
        import shutil
        import tempfile as _tempfile

        ensure_image()
        tmpdir = _tempfile.mkdtemp(prefix="nb-ipc-")
        ipc_dir = Path(tmpdir)
        ipc_dir.chmod(0o777)
        workspace = path.parent
        container_name = f"nb-kernel-{workspace.name}"

        write_connection_file(ipc_dir)
        # keep a copy — the kernel deletes the original after binding sockets
        conn_copy = STATE_DIR / "kernel.json"
        shutil.copy2(ipc_dir / "kernel.json", conn_copy)

        start_container(
            ipc_dir,
            workspace,
            name=container_name,
            read_only_workspace=False,
        )
        wait_for_sockets(ipc_dir)

        new_state = {
            "notebook": str(path),
            "connection_file": str(conn_copy.resolve()),
            "sandboxed": True,
            "ipc_dir": str(ipc_dir),
            "container_name": container_name,
        }
        if prev_serve_spec:
            new_state["serve_spec"] = prev_serve_spec
        save_state(new_state)
        nb = read_nb(str(path))
        log.info("open notebook=%s sandboxed container=%s", path, container_name)
        print(f"opened {path} ({len(nb.cells)} cells, sandboxed kernel started)")
    else:
        conn_file = STATE_DIR / "kernel.json"
        kernel_name = args.kernel_name  # None unless --kernel-name given
        pid = start_kernel(conn_file.resolve(), kernel_name=kernel_name)
        new_state = {
            "notebook": str(path),
            "connection_file": str(conn_file.resolve()),
            "pid": pid,
        }
        if kernel_name:
            new_state["kernel_name"] = kernel_name
        if prev_serve_spec:
            new_state["serve_spec"] = prev_serve_spec
        save_state(new_state)
        nb = read_nb(str(path))
        label = f"kernel '{kernel_name}'" if kernel_name else "kernel (local)"
        log.info("open notebook=%s kernel_name=%s pid=%d", path, kernel_name, pid)
        print(f"opened {path} ({len(nb.cells)} cells, {label} started)")


def cmd_status(args):
    state = load_state()
    if not state.get("notebook"):
        print("no notebook open")
        return

    nb_path = state["notebook"]
    alive = kernel_alive(state)

    nb = read_nb(nb_path)
    print(f"notebook:   {nb_path}")
    print(f"cells:      {len(nb.cells)}")
    mode = "sandboxed" if state.get("sandboxed") else "local"
    print(f"kernel:     {'running' if alive else 'stopped'} ({mode})")

    kernel_name = state.get("kernel_name")
    if kernel_name:
        print(f"kernelspec: {kernel_name}")
    print(f"state dir:  {STATE_DIR.resolve()}")
    conn = state.get("connection_file", "")
    if conn:
        print(f"conn file:  {conn}")

    spec = state.get("serve_spec")
    if spec:
        print(f"serve:      kernelspec '{spec}' installed")


def cmd_cells(args):
    state = require_state()
    nb = read_nb(state["notebook"])
    if not nb.cells:
        print("(no cells)")
        return
    for i, cell in enumerate(nb.cells):
        print(fmt_cell_compact(i, cell))


def cmd_cell(args):
    state = require_state()
    nb = read_nb(state["notebook"])
    idx = resolve_cell_index(nb, args)
    print(fmt_cell_full(idx, nb.cells[idx]))


def cmd_insert(args):
    state = require_state()
    nb = read_nb(state["notebook"])
    source = _read_source(args.source)
    if args.md:
        cell = nbformat.v4.new_markdown_cell(source=source)
    else:
        cell = nbformat.v4.new_code_cell(source=source)
    idx = min(args.index, len(nb.cells))
    nb.cells.insert(idx, cell)
    atomic_write_nb(nb, state["notebook"])
    print(fmt_cell_full(idx, nb.cells[idx]))


def cmd_edit(args):
    state = require_state()
    nb = read_nb(state["notebook"])
    idx = resolve_cell_index(nb, args)
    source = _read_source(args.source)
    nb.cells[idx]["source"] = source
    if args.md:
        nb.cells[idx]["cell_type"] = "markdown"
        nb.cells[idx].pop("outputs", None)
        nb.cells[idx].pop("execution_count", None)
    atomic_write_nb(nb, state["notebook"])
    print(fmt_cell_full(idx, nb.cells[idx]))


def require_kernel(state: dict) -> tuple[Path, str | None]:
    """Check kernel is alive and return (connection_file, ipc_dir or None)."""
    if not kernel_alive(state):
        print("error: kernel not running. run: nb open <path>", file=sys.stderr)
        sys.exit(1)
    return Path(state["connection_file"]), state.get("ipc_dir")


def cmd_exec(args):
    state = require_state()
    conn_file, ipc_dir = require_kernel(state)
    nb_path = state["notebook"]

    nb = read_nb(nb_path)
    idx = resolve_cell_index(nb, args)

    cell = nb.cells[idx]
    if cell["cell_type"] != "code":
        print(
            f"error: cell {idx} is {cell['cell_type']}, not code",
            file=sys.stderr,
        )
        sys.exit(1)

    cell_id = cell.get("id")
    log.info("exec cell=%d notebook=%s", idx, nb_path)
    print(f"executing cell {idx}...")

    # incremental output: stream to stdout and periodically flush to disk
    last_flush = 0.0

    def _on_output(outputs: list):
        nonlocal last_flush
        # print latest output to stdout
        latest = outputs[-1]
        text = fmt_outputs([latest], indent="")
        if text:
            print(text, flush=True)

        # throttled disk flush
        now = time.monotonic()
        if now - last_flush >= FLUSH_INTERVAL:
            _flush_outputs_to_disk(nb_path, cell_id, outputs)
            last_flush = now

    client = connect_client(conn_file, ipc_dir)
    try:
        outputs = execute_code(client, cell["source"], on_output=_on_output)

        # final flush: re-read, update, write
        _flush_outputs_to_disk(nb_path, cell_id, outputs, set_execution_count=True)

        # re-read for display
        nb = read_nb(nb_path)
        result = find_cell_by_id(nb, cell_id) if cell_id else None
        if result:
            print(fmt_cell_full(result[0], result[1]))
        else:
            print(fmt_cell_full(idx, nb.cells[idx]))
    finally:
        client.stop_channels()


def _flush_outputs_to_disk(
    nb_path: str,
    cell_id: str | None,
    outputs: list,
    *,
    set_execution_count: bool = False,
):
    """Re-read notebook, find cell by ID, update its outputs, atomic write."""
    nb = read_nb(nb_path)
    target_cell = None
    if cell_id:
        result = find_cell_by_id(nb, cell_id)
        if result:
            target_cell = result[1]
    if target_cell is None:
        return
    target_cell["outputs"] = list(outputs)
    if set_execution_count:
        for out in outputs:
            if out.get("execution_count"):
                target_cell["execution_count"] = out["execution_count"]
                break
    atomic_write_nb(nb, nb_path)


def cmd_run(args):
    state = require_state()
    conn_file, ipc_dir = require_kernel(state)

    code = _read_source(args.code)
    client = connect_client(conn_file, ipc_dir)
    try:
        outputs = execute_code(client, code)
        print(fmt_outputs(outputs, indent=""))
    finally:
        client.stop_channels()


def cmd_rm(args):
    state = require_state()
    nb = read_nb(state["notebook"])
    idx = resolve_cell_index(nb, args)
    nb.cells.pop(idx)
    atomic_write_nb(nb, state["notebook"])
    print(f"deleted cell {idx}")


def cmd_save(args):
    state = require_state()
    nb = read_nb(state["notebook"])
    atomic_write_nb(nb, state["notebook"])
    print(f"saved {state['notebook']}")


def _proxy_spec_name() -> str:
    """Derive a project-scoped kernelspec name from the cwd."""
    dirname = Path.cwd().name
    sanitized = re.sub(r"[^a-z0-9]+", "-", dirname.lower()).strip("-")
    return f"nb-proxy-{sanitized}"


def cmd_serve(args):
    """Install a kernelspec that proxies to the running kernel.

    VS Code discovers this kernelspec and launches the proxy as a 'kernel'.
    The proxy reads state.json at launch to find the current connection file,
    so it survives kernel restarts without reinstalling.
    """
    state = require_state()

    if not kernel_alive(state):
        print("error: kernel not running. run: nb open <path>", file=sys.stderr)
        sys.exit(1)

    # remove old proxy kernelspec if name changed
    old_spec = state.get("serve_spec")
    spec_name = _proxy_spec_name()
    if old_spec and old_spec != spec_name:
        subprocess.run(
            ["jupyter", "kernelspec", "remove", "-y", old_spec],
            capture_output=True,
        )

    dirname = Path.cwd().name
    python = sys.executable
    spec_dir = STATE_DIR / "kernelspec" / spec_name
    spec_dir.mkdir(parents=True, exist_ok=True)
    display_name = f"Python (nb: {dirname})"

    kernel_json = {
        "argv": [
            python,
            "-m",
            "autonomous_notebooks.proxy",
            "{connection_file}",
            "--state-dir",
            str(STATE_DIR.resolve()),
        ],
        "display_name": display_name,
        "language": "python",
    }
    (spec_dir / "kernel.json").write_text(json.dumps(kernel_json, indent=2))

    # install kernelspec so VS Code discovers it
    subprocess.run(
        [
            "jupyter",
            "kernelspec",
            "install",
            str(spec_dir),
            "--user",
            f"--name={spec_name}",
        ],
        capture_output=True,
        check=True,
    )

    state["serve_spec"] = spec_name
    save_state(state)
    print(f"kernelspec '{spec_name}' installed")
    print(f"in VS Code: select kernel -> '{display_name}'")


def cmd_unserve(args):
    state = load_state()
    spec_name = state.get("serve_spec")
    if not spec_name:
        print("no proxy kernelspec installed")
        return

    subprocess.run(
        ["jupyter", "kernelspec", "remove", "-y", spec_name],
        capture_output=True,
    )
    state.pop("serve_spec", None)
    save_state(state)
    print(f"kernelspec '{spec_name}' removed")


def cmd_shutdown(args):
    state = load_state()

    # remove proxy kernelspec if installed
    spec_name = state.get("serve_spec")
    if spec_name:
        subprocess.run(
            ["jupyter", "kernelspec", "remove", "-y", spec_name],
            capture_output=True,
        )
        print(f"kernelspec '{spec_name}' removed")

    if kernel_alive(state):
        kill_kernel(state)
        log.info("shutdown kernel pid=%s", state.get("pid"))
        print("kernel stopped")
    else:
        print("no kernel running")

    # clear state
    if STATE_FILE.exists():
        STATE_FILE.unlink()


# -- argument helpers --


def _add_cell_id_arg(parser: argparse.ArgumentParser):
    """Add --id flag as alternative to positional index."""
    parser.add_argument("index", type=int, nargs="?", default=None)
    parser.add_argument("--id", dest="id", help="cell ID (alternative to index)")


# -- main --


def main():
    p = argparse.ArgumentParser(prog="nb", description="Headless notebook CLI")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("open", help="open notebook and start kernel")
    s.add_argument("path", help="path to .ipynb file (created if missing)")
    s.add_argument(
        "--kernel-name",
        default=None,
        help="kernelspec to use (default: launch ipykernel directly)",
    )
    s.add_argument(
        "--sandboxed",
        action="store_true",
        help="run kernel in container (network=none, ro filesystem)",
    )

    sub.add_parser("status", help="show kernel and notebook status")
    sub.add_parser("cells", help="list all cells (compact)")

    s = sub.add_parser("cell", help="read one cell")
    _add_cell_id_arg(s)

    s = sub.add_parser("insert", help="insert a cell")
    s.add_argument("index", type=int, help="position to insert at")
    s.add_argument("source", help="cell source (use \\\\n for newlines, - for stdin)")
    s.add_argument("--md", action="store_true", help="create a markdown cell")

    s = sub.add_parser("edit", help="overwrite cell source")
    _add_cell_id_arg(s)
    s.add_argument("source", help="new source (use \\\\n for newlines, - for stdin)")
    s.add_argument("--md", action="store_true", help="convert cell to markdown")

    s = sub.add_parser("exec", help="execute a cell")
    _add_cell_id_arg(s)

    s = sub.add_parser("run", help="execute scratch code (not saved to notebook)")
    s.add_argument("code", help="code to execute (use \\\\n for newlines, - for stdin)")

    s = sub.add_parser("rm", help="delete a cell")
    _add_cell_id_arg(s)

    sub.add_parser("save", help="save notebook to disk")
    sub.add_parser("serve", help="share kernel with VS Code via proxy kernelspec")
    sub.add_parser("unserve", help="stop sharing kernel (kernel keeps running)")
    sub.add_parser("shutdown", help="stop kernel and clean up")

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return

    _setup_logging()

    cmds = {
        "open": cmd_open,
        "status": cmd_status,
        "cells": cmd_cells,
        "cell": cmd_cell,
        "insert": cmd_insert,
        "edit": cmd_edit,
        "exec": cmd_exec,
        "run": cmd_run,
        "rm": cmd_rm,
        "save": cmd_save,
        "serve": cmd_serve,
        "unserve": cmd_unserve,
        "shutdown": cmd_shutdown,
    }
    cmds[args.cmd](args)


if __name__ == "__main__":
    main()
