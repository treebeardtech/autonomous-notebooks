# 13 — Async execution and notebook progress

## TL;DR

Exec tools now return instantly. Execution runs in a background thread per notebook, and the notebook file itself becomes the live progress view — cells show timestamps and status markers as they run.

## Why

With the MCP rewrite (journal 12), long-running cells blocked the entire tool response. The agent couldn't do anything else while a cell ran, and the user stared at a static notebook with no indication of progress. For a training loop or heavy simulation this meant minutes of silence from both the agent and the notebook.

## What changed

**New module: `jobs.py`** — per-notebook background execution backed by daemon threads. `submit_execution` marks cells on disk, starts a thread, returns a `Job` immediately. One active job per notebook; second submissions are rejected with a clear message.

**Async MCP tools** — `exec_cell`, `exec_range`, `exec_all`, `insert_and_exec` are now `async def` and delegate to `jobs.submit_execution`. `run_scratch` stays blocking (via `anyio.to_thread.run_sync`) since callers need the output inline.

**New tool: `exec_status`** — check progress mid-execution. Returns per-cell status and elapsed time.

**Thread safety in `kernels.py`** — `threading.Lock` protects the kernel dict with a double-check pattern in `get_or_start` to handle startup races.

**Notebook progress markers** — cells transition visually:

| Phase   | Cell output                                     |
|---------|-------------------------------------------------|
| Queued  | `⏳ Queued`                                     |
| Running | `⏳ Running (2/5)... (started 14:32:05)`        |
| Done    | [real outputs] + `✓ 3.2s`                       |
| Error   | [real error traceback] + `✗ 1.1s`              |
| Skipped | `⊘ Skipped (earlier cell errored)`              |

## Shape

```
Claude Code ──stdio──> nb mcp (FastMCP, async)
                        ├─ nb_io          pure nbformat
                        ├─ exec_runner    execute_code + per-message flush
                        ├─ jobs           background threads + status markers
                        └─ kernels        thread-safe {path → kernel}
```

## Tests

34 passing. New tests for `exec_status` and conflict detection. Existing exec tests updated with `_run_and_wait` helper to handle the async-then-poll pattern.

## What it looks like

```
> exec_all(research.ipynb)
executing 7 cells (job 1a76630b)

> exec_status(research.ipynb)
job 1a76630b: running (7 cells)
  [1] done 0.4s
  [3] done 0.0s
  [5] running 2.6s
  [7] queued
  ...
```

Meanwhile the notebook in VS Code shows cells flipping through Queued → Running → output with timing.
