# 07 — Rounding Off Rough Edges

## Goal

Fix three UX friction points from beta testing: kernelspec requirement, generic proxy name, fragile shell escaping.

## What changed

**Direct kernel launch** — `nb open` no longer requires a `python3` kernelspec. Default path launches `ipykernel` directly via `sys.executable` + `subprocess.Popen`, with `write_connection_file()` generating the connection info. `--kernel-name` is opt-in for when you actually need a specific kernelspec (R, Julia, etc.). This means `uv run nb open` just works in any venv without kernelspec registration.

**Project-scoped proxy name** — `nb serve` now installs as `nb-proxy-{dirname}` with display name `Python (nb: {dirname})`. Working across multiple projects no longer produces ambiguous kernel picker entries. Old spec is cleaned up automatically if the name changes.

**Stdin support** — `nb insert 0 -- -` and `nb edit 0 -- -` read source from stdin. Heredocs work naturally for multiline code without `\\n` escaping. Agent path (passing source as arg with `\\n`) still works unchanged.

**Markdown cells** — `--md` flag on `insert` and `edit` creates/converts to markdown cells.

## Key findings

- `write_connection_file()` from `jupyter_client` handles port allocation and key generation — no need to replicate that logic.
- Stdin mode needs `--` before `-` with argparse (standard Unix convention, not a real issue).
- All 26 existing tests still pass — the kernel management changes are isolated to the startup path.

## Next steps

- Interactive E2E testing of the proxy with the new project-scoped name in VS Code.
- Consider `.nb.toml` for per-project config (sandbox mounts, default kernel, etc.).
