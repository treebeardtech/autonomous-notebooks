"""Integration tests for containerised kernel.

Requires podman-hpc available on PATH. Skipped otherwise.
Run: uv run pytest src/autonomous_notebooks/podman_hpc/test_kernel.py -v
"""

import shutil
import textwrap

import pytest

from autonomous_notebooks.podman_hpc.kernel import ContainerKernel, ensure_image

pytestmark = pytest.mark.skipif(
    not shutil.which("podman-hpc"),
    reason="podman-hpc not available",
)


def execute(client, code: str) -> dict:
    """Run code and collect outputs by type."""
    msg_id = client.execute(code)
    result: dict = {"stdout": "", "result": "", "error": ""}
    while True:
        msg = client.get_iopub_msg(timeout=15)
        if msg["parent_header"].get("msg_id") != msg_id:
            continue
        mt = msg["msg_type"]
        if mt == "stream":
            result["stdout"] += msg["content"]["text"]
        elif mt == "execute_result":
            result["result"] = msg["content"]["data"].get("text/plain", "")
        elif mt == "error":
            result["error"] = f"{msg['content']['ename']}: {msg['content']['evalue']}"
        elif mt == "status" and msg["content"]["execution_state"] == "idle":
            break
    return result


@pytest.fixture(scope="module")
def kernel(tmp_path_factory):
    """Start a containerised kernel for the test module."""
    workspace = tmp_path_factory.mktemp("workspace")
    (workspace / "data.csv").write_text("name,value\nfoo,42\n")
    (workspace / "script.py").write_text("x = 123\n")

    ensure_image()
    ck = ContainerKernel(workspace, name="nb-kernel-test")
    ck.start()
    yield ck
    ck.stop()


def test_arithmetic(kernel):
    out = execute(kernel.client, "2 + 2")
    assert out["result"] == "4"


def test_stdout(kernel):
    out = execute(kernel.client, 'print("hello")')
    assert "hello" in out["stdout"]


def test_network_blocked(kernel):
    out = execute(
        kernel.client,
        textwrap.dedent("""\
        import socket
        socket.create_connection(("8.8.8.8", 53), timeout=2)
    """),
    )
    assert out["error"]  # should have an error
    assert "unreachable" in out["error"].lower() or "resolution" in out["error"].lower()


def test_workspace_readable(kernel):
    out = execute(kernel.client, 'print(open("data.csv").read())')
    assert "foo,42" in out["stdout"]


def test_workspace_readonly(kernel):
    out = execute(kernel.client, 'open("output.txt", "w").write("bad")')
    assert "Read-only" in out["error"]


def test_home_isolated(kernel):
    # /home should be empty (no host home mounted)
    out = execute(kernel.client, "import os; print(os.listdir('/home'))")
    # should not contain the host user's home directory contents
    assert ".claude" not in out["stdout"]


def test_cwd_is_workspace(kernel):
    out = execute(kernel.client, "import os; print(os.getcwd())")
    assert "/workspace" in out["stdout"]


def test_state_persists_across_calls(kernel):
    execute(kernel.client, "x = 999")
    out = execute(kernel.client, "print(x)")
    assert "999" in out["stdout"]
