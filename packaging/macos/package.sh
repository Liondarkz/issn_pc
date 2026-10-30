#!/usr/bin/env bash
# Builds dist/issn-macos-x86_64.zip: the port's current macOS build (run build.sh first), its
# scripts and every non-system library it loads, with relative install names, so a tester
# needs neither Homebrew nor the dependency build. Unzip, then: bash run.sh (ISSN_PREBUILT=1).
set -euo pipefail
cd -- "$(dirname -- "$0")/../.."
deps=${ISSN_MAC_DEPS:-$PWD/out/macos-deps}
[[ -f out/issn-probe && -f out/gpu/libissngpu.dylib ]] || { echo 'Build first: bash build.sh' >&2; exit 1; }
name=issn-macos-x86_64
stage=dist/$name
rm -rf "$stage" "dist/$name.zip" && mkdir -p "$stage/bin/gpu"
cp out/issn-probe out/issn-gpu-capabilities "$stage/bin/"
cp out/gpu/libissngpu.dylib "$stage/bin/gpu/"
cp "$deps/lib/libMoltenVK.dylib" "$stage/bin/"
# shadPS4 points the Vulkan loader at the executable's directory; the path is the file's own.
printf '{"file_format_version":"1.0.0","ICD":{"library_path":"./libMoltenVK.dylib","api_version":"1.4.0","is_portability_driver":true}}\n' \
    > "$stage/bin/MoltenVK_icd.json"

# The libraries of $deps a Mach-O file loads, by install name (absolute or @rpath/).
deps_of() {
    otool -L "$1" | tail -n +2 | awk '{print $1}' | while read -r dep; do
        case $dep in
        "$deps"/lib/*|@rpath/*) [[ $dep == */libissngpu.dylib ]] || echo "$dep" ;;
        esac
    done
}
# Copies them into bin/ until nothing new appears (FFmpeg's libraries load each other).
queue=("$stage/bin/issn-probe" "$stage/bin/issn-gpu-capabilities" "$stage/bin/gpu/libissngpu.dylib")
while ((${#queue[@]})); do
    file=${queue[0]} queue=("${queue[@]:1}")
    for dep in $(deps_of "$file"); do
        base=$(basename "$dep")
        if [[ ! -f $stage/bin/$base ]]; then
            cp -L "$deps/lib/$base" "$stage/bin/$base"
            chmod u+w "$stage/bin/$base"
            install_name_tool -id "@rpath/$base" "$stage/bin/$base" 2>/dev/null
            queue+=("$stage/bin/$base")
        fi
        install_name_tool -change "$dep" "@rpath/$base" "$file" 2>/dev/null
    done
done
# Search paths: bin/ for everything, never the build machine's directories.
for file in "$stage"/bin/issn-probe "$stage"/bin/issn-gpu-capabilities "$stage"/bin/gpu/libissngpu.dylib "$stage"/bin/*.dylib; do
    for path in $(otool -l "$file" | awk '/LC_RPATH/{getline; getline; print $2}'); do
        [[ $path == @* ]] || install_name_tool -delete_rpath "$path" "$file" 2>/dev/null
    done
done
install_name_tool -add_rpath @loader_path "$stage/bin/issn-probe" 2>/dev/null || true
install_name_tool -add_rpath @loader_path "$stage/bin/issn-gpu-capabilities" 2>/dev/null || true
install_name_tool -add_rpath @loader_path/.. "$stage/bin/gpu/libissngpu.dylib" 2>/dev/null || true
install_name_tool -add_rpath @loader_path "$stage/bin/gpu/libissngpu.dylib" 2>/dev/null || true
# install_name_tool invalidates signatures; Apple silicon (Rosetta included) needs one: ad hoc.
for file in "$stage"/bin/issn-probe "$stage"/bin/issn-gpu-capabilities "$stage"/bin/gpu/*.dylib "$stage"/bin/*.dylib; do
    codesign --force --sign - "$file" 2>/dev/null
done

cp -R run.sh run.py scripts launcher patches LICENSE "$stage/"
cp packaging/macos/README-macOS.txt "$stage/"
# run.sh on a packaged build: nothing is built (ISSN_PREBUILT).
sed -i '' 's/^data=\${ISSN_DATA_DIR:-.}$/data=${ISSN_DATA_DIR:-.}; export ISSN_PREBUILT=${ISSN_PREBUILT:-1}/' "$stage/run.sh"
(cd dist && ditto -c -k --keepParent "$name" "$name.zip")
ls -lh "dist/$name.zip"
