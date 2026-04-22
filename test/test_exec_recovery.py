"""Simulate iopub framing desyncs and confirm exec_code recovers instead of crashing."""

from unittest.mock import MagicMock

from autonomous_notebooks.exec_runner import execute_code


class _FakeMsg(dict):
    pass


def _status_idle(msg_id: str) -> _FakeMsg:
    return _FakeMsg(
        parent_header={"msg_id": msg_id},
        msg_type="status",
        content={"execution_state": "idle"},
    )


def _stream(msg_id: str, text: str) -> _FakeMsg:
    return _FakeMsg(
        parent_header={"msg_id": msg_id},
        msg_type="stream",
        content={"name": "stdout", "text": text},
    )


def test_recovers_from_transient_iopub_parse_error():
    """One ValueError on iopub → client rebuilt → exec continues."""
    broken = MagicMock()
    fixed = MagicMock()
    broken.execute.return_value = "abc123"

    # broken client: first call works, second raises, then we're replaced
    broken.get_iopub_msg.side_effect = [
        _stream("abc123", "hello\n"),
        ValueError("'<IDS|MSG>' is not in list"),
    ]
    # fixed client (returned by recover_fn): finishes cleanly
    fixed.get_iopub_msg.side_effect = [
        _stream("abc123", "world\n"),
        _status_idle("abc123"),
    ]

    recoveries: list[int] = []

    def recover() -> MagicMock:
        recoveries.append(1)
        return fixed

    outputs = execute_code(broken, "doesnt_matter", timeout=10, recover_fn=recover)

    assert len(recoveries) == 1
    texts = [o["text"] for o in outputs if o["output_type"] == "stream"]
    assert "hello\n" in texts
    assert "world\n" in texts
    assert not any("iopub desync" in t for t in texts)


def test_gives_up_after_max_recoveries():
    """If parse errors keep firing past the cap, we emit a desync marker and return."""
    broken = MagicMock()
    broken.execute.return_value = "xyz"
    broken.get_iopub_msg.side_effect = ValueError("'<IDS|MSG>' is not in list")

    recoveries: list[int] = []

    def recover() -> MagicMock:
        recoveries.append(1)
        # return the same broken client so parsing keeps failing
        return broken

    outputs = execute_code(broken, "doesnt_matter", timeout=10, recover_fn=recover)
    assert len(recoveries) == 3  # max_recovers
    texts = [o["text"] for o in outputs if o["output_type"] == "stream"]
    assert any("iopub desync" in t for t in texts)


def test_no_recover_fn_means_immediate_desync_marker():
    """Without a recover_fn, the first parse error should still not crash — it breaks out."""
    broken = MagicMock()
    broken.execute.return_value = "xyz"
    broken.get_iopub_msg.side_effect = ValueError("'<IDS|MSG>' is not in list")

    outputs = execute_code(broken, "doesnt_matter", timeout=10)
    texts = [o["text"] for o in outputs if o["output_type"] == "stream"]
    assert any("iopub desync" in t for t in texts)


def test_kernel_died_emits_error_output_and_distinct_marker():
    """When recover_fn raises KernelDeadError, we emit an nbformat error
    (so the job is marked ERROR) and a clear 'kernel died' marker — not a
    generic 'iopub desync' one."""
    from autonomous_notebooks.kernels import KernelDeadError

    broken = MagicMock()
    broken.execute.return_value = "xyz"
    broken.get_iopub_msg.side_effect = ValueError("'<IDS|MSG>' is not in list")

    def recover() -> MagicMock:
        raise KernelDeadError("kernel for /tmp/x.ipynb has died")

    outputs = execute_code(broken, "doesnt_matter", timeout=10, recover_fn=recover)

    # Has an error output so the outer job marks this cell as errored.
    errors = [o for o in outputs if o["output_type"] == "error"]
    assert len(errors) == 1
    assert errors[0]["ename"] == "NbMcpKernelDied"
    # The traceback carries our "kernel died" marker, not "iopub desync".
    tb = "\n".join(errors[0]["traceback"])
    assert "kernel died mid-execution" in tb
    assert "iopub desync" not in tb
