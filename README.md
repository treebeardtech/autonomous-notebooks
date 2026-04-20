# autonomous-notebooks

> **Pre-release** — APIs may change without notice.

A stdio MCP server that lets agents read, edit, and execute Jupyter notebooks headlessly. No Jupyter server, no browser, no VS Code required. Produces standard `.ipynb` files that open natively in VS Code.

## Why

Agents are effective at research, analysis, and prototyping — but their interface with notebooks has been awkward. This package exposes notebook operations as MCP tools so Claude Code (or any MCP client) can drive notebooks without shell-escaping multi-line source, without managing kernel lifecycles, and without a persistent daemon.

## How it works

- **Read/edit** are kernel-free — pure `nbformat` operations against the given `.ipynb` path.
- **Exec** auto-starts an in-process `ipykernel` subprocess the first time you execute in a notebook. The kernel is keyed by the notebook's absolute path — one kernel per notebook, many notebooks concurrently.
- Kernels **live for the lifetime of the MCP server**, which Claude Code owns. When Claude Code exits, every kernel dies with it.

## Install and register

Add to your project as a dev dependency:

```bash
uv add --dev git+ssh://git@github.com/treebeardtech/autonomous-notebooks
```

Then tell Claude Code about the server. Either add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "nb": { "command": "uv", "args": ["run", "nb", "mcp"] }
  }
}
```

…or register globally:

```bash
claude mcp add nb -- uv run nb mcp
```

Open a Claude Code session in the project and the `nb` tools appear in the MCP tool list.

## Hello, notebook

Ask Claude:

> Use the `nb` MCP server to create `hello.ipynb` and run a cell that prints the first ten Fibonacci numbers.

Then open `hello.ipynb` in VS Code. The cell is already run, outputs already captured.

## Tools

All tools take `notebook_path` (absolute or relative, created if missing).

| Tool | Purpose |
| --- | --- |
| `list_cells` | Compact one-line-per-cell summary. |
| `read_cell(index?, cell_id?)` | Full source + outputs for one cell. |
| `insert_cell(index, source, markdown=False)` | Insert at index; shifts existing cells down. |
| `set_cell(source, index?, cell_id?, markdown=False)` | Overwrite a cell's source. |
| `delete_cell(index?, cell_id?)` | Delete a cell. |
| `clear_outputs(index?)` | Clear outputs from one cell or all code cells. |
| `exec_cell(index?, cell_id?)` | Execute a cell; outputs written back to the file. |
| `exec_range(start, end)` | Execute cells `[start, end)`; stops on first error. |
| `exec_all` | Execute every code cell in order; stops on first error. |
| `run_scratch(code)` | Execute arbitrary code without writing it to the notebook. |
| `insert_and_exec(index, source)` | Insert a code cell and execute it in one step. |
| `interrupt` | Send SIGINT to the notebook's kernel. |
| `shutdown_kernel` | Stop the notebook's kernel. Next exec starts a fresh one. |

## Admin CLI

Only two subcommands. Everything else is MCP tools.

```bash
nb mcp       # run the stdio MCP server (Claude Code invokes this)
nb cleanup   # kill stray ipykernel processes and remove leftover .nb/
```

## Contributing

```bash
git clone https://github.com/treebeardtech/autonomous-notebooks
cd autonomous-notebooks
just sync
just lint   # ruff + pyright + pytest
```

To test local changes in another project:

```bash
cd /path/to/your-project
uv add --dev --editable /path/to/autonomous-notebooks
```
