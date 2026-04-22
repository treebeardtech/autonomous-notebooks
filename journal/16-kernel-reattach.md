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

Reattach is really a special case of "the kernel did something
unexpected and the agent is guessing." Another one we've noticed:
disk-quota exhaustion (or any write-path failure) mid-exec can look
silent.

Places to audit:
- `atomic_write_nb` raises OSError — propagates to `_run_job`'s
  catch-all `except Exception`, which currently records
  `cp.error_summary = str(exc)` on running/queued cells but doesn't
  log loudly. Need an explicit `log.error("disk write failed…")`
  path so it shows up in `nb_mcp.log` and flows through Monitor.
- The log file's own FileHandler fails open if the FS fills up —
  subsequent warnings/errors then vanish. Consider a fallback to
  stderr when the file handler's emit fails.
- The kernel process itself can die from OOM / disk / SIGKILL.
  `km.is_alive()` starts returning False; our next exec raises, but
  the operator should hear about it as a clear error, not a generic
  exception traceback.

Worth a small hardening pass: every exception in `_run_job` and the
file handlers should log at ERROR with enough context to identify
*what* failed (cell idx, notebook path, operation, errno).
