# autonomous-notebooks

> **Pre-release** — this project is under active development. APIs may change without notice.

CLI for agents to read, modify, and execute Jupyter notebooks headless — no Jupyter server, no browser, no VS Code required. Produces standard `.ipynb` files that open natively in VS Code.

There is an experimental **sandboxed execution** mode for autonomous agents, currently supporting `podman-hpc`. Support for other container runtimes is planned.

## Why

Agents are very effective at research, analysis, and prototyping — but their interface with notebooks is challenging and suboptimal in VS Code. With the right integration and sandboxing, it should be possible to delegate research tasks to agents.

## Getting started

### Install

Add to your project as a dev dependency:

```bash
uv add --dev git+ssh://git@github.com/treebeardtech/autonomous-notebooks
```

### Create and run a notebook

```bash
nb open analysis.ipynb                      # creates notebook, starts kernel
nb insert 0 "import math\nprint(math.pi)"  # add a cell (\n for newlines)
nb exec 0                                   # execute it, outputs saved to .ipynb
```

The kernel uses your current Python — no kernelspec registration needed. For multiline code, pipe via stdin:

```bash
cat <<'EOF' | nb insert 1 -- -
for i in range(5):
    result = i ** 2
    print(f"{i}^2 = {result}")
EOF
nb exec 1
```

Add markdown cells with `--md`:

```bash
nb insert 0 "# My Analysis" --md
```

### Work with cells

```bash
nb cells              # list all cells (compact)
nb cell 2             # read one cell with full source + outputs
nb cell --id abc123   # read cell by stable ID
nb edit 2 "new code"  # overwrite cell source
nb rm 3               # delete a cell
nb run "2 + 2"        # scratch execution (not saved to notebook)
```

### Collaborate with VS Code

Share the kernel so both you and the agent see the same variables:

```bash
nb serve              # installs proxy kernelspec
```

In VS Code: select kernel **"Python (nb: your-project-name)"** from the kernel picker. You may need to **Ctrl+Shift+P > Reload Window** first.

Now both sides share state — the agent runs `nb run "X = 42"`, you run `print(X)` in VS Code and get `42`.

```bash
nb unserve            # remove kernelspec (kernel keeps running)
```

### Clean up

```bash
nb shutdown           # stops kernel, removes proxy kernelspec, clears state
```

## How it works

```
Agent  -->  nb CLI  -->  ipykernel (subprocess)
                    -->  nbformat (.ipynb on disk)

Human  -->  VS Code  -->  proxy.py (ZMQ bridge, launched via kernelspec)
                          both connect to the same kernel
```

- ipykernel is launched directly as a subprocess (no Jupyter server)
- `nbformat` reads/writes standard `.ipynb` files
- Outputs are captured from the kernel and saved into cells
- The proxy re-signs HMAC between VS Code's key and the kernel's key
- Open the `.ipynb` in VS Code at any time to see results
- State lives in `.nb/` in the working directory

## Reference

```bash
nb open <path>              # start kernel, set active notebook (creates if missing)
nb open <path> --kernel-name R  # use a specific kernelspec
nb open <path> --sandboxed  # run kernel in container (requires podman-hpc)
nb cells                    # list all cells
nb cell <index|--id ID>     # read one cell with outputs
nb insert <i> <source>      # insert cell (--md for markdown, - for stdin)
nb edit <i|--id ID> <source>  # overwrite cell source (--md, -)
nb exec <i|--id ID>         # execute cell, capture output
nb run <code>               # execute scratch code (- for stdin)
nb rm <i|--id ID>           # delete a cell
nb save                     # save notebook to disk
nb status                   # show kernel status, active notebook
nb serve                    # share kernel with VS Code
nb unserve                  # stop sharing (kernel keeps running)
nb shutdown                 # stop kernel, clean up
```

## Troubleshooting

**VS Code doesn't show the kernel** — Run **Ctrl+Shift+P > Reload Window** after `nb serve`.

**VS Code "Restart Kernel" doesn't clear state** — By design. VS Code restarts the *proxy*, not the kernel. To truly restart, run `nb open <path>` then "Restart Kernel" in VS Code.

**Proxy crashes silently** — Check `.nb/proxy.log`.

**Kernel not responding** — Run `nb status`. If stopped, `nb open <path>` starts a fresh one.

## Contributing

### Setup

Clone the repo and install dependencies:

```bash
git clone https://github.com/treebeardtech/autonomous-notebooks
cd autonomous-notebooks
just sync
```

Lint, typecheck, and run tests:

```bash
just lint
```

### Local testing in another repo

To test your local changes in another project, add `autonomous-notebooks` as an editable dependency:

```bash
cd /path/to/your-project
uv add --dev --editable /path/to/autonomous-notebooks
```

Now `uv run nb` in your project uses your local CLI code and picks up changes immediately.
