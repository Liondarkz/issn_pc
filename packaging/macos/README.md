# issn on macOS

The port builds on macOS as an **x86_64** program that Rosetta 2 runs on Apple silicon, with
Vulkan through MoltenVK. It is not a GPTK/Wine build: the same sources as on Linux, with a
`__APPLE__` branch where the systems differ. Status: builds, `issn-probe --vulkan-only` and
`--cpu-features` pass on an M4; not yet run with the game.

## Building

The repository path must not contain spaces (FFmpeg's configure does not accept them).

```sh
brew install cmake ninja pkg-config boost   # host tools; Boost is header-only here
bash packaging/macos/build-deps-x86_64.sh   # x86_64 dependencies into out/macos-deps (once)
bash build.sh                               # out/issn-probe, out/gpu/libissngpu.dylib
bash packaging/macos/package.sh             # optional: dist/issn-macos-x86_64.zip for testers
```

`ISSN_MAC_DEPS` points both scripts at another dependency prefix. The arm64 Homebrew is never
linked: only Boost's headers are taken from it.

## What differs from Linux

| Linux | macOS |
|---|---|
| guest TLS: `arch_prctl(ARCH_SET_GS)` | a pthread key, read as `gs:[8*key]`; the `fs:` sites are rewritten as on Windows (`patch_tcb_loads`) |
| host heap below 1 TiB: non-PIE brk heap, `mallopt` | mimalloc 3.5.3 patched to map only in 32-64 GiB (`mimalloc-os-window.patch`), linked first |
| `ucontext` `gregs` | `uc_mcontext->__ss` |
| `process_vm_readv`, `/proc/self/task` | `mach_vm_read_overwrite`, `task_threads` |
| `memfd_create` | `shm_open` + immediate unlink |
| timed mutex/rwlock waits, `CLOCK_MONOTONIC` waits | try + short sleep (`compat_mac.c`); `CLOCK_REALTIME` (as on Windows) |
| `SCHED_IDLE` helpers | `QOS_CLASS_BACKGROUND` |
| X11/Wayland surface | SDL Metal view, `VK_EXT_metal_surface` |
| — | `VK_KHR_portability_enumeration` + `VK_KHR_portability_subset` (MoltenVK) |
| — | Rosetta reports AVX in CPUID only with `ROSETTA_ADVERTISE_AVX=1`: `issn-probe` restarts itself with it |
