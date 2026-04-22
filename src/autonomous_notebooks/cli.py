"""Minimal admin CLI. Primary interface is the MCP server."""

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import time
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


def cmd_watch(args: argparse.Namespace) -> None:
    """Tail the nb-mcp log for a single job; exit when the job ends.

    Designed for Claude Code's Monitor tool:
        Monitor(command='uv run nb watch --job abc123')
    Each stdout line is one event notification.
    """
    log_path = Path(os.environ.get("NB_MCP_LOG_PATH", ".nb_mcp.log"))
    job_id: str | None = args.job
    target_path: str | None = args.path

    # Line-buffered so Monitor receives events promptly.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

    # Wait a short while for the log file to exist — MCP may be starting up
    # or the job may not have triggered any log writes yet.
    deadline = time.monotonic() + args.startup_timeout
    while not log_path.exists():
        if time.monotonic() > deadline:
            print(
                f"[nb watch] no log file at {log_path} after "
                f"{args.startup_timeout}s — is the MCP server running?",
                file=sys.stderr,
            )
            sys.exit(2)
        time.sleep(0.3)

    print(
        f"[nb watch] tailing {log_path} "
        f"(job={job_id or '?'}, path={target_path or '?'})"
    )

    f = log_path.open("r")
    seen_job_start = False

    # Read once from the top so a fast-starting job isn't missed. After we've
    # exhausted existing content we switch to tailing new lines.
    while True:
        line = f.readline()
        if not line:
            if seen_job_start is False and time.monotonic() > deadline:
                print(
                    f"[nb watch] no matching job within "
                    f"{args.startup_timeout}s — giving up",
                    file=sys.stderr,
                )
                sys.exit(3)
            time.sleep(0.3)
            continue

        event = _format_event(line, job_id=job_id, target_path=target_path)
        if event is None:
            continue

        # Latch job_id from the "submitted" line when the caller didn't give
        # us one (they had only a path).
        if job_id is None:
            m = re.search(r"job (\w+) submitted", line)
            if m:
                job_id = m.group(1)

        seen_job_start = True
        print(event)

        if " complete" in line or " crashed" in line:
            break

    f.close()


# Matches the formatter in _log.py: "2026-04-22 11:34:47+0000 INFO ..."
_LINE_RE = re.compile(
    r"^(?P<ts>\S+\s\S+)\s+(?P<level>\S+)\s+(?P<name>[^:]+):\s+(?P<msg>.*)$"
)


def _format_event(
    line: str, *, job_id: str | None, target_path: str | None
) -> str | None:
    """Turn a log line into a human-readable event line, or None to skip."""
    m = _LINE_RE.match(line.rstrip("\n"))
    if not m:
        return None
    ts, _level, _name, msg = (
        m.group("ts"),
        m.group("level"),
        m.group("name"),
        m.group("msg"),
    )

    # Filter to events for our job / path.
    if job_id is not None and f"job {job_id}" not in msg:
        # Allow path-tagged kernel lifecycle events through too.
        if not (target_path and target_path in msg):
            return None
    if job_id is None and target_path is not None and target_path not in msg:
        return None
    if job_id is None and target_path is None:
        return None

    # Short ts: "HH:MM:SS".
    short_ts = ts.split()[-1]
    short_ts = short_ts.split("+")[0]

    return f"{short_ts}  {msg}"


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

    watch = sub.add_parser(
        "watch",
        help="tail the mcp log for one job; emit one line per event, "
        "exit when the job ends. Designed for Claude Code's Monitor tool.",
    )
    watch.add_argument(
        "--job",
        help="job id (preferred — match precisely). If omitted, --path must be given.",
    )
    watch.add_argument(
        "--path",
        help="notebook path (as passed to the exec tool). Used to match jobs "
        "when --job is unknown, and to include kernel lifecycle events.",
    )
    watch.add_argument(
        "--startup-timeout",
        type=int,
        default=30,
        help="seconds to wait for the job's first log line before giving up (default 30)",
    )

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return

    if args.cmd == "watch" and not args.job and not args.path:
        watch.error("provide at least one of --job or --path")

    {"mcp": cmd_mcp, "cleanup": cmd_cleanup, "watch": cmd_watch}[args.cmd](args)


if __name__ == "__main__":
    main()
