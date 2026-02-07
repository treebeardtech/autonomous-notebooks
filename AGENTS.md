# AGENTS.md
### Autonomous Notebook Agents

Tools for agents to read, modify, and execute Jupyter notebooks — headless, no Jupyter server required. CLI first, MCP later.

---

## 1. Goals

- Allow an agent to **read, modify, and execute** cells in a Jupyter notebook.
- Work **headless** — no VS Code, no Jupyter server, no browser.
- Produce **standard .ipynb files** that VS Code opens natively.
- Keep execution **sandboxed** via a containerised kernel (later).
- Keep agent context usage manageable.
- **Later:** collaborative mode where human and agent share a live kernel via Jupyter server.

---

## 2. Architecture

### Current (Phase 1): CLI + direct kernel

```
Agent  ──>  nb CLI  ──>  jupyter_client (kernel)
                    ──>  nbformat (.ipynb read/write)
```

- `nb` CLI provides all notebook operations as subcommands.
- `jupyter_client` starts and manages kernels directly (no server).
- `nbformat` reads/writes standard .ipynb files.
- Outputs are captured from the kernel and saved into notebook cells.
- VS Code can open the resulting .ipynb at any time — zero install.

### Later (Phase 2): MCP wrapper

```
Agent  ──>  MCP Server (stdio)  ──>  nb CLI / library
```

- Thin MCP server wrapping the same operations as tools.
- Enables use from Claude Desktop, Claude Code, or any MCP client.

### Later (Phase 3): Collaborative mode

```
VS Code  ─────┐
              │
Jupyter Server  ←──  MCP Server / CLI  ←── Agent
              │
         ipykernel
```

- Shared Jupyter server for live collaboration.
- Both human and agent see the same kernel state.
- Requires Jupyter server to be running; optional enhancement.

---

## 3. What the CLI Can Do

```
nb open <path>              # set active notebook (starts kernel)
nb cells                    # list all cells (compact)
nb cell <index>             # read one cell with outputs
nb insert <index> <source>  # insert a cell
nb edit <index> <source>    # overwrite cell source
nb exec <index>             # execute a cell, capture output
nb run <code>               # execute scratch code (not saved to notebook)
nb rm <index>               # delete a cell
nb save                     # save notebook to disk
nb status                   # show kernel status, active notebook
nb shutdown                 # stop kernel
```

The CLI manages kernel lifecycle automatically:
- `open` starts a kernel if one isn't running.
- Kernel connection info is persisted so subsequent commands reuse it.
- `shutdown` stops the kernel and cleans up.

---

## 4. Kernel Management

Kernels are managed via `jupyter_client` directly:

- **No Jupyter server needed** — kernels are started as subprocesses.
- Connection files are stored in a known location (e.g. `.nb/`).
- Kernel spec can be the default `python3` or a custom containerised one.
- Kernel persists across CLI invocations (not per-command).

### Containerised kernel (later)

Custom kernelspec launching ipykernel inside Podman:

```jsonc
{
  "argv": ["podman", "run", "...", "python", "-m", "ipykernel_launcher", "-f", "{connection_file}"],
  "display_name": "Python (Sandboxed)",
  "language": "python"
}
```

---

## 5. Output Capture

When executing a cell:

1. Send cell source to kernel via `jupyter_client`.
2. Collect all `execute_result`, `stream`, `display_data`, `error` messages.
3. Convert to nbformat output objects.
4. Attach outputs to the cell in the notebook data structure.
5. Auto-save (or explicit `nb save`).

This means the .ipynb file always reflects the latest execution state — open it in VS Code and everything is there.

---

## 6. Keeping Context Usage Reasonable

- `nb cells` returns compact summaries (first line of source, truncated).
- `nb cell <i>` returns full source + outputs for one cell.
- Outputs can be large — truncate or omit when not needed.
- Prefer targeted reads over dumping the whole notebook.

---

## 7. Practical Workflow

1. Agent runs `nb open path/to/notebook.ipynb`.
2. Agent reads cells, inserts/edits code, executes cells.
3. Outputs are captured and saved to the .ipynb.
4. Human opens the .ipynb in VS Code later to review.
5. Kernel can be left running or shut down.

For unattended operation: run the agent in tmux/screen. The kernel and notebook persist independently of any terminal.

---

## Interactive E2E Testing

No automated integration tests for the full CLI+proxy flow — test interactively with the human:

1. `nb open nbs/test.ipynb` — start kernel
2. `nb insert <i> "print('hello')"` then `nb exec <i>` — verify output capture
3. `nb serve` — install proxy kernelspec
4. Human: in VS Code, reload window, select "Python (nb shared kernel)"
5. Agent: `nb run "X = 42"` — set a variable
6. Human: run `print(X)` in VS Code — verify shared state
7. `nb shutdown` — clean up

The human needs to be present to verify the VS Code side. Check `.nb/proxy.log` if the proxy fails silently.

---

## Agent Coding Guidelines

- Avoid redundant info in docstrings, keep them short and to the point, prefer inline comments close to usage to reduce risk of inconsistency after human edits
- Use typehints although not when it requires a load of stubbing things, ask if you are unsure, and you can use Any - be pragmatic
- We use `uv` for everything so `uv run` and `uv add`
- lint and test your code with `just lint`
- we have a project journal in `./journal/` - journal entries should be readable in 1min and skimmable in 10s
