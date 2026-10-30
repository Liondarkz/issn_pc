#!/usr/bin/env bash
# Dependências x86_64 (Intel, rodam no M4 via Rosetta 2) para o port issn_pc no macOS.
# Instala tudo em out/macos-deps (ou ISSN_MAC_DEPS) sem tocar no Homebrew ARM (/opt/homebrew).
# Idempotente: cada etapa concluída deixa um carimbo em $P/.stamps e é pulada na próxima vez.
# O caminho do repositório não pode ter espaços (o configure do FFmpeg não aceita).
set -euo pipefail

D="$(cd -- "$(dirname -- "$0")" && pwd)"           # packaging/macos (o patch do mimalloc)
ROOT="$(cd -- "$D/../.." && pwd)"
P="${ISSN_MAC_DEPS:-$ROOT/out/macos-deps}"         # prefixo de instalação (x86_64)
S="$ROOT/out/macos-deps-src"                       # códigos-fonte
mkdir -p "$P/.stamps" "$S"

export MACOSX_DEPLOYMENT_TARGET=13.0
ARCH="-arch x86_64 -mmacosx-version-min=$MACOSX_DEPLOYMENT_TARGET"
JOBS=$(sysctl -n hw.ncpu)

CMAKE_COMMON=(-G Ninja -DCMAKE_BUILD_TYPE=Release
    -DCMAKE_OSX_ARCHITECTURES=x86_64 -DCMAKE_OSX_DEPLOYMENT_TARGET=$MACOSX_DEPLOYMENT_TARGET
    -DCMAKE_INSTALL_PREFIX="$P" -DCMAKE_PREFIX_PATH="$P"
    -DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew   # nunca pegar biblioteca ARM do Homebrew
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5)

step() { echo; echo "=================== $1 ($(date +%H:%M:%S))"; }
done_() { touch "$P/.stamps/$1"; echo "OK: $1"; }
have() { [[ -f "$P/.stamps/$1" ]]; }
fetch() { # fetch <dir> <url> <tag> [--recursive]
    [[ -d "$S/$1/.git" ]] || git clone --depth 1 --branch "$3" ${4:-} "$2" "$S/$1" -q
}
cm() { # cm <name> <srcdir> [cmake args...]
    local name=$1 src=$2; shift 2
    cmake -S "$src" -B "$S/build-$name" "${CMAKE_COMMON[@]}" "$@" > "$S/build-$name.cmake.log"
    cmake --build "$S/build-$name" -j "$JOBS" > "$S/build-$name.build.log"
    cmake --install "$S/build-$name" > "$S/build-$name.install.log"
}

if ! have vulkan-headers; then step "Vulkan-Headers"
    fetch Vulkan-Headers https://github.com/KhronosGroup/Vulkan-Headers.git vulkan-sdk-1.4.363.0
    cm vulkan-headers "$S/Vulkan-Headers"; done_ vulkan-headers; fi

if ! have vulkan-loader; then step "Vulkan-Loader"
    fetch Vulkan-Loader https://github.com/KhronosGroup/Vulkan-Loader.git vulkan-sdk-1.4.363.0
    cm vulkan-loader "$S/Vulkan-Loader" -DVULKAN_HEADERS_INSTALL_DIR="$P" -DBUILD_TESTS=OFF
    done_ vulkan-loader; fi

if ! have moltenvk; then step "MoltenVK v1.4.2 (binário universal oficial da Khronos)"
    mkdir -p "$S/moltenvk" && cd "$S/moltenvk"
    [[ -f MoltenVK-macos.tar ]] || curl -sSL -o MoltenVK-macos.tar \
        https://github.com/KhronosGroup/MoltenVK/releases/download/v1.4.2/MoltenVK-macos.tar
    rm -rf x && mkdir x && tar -xf MoltenVK-macos.tar -C x
    dylib=$(find x -name libMoltenVK.dylib -path '*macOS*' | head -1)
    [[ -n $dylib ]] || dylib=$(find x -name libMoltenVK.dylib | head -1)
    lipo -info "$dylib"
    mkdir -p "$P/lib" "$P/share/vulkan/icd.d"
    cp "$dylib" "$P/lib/libMoltenVK.dylib"
    printf '{\n "file_format_version": "1.0.0",\n "ICD": {\n  "library_path": "../../../lib/libMoltenVK.dylib",\n  "api_version": "1.4.0",\n  "is_portability_driver": true\n }\n}\n' \
        > "$P/share/vulkan/icd.d/MoltenVK_icd.json"
    cd "$D"; done_ moltenvk; fi

if ! have vma; then step "VulkanMemoryAllocator (só cabeçalho)"
    fetch VMA https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator.git v3.5.0
    cm vma "$S/VMA"; done_ vma; fi

if ! have robin-map; then step "tsl-robin-map (só cabeçalho)"
    fetch robin-map https://github.com/Tessil/robin-map.git v1.4.1
    cm robin-map "$S/robin-map"; done_ robin-map; fi

if ! have fmt; then step "fmt 12.2.0"
    fetch fmt https://github.com/fmtlib/fmt.git 12.2.0
    cm fmt "$S/fmt" -DFMT_TEST=OFF -DFMT_DOC=OFF; done_ fmt; fi

if ! have xxhash; then step "xxHash 0.8.4"
    fetch xxHash https://github.com/Cyan4973/xxHash.git v0.8.4
    make -C "$S/xxHash" -j"$JOBS" CC="clang $ARCH" PREFIX="$P" lib > "$S/build-xxhash.log"
    make -C "$S/xxHash" CC="clang $ARCH" PREFIX="$P" install >> "$S/build-xxhash.log"
    done_ xxhash; fi

if ! have zydis; then step "Zydis 4.1.1"
    fetch zydis https://github.com/zyantific/zydis.git v4.1.1 --recursive
    cm zydis "$S/zydis" -DZYDIS_BUILD_TOOLS=OFF -DZYDIS_BUILD_EXAMPLES=OFF -DZYDIS_BUILD_SHARED_LIB=OFF
    done_ zydis; fi

if ! have zycore; then step "Zycore (a dependência do Zydis, instalada à parte para o zydis-config)"
    cm zycore "$S/zydis/dependencies/zycore" -DZYCORE_BUILD_SHARED_LIB=OFF
    done_ zycore; fi

if ! have mimalloc; then step "mimalloc 3.5.3 com janela de endereços abaixo de 1 TiB"
    # O guest do PS4 guarda ponteiros em 40 bits; o malloc do macOS fica perto de 128 TiB.
    # O patch (mimalloc-os-window.patch) põe toda memória do processo em 32–64 GiB.
    fetch mimalloc https://github.com/microsoft/mimalloc.git v3.5.3
    git -C "$S/mimalloc" apply --reverse --check "$D/mimalloc-os-window.patch" 2>/dev/null ||
        git -C "$S/mimalloc" apply "$D/mimalloc-os-window.patch"
    cm mimalloc "$S/mimalloc" -DMI_OVERRIDE=ON -DMI_OSX_ZONE=ON -DMI_OSX_INTERPOSE=ON \
        -DMI_BUILD_SHARED=OFF -DMI_BUILD_STATIC=ON -DMI_BUILD_OBJECT=ON -DMI_BUILD_TESTS=OFF \
        "-DCMAKE_C_FLAGS=-DMI_OS_WINDOW_START=0x800000000ULL -DMI_OS_WINDOW_END=0x1000000000ULL"
    done_ mimalloc; fi

if ! have sdl3; then step "SDL 3.4.18"
    fetch SDL https://github.com/libsdl-org/SDL.git release-3.4.18
    cm sdl3 "$S/SDL" -DSDL_SHARED=ON -DSDL_STATIC=OFF -DSDL_TESTS=OFF -DSDL_EXAMPLES=OFF
    done_ sdl3; fi

if ! have glslang; then step "glslang 16.6.0"
    fetch glslang https://github.com/KhronosGroup/glslang.git 16.6.0
    cm glslang "$S/glslang" -DENABLE_OPT=OFF -DGLSLANG_TESTS=OFF -DBUILD_SHARED_LIBS=OFF \
        -DENABLE_GLSLANG_BINARIES=ON -DGLSLANG_ENABLE_INSTALL=ON
    done_ glslang; fi

if ! have ffmpeg; then step "FFmpeg 9.0.2 (só as bibliotecas que o port usa)"
    fetch FFmpeg https://github.com/FFmpeg/FFmpeg.git n9.0.2
    cd "$S/FFmpeg"
    ./configure --prefix="$P" --arch=x86_64 --target-os=darwin --enable-cross-compile \
        --cc="clang $ARCH" --cxx="clang++ $ARCH" --nm=nm --ar=ar --ranlib=ranlib \
        --enable-shared --disable-static --disable-programs --disable-doc \
        --disable-autodetect --disable-network --disable-avdevice --disable-avfilter \
        > "$S/build-ffmpeg.configure.log"
    make -j"$JOBS" > "$S/build-ffmpeg.build.log" 2>&1
    make install > "$S/build-ffmpeg.install.log"
    cd "$D"; done_ ffmpeg; fi

step "Verificação"
for f in "$P"/lib/*.dylib; do [[ -L $f ]] || printf '%-34s %s\n' "$(basename "$f")" "$(lipo -archs "$f")"; done
echo "pkg-config:"; PKG_CONFIG_LIBDIR="$P/lib/pkgconfig:$P/share/pkgconfig" pkg-config --modversion sdl3 libxxhash libavcodec vulkan 2>&1
echo; echo "TODAS AS DEPENDÊNCIAS PRONTAS ($(date +%H:%M:%S))"
