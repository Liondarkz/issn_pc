#!/usr/bin/env bash
# Builds dist/issn-windows/ (and dist/issn-windows.zip): inFAMOUS Second Son.exe (the launcher, frozen
# with PyInstaller so players need no Python), issn-probe.exe with the MSYS2 CLANG64 DLLs it needs,
# the preparation scripts and run.py. Run from an MSYS2 CLANG64 shell after `bash build.sh`.
# Freezing uses a Windows Python 3.10+ (python.org; WINPYTHON overrides) and a private venv in
# out/pyenv with PyInstaller and Pillow (the game icon and the Steam pictures need it). The
# temporal upscalers are off, so neither the FSR 4 assets nor the
# DLSS runtime are packaged.
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."
source ./msys2-env.sh
[[ -f out/issn-probe.exe && -f out/issn-gpu-capabilities.exe && -f out/issn-play.exe ]] || { echo 'Build first: bash build.sh' >&2; exit 1; }

# A Windows Python (not MSYS2's) for PyInstaller.
python=${WINPYTHON:-}
if [[ -z $python ]]; then
    for candidate in /c/Python3*/python.exe "${LOCALAPPDATA:-/c/Users/$USER/AppData/Local}"/Programs/Python/Python3*/python.exe; do
        [[ -x $candidate ]] && python=$candidate
    done
fi
[[ -n $python ]] || { echo 'Need a Windows Python 3 (python.org) or WINPYTHON=path\to\python.exe' >&2; exit 1; }
# Windows Python needs USERPROFILE (some MSYS2 shells start without it).
export USERPROFILE=${USERPROFILE:-$(cygpath -w "/c/Users/$(id -un)")}
if [[ ! -x out/pyenv/Scripts/python.exe ]]; then
    "$python" -m venv out/pyenv
fi
out/pyenv/Scripts/python.exe -m pip install -q --disable-pip-version-check pyinstaller pillow iced-x86==1.21.0
# The scripts run inside inFAMOUS Second Son.exe (--script): the standard modules they import come along.
hidden=()
for module in argparse base64 collections hashlib json re shutil struct tempfile xml.etree.ElementTree \
              urllib.request ctypes.wintypes iced_x86 PIL.Image PIL.ImageDraw PIL.ImageFont; do
    hidden+=(--hidden-import "$module")
done
out/pyenv/Scripts/python.exe -m PyInstaller --noconfirm --clean --log-level WARN --windowed --copy-metadata pillow \
    --name "inFAMOUS Second Son" --icon "$(cygpath -w "$PWD/launcher/issn.ico")" --distpath out/pyi-dist \
    --workpath out/pyi-work --specpath out/pyi-work --paths "$(cygpath -w "$PWD/scripts")" "${hidden[@]}" \
    "$(cygpath -w "$PWD/launcher/issn_launcher_win.py")"

# The package is assembled in a fresh staging folder and zipped from there; dist/issn-windows
# (a playable copy that may hold saves and settings) is only refreshed afterwards.
dest=out/stage/issn-windows
rm -rf -- out/stage
mkdir -p "$dest/bin" "$dest/launcher"
cp -r "out/pyi-dist/inFAMOUS Second Son/." "$dest/"
llvm-strip -o "$dest/Play inFAMOUS Second Son.exe" out/issn-play.exe
cp launcher/issn.ico launcher/issn.png "$dest/launcher/"
# issn: the title face ships only when there is one (launcher/issn_font.ttf; none yet).
[[ -f launcher/issn_font.ttf ]] && cp launcher/issn_font.ttf "$dest/launcher/"
# The executables without debug information (out/ keeps the symbols for crash reports).
for exe in issn-probe.exe issn-gpu-capabilities.exe; do
    llvm-strip --strip-debug -o "$dest/bin/$exe" "out/$exe"
done
# Every DLL the executables load from the CLANG64 tree (SDL3, FFmpeg, Vulkan loader, ...).
ldd "$dest/bin/issn-probe.exe" "$dest/bin/issn-gpu-capabilities.exe" |
    awk '/\/clang64\/bin\// {print $3}' | sort -u | while read -r dll; do
        cp -u "$dll" "$dest/bin/"
    done
cp -r scripts patches "$dest/"
cp run.py LICENSE README.md packaging/windows/README-Windows.txt "$dest/"
find "$dest" -name __pycache__ -prune -exec rm -r {} +
# No game files and nothing a player made (saves, shader caches built from the game's shaders,
# settings, logs) may ship: stop when one is found.
forbidden=$(find "$dest" \( -iname eboot.bin -o -iname sce_module -o -iname sce_sys -o -iname savedata \
    -o -iname user -o -iname cache -o -iname '*.log' -o -iname issn.ini -o -iname settings.json \
    -o -iname mods.json -o -iname patches.json -o -iname fsr4_shaders -o -iname 'nvngx_dlss*.dll' \
    -o -iname '*.spv' -o -iname '*.pkg' -o -iname '*.sprx' -o -iname '*.prx' \) -print)
if [[ -n $forbidden ]]; then
    echo "Package holds files that must not ship:" >&2
    echo "$forbidden" >&2
    exit 1
fi
# FSR 4 Native AA (MIT, AMD's model; third_party/fsr4/README.md): shipped after the check above,
# which keeps any other .spv out.
cp -r third_party/fsr4 "$dest/fsr4_shaders"
# The title face of the launcher (Bebas Neue, SIL Open Font License).
cp launcher/OFL-BebasNeue.txt "$dest/launcher/"
# Shader seed (ISSN_SHADER_SEED_FROM=<cache folder verified with SHADPS4_CACHE_REBUILD=2>,
# tools/make_shader_seed.py): pipeline keys and shader metas, no shader code; a player's first start
# makes the shaders from their own game files. Shipped as user/cache/<name>, which run.py uses as the
# cache folder (shader-seed.txt). The stamp holds the hash of the executable it was verified with,
# so the verify run must have used this package's issn-probe.exe.
if [[ -n ${ISSN_SHADER_SEED_FROM:-} ]]; then
    version=$(sed -n "s/^VERSION = '\(.*\)'.*/\1/p" launcher/issn_launcher_win.py)
    seed_name=${ISSN_SHADER_SEED_NAME:-issn-seed-$version}
    verified_exe=$(dirname -- "$ISSN_SHADER_SEED_FROM")/../../bin/issn-probe.exe
    # sha256sum (coreutils): MSYS2 has no cmp unless diffutils is installed.
    if [[ ! -f $verified_exe ]] ||
        [[ $(sha256sum < "$verified_exe") != $(sha256sum < "$dest/bin/issn-probe.exe") ]]; then
        echo "The seed's folder was not verified with this package's bin/issn-probe.exe ($verified_exe)." >&2
        echo 'Run the verify from dist/issn-windows after packaging, without building again.' >&2
        exit 1
    fi
    "$python" -I "$(cygpath -w "$PWD/tools/make_shader_seed.py")" "$(cygpath -w "$ISSN_SHADER_SEED_FROM")" \
        "$(cygpath -w "$PWD/$dest/user/cache/$seed_name")"
    extra=$(find "$dest/user" -type f ! -name '*.key' ! -name '*.meta' ! -name seed-stamp.bin \
        ! -name vs-formats.bin ! -name seed-graphics-keys.txt -print)
    [[ -z $extra ]] || { echo "The seed holds files that must not ship:" >&2; echo "$extra" >&2; exit 1; }
    printf '%s\n' "$seed_name" > "$dest/shader-seed.txt"
fi
mkdir -p dist
rm -f dist/issn-windows.zip
(cd out/stage && powershell -NoProfile -Command \
    "Compress-Archive -Path issn-windows -DestinationPath ../../dist/issn-windows.zip")

# Refresh dist/issn-windows, keeping what players create there (saves, settings, mods), and
# only while nothing runs from it: deleting a running launcher's files breaks it.
play=dist/issn-windows
running=$(powershell -NoProfile -Command \
    "@(Get-Process | Where-Object { \$_.Path -like '$(cygpath -w "$PWD/$play")\\*' }).Count" | tr -d '\r')
if [[ ${running:-0} != 0 ]]; then
    echo "dist/issn-windows is in use ($running processes): not refreshed; the zip is ready." >&2
else
    mkdir -p "$play"
    find "$play" -mindepth 1 -maxdepth 1 ! -name user ! -name mods ! -name issn.ini \
        ! -name mods.json ! -name patches.json -exec rm -rf {} +
    # Everything but user/: the shader seed goes in only where that cache folder does not exist
    # yet. Copied over a folder that was played with, the seed's metas would replace the folder's
    # own under the same names while its SPIR-V stays: mismatched shaders (2026-10-24, a seed made
    # from such a folder crashed the game).
    find "$dest" -mindepth 1 -maxdepth 1 ! -name user -exec cp -r {} "$play/" \;
    if [[ -n ${seed_name:-} && ! -e $play/user/cache/$seed_name ]]; then
        mkdir -p "$play/user/cache"
        cp -r "$dest/user/cache/$seed_name" "$play/user/cache/"
    fi
fi
du -sh "$dest" dist/issn-windows.zip
