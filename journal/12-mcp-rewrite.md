# 12 — Rewrite as stdio MCP server

## TL;DR

Dropped sandbox + serve/proxy, replaced the tiered Bash CLI with a FastMCP stdio server. Kernels are now held in-process in the server, keyed by notebook path, and die with Claude Code. Read/edit tools don't touch a kernel.

## Why

Feedback + journal 11 pointed at three things: Bash heredocs were awful for multi-line edits, `.nb/state.json` enforced a pointless "active notebook" constraint, and the sandbox added a lot of surface for little real security. MCP sidesteps shell escaping entirely, and running the server as a subprocess of Claude Code gives us free lifecycle management.

## Shape

```
Claude Code ──stdio──> nb mcp (FastMCP)
                        ├─ nb_io          pure nbformat
                        ├─ exec_runner    execute_code + per-message flush
                        └─ kernels        {path → (KernelManager, client)}
```

Tools: `list_cells`, `read_cell`, `insert_cell`, `set_cell`, `delete_cell`, `clear_outputs`, `exec_cell`, `exec_range`, `exec_all`, `run_scratch`, `insert_and_exec`, `interrupt`, `shutdown_kernel`. All take `notebook_path` and auto-create missing `.ipynb` files.

CLI collapsed to two subcommands: `nb mcp` (runs the server) and `nb cleanup` (kills stray kernels, removes `.nb/`).

## Removed

- `podman_hpc/` — whole sandbox module
- `proxy.py` + `serve`/`unserve` VS Code bridge
- `cli2.py` and its tiered subcommands
- `test/test_kernel.py`, `test/test_collab.py`
- `pytorch` index + `ai` dep group (only relevant to the sandbox)

## Tests

32 passing. `test_nb_io.py` covers pure I/O, `test_kernels.py` covers pool start/reuse/shutdown, `test_server_tools.py` drives the MCP tool functions directly (insert+exec, two concurrent notebooks, shutdown-and-restart, error-halts-exec_all).

## Deferred

- VS Code kernel sharing — would need an IPC path into the server process. Revisit if there's demand.
- Background/async exec — long cells still block the tool response. Worth a round 13.
