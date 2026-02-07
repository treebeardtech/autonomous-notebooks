# 11 — Beta Feedback Round 2

## TL;DR

Raw observations from an agent workflow session, organized by theme. Mix of UX friction, missing features, and architectural questions.

## CLI UX

- **Heredoc permission friction** — `cat <<'EOF' | uv run nb edit insert --md 13 -` triggers a permission prompt every time. MCP would bypass this since no shell escaping needed.
- **Insert-then-exec is two steps** — common pattern is insert a cell then immediately run it; should be a single command (`nb exec insert`? `nb edit insert --exec`?).
- **Multiline edits broken** — shell quoting makes multi-line cell source painful. VS Code's `NotebookEdit` tool works fine for this use case.
- **`open` is misleading** — semantics are "start a kernel for this notebook", closer to `up` or `start-kernel` than opening a file.
- **`status` belongs in `sys` not `read`** — it's about kernel lifecycle, not notebook content.
- **`guide` should merge into main `--help`** — separate subcommand is easy to miss.

## Sandbox

- **Brittle mount paths** — sandboxing depends on getting system-specific mounts right; fragile across environments.
- **Scratch dir mounted read-only** — needs to be writable for real workloads.
- **Correct mounts not guaranteed** — need validation or sensible defaults for common paths.
- **Help should document constraints** — no internet access, models must be pre-cached, avoid non-permitted commands in autonomous mode.

## Kernel management

- **No interrupt command** — long-running cells can't be interrupted; need `nb exec interrupt` or similar.
- **Who shuts down the kernel?** — no lifecycle management; orphaned kernels accumulate. Consider a nuclear cleanup command.
- **Moving notebook causes crash** — `nb read status` crashes if the .ipynb has been relocated.
- **Should work without `open`** — read and edit operations don't need a running kernel.
- **Interrupting `open`/`shutdown`** — unclear what state is left in if these are killed mid-way.

## Execution

- **Foreground blocking** — long cells block the agent. Options: background execution with polling, or prompt the agent to use short timeouts + sleep.
- **No exec logging** — would help to have timestamps, "exec started" / "exec ended" markers from the kernel.
- **Cell magic struggles** — agent can't use `%` magics easily, falls back to subprocess calls.
- **Shell commands in sandbox** — guide should tell agent to use `get_ipython().system("cmd")` for bash commands inside sandboxed cells instead of `!cmd` syntax which has escaping issues.

## Architecture questions

- **Should we use skills?** — Claude Code skills might be a better abstraction than raw CLI for agent-facing interface.
- **Insert+exec as single command** — reduces round-trips and permission prompts.
- **MCP vs CLI** — MCP avoids shell escaping entirely; should we prioritize the MCP wrapper?
