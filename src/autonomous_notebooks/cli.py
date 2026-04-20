"""Minimal admin CLI. Primary interface is the MCP server."""

import argparse
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path


def cmd_mcp(_args: argparse.Namespace) -> None:
    """Run the stdio MCP server. Claude Code invokes this via .mcp.json."""
    from autonomous_notebooks.server import main as server_main

    server_main()


def cmd_cleanup(_args: argparse.Namespace) -> None:
    """Best-effort cleanup: kill stray ipykernel processes, remove local .nb/."""
    killed = _kill_stray_ipykernels()
    print(f"killed {killed} stray ipykernel process(es)")

    nb_dir = Path(".nb")
    if nb_dir.exists():
        shutil.rmtree(nb_dir)
        print(f"removed {nb_dir.resolve()}")


def _kill_stray_ipykernels() -> int:
    """Terminate ipykernel_launcher processes owned by this uid."""
    try:
        out = subprocess.run(
            ["pgrep", "-u", str(os.getuid()), "-f", "ipykernel_launcher"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("pgrep not available; skipping process kill", file=sys.stderr)
        return 0
    pids = [int(p) for p in out.stdout.split() if p.strip()]
    n = 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            n += 1
        except OSError:
            pass
    return n


def main() -> None:
    p = argparse.ArgumentParser(
        prog="nb",
        description="Autonomous notebooks — admin CLI. Primary interface is the MCP server.",
    )
    sub = p.add_subparsers(dest="cmd", required=False)

    sub.add_parser("mcp", help="run the stdio MCP server")
    sub.add_parser(
        "cleanup", help="kill stray kernels and remove leftover .nb/ directory"
    )

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return

    {"mcp": cmd_mcp, "cleanup": cmd_cleanup}[args.cmd](args)


if __name__ == "__main__":
    main()
