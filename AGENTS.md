# AGENTS.md
### Autonomous Notebook Agents

A stdio MCP server that lets an agent read, modify, and execute Jupyter notebooks headlessly. Kernels live inside the MCP server process and die with it.

---

## 1. Goals

- Agents can **read, modify, and execute** notebook cells through MCP tools.
- **Headless** — no VS Code, no Jupyter server, no browser.
- Produces **standard .ipynb** files.
- **Stateless read/edit** — just `nbformat` against a path. No state file.
- **Per-notebook kernels**, auto-started on first exec, tied to the MCP server's process lifecycle (i.e. Claude Code's).

---

## 2. Architecture

```
Claude Code  ──stdio──>  nb MCP server (FastMCP)
                          ├─ nb_io         (pure nbformat read/write)
                          ├─ exec_runner   (streaming code execution)
                          └─ kernels       ({notebook_path → ipykernel})
```

- `nb mcp` is the stdio entry point. Claude Code launches it as a subprocess.
- Read/edit tools work against any `.ipynb` path without touching a kernel.
- Exec tools call `kernels.get_or_start(path)` — first call starts a kernel, later calls reuse it.
- When Claude Code exits, stdin closes / SIGTERM fires → `shutdown_all()` stops every kernel.

---

## 3. MCP tools

All tools take `notebook_path: str` as their first argument.

**Read (kernel-free)**
- `list_cells(notebook_path)`
- `read_cell(notebook_path, index?, cell_id?)`

**Edit (kernel-free, writes to disk)**
- `insert_cell(notebook_path, index, source, markdown=False)`
- `set_cell(notebook_path, source, index?, cell_id?, markdown=False)`
- `delete_cell(notebook_path, index?, cell_id?)`
- `clear_outputs(notebook_path, index?)`

**Exec (auto-starts kernel)**
- `exec_cell(notebook_path, index?, cell_id?, timeout=120)`
- `exec_range(notebook_path, start, end, timeout=120)`
- `exec_all(notebook_path, timeout=120)`
- `run_scratch(notebook_path, code, timeout=120)` — ephemeral
- `insert_and_exec(notebook_path, index, source, timeout=120)`
- `exec_status(notebook_path)` — snapshot of the active or most recent job
- `wait(notebook_path, timeout=30)` — block until job finishes or timeout; status includes idle time so the agent can tell progress from hang

**Kernel lifecycle**
- `interrupt(notebook_path)`
- `shutdown_kernel(notebook_path)`

The server also auto-creates empty `.ipynb` files on first touch, so `insert_cell(path_to_new_nb, 0, "…")` just works.

---

## 4. CLI

Minimal. Primary interface is MCP.

```
nb mcp       # run the stdio MCP server
nb cleanup   # kill stray ipykernel processes and delete .nb/
```

## 4a. Logging

Server writes to `./.nb_mcp.log` (CWD) at INFO level by default. Covers job/cell lifecycle, kernel start/stop, dropped-output warnings, and unhandled exceptions. Override via `NB_MCP_LOG_LEVEL` (DEBUG/INFO/WARNING/ERROR) or `NB_MCP_LOG_PATH`.

---

## 5. Output capture

`exec_runner.execute_code` subscribes to the kernel's iopub channel and converts each message to an `nbformat` output dict: `stream`, `display_data`, `execute_result`, `error`. For cell execs, outputs are re-written to the `.ipynb` (by cell ID) after every message so the file on disk mirrors progress in real time.

---

## 6. Interactive E2E check

No full integration tests for the MCP loop. Quickest manual check:

1. `.mcp.json` in the repo root, run Claude Code in-repo.
2. `list_cells /tmp/demo.ipynb` — file is created, prints `(no cells)`.
3. `insert_and_exec /tmp/demo.ipynb 0 "print('hello')"` — outputs captured.
4. Open `/tmp/demo.ipynb` in VS Code — cell + output visible.
5. `run_scratch /tmp/demo.ipynb "2 + 2"` — returns `4`.
6. Exit Claude Code, then `ps -u $USER | grep ipykernel` — empty.

If something's wedged: `nb cleanup`.

---

## Agent Coding Guidelines

- Docstrings short and to the point; prefer inline comments close to usage.
- Use typehints, but `Any` is fine when stubs are more trouble than they're worth.
- We use `uv` for everything — `uv run` and `uv add`.
- Lint and test with `just lint`.
- Project journal lives in `./journal/` — entries should be readable in 1 min, skimmable in 10 s.
