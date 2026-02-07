# 10 — GPU in Sandboxed Kernels

## Goal

Fix `torch.cuda.is_available() == False` inside sandboxed (containerised) kernels despite `nvidia-smi` working.

## TL;DR

`podman-hpc --gpu` mounts host NVIDIA driver libs at `/usr/lib64/` (SUSE/Cray convention), but Ubuntu-based container images don't have `/usr/lib64` in their `ld.so.conf`. PyTorch couldn't find `libcuda.so`, so CUDA appeared unavailable. Fix: set `LD_LIBRARY_PATH=/usr/lib64` in the container when GPU is enabled.

## Root cause

- `nvidia-smi` works because it's statically linked / has rpath — doesn't need dynamic linker to find `libcuda.so`
- `torch.cuda.init()` loads `libcuda.so` via the dynamic linker, which only searches paths in `ld.so.conf`
- Ubuntu's `ld.so.conf` includes `/lib/aarch64-linux-gnu`, `/usr/local/cuda/...` etc — NOT `/usr/lib64`
- Host driver libs (mounted by `podman-hpc --gpu`) sit at `/usr/lib64/libcuda.so.565.57.01` — invisible to the linker

## Changes

- **`podman_hpc/kernel.py`**: Added `-e LD_LIBRARY_PATH=/usr/lib64` to container env when `gpu=True`
- **`podman_hpc/Containerfile`**: Updated base image from `python:3.12-slim` to `nvidia/cuda:12.9.1-cudnn-devel-ubuntu24.04`, added `apt-get install python3 python3-pip`
- **`nbs/gpu.ipynb`**: Fixed `total_mem` → `total_memory`, `torch_dtype` → `dtype`, fixed `apply_chat_template` to use `return_dict=True` + `**inputs` for `generate()`

## Verified

Full end-to-end via `nb sandbox all` with `--mount /scratch/...`:

1. `nvidia-smi` — 4x GH200 120GB
2. `LD_LIBRARY_PATH=/usr/lib64` set
3. `torch.cuda.is_available() = True`, 102 GB memory
4. Llama 3.2 1B loaded on `cuda:0`
5. Inference: "The capital of France is Paris."
6. `nb sandbox run` ad-hoc commands work
