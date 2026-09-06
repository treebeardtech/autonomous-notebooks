# 17 — Async by default, and why the server kept dying

## TL;DR

Exec tools now hand back after a fixed ~5s grace instead of an
agent-chosen `block_for`. Scratch runs go through the same job slot.
The "MCP server crashed, restart Claude Code" episodes were not crashes:
Claude Code dropped the connection after the user cancelled a long
blocking exec, because mcp 1.x answers cancelled requests. Fixed by
moving to mcp 2.x (never answers them) with a shim for 1.x consumers.

## What the transcripts said

One host, 76 Claude Code transcripts, 2204 `nb` tool calls.

- The agent set `block_for` itself and chose 120–400s in every long
  case; the p90 of `exec_range` was 53s and 37 calls hit Claude Code's
  120s ceiling ("moved to the background as task …").
- The Monitor path (`nb watch`) was used **once**. Agents don't reach
  for it when they can block instead.
- Three mid-session connection drops. Each has the same shape in
  `~/.cache/claude-cli-nodejs/*/mcp-logs-nb/`:

  ```
  Tool 'exec_all' failed after 79s: MCP error -32001: AbortError: user-cancel
  Connection error: Received a response for an unknown message ID:
      {"jsonrpc":"2.0","id":315,"error":{"code":0,"message":"Request cancelled"}}
  STDIO connection dropped after 481406s uptime
  Tool 'list_cells' failed after 1s: MCP error -32000: Connection closed
  ```

  The user pressed Escape on a blocking exec. Claude Code sent
  `notifications/cancelled` and forgot the id. mcp 1.x's
  `RequestResponder.cancel()` then *replied* to the cancelled request
  with `error code 0 "Request cancelled"`. Claude Code treats a reply
  for an unknown id as a broken transport, drops it, and on the next
  tool call tears the server down — `.nb_mcp.log` shows `kernel stopped`
  for every notebook at that second. All in-memory state gone; the user
  ran `/mcp` or restarted.

  The teardown is the server dying, not Claude Code killing it. Our
  handler is parked on a *shielded* worker-thread join, so the cancel
  doesn't interrupt it; it returns normally when the join ends, and
  `_handle_request` calls `respond()`, which trips
  `assert not self._completed` ("Request already responded to") because
  `cancel()` already marked it done. AssertionError in the task group →
  process exits → atexit stops every kernel. Reproduced on the old code
  with the probe below: error reply at the cancel, exit code 1 with that
  assertion when the join ended. The field timeline matches: job
  complete 13:24:10, seven `kernel stopped` lines 13:24:13, next tool
  call `Connection closed`.

- Ten "Connection failed" entries were a different, boring thing: `uv
  run nb mcp` with `nb` not installed in that project's venv
  (`Failed to spawn: nb`).

Reproduced with a raw JSON-RPC probe against the old server: cancel a
blocking `insert_and_exec`, receive `{"id":2,"error":{"code":0,...}}`,
then watch the process die once the blocking window ends.

## What changed

- `block_for` is gone from every exec tool. Grace is server-side:
  `NB_MCP_BLOCK_FOR_SEC` (default 5). Past it the response says the job
  is still running and how to follow it (`exec_status` or the Monitor
  line).
- `run_scratch` is a job too. Output is held on the `Job` and returned
  by `exec_status` when it outlives the grace. Side effect: scratch and
  cell execs now share the one-job-per-notebook slot, so they can no
  longer read the same iopub channel concurrently and drop each other's
  messages.
- mcp `>=1.27`, resolved to 2.x here (`FastMCP` → `MCPServer`, sync
  tools now run on worker threads). `_mcp_compat.py` handles 1.x —
  needed because `claude-agent-sdk` still pins `mcp<2` — and patches
  `cancel()` to only cancel the scope: no reply, and no `_completed`
  flag for `respond()` to trip over when the handler returns.
- `nb watch` exits on `halted` too. Previously an errored cell left the
  Monitor tailing forever.
- Kernel subprocesses no longer inherit the server's stdout (the RPC
  channel); they get our stderr.
- Deps re-locked: ipykernel 7.3, jupyter-client 8.10, nbformat 5.11,
  pyright 1.1.411, pytest 9.1, ruff 0.16.

## Tests

57 passing on mcp 2.1; the same suite runs green on mcp 1.27 through
the shim. New: grace/async paths, scratch-as-job (async, error,
conflict), `nb watch` on halted, and a stdio integration test that
cancels a blocking request and asserts no response ever arrives.
