# Headless CLI — Direct Kernel Management

**Date:** 2026-02-07
**Status:** Working

## Question

Can we run notebooks headless (no VS Code, no Jupyter server) so an agent can work while the human is away?

## Approach: `jupyter_client` + `nbformat` directly

Skip the Jupyter server entirely. Start kernels as subprocesses via `jupyter_client.KernelManager`, read/write `.ipynb` files via `nbformat`. CLI persists kernel connection across invocations.

```
Agent  →  nb CLI  →  jupyter_client (kernel subprocess)
                  →  nbformat (.ipynb on disk)
```

## What Worked

- `KernelManager` starts ipykernel as a subprocess — no server needed
- Output capture via iopub channel is straightforward: collect `stream`, `execute_result`, `display_data`, `error` messages, convert to nbformat output objects, attach to cell
- Standard `.ipynb` output — VS Code opens it natively with all outputs visible
- `nb run` for scratch execution is useful for agents probing state without polluting the notebook

## Key Problem: Kernel Dying on CLI Exit

`KernelManager` sets `JPY_PARENT_PID` in the kernel's environment by default. The kernel monitors this PID and self-terminates when it disappears. Also, `KernelManager.__del__` calls `cleanup_connection_file()`.

**Fix:** `km.start_kernel(independent=True)` skips `JPY_PARENT_PID`, and setting `km._connection_file_written = False` prevents `__del__` from deleting the connection file. Track the kernel PID ourselves in state.json for lifecycle management.

## State Management

Minimal state in `.nb/`:
- `state.json` — notebook path, connection file path, kernel PID
- `kernel.json` — jupyter connection file (ports, key, transport)

`kernel_alive()` checks PID via `os.kill(pid, 0)` — fast, no network round-trip.

## CLI Surface

```
nb open <path>       # start kernel, set active notebook
nb cells             # compact listing
nb cell <i>          # full cell + outputs
nb insert <i> <src>  # add code cell
nb edit <i> <src>    # overwrite source
nb exec <i>          # execute, capture outputs into .ipynb
nb run <code>        # scratch execution (not saved)
nb rm <i>            # delete cell
nb save              # explicit save
nb shutdown          # kill kernel, clean up
nb status            # notebook path, cell count, kernel state
```

## Next Steps

- MCP wrapper (thin layer over the same operations as tools)
- Containerised kernelspec (Podman) for sandboxing
