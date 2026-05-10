# 15 — Monitor-driven observability, exec hardening

## TL;DR

The blocking `wait` MCP tool (journal 14) is gone. Progress is now a
stream of stderr-styled markers in the cell itself plus a filterable
log file, tailed by a new `nb watch` CLI that plugs directly into
Claude Code's Monitor tool. Along the way: real wall-clock timeouts,
recoverable ZMQ framing errors, id backfill for hand-edited
notebooks, and a global `status` tool.

## Why

`wait(timeout=N)` held a tool slot for up to N seconds. For a
10-minute training job that meant the agent couldn't talk to the
user or do other work. Claude Code shipped a Monitor tool that
streams events as stdout lines — much better UX. This journal
covers the pivot and everything the pivot forced us to clean up.

## What changed

**Observability**

- All MCP-injected cell markers (`⏳ Queued`, `▶ Running`, `✓ Done`,
  `✗ Error`, `⊘ Skipped`) carry an `[nb mcp]` prefix and render as
  stderr stream outputs — visually distinct from the cell's own
  stdout in VS Code / Jupyter. Running banner stays pinned at the
  top of the cell's outputs throughout streaming; Done/Error footers
  include wall-clock timestamp + timezone + elapsed.
- File logger at `./.nb_mcp.log` (INFO default). Covers job/cell
  lifecycle, kernel start/stop/reset, dropped-output warnings,
  uncaught exceptions. `NB_MCP_LOG_LEVEL`, `NB_MCP_LOG_PATH` override.
- Progress emitter: throttled `job X cell [N] out: <last line>` log
  lines every `NB_MCP_PROGRESS_INTERVAL_SEC` (default 10s) so a
  chatty cell produces a steady event stream.
- Heartbeat: matching `still running (Ns elapsed)` lines for silent
  cells (time.sleep, GPU compute, I/O wait). Fires only when both
  the progress emitter and the kernel itself have been quiet for a
  full interval — chatty cells never see it.

**Monitor integration**

- New CLI `nb watch --job <id> [--path <nb>]`. Tails the log,
  filters to events for that job, line-buffers, exits on
  `job X complete|crashed`. One formatted line per event — built
  for Monitor consumption.
- Non-scratch exec tools now take `block_for` (default 10s). If the
  job completes inside that grace window, response is the full
  status inline. Otherwise response is a Monitor-ready command —
  agent runs `Monitor(command='uv run nb watch --job ... --path ...')`
  in parallel and gets pinged on every event.
- New `status()` MCP tool (no args) + `nb status` CLI: overview of
  every kernel + every active/recent job, for when you don't know
  which notebook you care about.

**Hardening**

- `exec_cell` timeout was passing straight into
  `client.get_iopub_msg(timeout=...)` — that's the gap *between*
  messages, not wall-clock. A chatty training loop kept resetting
  it, so `timeout=600` ran forever. The timeout is now a hard
  deadline from cell start; we poll iopub in 1s slices and
  interrupt the kernel when the deadline fires (so the busy kernel
  doesn't block subsequent cells).
- iopub ZMQ framing desyncs (heavy output → one bad frame →
  `ValueError("'<IDS|MSG>' is not in list")` every subsequent read)
  used to crash the job thread. Now we catch the exception, call
  `kernels.reset_client(path)` to rebuild ZMQ channels against the
  same `KernelManager`, and re-subscribe to the original `msg_id`.
  Up to 3 recoveries per cell, then a clean `[nb mcp] iopub desync`
  marker instead of a zombie thread.
- Cell-id backfill: `_flush_outputs_to_disk` is keyed on cell id
  and used to return silently when id was missing. For notebooks
  written outside the MCP the Queued marker (by index) would
  appear and every streaming flush (by id) would drop — live
  kernel, invisible output. `exec_cell_to_disk` now assigns an id
  before the first flush, and a missing-id fallback now logs a
  WARNING instead of silent no-op.

## Shape

```
Claude Code ──stdio──> nb mcp (FastMCP)
                        ├─ nb_io          pure nbformat
                        ├─ exec_runner    execute_code, streaming flush,
                        │                 wall-clock deadline, iopub recovery
                        ├─ jobs           threads, progress + heartbeat,
                        │                 global status
                        ├─ kernels        {path → (km, client)},
                        │                 reset_client() for recovery
                        └─ _log           .nb_mcp.log

           └──shell──>  uv run nb watch  (tail + filter + exit-on-done)
                        └── Monitor(command=...)
```

## Tests

50 passing. New coverage: wall-clock timeout, iopub recovery (three
scenarios), id-less cells, Monitor-hint + block_for paths, progress
and heartbeat log lines, `nb watch` happy path + startup timeout,
global `status`.
