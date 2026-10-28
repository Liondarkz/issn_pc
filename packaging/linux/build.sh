#!/usr/bin/env bash
# Builds dist/issn-x86_64.AppImage from the Linux build (run `bash build.sh` in the repo root first).
# Libraries are bundled with their dependencies, except the ones that must come from the host
# (glibc, Vulkan loader and drivers, X11/Wayland, GL). The Tk launcher is frozen with PyInstaller, so
# players need no Python. Needs: ldd, patchelf, curl (appimagetool), python-pyinstaller, tk, pillow, iced-x86.
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."
[[ -f out/issn-probe && -f out/gpu/libissngpu.so ]] || { echo 'Build first: bash build.sh' >&2; exit 1; }

appdir=out/AppDir
rm -rf "$appdir"
share=$appdir/usr/share/issn
mkdir -p "$share/bin/gpu" "$appdir/usr/lib"

install -m755 out/issn-probe out/issn-gpu-capabilities "$share/bin/"
install -m755 out/gpu/libissngpu.so "$share/bin/gpu/"
# Without debug information (out/ keeps the symbols for crash reports), as the Windows package.
strip --strip-debug "$share/bin/issn-probe" "$share/bin/issn-gpu-capabilities" "$share/bin/gpu/libissngpu.so"
cp run.py LICENSE README.md packaging/linux/README-Linux.txt "$share/"
cp -r scripts patches "$share/"
mkdir -p "$share/launcher"
# The title face of the launcher (Bebas Neue, SIL Open Font License; fontconfig, register_font).
install -m644 launcher/issn_font.ttf launcher/OFL-BebasNeue.txt "$share/launcher/"
# The icon of the AppImage, the launcher and the game window: packaging/linux/issn.png when it
# exists (a local choice), else the launcher's own.
icon=launcher/issn.png
[[ -f packaging/linux/issn.png ]] && icon=packaging/linux/issn.png
install -m644 "$icon" "$share/launcher/issn.png"
install -m644 launcher/pix_qr.png "$share/launcher/pix_qr.png"

# The launcher also runs run.py (--run) and the preparation scripts (--script) itself, as on
# Windows: the standard modules those scripts import come along as hidden imports.
# iced-x86 (pip install iced-x86==1.21.0): scripts/isa_rewrite.py, CPUs without MOVBE/BMI1/LZCNT.
# PIL._tkinter_finder: PIL.ImageTk loads it by name on Linux (the launcher's pictures); without it
# the frozen launcher stopped at its first image (ModuleNotFoundError, 2026-10-23).
hidden=()
for module in argparse base64 collections hashlib json re shutil struct tempfile xml.etree.ElementTree \
              urllib.request iced_x86 PIL._tkinter_finder; do
    hidden+=(--hidden-import "$module")
done
python3 -c 'import iced_x86' || { echo 'Need iced-x86: pip install iced-x86==1.21.0' >&2; exit 1; }
python3 -m PyInstaller --noconfirm --clean --log-level WARN --windowed --name issn-launcher \
    --distpath out/pyi-dist --workpath out/pyi-work --specpath out/pyi-work \
    --paths "$PWD/scripts" "${hidden[@]}" "$PWD/launcher/issn_launcher_win.py"
cp -r out/pyi-dist/issn-launcher/. "$share/"
find "$share" -name __pycache__ -prune -exec rm -r {} +
# No game files and nothing a player made (saves, shader caches built from the game's shaders,
# settings, logs) may ship: stop when one is found (as packaging/windows/package.sh).
forbidden=$(find "$appdir" \( -iname eboot.bin -o -iname sce_module -o -iname sce_sys -o -iname savedata \
    -o -iname user -o -iname cache -o -iname '*.log' -o -iname issn.ini -o -iname settings.json \
    -o -iname mods.json -o -iname patches.json -o -iname fsr4_shaders -o -iname 'nvngx_dlss*' \
    -o -iname '*.spv' -o -iname '*.pkg' -o -iname '*.sprx' -o -iname '*.prx' \) -print)
if [[ -n $forbidden ]]; then
    echo "AppImage holds files that must not ship:" >&2
    echo "$forbidden" >&2
    exit 1
fi
# FSR 4 (MIT, AMD's models; third_party/fsr4/README.md): shipped after the check above, which
# keeps any other .spv out.
cp -r third_party/fsr4 "$share/fsr4_shaders"
install -m755 packaging/linux/AppRun "$appdir/AppRun"
install -m644 packaging/linux/issn.desktop "$appdir/issn.desktop"
install -m644 "$icon" "$appdir/issn.png"

# Host-provided libraries: bundling any of these breaks drivers or the display connection.
excluded='^(ld-linux.*|libc|libm|libdl|librt|libpthread|libresolv|libutil|libnsl|libanl|libvulkan|libGL|libGLX|libGLdispatch|libEGL|libOpenGL|libgbm|libdrm.*|libX11|libX11-xcb|libxcb.*|libXau|libXdmcp|libXext|libXfixes|libXrender|libXi|libXcursor|libXrandr|libwayland-.*|libasound|libudev|libpulse.*|libfontconfig|libfreetype|libOpenCL|libva.*|libvdpau|libvpl|libnvidia-.*|libcuda|libz|libstdc\+\+|libgcc_s)\.so'
declare -A seen
bundle() {
    local elf=$1 line lib name
    while read -r line; do
        lib=$(awk '/=> \//{print $3}' <<<"$line")
        [[ -n $lib ]] || continue
        name=$(basename -- "$lib")
        [[ ${seen[$name]:-} ]] && continue
        seen[$name]=1
        [[ $name =~ $excluded ]] && continue
        install -m755 "$lib" "$appdir/usr/lib/$name"
        bundle "$lib"
    done < <(ldd "$elf")
}
bundle out/gpu/libissngpu.so
bundle out/issn-probe
# libstdc++/libgcc/libz come from the build host: the same glibc generation as the one that runs
# the AppImage is assumed, and a newer libstdc++ than Mesa's is the safe direction.
for name in libstdc++.so.6 libgcc_s.so.1; do
    install -m755 "$(ldd out/gpu/libissngpu.so | awk -v n="$name" '$1==n{print $3}')" "$appdir/usr/lib/$name"
done
# $ORIGIN/gpu is already the probe's RUNPATH; the libraries it needs sit in usr/lib via AppRun.
find "$appdir/usr/lib" -name '*.so*' -exec strip --strip-unneeded {} + 2>/dev/null || true

tool=${APPIMAGETOOL:-$(command -v appimagetool || true)}
if [[ -z $tool ]]; then
    tool=out/appimagetool-x86_64.AppImage
    [[ -x $tool ]] || {
        curl -fL -o "$tool" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
        chmod +x "$tool"
    }
fi
mkdir -p dist
# Written aside and moved over: replacing a running AppImage in place fails ("Text file busy").
ARCH=x86_64 "$tool" --appimage-extract-and-run "$appdir" dist/issn-x86_64.AppImage.new
mv -f dist/issn-x86_64.AppImage.new dist/issn-x86_64.AppImage
ls -lh dist/issn-x86_64.AppImage
