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

Commands are grouped into permission tiers for agent access control:

```
# Top-level
nb guide                    # print agent reference

# Sys tier — lifecycle
nb sys open <path>              # set active notebook (starts kernel)
nb sys open <path> --sandboxed  # sandboxed kernel (GPU on by default)
nb sys open --sandboxed --no-gpu --podman-arg='--shm-size=8g'
nb sys shutdown                 # stop kernel
nb sys serve / nb sys unserve   # share/unshare kernel with VS Code
nb sys restart                  # restart kernel with same settings

# Read tier — no side effects, auto-allowed
nb read cells               # list all cells (compact)
nb read cell <index>        # read one cell with outputs
nb read peek <path>         # read-only cell listing from any notebook
nb read status              # show kernel status, active notebook

# Edit tier — modifies notebook on disk
nb edit insert <i> <src>    # insert a cell
nb edit set <i> <src>       # overwrite cell source
nb edit rm <i>              # delete a cell
nb edit save                # save notebook to disk
nb edit clear [<i>]         # clear outputs (all or one cell)

# Exec tier — runs code on kernel
nb exec cell <i>            # execute a cell, capture output
nb exec cell <start>:<end>  # execute range of cells
nb exec all                 # execute all code cells (stop on error)
nb exec run <code>          # execute scratch code (not saved)

# Sandbox tier — exec, but errors if kernel not sandboxed
nb sandbox cell <i>         # execute cell (sandboxed only)
nb sandbox all              # execute all cells (sandboxed only)
nb sandbox run <code>       # execute scratch code (sandboxed only)
```

The CLI manages kernel lifecycle automatically:
- `nb sys open` starts a kernel if one isn't running.
- Kernel connection info is persisted so subsequent commands reuse it.
- `nb sys shutdown` stops the kernel and cleans up.

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

- `nb read cells` returns compact summaries (first line of source, truncated).
- `nb read cell <i>` returns full source + outputs for one cell.
- Outputs can be large — truncate or omit when not needed.
- Prefer targeted reads over dumping the whole notebook.

---

## 7. Practical Workflow

1. Agent runs `nb sys open path/to/notebook.ipynb`.
2. Agent reads cells, inserts/edits code, executes cells.
3. Outputs are captured and saved to the .ipynb.
4. Human opens the .ipynb in VS Code later to review.
5. Kernel can be left running or shut down.

For unattended operation: run the agent in tmux/screen. The kernel and notebook persist independently of any terminal.

---

## Interactive E2E Testing

No automated integration tests for the full CLI+proxy flow — test interactively with the human:

1. `nb sys open nbs/test.ipynb` — start kernel
2. `nb edit insert <i> "print('hello')"` then `nb exec cell <i>` — verify output capture
3. `nb sys serve` — install proxy kernelspec
4. Human: in VS Code, reload window, select "Python (nb shared kernel)"
5. Agent: `nb exec run "X = 42"` — set a variable
6. Human: run `print(X)` in VS Code — verify shared state
7. `nb sys shutdown` — clean up

The human needs to be present to verify the VS Code side. Check `.nb/proxy.log` if the proxy fails silently.

---

## Agent Coding Guidelines

- Avoid redundant info in docstrings, keep them short and to the point, prefer inline comments close to usage to reduce risk of inconsistency after human edits
- Use typehints although not when it requires a load of stubbing things, ask if you are unsure, and you can use Any - be pragmatic
- We use `uv` for everything so `uv run` and `uv add`
- lint and test your code with `just lint`
- we have a project journal in `./journal/` - journal entries should be readable in 1min and skimmable in 10s
