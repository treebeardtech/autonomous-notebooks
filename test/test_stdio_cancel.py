"""Stdio integration: a request the client cancels must never be answered.

Answering it made Claude Code log "Received a response for an unknown message
ID" and drop the connection, which killed the server and its kernels.
"""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path


def _rpc(**msg) -> str:
    return json.dumps({"jsonrpc": "2.0", **msg}) + "\n"


def test_cancelled_request_gets_no_response_and_server_survives(tmp_path: Path):
    nb = str(tmp_path / "p.ipynb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "autonomous_notebooks.cli", "mcp"],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": str(Path(__file__).parent.parent / "src"),
            "NB_MCP_LOG_PATH": str(tmp_path / "nb_mcp.log"),
            # Long enough that the cancel lands while the tool is still blocking.
            "NB_MCP_BLOCK_FOR_SEC": "6",
        },
    )
    assert proc.stdin and proc.stdout
    received: list[dict] = []

    def reader() -> None:
        assert proc.stdout
        for line in proc.stdout:
            received.append(json.loads(line))

    threading.Thread(target=reader, daemon=True).start()

    def send(**msg) -> None:
        assert proc.stdin
        proc.stdin.write(_rpc(**msg))
        proc.stdin.flush()

    try:
        send(
            id=1,
            method="initialize",
            params={
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        )
        time.sleep(1)
        send(method="notifications/initialized")
        send(
            id=2,
            method="tools/call",
            params={
                "name": "insert_and_exec",
                "arguments": {
                    "notebook_path": nb,
                    "index": 0,
                    "source": "import time; time.sleep(15)",
                },
            },
        )
        time.sleep(2)
        send(
            method="notifications/cancelled", params={"requestId": 2, "reason": "user"}
        )
        time.sleep(7)  # past the grace period — a 1.x-style error reply would land here
        send(id=3, method="tools/call", params={"name": "status", "arguments": {}})
        time.sleep(2)

        ids = [m.get("id") for m in received]
        assert 1 in ids, received
        assert 2 not in ids, f"cancelled request was answered: {received}"
        assert 3 in ids, f"server did not answer after the cancel: {received}"
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
