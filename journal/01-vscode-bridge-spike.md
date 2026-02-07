# VS Code Notebook Bridge — Spike

**Date:** 2025-02-07
**Status:** Working prototype, limitations identified

## Question

How can an agent execute a notebook cell so it looks identical to a human clicking Run?

## The Crux

Jupyter kernels are multi-client (IOPub broadcasts to everyone), but **cell-output association is client-side**. VS Code tracks which `msg_id` it sent and matches outputs back to cells. An external client sending its own `execute_request` produces orphaned outputs — the kernel runs the code, but VS Code doesn't know which cell to put the results in.

## Approach: VS Code Extension Bridge

Thin JS extension (~120 lines) that starts an HTTP server on `127.0.0.1:18741`. Routes map to VS Code's notebook API — so executing a cell goes through the exact same codepath as clicking Run.

```
Agent  →  HTTP  →  VS Code Extension  →  vscode.commands('notebook.cell.execute')
                                          ↓
                                     same kernel, same UI, same outputs
```

**API surface:**
- `GET /cells` — read all cells
- `GET /cells/:i` — read one cell
- `POST /cells/:i/execute` — run cell, wait for result
- `POST /cells/:i` — insert cell
- `PUT /cells/:i` — edit cell source
- `DELETE /cells/:i` — delete cell

Tested with a small Python CLI (`uv run nb exec 0`) — inserts cells, executes them, outputs appear in VS Code and return to the agent.

## What Worked

- Extension is trivial: `package.json` + one JS file, no build step, install via symlink
- `executeCommand('notebook.cell.execute')` awaits kernel completion — no need for execution state events
- Cell outputs serialize cleanly (text as utf-8, binary as base64)

## What Didn't

- `vscode.notebooks.onDidChangeNotebookCellExecutionState` doesn't exist in our VS Code version — replaced with polling on `executionSummary.executionOrder`
- Must capture execution order *before* `await executeCommand`, not after (otherwise poll sees the already-updated value and spins forever)

## Limitation: Not Suitable for Unattended Operation

This architecture requires VS Code to be open with a notebook focused. For autonomous agents running while the human is away:

1. **VS Code must be running** — extension dies with the editor
2. **Notebook must be the active editor** — uses `activeNotebookEditor`
3. **Kernel lifecycle is unmanaged** — no restart on crash
4. **SSH disconnect kills everything**

## Next Step

For unattended mode, bypass VS Code entirely and talk to the **Jupyter server REST API + kernel WebSocket**. The server runs headless, survives disconnects, and manages kernels. VS Code becomes just a frontend that reconnects to see results. Two modes:

- **Collaborative** (human present): VS Code bridge (this spike)
- **Autonomous** (human away): direct Jupyter server API
