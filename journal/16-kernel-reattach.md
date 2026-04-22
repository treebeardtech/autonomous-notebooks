# 16 — Kernel reattach across MCP restarts

## The question

If Claude Code is killed, or the MCP subprocess crashes, can the next
MCP session reattach to the kernels the previous one was using —
rather than leaving a GPU-holding training job orphaned and
starting fresh?

## Why it matters

Long-running training / eval jobs are the motivating case. Right now:

- Graceful exit (SIGTERM / SIGINT / atexit) runs `kernels.shutdown_all`
  — every kernel dies.
- Hard crash (SIGKILL, segfault) leaves orphan ipykernel processes
  with PPID 1. The kernel is still alive and still writing eval files,
  but the next MCP starts fresh and knows nothing about it. Agent
  can't execute anything on that kernel, and can't clean it up
  without `nb cleanup` killing the in-progress work.

Users want the training run to survive a Claude Code restart.

## What reattach requires

Jupyter exposes a **connection file** per kernel — a JSON blob in
`~/.local/share/jupyter/runtime/kernel-*.json` with the ZMQ ports
and session key. Given that path, `KernelManager(connection_file=...)`
+ `load_connection_file()` rebuilds a manager against the running
process, no new kernel spawned.

So the pieces we need:

1. **Persist** `{resolved_path → {connection_file, pid, started_at}}`
   somewhere stable (e.g. `.nb/kernels.json`) on each kernel start.
2. **Don't kill on exit** when persistence is enabled. Today's
   atexit + SIGTERM + SIGINT handlers all call `shutdown_all` — in
   persist mode they need to detach (close client channels, leave
   kernel alive) instead.
3. **Reattach on startup**: before starting a new kernel for a path,
   check the state file; if the pid is still alive and the
   connection file is still present, reconnect via
   `KernelManager.load_connection_file`. If either check fails,
   drop the stale entry and start fresh.
4. Clean up `jobs._active` / `_finished` expectations — those are
   per-session only; a reattached kernel has no job history.

## Decisions to make

- **Default behavior**: keep today's "kill on exit" default and make
  persist opt-in (env var `NB_MCP_PERSIST_KERNELS=1` or
  `nb mcp --persist`)? Or flip the default? Opt-in is safer — avoids
  surprise GPU leaks for users running quick experiments.
- **Cleanup path**: `nb cleanup` already kills stray ipykernels and
  removes `.nb/`. That's the escape hatch. Good.
- **Staleness window**: if the persisted entry is older than N hours,
  should we refuse to reattach even if the pid is still alive?
  Probably not — if the process is alive, the kernel is usable.
- **Heartbeat on startup**: after `load_connection_file`, should we
  probe the kernel with a silent `execute("1", silent=True)` to
  confirm responsiveness before handing the client to the caller?
  Likely yes — a live pid doesn't guarantee a responsive kernel
  (e.g. stuck in a C extension).

## Sketch

```python
# kernels.py
def get_or_start(path):
    # 1. in-memory hit (unchanged)
    # 2. reattach from persisted state
    entry = _load_persisted().get(_key(path))
    if entry and _pid_alive(entry["pid"]) and Path(entry["connection_file"]).exists():
        try:
            km = KernelManager(connection_file=entry["connection_file"])
            km.load_connection_file()
            client = km.client()
            client.start_channels()
            client.wait_for_ready(timeout=5)
            _probe(client)  # optional silent execute to verify
            _kernels[k] = (km, client)
            log.info("reattached to kernel for %s (pid %d)", k, entry["pid"])
            return client
        except Exception:
            log.exception("reattach failed — starting fresh")
            _drop_persisted(k)
    # 3. start new (unchanged); write persisted if enabled

def _ensure_shutdown_hooks():
    if _persist_enabled():
        atexit.register(_detach_all)  # stop_channels only, kernels live on
    else:
        atexit.register(shutdown_all)  # current behavior
```

## Open

Not yet implemented — parked pending a decision on opt-in vs.
default-on. My lean is opt-in with a simple env var, no CLI flag at
first.

## Adjacent: surface silent execution failures

**Done.** The user's ask was narrower than the original note suggested:
as long as something lands in `.nb_mcp.log` the agent can see it via
Monitor. On audit most paths were already covered — the one genuinely
misleading case was a dying kernel being reported as a generic
"iopub desync".

What ships:

- New `KernelDeadError` raised by `kernels.reset_client` when the
  underlying `KernelManager.is_alive()` is False or `wait_for_ready`
  times out after a channel rebuild.
- `exec_runner.execute_code` catches it distinctly. Emits `log.error
  "kernel died during cell execution: …"` plus an **nbformat error
  output** on the cell (`ename=NbMcpKernelDied`) so the job is marked
  ERROR and subsequent cells are skipped — previously the job
  optimistically continued against a dead kernel.
- The cell's inline marker now says *"kernel died mid-execution (OOM
  / killed / crashed). Use `status()` to check…"* instead of
  "iopub desync".

What the agent sees over Monitor when a training job's kernel gets
OOM-killed:

```
14:02:31  job abc123 cell [2] out: step 847 loss=0.31
14:02:42  job abc123 cell [2] out: step 903 loss=0.29
14:02:53  kernel for /.../train.ipynb is dead — cannot reset client
14:02:53  kernel died during cell execution: kernel for … has died
14:02:53  job abc123 cell [2] errored after 512.3s: NbMcpKernelDied: …
14:02:53  job abc123 halted — 1 cells skipped
```

What we did **not** do (notes for next time):

- Log-handler-fallback-to-stderr when the file handler's emit fails
  (e.g. disk full mid-run). Rare, still a hole.
- Explicit per-operation disk-write logging — the `log.exception` in
  `_run_job`'s catch-all already captures errno + traceback for the
  common cases (disk full, permissions, bad filesystem). Sufficient
  for now.
