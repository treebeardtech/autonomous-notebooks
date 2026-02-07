# 04 — Human-Agent Collaboration

## Decision

Add incremental output during execution and a `nb serve` command for live collaboration via VS Code.

## What works

- **Atomic writes**: all notebook writes go through temp file + `os.rename()`. Prevents VS Code from reading a half-written file.
- **Incremental output**: `execute_code()` takes an `on_output` callback. `cmd_exec` streams each output to stdout and flushes to disk every 2s (re-reads notebook before each write to preserve human edits).
- **Cell-ID addressing**: `--id` flag on cell/edit/exec/rm as stable alternative to index.
- **Mtime conflict detection**: `write_nb_if_unchanged()` warns when file was modified externally. Last writer wins.
- **Sandboxed kernel**: still works — network blocked, workspace read-only, confirmed with test notebook.
- **`nb serve` — ZMQ proxy for VS Code kernel sharing**: VS Code can connect to the agent's running kernel (including sandboxed) via a proxy kernelspec. Tested end-to-end: variables set by agent visible in VS Code.

## How `nb serve` works (ZMQ proxy approach)

The provisioner approach (`ExistingKernelProvisioner`) hit a wall with `_reconcile_connection_info`. The working solution uses a **ZMQ proxy** instead:

1. `nb serve` installs a kernelspec ("Python (nb shared kernel)") whose `argv` launches `proxy.py`
2. VS Code discovers the kernelspec and launches the proxy as if it were a kernel
3. The proxy reads two connection files: VS Code's (TCP) and the existing kernel's (IPC or TCP)
4. For each channel (shell, iopub, stdin, control, heartbeat), the proxy:
   - Binds on VS Code's ports (frontend)
   - Connects to the kernel's sockets (backend)
   - Forwards messages, re-signing HMAC between the two keys
5. `nb unserve` removes the kernelspec

No Jupyter server needed. No monkey-patching of jupyter internals.

### Why the provisioner approach failed

The KernelManager was designed to _own_ the kernel lifecycle. `_reconcile_connection_info` compares the KM's in-memory state with what the provisioner returned — when the existing kernel uses IPC/different ports, it raises ValueError. Subclassing KernelManager to skip the check would work but is fragile across jupyter_client versions.

## Architecture (current)

```
Agent  -->  nb CLI  -->  BlockingKernelClient (ZMQ)  --+
                                                       +--> kernel (local or sandboxed)
Human  -->  VS Code  -->  proxy.py (ZMQ bridge)  ------+
                          (launched via kernelspec)
```

Both agent and human share the same kernel state. The notebook `.ipynb` file is also shared via atomic writes.

## Files changed

| File | What |
|------|------|
| `cli2.py` | Atomic writes, incremental output, `--id` flag, serve/unserve via proxy kernelspec |
| `proxy.py` | ZMQ proxy: bridges VS Code TCP to kernel IPC/TCP, re-signs HMAC |
| `server.py` | ExistingKernelProvisioner (dead code — kept for reference) |
| `test/test_collab.py` | 16 tests for atomic write, mtime, cell-ID, callback, provisioner |
| `pyproject.toml` | Added `jupyter-server` dep, provisioner entry point |

## Next steps — making serve reliable

### Must fix

- **Notebook kernelspec auto-selection**: `nb serve` sets the notebook metadata to `nb-proxy`, but VS Code often ignores this and uses the cached/default kernel. Need to understand VS Code's kernel selection priority — may need to set it in workspace settings instead of notebook metadata.
- **Proxy lifecycle visibility**: currently the proxy is a fire-and-forget child process of VS Code. If it crashes (e.g. kernel dies, IPC sockets disappear), VS Code shows a generic "kernel disconnected" error. The proxy should log to a known location (`.nb/proxy.log`) so failures are diagnosable.
- **`nb serve` after kernel restart**: if the kernel is shut down and reopened, the target connection file changes (new IPC dir). The installed kernelspec still points to the old path. `nb serve` should detect this and reinstall, or the kernelspec should read the target path from `.nb/state.json` at launch time instead of baking it in.
- **Clean up dead code**: `server.py` (ExistingKernelProvisioner) is dead. Remove it and the `jupyter-server` dependency. The provisioner entry point in `pyproject.toml` can go too.

### Should do

- **Test the proxy properly**: current tests cover the old provisioner approach. Need tests for `proxy.py` — at minimum: resign logic, address construction, and an integration test that launches the proxy and sends a message through it (like the manual test we ran).
- **Concurrent execution**: both agent and VS Code can send execute_request simultaneously. The kernel handles this (it queues), but outputs on iopub are interleaved. The proxy forwards everything — each side sees the other's outputs. This is correct but may be surprising. Document the behaviour; consider whether iopub filtering by msg_id is needed.
- **Graceful proxy shutdown**: when VS Code kills the proxy (SIGTERM), sockets should be closed cleanly. Currently daemon threads just die. Add signal handler that closes the ZMQ context.
- **`nb unserve` on shutdown**: `nb shutdown` removes the kernelspec, but if the agent crashes without running shutdown, the stale kernelspec remains. Could check on next `nb open` and clean up.

### Nice to have

- **Multiple VS Code clients**: the proxy currently tracks a single `client_id` for ROUTER routing. If two VS Code windows connect to the same proxy, the second client's replies would be misrouted. Use a dict of identities or let ZMQ ROUTER handle it properly.
- **Status indicator**: `nb status` could show whether a proxy is currently connected (check if the kernelspec is installed and if any proxy processes are running).
- **Proxy without kernelspec install**: the `jupyter kernelspec install --user` step is global — it pollutes the user's kernel list even for other projects. Consider a project-local kernelspec dir or a VS Code workspace setting that points directly to the kernelspec.
