# 08 — Shared-Node Security Review

## Goal

Assess risk of running `nb` on an HPC node where other users share the same localhost.

## TL;DR

Any co-tenant can connect to your kernel and execute code as your user. The attack is trivial: find the ports via `ps aux`, connect with ZMQ, no auth required.

## What we found

**TCP on shared localhost** — The kernel binds 5 ZMQ ports on `127.0.0.1`. On a shared node, every user can reach these. The proxy adds 5 more (9000-9004).

**Empty HMAC keys** — `.nb/kernel.json` has `"key": ""`. The proxy's `make_signer()` returns empty bytes when the key is empty, so messages are unsigned. Even with keys, connection files end up world-readable.

**IPC dirs are 0o777** — The sandboxed path uses IPC sockets instead of TCP (good), then creates the socket directory with `chmod(0o777)` (bad). `kernel.py:174` and `cli2.py:407`.

**Info leaks** — `.nb/proxy.log` is `0o644` and contains connection file paths. Runtime connection files in `/run/user/{uid}/jupyter/runtime/` are also world-readable. Process listing shows the connection file path.

## Fixes, ranked by impact

1. **IPC by default, not TCP** — eliminates the network-reachable surface entirely
2. **chmod 0o700 on IPC dirs** — trivial one-liner, blocks socket access from other users
3. **Non-empty HMAC keys** — verify `write_connection_file()` generates real keys
4. **chmod 0o600 on proxy.log and runtime connection files** — stop leaking paths and keys
5. **atexit cleanup of stale IPC dirs** — defence in depth

## Key takeaway

This is not a theoretical risk. The attack requires zero privilege escalation — just `ps aux`, `cat`, and a ZMQ client. Not safe on shared nodes without fixes.
