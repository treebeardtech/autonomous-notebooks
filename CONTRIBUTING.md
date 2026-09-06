# Contributing

## Components

Seven moving parts. Claude Code owns the server process; the server owns the kernels.

```
┌──────────────┐   stdio JSON-RPC   ┌──────────────────────────┐
│ Claude Code  │ ◀────────────────▶ │ nb mcp server            │
│ (MCP client) │                    │ server.py · tool fns     │
└──────┬───────┘                    └────┬───────────────┬─────┘
       │ Monitor: `nb watch`             │ exec_*        │ list / read / edit
       │ tails .nb_mcp.log        ┌──────▼──────┐        │
       │                          │ jobs        │        │
       └──── log lines ◀───────── │ 1 thread/nb │        │
                                  └──────┬──────┘        │
                                         │ run cell      │
                                  ┌──────▼──────┐   ┌────▼──────┐
                                  │ exec_runner │──▶│ nb_io     │
                                  │ iopub→outs  │   │ nbformat  │
                                  └──────┬──────┘   └────┬──────┘
                                         │ execute        │ atomic write
                                  ┌──────▼──────┐   ┌────▼──────┐
                                  │ kernels     │   │ *.ipynb   │
                                  │ ipykernel/nb│   │ on disk   │
                                  └─────────────┘   └───────────┘
```

- **nb mcp server** (`server.py`) — stdio entry point. Every tool is a plain
  function, so tests call them directly without a transport.
- **jobs** — one background thread per notebook. Owns job state, the grace
  period, and the `.nb_mcp.log` lines that `nb watch` streams into Claude
  Code's Monitor.
- **exec_runner** — sends code to a kernel, turns iopub messages into
  nbformat outputs, flushes them to the file as they arrive.
- **kernels** — in-process pool of ipykernel subprocesses keyed by notebook
  path. Dies with the server.
- **nb_io** — pure nbformat read/write. No kernel, no state.

## Dev loop

```bash
git clone https://github.com/treebeardtech/autonomous-notebooks
cd autonomous-notebooks
just sync
just lint   # ruff + pyright + pytest
```

To try local changes from another project:

```bash
cd /path/to/your-project
uv add --dev --editable /path/to/autonomous-notebooks
```

Design history lives in `journal/` (one short entry per round).
`AGENTS.md` is the reference for agents working in this repo.
