#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Prepares the game image and starts issn-probe (Windows and Linux; run.sh wraps it on Linux).

Environment variables (ISSN_GAME_DIR, ISSN_DATA_DIR, ISSN_PATCHES, ISSN_MODS_*,
ISSN_USER_DIR, ...). Extra arguments go to issn-probe.
The in-game "Apply and restart" runs this script again through ISSN_RESTART_COMMAND;
`--after PID` waits for the previous game process to end first (its GPU device and memory).
"""
import os
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

WINDOWS = sys.platform == 'win32'
EXE_SUFFIX = '.exe' if WINDOWS else ''
if WINDOWS:
    import ctypes

PORT = Path(__file__).resolve().parent



def shader_seed_name(port, user_dir):
    """The folder name in shader-seed.txt when that folder is in user_dir/cache, else None."""
    try:
        name = (Path(port) / 'shader-seed.txt').read_text().strip()
    except OSError:
        return None
    if not name or not all(c.isalnum() or c in '-_.' for c in name) or name.startswith('.'):
        return None
    return name if (Path(user_dir) / 'cache' / name).is_dir() else None

def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def wait_for(pid):
    if not WINDOWS:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                pass
            time.sleep(0.1)
        return
    kernel32 = ctypes.windll.kernel32
    SYNCHRONIZE = 0x00100000
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if handle:
        kernel32.WaitForSingleObject(handle, 30000)
        kernel32.CloseHandle(handle)


def no_console():
    """Started without a console (the launcher): console programs must not open their own."""
    if not WINDOWS:
        return 0
    return 0 if ctypes.windll.kernel32.GetConsoleWindow() else subprocess.CREATE_NO_WINDOW


def parse_cpu_features(output):
    """Instruction sets `issn-probe --cpu-features` reports missing ([] when complete)."""
    for line in output.splitlines():
        if line.startswith('CPU: missing '):
            return [name.strip() for name in line[len('CPU: missing '):].split(',') if name.strip()]
    return []


def isa_rewrite_wanted(missing, env):
    """ISSN_ISA_REWRITE=1 (or sse4a: the EXTRQ sites only) forces the ISA rewrite table, 0 disables
    it; otherwise the CPU decides."""
    policy = env.get('ISSN_ISA_REWRITE')
    if policy in ('0', '1', 'sse4a'):
        return policy != '0'
    return bool(missing)


def run_script(name, *args, capture=False, check=True):
    # Inside the packaged launcher executable (PyInstaller) the executable runs scripts itself.
    script = ['--script'] if getattr(sys, 'frozen', False) else []
    command = [sys.executable, *script, str(PORT / 'scripts' / name), *map(str, args)]
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    if capture:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True,
                                encoding='utf-8', env=env, creationflags=no_console())
        if not check:
            return result.returncode, result.stdout
        if result.returncode:
            sys.exit(result.returncode)
        return result.stdout
    # stdin given: the output handles are passed explicitly (a windowed launcher child would
    # get none otherwise).
    result = subprocess.run(command, stdin=subprocess.DEVNULL, env=env, creationflags=no_console())
    if result.returncode:
        sys.exit(result.returncode)
    return None


def launch_prefix(env):
    """Linux integrations chosen in the launcher: MangoHud is its Vulkan layer (an environment
    variable), GameMode a command that starts the game."""
    if WINDOWS:
        return []
    if env.get('ISSN_MANGOHUD') == '1':
        env.setdefault('MANGOHUD', '1')
    if env.get('ISSN_GAMEMODE') == '1':
        if gamemode := shutil.which('gamemoderun'):
            return [gamemode]
        print('GameMode: gamemoderun not found, starting without it', flush=True)
    return []


def find_executable(name):
    """bin/ (packaged), else out/ (built with build.sh)."""
    for candidate in (PORT / 'bin' / name, PORT / 'out' / name):
        if candidate.is_file():
            return candidate
    return None


def glyph_mounts(env, probe, game, out):
    """(probe arguments, style) of the controller glyphs: patched copies of the WADs that hold the
    game's button icons, mounted over them (scripts/glyphs.py). Opt-in: unless ISSN_GLYPH_STYLE is
    auto, xbox or nintendo nothing is asked, drawn or mounted, and old copies are removed. The
    game folder is never written."""
    choice = env.get('ISSN_GLYPH_STYLE', 'off')
    import game_profiles
    import patches as game_patches
    if game_patches.game_title_id(game) in game_profiles.INFAMOUS_SECOND_SON:
        return infamous_glyph_mounts(env, probe, game, out, choice)
    if choice not in ('auto', 'xbox', 'nintendo'):
        shutil.rmtree(Path(out) / 'glyphs', ignore_errors=True)
        return [], None
    try:
        import glyphs
        pad = ''
        if choice == 'auto':
            pad = subprocess.run([str(probe), '--gamepad'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 text=True, timeout=15, creationflags=no_console()).stdout
        style, faces, name = glyphs.resolve(choice, pad)
        mounts = glyphs.build(game, out, style, faces, env.get('ISSN_PAD_MAP', ''))
    except Exception as error:  # noqa: BLE001  (icons only: whatever fails, the game still starts)
        print(f'Glyphs: {error}; the game keeps its own button icons', flush=True)
        return [], None
    if not mounts:
        print('Glyphs: no Xbox or Nintendo controller, the game keeps its own button icons' if style == 'playstation'
              else 'Glyphs: no button icons found in this game folder', flush=True)
        return [], None
    print(f'Glyphs: {style}{f" ({name})" if name else ""}, on copies in {Path(out) / "glyphs"}; game files untouched',
          flush=True)
    return [part for guest, path in mounts.items() for part in ('--mount', f'{guest}={path}')], style


def infamous_glyph_mounts(env, probe, game, out, choice):
    """inFAMOUS Second Son's button icons (scripts/infamous_glyphs.py): keyboard and mouse keys or
    Xbox letters drawn into copies of the language packages' icon atlas. auto: the connected pad's
    style (a PlayStation pad keeps the game's own), the keyboard without one. The tester idea of
    u/humonculus87 (Reddit)."""
    import infamous_glyphs
    style = choice if choice in ('keyboard', 'xbox') else None
    name = ''
    if choice == 'auto':
        try:
            import glyphs
            pad = subprocess.run([str(probe), '--gamepad'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 text=True, timeout=15, creationflags=no_console()).stdout
            found = glyphs.parse_gamepad(pad)
        except Exception:  # noqa: BLE001  (icons only)
            found = None
        style = 'keyboard' if not found else None if found[0] == 'playstation' else 'xbox'
        name = found[2] if found else ''
    if not style:
        infamous_glyphs.build(game, out, 'off')
        if choice == 'auto':
            print(f'Button icons: the game\'s own (PlayStation controller{f" {name}" if name else ""})', flush=True)
        return [], None
    try:
        mounts = infamous_glyphs.build(game, out, style, env.get('ISSN_KEY_MAP', ''))
    except Exception as error:  # noqa: BLE001  (icons only: whatever fails, the game still starts)
        print(f'Button icons: {error}; the game keeps its own', flush=True)
        return [], None
    if not mounts:
        print('Button icons: no language package with the icon font in this game folder', flush=True)
        return [], None
    label = {'keyboard': 'keyboard and mouse', 'xbox': 'Xbox'}[style]
    print(f'Button icons: {label}{f" ({name})" if name else ""}, {len(mounts)} language packages redrawn '
          f'in {Path(out) / "glyphs"}; game files untouched', flush=True)
    return [part for guest, path in mounts.items() for part in ('--mount', f'{guest}={path}')], style


def main():
    args = sys.argv[1:]
    if len(args) >= 2 and args[0] == '--after':
        wait_for(int(args[1]))
        args = args[2:]
    # As run.sh: relative paths (ISSN_GAME_DIR, ISSN_DATA_DIR, fsr4_shaders/) are from the port.
    os.chdir(PORT)
    env = os.environ
    # The port's settings were called GOW3_* before the rename (it began as the God of War III
    # port): old commands and scripts keep working, an ISSN_* name wins when both are set.
    for key in [k for k in env if k.startswith('GOW3_')]:
        env.setdefault('ISSN_' + key[5:], env[key])
    data = Path(env.get('ISSN_DATA_DIR', PORT))
    out = data / 'out'
    out.mkdir(parents=True, exist_ok=True)
    env.setdefault('ISSN_CONFIG', str(data / 'issn.ini'))
    # Values this script derived on the previous start ("Apply and restart" inherits them) are
    # derived again from the new options.
    for key in env.pop('ISSN_DERIVED', '').split(','):
        env.pop(key, None)
    derived = []
    sys.path.insert(0, str(PORT / 'scripts'))
    import graphics_options
    graphics, reverted = graphics_options.prepare(env['ISSN_CONFIG'], out / 'graphics_state.json')
    if reverted:
        print('Graphics: the last restart never reached the game; previous options restored', flush=True)
    # issn: a variable set by hand (a test run) wins over issn.ini; the ones taken from the ini
    # are derived, so a restart from the in-game menu takes the ini's new values.
    for key, value in graphics_options.environment(graphics).items():
        if key not in env:
            env[key] = value
            derived.append(key)
    if not env.get('ISSN_FSR411_DIR') and not (PORT / 'fsr4_411').is_dir() and (data / 'fsr4_411').is_dir():
        env['ISSN_FSR411_DIR'] = str(data / 'fsr4_411')

    probe = Path(env['ISSN_PROBE']) if env.get('ISSN_PROBE') else find_executable('issn-probe' + EXE_SUFFIX)
    if not probe or not probe.is_file():
        fail(f'issn-probe{EXE_SUFFIX} not found: build it with build.sh or use a packaged build.')
    # Development builds run from the MSYS2 tree: its CLANG64 DLLs (SDL3, FFmpeg, ...).
    if WINDOWS and not (probe.parent / 'SDL3.dll').is_file():
        clang64 = Path(env.get('MSYS2_ROOT', r'C:\msys64')) / 'clang64' / 'bin'
        if clang64.is_dir():
            env['PATH'] = f'{clang64}{os.pathsep}{env.get("PATH", "")}'
    # Instruction sets this CPU lacks (MOVBE, BMI1, LZCNT): the ISA rewrite covers them; exit 20
    # is a set nothing covers.
    features = subprocess.run([str(probe), '--cpu-features'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              text=True, creationflags=no_console())
    if features.returncode == 20:
        print(features.stdout, end='', flush=True)
        sys.exit(20)
    missing = parse_cpu_features(features.stdout)

    game = Path(env.get('ISSN_GAME_DIR', PORT.parent / 'CUSA00004'))
    if not (game / 'eboot.bin').is_file():
        fail(f'No eboot.bin in {game} (set ISSN_GAME_DIR).')
    update_view = None
    if env.get('ISSN_PATCH_DIR'):
        import game_update
        try:
            update_view = game_update.create_view(game, env['ISSN_PATCH_DIR'], out)
        except (ValueError, OSError) as error:
            fail(str(error))
        game = update_view
        print(f'Game and update combined from {env["ISSN_PATCH_DIR"]}', flush=True)
    original_game = game.resolve()
    # issn_pc: the port is made for the 01.07 update (code patches, mouse hook, seed). A base 01.00
    # dump ends in an unsupported system call after the intro (sceKernelLoadStartModule, tester log
    # 10-24) - say so here instead. ISSN_ALLOW_OLD_VERSION=1 tries anyway.
    import game_profiles
    import patches as game_patches
    # Decided by the eboot.bin: some dumps carry the 01.07 eboot.bin under the base game's
    # param.sfo (APP_VER 01.00; tester log 10-25 played that way).
    title, version = game_patches.game_title_id(game), game_patches.game_app_version(game)
    if (title in game_profiles.INFAMOUS_SECOND_SON and version and version != '01.07'
            and env.get('ISSN_ALLOW_OLD_VERSION') != '1'
            and game_patches.eboot_sha256(game) not in game_profiles.INFAMOUS_107_EBOOTS):
        fail(f'{title} version {version}: this port needs the game\'s 01.07 update. In the launcher, '
             'set "Update folder" to the 01.07 update (the folder with its eboot.bin and sce_sys), or '
             'lay the update over the game folder, then press PLAY again.')
    game = Path(run_script('mods.py', game, '--out', out,
                           '--mods-dir', env.get('ISSN_MODS_DIR', data / 'mods'),
                           '--config', env.get('ISSN_MODS_CONFIG', data / 'mods.json'),
                           '--enabled', env.get('ISSN_MODS_ENABLED', '1'), capture=True).strip())
    mod_view = game if game.resolve() != original_game else None
    try:
        run_script('prepare.py', game, '--out', out)
        # issn: link_libc.py (boot-libc.bin, superseded by link_modules.py) is not run: nothing
        # loads its output, and it rejects older SDKs' libc.prx (inFAMOUS Second Son).
        run_script('link_modules.py', game, '--out', out)
        # A failed rewrite prints its reason and the game starts without the table.
        isa_args = []
        if isa_rewrite_wanted(missing, env):
            code, output = run_script('isa_rewrite.py', out / 'boot-linked.bin', '--out', out,
                                      capture=True, check=False)
            print(output, end='', flush=True)
            if code == 0:
                isa_args = ['--isa-rewrite', str(out / 'isa-rewrite.bin')]
        run_script('content_profile.py', game, '--out', out, '--sku', env.get('ISSN_CONTENT_SKU', 'full'))

        # issn: settings a game needs (scripts/game_profiles.py); derived again on each start.
        import game_profiles
        title, game_settings = game_profiles.environment(game, env)
        if game_settings:
            env.update(game_settings)
            derived.extend(game_settings)
            print(f'Game profile {title}: ' + ', '.join(f'{k}={v}' for k, v in game_settings.items()), flush=True)

        import patches
        from patches import game_profile_for, patch_requirements, patch_status, selected_patches
        profile = game_profile_for(game)
        engine_fps = 60
        # The window shows the game's icon (BMP: the format SDL loads without extra libraries); on
        # Linux the launcher's own icon (the AppImage ships the one chosen when it was built) and
        # an application id the desktop entry matches, so the shell shows the same icon.
        icon_file = game / 'sce_sys' / 'icon0.png'
        if not WINDOWS:
            env.setdefault('SDL_APP_ID', 'issn')
            if (PORT / 'launcher' / 'issn.png').is_file():
                icon_file = PORT / 'launcher' / 'issn.png'
        try:
            from PIL import Image
            with Image.open(icon_file) as icon:
                icon.convert('RGBA').resize((64, 64), Image.LANCZOS).save(out / 'window_icon.bmp')
            env.setdefault('ISSN_WINDOW_ICON', str(out / 'window_icon.bmp'))
        except (ImportError, OSError):
            pass
        # The temporal upscalers and motion vectors are not calibrated for this game yet: they stay
        # off unless the launcher's experimental switch is on (then issn.ini decides).
        if env.get('ISSN_EXPERIMENTAL_UPSCALER') != '1':
            env.setdefault('ISSN_UPSCALER', 'none')
        if profile:
            names = selected_patches(profile[1], profile[2], env.get('ISSN_PATCHES', ''), env.get('ISSN_PATCHES_ONLY') == '1')
            # issn.ini decides the resolution and frame rate patches (in-game menu, launcher); their
            # names are God of War III's (issn: other games keep their patch file's choice).
            if profile[3] == 'God of War III':
                names = graphics_options.patch_names(names, graphics)
            names = graphics_options.restrict_to_available(names, patches.available_patches(profile[1], profile[2]))
            # issn: Internal resolution (the shim's render size) replaces the old fixed-size
            # resolution patches; both would rewrite the same immediates.
            if profile[3] == 'inFAMOUS Second Son' and (env.get('ISSN_RENDER_HEIGHT') or env.get('ISSN_RENDER_SCALE')):
                dropped = [n for n in names if n.startswith('Native ') and '(render and output)' in n]
                if dropped:
                    print(f'Patches: {", ".join(dropped)} left out: Internal resolution sets the size', flush=True)
                names = [n for n in names if n not in dropped]
            env['ISSN_PATCHES'] = ';'.join(names)
            env['ISSN_PATCHES_ONLY'] = '1'
        (out / 'patch_status.json').unlink(missing_ok=True)
        run_script('patches.py', '--out', out, '--extra', env.get('ISSN_PATCHES', ''), '--game-dir', game,
                   '--patches-dir', env.get('ISSN_PATCHES_DIR', data / 'patches'),
                   '--patches-config', env.get('ISSN_PATCHES_CONFIG', data / 'patches.json'))
        if profile:
            # Only the patches found in this eboot.bin count: a frame rate patch that was skipped
            # leaves the engine at 60 FPS, and the notes' memory and VBlank needs follow the same list.
            applied = (patch_status(out) or {}).get('applied', [])
            engine_fps = graphics_options.applied_engine_fps(applied)
            dmem, vblank = patch_requirements(profile[1], applied, profile[2])
            if profile[3] == 'inFAMOUS Second Son':
                dmem = max(dmem or 0, infamous_dmem_mb(env, applied)) or None
            for key, value in (('ISSN_DMEM_MB', dmem), ('ISSN_VBLANK_HZ', vblank)):
                if value and key not in env:
                    env[key] = str(value)
                    derived.append(key)
        # issn-axy: without ISSN_VBLANK_HZ the GPU library takes the frame rate limit from issn.ini.
        # Without the frame rate patch the engine steps 1/60 s per frame: the present thread must
        # not flip faster (issn_graphics.h PresentLimit), or the game speeds up.
        env['ISSN_ENGINE_FPS'] = str(engine_fps)
        derived.append('ISSN_ENGINE_FPS')
        print(f'Engine frame rate: {engine_fps} FPS'
              + ('' if engine_fps > 60 or graphics['engine_fps'] == '60' else
                 ' (the frame rate patch was not found in this eboot.bin)'), flush=True)
        glyph_args, glyph_style = glyph_mounts(env, probe, game, out)
        if glyph_style:  # the in-game menu names the buttons the same way
            env['ISSN_GLYPHS'] = glyph_style
            derived.append('ISSN_GLYPHS')
        voice = env.get('ISSN_VOICE', '').strip().lower()
        if voice:
            # inFAMOUS Second Son: the voices of one language under the text of another (the
            # runtime opens lang_<x>_audio.xpps as lang_<voice>_audio.xpps).
            others = ('english', 'dutch', 'french', 'german', 'italian', 'polish', 'portuguese',
                      'russian', 'spanish', 'turkish', 'danish', 'finnish', 'norwegian', 'swedish',
                      'czech', 'arabic', 'greek')
            aliases = [f'lang_{x}_audio.xpps=lang_{voice}_audio.xpps' for x in others if x != voice]
            if env.get('ISSN_FILE_ALIAS'):
                aliases.insert(0, env['ISSN_FILE_ALIAS'])
            env['ISSN_FILE_ALIAS'] = ';'.join(aliases)
            derived.append('ISSN_FILE_ALIAS')
            print(f'Voices: {voice} (with the text of ISSN_LANGUAGE)', flush=True)
        # issn-axy: FSR 4 Native AA's model (ISSN_FSR4): fsr4_shaders in a package, else the
        # repository's copy.
        if 'ISSN_FSR4_DIR' not in env:
            for candidate in (PORT / 'fsr4_shaders', PORT / 'third_party' / 'fsr4'):
                if (candidate / 'rcas.spv').is_file():
                    env['ISSN_FSR4_DIR'] = str(candidate)
                    break
        env['ISSN_DERIVED'] = ','.join(derived)
        # The guest memory pool is committed in full at start: a resolution patch above 1080p
        # can need more than 16 GB of RAM, and the game then closes when the page file cannot grow.
        import guest_memory
        if warning := guest_memory.memory_warning(env.get('ISSN_DMEM_MB'), guest_memory.physical_memory_mb(),
                                                  env.get('ISSN_STREAM_MEMORY', 'cached')):
            print(warning, flush=True)

        # The in-game restart starts this script again once this process is gone.
        # GPU caches (shaders, pipelines) beside the saves; read before main(), so set here.
        user_dir = Path(env.get('ISSN_USER_DIR', data / 'user')).resolve()
        user_dir.mkdir(parents=True, exist_ok=True)
        env.setdefault('ISSN_GPU_USER_DIR', str(user_dir))
        # issn_pc: the shader seed of this package (packaging/windows/package.sh) is the cache folder.
        if (seed_name := shader_seed_name(PORT, user_dir)):
            env.setdefault('SHADPS4_CACHE_NAME', seed_name)
        from save_backup import backup
        try:
            if made := backup(user_dir):
                print(f'Saves backed up to {made}', flush=True)
        except OSError as error:
            print(f'Save backup failed, starting anyway: {error}', flush=True)
        this = ['--run'] if getattr(sys, 'frozen', False) else [str(Path(__file__).resolve())]
        # Windows: the probe starts the new launch and ends, so it waits for this process. Linux:
        # the probe replaces itself with the new launch while this process still waits for it.
        after = ['--after', str(os.getpid())] if WINDOWS else []
        restart = [sys.executable, *this, *after, *args]
        env['ISSN_RESTART_COMMAND'] = subprocess.list2cmdline(restart) if WINDOWS else shlex.join(restart)
        command = [*launch_prefix(env), str(probe), str(out / 'boot-linked.bin'), '--content-profile', str(out / 'content.bin'),
                   '--patches', str(out / 'patches.bin'), '--app0', str(game),
                   '--user', str(user_dir),
                   '--timeout', env.get('ISSN_TIMEOUT', '0'), *isa_args, *glyph_args, *args]
        # No stdin: an inherited pipe (shells such as Git Bash) cost the game its console output.
        if WINDOWS and env.get('ISSN_DEBUGGER') == '1':
            # issn: a minimal debugger reports fast fails, which no handler in the process sees.
            sys.path.insert(0, str(PORT / 'scripts'))
            import windebug
            code = windebug.run(command)
        else:
            code = subprocess.run(command, stdin=subprocess.DEVNULL, creationflags=no_console()).returncode
        report_exit(code)
        return code
    finally:
        if mod_view:
            shutil.rmtree(mod_view, ignore_errors=True)
        if update_view:
            game_update.remove_view(update_view)


def infamous_render_lines(env, applied):
    """The lines inFAMOUS Second Son renders: Internal resolution (ISSN_RENDER_HEIGHT /
    ISSN_RENDER_SCALE, the shim's render size) or an old "Native WxH" patch, else 1080."""
    try:
        if env.get('ISSN_UPSCALE_FACTOR') and float(env['ISSN_UPSCALE_FACTOR']) >= 1:
            # the window decides (unknown here): a 4K screen, the most it can be
            return max(360, min(2160, round(2160 / float(env['ISSN_UPSCALE_FACTOR']))))
        if env.get('ISSN_RENDER_HEIGHT'):
            return max(360, min(2160, int(env['ISSN_RENDER_HEIGHT'])))
        if env.get('ISSN_RENDER_SCALE'):
            return max(360, min(2160, round(1080 * float(env['ISSN_RENDER_SCALE']) / 100)))
    except ValueError:
        pass
    for name in applied:
        if m := re.match(r'Native (\d+)x(\d+) \(render and output\)', name):
            return int(m.group(2))
    return 1080


def infamous_dmem_mb(env, applied):
    """Direct memory for inFAMOUS Second Son above 1080 lines (0 at or below). The game gives its
    GPU heap all the direct memory left at boot (3.3 GiB of the retail 5056 MiB); its render
    targets at 4K need about four times their 1080p room, and with the retail pool the picture
    stayed black after the opening poster (RX 7900 XT tester, native 4K patch, 10-24). 4 GiB more
    at 4K (as the shadPS4 notes ask for God of War III's), in proportion to the pixels below."""
    lines = infamous_render_lines(env, applied)
    if lines <= 1080:
        return 0
    extra = 4096 * ((lines / 1080) ** 2 - 1) / 3
    return 5056 + int(-(-extra // 256) * 256)


# Windows exit codes of a process that ended without its own report.
EXIT_CODES = {
    0xC0000005: 'access violation outside the loader\'s handler',
    0xC00000FD: 'stack overflow',
    0xC0000409: 'fast fail (stack buffer overrun check, abort or an invalid parameter)',
    0xC0000374: 'heap corruption',
    0xC000001D: 'illegal instruction',
    0xE0000008: 'out of memory',
}


def report_exit(code):
    """Prints the probe's exit code when it is not a normal one (Windows status codes)."""
    status = code & 0xffffffff
    if status in (0, 1, 3, 124) or status < 256:
        return
    print(f'issn-probe ended with status 0x{status:08X}' +
          (f' ({EXIT_CODES[status]})' if status in EXIT_CODES else ''), file=sys.stderr, flush=True)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
