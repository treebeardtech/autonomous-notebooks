# 03 — Containerised Kernels via podman-hpc

**Date:** 2026-02-07
**Status:** Working, integrated into CLI

## TL;DR

ipykernel runs inside a podman-hpc container with `--network=none` and repo-scoped filesystem. Host CLI connects via ZMQ IPC sockets through a bind-mounted tmpdir. `nb open path --sandboxed` just works.

## Key Findings

**IPC transport is the critical insight.** With `--network=none`, TCP ports are unreachable from the host. But ZMQ supports IPC (unix domain sockets), and these work perfectly through a bind-mounted directory.

**ipykernel ignores connection file transport settings.** Passing `-f kernel.json` alone doesn't work — the kernel overwrites it with TCP defaults. You must also pass `--transport=ipc --ip=/ipc/kernel` as CLI args.

**The kernel deletes the connection file** after binding its sockets. The CLI saves a copy to `.nb/kernel.json` before starting the container so subsequent commands can reconnect.

**podman-hpc build needs `TMPDIR=/tmp`.** Default buildah tmpdir hits permission errors on shared HPC filesystems.

**`--home` flag mounts real home — don't use it.** Without `--home`, `/home` is empty inside the container (good for isolation).

## Architecture

```
Host CLI  ──zmq/ipc──>  /tmp/nb-ipc-XXXX/kernel-{1..5}  ──bind mount──>  /ipc/kernel-{1..5}  ──>  ipykernel
                                                                          (inside container)
```

- Connection file written to tmpdir with `transport=ipc`, `ip=/ipc/kernel`
- Copy saved to `.nb/kernel.json` (kernel deletes the original)
- Container started with `-v tmpdir:/ipc -v workspace:/workspace:ro --network=none`
- 5 unix sockets appear: `kernel-1` through `kernel-5` (shell, iopub, stdin, control, hb)
- Host client loads connection file, overrides `ip` to host-side path, connects normally

## CLI Integration

```
nb open nbs/test.ipynb --sandboxed    # starts containerised kernel
nb status                              # shows "running (sandboxed)"
nb exec 0                             # executes via container kernel
nb run 'print("hi")'                  # scratch execution in sandbox
nb shutdown                            # stops container + cleans up
```

Workspace = notebook's parent directory, mounted read-only at `/workspace`. Container name derived from workspace dir name. State persisted in `.nb/state.json` with `sandboxed`, `ipc_dir`, and `container_name` fields. Container liveness checked via `podman-hpc inspect`.

## Isolation Properties Verified

| Property | Method | Result |
|---|---|---|
| No network | `--network=none` | `OSError: Network is unreachable` |
| Read-only workspace | `-v path:/workspace:ro` | `OSError: Read-only file system` |
| No home dir | omit `--home` flag | `/home` is empty |
| Kernel state persists | sequential executions | variables survive across calls |
| Workspace files readable | read from `/workspace` | csv/py files accessible |

## Files

- `src/autonomous_notebooks/podman_hpc/Containerfile` — minimal image (python:3.12-slim + pyzmq + ipykernel)
- `src/autonomous_notebooks/podman_hpc/kernel.py` — `ContainerKernel` class + lower-level functions
- `src/autonomous_notebooks/podman_hpc/test_kernel.py` — 8 integration tests
- `src/autonomous_notebooks/cli2.py` — `--sandboxed` flag on `open` command

## Next

- Consider rw workspace mode for when agent needs to write output files
- Pin image versions / add to justfile for rebuild
- Custom images with more packages (numpy, pandas, etc.)
