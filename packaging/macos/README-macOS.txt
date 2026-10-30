issn on macOS (Apple silicon) - TEST BUILD
==========================================

This is an early test build. The port compiles and its checks pass on an M4
(Vulkan through MoltenVK: command submission + readback PASS; CPU: complete
but SSE4A, which the port emulates as on Intel). It has NOT yet been run with
the game: every report helps.

What you need
- A Mac with Apple silicon, macOS 13 or newer, Rosetta 2
  (softwareupdate --install-rosetta).
- Python 3 (python3; macOS offers to install it on first use).
- Your own dump of the game, prepared as described in the main README.
  This package contains no game files.

Running
1. Unzip, open Terminal in the folder, and run:  bash run.sh
   (the same arguments and environment variables as on Linux).
2. macOS may refuse the unsigned binaries the first time: run
   xattr -dr com.apple.quarantine .   in the folder, then try again.

How it works on a Mac
- The build is x86_64 and runs under Rosetta 2: the game's own x86-64 code
  runs as on a PC. issn-probe restarts itself with ROSETTA_ADVERTISE_AVX=1,
  which Rosetta needs before it reports AVX/AVX2/F16C/BMI in CPUID.
- Graphics: Vulkan through MoltenVK (bin/libMoltenVK.dylib) onto Metal.
- Memory: the guest packs pointers into 40 bits, so the whole process heap
  must stay below 1 TiB. A patched mimalloc (packaging/macos) keeps it in
  32-64 GiB instead of macOS's usual ~128 TiB.

Reporting
Please send: the Mac model, the macOS version, what happened, and the whole
terminal output (and out/*.log if there are any).

Building it yourself: see packaging/macos/README.md.
