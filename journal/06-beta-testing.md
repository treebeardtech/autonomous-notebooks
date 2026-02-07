# 06 — Beta Testing in an External ML Project

## Goal

Use `nb` CLI for real work in an external ML project. Validate that the tool works end-to-end outside its own repo.

## What we did

- **`--kernel-name` flag** on `nb open` — lets user pick any registered kernelspec (default: `python3`). Stored in `state.json`.
- **CLI logging** — `FileHandler` to `.nb/cli.log` at INFO level. Logs open/exec/shutdown.
- **Enhanced `nb status`** — shows kernelspec, state dir path, connection file path.
- **Editable install** — added `autonomous-notebooks` as editable dev dep in the ML project via `uv add --dev --editable`. This means `uv run nb` from the target project uses the project's Python (with torch, transformers, etc.) and picks up CLI code changes immediately.
- **Sandbox mounts** — updated `start_container()` to mount `$HOME` and workspace at their real paths (not `/workspace`), and auto-detect `.venv` python by walking up from workspace.
- **Containerfile.dev** — heavier dev container with ML deps (for future use).

## Key findings

- **Editable install is the simplest approach** for using `nb` in another project. No kernelspec registration needed — `uv run nb` just works with the project's venv.
- **Sandbox + ML deps is friction-heavy**: `--network=none` blocks HuggingFace cache checks, the HF cache directory isn't mounted, and `uv run` inside container fails if workspace is read-only. The sandboxed path needs more config surface.
- **Proxy kernelspec works** for VS Code collaboration — but user must pick "Python (nb shared kernel)", not the project's own kernel.

## Next steps

1. **Repo-specific sandbox config** — user should be able to configure start scripts and mount paths (e.g. `/scratch`) per-repo, probably via a `.nb.toml` or similar.
2. **Configurable image/Dockerfile** — the container image or Dockerfile should be specifiable in the user's repo, not hardcoded to our Containerfile.
3. **Separate command categories** — split commands into `read` (cells, cell, status), `edit` (insert, edit, rm), `exec` (exec, run), and `sandbox_exec` — making sandboxing an execution concern rather than a kernel lifecycle concern.
