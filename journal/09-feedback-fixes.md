# 09 — Feedback Fixes

## Goal

Address the issues captured in `feedback.md` after the first real agent workflow session.

## TL;DR

Four commits covering docs, sandbox mounts, buildah compat, and permission-tiered CLI. The biggest change: `nb` commands are now grouped into `read`/`edit`/`exec`/`sandbox` tiers so Claude Code permissions can distinguish read-only from side-effecting operations.

## What feedback.md raised

| # | Issue | Status |
|---|-------|--------|
| 1.1 | Agent needed more docs | Fixed — `nb guide` added (724c3a7) |
| 1.2 | Generic kernel names | Fixed earlier (journal 07) |
| 2.1 | Scratch dir not mounted | Fixed — `--mount` flag added (1477b30) |
| 2.2 | Permissions not granular enough | Fixed — tiered CLI structure (this session) |
| 3.1 | Can't read other notebooks | Fixed — `nb read peek` reads any .ipynb without touching the active kernel (724c3a7) |
| 4.x | Shell escaping issues | Mitigated — `nb guide` documents stdin patterns; `cmd_guide` updated |
| 6.1 | `nb insert 0` semantics confusing | Documented — guide clarifies "inserts *at* index i" |

Items 1.3 (auto-shutdown), 3.2 (variable inspection desync), and 5.1 (save interruption) are Claude Code / workflow issues outside the CLI's scope.

## Changes by commit

**724c3a7** — better docs and `nb peek`
- Added `nb guide` with shell patterns, argument order, cell addressing docs
- Added `nb peek <path>` for read-only access to any notebook
- `.gitignore` updates

**1477b30** — extra mounts
- `--mount` flag on `nb open --sandboxed` for additional bind mounts (repeatable)
- Passed through to `podman-hpc run`

**0c508ce** — buildah fix
- Switched container build from `podman-hpc build` to `buildah` to work around a tagging issue

**7263856** — permission-tiered CLI
- Flat subcommands → nested tiers: `nb read`, `nb edit`, `nb exec`, `nb sandbox`
- `nb edit` (the old "overwrite cell" command) renamed to `nb edit set` to avoid collision with the tier name
- `nb sandbox {cell,run}` reuses exec handlers but guards on `state["sandboxed"]`
- `.claude/settings.json` auto-allows only `nb read *` and `nb guide`; everything else requires confirmation
- `CLAUDE.md` and `cmd_guide` updated to reflect new structure

**this session** — CLI round 2 (sys tier, ranges, GPU)
- Lifecycle commands (`open`, `shutdown`, `serve`, `unserve`) moved under `nb sys` tier — enables `Bash(nb sys *)` permission pattern
- New `nb sys restart` — kills and restarts kernel with same settings from state.json
- New `nb exec all` / `nb sandbox all` — run all code cells, stop on first error
- Cell range support: `nb exec cell 0:5`, `nb exec cell 3:` (Python-style slicing)
- New `nb edit clear [i]` — clear outputs from all cells or one cell
- Sandbox GPU passthrough: `--gpu` on by default for `nb sys open --sandboxed`, disable with `--no-gpu`
- `--podman-arg` flag for arbitrary podman-hpc flags (repeatable)
- Sandbox config (gpu, mounts, extra_args) persisted in state.json for `restart`
- `cmd_guide` now includes example `.claude/settings.json` for permissions setup
- `.claude/settings.json` updated with `nb sys *` patterns
