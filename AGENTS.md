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
Claude Code  ──stdio──>  nb MCP server (mcp MCPServer, 1.x FastMCP via _mcp_compat)
                          ├─ nb_io         (pure nbformat read/write)
                          ├─ exec_runner   (streaming code execution)
                          ├─ jobs          (one background job per notebook)
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
- `run_scratch(notebook_path, code, timeout=120)` — ephemeral, output kept in memory
- `insert_and_exec(notebook_path, index, source, timeout=120)`
- `exec_status(notebook_path)` — snapshot of the active or most recent job
  (for a finished scratch run, includes its output)
- `status()` — **global** snapshot: every kernel + every active/recent job (no path arg)

Every exec tool (scratch included) is a background job. The tool waits a
fixed grace period — `NB_MCP_BLOCK_FOR_SEC`, default 5s — and if the job
finishes in time returns its status inline. Otherwise it hands back
immediately with two ways to follow along: poll `exec_status`, or run the
ready-made Monitor command, e.g.
`Monitor(command='uv run nb watch --job abc123 --path nb.ipynb')`.
The grace is deliberately **not** a tool argument: given the knob, agents
asked for minutes, which held the tool slot, tripped Claude Code's 120s
auto-background, and invited the user-cancel that dropped the connection
(journal 17). `timeout` is the wall-clock limit per cell, not a wait.

One job per notebook at a time — a second exec or scratch on the same
notebook is refused until the first finishes (two readers on one kernel's
iopub channel would steal each other's output).

**Kernel lifecycle**
- `interrupt(notebook_path)`
- `shutdown_kernel(notebook_path)`

The server also auto-creates empty `.ipynb` files on first touch, so `insert_cell(path_to_new_nb, 0, "…")` just works.

---

## 4. CLI

Minimal. Primary interface is MCP.

```
nb mcp                              # run the stdio MCP server
nb cleanup                          # kill stray ipykernel processes and delete .nb/
nb status                           # summarise recent jobs from the log + live
                                    # ipykernel processes. For the running MCP's
                                    # in-memory state, use the `status` MCP tool.
nb watch --job <id> [--path <nb>]   # tail .nb_mcp.log for one job, exit when it ends
                                    # (complete / halted / crashed). One line per event —
                                    # designed for Monitor.
```

## 4a. Logging

Server writes to `./.nb_mcp.log` (CWD) at INFO level by default. Covers
job/cell lifecycle, kernel start/stop, dropped-output warnings, and
unhandled exceptions. Override via `NB_MCP_LOG_LEVEL`
(DEBUG/INFO/WARNING/ERROR) or `NB_MCP_LOG_PATH`.

While a cell is running, two kinds of progress line land in the log —
both matching the `nb watch` → Monitor filter:

- `job X cell [N] out: …` — the last line of fresh stream output,
  throttled to one per `NB_MCP_PROGRESS_INTERVAL_SEC` (default 10s).
- `job X cell [N] still running (Ns elapsed)` — heartbeat that fires
  only if the cell has produced no output AND the progress emitter
  hasn't logged within the interval. Keeps Monitor pinging the agent
  during silent work (`time.sleep`, GPU compute, blocking I/O) so a
  healthy-but-quiet cell doesn't look hung.

Set `NB_MCP_PROGRESS_INTERVAL_SEC=0` to disable both.

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
