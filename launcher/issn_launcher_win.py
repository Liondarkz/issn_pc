#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""inFAMOUS Second Son (issn) launcher for Windows and Linux (Tkinter), from the God of War III
(gow3_pc) launcher.

Every setting of the port in one window: the game folder and saves, issn.ini (upscaler,
preset, output, effects), start-up options passed to run.py as environment variables
(frame rate, presentation, HDR, ...), mods, third-party patches and controls.
Launcher options live in %APPDATA%/issn-launcher/settings.json (~/.config/issn-launcher on Linux).

Frozen with PyInstaller (packaging/windows/package.sh) the same inFAMOUS Second Son.exe also runs the
game without the window (`--play`), run.py (`--run`) and the preparation scripts (`--script`),
so a packaged port needs no Python installation.
"""
import contextlib
import glob
import json
import os
from pathlib import Path
import queue
import re
import runpy
import shutil
import stat
import signal
import subprocess
import sys
import tempfile
import threading
import unicodedata
import time
import urllib.request
import webbrowser
import zipfile

WINDOWS = sys.platform == 'win32'
# Windows without a cursor of their own show the X root window's, which XWayland leaves blank.
ARROW = '' if WINDOWS else 'left_ptr'
if WINDOWS:
    import ctypes
FROZEN = getattr(sys, 'frozen', False)
PORT_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PORT_DIR / 'scripts'))
import save_backup  # noqa: E402  (scripts/)
import steam_shortcut  # noqa: E402  (scripts/)
from patches import game_profile, game_sfo  # noqa: E402  (scripts/)
from prepare import load_sfo  # noqa: E402  (scripts/)
DATA_DIR = Path(os.environ.get('ISSN_DATA_DIR', PORT_DIR))
CONFIG_DIR = (Path(os.environ.get('APPDATA', Path.home())) if WINDOWS else
              Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')) / 'issn-launcher'
CONFIG_FILE = CONFIG_DIR / 'settings.json'
MOD_PRESETS = DATA_DIR / 'mods_presets.json'
APP_NAME = 'inFAMOUS Second Son'
MAX_LOG_LINES = 6000
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# The game and everything it starts form one process group, ended together (GameJob).
NEW_GROUP = {} if WINDOWS else {'start_new_session': True}
UI_FONT, MONO_FONT, SERIF_FONT = 'Segoe UI', 'Consolas', 'Georgia'
# This build; GitHub release tags are windows-v<VERSION>.
VERSION = '0.3.4'
# This port's own test builds (never the God of War III port's: they must not be installed over
# it). The source repository is private; the builds are published in a public repository that
# holds only releases (tag windows-v<VERSION>, the zip from packaging/windows/package.sh).
RELEASES_REPO = 'Liondarkz/issn_pc'  # releases moved here with 0.3.0 (the repo is public)
RELEASES_API = f'https://api.github.com/repos/{RELEASES_REPO}/releases/latest'
RELEASES_PAGE = f'https://github.com/{RELEASES_REPO}/releases'
UPDATES = bool(RELEASES_API)
UPDATE_DIR = Path(tempfile.gettempdir()) / 'issn-update'
# Never copied over an installation by an update (the package does not hold them either).
USER_FILES = ('user', 'out', 'mods', 'issn.ini', 'mods.json', 'mods_presets.json', 'patches.json', 'last_run.log')
# The display face for titles: Bebas Neue (launcher/issn_font.ttf, SIL Open Font License, with the
# accents of the Latin-script languages); main() registers it for this process only.
FONT_FILE = PORT_DIR / 'launcher' / 'issn_font.ttf'
HEADING_FAMILY = 'Bebas Neue'  # the family name inside issn_font.ttf
FR_PRIVATE = 0x10


# ---------------------------------------------------------------------------------------------
# Command line roles of the frozen executable.

def attach_stdio():
    """A windowed executable starts without sys.stdout; inherited pipes or a console still exist."""
    if not WINDOWS:
        return
    import msvcrt
    for name, std in (('stdout', -11), ('stderr', -12)):
        if getattr(sys, name) is not None:
            continue
        stream = None
        handle = ctypes.windll.kernel32.GetStdHandle(std)
        if handle and handle != ctypes.c_void_p(-1).value:
            try:
                stream = open(msvcrt.open_osfhandle(handle, os.O_WRONLY), 'w', encoding='utf-8',
                              errors='replace', buffering=1)
            except OSError:
                stream = None
        setattr(sys, name, stream or open(os.devnull, 'w'))


def run_role(argv):
    """--run [args]: run.py; --script <file> [args]: a preparation script. Returns an exit code."""
    attach_stdio()
    for stream in (sys.stdout, sys.stderr):  # keep messages in order with the game's output
        try:
            stream.reconfigure(encoding='utf-8', line_buffering=True)
        except (AttributeError, ValueError):
            pass
    if argv[0] == '--script':
        path, sys.argv = argv[1], argv[1:]
    else:
        path = str(PORT_DIR / 'run.py')
        sys.argv = [path, *argv[1:]]
    try:
        runpy.run_path(path, run_name='__main__')
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
    return 0


def run_command():
    """The command that starts run.py: this executable when frozen, else Python."""
    return [sys.executable, '--run'] if FROZEN else [sys.executable, str(PORT_DIR / 'run.py')]


def find_tool(name):
    """A port executable: bin/ in a packaged build, out/ in a source checkout (as run.py looks)."""
    return next((str(p) for p in (PORT_DIR / 'bin' / name, PORT_DIR / 'out' / name) if p.is_file()), None)


def parse_gpu_list(text):
    """One tuple per line of `issn-gpu-capabilities --list`: index, name, type and, from the tools
    that print them, the game's multisampling default on that driver ('off' or 'on') and '1' on
    the GPU the game picks without ISSN_GPU_ID. Other lines are skipped."""
    rows = [line.split('\t') for line in text.splitlines()]
    return [tuple(row) for row in rows if len(row) >= 3 and row[0].isdigit()]


def chosen_gpu(gpus, gpu_id):
    """The row of parse_gpu_list the game will run on: the Graphics card setting when it is in the
    list, else the GPU the tool marks as the game's pick; None when neither is known."""
    chosen = [row for row in gpus if row[0] == str(gpu_id)]
    return (chosen or [row for row in gpus if len(row) > 4 and row[4] == '1'] or [None])[0]


def no_msaa_options(gpus, gpu_id):
    """NO_MSAA with Automatic naming the chosen card and the game's decision for it."""
    row = chosen_gpu(gpus, gpu_id)
    if row is None or len(row) < 4 or row[3] not in ('off', 'on'):
        return list(NO_MSAA)
    label = _('Automatic ({}: off)' if row[3] == 'off' else 'Automatic ({}: on)').format(row[1])
    return [('', (label,))] + NO_MSAA[1:]


def list_gpus():
    """The Vulkan GPUs in the order the game numbers them, or [] when the tool cannot tell."""
    tool = find_tool('issn-gpu-capabilities' + ('.exe' if WINDOWS else ''))
    if not tool:
        return []
    try:
        done = subprocess.run([tool, '--list'], capture_output=True, text=True, timeout=5,
                              creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_gpu_list(done.stdout) if done.returncode == 0 else []


# ---------------------------------------------------------------------------------------------
# Languages: every text is written in English with the Russian next to it; the other
# languages are in issn_lang.py, keyed by the English text.

sys.path.insert(0, str(Path(__file__).resolve().parent))
import button_icons  # noqa: E402
import issn_lang  # noqa: E402

LANG = 'en'
FONT_LOADED = False  # set by main() for the window; the command-line roles never need the font


WARN = '  ⚠'  # marks a risky choice; the text before it is translated as usual


def monitor_under_pointer(root):
    """(x, y, width, height) of the monitor holding the mouse pointer, from xrandr; zeros if unknown."""
    try:
        text = subprocess.run(['xrandr', '--query'], capture_output=True, text=True, timeout=3,
                              env=host_environment()).stdout
    except (OSError, subprocess.SubprocessError):
        return 0, 0, 0, 0
    pointer_x, pointer_y = root.winfo_pointerxy()
    monitors = [(x, y, width, height) for width, height, x, y in
                (tuple(map(int, match.groups())) for match in
                 re.finditer(r' connected (?:primary )?(\d+)x(\d+)\+(\d+)\+(\d+)', text))]
    for x, y, width, height in monitors:
        if x <= pointer_x < x + width and y <= pointer_y < y + height:
            return x, y, width, height
    return monitors[0] if monitors else (0, 0, 0, 0)


def pick_fonts(families):
    """Windows fonts first, then the DejaVu family that most Linux desktops ship."""
    global UI_FONT, MONO_FONT, SERIF_FONT
    available = set(families)
    pick = lambda names: next((name for name in names if name in available), names[-1])
    UI_FONT = pick(('Segoe UI', 'Noto Sans', 'DejaVu Sans'))
    MONO_FONT = pick(('Consolas', 'Noto Sans Mono', 'DejaVu Sans Mono'))
    SERIF_FONT = pick(('Georgia', 'Noto Serif', 'DejaVu Serif'))


def host_environment():
    """The environment of the user's session for programs outside the bundle (xdg-open)."""
    env = dict(os.environ)
    for name in ('LD_LIBRARY_PATH', 'PYTHONHOME', 'PYTHONPATH', 'TCL_LIBRARY', 'TK_LIBRARY', 'FONTCONFIG_FILE'):
        env.pop(name, None)
    return env


def open_url(url):
    if WINDOWS:
        webbrowser.open(url)
    else:
        subprocess.Popen(['xdg-open', url], env=host_environment(), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _(en, ru=None):
    if en.endswith(WARN):
        return _(en[:-len(WARN)], ru and ru.removesuffix(WARN)) + WARN
    if LANG == 'ru':
        return ru or en
    return issn_lang.table(LANG).get(en) or en


def environment_language():
    for name in ('LC_ALL', 'LC_MESSAGES', 'LANG'):
        code = re.match(r'[a-z]{2}', os.environ.get(name, ''))
        if code:
            return code.group()
    return 'en'


def windows_language():
    if not WINDOWS:
        return environment_language()
    try:
        primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3ff
    except (AttributeError, OSError):
        return 'en'
    return issn_lang.WINDOWS_LANGUAGES.get(primary, 'en')


def register_font(path):
    """Makes a font file available to this process (nothing else sees it). False when the file is missing or
    GDI refuses it: the launcher then keeps its standard fonts. Linux: a fontconfig file that adds the
    font's folder to the system's (FONTCONFIG_FILE, set before Tk starts), in the launcher's config folder."""
    if not WINDOWS:
        return register_font_fontconfig(path)
    try:
        return path.is_file() and bool(ctypes.windll.gdi32.AddFontResourceExW(str(path), FR_PRIVATE, 0))
    except (AttributeError, OSError):
        return False


def register_font_fontconfig(path):
    if not path.is_file():
        return False
    system = next((p for p in ('/etc/fonts/fonts.conf', '/usr/share/fontconfig/fonts.conf') if Path(p).is_file()), None)
    if system is None:
        return False
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        conf = CONFIG_DIR / 'fonts.conf'
        from xml.sax.saxutils import escape
        text = ('<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig>\n'
                f'  <include ignore_missing="yes">{escape(system)}</include>\n'
                f'  <dir>{escape(str(path.parent))}</dir>\n'
                f'  <cachedir>{escape(str(CONFIG_DIR / "fontcache"))}</cachedir>\n</fontconfig>\n')
        if not conf.is_file() or conf.read_text(encoding='utf-8') != text:
            conf.write_text(text, encoding='utf-8')
    except OSError:
        return False
    os.environ['FONTCONFIG_FILE'] = str(conf)
    return True


# The title face has ASCII letters only: the Latin-script languages use it for headings with
# the accents stripped (heading_text); the other scripts keep the launcher's usual families.
LATIN_UI_LANGUAGES = ('en', 'es', 'pt', 'fr', 'de', 'it', 'pl', 'tr')
HEADING_LETTERS = {'ß': 'ss', 'ẞ': 'SS', 'œ': 'oe', 'Œ': 'OE', 'æ': 'ae', 'Æ': 'AE', 'ø': 'o', 'Ø': 'O',
                   'ł': 'l', 'Ł': 'L', 'ı': 'i', 'đ': 'd', 'Đ': 'D'}


def heading_face(every_language=False):
    """Whether headings are drawn with the title face: once its file is registered, in the
    Latin-script languages, or in every language for the game's name."""
    return FONT_LOADED and (every_language or LANG in LATIN_UI_LANGUAGES)


def heading_font(size, fallback=None, every_language=False):
    if heading_face(every_language):
        return (HEADING_FAMILY, size)
    return (fallback or SERIF_FONT, size)


def plain_letter(c):
    """c without its accents when its base letter is ASCII (Á becomes A). The other scripts keep
    their letters whole: Й is not И, ゴ is not コ, a hangul syllable is not its jamo."""
    base = unicodedata.normalize('NFD', c)[0]
    return base if base.isascii() else c


def heading_text(text, every_language=False):
    """A heading's text for its font. Bebas Neue has the accents of every Latin-script language
    the launcher speaks, so the text stays as written (HEADING_LETTERS and plain_letter remain for
    a face without them)."""
    return text


# ---------------------------------------------------------------------------------------------
# Settings. issn.ini keys (the game reads them, the in-game menu edits them) and the
# launcher's own settings.json (passed to run.py as environment variables).

INI_FLAGS = {'cheat_double_jumps', 'cheat_very_hard', 'object_motion', 'show_fps', 'vsync', 'async_shaders', 'deferred_readback',
             'stale_readback'}
# The graphics keys are also in the in-game menu (Insert / R3+L2); run.py reads the startup ones.
INI_DEFAULTS = {'upscaler': 'fsr3', 'preset': '1',
                'object_motion': '1', 'show_fps': '1', 'output_res': '1920x1080',
                'live_resolution': 'auto', 'display_mode': 'windowed', 'vsync': '1', 'fps_limit': '0',
                'async_shaders': '1', 'deferred_readback': '1', 'stale_readback': '1',
                'render_resolution': 'native', 'engine_fps': '120',
                'frames_queued': '1', 'cheat_very_hard': '0', 'cheat_double_jumps': '0'}
DISPLAY_MODES = [('windowed', ('Windowed',)), ('borderless', ('Borderless',)), ('fullscreen', ('Fullscreen',))]
FPS_LIMITS = [('30', ('30',)), ('60', ('60',)), ('120', ('120',)), ('240', ('240',)), ('0', ('Unlimited',))]
# The sidebar's pages, top to bottom, and the methods that refresh a page's lists when it is shown.
PAGES = ('home', 'graphics', 'performance', 'game', 'advanced')
PAGE_REFRESH = {'home': ('refresh_status',), 'game': ('refresh_lists', 'refresh_game_patches')}
APP_DEFAULTS = {'ui_language': '', 'game_dir': os.environ.get('ISSN_GAME_DIR', str(PORT_DIR.parent / 'CUSA00004') if WINDOWS else ''), 'update_dir': '', 'experimental_upscaler': False,
                'video_backend': 'auto', 'gamemode': False, 'mangohud': False, 'desktop_integration': True, 'user_dir': '',
                'mods_dir': '', 'mods_enabled': True, 'patches_dir': '', 'language': '1', 'game_language': '', 'pad_emulation': '',
                'player_name': '', 'hdr': False, 'draw_pipe': '', 'readbacks': '', 'no_msaa': '', 'stream_memory': 'cached',
                'gpu_id': '', 'background_warmup': True, 'parallel_warmup': True, 'pipeline_binaries': False,
                'perf_diag': False, 'frame_capture_key': False, 'frame_stats': False, 'gpu_profile': False,
                'vk_validation': False, 'close_on_play': False,
                'check_updates': False, 'game_patches': {}, 'controls': {}, 'pad_style': 'playstation',
                'glyph_style': 'off', 'compute_priority': True, 'ordered_readback': False, 'draw_distance': '',
                'perf_mode': '', 'render_scale': '', 'shader_compile': '', 'screen_shape': '',
                'anti_aliasing': 'game'}

# PS4 button, default key, default gamepad button (SDL names; src/runtime_pad.c). L2/R2 come
# from the analog triggers and the left touchpad click (photo mode) from L1 + the right click's
# button; neither is remapped on the gamepad.
# issn: the keyboard layout of axylpup's build (runtime_pad.c key_map); L2 / R2 are also the
# right / left mouse buttons.
CONTROLS = [('cross', 'Space', 'a'), ('circle', 'Left Shift', 'b'), ('square', 'C', 'x'),
            ('triangle', 'F', 'y'), ('l1', 'Q', 'leftshoulder'), ('r1', 'E', 'rightshoulder'),
            ('l2', '1', None), ('r2', '3', None), ('l3', 'U', 'leftstick'),
            ('r3', 'O', 'rightstick'), ('options', 'Return', 'start'),
            ('touchpad_left', 'R', None), ('touchpad_right', 'G', 'back'),
            ('up', 'Up', 'dpup'), ('down', 'Down', 'dpdown'), ('left', 'Left', 'dpleft'), ('right', 'Right', 'dpright'),
            # issn: rattles the pad's motion sensor (the spray can); misc1 is the Share button of an
            # Xbox Series pad and the mute button of a DualSense, which the game does not use.
            ('shake', 'X', 'misc1')]
PS_BUTTON_NAMES = {'cross': 'Cross', 'circle': 'Circle', 'square': 'Square', 'triangle': 'Triangle',
                   'l1': 'L1', 'r1': 'R1', 'l2': 'L2', 'r2': 'R2', 'l3': 'L3', 'r3': 'R3',
                   'options': 'Options', 'touchpad_left': 'Touchpad left', 'touchpad_right': 'Touchpad right',
                   'up': 'D-pad up', 'down': 'D-pad down', 'left': 'D-pad left', 'right': 'D-pad right',
                   'shake': 'Shake (motion)'}
# Gamepad buttons by SDL name, labelled as on a PlayStation or an Xbox controller.
PAD_STYLES = {
    'playstation': {'a': 'Cross', 'b': 'Circle', 'x': 'Square', 'y': 'Triangle', 'leftshoulder': 'L1',
                    'rightshoulder': 'R1', 'leftstick': 'L3', 'rightstick': 'R3', 'start': 'Options',
                    'back': 'Share / Create', 'touchpad': 'Touchpad click', 'guide': 'PS button',
                    'dpup': 'D-pad up', 'dpdown': 'D-pad down', 'dpleft': 'D-pad left',
                    'dpright': 'D-pad right', 'misc1': 'Mute button'},
    'xbox': {'a': 'A', 'b': 'B', 'x': 'X', 'y': 'Y', 'leftshoulder': 'LB', 'rightshoulder': 'RB',
             'leftstick': 'LS (left stick click)', 'rightstick': 'RS (right stick click)', 'start': 'Menu',
             'back': 'View', 'touchpad': 'Touchpad click', 'guide': 'Xbox button', 'dpup': 'D-pad up',
             'dpdown': 'D-pad down', 'dpleft': 'D-pad left', 'dpright': 'D-pad right', 'misc1': 'Share'},
}


GLYPH_STYLES = [('off', ('Original (default)',)), ('auto', ('Match my controller',)), ('xbox', ('Xbox',)),
                ('nintendo', ('Nintendo',))]


def control_changes(values, kind):
    """The buttons whose chosen key or SDL gamepad name differs from the default."""
    column = 1 if kind == 'key' else 2
    return {row[0]: values[row[0]].strip() for row in CONTROLS
            if row[column] and values.get(row[0], '').strip()
            and values[row[0]].strip().casefold() != row[column].casefold()}


def control_values(controls, kind):
    """The saved keys ('key') or gamepad names ('pad') by game button, for the buttons that have
    a box of that kind. 'touchpad', saved before the sides were split, is the left click."""
    chosen = controls.get(kind, {}) if isinstance(controls, dict) else {}
    if not isinstance(chosen, dict):
        return {}
    values = {button: value for button, value in chosen.items() if button != 'touchpad'}
    if 'touchpad' in chosen and 'touchpad_left' not in values:
        values['touchpad_left'] = chosen['touchpad']
    return {button: values[button] for button, key, pad in CONTROLS
            if (key if kind == 'key' else pad) and isinstance(values.get(button), str) and values[button].strip()}


def control_map(controls, kind):
    """ISSN_KEY_MAP/ISSN_PAD_MAP value: 'cross=Q,circle=Left Shift' for the changed buttons."""
    chosen = control_values(controls, kind)
    return ','.join(f'{button}={chosen[button].strip()}' for button, _key, _pad in CONTROLS
                    if button in chosen and not set(chosen[button]) & set(',='))

UPSCALERS = [('fsr3', ('FSR 3.1 (every GPU)', 'FSR 3.1 (любая видеокарта)')),
             ('taa', ('TAA (native resolution anti-aliasing)', 'TAA (нативное сглаживание)')),
             ('off', ('Off', 'Выключен'))]
PRESETS = [('0', ('Native AA (×1.0)',)), ('1', ('Quality (×1.5)',)), ('2', ('Balanced (×1.7)',)),
           ('3', ('Performance (×2)',)), ('4', ('Ultra Performance (×3)',))]
OUTPUTS = [('1280x720', ('1280 × 720 (Steam Deck)',)), ('1920x1080', ('1920 × 1080',)),
           ('2560x1440', ('2560 × 1440',)), ('3840x2160', ('3840 × 2160 (4K)',))]
LIVE = [('auto', ('Auto (by graphics card)', 'Авто (по видеокарте)')), ('0', ('Off (faster)', 'Выключена (быстрее)')),
        ('1', ('On (change without restarting)', 'Включена (без перезапуска)'))]
# PS4 system language ids (sceSystemServiceParamGetInt). '' follows the launcher's language.
# The US release (CUSA00223) has the Brazilian Portuguese dub and text; a language the dump does
# not have falls back to the game's own default.
LANGUAGES = [('', ('Automatic (launcher language)', 'Автоматически (язык лаунчера)')),
             ('17', ('Portuguese (Brazil)', 'Португальский (Бразилия)')),
             ('7', ('Portuguese (Portugal)', 'Португальский (Португалия)')),
             ('1', ('English', 'Английский')), ('18', ('English (UK)', 'Английский (Великобритания)')),
             ('3', ('Spanish', 'Испанский')), ('20', ('Spanish (Latin America)', 'Испанский (Латинская Америка)')),
             ('2', ('French', 'Французский')), ('4', ('German', 'Немецкий')),
             ('5', ('Italian', 'Итальянский')), ('6', ('Dutch', 'Нидерландский')),
             ('16', ('Polish', 'Польский')), ('8', ('Russian', 'Русский')), ('19', ('Turkish', 'Турецкий')),
             ('21', ('Arabic', 'Арабский')), ('0', ('Japanese', 'Японский')), ('9', ('Korean', 'Корейский')),
             ('11', ('Chinese (Simplified)', 'Китайский (упрощённый)')),
             ('10', ('Chinese (Traditional)', 'Китайский (традиционный)'))]
# issn: touchpad and motion emulation for pads without them (runtime_pad.c, ISSN_PAD_EMULATION).
PAD_EMULATION = [('', ('Automatic (on for pads without a touchpad)',)), ('1', ('On',)), ('0', ('Off',))]
# Launcher language -> PS4 language id for the Automatic game language.
GAME_LANGUAGE_OF_UI = {'pt': '17', 'en': '1', 'es': '3', 'fr': '2', 'de': '4', 'it': '5', 'pl': '16',
                       'ru': '8', 'tr': '19', 'ar': '21', 'ja': '0', 'ko': '9', 'zh': '11'}


def game_language(s):
    """The PS4 language id for the game: the Game language choice, else the launcher's."""
    chosen = str(s.get('game_language', ''))
    if chosen in {value for value, _text in LANGUAGES if value}:
        return chosen
    ui = s.get('ui_language') or (windows_language() if WINDOWS else 'en')
    return GAME_LANGUAGE_OF_UI.get(ui, '1')
DRAW_PIPE = [('', ('Auto (8+ threads)', 'Авто (8+ потоков)')), ('1', ('On', 'Включён')),
             ('0', ('Off (more stable)', 'Выключен (стабильнее)'))]
# inFAMOUS: the game profile reads GPU results back precisely (smoke, sparks, neon need them).
READBACKS = [('', ('Full effects (default)',)), ('1', ('Light: faster, some effects missing',)),
             ('0', ('Off: fastest, most effects missing',))]
# ISSN_LOD_SCALE: how far detail is drawn in the city ('' = the game profile's 1.0, the game's own).
DRAW_DISTANCE = [('', ('Original (default)',)), ('0.6', ('Short (faster)',)), ('1.5', ('Long (less pop-in)',)),
                 ('2.0', ('Very long (least pop-in, slower)',))]
# ISSN_PERF_MODE (axylpup's 1.1 performance mode, gpu/shim/issn_view.cpp): the game's own lower
# settings for the open city: shorter LOD, one shadow cascade less, lower-resolution particles,
# fewer high-resolution textures; Maximum also halves the pedestrians.
PERF_MODES = [('', ('Off: full quality (default)',)), ('low', ('Balanced: about 13% more FPS in the city',)),
              ('office', ('Maximum: about 19% more FPS, fewer pedestrians',))]
# ISSN_RENDER_SCALE (gpu/shim/issn_game_patches.cpp, axylpup's 1.1 render size): the lines the
# game renders, in percent of 1080. With FSR 4 the picture is rebuilt at the window size by the
# preset for that ratio (67% = Quality, 58% = Balanced, 50% = Performance).
RENDER_SCALES = [('', ('100%: 1920x1080, the game\'s own (default)',)), ('83', ('83%: 1592x896',)),
                 ('67', ('67%: 1280x720 (FSR 4 Quality)',)), ('58', ('58%: 1112x624 (FSR 4 Balanced)',)),
                 ('50', ('50%: 968x544 (FSR 4 Performance)',))]
# ISSN_ASPECT (gpu/shim/issn_game_patches.cpp, axylpup's 1.1 screen shapes): '' = the game window's
# shape (fullscreen: the monitor's), off = the game's 16:9, or a fixed shape.
SCREEN_SHAPES = [('', ('Automatic: the shape of the window / monitor (default)',)), ('off', ('16:9, the game\'s own',)),
                 ('21:9', ('21:9 ultrawide',)), ('32:9', ('32:9 super ultrawide',)), ('16:10', ('16:10',))]
# SHADPS4_ASYNC_PIPELINES (the game profile's default 1): a new graphics shader compiles on
# background threads and what it draws is skipped until it is ready, instead of the frame waiting.
SHADER_COMPILE = [('', ('In the background: no stutter, new things can appear a moment later (default)',)),
                  ('frame', ('In the frame: the original, the game stutters while new shaders compile',))]
# issn-axy: what replaces the game's own temporal anti-aliasing (SMAA T2x). FSR 4 ships with the
# port (third_party/fsr4); DLSS needs NVIDIA's Streamline files in dlss_folder().
ANTI_ALIASING = [('game', ("The game's own (SMAA T2x)",)), ('fsr4', ('FSR 4 Native AA (AMD Radeon RX 7000 / 9000)',)),
                 ('dlss', ('DLSS (NVIDIA RTX)',))]
# NVIDIA's Streamline (DLSS) is Windows only: on Linux the choice is not offered.
if not WINDOWS:
    ANTI_ALIASING = [option for option in ANTI_ALIASING if option[0] != 'dlss']
DLSS_FILES = ('sl.interposer.dll', 'sl.common.dll', 'sl.dlss.dll', 'sl.pcl.dll', 'nvngx_dlss.dll')
STREAMLINE_PAGE = 'https://github.com/NVIDIA-RTX/Streamline/releases/tag/v2.14.16'
# ISSN_NO_MSAA: '' leaves the game's own default, off on AMD's and Intel's Windows drivers
# (video_core/amdgpu/issn_msaa_default.h); no_msaa_options names the chosen card in Automatic.
NO_MSAA = [('', ('Automatic',)), ('1', ('Off (fixes black lines)',)), ('0', ('On',))]
NO_MSAA_HINT = ("The game's 2-sample depth buffer, read as a texture by its lighting. AMD's and Intel's Windows "
                'drivers read it wrong and the lighting shows black lines, so Automatic turns multisampling off '
                'on those cards (Radeon, Arc) and keeps it on elsewhere (NVIDIA). Off and On force the choice on '
                'any card.')
STREAM_MEMORY = [('cached', ('System RAM (cached, default)',)),
                 ('host', ('System RAM (uncached)',)),
                 ('device', ('GPU memory (previous behavior)',))]
FRAMES_QUEUED = [('1', ('1 (lowest latency)',)), ('2', ('2',)), ('0', ('Unlimited',))]
UI_LANGUAGES = issn_lang.LANGUAGE_NAMES

def dlss_folder():
    """Where the GPU library looks for NVIDIA's DLSS files: dlss beside issn-probe.exe."""
    for exe_dir in (PORT_DIR / 'bin', PORT_DIR / 'out'):
        if (exe_dir / ('issn-probe.exe' if WINDOWS else 'issn-probe')).is_file():
            return exe_dir / 'dlss'
    return PORT_DIR / 'bin' / 'dlss'


def dlss_missing():
    """NVIDIA's files not yet in dlss_folder()."""
    folder = dlss_folder()
    return [name for name in DLSS_FILES if not (folder / name).is_file()]


def fsr4_folder():
    """Next to the port (the packages ship it, the AppImage too); in the data directory when the
    port is read-only and has none."""
    shipped = PORT_DIR / 'fsr4_shaders'
    if WINDOWS or os.access(PORT_DIR, os.W_OK) or (shipped / 'rcas.spv').is_file():
        return shipped
    return DATA_DIR / 'fsr4_shaders'


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def ini_path():
    return Path(os.environ.get('ISSN_CONFIG', DATA_DIR / 'issn.ini'))


def load_ini():
    values, lines = dict(INI_DEFAULTS), []
    try:
        lines = ini_path().read_text(encoding='utf-8').splitlines()
    except OSError:
        pass
    for line in lines:
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values, lines


def atomic_write(path, text):
    """Replace a complete file from its own directory, preserving the old file on write failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_ini(values, lines):
    """Rewrites the edited keys in place, appends missing ones, keeps comments and other keys."""
    written, out = set(), []
    for line in lines:
        if '=' in line and not line.lstrip().startswith('#'):
            key = line.split('=', 1)[0].strip()
            if key in values:
                out.append(f'{key}={values[key]}')
                written.add(key)
                continue
        out.append(line)
    if not lines:
        out.append('# issn settings (in-game menu: Insert / R3+L2)')
    out += [f'{key}={value}' for key, value in values.items() if key not in written]
    atomic_write(ini_path(), '\n'.join(out) + '\n')


def game_info(folder):
    """(title, app version) of a game folder, or None without eboot.bin."""
    folder = Path(folder or '.')
    if not (folder / 'eboot.bin').is_file():
        return None
    values = game_sfo(folder)
    return values.get('TITLE', APP_NAME).replace('™', '').replace('®', '').strip(), values.get('APP_VER', '?')


def meta_folder(game_dir, update_dir):
    """The folder the title, version and patches are read from: the update when there is one."""
    if update_dir and (Path(update_dir) / 'eboot.bin').is_file():
        return update_dir
    return game_dir


VIDEO_BACKENDS = [('auto', ('Automatic',)), ('wayland', ('Wayland',)), ('x11', ('X11 (XWayland)',))]
VULKAN_LAYER_DIRS = ('/usr/share/vulkan/implicit_layer.d', '/etc/vulkan/implicit_layer.d',
                     os.path.expanduser('~/.local/share/vulkan/implicit_layer.d'))


def linux_tools():
    """Which of the optional Linux integrations this system has."""
    return {'wayland': bool(os.environ.get('WAYLAND_DISPLAY')),
            'gamemode': bool(shutil.which('gamemoderun')),
            'mangohud': bool(shutil.which('mangohud')) or any(
                glob.glob(os.path.join(d, '*angoHud*.json')) for d in VULKAN_LAYER_DIRS)}


def file_state(path):
    """'ok', 'missing', 'folder', 'empty' or 'unreadable' for a file the port needs, from one
    stat: an empty one (a copy that failed, a placeholder made by hand) is as good as missing."""
    try:
        status = Path(path).stat()
    except (FileNotFoundError, NotADirectoryError):
        return 'missing'
    except OSError:
        return 'unreadable'
    if stat.S_ISDIR(status.st_mode):
        return 'folder'
    if not stat.S_ISREG(status.st_mode):
        return 'missing'
    if status.st_size == 0:
        return 'empty'
    try:
        with Path(path).open('rb') as stream:
            stream.read(1)
    except OSError:
        return 'unreadable'
    return 'ok'


def usable(path):
    return file_state(path) == 'ok'


def sfo_state(path):
    """file_state for a param.sfo, which must also parse: 'invalid' when it does not."""
    state = file_state(path)
    if state != 'ok':
        return state
    try:
        load_sfo(path)
    except OSError:
        return 'unreadable'
    except ValueError:
        return 'invalid'
    return 'ok'


# The files the port loads from a dump, each with the check it must pass.
REQUIRED_GAME_FILES = (('eboot.bin', file_state), ('sce_sys/param.sfo', sfo_state),
                       ('sce_module/libc.prx', file_state), ('sce_module/libSceFios2.prx', file_state))
REQUIRED_UPDATE_FILES = REQUIRED_GAME_FILES[:2]


def missing_game_files(folder, requirements=REQUIRED_GAME_FILES):
    """[(name, state)] for the required files that do not pass their check in the folder: an
    incomplete dump, a placeholder made by hand, a file this user may not read, a param.sfo
    that is not one."""
    folder = Path(folder or '.')
    return [(name, state) for name, check in requirements if (state := check(folder / name)) != 'ok']


def describe_missing(missing):
    """The entries of missing_game_files as shown to the player."""
    labels = {'empty': _('{} (empty)'), 'unreadable': _('{} (unreadable)'),
              'invalid': _('{} (not a param.sfo)'), 'folder': _('{} (a folder, not a file)')}
    return [labels[state].format(name) if state in labels else name for name, state in missing]


def ready_to_play(game_dir, update_dir=''):
    """Whether --play (Steam, "Play inFAMOUS Second Son.exe") may start the game at once; anything
    the Play button would refuse opens the launcher window instead."""
    if update_dir and same_folder(update_dir, game_dir or '.'):
        return False
    return not (missing_game_files(game_dir)
                or (update_dir and missing_game_files(update_dir, REQUIRED_UPDATE_FILES)))


def same_folder(a, b):
    """Whether two paths name one folder (links and 8.3 spellings included)."""
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def system_files_beside(folder, reference=None, base=None):
    """PKG extractors write the system files (param.sfo, icon0.png, pic1.png, trophy/) to Sc0/
    beside the game files (Image0/), while the port reads them from the folder's sce_sys/: the
    Sc0 folder beside `folder` holding a God of War III param.sfo (patches.game_profile), while
    `folder` has none worth keeping (an unreadable one is a different problem). For an update
    folder the title must be that of `reference` (the base game's param.sfo values); an Sc0
    beside the base game itself (`base`) is the base game's own unless the base game's param.sfo
    says another version."""
    folder = Path(os.path.abspath(folder or '.'))
    if sfo_state(folder / 'sce_sys/param.sfo') in ('ok', 'unreadable') or folder.parent == folder:
        return None
    try:
        with os.scandir(folder.parent) as entries:  # a linked Sc0 (mklink /J) counts too
            names = [e.name for e in entries if e.is_dir() and e.name.casefold() == 'sc0']
    except OSError:
        return None
    if len(names) != 1 or os.path.normcase(names[0]) == os.path.normcase(folder.name):
        return None
    sibling = folder.parent / names[0]
    try:
        values = load_sfo(sibling / 'param.sfo')
    except (OSError, ValueError):
        return None
    title = values.get('TITLE')
    if not game_profile(values.get('TITLE_ID'), title if isinstance(title, str) else None):
        return None
    if reference is not None and values.get('TITLE_ID') != reference.get('TITLE_ID'):
        return None
    if base is not None and same_folder(folder.parent, Path(os.path.abspath(base)).parent) and (
            reference is None or values.get('APP_VER') == reference.get('APP_VER')):
        return None
    return sibling


@contextlib.contextmanager
def replacing(destination):
    """A .part path to write `destination` through: the file takes its place whole on success
    and is removed on failure, so a write cut short never passes for the file."""
    part = destination.with_name(destination.name + '.part')
    try:
        yield part
        os.replace(part, destination)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def adopt_system_files(source, game):
    """Copies the files of `source` (an Sc0/ folder) into <game>/sce_sys, keeping every usable
    file the game already has there (empty ones, and a param.sfo that is not one, are replaced);
    returns the relative names copied. Each file lands whole or not at all, and param.sfo goes
    last: while it is missing, a copy that failed halfway is offered again."""
    source, target = Path(source), Path(game) / 'sce_sys'

    def unreadable(error):  # os.walk would skip the folder silently
        raise error
    files = sorted(((Path(directory) / name).relative_to(source)
                    for directory, _folders, names in os.walk(source, onerror=unreadable) for name in names),
                   key=lambda relative: (relative.as_posix() == 'param.sfo', relative.as_posix()))
    copied = []
    for relative in files:
        destination = target / relative
        if destination.is_dir():
            raise OSError(f'{destination} is a folder: {source / relative} cannot replace it')
        check = sfo_state if relative.as_posix() == 'param.sfo' else file_state
        if check(destination) == 'ok':
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with replacing(destination) as part:
            shutil.copy2(source / relative, part)
        copied.append(relative.as_posix())
    return copied


DESKTOP_MARKER = 'X-ISSN-Integration=true'


def desktop_entry(command, icon, marker=False):
    """The menu entry; its StartupWMClass is the application id of the launcher and of the game window."""
    return ('[Desktop Entry]\nType=Application\nName=inFAMOUS Second Son\n'
            f'Exec={command}\nIcon={icon}\nTerminal=false\nCategories=Game;\nStartupWMClass=issn\n'
            + (DESKTOP_MARKER + '\n' if marker else ''))


def integrate_desktop(enabled, appimage, icon_source, data_dir, home=None):
    """AppImage on Linux: the menu entry the desktop matches the windows (application id issn) to,
    which is what shows their icon in the dock. The entry is ours while it carries the marker; one
    without it was written by hand (the Desktop shortcut button) and is left alone."""
    entry = Path(home or Path.home()) / '.local' / 'share' / 'applications' / 'issn.desktop'
    try:
        ours = entry.is_file() and DESKTOP_MARKER in entry.read_text(encoding='utf-8', errors='replace')
        if not enabled:
            if ours:
                entry.unlink()
            return None
        if not appimage or (entry.is_file() and not ours):
            return None
        icon = Path(data_dir) / 'issn.png'
        icon.parent.mkdir(parents=True, exist_ok=True)
        if not icon.is_file() or icon.read_bytes() != Path(icon_source).read_bytes():
            shutil.copyfile(icon_source, icon)
        text = desktop_entry(f'"{appimage}"', icon, marker=True)
        entry.parent.mkdir(parents=True, exist_ok=True)
        if not entry.is_file() or entry.read_text(encoding='utf-8') != text:
            entry.write_text(text, encoding='utf-8')
        return entry
    except OSError:
        return None


def steam_icon(game):
    """Picture of the Steam shortcut's icon: the launcher's own on Linux, the game's on Windows."""
    return Path(game) / 'sce_sys' / 'icon0.png' if WINDOWS else PORT_DIR / 'launcher' / 'issn.png'


def steam_launch_command():
    """(program, launch options, start folder) with which Steam starts the game without the window."""
    appimage = os.environ.get('APPIMAGE')
    if appimage:
        return appimage, '--play', str(Path(appimage).parent)
    if FROZEN:
        return sys.executable, '--play', str(Path(sys.executable).parent)
    return sys.executable, f'"{Path(__file__).resolve()}" --play', str(PORT_DIR)


def game_profile_of(folder):
    """(title id, (ids, built-in XML, app version)) of a game with a patch profile, else (id, None)."""
    from patches import game_profile_for, game_title_id
    title_id = game_title_id(folder) if folder else None
    return title_id, game_profile_for(folder)


def game_environment(s):
    env = dict(os.environ)
    env['ISSN_GAME_DIR'] = s['game_dir']
    env.pop('ISSN_PATCH_DIR', None)
    # Temporal upscaling is parked (build_graphics): a switch saved on by 2.1.8 has no control left to
    # turn it off, so it is ignored until the upscaler section comes back.
    env['ISSN_EXPERIMENTAL_UPSCALER'] = '0'
    if s.get('update_dir'):
        env['ISSN_PATCH_DIR'] = s['update_dir']
    if not WINDOWS:
        backend = s.get('video_backend', 'auto')
        if backend in ('wayland', 'x11'):
            env['SDL_VIDEO_DRIVER'] = env['SDL_VIDEODRIVER'] = backend
        env['ISSN_GAMEMODE'] = '1' if s.get('gamemode') else '0'
        env['ISSN_MANGOHUD'] = '1' if s.get('mangohud') else '0'
    # The launcher's patch selection is the whole list (run.py, patches.py).
    title_id, profile = game_profile_of(meta_folder(s['game_dir'], s.get('update_dir')))
    chosen = s.get('game_patches', {}).get(title_id) if title_id else None
    if profile and chosen is not None:
        env['ISSN_PATCHES'] = ';'.join(chosen)
        env['ISSN_PATCHES_ONLY'] = '1'
    if s['user_dir']:
        env['ISSN_USER_DIR'] = s['user_dir']
    # issn-axy: the anti-aliasing choice (ANTI_ALIASING); an inherited switch never mixes them.
    for name in ('SHADPS4_DLSS', 'SHADPS4_DLSS_UPSCALE', 'ISSN_FSR4'):
        env.pop(name, None)
    anti_aliasing = s.get('anti_aliasing', 'game')
    if anti_aliasing == 'fsr4':
        env['ISSN_FSR4'] = '1'
    elif anti_aliasing == 'dlss' and WINDOWS:
        env['SHADPS4_DLSS'] = '1'
        env['SHADPS4_DLSS_UPSCALE'] = '1'  # the game's 1080p up to the window (DLAA at 1080p)
    if (fsr4_folder() / 'rcas.spv').is_file():
        env['ISSN_FSR4_DIR'] = str(fsr4_folder())
    env['ISSN_MODS_DIR'] = s['mods_dir'] or str(DATA_DIR / 'mods')
    env['ISSN_MODS_CONFIG'] = str(DATA_DIR / 'mods.json')
    env['ISSN_MODS_ENABLED'] = '1' if s['mods_enabled'] else '0'
    env['ISSN_PATCHES_DIR'] = s['patches_dir'] or str(DATA_DIR / 'patches')
    env['ISSN_PATCHES_CONFIG'] = str(DATA_DIR / 'patches.json')
    # issn: a new key ('language' was saved as English by earlier launchers).
    env['ISSN_LANGUAGE'] = game_language(s)
    if str(s['player_name']).strip():
        env['ISSN_USER_NAME'] = str(s['player_name']).strip()
    if s['hdr']:
        env['ISSN_HDR'] = '1'
    if s.get('readbacks'):
        env['ISSN_READBACKS'] = s['readbacks']
    # The saved UI choice wins over inherited diagnostic overrides, including when reverting.
    stream_memory = s.get('stream_memory', APP_DEFAULTS['stream_memory'])
    env['ISSN_STREAM_MEMORY'] = stream_memory if stream_memory in ('cached', 'host', 'device') else 'cached'
    gpu_id = str(s.get('gpu_id', ''))
    env.pop('ISSN_GPU_ID', None)
    if gpu_id.isdigit():
        env['ISSN_GPU_ID'] = gpu_id
    # The old core's switches (draw pipe, compute priority, warm-up, pipeline binaries, ...) are
    # gone with it: settings saved by 0.2 keep their keys and nothing reads them.
    if s.get('draw_distance') in {value for value, _text in DRAW_DISTANCE if value}:
        env['ISSN_LOD_SCALE'] = str(s['draw_distance'])
    env.pop('ISSN_PERF_MODE', None)
    for name in ('ISSN_RENDER_SCALE', 'ISSN_RENDER_HEIGHT'):
        env.pop(name, None)
    if s.get('render_scale') in {value for value, _text in RENDER_SCALES if value}:
        env['ISSN_RENDER_SCALE'] = s['render_scale']
    if s.get('perf_mode') in {value for value, _text in PERF_MODES if value}:
        env['ISSN_PERF_MODE'] = s['perf_mode']
    env.pop('ISSN_ASPECT', None)
    if s.get('screen_shape') in {value for value, _text in SCREEN_SHAPES if value}:
        env['ISSN_ASPECT'] = s['screen_shape']
    env.pop('SHADPS4_ASYNC_PIPELINES', None)
    if s.get('shader_compile') == 'frame':
        env['SHADPS4_ASYNC_PIPELINES'] = '0'
    for key, name in (('frame_stats', 'ISSN_FRAME_STATS'), ('vk_validation', 'ISSN_VK_VALIDATION')):
        if s[key]:
            env[name] = '1'
    env.pop('ISSN_PAD_EMULATION', None)
    if s.get('pad_emulation') in ('0', '1'):
        env['ISSN_PAD_EMULATION'] = s['pad_emulation']
    for kind, name in (('key', 'ISSN_KEY_MAP'), ('pad', 'ISSN_PAD_MAP')):
        if value := control_map(s.get('controls', {}), kind):
            env[name] = value
    glyph_style = s.get('glyph_style')
    env.pop('ISSN_GLYPH_STYLE', None)
    if glyph_style in ('auto', 'xbox', 'nintendo'):
        env['ISSN_GLYPH_STYLE'] = glyph_style
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


# ---------------------------------------------------------------------------------------------
# The window.

# inFAMOUS Second Son: Seattle's wet concrete at night, the red of Delsin's smoke and the pink /
# cyan of neon. Titles in Bebas Neue; the art of the player's own dump fills the home page.
BG, PANEL, CARD, LINE = '#09090c', '#101116', '#181a21', '#262935'
TEXT, MUTED, ACCENT, ACCENT_HI = '#f2f2f4', '#8c90a0', '#e2263b', '#ff4359'
NEON, HOVER = '#24d3f2', '#15171e'
NEON_PINK = '#ff3ea5'
RESOLUTION_CHOICES = [('native', ('Native (1080p)',)), ('480p', ('480p',)), ('720p', ('720p',)),
                      ('1440p', ('1440p',)), ('1800p', ('1800p',)), ('4K', ('4K',))]
ENGINE_FPS_CHOICES = [('60', ('60 (original)',)), ('120', ('120',)), ('240', ('240',))]


def window_icon(root, tk, game_icon, own_icon, ico_file):
    """The launcher window's icon: the game's (icon0.png) when the game is set, else the port's own.
    Windows gets an .ico made with Pillow (ico_file); without Pillow, Tk reads the PNG itself.
    Returns the PhotoImage the caller must keep alive, or None.
    Tk 9 applies the -default forms only to toplevels created later (the window itself kept Tk's
    feather in the title bar and the taskbar), so the window gets the icon by itself first."""
    if WINDOWS:
        try:
            from PIL import Image
            ico_file.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(game_icon) as image:
                image.convert('RGBA').save(ico_file, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (256, 256)])
            root.iconbitmap(str(ico_file))
            root.iconbitmap(default=str(ico_file))
            return None
        except (ImportError, OSError, tk.TclError):
            pass
    for icon in ((game_icon, own_icon) if WINDOWS else (own_icon, game_icon)):
        try:
            photo = tk.PhotoImage(file=str(icon))
            if photo.width() > 128:
                photo = photo.subsample(photo.width() // 64)
            root.iconphoto(False, photo)
            root.iconphoto(True, photo)
            return photo
        except (tk.TclError, OSError):
            continue
    return None


def hero_image(art, width, height):
    """The home page's background: the dump's art scaled to cover the page, darkened towards the
    title in the lower left; without a dump, smoke and neon streaks on black. None without Pillow."""
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageTk
    except ImportError:
        return None
    width, height = max(width, 1), max(height, 1)
    hex_rgb = lambda c: tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
    if art is not None:
        try:
            with Image.open(art) as source:
                image = source.convert('RGB')
        except OSError:
            image = None
    else:
        image = None
    if image is not None:
        scale = max(width / image.width, height / image.height)
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)
        left, top = (image.width - width) // 2, max(0, round((image.height - height) * 0.35))
        image = image.crop((left, top, left + width, top + height)).convert('RGBA')
    else:
        image = Image.new('RGBA', (width, height), hex_rgb(BG) + (255,))
        streaks = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(streaks)
        for color, x, thickness, alpha in ((ACCENT, 0.58, 0.10, 150), (NEON_PINK, 0.74, 0.03, 170),
                                           (NEON, 0.83, 0.02, 190), (ACCENT, 0.95, 0.05, 90)):
            x0, slant, span = width * x, height * 0.55, width * thickness
            draw.polygon([(x0, 0), (x0 + span, 0), (x0 + span - slant, height), (x0 - slant, height)],
                         fill=hex_rgb(color) + (alpha,))
        image.alpha_composite(streaks.filter(ImageFilter.GaussianBlur(max(6, width // 60))))
    # Shade: black from the left edge (where the title sits) and from the bottom.
    shade = Image.new('L', (width, height))
    pixels = []
    for y in range(height):
        fy = max(0.0, (y / height - 0.35) / 0.65)
        pixels.extend(min(255, int(255 * max(0.0, 0.92 - x / width * 1.15) + 210 * fy * fy))
                      for x in range(0, width, 8))
    small = Image.new('L', ((width + 7) // 8, height))
    small.putdata(pixels)
    shade = small.resize((width, height), Image.BILINEAR)
    image = Image.composite(Image.new('RGBA', (width, height), hex_rgb(BG) + (255,)), image, shade)
    return ImageTk.PhotoImage(image.convert('RGB'))


class PlayButton:
    """PLAY: a slanted bar drawn on a canvas, like the game's own menus. configure(text=,
    state=) and pack() as a ttk button."""

    def __init__(self, launcher, parent, command, width, height):
        self.canvas = launcher.tk.Canvas(parent, width=width, height=height, bg=BG, highlightthickness=0, bd=0,
                                         cursor='hand2')
        self.command, self.text, self.state, self.hover = command, '', 'normal', False
        self.width, self.height = width, height
        self.canvas.bind('<Button-1>', lambda _e: self.invoke())
        self.canvas.bind('<Enter>', lambda _e: self.set_hover(True))
        self.canvas.bind('<Leave>', lambda _e: self.set_hover(False))
        self.draw()

    def set_hover(self, value):
        self.hover = value
        self.draw()

    def configure(self, text=None, state=None):
        if text is not None:
            self.text = text
        if state is not None:
            self.state = str(state)
        self.draw()

    def invoke(self):
        if self.state == 'normal':
            self.command()

    def __getitem__(self, key):  # button['text'] / button['state'], as on a ttk button
        return getattr(self, key)

    def pack(self, **options):
        self.canvas.pack(**options)

    def draw(self):
        c, w, h = self.canvas, self.width, self.height
        c.delete('all')
        slant = h * 0.38
        enabled = self.state == 'normal'
        fill = (ACCENT_HI if self.hover else ACCENT) if enabled else '#22242c'
        c.create_polygon(slant, 0, w, 0, w - slant, h, 0, h, fill=fill, outline='')
        c.create_line(slant + 1, 0, 1, h, fill=NEON if enabled else LINE, width=3)
        c.create_text(w / 2, h / 2 + 1, text=self.text, fill=TEXT if enabled else '#6c7080',
                      font=heading_font(26, UI_FONT, every_language=True))


class Launcher:
    def __init__(self, root, tk, ttk, filedialog, messagebox):
        self.tk, self.ttk, self.filedialog, self.messagebox = tk, ttk, filedialog, messagebox
        self.root = root
        self.app = {**APP_DEFAULTS, **load_json(CONFIG_FILE, {})}
        self.ini, self.ini_lines = load_ini()
        self.vars = {}
        self.process = self.job = None
        self.output = queue.Queue()
        self.banner_key = self.banner_photo = None
        self.ui_calls = queue.Queue()  # work for the Tk thread from helper threads
        self.mod_order, self.mod_vars, self.patch_vars = [], {}, {}
        self.pending_update = None  # (version, url, page) once offered
        root.title(f'{APP_NAME} (PC)')
        if sys.platform == 'win32':
            try:  # the taskbar shows this window's icon, not Python's
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('issnpc.launcher')
            except (AttributeError, OSError):
                pass
        root.configure(bg=BG)
        self.dpi = root.winfo_fpixels('1i') / 96.0
        left, top, width, height = (0, 0, 0, 0) if WINDOWS else monitor_under_pointer(root)
        if width:
            # Linux reports 96 dpi everywhere: size the interface by the monitor it opens on.
            boost = max(1.0, min(width / 1920, height / 1080, 2.0)) * 1.25
            self.dpi *= boost
            root.tk.call('tk', 'scaling', root.winfo_fpixels('1i') / 72.0 * boost)
        window_width, window_height = self.px(1120), self.px(740)
        if width:
            window_width, window_height = min(window_width, int(width * 0.9)), min(window_height, int(height * 0.9))
            root.geometry(f'{window_width}x{window_height}+{left + (width - window_width) // 2}'
                          f'+{top + (height - window_height) // 2}')
        else:
            root.geometry(f'{window_width}x{window_height}')
        root.minsize(min(self.px(980), window_width), min(self.px(660), window_height))
        self.set_icon()
        self.style()
        self.build()
        self.show('home')
        self.refresh_lists()  # the Game page's lists now, so the first visit does not count as a change
        self.refresh_game_patches()
        self.saved = self.gather()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(100, self.drain_output)
        root.after(500, self.watch_changes)
        if WINDOWS and UPDATES and self.app.get('check_updates', True):
            threading.Thread(target=self.check_update, daemon=True).start()

    def meta_dir(self):
        return meta_folder(self.var('game_dir', 'app').get(), self.var('update_dir', 'app').get())

    def px(self, size):
        return int(size * self.dpi)

    def set_icon(self):
        game_icon = Path(self.meta_dir() or '.') / 'sce_sys' / 'icon0.png'
        self.icon = window_icon(self.root, self.tk, game_icon, PORT_DIR / 'launcher' / 'issn.png',
                                CONFIG_DIR / 'game_icon.ico')

    # ---- look --------------------------------------------------------------------------------
    def check_images(self):
        """16 px (DPI-scaled) check box images: the clam theme draws a cross."""
        tk = self.tk
        n = max(14, int(16 * self.dpi))
        images = []
        for checked in (False, True):
            image = tk.PhotoImage(width=n + self.px(8), height=n)  # unset pixels stay transparent
            fill = ACCENT if checked else CARD
            image.put(ACCENT_HI if checked else '#3a4250', to=(0, 0, n, n))
            image.put(fill, to=(1, 1, n - 1, n - 1))
            if checked:
                # Tick: down from (0.22n, 0.52n) to (0.42n, 0.72n), up to (0.78n, 0.30n).
                pts = []
                x0, y0, x1, y1, x2, y2 = .22 * n, .52 * n, .42 * n, .72 * n, .78 * n, .28 * n
                for t in range(40):
                    f = t / 39
                    pts.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
                    pts.append((x1 + (x2 - x1) * f, y1 + (y2 - y1) * f))
                width = max(1, round(n / 9))
                for x, y in pts:
                    image.put('#f4ece0', to=(int(x), int(y), int(x) + width, int(y) + width))
            images.append(image)
        self.check_off, self.check_on = images

    def style(self):
        ttk = self.ttk
        s = ttk.Style(self.root)
        s.theme_use('clam')
        if not hasattr(self, 'check_on'):  # once: a rebuilt window keeps the element and its images
            self.check_images()
            s.element_create('Bb.indicator', 'image', self.check_off, ('selected', self.check_on), sticky='')
        s.layout('TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
            ('Bb.indicator', {'side': 'left', 'sticky': ''}),
            ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
                ('Checkbutton.label', {'sticky': 'nswe'})]})]})])
        base = (UI_FONT, 10)
        self.root.option_add('*TCombobox*Listbox.background', CARD)
        self.root.option_add('*TCombobox*Listbox.foreground', TEXT)
        self.root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
        self.root.option_add('*TCombobox*Listbox.font', base)
        s.configure('.', background=PANEL, foreground=TEXT, fieldbackground=CARD, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, troughcolor=CARD, focuscolor=ACCENT, font=base)
        s.configure('TFrame', background=PANEL)
        s.configure('TLabel', background=PANEL, foreground=TEXT)
        s.configure('Muted.TLabel', background=PANEL, foreground=MUTED, font=(UI_FONT, 9))
        s.configure('Restart.TLabel', background=PANEL, foreground=ACCENT_HI, font=(UI_FONT, 8, 'bold'))
        s.configure('Section.TLabel', background=PANEL, foreground=TEXT, font=heading_font(19, UI_FONT))
        s.configure('Slash.TLabel', background=PANEL, foreground=ACCENT, font=heading_font(19, UI_FONT))
        s.configure('Tile.TLabel', background=PANEL, foreground=MUTED, font=heading_font(14, UI_FONT))
        s.configure('TCheckbutton', background=PANEL, foreground=TEXT, padding=(0, 3))
        s.map('TCheckbutton', background=[('active', PANEL)], foreground=[('disabled', MUTED)])
        s.configure('TRadiobutton', background=PANEL, foreground=TEXT, indicatorcolor=CARD)
        s.map('TRadiobutton', background=[('active', PANEL)], indicatorcolor=[('selected', ACCENT)])
        s.configure('Warning.TLabel', background='#2a1414', foreground='#e0a0a0', padding=(10, 6))
        s.configure('TCombobox', arrowcolor=MUTED, foreground=TEXT, padding=4)
        s.map('TCombobox', fieldbackground=[('readonly', CARD)], foreground=[('readonly', TEXT)],
              selectbackground=[('readonly', CARD)], selectforeground=[('readonly', TEXT)])
        s.configure('TEntry', foreground=TEXT, insertcolor=TEXT, padding=4)
        s.configure('TSpinbox', foreground=TEXT, arrowcolor=MUTED, insertcolor=TEXT, padding=4)
        s.configure('TButton', background=CARD, foreground=TEXT, padding=(12, 6), borderwidth=1)
        s.map('TButton', background=[('active', LINE), ('disabled', PANEL)], foreground=[('disabled', MUTED)])
        s.configure('Play.TButton', background=ACCENT, foreground='#f4ece0', font=(*heading_font(15), 'bold'),
                    padding=(36, 10), borderwidth=0)
        s.map('Play.TButton', background=[('active', ACCENT_HI), ('disabled', '#1c222e')],
              foreground=[('disabled', '#8a7a78')])
        s.configure('Horizontal.TProgressbar', background=ACCENT, troughcolor=CARD, bordercolor=LINE)
        s.configure('Vertical.TScrollbar', background=CARD, arrowcolor=MUTED, troughcolor=PANEL, bordercolor=PANEL)

    # ---- widget helpers ----------------------------------------------------------------------
    def var(self, key, store):
        """The Tk variable of a setting (one per setting, shared by every widget that shows it)."""
        tk = self.tk
        if key not in self.vars:
            if store == 'ini':
                value = self.ini.get(key, INI_DEFAULTS[key])
                if key in INI_FLAGS:
                    v = tk.BooleanVar(value=value == '1')
                else:
                    v = tk.StringVar(value=value)
            else:
                value = self.app.get(key, APP_DEFAULTS[key])
                if isinstance(APP_DEFAULTS[key], bool):
                    v = tk.BooleanVar(value=bool(value))
                else:
                    v = tk.StringVar(value=str(value))
            v.store = store
            self.vars[key] = v
        return self.vars[key]

    def choice(self, parent, key, store, options, width=30, var=None):
        """A combobox over (value, (english, russian)) pairs, kept in sync with its variable."""
        var = var if var is not None else self.var(key, store)
        values = [v for v, _t in options]
        box = self.ttk.Combobox(parent, values=[_(*text) for _v, text in options], state='readonly', width=width)
        if var.get() not in values:
            var.set(values[0])

        def show(*_args):
            current = var.get()
            box.current(values.index(current) if current in values else 0)
        show()
        box.bind('<<ComboboxSelected>>', lambda _e: var.set(values[box.current()]))
        var.trace_add('write', show)
        return box

    def next_row(self, parent):
        return parent.grid_size()[1]

    def title_cell(self, parent, title, restart=False):
        """The name of a row; `restart` adds a mark: changing it needs a new start of the game."""
        cell = self.ttk.Frame(parent)
        self.ttk.Label(cell, text=title).pack(side='left')
        if restart:
            self.ttk.Label(cell, text=_('restart'), style='Restart.TLabel').pack(side='left', padx=(8, 0))
        return cell

    def row(self, parent, title, widget, hint=None, restart=False):
        """Name on the left, control on the right, an optional note below."""
        r = self.next_row(parent)
        self.title_cell(parent, title, restart).grid(row=r, column=0, sticky='w', padx=(0, 18), pady=(8, 0))
        widget.grid(row=r, column=1, sticky='w', pady=(5, 0))
        if hint:
            self.ttk.Label(parent, text=hint, style='Muted.TLabel', wraplength=self.px(640), justify='left').grid(
                row=r + 1, column=0, columnspan=2, sticky='w', pady=(1, 2))
        return widget

    def check(self, parent, key, store, title, hint=None, var=None, restart=False):
        box = self.ttk.Checkbutton(parent, variable=var if var is not None else self.var(key, store))
        return self.row(parent, title, box, hint, restart)

    def note(self, parent, text, top=12):
        self.ttk.Label(parent, text=text, style='Muted.TLabel', wraplength=self.px(680), justify='left').grid(
            row=self.next_row(parent), column=0, columnspan=2, sticky='w', pady=(top, 0))

    def section(self, parent, title, top=20):
        """A section heading: a red slash, the title in the display face, a hairline under it."""
        r = self.next_row(parent)
        cell = self.ttk.Frame(parent)
        self.ttk.Label(cell, text='/', style='Slash.TLabel').pack(side='left', padx=(0, 8))
        self.ttk.Label(cell, text=heading_text(title.upper()), style='Section.TLabel').pack(side='left')
        cell.grid(row=r, column=0, columnspan=2, sticky='w', pady=(top, 0))
        self.tk.Frame(parent, bg=LINE, height=1).grid(row=r + 1, column=0, columnspan=2, sticky='we', pady=(2, 2))

    def folder(self, parent, key, title, prompt, hint=None, on_change=None):
        ttk = self.ttk
        var = self.var(key, 'app')
        holder = ttk.Frame(parent)
        ttk.Entry(holder, textvariable=var, width=46).pack(side='left')

        def browse():
            chosen = self.filedialog.askdirectory(title=prompt, initialdir=var.get() or str(PORT_DIR))
            if chosen:
                var.set(str(Path(chosen)))
        ttk.Button(holder, text=_('Browse…', 'Обзор…'), command=browse).pack(side='left', padx=(6, 0))
        ttk.Button(holder, text=_('Open', 'Открыть'), command=lambda: self.open_path(var.get(), key)).pack(
            side='left', padx=(6, 0))
        if on_change:
            var.trace_add('write', lambda *_a: on_change())
        return self.row(parent, title, holder, hint)

    def scrolled_page(self, name, title, subtitle):
        """A settings page: a heading and content that scrolls vertically."""
        tk, ttk = self.tk, self.ttk
        outer = ttk.Frame(self.content)
        head = ttk.Frame(outer, padding=(32, 20, 32, 8))
        head.pack(fill='x')
        ttk.Label(head, text=heading_text(title.upper()), background=PANEL, foreground=TEXT,
                  font=heading_font(34, UI_FONT)).pack(anchor='w')
        ttk.Label(head, text=subtitle, style='Muted.TLabel').pack(anchor='w')
        canvas = tk.Canvas(outer, bg=PANEL, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        inner = ttk.Frame(canvas, padding=(32, 0, 32, 24))
        inner.columnconfigure(1, weight=1)
        window = canvas.create_window(0, 0, window=inner, anchor='nw')
        inner.bind('<Configure>', lambda _e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        outer.scroll = lambda units: canvas.yview_scroll(units, 'units') if inner.winfo_height() > canvas.winfo_height() else None
        self.pages[name] = outer
        return inner

    # ---- layout ------------------------------------------------------------------------------
    def build(self):
        tk, ttk = self.tk, self.ttk
        header = tk.Frame(self.root, bg=BG, height=self.px(86))
        header.pack(fill='x', side='top')
        header.pack_propagate(False)
        brand = tk.Frame(header, bg=BG)
        brand.pack(side='left', padx=(self.px(28), self.px(36)))
        tk.Label(brand, text='INFAMOUS', bg=BG, fg=TEXT, font=heading_font(32, UI_FONT, every_language=True),
                 bd=0, pady=0).pack(anchor='w', pady=(self.px(10), 0))
        tk.Label(brand, text=f'SECOND SON  /  PC  ·  v{VERSION}', bg=BG, fg=ACCENT_HI,
                 font=heading_font(14, UI_FONT, every_language=True), bd=0, pady=0).pack(anchor='w')
        self.nav, self.current_page = {}, None
        titles = {'home': _('Home'), 'graphics': _('Graphics', 'Графика'), 'performance': _('Performance'),
                  'game': _('Game', 'Игра'), 'advanced': _('Advanced', 'Дополнительно')}
        tabs = tk.Frame(header, bg=BG)
        tabs.pack(side='left', fill='y')
        for name in PAGES:
            column = tk.Frame(tabs, bg=BG)
            column.pack(side='left', fill='y', padx=self.px(12))
            label = tk.Label(column, text=heading_text(titles[name].upper()), bg=BG, fg=MUTED,
                             font=heading_font(19, UI_FONT), cursor='hand2', bd=0)
            label.pack(side='top', expand=True)
            underline = tk.Frame(column, bg=BG, height=self.px(3))
            underline.pack(side='bottom', fill='x', pady=(0, self.px(14)))
            for widget in (column, label):
                widget.bind('<Button-1>', lambda _e, n=name: self.show(n))
                widget.bind('<Enter>', lambda _e, n=name: n != self.current_page and self.nav[n][0].configure(fg=NEON))
                widget.bind('<Leave>', lambda _e, n=name: n != self.current_page and self.nav[n][0].configure(fg=MUTED))
            self.nav[name] = (label, underline)
        self.side_note = tk.Label(header, text=_("In the game: Insert or R3+L2\nopens the port's menu."),
                                  bg=BG, fg=MUTED, font=(UI_FONT, 9), justify='right')
        self.side_note.pack(side='right', padx=self.px(26))
        # A two-colour neon line under the header: smoke red into neon cyan.
        line = tk.Frame(self.root, bg=BG, height=2)
        line.pack(fill='x')
        tk.Frame(line, bg=ACCENT, height=2).place(relx=0, rely=0, relwidth=0.62, relheight=1)
        tk.Frame(line, bg=NEON_PINK, height=2).place(relx=0.62, rely=0, relwidth=0.2, relheight=1)
        tk.Frame(line, bg=NEON, height=2).place(relx=0.82, rely=0, relwidth=0.18, relheight=1)
        # Notices (an available update) appear here, under the header.
        self.notice = tk.Frame(self.root, bg=BG)
        self.update_box = None

        right = tk.Frame(self.root, bg=PANEL)
        right.pack(side='top', fill='both', expand=True)
        bar = tk.Frame(right, bg=BG, height=self.px(78))
        bar.pack(fill='x', side='bottom')
        bar.pack_propagate(False)
        self.status = tk.Label(bar, text='', bg=BG, fg=MUTED, font=(UI_FONT, 10), anchor='w', justify='left')
        self.status.pack(side='left', padx=self.px(28))
        self.play_button = PlayButton(self, bar, self.play, self.px(230), self.px(54))
        self.play_button.configure(text=heading_text(_('PLAY', 'ИГРАТЬ')))
        self.play_button.pack(side='right', padx=(self.px(12), self.px(28)), pady=self.px(12))
        self.save_button = ttk.Button(bar, text=_('Save', 'Сохранить'), command=self.save, state='disabled')
        self.save_button.pack(side='right', pady=self.px(12))
        self.content = tk.Frame(right, bg=PANEL)
        self.content.pack(fill='both', expand=True)

        self.pages = {}
        self.build_home()
        self.build_graphics()
        self.build_performance()
        self.build_game()
        self.build_advanced()
        self.root.bind_all('<MouseWheel>', self.wheel)
        if not WINDOWS:  # X11 (Tk 8.6): the wheel arrives as buttons 4 and 5; on Windows those are side buttons
            self.root.bind_all('<Button-4>', self.wheel)
            self.root.bind_all('<Button-5>', self.wheel)
        self.game_changed()

    def wheel(self, event):
        page = self.pages.get(self.current_page)
        if hasattr(page, 'scroll') and not isinstance(event.widget, (self.tk.Text, self.ttk.Combobox)) \
                and 'popdown' not in str(event.widget):
            page.scroll({4: -1, 5: 1}.get(event.num) or int(-event.delta / 120))

    def show(self, name):
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill='both', expand=True)
        self.current_page = name
        for n, (label, underline) in self.nav.items():
            label.configure(fg=TEXT if n == name else MUTED)
            underline.configure(bg=ACCENT if n == name else BG)
        for method in PAGE_REFRESH.get(name, ()):
            getattr(self, method)()

    def build_home(self):
        tk, ttk = self.tk, self.ttk
        page = tk.Frame(self.content, bg=BG)
        self.pages['home'] = page
        quick = tk.Frame(page, bg=PANEL)
        quick.pack(fill='x', side='bottom')
        inner = ttk.Frame(quick, padding=(32, 12, 32, 14))
        inner.pack(fill='x')
        items = ((_('Anti-aliasing'), 'anti_aliasing', 'app', ANTI_ALIASING),
                 (_('Frame rate limit'), 'fps_limit', 'ini', FPS_LIMITS),
                 (_('Draw distance'), 'draw_distance', 'app', DRAW_DISTANCE),
                 (_('Display mode'), 'display_mode', 'ini', DISPLAY_MODES))
        for index, (title, key, store, options) in enumerate(items):
            cell = ttk.Frame(inner)
            cell.grid(row=0, column=index, sticky='w', padx=(0, self.px(26)))
            ttk.Label(cell, text=heading_text(title.upper()), style='Tile.TLabel').pack(anchor='w')
            self.choice(cell, key, store, options, width=24 if index == 0 else 17).pack(anchor='w', pady=(2, 0))
        self.banner_key = self.banner_photo = None
        self.banner = tk.Canvas(page, bg=BG, highlightthickness=0, bd=0)
        self.banner.pack(fill='both', expand=True)
        self.banner.bind('<Configure>', lambda _e: self.draw_banner())
        self.banner_button = ttk.Button(self.banner, text=_('Choose the game folder'),
                                        command=lambda: self.show('game'))

    def draw_banner(self):
        """The home page: the dump's own art (sce_sys/pic1.png) behind the game's name."""
        c = self.banner
        c.delete('all')
        w, h = max(c.winfo_width(), 400), max(c.winfo_height(), 200)
        folder = self.meta_dir()
        info = game_info(folder)
        art = Path(folder or '.') / 'sce_sys' / 'pic1.png'
        key = (str(art), art.is_file(), w, h)
        if self.banner_key != key:
            self.banner_key, self.banner_photo = key, hero_image(art if art.is_file() else None, w, h)
        if self.banner_photo:
            c.create_image(0, 0, image=self.banner_photo, anchor='nw')
        x, base = self.px(40), h - self.px(34)
        title_face = heading_font(min(96, max(54, h // 4)), UI_FONT, every_language=True)
        c.create_text(x, base, text='SECOND SON', anchor='sw', fill=ACCENT_HI,
                      font=heading_font(min(46, max(28, h // 8)), UI_FONT, every_language=True))
        c.create_text(x - 2, base - self.px(min(44, max(28, h // 8)) * 0.95), text='INFAMOUS', anchor='sw',
                      fill=TEXT, font=title_face)
        if info:
            title_id = game_profile_of(folder)[0]
            chip = f'  {title_id or "?"}  ·  ' + _('version {}', 'версия {}').format(info[1]) + '  '
            item = c.create_text(x + self.px(4), self.px(28), text=chip, anchor='nw', fill=TEXT,
                                 font=(UI_FONT, 10, 'bold'))
            x0, y0, x1, y1 = c.bbox(item)
            c.create_rectangle(x0 - 2, y0 - 4, x1 + 2, y1 + 4, fill=ACCENT, outline='')
            c.tag_raise(item)
        else:
            c.create_text(x + self.px(4), self.px(34), text=_('Choose your game folder to play.'), anchor='nw',
                          fill=TEXT, font=(UI_FONT, 12))
            c.create_window(x + self.px(4), self.px(66), window=self.banner_button, anchor='nw')

    def build_graphics(self):
        """Window, anti-aliasing (the game's own, FSR 4 or DLSS) and the overlay."""
        tk, ttk = self.tk, self.ttk
        f = self.scrolled_page('graphics', _('Graphics', 'Графика'), _('How the game appears on your screen.'))
        self.build_display(f)
        self.row(f, _('Screen shape'), self.choice(f, 'screen_shape', 'app', SCREEN_SHAPES, width=44),
                 _('Ultrawide and other screens: the game fills the shape and sees more at the sides (or above and '
                   'below on 16:10); the HUD moves to the edges and movies keep their shape with bars. Automatic '
                   'follows the window, so play in fullscreen on an ultrawide monitor.'), restart=True)
        self.section(f, _('Anti-aliasing'))
        self.row(f, _('Anti-aliasing'), self.choice(f, 'anti_aliasing', 'app', ANTI_ALIASING, width=44),
                 _("Replaces the game's own temporal anti-aliasing. FSR 4 (included) needs a Radeon RX 7000 or "
                   "9000; DLSS needs an NVIDIA RTX card and NVIDIA's files (below). Without what they need the "
                   "game keeps its own.") if WINDOWS else
                 _("Replaces the game's own temporal anti-aliasing. FSR 4 (included) needs a Radeon RX 7000 or "
                   "9000 and a recent Mesa (RADV); without it the game keeps its own."), restart=True)
        if not WINDOWS:  # DLSS (Streamline) is Windows only
            self.section(f, _('Overlay'))
            self.check(f, 'show_fps', 'ini', _('Show performance overlay'),
                       _('Items, size and corner: in-game menu (Insert / R3+L2) > Overlay.'))
            self.note(f, _('Screenshots: F12 (saved in the user/screenshots folder).'), top=10)
            return
        holder = ttk.Frame(f)
        holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(10, 0))
        ttk.Button(holder, text=_('Open the DLSS folder'), command=self.open_dlss_folder).pack(side='left')
        ttk.Button(holder, text=_('Download page (Streamline 2.14.16)'),
                   command=lambda: open_url(STREAMLINE_PAGE)).pack(side='left', padx=6)
        self.dlss_state = ttk.Label(holder, text='', style='Muted.TLabel')
        self.dlss_state.pack(side='left', padx=10)
        self.note(f, _('DLSS: from the Streamline SDK 2.14.16 zip, folder bin\\x64, copy {} into the DLSS folder.')
                  .format(', '.join(DLSS_FILES)), top=6)
        self.section(f, _('Overlay'))
        self.check(f, 'show_fps', 'ini', _('Show performance overlay'),
                   _('Items, size and corner: in-game menu (Insert / R3+L2) > Overlay.'))
        self.note(f, _('Screenshots: F12 (saved in the user\\screenshots folder).'), top=10)
        self.refresh_dlss_state()

    def refresh_dlss_state(self):
        missing = dlss_missing()
        self.dlss_state.configure(text=_('DLSS files: ready') if not missing else
                                  _('DLSS files missing: {}').format(', '.join(missing)))

    def open_dlss_folder(self):
        folder = dlss_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.messagebox.showerror(APP_NAME, str(error))
            return
        self.open_path(folder)
        self.refresh_dlss_state()

    def build_display(self, f):
        """Window: the top of the Graphics page."""
        self.section(f, _('Window', 'Окно'), top=4)
        self.row(f, _('Display mode'), self.choice(f, 'display_mode', 'ini', DISPLAY_MODES),
                 _('Borderless covers the screen without changing the display mode.'))
        self.check(f, 'vsync', 'ini', _('VSync'), _('Off allows tearing for the lowest latency.'), restart=True)
        self.check(f, 'hdr', 'app', _('Allow HDR output', 'Разрешить HDR'),
                   _('When HDR is on in Windows and the display supports it.',
                     'Если HDR включён в Windows и монитор его поддерживает.') if WINDOWS else
                   _('When the desktop (KDE Plasma 6, GNOME 48 or gamescope) has HDR on and the display supports it.'))

    def build_performance(self):
        f = self.scrolled_page('performance', _('Performance'), _('Frame rate and speed options.'))
        self.section(f, _('Frame rate', 'Частота кадров'), top=4)
        self.row(f, _('Frame rate limit'), self.choice(f, 'fps_limit', 'ini', FPS_LIMITS),
                 _('The highest frame rate. The game keeps its speed at any frame rate; Unlimited runs as fast '
                   'as the PC allows.'), restart=True)
        self.section(f, 'inFAMOUS')
        self.row(f, _('Draw distance'), self.choice(f, 'draw_distance', 'app', DRAW_DISTANCE),
                 _('How far buildings and details keep their full detail in the open city. Long and Very long '
                   'show less pop-in than the PS4 and cost some FPS.'), restart=True)
        self.row(f, _('Internal resolution'), self.choice(f, 'render_scale', 'app', RENDER_SCALES),
                 _('The size the game draws at. Below 100% is faster; with FSR 4 (Graphics > Anti-aliasing) '
                   'the picture is rebuilt at the window size, otherwise it is only scaled up and looks soft. '
                   'The HUD keeps its layout.'), restart=True)
        self.row(f, _('Performance mode'), self.choice(f, 'perf_mode', 'app', PERF_MODES),
                 _('The game\'s own lower settings, from axylpup\'s 1.1: shorter detail distance, one shadow '
                   'level less, lighter particles and fewer high-resolution textures nearby. Figures are '
                   'from his PC in the heaviest city view.'), restart=True)
        self.row(f, _('Shader compilation'), self.choice(f, 'shader_compile', 'app', SHADER_COMPILE),
                 _('The first time a place or effect appears its shaders are compiled. In the background the '
                   'game keeps running and what they draw shows up a moment later; in the frame the game '
                   'waits for them (a stutter). Either way they are kept in the cache for the next time.'),
                 restart=True)
        self.row(f, _('Effects'), self.choice(f, 'readbacks', 'app', READBACKS),
                 _('Smoke, sparks, neon and bullets are computed on the graphics card and read back by the '
                   'game. Full keeps all of them; Light and Off are faster but lose effects.'), restart=True)
        self.section(f, _('Graphics card'))
        gpus = list_gpus()
        cards = [('', ('Automatic',))] + [(row[0], (row[1],)) for row in gpus]
        self.row(f, _('Graphics card'), self.choice(f, 'gpu_id', 'app', cards),
                 _('The GPU the game runs on. Choose your dedicated card if the game uses the integrated one.'),
                 restart=True)

    def build_game(self):
        f = self.scrolled_page('game', _('Game', 'Игра'), _('Your game dump, saves and the game patches.'))
        self.section(f, _('Folders'), top=4)
        self.folder(f, 'game_dir', _('Game folder', 'Папка игры'),
                    _('Choose the folder with eboot.bin', 'Выберите папку с eboot.bin'),
                    _('Your own dump of the game (eboot.bin, sce_module, sce_sys): inFAMOUS Second Son, '
                      'any region and version.',
                      'Ваш дамп игры (eboot.bin, sce_module, sce_sys): inFAMOUS Second Son, '
                      'любой регион и версия.'), on_change=self.game_changed)
        self.folder(f, 'update_dir', _('Update folder (optional)'),
                    _('Choose the folder of the update (eboot.bin, sce_sys)'),
                    _('Only if the update is dumped apart from the game: it is laid over the game folder '
                      'without changing either. Leave empty when the game folder already has the update.'),
                    on_change=self.game_changed)
        self.folder(f, 'user_dir', _('Saves folder', 'Папка сохранений'),
                    _('Choose the saves folder', 'Выберите папку сохранений'),
                    _('Empty: {} (shader caches are kept there too).',
                      'Пусто: {} (там же кэш шейдеров).').format(DATA_DIR / 'user'), on_change=self.refresh_status)
        self.section(f, _('Saves'))
        self.build_save_backups(f)
        self.section(f, _('Language'))
        self.row(f, _('Game language', 'Язык игры'), self.choice(f, 'game_language', 'app', LANGUAGES),
                 _('Text and voices. Automatic follows the launcher language (Portuguese: the Brazilian dub).'),
                 restart=True)
        self.build_patches(f)
        self.build_mods(f)
        self.build_controls(f)

    def user_dir(self):
        return Path(self.var('user_dir', 'app').get() or DATA_DIR / 'user')

    def build_save_backups(self, f):
        """run.py backs the saves up before every start; one of them can be restored here."""
        ttk = self.ttk
        holder = ttk.Frame(f)
        self.backup_choice = ttk.Combobox(holder, state='readonly', width=30)
        self.backup_choice.pack(side='left')
        ttk.Button(holder, text=_('Restore', 'Восстановить'), command=self.restore_backup).pack(side='left', padx=(6, 0))
        ttk.Button(holder, text=_('Refresh', 'Обновить'), command=self.refresh_backups).pack(side='left', padx=(6, 0))
        self.row(f, _('Save backups', 'Резервные копии'), holder,
                 _('Made before every start, newest first; the last {} are kept. Restoring backs up the '
                   'current saves first.').format(save_backup.KEEP))
        self.refresh_backups()

    def refresh_backups(self):
        names = save_backup.list_backups(self.user_dir())
        self.backup_choice.configure(values=names)
        self.backup_choice.set(names[0] if names else '')

    def restore_backup(self):
        name = self.backup_choice.get()
        if not name:
            return
        if self.process:
            self.messagebox.showwarning(APP_NAME, _('Close the game before restoring saves.'))
            return
        if not self.messagebox.askyesno(APP_NAME, _('Replace the current saves with the backup {}? '
                                                    'The current saves are backed up first.').format(name)):
            return
        try:
            safety = save_backup.restore(self.user_dir(), name)
        except (OSError, ValueError) as error:
            self.messagebox.showerror(APP_NAME, _('Could not restore: {}').format(error))
            return
        self.refresh_backups()
        self.refresh_status()
        self.messagebox.showinfo(APP_NAME, _('Saves restored from {}.').format(name) +
                                 (_(' Previous saves: {}.').format(safety.name) if safety else ''))

    def build_patches(self, f):
        """Community patches of the selected game's built-in XML and third-party patch files, on the
        Game page. Resolution and frame rate patches follow Performance (issn.ini; run.py adds them)."""
        self.section(f, _('Game patches', 'Патчи игры'))
        self.game_patch_frame = self.ttk.Frame(f)
        self.game_patch_frame.grid(row=self.next_row(f), column=0, columnspan=2, sticky='we')
        self.game_patch_title, self.game_patch_vars = None, {}
        self.section(f, _('Third-party patches', 'Сторонние патчи'))
        self.folder(f, 'patches_dir', _('Patches folder', 'Папка патчей'), _('Choose the patches folder', 'Выберите папку патчей'),
                    _('shadPS4/GoldHEN XML patch files for this game version. Empty: {}',
                      'XML-патчи shadPS4/GoldHEN для этой версии игры. Пусто: {}').format(DATA_DIR / 'patches'),
                    on_change=self.refresh_lists)
        self.patches_frame = self.ttk.Frame(f)
        self.patches_frame.grid(row=self.next_row(f), column=0, columnspan=2, sticky='we', pady=(8, 0))
        self.ttk.Button(f, text=_('Refresh', 'Обновить'), command=self.refresh_lists).grid(
            row=self.next_row(f), column=0, sticky='w', pady=(14, 0))

    def refresh_game_patches(self):
        import xml.etree.ElementTree as ET
        from patches import ALWAYS_ON, EXCLUSIVE_PREFIX
        tk, f = self.tk, self.game_patch_frame
        self.store_game_patches()
        for widget in f.winfo_children():
            widget.destroy()
        f.columnconfigure(1, weight=1)
        folder = self.meta_dir()
        title_id, profile = game_profile_of(folder)
        self.game_patch_title, self.game_patch_vars = None, {}
        if not profile:
            self.note(f, _('No built-in patches for this game.', 'Для этой игры нет встроенных патчей.'), top=0)
            return
        _ids, xml, version = profile[:3]
        metas = [m for m in ET.parse(xml).getroot().iter('Metadata') if m.get('AppVer') == version]
        chosen = self.app.get('game_patches', {}).get(title_id)
        on = set(chosen) if chosen is not None else {m.get('Name') for m in metas
                                                         if m.get('isEnabled', 'false').lower() == 'true'}
        self.note(f, _('{} · version {} · {}. Resolution and frame rate patches follow Performance.',
                       '{} · версия {} · {}.').format(title_id, version, xml.name), top=0)
        for meta in metas:
            name = meta.get('Name')
            if name.startswith(EXCLUSIVE_PREFIX) or name.startswith('Frame Rate Patch') or name in ALWAYS_ON:
                continue
            var = tk.BooleanVar(value=name in on)
            self.game_patch_vars[name] = var
            author = meta.get('Author')
            self.check(f, None, None, name + (f'  ({author})' if author else ''), meta.get('Note'), var=var)
        self.game_patch_title = title_id

    def store_game_patches(self):
        """The page's selection into settings (the whole list for this game, without the resolution
        and frame rate patches: run.py adds those from issn.ini)."""
        if not self.game_patch_title:
            return
        self.app.setdefault('game_patches', {})[self.game_patch_title] = \
            [n for n, v in self.game_patch_vars.items() if v.get()]

    def build_mods(self, f):
        """Loose-file mods layered over the game at start, on the Game page."""
        self.section(f, _('Mods', 'Моды'))
        self.check(f, 'mods_enabled', 'app', _('Load mods', 'Загружать моды'),
                   _('Loose-file mods, each in its own folder with dvdroot_ps4.',
                     'Моды из файлов, каждый в своей папке с dvdroot_ps4.'))
        self.folder(f, 'mods_dir', _('Mods folder', 'Папка модов'), _('Choose the mods folder', 'Выберите папку модов'),
                    _('Empty: {}', 'Пусто: {}').format(DATA_DIR / 'mods'), on_change=self.refresh_lists)
        self.mods_frame = self.ttk.Frame(f)
        self.mods_frame.grid(row=self.next_row(f), column=0, columnspan=2, sticky='we', pady=(8, 0))
        self.ttk.Button(f, text=_('Refresh', 'Обновить'), command=self.refresh_lists).grid(
            row=self.next_row(f), column=0, sticky='w', pady=(14, 0))

    def build_controls(self, f):
        """Remapping of the PS4 buttons, on the Game page (run.py passes ISSN_KEY_MAP and
        ISSN_PAD_MAP to the game)."""
        tk, ttk = self.tk, self.ttk
        self.section(f, _('Controls', 'Управление'))
        self.note(f, _('Which key or gamepad button presses each button of the game.'), top=4)
        saved = {kind: control_values(self.app.get('controls'), kind) for kind in ('key', 'pad')}
        style = self.app.get('pad_style') if self.app.get('pad_style') in PAD_STYLES else 'playstation'
        self.pad_style = tk.StringVar(value=style)
        names = ttk.Frame(f)
        for value, label in (('playstation', 'PlayStation'), ('xbox', 'Xbox')):
            ttk.Radiobutton(names, text=label, value=value, variable=self.pad_style,
                            command=self.relabel_pad_controls).pack(side='left', padx=(0, 14))
        self.row(f, _('Gamepad names'), names)
        self.row(f, _('Controller without touchpad'), self.choice(f, 'pad_emulation', 'app', PAD_EMULATION),
                 _('For Xbox, 8BitDo and other pads without a touchpad or motion sensors. Touchpad click: '
                   'Select/Back (with L1: the left side). Swipe: hold Select/Back and push the right stick. '
                   'Two fingers: hold Select/Back + RB. Spray can: L3 + R3 together tilts the pad, LB '
                   'rattles it, the right stick aims. Automatic turns it off on a DualShock 4 or DualSense.'),
                 restart=True)
        self.note(f, _('Keyboard and mouse: WASD moves, IJKL turns the camera. Click in the game window and the '
                       'mouse aims (1:1, as in axylpup\'s build); Y or F7 lets the mouse go and takes it back, '
                       '- / = change its speed. Left click R2 (shoot), right click L2 (aim). Spray can: hold Z '
                       'to tilt the pad, tap Z or hold X to rattle it, move the mouse to aim. R / G: left / right '
                       'touchpad (G + IJKL: swipe). Arrows: d-pad. F12: screenshot. Insert: the port\'s menu.'), top=8)
        table = ttk.Frame(f)
        table.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(16, 0))
        for column, span, title in ((0, 2, _('GAME BUTTON')), (2, 1, _('KEYBOARD')), (3, 2, _('GAMEPAD'))):
            ttk.Label(table, text=heading_text(title), style='Section.TLabel').grid(row=0, column=column, columnspan=span, sticky='w')
        self.control_vars, self.pad_boxes, self.pad_icons = {'key': {}, 'pad': {}}, {}, {}
        size = self.px(28)
        labels = PAD_STYLES[style]

        def icon_canvas(parent):
            return tk.Canvas(parent, width=size, height=size, bg=PANEL, highlightthickness=0, bd=0)
        for r, (button, key, pad) in enumerate(CONTROLS, start=1):
            game_icon = icon_canvas(table)
            game_icon.grid(row=r, column=0, sticky='w', pady=3)
            button_icons.draw(game_icon, button_icons.GAME_BUTTONS[button], 'playstation', size)
            ttk.Label(table, text=PS_BUTTON_NAMES[button], width=14).grid(row=r, column=1, sticky='w', padx=(0, 12))
            key_var = tk.StringVar(value=saved.get('key', {}).get(button) or key)
            self.control_vars['key'][button] = key_var
            ttk.Entry(table, textvariable=key_var, width=16).grid(row=r, column=2, sticky='w', padx=(0, 18))
            pad_name = saved.get('pad', {}).get(button) or pad
            pad_var = tk.StringVar(value=pad_name or '')
            self.control_vars['pad'][button] = pad_var
            pad_icon = icon_canvas(table)
            pad_icon.grid(row=r, column=3, sticky='w', padx=(0, 6))
            self.pad_icons[button] = pad_icon
            if pad:
                box = ttk.Combobox(table, state='readonly', width=22, values=list(labels.values()))
                box.set(labels.get(pad_name, pad_name))

                def chosen(_e, b=box, v=pad_var, n=button):
                    v.set(self.pad_name(b.get()))
                    self.draw_pad_icon(n)
                box.bind('<<ComboboxSelected>>', chosen)
                self.pad_boxes[button] = box
            else:
                fixed = (_('L1 + Touchpad right') if button == 'touchpad_left'
                         else _('{} trigger').format(PS_BUTTON_NAMES[button]))
                box = ttk.Label(table, text=fixed, style='Muted.TLabel')
            box.grid(row=r, column=4, sticky='w')
            self.draw_pad_icon(button)
        self.note(f, _('Keys use SDL names: letters, digits, Space, Return, Tab, Left Shift, Left Ctrl, Left Alt, '
                       'Backspace, Up, Down, Left, Right, F1 to F12. A name the game does not know keeps the default '
                       '(the log says which). L2/R2 on the gamepad stay on the triggers. Touchpad right opens the '
                       'game\'s pause menu; Touchpad left (photo mode) is L1 + Touchpad right on the gamepad. Shake moves the '
                       'motion sensors like a shaken controller (the spray can), for pads without them. Insert '
                       'and R3+L2 always open the port\'s menu. Applied when the game starts.'))
        self.ttk.Button(f, text=_('Reset to defaults'), command=self.reset_controls).grid(
            row=self.next_row(f), column=0, sticky='w', pady=(14, 0))

    def draw_pad_icon(self, button):
        """The gamepad column's icon of a game button, in the chosen style (L2/R2: the triggers)."""
        name = self.control_vars['pad'][button].get() or button_icons.GAME_BUTTONS[button]
        button_icons.draw(self.pad_icons[button], name, self.pad_style.get(), self.px(28))

    def pad_name(self, label):
        """SDL gamepad button name of a label in the current naming style."""
        return next((n for n, l in PAD_STYLES[self.pad_style.get()].items() if l == label), label)

    def relabel_pad_controls(self):
        labels = PAD_STYLES[self.pad_style.get()]
        for button, box in self.pad_boxes.items():
            box.configure(values=list(labels.values()))
            name = self.control_vars['pad'][button].get()
            box.set(labels.get(name, name))
        for button in self.pad_icons:
            self.draw_pad_icon(button)

    def reset_controls(self):
        for button, key, pad in CONTROLS:
            self.control_vars['key'][button].set(key)
            self.control_vars['pad'][button].set(pad or '')
        self.relabel_pad_controls()

    def build_linux_options(self, f):
        """Wayland / X11, GameMode and MangoHud: only the ones this system has can be turned on."""
        tools = linux_tools()
        self.section(f, _('Linux'), top=4)
        self.check(f, 'desktop_integration', 'app', _('Menu entry and icon (AppImage)'),
                   _('Creates issn.desktop in ~/.local/share/applications so the launcher and the game show their '
                     'icon in the dock and the menu. Turning it off removes the entry.'))
        self.row(f, _('Window system'), self.choice(f, 'video_backend', 'app', VIDEO_BACKENDS), restart=True,
                 hint=_('Automatic lets SDL choose. Pick X11 if the window misbehaves on Wayland (it runs through '
                        'XWayland).') + ('' if tools['wayland'] else '  ' + _('No Wayland session detected.')))
        for key, title, name, hint in (
                ('gamemode', _('GameMode'), 'gamemode', _('Asks GameMode (gamemoderun) for the performance governor '
                                                          'while the game runs.')),
                ('mangohud', _('MangoHud overlay'), 'mangohud', _('Shows MangoHud (FPS, frame times, temperatures) '
                                                                   'through its Vulkan layer.'))):
            box = self.check(f, key, 'app', title, hint + ('' if tools[name] else '  ' + _('Not installed.')), restart=True)
            if not tools[name]:
                box.configure(state='disabled')

    def build_advanced(self):
        tk, ttk = self.tk, self.ttk
        f = self.scrolled_page('advanced', _('Advanced', 'Дополнительно'),
                               _('Launcher options, files and diagnostics.'))
        self.section(f, _('Launcher', 'Лаунчер'), top=4)
        self.row(f, _('Launcher language', 'Язык лаунчера'), self.choice(f, 'ui_language', 'app', UI_LANGUAGES),
                 _('Applies when the launcher opens again.', 'Применится при следующем открытии лаунчера.'))
        self.check(f, 'close_on_play', 'app', _('Close the launcher when the game starts', 'Закрывать лаунчер при запуске игры'))
        if WINDOWS and UPDATES:
            self.check(f, 'check_updates', 'app', _('Check for updates when the launcher opens',
                                                    'Проверять обновления при открытии лаунчера'))
        holder = ttk.Frame(f)
        holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(10, 0))
        if WINDOWS and UPDATES:
            ttk.Button(holder, text=_('Check for updates', 'Проверить обновления'),
                       command=lambda: threading.Thread(target=self.check_update, args=(True,), daemon=True).start()
                       ).pack(side='left')
        ttk.Button(holder, text=_('Desktop shortcut', 'Ярлык на рабочем столе'), command=self.shortcut).pack(side='left', padx=6)
        ttk.Button(holder, text=_('Add to Steam'), command=self.add_to_steam).pack(side='left')
        ttk.Label(holder, text=f'v{VERSION}', style='Muted.TLabel').pack(side='left', padx=10)
        if not WINDOWS:
            self.build_linux_options(f)
        self.section(f, _('Files'))
        holder = ttk.Frame(f)
        holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(6, 0))
        ttk.Button(holder, text=_('Port folder', 'Папка порта'), command=lambda: self.open_path(DATA_DIR)).pack(side='left')
        ttk.Button(holder, text='issn.ini', command=lambda: self.open_path(ini_path())).pack(side='left', padx=6)
        ttk.Button(holder, text=_('Save a bug report'), command=self.bug_report).pack(side='left')
        ttk.Label(holder, text=_('A zip on the desktop with the last log and your settings, to send with a bug.'),
                  style='Muted.TLabel').pack(side='left', padx=10)
        holder = ttk.Frame(f)
        holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(10, 0))
        ttk.Button(holder, text=_('Clear shader cache', 'Очистить кэш шейдеров'), command=self.clear_cache).pack(side='left')
        ttk.Label(holder, text=_('If the game only shows a black screen, this usually helps.',
                                 'Если игра показывает только чёрный экран, обычно это помогает.'),
                  style='Muted.TLabel').pack(side='left', padx=10)
        # Diagnostics: for development, folded away until asked for.
        toggle = ttk.Label(f, text='', style='Section.TLabel', cursor='hand2')
        toggle.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(22, 2))
        body = ttk.Frame(f)
        body.columnconfigure(1, weight=1)
        body_row = self.next_row(f)
        self.check(body, 'frame_stats', 'app', _('Frame statistics in the log (every 5 s)', 'Статистика кадров в журнале (раз в 5 с)'))
        self.check(body, 'vk_validation', 'app', _('Vulkan validation layers (needs the Vulkan SDK; much slower)',
                                                   'Слои валидации Vulkan (нужен Vulkan SDK; сильно замедляет)'))
        shown = tk.BooleanVar(value=False)

        def flip(_e=None):
            shown.set(not shown.get())
            toggle.configure(text=('▾ ' if shown.get() else '▸ ') + heading_text(_('Diagnostics (for development)').upper()))
            if shown.get():
                body.grid(row=body_row, column=0, columnspan=2, sticky='we')
            else:
                body.grid_forget()
        toggle.bind('<Button-1>', flip)
        shown.set(True)
        flip()
        self.build_log(f)

    def build_log(self, f):
        """The game's output, at the end of the Advanced page (the wheel over it scrolls the log)."""
        tk, ttk = self.tk, self.ttk
        self.section(f, _('Log', 'Журнал'))
        buttons = ttk.Frame(f)
        buttons.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(6, 8))
        ttk.Button(buttons, text=_('Copy', 'Копировать'), command=self.copy_log).pack(side='left')
        ttk.Button(buttons, text=_('Clear', 'Очистить'), command=lambda: self.set_log('')).pack(side='left', padx=6)
        holder = ttk.Frame(f)
        holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='we')
        self.log = tk.Text(holder, wrap='none', height=18, bg='#05070a', fg='#c9d2dc', insertbackground=TEXT,
                           relief='flat', font=(MONO_FONT, 9), padx=8, pady=6, state='disabled', highlightthickness=0)
        bar = ttk.Scrollbar(holder, command=self.log.yview)
        self.log.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        self.log.pack(side='left', fill='both', expand=True)

    # ---- state -------------------------------------------------------------------------------
    def game_changed(self):
        folder = self.meta_dir()
        info = game_info(folder)
        title = info[0] if info else APP_NAME
        self.root.title(f'{title} (PC)')
        if self.current_page == 'game':
            self.refresh_game_patches()
        self.banner_key = None
        self.draw_banner()
        self.set_icon()
        self.refresh_status()

    def refresh_status(self):
        if getattr(self, 'copying_system_files', False):
            return
        info = game_info(self.meta_dir())
        if not self.process:
            self.play_button.configure(text=heading_text(_('PLAY', 'ИГРАТЬ')), state='normal' if info else 'disabled')
            self.status.configure(text=self.summary() if info else _('Choose the game folder first.',
                                                                     'Сначала выберите папку игры.'), fg=MUTED)

    def summary(self):
        """The status line: what the next start uses."""
        def label(options, value):
            return next((_(*text) for v, text in options if v == value), value)
        aa = label(ANTI_ALIASING, self.var('anti_aliasing', 'app').get())
        limit = self.var('fps_limit', 'ini').get()
        limit = _('no FPS limit') if limit == '0' else _('up to {} FPS').format(limit)
        distance = label(DRAW_DISTANCE, self.var('draw_distance', 'app').get())
        return _('{} · {} · draw distance: {}').format(aa, limit, distance.lower())

    def refresh_lists(self):
        if not hasattr(self, 'patches_frame'):
            return
        tk, ttk = self.tk, self.ttk
        from mods import discover
        from patches import external_patches
        for holder in (self.mods_frame, self.patches_frame):
            for widget in holder.winfo_children():
                widget.destroy()
        root = Path(self.var('mods_dir', 'app').get() or DATA_DIR / 'mods')
        profile = load_json(DATA_DIR / 'mods.json', {})
        available = discover(root)
        if set(self.mod_order) != set(available):
            known = [n for n in profile.get('order', []) if n in available]
            self.mod_order = known + [n for n in available if n not in known]
        old = {n: v.get() for n, v in self.mod_vars.items()}
        self.mod_vars = {}
        if not available:
            ttk.Label(self.mods_frame, text=_('No mods in {} yet.', 'В {} пока нет модов.').format(root),
                      style='Muted.TLabel').pack(anchor='w')
        for index, name in enumerate(self.mod_order):
            line = ttk.Frame(self.mods_frame)
            line.pack(fill='x', pady=1)
            var = tk.BooleanVar(value=old.get(name, name not in profile.get('disabled', [])))
            self.mod_vars[name] = var
            ttk.Button(line, text='▲', width=3, command=lambda i=index: self.move_mod(i, -1)).pack(side='left')
            ttk.Button(line, text='▼', width=3, command=lambda i=index: self.move_mod(i, 1)).pack(side='left', padx=(2, 10))
            ttk.Checkbutton(line, text=f'{index + 1}.  {name}', variable=var,
                            command=self.refresh_conflicts).pack(side='left')
        if len(self.mod_order) > 1:
            ttk.Label(self.mods_frame, text=_('Lower in the list loads later and wins conflicts.',
                                              'Ниже в списке — загружается позже и перекрывает.'),
                      style='Muted.TLabel').pack(anchor='w', pady=(4, 0))
        self.conflict_label = ttk.Label(self.mods_frame, text='', justify='left', foreground='#e08080',
                                        wraplength=self.px(680))
        self.conflict_label.pack(anchor='w', pady=(4, 0))
        self.refresh_conflicts()
        if available:
            presets = ttk.Frame(self.mods_frame)
            presets.pack(anchor='w', pady=(8, 0))
            ttk.Label(presets, text=_('Preset', 'Пресет')).pack(side='left', padx=(0, 6))
            self.preset_choice = ttk.Combobox(presets, values=sorted(load_json(MOD_PRESETS, {})), width=24)
            self.preset_choice.pack(side='left')
            ttk.Button(presets, text=_('Load', 'Загрузить'), command=self.load_mod_preset).pack(side='left', padx=(6, 0))
            ttk.Button(presets, text=_('Save', 'Сохранить'), command=self.save_mod_preset).pack(side='left', padx=(6, 0))
            ttk.Button(presets, text=_('Delete', 'Удалить'), command=self.delete_mod_preset).pack(side='left', padx=(6, 0))
        chosen = load_json(DATA_DIR / 'patches.json', {})
        _title_id, game = game_profile_of(self.meta_dir())
        found = external_patches(Path(self.var('patches_dir', 'app').get() or DATA_DIR / 'patches'),
                                 game[2], game[1], game[0]) if game else []
        old = {n: v.get() for n, v in self.patch_vars.items()}
        self.patch_vars = {}
        if not found:
            ttk.Label(self.patches_frame, text=_('No patch files for this game version yet.', 'Пока нет патчей для этой версии игры.'),
                      style='Muted.TLabel').pack(anchor='w')
        for key, _path, meta in found:
            on = key in chosen.get('enabled', []) or (key not in chosen.get('disabled', []) and
                                                      meta.get('isEnabled', 'false').lower() == 'true')
            var = tk.BooleanVar(value=old.get(key, on))
            self.patch_vars[key] = var
            author = meta.get('Author')
            ttk.Checkbutton(self.patches_frame, text=key + (f'  —  {author}' if author else ''), variable=var).pack(anchor='w')

    def refresh_conflicts(self):
        from mods import conflicts
        if not getattr(self, 'conflict_label', None) or not self.conflict_label.winfo_exists():
            return
        root = Path(self.var('mods_dir', 'app').get() or DATA_DIR / 'mods')
        found = conflicts(root, [n for n in self.mod_order if self.mod_vars[n].get()])
        lines = [_('⚠ {} file(s) replaced by more than one mod (the last one wins):').format(len(found))] if found else []
        lines += [f'   {path}: {" > ".join(owners)}' for path, owners in found[:12]]
        if len(found) > 12:
            lines.append(_('   … and {} more').format(len(found) - 12))
        self.conflict_label.configure(text='\n'.join(lines))

    def save_mod_preset(self):
        name = self.preset_choice.get().strip()
        if not name:
            self.messagebox.showinfo(APP_NAME, _('Type a name for the preset first.'))
            return
        presets = load_json(MOD_PRESETS, {})
        presets[name] = {'order': self.mod_order, 'disabled': [n for n, v in self.mod_vars.items() if not v.get()]}
        MOD_PRESETS.write_text(json.dumps(presets, indent=2), encoding='utf-8')
        self.preset_choice.configure(values=sorted(presets))

    def load_mod_preset(self):
        from mods import apply_preset
        preset = load_json(MOD_PRESETS, {}).get(self.preset_choice.get().strip())
        if not isinstance(preset, dict):
            return
        order, disabled = apply_preset(preset, list(self.mod_order))
        self.mod_order = order
        for name, var in self.mod_vars.items():
            var.set(name not in disabled)
        chosen = self.preset_choice.get()
        self.refresh_lists()
        self.preset_choice.set(chosen)

    def delete_mod_preset(self):
        presets = load_json(MOD_PRESETS, {})
        if presets.pop(self.preset_choice.get().strip(), None) is not None:
            MOD_PRESETS.write_text(json.dumps(presets, indent=2), encoding='utf-8')
        self.preset_choice.configure(values=sorted(presets))
        self.preset_choice.set('')

    def move_mod(self, index, step):
        other = index + step
        if 0 <= other < len(self.mod_order):
            self.mod_order[index], self.mod_order[other] = self.mod_order[other], self.mod_order[index]
            self.refresh_lists()

    def gather(self):
        """What collect() writes, as data: settings.json, the issn.ini values, the mods selection and
        the third-party patches selection. Reads the window and changes nothing."""
        app, ini = dict(self.app), dict(self.ini)
        if self.game_patch_title:
            app['game_patches'] = {**app.get('game_patches', {}),
                                   self.game_patch_title: [n for n, v in self.game_patch_vars.items() if v.get()]}
        for key, var in self.vars.items():
            try:
                value = var.get()
            except self.tk.TclError:  # an unfinished number in a spinbox
                value = APP_DEFAULTS.get(key, INI_DEFAULTS.get(key))
            if var.store == 'ini':
                ini[key] = ('1' if value else '0') if key in INI_FLAGS else str(value)
            else:
                app[key] = value
        app['controls'] = {kind: control_changes({b: v.get() for b, v in vars_.items()}, kind)
                           for kind, vars_ in self.control_vars.items()}
        app['pad_style'] = self.pad_style.get()
        mods = {'order': list(self.mod_order),
                'disabled': [n for n, v in self.mod_vars.items() if not v.get()]} if self.mod_order else None
        patches = {'enabled': sorted(k for k, v in self.patch_vars.items() if v.get()),
                   'disabled': sorted(k for k, v in self.patch_vars.items() if not v.get())} if self.patch_vars else None
        return {'app': app, 'ini': ini, 'mods': mods, 'patches': patches}

    def collect(self):
        """Writes settings.json, issn.ini, mods.json and patches.json."""
        state = self.gather()
        _disk, lines = load_ini()
        persisted = {line.split('=', 1)[0].strip() for line in lines
                     if '=' in line and not line.lstrip().startswith('#')}
        baseline = getattr(self, 'saved', {'ini': self.ini})['ini']
        edited = {key: state['ini'][key] for key, var in self.vars.items()
                  if var.store == 'ini' and
                  (key not in persisted or state['ini'][key] != baseline.get(key))}
        try:
            save_ini(edited, lines)
            atomic_write(CONFIG_FILE, json.dumps(state['app'], indent=2, ensure_ascii=False))
            if state['mods']:
                atomic_write(DATA_DIR / 'mods.json', json.dumps(state['mods'], indent=2))
            if state['patches']:
                # Patches of other game versions keep their choice.
                chosen = load_json(DATA_DIR / 'patches.json', {})
                shown = set(self.patch_vars)
                enabled = {k for k in chosen.get('enabled', []) if k not in shown} | set(state['patches']['enabled'])
                disabled = {k for k in chosen.get('disabled', []) if k not in shown} | set(state['patches']['disabled'])
                atomic_write(DATA_DIR / 'patches.json', json.dumps(
                    {'enabled': sorted(enabled), 'disabled': sorted(disabled)}, indent=2))
        except OSError:
            self.messagebox.showerror(APP_NAME, _('Could not save all settings. Check write permissions and free disk space, then try again.',
                                                   'Не удалось сохранить все настройки. Проверьте права записи и свободное место, затем повторите.'))
            return False
        self.app = state['app']
        self.ini, self.ini_lines = load_ini()
        for key, var in self.vars.items():
            if var.store == 'ini':
                var.set(self.ini[key] == '1' if key in INI_FLAGS else self.ini[key])
        self.saved = self.gather()
        self.refresh_save_button()
        return True

    def save(self):
        """The Save button: the files, without starting the game. A new launcher language rebuilds
        the window in it."""
        if not self.collect():
            return
        language = self.app.get('ui_language') or windows_language()
        if language != LANG:
            self.rebuild(language)

    def rebuild(self, language):
        """The window again in another language: every setting stays in its variable; the page,
        the log, the running game and a pending update offer come back."""
        global LANG
        LANG = language
        log, page, update = self.log.get('1.0', 'end-1c'), self.current_page, self.pending_update
        status = self.status['text'], self.status['fg']  # of the running game; otherwise build() redoes it
        for var in self.vars.values():  # the old widgets' value labels and links
            for modes, name in var.trace_info():
                var.trace_remove(modes, name)
        for child in self.root.winfo_children():
            child.destroy()
        self.style()
        self.build()
        self.set_log(log)
        if self.process:
            self.play_button.configure(text=heading_text(_('RUNNING')), state='disabled')
            self.status.configure(text=status[0], fg=status[1])
        if update:
            self.offer_update(*update)
        self.show(page)
        self.refresh_lists()
        self.refresh_game_patches()
        self.saved = self.gather()
        self.refresh_save_button()

    def refresh_save_button(self):
        """Save is only active while the window differs from the files (gather() against the last save)."""
        self.save_button.configure(state='normal' if self.gather() != self.saved else 'disabled')

    def watch_changes(self):
        self.refresh_save_button()
        self.root.after(500, self.watch_changes)

    # ---- game process ------------------------------------------------------------------------
    def offer_system_files(self):
        """A PKG extraction (game or update) has its system files beside the game: offers the
        copy (system_files_beside) for the game folder and for an update folder with an
        eboot.bin, the update's candidate checked against the game's param.sfo. False when a
        copy was started: it runs in the background and presses Play again when it ends."""
        game_dir = self.app['game_dir'] or '.'
        update_dir = self.app.get('update_dir') or ''
        folders = [(game_dir, False)]
        if update_dir and not same_folder(update_dir, game_dir) and (Path(update_dir) / 'eboot.bin').is_file():
            folders.append((update_dir, True))
        for folder, is_update in folders:
            reference = (game_sfo(game_dir) or None) if is_update else None
            source = system_files_beside(folder, reference, game_dir if is_update else None)
            if not source or not self.messagebox.askyesno(APP_NAME, _(
                    'The files in {} look like a PKG extraction: the system files (param.sfo, icon0.png, '
                    'pic1.png and the rest) are in\n\n{}\n\nCopy all of them into its sce_sys folder now? '
                    'Files already there are kept.').format(folder, source)):
                continue
            self.adopt_in_background(source, folder)
            return False
        return True

    def adopt_in_background(self, source, folder):
        """Copies the Sc0 files off the Tk thread (a slow drive would freeze the window), shows
        the result and presses Play again, so the next offer or check follows from what is on
        disk."""
        self.copying_system_files = True
        self.play_button.configure(state='disabled')
        self.status.configure(text=_('Copying the system files…'), fg=TEXT)

        def work():
            error = None
            try:
                adopt_system_files(source, folder)
            except OSError as failure:
                error = failure

            def done():
                self.copying_system_files = False
                self.game_changed()  # whatever was copied already shows
                if error:
                    self.messagebox.showerror(APP_NAME, _('Could not copy the files: {}').format(error))
                    self.show('game')
                else:
                    self.play()
            self.ui_calls.put(done)
        threading.Thread(target=work, daemon=True).start()

    def play(self):
        if self.process or getattr(self, 'copying_system_files', False):
            return
        if not self.collect():
            return
        if not game_info(self.app['game_dir']):
            self.messagebox.showerror(APP_NAME, _('Choose the game folder with eboot.bin (CUSA00004).',
                                                  'Выберите папку игры с eboot.bin (CUSA00004).'))
            self.show('game')
            return
        if not self.offer_system_files():
            return
        missing = missing_game_files(self.app['game_dir'])
        if missing:
            self.messagebox.showerror(APP_NAME, _('The game dump is incomplete. Missing or empty files:\n\n{}\n\n'
                                                  'Dump the game again, or copy these files from a complete dump.')
                                      .format('\n'.join(describe_missing(missing))))
            self.show('game')
            return
        update_dir = self.app.get('update_dir') or ''
        if update_dir and same_folder(update_dir, self.app['game_dir'] or '.'):
            self.messagebox.showerror(APP_NAME, _('Choose two separate folders for the game and its update, '
                                                  'or leave the update folder empty.'))
            self.show('game')
            return
        missing = missing_game_files(update_dir, REQUIRED_UPDATE_FILES) if update_dir else []
        if missing:
            self.messagebox.showerror(APP_NAME, _('The update folder {} is incomplete. Missing or empty files:\n\n{}\n\n'
                                                  'Choose the folder of the update (eboot.bin, sce_sys), or leave it empty.')
                                      .format(update_dir, '\n'.join(describe_missing(missing))))
            self.show('game')
            return
        self.set_log('')
        # The same last_run.log as --play, so a report from the PLAY button has the file too.
        run_log = open_play_log(self.app)
        # With 'close_on_play' the launcher exits 5 s after the start: the game writes the log
        # file itself (its output goes straight to the file, which outlives the launcher).
        # Tester 2026-10-23: three runs in that mode left last_run.log at an older run.
        direct_log = open_direct_log(self.app) if self.app.get('close_on_play') else None
        try:
            self.process = subprocess.Popen(run_command(), cwd=PORT_DIR, env=game_environment(self.app),
                                            stdin=subprocess.DEVNULL,
                                            stdout=direct_log if direct_log else subprocess.PIPE,
                                            stderr=subprocess.STDOUT, creationflags=NO_WINDOW, **NEW_GROUP)
        except OSError as error:
            if direct_log:
                direct_log.close()
            run_log.close()
            self.append(_('Could not start: {}', 'Не удалось запустить: {}').format(error) + '\n')
            self.process = None
            return
        self.job = GameJob(self.process)
        if direct_log:
            direct_log.close()  # the game holds its own handle
            self.append(_('The game writes its log to {}', 'Игра пишет журнал в {}')
                        .format(run_log_path(self.app)) + '\n')
            run_log.close()
            process = self.process
            threading.Thread(target=lambda: self.output.put((process.wait(),)), daemon=True).start()
        else:
            threading.Thread(target=self.read_output, args=(self.process, run_log), daemon=True).start()
        self.play_button.configure(text=heading_text(_('RUNNING')), state='disabled')
        self.status.configure(text=_('Preparing the game; it opens in its own window…',
                                     'Подготовка игры; она откроется в своём окне…'), fg=TEXT)
        if self.app.get('close_on_play'):
            self.root.after(5000, self.root.destroy)  # the game keeps running

    def read_output(self, process, run_log=None):
        try:
            for raw in iter(process.stdout.readline, b''):
                text = raw.decode('utf-8', errors='replace')
                if run_log:
                    try:
                        run_log.write(text)
                    except (OSError, ValueError):
                        # Disk full or folder gone: keep draining the pipe, or the game blocks
                        # on its next print and the button stays at RUNNING.
                        _close_quietly(run_log)
                        run_log = None
                self.output.put(text)
        finally:
            if run_log:
                _close_quietly(run_log)
            self.output.put((process.wait(),))

    def drain_output(self):
        while not self.ui_calls.empty():
            self.ui_calls.get_nowait()()
        try:
            for _i in range(500):
                item = self.output.get_nowait()
                if isinstance(item, tuple):
                    self.append(_('\nThe game exited (code {}).\n', '\nИгра завершилась (код {}).\n').format(item[0]))
                    self.process = None
                    if self.job:
                        self.job.close()
                    self.refresh_status()
                else:
                    if 'Entering original x86-64 code' in item:
                        self.status.configure(text=_('The game is running.', 'Игра запущена.'), fg=TEXT)
                    elif 'restarting through run.py' in item:
                        self.status.configure(text=_('Restarting with the new settings…', 'Перезапуск с новыми настройками…'), fg=TEXT)
                    self.append(item)
        except queue.Empty:
            pass
        self.root.after(100, self.drain_output)

    def append(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text)
        lines = int(self.log.index('end-1c').split('.')[0])
        if lines > MAX_LOG_LINES:
            self.log.delete('1.0', f'{lines - MAX_LOG_LINES}.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def set_log(self, text):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.insert('end', text)
        self.log.configure(state='disabled')

    def bug_report(self):
        """The last run's log, issn.ini and the launcher settings in a zip on the desktop (no game files)."""
        desktop = Path.home() / 'Desktop'
        target = (desktop if desktop.is_dir() else DATA_DIR) / time.strftime('issn-bug-report-%Y%m%d-%H%M%S.zip')
        try:
            with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
                for path in (run_log_path(self.app), ini_path(), CONFIG_FILE, DATA_DIR / 'out' / 'patch_status.json'):
                    if Path(path).is_file():
                        z.write(path, Path(path).name)
                import platform
                z.writestr('version.txt', f'launcher v{VERSION}\n{platform.platform()}\n'
                                          f'{platform.processor()}\n')
        except OSError as error:
            self.status.configure(text=_('Could not save the report: {}').format(error))
            return
        self.status.configure(text=_('Bug report saved: {}').format(target))
        self.open_path(target.parent)

    def copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log.get('1.0', 'end'))

    def open_path(self, path, key=None):
        if key == 'user_dir' and not path:
            path = DATA_DIR / 'user'
        elif key == 'mods_dir' and not path:
            path = DATA_DIR / 'mods'
        elif key == 'patches_dir' and not path:
            path = DATA_DIR / 'patches'
        path = Path(path or DATA_DIR)
        if path.suffix == '.ini':
            if not self.collect():
                return
        elif not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        if WINDOWS:
            os.startfile(str(path))
        else:
            subprocess.Popen(['xdg-open', str(path)], env=host_environment(), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ---- updates -------------------------------------------------------------------------------
    def check_update(self, manual=False):
        """Helper thread: asks GitHub for the newest release and offers it when it is newer."""
        try:
            version, url, page = latest_release()
        except (OSError, ValueError, KeyError) as failure:
            if manual:
                self.ui_calls.put(lambda error=failure: self.messagebox.showerror(
                    APP_NAME, _('Could not check for updates: {}', 'Не удалось проверить обновления: {}').format(error)))
            return

        def show():
            if version_tuple(version) > version_tuple(VERSION):
                self.offer_update(version, url, page)
            elif manual:
                self.messagebox.showinfo(APP_NAME, _('You have the latest version ({}).',
                                                         'У вас последняя версия ({}).').format(VERSION))
        self.ui_calls.put(show)

    def offer_update(self, version, url, page):
        if self.update_box:
            return
        tk, ttk = self.tk, self.ttk
        text = _('Version {} is available.', 'Доступна версия {}.').format(version)
        box = tk.Frame(self.notice, bg=CARD, highlightthickness=0)
        tk.Frame(box, bg=NEON, width=self.px(4)).pack(side='left', fill='y')
        self.update_label = tk.Label(box, text=text, bg=CARD, fg=TEXT, font=(UI_FONT, 10, 'bold'), justify='left')
        self.update_label.pack(side='left', padx=self.px(14), pady=self.px(8))
        self.update_button = ttk.Button(box, text=_('Update', 'Обновить'),
                                        command=lambda: self.install_update(version, url, page))
        self.update_button.pack(side='right', padx=(0, self.px(14)), pady=self.px(6))
        ttk.Button(box, text=_("What's new", 'Что нового'), command=lambda: open_url(page)).pack(
            side='right', padx=(0, self.px(8)), pady=self.px(6))
        box.pack(fill='x')
        self.notice.pack(fill='x', after=self.notice.master.winfo_children()[1])
        self.update_box = box
        self.pending_update = (version, url, page)
        if not self.process:
            self.status.configure(text=text, fg=TEXT)

    def install_update(self, version, url, page):
        if self.process:
            self.messagebox.showinfo(APP_NAME, _('Close the game before updating.', 'Закройте игру перед обновлением.'))
            return
        if not FROZEN or not url:  # a source tree updates with git
            open_url(page)
            return
        if not self.messagebox.askyesno(APP_NAME, _(
                'Install version {} now? The launcher closes, installs it and opens again. Saves and settings are kept.',
                'Установить версию {} сейчас? Лаунчер закроется, установит её и откроется снова. Сохранения и '
                'настройки останутся.').format(version)):
            return
        self.update_button.configure(state='disabled')

        def progress(percent):
            self.update_label.configure(text=_('Downloading version {}… {}%', 'Загрузка версии {}… {}%').format(
                version, percent))

        def work():
            try:
                shutil.rmtree(UPDATE_DIR, ignore_errors=True)
                UPDATE_DIR.mkdir(parents=True, exist_ok=True)
                archive = UPDATE_DIR / 'update.zip'
                request = urllib.request.Request(url, headers={'User-Agent': 'issn-launcher'})
                with urllib.request.urlopen(request, timeout=60) as response, open(archive, 'wb') as out:
                    total, done, shown = int(response.headers.get('Content-Length') or 0), 0, -1
                    while chunk := response.read(1 << 20):
                        out.write(chunk)
                        done += len(chunk)
                        percent = done * 100 // total if total else 0
                        if percent != shown:
                            shown = percent
                            self.ui_calls.put(lambda p=percent: progress(p))
                with zipfile.ZipFile(archive) as package:
                    package.extractall(UPDATE_DIR / 'new')
                archive.unlink()
                new = next((p.parent for p in (UPDATE_DIR / 'new').rglob('inFAMOUS Second Son.exe')), None)
                if not new:
                    raise OSError('inFAMOUS Second Son.exe is missing from the download')
                # The new launcher copies itself over this installation once this one has closed.
                subprocess.Popen([str(new / 'inFAMOUS Second Son.exe'), '--install-update', str(PORT_DIR), str(os.getpid())],
                                 cwd=str(new), stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
                self.ui_calls.put(self.root.destroy)
            except (OSError, zipfile.BadZipFile) as failure:
                def failed(error=failure):
                    self.update_button.configure(state='normal')
                    self.update_label.configure(text=_('Version {} is available.', 'Доступна версия {}.').format(version))
                    self.messagebox.showerror(APP_NAME, _('Update failed: {}', 'Не удалось обновить: {}').format(error))
                self.ui_calls.put(failed)
        threading.Thread(target=work, daemon=True).start()

    def clear_cache(self):
        """Shader and pipeline caches; they are rebuilt while playing."""
        if self.process:
            return
        import shutil
        cache = Path(self.app['user_dir'] or DATA_DIR / 'user') / 'cache'
        shutil.rmtree(cache, ignore_errors=True)
        self.messagebox.showinfo(APP_NAME, _('Shader cache cleared. The next start stutters for a few minutes while '
                                                 'it is rebuilt.', 'Кэш шейдеров очищен. Следующий запуск несколько '
                                                 'минут будет подтормаживать, пока кэш собирается заново.'))

    def shortcut(self):
        """inFAMOUS Second Son.lnk on the desktop (a .desktop entry in the menu and on the desktop on Linux)."""
        if not WINDOWS:
            self.linux_shortcut()
            return
        if FROZEN:
            target, arguments, icon = sys.executable, '', f'{sys.executable},0'
        else:  # a source tree: the launcher script with the windowless Python
            pythonw = Path(sys.executable).with_name('pythonw.exe')
            target = str(pythonw if pythonw.exists() else sys.executable)
            arguments, icon = Path(__file__).resolve(), PORT_DIR / 'launcher' / 'issn.ico'
        script = ('$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath("Desktop")'
                  '+"\\inFAMOUS Second Son.lnk");'
                  f'$s.TargetPath="{target}";$s.WorkingDirectory="{PORT_DIR}";$s.IconLocation="{icon}";'
                  + (f"$s.Arguments='\"{arguments}\"';" if arguments else '') + '$s.Save()')
        result = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True,
                                creationflags=NO_WINDOW)
        if result.returncode == 0:
            self.messagebox.showinfo(APP_NAME, _('Shortcut created on the desktop.', 'Ярлык создан на рабочем столе.'))
        else:
            self.messagebox.showerror(APP_NAME, result.stderr.decode(errors='replace')[:400])

    def linux_shortcut(self):
        launcher = os.environ.get('APPIMAGE') or (sys.executable if FROZEN else '')
        command = f'"{launcher}"' if launcher else f'python3 "{Path(__file__).resolve()}"'
        icon_dir = Path.home() / '.local' / 'share' / 'icons'
        icon = icon_dir / 'issn.png'
        try:
            icon_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PORT_DIR / 'launcher' / 'issn.png', icon)
        except OSError:
            icon = 'issn'
        entry = desktop_entry(command, icon)
        folders = [Path.home() / '.local' / 'share' / 'applications']
        try:
            desktop = subprocess.run(['xdg-user-dir', 'DESKTOP'], capture_output=True, text=True,
                                     env=host_environment()).stdout.strip()
        except OSError:
            desktop = ''
        folders.append(Path(desktop) if desktop else Path.home() / 'Desktop')
        created = []
        try:
            for folder in folders:
                if not folder.is_dir() and folder != folders[0]:
                    continue
                folder.mkdir(parents=True, exist_ok=True)
                # In the applications menu the file is named after the window's application id.
                target = folder / ('issn.desktop' if folder == folders[0] else 'inFAMOUS Second Son.desktop')
                target.write_text(entry, encoding='utf-8')
                target.chmod(0o755)
                created.append(str(target))
        except OSError as error:
            self.messagebox.showerror(APP_NAME, str(error))
            return
        self.messagebox.showinfo(APP_NAME, _('Shortcut created:', 'Ярлык создан:') + '\n' + '\n'.join(created))

    def add_to_steam(self):
        """A non-Steam shortcut with the game's icon, capsules, hero and logo (scripts/steam_shortcut.py).
        Steam rewrites its shortcuts when it exits, so an open Steam is closed first (when the player
        agrees) and opened again afterwards."""
        if not self.collect():
            return
        game = self.var('game_dir', 'app').get()
        if not game_info(game):
            self.messagebox.showerror(APP_NAME, _('Choose the game folder with eboot.bin (CUSA00004).',
                                                  'Выберите папку игры с eboot.bin (CUSA00004).'))
            return
        reopen = False
        if steam_shortcut.steam_running():
            if not self.messagebox.askyesno(APP_NAME, _('Steam is open. Close Steam, add the game and open Steam again?')):
                return
            reopen = True
        self.root.configure(cursor='watch')
        command, icon = steam_launch_command(), steam_icon(game)

        def work():
            try:
                if reopen and not steam_shortcut.close_steam():
                    raise steam_shortcut.SteamError('still_running')
                result = steam_shortcut.add_to_steam(game, command, icon)
            except Exception as error:  # reported in the window, never lost in a thread
                outcome = error
            else:
                outcome = result
            if reopen:
                steam_shortcut.start_steam()
            self.ui_calls.put(lambda: self.steam_done(outcome))
        threading.Thread(target=work, daemon=True).start()

    def steam_done(self, outcome):
        self.root.configure(cursor=ARROW)
        if isinstance(outcome, steam_shortcut.SteamError):
            text = {'running': _('Steam is open. Close it completely (Steam > Exit) and click again: Steam '
                                 'rewrites its shortcuts when it closes.'),
                    'still_running': _('Steam did not close. Close it (Steam > Exit) and click again.'),
                    'no_steam': _('Steam was not found for this user.'),
                    'no_art': _('The game folder has no sce_sys/pic1.png for the pictures.')}
            self.messagebox.showerror(APP_NAME, text.get(outcome.code, str(outcome)))
        elif isinstance(outcome, Exception):
            self.messagebox.showerror(APP_NAME, f'{type(outcome).__name__}: {outcome}')
        else:
            text = (_('The shortcut "{}" was updated in Steam (accounts: {}), with its icon, capsules, background '
                      'and logo. Open Steam to see it in the library.') if outcome['updated'] else
                    _('The shortcut "{}" was added to Steam (accounts: {}), with its icon, capsules, background '
                      'and logo. Open Steam to see it in the library.'))
            self.messagebox.showinfo(APP_NAME, text.format(steam_shortcut.APP_NAME, outcome['accounts']))

    def close(self):
        if not self.collect():
            return
        self.root.destroy()


class GameJob:
    """A Windows job holding run.py and everything it starts: issn-probe.exe and the launches made
    by the in-game restart (no longer descendants of the first process)."""

    def __init__(self, process):
        if not WINDOWS:
            self.group = process.pid  # started with start_new_session: its own process group
            self.handle = None
            return
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self.handle = kernel32.CreateJobObjectW(None, None)
        if self.handle:
            kernel32.AssignProcessToJobObject(ctypes.c_void_p(self.handle), ctypes.c_void_p(int(process._handle)))

    def terminate(self):
        if not WINDOWS:
            self.signal_group(signal.SIGTERM)
            threading.Timer(3, self.signal_group, args=(signal.SIGKILL,)).start()
            return
        if self.handle:
            ctypes.windll.kernel32.TerminateJobObject(ctypes.c_void_p(self.handle), 1)

    def signal_group(self, number):
        try:
            os.killpg(self.group, number)
        except (ProcessLookupError, PermissionError):
            pass

    def close(self):
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self.handle))
            self.handle = None


def run_log_path(settings):
    """<saves folder>/last_run.log: the game's output, written by both launch paths."""
    return Path(settings.get('user_dir') or DATA_DIR / 'user') / 'last_run.log'


class _NullLog:
    """Stands in for the run log when its folder cannot be written: the game still starts."""
    def write(self, text):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def open_run_log(settings):
    """The run log, truncated, line-buffered; a null log when the folder is unusable."""
    path = run_log_path(settings)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return open(path, 'w', encoding='utf-8', buffering=1)
    except (OSError, ValueError):
        return _NullLog()


def open_play_log(settings):
    """The run log for the PLAY button. With 'close_on_play' the launcher exits 5 s after the
    start and the reader thread dies with it: a truncated file would replace the previous full
    log, so that mode leaves the file alone."""
    if settings.get('close_on_play'):
        return _NullLog()
    return open_run_log(settings)


def open_direct_log(settings):
    """last_run.log as a binary file for the game's own stdout (close_on_play); None when the
    folder cannot be written (the game then starts without a log)."""
    path = run_log_path(settings)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return open(path, 'wb')
    except (OSError, ValueError):
        return None


def _close_quietly(log):
    try:
        log.close()
    except (OSError, ValueError):
        pass


def play_without_window(settings):
    """--play: the game with the saved settings (shortcuts, Steam). Output goes to the console
    when there is one and to <saves folder>/last_run.log."""
    attach_stdio()
    with open_run_log(settings) as log:
        process = subprocess.Popen(run_command(), cwd=PORT_DIR, env=game_environment(settings),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        for raw in iter(process.stdout.readline, b''):
            text = raw.decode('utf-8', errors='replace')
            log.write(text)
            try:
                sys.stdout.write(text)
            except (OSError, ValueError):
                pass
        return process.wait()


def version_tuple(text):
    return tuple(int(number) for number in re.findall(r'\d+', text or ''))


def latest_release():
    """(version, zip URL, page URL) of the newest GitHub release."""
    if not UPDATES:
        raise OSError('this port has no releases yet')
    request = urllib.request.Request(RELEASES_API, headers={'Accept': 'application/vnd.github+json',
                                                            'User-Agent': 'issn-launcher'})
    with urllib.request.urlopen(request, timeout=15) as response:
        release = json.load(response)
    version = '.'.join(re.findall(r'\d+', release['tag_name']))
    url = next((asset['browser_download_url'] for asset in release.get('assets', [])
                if asset.get('name', '').lower().endswith('.zip')), None)
    return version, url, release.get('html_url') or RELEASES_PAGE


def install_update(target, wait_pid):
    """--install-update TARGET PID, run by the downloaded version from its temporary folder:
    waits for the old launcher to close, copies this version over TARGET (never the saves,
    settings or mods) and starts it."""
    target = Path(target)
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x00100000, False, int(wait_pid))  # SYNCHRONIZE
    if handle:
        kernel.WaitForSingleObject(handle, 60000)
        kernel.CloseHandle(handle)
    ignore = shutil.ignore_patterns(*USER_FILES)
    for attempt in range(30):
        try:
            shutil.copytree(PORT_DIR, target, dirs_exist_ok=True, ignore=ignore)
            break
        except OSError:  # a file still in use: the old launcher is closing
            time.sleep(1)
    else:
        ctypes.windll.user32.MessageBoxW(None, _(
            'Could not install the update. Download it from the releases page.',
            'Не удалось установить обновление. Скачайте его со страницы релизов.'), APP_NAME, 0x10)
        open_url(RELEASES_PAGE)
        return 1
    subprocess.Popen([str(target / 'inFAMOUS Second Son.exe')], cwd=str(target))
    return 0


def main():
    global LANG, FONT_LOADED
    args = sys.argv[1:]
    if args and args[0] in ('--run', '--script'):
        sys.exit(run_role(args))
    settings = {**APP_DEFAULTS, **load_json(CONFIG_FILE, {})}
    LANG = settings.get('ui_language') or windows_language()
    if not WINDOWS:
        integrate_desktop(settings.get('desktop_integration', True), os.environ.get('APPIMAGE'),
                          PORT_DIR / 'launcher' / 'issn.png', DATA_DIR)
    if WINDOWS and args[:1] == ['--install-update'] and len(args) == 3:
        sys.exit(install_update(args[1], args[2]))
    if WINDOWS and FROZEN and UPDATE_DIR not in PORT_DIR.parents:
        shutil.rmtree(UPDATE_DIR, ignore_errors=True)  # what a finished update left behind
    # Without a usable game folder there is nothing to play yet: open the launcher instead.
    if '--play' in args and ready_to_play(settings['game_dir'], settings.get('update_dir') or ''):
        sys.exit(play_without_window(settings))
    if WINDOWS:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on scaled displays
        except (AttributeError, OSError):
            pass
    FONT_LOADED = register_font(FONT_FILE)  # titles keep the usual serif without it
    import tkinter as tk
    from tkinter import filedialog, font, messagebox, ttk
    root = tk.Tk() if WINDOWS else tk.Tk(className='issn')
    if not WINDOWS:
        root.configure(cursor=ARROW)
        for window in ('Toplevel', 'Dialog', 'TkFDialog', 'TkChooseDir'):  # dialogs: tk_messageBox, file pickers
            root.option_add(f'*{window}.cursor', ARROW)
    families = font.families(root)
    pick_fonts(families)
    # Linux: the fontconfig file (register_font) only counts when Tk then lists the face.
    FONT_LOADED = (FONT_LOADED and WINDOWS) or HEADING_FAMILY in families
    Launcher(root, tk, ttk, filedialog, messagebox)
    root.mainloop()


if __name__ == '__main__':
    main()
