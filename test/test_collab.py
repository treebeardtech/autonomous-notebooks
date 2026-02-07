"""Tests for collaboration features: atomic writes, mtime detection, cell-ID addressing,
incremental output callback, and server lifecycle helpers."""

import json
import os
import time

import nbformat

from autonomous_notebooks.cli2 import (
    atomic_write_nb,
    find_cell_by_id,
    read_nb,
    read_nb_with_mtime,
    write_nb_if_unchanged,
)


# -- atomic_write_nb --


def test_atomic_write_creates_file(tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(source="x = 1"))
    path = str(tmp_path / "test.ipynb")
    # write an initial file so atomic_write can target it
    nbformat.write(nb, path)

    nb.cells[0]["source"] = "x = 2"
    atomic_write_nb(nb, path)

    result = read_nb(path)
    assert result.cells[0]["source"] == "x = 2"


def test_atomic_write_no_leftover_tmp(tmp_path):
    nb = nbformat.v4.new_notebook()
    path = str(tmp_path / "test.ipynb")
    nbformat.write(nb, path)

    atomic_write_nb(nb, path)

    tmp_files = list(tmp_path.glob("*.ipynb.tmp"))
    assert tmp_files == []


def test_atomic_write_to_new_file(tmp_path):
    nb = nbformat.v4.new_notebook()
    path = str(tmp_path / "brand_new.ipynb")
    atomic_write_nb(nb, path)
    result = read_nb(path)
    assert result.cells == []


# -- read_nb_with_mtime --


def test_read_nb_with_mtime(tmp_path):
    nb = nbformat.v4.new_notebook()
    path = str(tmp_path / "test.ipynb")
    nbformat.write(nb, path)

    _, mtime = read_nb_with_mtime(path)
    assert abs(mtime - os.path.getmtime(path)) < 0.01


# -- write_nb_if_unchanged --


def test_write_nb_if_unchanged_succeeds(tmp_path):
    nb = nbformat.v4.new_notebook()
    path = str(tmp_path / "test.ipynb")
    nbformat.write(nb, path)
    mtime = os.path.getmtime(path)

    nb.cells.append(nbformat.v4.new_code_cell(source="y = 1"))
    result = write_nb_if_unchanged(nb, path, mtime)
    assert result is True

    loaded = read_nb(path)
    assert len(loaded.cells) == 1


def test_write_nb_if_unchanged_warns_on_conflict(tmp_path, capsys):
    nb = nbformat.v4.new_notebook()
    path = str(tmp_path / "test.ipynb")
    nbformat.write(nb, path)
    old_mtime = os.path.getmtime(path)

    # simulate external modification
    time.sleep(0.05)
    nbformat.write(nb, path)

    nb.cells.append(nbformat.v4.new_code_cell(source="y = 1"))
    write_nb_if_unchanged(nb, path, old_mtime)

    captured = capsys.readouterr()
    assert "modified externally" in captured.err


# -- find_cell_by_id --


def test_find_cell_by_id_found(tmp_path):
    nb = nbformat.v4.new_notebook()
    cell = nbformat.v4.new_code_cell(source="a = 1")
    cell["id"] = "test-cell-id-123"
    nb.cells.append(cell)

    result = find_cell_by_id(nb, "test-cell-id-123")
    assert result is not None
    idx, found_cell = result
    assert idx == 0
    assert found_cell["source"] == "a = 1"


def test_find_cell_by_id_not_found(tmp_path):
    nb = nbformat.v4.new_notebook()
    nb.cells.append(nbformat.v4.new_code_cell(source="a = 1"))
    result = find_cell_by_id(nb, "nonexistent")
    assert result is None


def test_find_cell_by_id_multiple_cells():
    nb = nbformat.v4.new_notebook()
    for i in range(3):
        cell = nbformat.v4.new_code_cell(source=f"x = {i}")
        cell["id"] = f"cell-{i}"
        nb.cells.append(cell)

    result = find_cell_by_id(nb, "cell-1")
    assert result is not None
    assert result[0] == 1
    assert result[1]["source"] == "x = 1"


# -- execute_code on_output callback --


def test_execute_code_callback_called(tmp_path):
    """Test that on_output callback is invoked (mocked kernel client)."""
    from unittest.mock import MagicMock

    from autonomous_notebooks.cli2 import execute_code

    client = MagicMock()
    client.execute.return_value = "msg-123"

    # simulate a stream message followed by idle
    client.get_iopub_msg.side_effect = [
        {
            "parent_header": {"msg_id": "msg-123"},
            "msg_type": "stream",
            "content": {"name": "stdout", "text": "hello\n"},
        },
        {
            "parent_header": {"msg_id": "msg-123"},
            "msg_type": "status",
            "content": {"execution_state": "idle"},
        },
    ]

    callback_calls: list[list] = []
    outputs = execute_code(
        client, "print('hello')", on_output=lambda o: callback_calls.append(list(o))
    )

    assert len(outputs) == 1
    assert outputs[0]["output_type"] == "stream"
    assert len(callback_calls) == 1
    assert callback_calls[0][0]["text"] == "hello\n"


# -- proxy: zmq_addr --


def test_zmq_addr_tcp():
    from autonomous_notebooks.proxy import zmq_addr

    info = {"ip": "127.0.0.1", "shell_port": 5555, "transport": "tcp"}
    assert zmq_addr(info, "shell_port") == "tcp://127.0.0.1:5555"


def test_zmq_addr_ipc():
    from autonomous_notebooks.proxy import zmq_addr

    info = {"ip": "/tmp/kernel", "shell_port": 5555, "transport": "ipc"}
    assert zmq_addr(info, "shell_port") == "ipc:///tmp/kernel-5555"


def test_zmq_addr_default_transport():
    """Default transport is tcp when not specified."""
    from autonomous_notebooks.proxy import zmq_addr

    info = {"ip": "0.0.0.0", "iopub_port": 9999}
    assert zmq_addr(info, "iopub_port") == "tcp://0.0.0.0:9999"


# -- proxy: resign --


def test_resign_message():
    from autonomous_notebooks.proxy import DELIM, make_signer, resign

    signer = make_signer(b"new-key")
    header = b'{"msg_type":"execute_request"}'
    parent = b"{}"
    metadata = b"{}"
    content = b'{"code":"x=1"}'
    old_hmac = b"old-signature"

    frames = [b"ident", DELIM, old_hmac, header, parent, metadata, content]
    result = resign(frames, signer)

    # structure: ident, DELIM, new_hmac, header, parent, metadata, content
    assert result[0] == b"ident"
    assert result[1] == DELIM
    assert result[2] != old_hmac  # HMAC was replaced
    assert result[3:] == [header, parent, metadata, content]

    # verify the new HMAC is correct
    expected = signer([header, parent, metadata, content])
    assert result[2] == expected


def test_resign_non_jupyter():
    """Frames without DELIM are passed through unchanged."""
    from autonomous_notebooks.proxy import make_signer, resign

    signer = make_signer(b"key")
    frames = [b"just", b"raw", b"bytes"]
    result = resign(frames, signer)
    assert result == frames


# -- proxy: load_kernel_info --


def test_load_kernel_info_tcp(tmp_path):
    from autonomous_notebooks.proxy import load_kernel_info

    conn_data = {
        "ip": "127.0.0.1",
        "shell_port": 5555,
        "transport": "tcp",
        "key": "abc",
    }
    conn_file = tmp_path / "kernel.json"
    conn_file.write_text(json.dumps(conn_data))

    state = {"connection_file": str(conn_file), "notebook": "test.ipynb"}
    (tmp_path / "state.json").write_text(json.dumps(state))

    result = load_kernel_info(tmp_path)
    assert result["ip"] == "127.0.0.1"
    assert result["shell_port"] == 5555


def test_load_kernel_info_sandboxed(tmp_path):
    """Sandboxed kernels get ip overridden to host-side IPC path."""
    from autonomous_notebooks.proxy import load_kernel_info

    ipc_dir = tmp_path / "ipc"
    ipc_dir.mkdir()

    conn_data = {
        "ip": "127.0.0.1",
        "shell_port": 5555,
        "transport": "ipc",
        "key": "abc",
    }
    conn_file = tmp_path / "kernel.json"
    conn_file.write_text(json.dumps(conn_data))

    state = {
        "connection_file": str(conn_file),
        "notebook": "test.ipynb",
        "ipc_dir": str(ipc_dir),
    }
    (tmp_path / "state.json").write_text(json.dumps(state))

    result = load_kernel_info(tmp_path)
    assert result["ip"] == str(ipc_dir / "kernel")
