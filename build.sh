#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
mkdir -p out
# Windows: MSYS2 CLANG64 (packages in packaging/windows/README.md). The GPU library is static
# there and CMake links issn-probe.exe as well. LTO on by default (ISSN_LTO=OFF to build without);
# PGO: ISSN_PGO=generate builds the instrumented executable for a training run, and the default
# uses pgo-windows/issn-windows.profdata when it is there (ISSN_PGO=off to build without).
case $(uname -s) in
MINGW*|MSYS*)
    # shellcheck source=msys2-env.sh
    source ./msys2-env.sh
    for tool in clang cmake ninja pkg-config; do
        command -v "$tool" >/dev/null || { echo "Need $tool from MSYS2 CLANG64 (packaging/windows/README.md)." >&2; exit 1; }
    done
    if [[ ! -f gpu/third_party/fsr-vulkan/CMakeLists.txt || ! -f gpu/third_party/imgui/imgui.h ||
          ! -f gpu/third_party/xbyak/xbyak/xbyak.h ]]; then
        git submodule update --init --recursive
    fi
    # A patch that is neither applied nor applicable: the tree holds an older version of the
    # patches (the GOW3_ -> ISSN_ rename changed their text). Start again from the pinned commit.
    for patch in gpu/patches/fsr-vulkan/*.patch; do
        if ! git -C gpu/third_party/fsr-vulkan apply --reverse --check "$PWD/$patch" 2>/dev/null &&
           ! git -C gpu/third_party/fsr-vulkan apply --check "$PWD/$patch" 2>/dev/null; then
            git -C gpu/third_party/fsr-vulkan checkout -- .
            break
        fi
    done
    for patch in gpu/patches/fsr-vulkan/*.patch; do
        if ! git -C gpu/third_party/fsr-vulkan apply --reverse --check "$PWD/$patch" 2>/dev/null; then
            git -C gpu/third_party/fsr-vulkan apply "$PWD/$patch"
        fi
    done
    pgo=${ISSN_PGO:-}
    if [[ -z $pgo ]]; then
        if [[ -f pgo-windows/issn-windows.profdata ]]; then pgo=use; else pgo=off; fi
    fi
    cmake -S gpu -B out/gpu -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo -DISSN_PGO="$pgo" \
        -DISSN_PGO_FILE="$PWD/pgo-windows/issn-windows.profdata" \
        -DISSN_LTO="${ISSN_LTO:-ON}" -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ >/dev/null
    echo "GPU library and loader: LTO ${ISSN_LTO:-ON}, PGO $pgo"
    if ! ninja -C out/gpu issn-probe issn-gpu-capabilities issn-play > out/gpu-build.log 2>&1; then
        grep -v '^\[' out/gpu-build.log | tail -40 >&2
        echo 'Build failed (full log: out/gpu-build.log)' >&2; exit 1
    fi
    cp out/gpu/issn-probe.exe out/gpu/issn-gpu-capabilities.exe out/gpu/issn-play.exe out/
    echo "Built $PWD/out/issn-probe.exe"
    exit 0
    ;;
esac
# Linux defaults; macOS replaces them below.
no_pie=(-no-pie) export_dynamic=-rdynamic origin='$ORIGIN' heap=() cmake_platform=()
# macOS: an x86_64 build that Rosetta 2 runs, Vulkan through MoltenVK. Dependencies come from
# packaging/macos/build-deps-x86_64.sh (ISSN_MAC_DEPS), never from the arm64 Homebrew.
if [[ $(uname -s) == Darwin ]]; then
    deps=${ISSN_MAC_DEPS:-$PWD/out/macos-deps}
    [[ -f $deps/lib/libMoltenVK.dylib ]] || { echo "macOS dependencies missing: run packaging/macos/build-deps-x86_64.sh" >&2; exit 1; }
    export MACOSX_DEPLOYMENT_TARGET=13.0 PATH="$deps/bin:$PATH"
    export PKG_CONFIG_LIBDIR="$deps/lib/pkgconfig:$deps/share/pkgconfig"
    printf '#!/bin/sh\nexec clang -arch x86_64 -mmacosx-version-min=13.0 "$@"\n' > out/cc-x86_64
    chmod +x out/cc-x86_64
    CC=$PWD/out/cc-x86_64
    # The guest packs pointers into 40 bits: mimalloc, patched to map in 32-64 GiB, replaces
    # the system malloc (near 128 TiB). It must come first on the link line.
    no_pie=() export_dynamic=-Wl,-export_dynamic,-rpath,"$deps/lib" origin=@loader_path
    heap=("$deps/lib/mimalloc-3.5/mimalloc.o" src/compat_mac.c)
    # shadPS4 points the Vulkan loader at the executable's directory (VK_DRIVER_FILES).
    printf '{"file_format_version":"1.0.0","ICD":{"library_path":"%s","api_version":"1.4.0","is_portability_driver":true}}\n' \
        "$deps/lib/libMoltenVK.dylib" > out/MoltenVK_icd.json
    boost=$(ls -d /opt/homebrew/lib/cmake/Boost-* 2>/dev/null | tail -1)
    cmake_platform=(-DCMAKE_OSX_ARCHITECTURES=x86_64 -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0
        -DCMAKE_PREFIX_PATH="$deps" -DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew
        -DBoost_DIR="$boost" -DCMAKE_POLICY_VERSION_MINIMUM=3.5)
    ISSN_PGO=${ISSN_PGO:-off} ISSN_LTO=${ISSN_LTO:-OFF}
fi
if [[ -z ${CC:-} ]]; then
    CC=$(command -v cc || command -v gcc || true)
    if [[ -z $CC ]]; then
        for candidate in /nix/store/*-gcc-wrapper-*/bin/gcc; do
            if [[ -x $candidate ]]; then CC=$candidate; break; fi
        done
    fi
fi
if [[ -z ${CC:-} ]]; then echo 'Install GCC/Clang or set CC.' >&2; exit 1; fi
# Dependencies come from pkg-config (Vulkan loader/headers, SDL3). On NixOS the
# environment is provided by shell.nix; re-enter it automatically if needed.
if ! { command -v pkg-config >/dev/null && pkg-config --exists vulkan sdl3 && command -v cmake >/dev/null && command -v ninja >/dev/null; }; then
    if [[ -z ${ISSN_IN_NIX_SHELL:-} ]] && command -v nix-shell >/dev/null; then
        exec env ISSN_IN_NIX_SHELL=1 nix-shell shell.nix --run "bash build.sh $*"
    fi
    echo 'Need pkg-config with vulkan and sdl3, cmake and ninja (see shell.nix).' >&2; exit 1
fi
read -r -a includes <<< "$(pkg-config --cflags vulkan sdl3 libavcodec libavutil libswscale)"
read -r -a libraries <<< "$(pkg-config --libs vulkan sdl3 libavcodec libavutil libswscale)"
# GPU library (shadPS4 video core + drivers), built by CMake into out/gpu/libissngpu.so.
# ISSN_PGO: generate (instrumented build that writes pgo/ while the game runs), use, off.
# Default: use the profile in pgo/ when there is one. ISSN_LTO=OFF disables link-time optimization.
pgo=${ISSN_PGO:-}
if [[ -z $pgo ]]; then
    if [[ -n $(find pgo -name '*.gcda' -print -quit 2>/dev/null) ]]; then pgo=use; else pgo=off; fi
fi
mkdir -p pgo
# Submodules (git clone --recursive, or: git submodule update --init) and this port's changes
# to FSR-Vulkan (gpu/patches/fsr-vulkan), applied to its working tree once.
if [[ ! -f gpu/third_party/fsr-vulkan/CMakeLists.txt || ! -f gpu/third_party/imgui/imgui.h ]]; then
    git submodule update --init --recursive
fi
for patch in gpu/patches/fsr-vulkan/*.patch; do
    if ! git -C gpu/third_party/fsr-vulkan apply --reverse --check "$PWD/$patch" 2>/dev/null &&
       ! git -C gpu/third_party/fsr-vulkan apply --check "$PWD/$patch" 2>/dev/null; then
        git -C gpu/third_party/fsr-vulkan checkout -- .  # an older version of the patches
        break
    fi
done
for patch in gpu/patches/fsr-vulkan/*.patch; do
    if ! git -C gpu/third_party/fsr-vulkan apply --reverse --check "$PWD/$patch" 2>/dev/null; then
        git -C gpu/third_party/fsr-vulkan apply "$PWD/$patch"
    fi
done
cmake -S gpu -B out/gpu -G Ninja -DCMAKE_BUILD_TYPE=RelWithDebInfo -DISSN_PGO="$pgo" \
    -DISSN_LTO="${ISSN_LTO:-ON}" -DISSN_PGO_DIR="$PWD/pgo" ${cmake_platform[@]+"${cmake_platform[@]}"} >/dev/null
echo "GPU library: PGO $pgo, LTO ${ISSN_LTO:-ON}"
# A failed GPU build must stop here: an older libissngpu.so would otherwise be used silently.
if ! ninja -C out/gpu issngpu > out/gpu-build.log 2>&1; then
    grep -v '^\[' out/gpu-build.log | tail -40 >&2
    echo 'GPU library build failed (full log: out/gpu-build.log)' >&2; exit 1
fi
# $ORIGIN/gpu: packaged copies keep the library next to the binary without patching it.
gpu=(-Lout/gpu -lissngpu -Wl,-rpath,"$origin/gpu" -Wl,-rpath,"$PWD/out/gpu" "$export_dynamic")
runtime=(src/runtime*.c)
# Third-party decoders: compiled once, without this project's -Werror policy.
atrac9=(third_party/LibAtrac9/C/src/*.c)
if [[ ! -f out/libatrac9.a || -n $(find third_party/LibAtrac9/C/src -newer out/libatrac9.a -name '*.c') ]]; then
    rm -rf out/atrac9 && mkdir -p out/atrac9
    for source in "${atrac9[@]}"; do "$CC" -std=c99 -O2 -g -w -c "$source" -o "out/atrac9/$(basename "${source%.c}").o"; done
    ar rcs out/libatrac9.a out/atrac9/*.o
fi
# isa_emulate runs inside guest code: no XMM/YMM.
"$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -mgeneral-regs-only -c src/isa_emulate.c -o out/isa_emulate.o
"$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread ${no_pie[@]+"${no_pie[@]}"} "${includes[@]}" -I. -Isrc ${heap[@]+"${heap[@]}"} src/probe.c "${runtime[@]}" src/cpu_check.c src/vulkan_smoke.c out/isa_emulate.o src/isa_rewrite.c out/libatrac9.a -lm "${gpu[@]}" "${libraries[@]}" -o out/issn-probe
echo "Built $PWD/out/issn-probe"
# GPU check for run.sh (live_resolution=auto): links only the Vulkan loader.
"$CC" -std=c11 -O2 -Wall -Wextra -Werror "${includes[@]}" -Igpu/shadps4 tools/gpu_capabilities.c "${libraries[@]}" -o out/issn-gpu-capabilities
if [[ ${1:-} == --test ]]; then
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread "${includes[@]}" -I. -Isrc tests/test_pad.c "${libraries[@]}" -lm -o out/pad-test
    out/pad-test
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -I. -Isrc tests/test_eventflag.c -o out/eventflag-test
    out/eventflag-test
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -I. -Isrc tests/test_runtime.c "${runtime[@]}" out/libatrac9.a -lm "${gpu[@]}" "${libraries[@]}" -o out/runtime-test
    out/runtime-test
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -I. -Isrc tests/test_file_mods.c -o out/file-mods-test
    out/file-mods-test
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -I. -Isrc tests/test_sema.c "${runtime[@]}" out/libatrac9.a -lm "${gpu[@]}" "${libraries[@]}" -o out/sema-test
    out/sema-test
    "$CC" -std=c11 -D_GNU_SOURCE -O2 -g -Wall -Wextra -Werror -I. -Isrc tests/test_content.c src/runtime_content.c -o out/content-test
    out/content-test
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread "${includes[@]}" -I. -Isrc tests/test_mp3.c src/runtime_mp3.c out/libatrac9.a -lm "${libraries[@]}" -o out/mp3-test
    out/mp3-test tests/data/sine.mp3
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread "${includes[@]}" -I. -Isrc tests/test_videodec.c "${libraries[@]}" -o out/videodec-test
    out/videodec-test tests/data/tiny.h264
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -pthread -I. -Isrc tests/test_services.c -o out/services-test
    out/services-test
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -mbmi -mlzcnt -mmovbe -I. -Isrc tests/test_isa_emulate.c out/isa_emulate.o -o out/isa-emulate-test
    out/isa-emulate-test
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -c src/isa_rewrite.c -I. -Isrc -o out/isa_rewrite.o
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -mbmi -mlzcnt -I. -Isrc tests/test_isa_stub.c out/isa_rewrite.o out/isa_emulate.o -o out/isa-stub-test
    out/isa-stub-test tests/data/isa_fixture.bin
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -I. -Isrc tests/test_isa_extrq.c out/isa_rewrite.o out/isa_emulate.o -o out/isa-extrq-test
    out/isa-extrq-test
    "$CC" -std=c11 -O2 -g -Wall -Wextra -Werror -I. -Isrc tests/test_redzone_guard.c -o out/redzone-guard-test
    out/redzone-guard-test
fi
