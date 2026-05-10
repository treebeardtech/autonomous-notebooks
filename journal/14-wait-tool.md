# 14 — Wait tool for long-running jobs

## TL;DR

New `wait(notebook_path, timeout=30)` MCP tool. Blocks until the active exec job finishes or the timeout hits, then returns status — including "last output Xs ago" for the running cell so the agent can tell a live job from a hung one.

## Why

With async exec (journal 13), the agent could kick off a job and move on, but had no clean way to *wait* for it. In practice Claude Code would schedule self-wake-ups at arbitrary intervals, which often didn't fire for training-style cells that take minutes. The agent ended up either polling `exec_status` in a loop or blindly sleeping — both wasteful, and neither surfaced whether the cell was actually making progress.

## What changed

**`wait` tool** — async, uses `anyio.to_thread.run_sync` to `thread.join(timeout)` the job worker. Returns as soon as the job ends, or on timeout. No active job → returns the most recent job's status (same shape as `exec_status`).

**`last_output_at` on `CellProgress`** — bumped every time the kernel emits a new message for the running cell. `exec_cell_to_disk` now accepts an `on_output` callback that `jobs._run_job` wires through to update this timestamp.

**`idle` property** — `time.monotonic() - last_output_at` while running. `get_status` renders it as ` (last output 3.2s ago)` or ` (no output yet)` next to the running cell.

## How the agent uses it

```
> exec_all(training.ipynb)
executing 4 cells (job ab12cd34)

> wait(training.ipynb, timeout=30)
job ab12cd34: running (4 cells)
  [0] done 0.4s
  [1] done 1.1s
  [2] running 28.5s (last output 2.1s ago)
  [3] queued
```

Low idle time → job is alive, call `wait` again with a longer timeout (exponentially back off). Idle time climbing across successive calls → probably hung, consider `interrupt`.

## Tests

37 passing. Added three: wait-returns-on-finish, wait-times-out-while-running, wait-on-fresh-notebook.
