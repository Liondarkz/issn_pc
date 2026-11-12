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
import trophies  # noqa: E402  (scripts/)
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
VERSION = '0.3.7.2'
# This port's own test builds (never the God of War III port's: they must not be installed over
# it). The source repository is private; the builds are published in a public repository that
# holds only releases (tag windows-v<VERSION>, the zip from packaging/windows/package.sh).
RELEASES_REPO = 'Liondarkz/issn_pc'  # releases moved here with 0.3.0 (the repo is public)
RELEASES_API = f'https://api.github.com/repos/{RELEASES_REPO}/releases/latest'
RELEASES_PAGE = f'https://github.com/{RELEASES_REPO}/releases'
# The What's new page: the latest releases and their notes (the GitHub release text).
RELEASES_LIST = f'https://api.github.com/repos/{RELEASES_REPO}/releases?per_page=8'
UPDATES = bool(RELEASES_API)
# issn: who makes the port and where to follow it (Advanced > About, and the Home page's corner).
# AUTHOR_LINKS: (label, url); the social networks are added as the author gives them.
# SUPPORT_LINKS: donation pages; the 'Support the project' row shows only when one is set.
AUTHOR = 'Liondarkz'
AUTHOR_LINKS = [('GitHub', 'https://github.com/Liondarkz')]
# The port's Discord server (opened 10-29): Home page link and Advanced > About.
DISCORD_URL = 'https://discord.gg/y82HQ5uTQg'
SUPPORT_LINKS = [('Ko-fi', 'https://ko-fi.com/liondarkz'),  # card or PayPal, any country
                 ('GitHub Sponsors', 'https://github.com/sponsors/Liondarkz')]
# Pix (Brazil): his random key; the QR code in launcher/pix_qr.png holds pix_payload() (static BR
# Code; the bank shows the key holder's own name from the key, whatever the payload's name says).
PIX_KEY = '5db07608-b610-4512-a416-2c8ddd6668e5'
PIX_NAME, PIX_CITY = 'LIONDARKZ', 'BRASIL'
PIX_QR = PORT_DIR / 'launcher' / 'pix_qr.png'
CREDITS = ('axylpup (inFAMOUS Second Son shadPS4 build), HayatoG (iss_pc), AYOUB1080p (shadPS4 '
           'performance build), the shadPS4 team, and every tester who sent a log')
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
import issn_hardware  # noqa: E402
import issn_downloads  # noqa: E402
import issn_help  # noqa: E402
import issn_theme  # noqa: E402
import issn_themes  # noqa: E402
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
    FONT_FAMILIES.update(available)
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


def pix_payload(key=None, name=None, city=None):
    """The 'Pix copia e cola' text (static BR Code, EMV QRCPS) of a Pix key, any amount."""
    def field(tag, value):
        return f'{tag}{len(value):02d}{value}'
    body = (field('00', '01') + field('26', field('00', 'br.gov.bcb.pix') + field('01', key or PIX_KEY)) +
            field('52', '0000') + field('53', '986') + field('58', 'BR') + field('59', (name or PIX_NAME)[:25]) +
            field('60', (city or PIX_CITY)[:15]) + field('62', field('05', '***')) + '6304')
    crc = 0xFFFF  # CRC-16/CCITT-FALSE over everything up to and with '6304'
    for byte in body.encode():
        crc ^= byte << 8
        for _bit in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return body + f'{crc:04X}'


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
             'stale_readback', 'motion_blur', 'chromatic_aberration', 'trophy_note', 'paper_trail'}
# The graphics keys are also in the in-game menu (Insert / R3+L2); run.py reads the startup ones.
INI_DEFAULTS = {'upscaler': 'fsr3', 'preset': '1', 'trophy_note': '1', 'paper_trail': '0', 'addcont_dir': '',
                'object_motion': '1', 'show_fps': '1', 'output_res': '1920x1080',
                'live_resolution': 'auto', 'display_mode': 'windowed', 'vsync': '1', 'fps_limit': '0',
                'async_shaders': '1', 'deferred_readback': '1', 'stale_readback': '1',
                'render_resolution': 'native', 'engine_fps': '120',
                'frames_queued': '1', 'cheat_very_hard': '0', 'cheat_double_jumps': '0',
                'mouse_sensitivity': '1.00', 'pad_sensitivity': '1.00',
                'motion_blur': '1', 'chromatic_aberration': '1', 'sharpening': '0'}
DISPLAY_MODES = [('windowed', ('Windowed',)), ('borderless', ('Borderless',)), ('fullscreen', ('Fullscreen',))]
# issn: frame pacing on screens without G-Sync / FreeSync (axylpup's 1.1 "Fifo (half refresh)",
# vk_swapchain.cpp): every game frame is presented N times with V-Sync, so each stays exactly N
# screen refreshes (72 FPS on 144 Hz, 120 on 240 Hz) instead of some one and some two (judder).
FRAME_PACING = [('', ('Normal: a new frame at every refresh it is ready (default)',)),
                ('2', ('Half the refresh rate, even (72 FPS on 144 Hz, 120 on 240 Hz)',)),
                ('3', ('A third of the refresh rate, even (48 FPS on 144 Hz, 80 on 240 Hz)',))]
FPS_LIMITS = [('30', ('30',)), ('40', ('40',)), ('45', ('45',)), ('60', ('60',)), ('120', ('120',)), ('240', ('240',)),
              ('0', ('Unlimited',))]
# The sidebar's pages, top to bottom, and the methods that refresh a page's lists when it is shown.
# issn 11-05 (the launcher's new layout, as bbport's): the pages every player needs, then the expert's,
# shown under "Advanced view" only.
PAGES = ('home', 'picture', 'controls', 'game', 'trophies', 'news', 'customize', 'help',
         'graphics', 'files', 'pc', 'advanced')
EXPERT_PAGES = ('graphics', 'files', 'pc', 'advanced')
PAGE_REFRESH = {'home': ('refresh_status',), 'news': ('refresh_news',), 'help': ('run_help',),
                'files': ('refresh_lists', 'refresh_game_patches'), 'trophies': ('refresh_trophies',)}
APP_DEFAULTS = {'ui_language': '', 'game_dir': os.environ.get('ISSN_GAME_DIR', str(PORT_DIR.parent / 'CUSA00004') if WINDOWS else ''), 'update_dir': '', 'experimental_upscaler': False,
                'video_backend': 'auto', 'gamemode': False, 'mangohud': False, 'desktop_integration': True, 'user_dir': '',
                'mods_dir': '', 'mods_enabled': True, 'patches_dir': '', 'language': '1', 'game_language': '', 'pad_emulation': '', 'spray_stick': '', 'stick_deadzone': '', 'skip_intro': False, 'save_backups': True, 'vram_limit': '', 'frame_pacing': '', 'frame_gen': False, 'seed_any_profile': False,
                'player_name': '', 'hdr': False, 'draw_pipe': '', 'readbacks': '', 'no_msaa': '', 'stream_memory': 'cached',
                'gpu_id': '', 'background_warmup': True, 'parallel_warmup': True, 'pipeline_binaries': False,
                'perf_diag': False, 'frame_capture_key': False, 'frame_stats': False, 'gpu_profile': False,
                'vk_validation': False, 'close_on_play': False,
                'check_updates': False, 'game_patches': {}, 'controls': {}, 'pad_style': 'playstation',
                'glyph_style': 'off', 'compute_priority': True, 'ordered_readback': False, 'draw_distance': '',
                'perf_mode': '', 'render_height': '', 'upscale_quality': '', 'aniso': '', 'shader_compile': '', 'screen_shape': '',
                'anti_aliasing': 'game', 'optiscaler_output': 'fsr22', 'expert_view': False,
                'launcher_theme': issn_themes.DEFAULT, 'launcher_fx': 'full', 'launcher_goal': '',
                'new_launcher': False}

# PS4 button, default key, default gamepad button (SDL names; src/runtime_pad.c). L2/R2 come
# from the analog triggers and the left touchpad click (photo mode) from L1 + the right click's
# button; neither is remapped on the gamepad.
# issn: the keyboard layout of axylpup's build (runtime_pad.c key_map); L2 / R2 are also the
# right / left mouse buttons.
CONTROLS = [('cross', 'Space', 'a'), ('circle', 'Left Shift', 'b'), ('square', 'C', 'x'),
            ('triangle', 'F', 'y'), ('l1', 'Q', 'leftshoulder'), ('r1', 'E', 'rightshoulder'),
            ('l2', '1', None), ('r2', '3', None), ('l3', 'U', 'leftstick'),
            ('r3', 'O', 'rightstick'), ('options', 'Escape', 'start'),
            ('touchpad_left', 'G', None), ('touchpad_right', 'R', 'back'),
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


# issn: the button icons of the game's prompts (scripts/infamous_glyphs.py; idea of u/humonculus87).
GLYPH_STYLES = [('off', ('PlayStation, the game\'s own (default)',)),
                ('auto', ('Automatic: keyboard and mouse, or the connected controller',)),
                ('keyboard', ('Keyboard and mouse',)), ('xbox', ('Xbox',))]


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


def ini_key_changes(ini):
    """issn.ini key_map (keys changed in the game's own menu, Controls tab) as {button: key}."""
    changes = {}
    for entry in str(ini.get('key_map', '') or '').split(','):
        button, sep, key = entry.partition('=')
        if sep and button.strip() in {b for b, _k, _p in CONTROLS} and key.strip():
            changes[button.strip()] = key.strip()
    return changes


def merge_ini_keys(settings, ini):
    """The launcher's controls with the keys changed in the game's menu on top: the game writes
    them to issn.ini, the launcher shows them and passes them on (ISSN_KEY_MAP)."""
    changes = ini_key_changes(ini)
    if not changes:
        return settings
    controls = dict(settings.get('controls') or {})
    controls['key'] = {**(controls.get('key') or {}), **changes}
    return {**settings, 'controls': controls}


# issn-axy: launcher settings the game's menu (Image tab) changes for the next start. The game
# writes them to issn.ini as menu_<key> with the launcher's own values; the launcher takes them
# over when it opens and when the game closes, and writes them there when they change here.
MENU_KEYS = ('anti_aliasing', 'upscale_quality', 'aniso', 'draw_distance', 'frame_gen')


def menu_choices(key):
    return {value for value, _text in {'anti_aliasing': ANTI_ALIASING, 'upscale_quality': UPSCALE_QUALITY,
                                        'aniso': ANISO, 'draw_distance': DRAW_DISTANCE}[key]}


def menu_ini_value(settings, key):
    """A launcher setting as the game's menu_<key> in issn.ini."""
    if key == 'frame_gen':
        return '1' if settings.get(key) else '0'
    return str(settings.get(key) or '')


def menu_settings(ini):
    """The launcher settings the game's menu left in issn.ini (only valid values)."""
    found = {}
    for key in MENU_KEYS:
        name = f'menu_{key}'
        if name not in ini:
            continue
        value = str(ini[name]).strip()
        if key == 'frame_gen':
            if value in ('0', '1'):
                found[key] = value == '1'
        elif value in menu_choices(key):
            found[key] = value
    return found


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
# issn: the spray can's right-stick aim speed, degrees a second at full push (runtime_pad.c,
# ISSN_SPRAY_STICK; the runtime's default is 30).
SPRAY_STICK = [('', ('Normal (default)',)), ('15', ('Slow',)), ('60', ('Fast',)),
               ('115', ('Very fast (0.3.4 and earlier)',))]
# issn: the analog sticks' dead zone (runtime_pad.c, ISSN_STICK_DEADZONE; the runtime's default
# is 0.05). A worn stick that rests off center drifts in game (tester, Xbox One S, 10-29).
# issn 11-04: ISSN_VRAM_LIMIT_MB, the video memory the texture clean-up keeps the game under (GitHub
# issue 32: a 6 GB laptop card ran out and lost the device). '' = what the driver allows.
VRAM_LIMITS = [('', ('Automatic (what the driver allows)',)), ('3584', ('3.5 GB (4 GB cards)',)),
               ('5120', ('5 GB (6 GB cards)',)), ('6656', ('6.5 GB (8 GB cards)',)),
               ('9728', ('9.5 GB (10-12 GB cards)',))]
STICK_DEADZONE = [('', ('Small: 5% (default)',)), ('0.10', ('Medium: 10%',)), ('0.15', ('Large: 15%',)),
                  ('0.20', ('Very large: 20% (worn sticks)',))]
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
# The old Effects choice (Light '1' / Off '0') is gone in 0.3.5.1: the CPU then reads the game's
# exposure metering stale and the picture can stay dark at night (tester, RTX 5060). A saved
# Light / Off is ignored; ISSN_READBACKS set by hand in the environment still applies.
# ISSN_LOD_SCALE: how far detail is drawn in the city ('' = the game profile's 1.0, the game's own).
DRAW_DISTANCE = [('', ('Original (default)',)), ('0.6', ('Short (faster)',)), ('1.5', ('Long (less pop-in)',)),
                 ('2.0', ('Very long (least pop-in, slower)',))]
# ISSN_PERF_MODE (axylpup's 1.1 performance mode, gpu/shim/issn_view.cpp): the game's own lower
# settings for the open city: shorter LOD, one shadow cascade less, lower-resolution particles,
# fewer high-resolution textures; Maximum also halves the pedestrians.
PERF_MODES = [('', ('Off: full quality (default)',)), ('low', ('Balanced: about 13% more FPS in the city',)),
              ('office', ('Maximum: about 19% more FPS, fewer pedestrians',))]
# issn: the two halves of the old Internal resolution, apart (tester request 10-27). RESOLUTIONS:
# the lines the game draws (ISSN_RENDER_HEIGHT; the width follows the screen shape, 16:9 shown).
RESOLUTIONS = [('600', ('1064 × 600 (like 800 × 600, the fastest)',)), ('720', ('1280 × 720 (720p)',)),
               ('768', ('1368 × 768',)), ('900', ('1600 × 900',)),
               ('', ("1920 × 1080 (1080p, the game's own, default)",)), ('1440', ('2560 × 1440 (2K)',)),
               ('1800', ('3200 × 1800',)), ('2160', ('3840 × 2160 (4K)',)),
               # issn 10-28 (testers): above 4K, experimental; on a smaller screen it is supersampling.
               ('2880', ('5120 × 2880 (5K, experimental: 16 GB of RAM or more)',)),
               ('4320', ('7680 × 4320 (8K, experimental: 32 GB of RAM, 16 GB of VRAM)',))]
# UPSCALE_QUALITY: with FSR 4 or DLSS, the game draws at the window's size divided by this and the
# upscaler rebuilds the picture at the window size (ISSN_UPSCALE_FACTOR); it replaces Resolution.
# issn: anisotropic filtering forced on the game's textures (ISSN_ANISO, texture_cache/sampler.cpp).
ANISO = [('', ("The game's own (default)",)), ('4', ('4x',)), ('8', ('8x',)), ('16', ('16x (sharpest)',))]
UPSCALE_QUALITY = [('', ('Off: the game draws at the Resolution above',)), ('1.5', ('Quality (window ÷ 1.5)',)),
                   ('1.7', ('Balanced (window ÷ 1.7)',)), ('2', ('Performance (window ÷ 2)',)),
                   ('3', ('Ultra Performance (window ÷ 3)',))]


def migrate_settings(settings):
    """Settings of older launchers in today's names: render_scale (percent of 1080 or 'hNNNN')
    becomes render_height."""
    settings = dict(settings)
    old = str(settings.pop('render_scale', '') or '')
    if old and 'render_height' not in settings:
        try:
            lines = int(old[1:]) if old.startswith('h') else round(1080 * int(old) / 100)
        except ValueError:
            lines = 1080
        heights = [int(value) for value, _text in RESOLUTIONS if value] + [1080]
        nearest = min(heights, key=lambda h: abs(h - lines))
        settings['render_height'] = '' if nearest == 1080 else str(nearest)
    # 0.3.6: Options (Start) moved from Return to Escape for everyone (user 10-29); a Return saved
    # by an older launcher was its default, not a choice.
    controls = settings.get('controls')
    if isinstance(controls, dict) and isinstance(controls.get('key'), dict):
        if str(controls['key'].get('options', '')).strip().casefold() in ('return', 'enter'):
            keys = dict(controls['key'])
            keys.pop('options')
            settings['controls'] = {**controls, 'key': keys}
    return settings


# ISSN_ASPECT (gpu/shim/issn_game_patches.cpp, axylpup's 1.1 screen shapes): '' = the game window's
# shape (fullscreen: the monitor's), off = the game's 16:9, or a fixed shape.
SCREEN_SHAPES = [('', ('Automatic: the shape of the window / monitor (default)',)), ('off', ('16:9, the game\'s own',)),
                 ('21:9', ('21:9 ultrawide',)), ('32:9', ('32:9 super ultrawide',)), ('16:10', ('16:10',))]
# SHADPS4_ASYNC_PIPELINES (the game profile's default 1): a new graphics shader compiles on
# background threads and what it draws is skipped until it is ready, instead of the frame waiting.
SHADER_COMPILE = [('', ('In the background: no stutter, new things can appear a moment later (default)',)),
                  ('frame', ('In the frame: the original, the game stutters while new shaders compile',))]
# issn-axy: what replaces the game's own temporal anti-aliasing (SMAA T2x). FSR 4 ships with the
# port (third_party/fsr4); DLSS needs NVIDIA's Streamline files in dlss_folder(), XeSS Intel's
# libxess.dll in xess_folder().
ANTI_ALIASING = [('game', ("The game's own (SMAA T2x)",)), ('fsr4', ('FSR 4 (AMD Radeon RX 7000 / 9000)',)),
                 ('xess', ('XeSS (Intel; also Radeon and GeForce)',)), ('dlss', ('DLSS (NVIDIA RTX)',)),
                 ('off', ('Off (no anti-aliasing)',))]
# NVIDIA's Streamline (DLSS) and Intel's libxess.dll are Windows only: on Linux they are not offered.
if not WINDOWS:
    ANTI_ALIASING = [option for option in ANTI_ALIASING if option[0] not in ('dlss', 'xess')]
DLSS_FILES = ('sl.interposer.dll', 'sl.common.dll', 'sl.dlss.dll', 'sl.pcl.dll', 'nvngx_dlss.dll')
STREAMLINE_PAGE = 'https://github.com/NVIDIA-RTX/Streamline/releases/tag/v2.14.16'
XESS_FILES = ('libxess.dll',)
XESS_PAGE = 'https://github.com/intel/xess/releases'
# What the launcher downloads for XeSS / DLSS (issn_downloads: the vendors' release zips).
UPSCALER_PACKAGES = {'xess': issn_downloads.XESS, 'dlss': issn_downloads.DLSS}
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


def screen_refresh_rate():
    """The main screen's refresh rate in Hz (60 when it cannot be read)."""
    if WINDOWS:
        try:
            import ctypes
            user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
            dc = user32.GetDC(0)
            try:
                hz = gdi32.GetDeviceCaps(dc, 116)  # VREFRESH
            finally:
                user32.ReleaseDC(0, dc)
            return hz if hz > 1 else 60
        except (AttributeError, OSError):
            return 60
    try:
        out = subprocess.run(['xrandr', '--current'], capture_output=True, text=True, timeout=3).stdout
        rates = re.findall(r'(\d+(?:\.\d+)?)\*', out)
        return round(float(rates[0])) if rates else 60
    except (OSError, subprocess.SubprocessError, ValueError):
        return 60


def upscaler_folder(key):
    return xess_folder() if key == 'xess' else dlss_folder()


def upscaler_missing(key):
    return issn_downloads.missing(UPSCALER_PACKAGES[key], upscaler_folder(key))


def xess_folder():
    """Where the GPU library looks for Intel's libxess.dll: xess beside issn-probe.exe."""
    return dlss_folder().parent / 'xess'


def xess_missing():
    """Intel's files not yet in xess_folder()."""
    folder = xess_folder()
    return [name for name in XESS_FILES if not (folder / name).is_file()]


# OptiScaler (optional, a separate program: issn_downloads): what it turns the port's XeSS input
# into, written as VulkanUpscaler in bin\OptiScaler.ini. FSR 2.2 is the one tested on the port
# (João, RX 9060 XT, 2026-11-04); FSR 4 goes through DX12 (RDNA 3/4); DLSS is offered on
# NVIDIA RTX only.
OPTISCALER_CHOICES = [('fsr4', ('FSR 4 (Radeon RX 7000 / 9000, RDNA 3/4)',)), ('fsr31', ('FSR 3.1',)),
                      ('xess', ('XeSS',)), ('dlss', ('DLSS (NVIDIA RTX)',)),
                      ('fsr22', ('FSR 2.2 (tested with the port)',))]


OPTISCALER_SHORT = {'fsr4': 'FSR 4', 'fsr31': 'FSR 3.1', 'xess': 'XeSS', 'dlss': 'DLSS', 'fsr22': 'FSR 2.2'}


def optiscaler_output_name(value):
    """A VulkanUpscaler value as the player knows it ('fsr31_12' -> 'FSR 4'); other values as they are."""
    for key, names in issn_downloads.OPTISCALER_OUTPUTS.items():
        if str(value or '').lower() in names:
            return OPTISCALER_SHORT[key]
    return value or '?'


def optiscaler_bin():
    """Where OptiScaler goes: beside issn-probe.exe, which imports WINMM.dll."""
    return dlss_folder().parent


def optiscaler_choices(pc):
    """The outputs for this graphics card (pc None: not checked yet). DLSS only on NVIDIA RTX;
    FSR 4 not on NVIDIA or Intel cards."""
    vendor = pc.vendor if pc is not None else ''
    return [(key, text) for key, text in OPTISCALER_CHOICES
            if not (key == 'dlss' and not (pc is not None and pc.dlss))
            and not (key == 'fsr4' and vendor in ('nvidia', 'intel'))]


# issn 11-05: sharpening strengths offered where there is no slider (the new launcher's list).
SHARPENING = [('0', ('Off',))] + [(str(p), (f'{p}%',)) for p in range(5, 101, 5)]


def sharpening_percent(value):
    try:
        return max(0, min(100, int(float(str(value).strip().rstrip('%') or 0))))
    except ValueError:
        return 0


def sync_optiscaler_sharpening():
    """Before a start: the issn.ini sharpening into OptiScaler.ini when OptiScaler is in bin\\
    (it sharpens instead of the port then; gpu issn_sharpen.h). Returns the Sharpness or None."""
    if not WINDOWS:
        return None
    try:
        return issn_downloads.set_sharpening(optiscaler_bin(), sharpening_percent(load_ini()[0].get('sharpening', 0)))
    except (OSError, ValueError):
        return None


def optiscaler_log_line(settings):
    """The run log's first line when bin\\winmm.dll is there ('' otherwise)."""
    if not WINDOWS:
        return ''
    try:
        state = issn_downloads.optiscaler_state(optiscaler_bin())
    except OSError:
        return ''
    if not state:
        return ''
    if state['foreign']:
        return 'OptiScaler: not installed, but bin\\winmm.dll is there (another program, loaded by the game)\n'
    sharpness = sync_optiscaler_sharpening()
    line = ('OptiScaler {} installed {}: bin\\winmm.dll, VulkanUpscaler={}, menu key {}, Sharpness {} (OptiScaler.ini)'
            .format(state['version'] or '(version unknown)', 'by hand' if state['by_hand'] else 'by the launcher',
                    state['upscaler'] or '?', state['menu_key'] or '?', sharpness or '?'))
    aa = settings.get('anti_aliasing', 'game')
    if aa != 'xess':
        line += f"; the port's anti-aliasing is {aa}, not XeSS: OptiScaler gets no input"
    return line + '\n'


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


FIRST_LIGHT_TITLES = ('CUSA00575', 'CUSA00897', 'CUSA01064')  # as game_profiles.INFAMOUS_FIRST_LIGHT

# issn 11-11: the games this launcher starts. Each keeps its own game and update folders
# (settings.json 'games'); picture, controls and the rest are shared. 'game_dir'/'update_dir' stay
# the chosen game's, so everything that reads them follows the choice.
GAMES = (('second_son', 'inFAMOUS Second Son'), ('first_light', 'inFAMOUS First Light'))
GAME_SLOT_KEYS = ('game_dir', 'update_dir', 'launcher_theme')
# The launcher theme a game starts with (it then keeps the player's choice): Fetch's neon.
GAME_THEMES = {'first_light': 'neon'}


def game_of_folder(folder, update=''):
    """'first_light' for a First Light dump in the folders, else 'second_son'."""
    try:
        title = game_sfo(Path(meta_folder(folder, update) or '.')).get('TITLE_ID')
    except (OSError, ValueError):
        title = None
    return 'first_light' if title in FIRST_LIGHT_TITLES else 'second_son'


def current_game(settings):
    """The chosen game: settings['current_game'], else the one in the game folder."""
    chosen = settings.get('current_game')
    if chosen in dict(GAMES):
        return chosen
    return game_of_folder(settings.get('game_dir') or '', settings.get('update_dir') or '')


def game_art(game_dir, update_dir=''):
    """The game's pic1.png for the Play page: the update's, else the game's, in sce_sys or in the
    Sc0 folder of a PKG extraction (a First Light update has none of its own); None without one."""
    for folder in (meta_folder(game_dir, update_dir), game_dir):
        if not folder:
            continue
        for path in (Path(folder) / 'sce_sys' / 'pic1.png', Path(folder).parent / 'Sc0' / 'pic1.png'):
            if path.is_file():
                return path
    return None


def open_game_art(path):
    """The Play page art as an RGB Pillow image, None without one. issn 11-11: Second Son's pic1.png
    is grey crows on near-black, which read as a dark empty page beside First Light's colour art;
    a dark picture is lifted into the smoke reds and the game's logo (the bright lettering of the
    icon0.png beside it, from the player's own dump) goes on the right."""
    if not path:
        return None
    try:
        from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat
        with Image.open(path) as source:
            image = source.convert('RGB')
    except (ImportError, OSError):
        return None
    try:
        if ImageStat.Stat(image.convert('L')).mean[0] >= 30:
            return image
        width, height = image.size
        grey = ImageOps.autocontrast(image.convert('L'), cutoff=1).point(lambda v: min(255, int(v * 1.15)))
        duo = ImageOps.colorize(grey, black='#07070b', mid='#5a1420', white='#ff4a5c', midpoint=150)
        glow = duo.filter(ImageFilter.GaussianBlur(max(1, width // 48)))
        image = ImageChops.screen(duo, ImageChops.multiply(glow, Image.new('RGB', image.size, (140, 140, 140))))
        icon = Path(path).parent / 'icon0.png'
        if icon.is_file():
            size = int(height * 0.5)
            with Image.open(icon) as source:
                logo = source.convert('RGB').resize((size, size), Image.LANCZOS)
            mask = logo.convert('L').point(lambda v: 0 if v < 120 else min(255, (v - 120) * 4))
            mask = mask.filter(ImageFilter.GaussianBlur(1))
            x, y = width - size - int(width * 0.05), int(height * 0.42 - size * 0.5)
            image.paste((10, 4, 6), (x + 6, y + 8), mask.filter(ImageFilter.GaussianBlur(10)))
            image.paste(logo, (x, y), mask)
    except (OSError, ValueError):
        pass
    return image


def game_picker_shown(settings):
    """issn 11-11: First Light is still raw, so the 0.3.7.2 release hides the picker; it shows only
    where First Light was already chosen (settings.json current_game / games, João's tests) or
    with ISSN_FIRST_LIGHT=1."""
    games = settings.get('games') or {}
    return os.environ.get('ISSN_FIRST_LIGHT') == '1' or settings.get('current_game') == 'first_light' \
        or 'first_light' in games


def addcont_summary(folder, game_folder=''):
    """One line for the Game page: the DLC the port finds in `folder` (content_profile.scan_addons)."""
    if not folder:
        return _('No DLC folder: the game runs without DLC.')
    try:
        import content_profile
        title = game_sfo(Path(game_folder or '.')).get('TITLE_ID') or 'CUSA00223'
        found, skipped = content_profile.scan_addons(folder, title)
    except (ImportError, OSError, ValueError) as error:
        return _('Could not read the DLC folder: {}').format(error)
    if not found:
        reason = skipped[0][1] if skipped else _('no DLC param.sfo there')
        return _('No DLC found ({}).').format(reason)
    return _('DLC found: {}').format(', '.join(a['name'] for a in found))


def switch_game(settings, game):
    """The settings with `game` chosen: the folders of the game left are kept under 'games', the
    chosen game's come back (empty the first time)."""
    if game not in dict(GAMES):
        raise ValueError(game)
    settings = dict(settings)
    games = {name: dict(slot) for name, slot in (settings.get('games') or {}).items() if isinstance(slot, dict)}
    games[current_game(settings)] = {key: settings.get(key) or (issn_themes.DEFAULT if key == 'launcher_theme' else '')
                                     for key in GAME_SLOT_KEYS}
    slot = games.get(game, {})
    for key in GAME_SLOT_KEYS:
        if key == 'launcher_theme':
            settings[key] = slot.get(key) or GAME_THEMES.get(game) or settings.get(key) or issn_themes.DEFAULT
        else:
            settings[key] = slot.get(key) or ''
    settings['games'], settings['current_game'] = games, game
    return settings


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
    # issn 11-11: inFAMOUS First Light is offered the same copy (scripts/game_profiles.py).
    if not game_profile(values.get('TITLE_ID'), title if isinstance(title, str) else None) \
            and values.get('TITLE_ID') not in FIRST_LIGHT_TITLES:
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
    for name in ('SHADPS4_DLSS', 'SHADPS4_DLSS_UPSCALE', 'ISSN_FSR4', 'ISSN_XESS', 'ISSN_AA_OFF'):
        env.pop(name, None)
    anti_aliasing = s.get('anti_aliasing', 'game')
    if anti_aliasing == 'off':
        env['ISSN_AA_OFF'] = '1'  # issn 11-04: the T2x resolve becomes a plain copy, no jitter
    elif anti_aliasing == 'fsr4':
        env['ISSN_FSR4'] = '1'
    elif anti_aliasing == 'xess' and WINDOWS:
        env['ISSN_XESS'] = '1'
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
    env['ISSN_PORT_VERSION'] = VERSION  # the shader loading screen shows it
    if str(s['player_name']).strip():
        env['ISSN_USER_NAME'] = str(s['player_name']).strip()
    if s['hdr']:
        env['ISSN_HDR'] = '1'
    # The saved UI choice wins over inherited diagnostic overrides, including when reverting.
    stream_memory = s.get('stream_memory', APP_DEFAULTS['stream_memory'])
    env['ISSN_STREAM_MEMORY'] = stream_memory if stream_memory in ('cached', 'host', 'device') else 'cached'
    env.pop('ISSN_VRAM_LIMIT_MB', None)
    if s.get('vram_limit') in {value for value, _text in VRAM_LIMITS if value}:
        env['ISSN_VRAM_LIMIT_MB'] = s['vram_limit']
    gpu_id = str(s.get('gpu_id', ''))
    env.pop('ISSN_GPU_ID', None)
    if gpu_id.isdigit():
        env['ISSN_GPU_ID'] = gpu_id
    # The old core's switches (draw pipe, compute priority, warm-up, pipeline binaries, ...) are
    # gone with it: settings saved by 0.2 keep their keys and nothing reads them.
    if s.get('draw_distance') in {value for value, _text in DRAW_DISTANCE if value}:
        env['ISSN_LOD_SCALE'] = str(s['draw_distance'])
    env.pop('ISSN_PERF_MODE', None)
    for name in ('ISSN_RENDER_SCALE', 'ISSN_RENDER_HEIGHT', 'ISSN_UPSCALE_FACTOR'):
        env.pop(name, None)
    s = migrate_settings(s)
    if s.get('render_height') in {value for value, _text in RESOLUTIONS if value}:
        env['ISSN_RENDER_HEIGHT'] = s['render_height']
    env.pop('ISSN_ANISO', None)
    if s.get('aniso') in {value for value, _text in ANISO if value}:
        env['ISSN_ANISO'] = s['aniso']
    if s.get('upscale_quality') in {value for value, _text in UPSCALE_QUALITY if value}:
        env['ISSN_UPSCALE_FACTOR'] = s['upscale_quality']
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
    # issn 0.3.6 (test): the shader seed's graphics stages on any GPU (vk_pipeline_serialization.cpp).
    env.pop('ISSN_SEED_ANY_PROFILE', None)
    if s.get('seed_any_profile'):
        env['ISSN_SEED_ANY_PROFILE'] = '1'
    # issn: even frame pacing: V-Sync with every frame shown N refreshes (vk_swapchain.cpp).
    env.pop('SHADPS4_VSYNC_DIVIDER', None)
    if s.get('frame_pacing') in {value for value, _text in FRAME_PACING if value}:
        env['SHADPS4_VSYNC_DIVIDER'] = s['frame_pacing']
    # issn: the start-up logos movie reads as missing (runtime_file.c).
    env.pop('ISSN_SKIP_INTRO', None)
    if s.get('skip_intro'):
        env['ISSN_SKIP_INTRO'] = '1'
    # issn: the save backup run.py makes before a start (scripts/save_backup.py); a player asked to
    # turn it off (Discord 11-01).
    env.pop('ISSN_SAVE_BACKUP', None)
    if not s.get('save_backups', True):
        env['ISSN_SAVE_BACKUP'] = '0'
    # issn-axy: FSR 3.1 frame generation (gpu issn_framegen.h); it needs FSR 4, XeSS or DLSS.
    env.pop('ISSN_FRAME_GEN', None)
    if s.get('frame_gen') and s.get('anti_aliasing', 'game') not in ('game', 'off'):
        env['ISSN_FRAME_GEN'] = '1'
    env.pop('ISSN_STICK_DEADZONE', None)
    if s.get('stick_deadzone') in {value for value, _text in STICK_DEADZONE if value}:
        env['ISSN_STICK_DEADZONE'] = s['stick_deadzone']
    env.pop('ISSN_SPRAY_STICK', None)
    if s.get('spray_stick') in {value for value, _text in SPRAY_STICK if value}:
        env['ISSN_SPRAY_STICK'] = s['spray_stick']
    for kind, name in (('key', 'ISSN_KEY_MAP'), ('pad', 'ISSN_PAD_MAP')):
        if value := control_map(s.get('controls', {}), kind):
            env[name] = value
    glyph_style = s.get('glyph_style')
    env.pop('ISSN_GLYPH_STYLE', None)
    if glyph_style in ('auto', 'keyboard', 'xbox', 'nintendo'):
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
NAV_ACTIVE, NAV_HOVER = '#16181f', '#111319'  # side bar rows: the page shown, under the mouse
# Segoe Fluent Icons / Segoe MDL2 Assets code points for the side bar (Windows only).
NAV_ICONS = {'home': '\ue768', 'picture': '\ue7f4', 'controls': '\ue7fc', 'game': '\ue8b7', 'trophies': '\ue734',
             'news': '\ue789', 'customize': '\ue790', 'help': '\ue897', 'graphics': '\ue9e9', 'files': '\uea86',
             'pc': '\ue977', 'advanced': '\ue713'}
FONT_FAMILIES = set()  # filled once the window exists (pick_fonts)
# issn_theme's colours (rounded widgets): fields, hovers and borders between PANEL and TEXT.
THEME_COLORS = {'bg': '#09090c', 'panel': '#101116', 'card': '#181a21', 'field': '#14161d', 'card_hover': '#1d2029',
                'hover': '#242836', 'border': '#262935', 'border_strong': '#323645', 'border_hover': '#454b60',
                'text': '#f2f2f4', 'muted': '#8c90a0', 'accent': '#e2263b', 'accent_hi': '#ff4359',
                'accent_dark': '#a8172a', 'neon': '#24d3f2'}
THEME_NAME, EMBER = issn_themes.DEFAULT, '#ff8a3d'
FX_LEVELS = [('full', ('Full',)), ('light', ('Light (less work for the PC)',)), ('off', ('Off',))]


def use_theme(name):
    """issn 11-05: a launcher theme's colours (issn_themes: smoke, neon, video, concrete) in the
    module's colour constants; the window is built (or rebuilt) after this."""
    global BG, PANEL, CARD, LINE, ACCENT, ACCENT_HI, NEON, NEON_PINK, HOVER, NAV_ACTIVE, NAV_HOVER
    global THEME_COLORS, THEME_NAME, EMBER
    THEME_NAME = issn_themes.known(name)
    c = issn_themes.colors(THEME_NAME)
    BG, PANEL, CARD, LINE = c['bg'], c['panel'], c['card'], c['line']
    ACCENT, ACCENT_HI, NEON, NEON_PINK, EMBER = c['accent'], c['accent_hi'], c['neon'], c['pink'], c['ember']
    HOVER, NAV_ACTIVE, NAV_HOVER = c['nav_hover'], c['nav_active'], c['nav_hover']
    THEME_COLORS = {key: c[key] for key in ('bg', 'panel', 'card', 'field', 'card_hover', 'hover', 'border',
                                            'border_strong', 'border_hover', 'text', 'muted', 'accent',
                                            'accent_hi', 'accent_dark', 'neon')}


DISCORD_BLUE = '#8c9bff'  # Discord's blurple, lightened for the dark page
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


def theme_backdrop(width, height, strength=1.0):
    """The Play page banner's still atmosphere for the launcher theme (issn_themes), as an RGBA
    layer: smoke and the neon streaks, neon light, TV lines or cracked concrete. BannerFx moves
    things over it."""
    from PIL import Image, ImageDraw, ImageFilter
    import random
    rng = random.Random(7)
    hex_rgb = lambda c: tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
    a = lambda value: max(0, min(255, round(value * strength)))
    layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    blur = max(6, width // 60)
    if THEME_NAME in ('smoke', 'neon'):
        streaks = ((ACCENT, 0.58, 0.10, 150), (NEON_PINK, 0.74, 0.03, 170), (NEON, 0.83, 0.02, 190),
                   (ACCENT, 0.95, 0.05, 90)) if THEME_NAME == 'smoke' else \
            ((NEON_PINK, 0.52, 0.025, 200), (NEON, 0.63, 0.012, 220), (NEON_PINK, 0.78, 0.05, 120),
             (NEON, 0.9, 0.02, 200), (EMBER, 0.97, 0.015, 150))
        for color, x, thickness, alpha in streaks:
            x0, slant, span = width * x, height * 0.55, width * thickness
            draw.polygon([(x0, 0), (x0 + span, 0), (x0 + span - slant, height), (x0 - slant, height)],
                         fill=hex_rgb(color) + (a(alpha),))
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
        glow = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        g = ImageDraw.Draw(glow)
        if THEME_NAME == 'smoke':  # smoke along the floor, lit red from below
            for _n in range(9):
                cx, cy, r = rng.uniform(0, width), height * rng.uniform(0.72, 1.05), rng.uniform(0.08, 0.2) * width
                g.ellipse((cx - r, cy - r * 0.6, cx + r, cy + r * 0.6), fill=(150, 146, 150, a(55)))
            g.rectangle((0, height * 0.82, width, height), fill=hex_rgb(ACCENT) + (a(60),))
        else:  # neon: a pink glow top right, a cyan one bottom left
            r = width * 0.3
            g.ellipse((width * 0.85 - r, -r * 0.8, width * 0.85 + r, r * 0.8), fill=hex_rgb(NEON_PINK) + (a(70),))
            g.ellipse((width * 0.25 - r, height - r * 0.6, width * 0.25 + r, height + r), fill=hex_rgb(NEON) + (a(50),))
        layer.alpha_composite(glow.filter(ImageFilter.GaussianBlur(blur * 5)))
    elif THEME_NAME == 'video':
        glow = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        r = width * 0.35
        ImageDraw.Draw(glow).ellipse((width * 0.8 - r, -r, width * 0.8 + r, r), fill=hex_rgb(ACCENT) + (a(90),))
        layer.alpha_composite(glow.filter(ImageFilter.GaussianBlur(blur * 5)))
        draw = ImageDraw.Draw(layer)
        for y in range(0, height, 3):  # TV lines
            draw.line((0, y, width, y), fill=hex_rgb(NEON) + (a(14),))
        for _n in range(14):  # a few broken pixels
            x, y = rng.uniform(width * 0.45, width), rng.uniform(0, height)
            w = rng.uniform(20, 140)
            draw.rectangle((x, y, x + w, y + rng.choice((2, 3, 5))),
                           fill=hex_rgb(rng.choice((NEON, NEON_PINK, ACCENT))) + (a(rng.randint(40, 110)),))
    else:  # concrete: grain, a warm light and cracks
        grain = Image.effect_noise((width, height), 40).convert('L')
        tone = Image.new('RGBA', (width, height), hex_rgb('#b9b3a8') + (0,))
        tone.putalpha(grain.point(lambda v: a(max(0, v - 120) * 0.35)))
        layer.alpha_composite(tone)
        glow = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        r = width * 0.32
        ImageDraw.Draw(glow).ellipse((width * 0.82 - r, -r * 0.7, width * 0.82 + r, r * 0.7),
                                     fill=hex_rgb(ACCENT) + (a(55),))
        layer.alpha_composite(glow.filter(ImageFilter.GaussianBlur(blur * 5)))
        draw = ImageDraw.Draw(layer)
        for start in ((0.66, 0.0), (0.9, 0.18), (0.55, 1.0)):
            x, y = width * start[0], height * start[1]
            points = [(x, y)]
            for _n in range(6):
                x += rng.uniform(-0.04, 0.05) * width
                y += (0.13 if start[1] < 0.5 else -0.12) * height
                points.append((x, y))
            draw.line(points, fill=hex_rgb(ACCENT) + (a(110),), width=max(1, width // 700))
    return layer


def hero_image(art, width, height):
    """The home page's background: the dump's art scaled to cover the page, darkened towards the
    title in the lower left; without a dump, smoke and neon streaks on black. None without Pillow."""
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageTk
    except ImportError:
        return None
    width, height = max(width, 1), max(height, 1)
    hex_rgb = lambda c: tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
    image = open_game_art(art) if art is not None else None
    has_art = image is not None
    if image is not None:
        scale = max(width / image.width, height / image.height)
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.LANCZOS)
        left, top = (image.width - width) // 2, max(0, round((image.height - height) * 0.35))
        image = image.crop((left, top, left + width, top + height)).convert('RGBA')
    else:
        image = Image.new('RGBA', (width, height), hex_rgb(BG) + (255,))
    # issn 11-05: the theme's own atmosphere; over the dump's art it is lighter.
    image.alpha_composite(theme_backdrop(width, height, 0.55 if has_art else 1.0))
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


class BannerFx:
    """issn 11-05: what moves over the Play page's banner, by launcher theme (issn_themes): light
    running down the neon streaks, embers rising from the smoke, a TV scan band and glitches,
    concrete dust. Plain canvas items (cheap); it waits while another page is shown, the window
    is minimized or the game runs, and Customize > Effects can make it lighter or turn it off."""

    def __init__(self, app, canvas):
        import random
        self.app, self.canvas, self.rng = app, canvas, random.Random()
        self.items, self.w, self.h, self.pending, self.t = [], 0, 0, None, 0

    def level(self):
        return self.app.app.get('launcher_fx', 'full') if not hasattr(self.app, 'vars') or \
            'launcher_fx' not in self.app.vars else self.app.var('launcher_fx', 'app').get()

    def reset(self, w, h):
        c = self.canvas
        c.delete('fx')
        self.items, self.w, self.h = [], w, h
        level = self.level()
        if level == 'off':
            return
        count = 1 if level == 'full' else 0.5
        make = {'neon': self.make_neon, 'smoke': self.make_embers, 'video': self.make_video,
                'concrete': self.make_dust}[THEME_NAME]
        make(count)
        if c.find_withtag('art'):
            c.tag_raise('fx', 'art')  # over the picture, under the titles and links
        if self.pending is None:  # on the root: its after list is the one the window cancels
            self.pending = self.app.root.after(40, self.tick)

    # --- the pieces -----------------------------------------------------------------------
    def blend(self, color, amount):
        """`color` toward the page's background (no alpha on a Tk canvas)."""
        return issn_theme.mix(color, BG, amount)

    def make_neon(self, count):
        for n in range(max(2, round(5 * count))):
            color = (NEON_PINK, NEON, NEON_PINK, NEON, EMBER)[n % 5]
            x = self.w * self.rng.uniform(0.45, 1.05)
            lines = [self.canvas.create_line(0, 0, 0, 0, fill=self.blend(color, f), width=wd, capstyle='round',
                                             tags=('fx',)) for f, wd in ((0.8, 9), (0.55, 5), (0.0, 2))]
            self.items.append({'kind': 'runner', 'lines': lines, 'x': x, 'y': self.rng.uniform(-self.h, self.h),
                               'speed': self.rng.uniform(5, 10), 'len': self.rng.uniform(70, 160)})

    def make_embers(self, count):
        for _n in range(max(6, round(26 * count))):
            item = self.canvas.create_oval(0, 0, 0, 0, outline='', fill=EMBER, tags=('fx',))
            self.items.append(self.new_ember({'kind': 'ember', 'id': item}, fresh=True))

    def new_ember(self, e, fresh=False):
        e.update(x=self.rng.uniform(0, self.w), y=self.h * (self.rng.uniform(0.3, 1.0) if fresh else 1.02),
                 vy=self.rng.uniform(1.0, 2.6), size=self.rng.uniform(1.5, 3.5), phase=self.rng.uniform(0, 6.3),
                 color=self.rng.choice((EMBER, ACCENT_HI, '#ffb23d')))
        return e

    def make_video(self, count):
        self.items.append({'kind': 'scan', 'y': 0.0, 'lines': [
            self.canvas.create_line(0, 0, 0, 0, fill=self.blend(NEON, f), tags=('fx',)) for f in (0.86, 0.78, 0.86)]})
        for _n in range(max(2, round(4 * count))):
            item = self.canvas.create_rectangle(0, 0, 0, 0, outline='', fill=NEON, state='hidden', tags=('fx',))
            self.items.append({'kind': 'glitch', 'id': item, 'left': 0})

    def make_dust(self, count):
        for _n in range(max(8, round(34 * count))):
            item = self.canvas.create_oval(0, 0, 0, 0, outline='', fill='#d8cdb9', tags=('fx',))
            self.items.append(self.new_speck({'kind': 'dust', 'id': item}, fresh=True))

    def new_speck(self, d, fresh=False):
        d.update(x=self.rng.uniform(-0.1, 0.9) * self.w, y=self.h * self.rng.uniform(0.2 if fresh else 0.85, 1.0),
                 vx=self.rng.uniform(0.3, 1.1), vy=-self.rng.uniform(0.2, 0.7), size=self.rng.uniform(1, 2.4),
                 shade=self.rng.uniform(0.25, 0.7))
        return d

    # --- the clock ------------------------------------------------------------------------
    def tick(self):
        c, app = self.canvas, self.app
        self.pending = None
        try:
            if not c.winfo_exists():
                return
        except Exception:  # noqa: BLE001 - the window is gone
            return
        level = self.level()
        if level == 'off' or not self.items:
            return
        idle = app.current_page != 'home' or app.root.state() == 'iconic' or app.process is not None
        if not idle:
            self.t += 1
            for item in self.items:
                getattr(self, 'move_' + item['kind'])(item)
        self.pending = app.root.after(500 if idle else (40 if level == 'full' else 80), self.tick)

    def move_runner(self, r):
        c, slant = self.canvas, 0.55  # the streaks' slope (theme_backdrop)
        r['y'] += r['speed']
        if r['y'] > self.h + r['len']:
            r['y'] = -r['len'] - self.rng.uniform(0, self.h)
            r['x'] = self.w * self.rng.uniform(0.45, 1.05)
        y0, y1 = r['y'] - r['len'], r['y']
        x0, x1 = r['x'] - y0 * slant, r['x'] - y1 * slant
        for line in r['lines']:
            c.coords(line, x0, y0, x1, y1)

    def move_ember(self, e):
        import math
        e['y'] -= e['vy']
        e['x'] += math.sin(self.t * 0.05 + e['phase']) * 0.6
        if e['y'] < self.h * 0.15:
            self.new_ember(e)
        rise = max(0.0, min(1.0, (self.h - e['y']) / (self.h * 0.85)))
        s = e['size']
        self.canvas.coords(e['id'], e['x'] - s, e['y'] - s, e['x'] + s, e['y'] + s)
        self.canvas.itemconfigure(e['id'], fill=self.blend(e['color'], min(0.92, rise)))

    def move_scan(self, scan):
        scan['y'] = (scan['y'] + self.h / 110) % (self.h + 40)
        for n, line in enumerate(scan['lines']):
            y = scan['y'] - 20 + n * 2
            self.canvas.coords(line, 0, y, self.w, y)

    def move_glitch(self, g):
        c = self.canvas
        if g['left'] > 0:
            g['left'] -= 1
            if g['left'] == 0:
                c.itemconfigure(g['id'], state='hidden')
            return
        if self.rng.random() < 0.012:
            x, y = self.rng.uniform(self.w * 0.35, self.w), self.rng.uniform(0, self.h)
            w, h = self.rng.uniform(60, 320), self.rng.choice((2, 3, 4, 6))
            c.coords(g['id'], x, y, x + w, y + h)
            c.itemconfigure(g['id'], state='normal', fill=self.rng.choice((NEON, NEON_PINK, ACCENT_HI)))
            g['left'] = self.rng.randint(2, 5)

    def move_dust(self, d):
        d['x'] += d['vx']
        d['y'] += d['vy']
        if d['x'] > self.w or d['y'] < self.h * 0.1:
            self.new_speck(d)
        s = d['size']
        self.canvas.coords(d['id'], d['x'] - s, d['y'] - s, d['x'] + s, d['y'] + s)
        self.canvas.itemconfigure(d['id'], fill=self.blend('#d8cdb9', d['shade']))


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


def clear_shader_cache(user_dir, port_dir=None):
    """Empties user_dir/cache but keeps the package's shader seed (shader-seed.txt) when it is the
    folder the package shipped (the default user folder inside the port): deleting it there lost it
    for good, and every shader then compiled while playing (11-07, an RTX 5090 player's log:
    "shader-seed.txt names issn-seed-0.3.7, which is missing", hitches in every fight). Returns the
    name of the folder kept, or ''."""
    import shutil
    port_dir = Path(port_dir or PORT_DIR)
    cache = Path(user_dir) / 'cache'
    try:
        seed = (port_dir / 'shader-seed.txt').read_text().strip()
    except OSError:
        seed = ''
    keep = ''
    if seed and not seed.startswith('.') and all(c.isalnum() or c in '-_.' for c in seed):
        shipped = port_dir / 'user' / 'cache' / seed
        try:
            if (cache / seed).resolve() == shipped.resolve() and (shipped / 'seed-stamp.bin').is_file():
                keep = seed
        except OSError:
            keep = ''
    if not cache.is_dir():
        return keep
    for entry in cache.iterdir():
        if keep and entry.name == keep:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            try:
                entry.unlink()
            except OSError:
                pass
    return keep


class Launcher:
    def __init__(self, root, tk, ttk, filedialog, messagebox):
        self.tk, self.ttk, self.filedialog, self.messagebox = tk, ttk, filedialog, messagebox
        self.root = root
        self.app = {**APP_DEFAULTS, **migrate_settings(load_json(CONFIG_FILE, {}))}
        self.ini, self.ini_lines = load_ini()
        self.app = merge_ini_keys(self.app, self.ini)
        self.app = {**self.app, **menu_settings(self.ini)}
        self.vars = {}
        self.process = self.job = None
        self.output = queue.Queue()
        self.banner_key = self.banner_photo = None
        self.ui_calls = queue.Queue()  # work for the Tk thread from helper threads
        self.upscaler_downloads = set()  # XeSS / DLSS files being fetched (download_upscaler)
        self.upscaler_tried = set()  # fetched once before Play this session
        self.mod_order, self.mod_vars, self.patch_vars = [], {}, {}
        self.pending_update = None  # (version, url, page) once offered
        root.title(f'{APP_NAME} (PC)')
        if sys.platform == 'win32':
            try:  # the taskbar shows this window's icon, not Python's
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('issnpc.launcher')
            except (AttributeError, OSError):
                pass
        use_theme(self.app.get('launcher_theme'))
        root.configure(bg=BG)
        self.dpi = root.winfo_fpixels('1i') / 96.0
        left, top, width, height = (0, 0, 0, 0) if WINDOWS else monitor_under_pointer(root)
        if width:
            # Linux reports 96 dpi everywhere: size the interface by the monitor it opens on.
            boost = max(1.0, min(width / 1920, height / 1080, 2.0)) * 1.25
            self.dpi *= boost
            root.tk.call('tk', 'scaling', root.winfo_fpixels('1i') / 72.0 * boost)
        window_width, window_height = self.px(1280), self.px(760)  # the side bar takes 232 px
        if width:
            window_width, window_height = min(window_width, int(width * 0.9)), min(window_height, int(height * 0.9))
            root.geometry(f'{window_width}x{window_height}+{left + (width - window_width) // 2}'
                          f'+{top + (height - window_height) // 2}')
        else:
            root.geometry(f'{window_width}x{window_height}')
        root.minsize(min(self.px(1100), window_width), min(self.px(660), window_height))
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
    def style(self):
        ttk = self.ttk
        s = ttk.Style(self.root)
        s.theme_use('clam')
        base = (UI_FONT, 10)
        # issn 0.3.6: rounded buttons, fields, switches and a slim scroll bar drawn by Pillow
        # (issn_theme), once: a rebuilt window keeps the elements and their images.
        if getattr(self, 'theme_name', None) != THEME_NAME:
            # issn 11-05: another theme draws its elements under a tag of its own (ttk keeps the old).
            self.theme_count = getattr(self, 'theme_count', 0) + 1
            tag = 'Issn' if self.theme_count == 1 else f'Issn{self.theme_count}'
            self.theme = issn_theme.Theme(self.root, self.dpi, THEME_COLORS, tag)
            self.theme.style(s, {'': PANEL, 'Card.': CARD, 'Bar.': BG}, (UI_FONT, heading_font(17, UI_FONT)))
            self.theme_name = THEME_NAME
        self.root.option_add('*TCombobox*Listbox.background', CARD)
        self.root.option_add('*TCombobox*Listbox.foreground', TEXT)
        self.root.option_add('*TCombobox*Listbox.selectBackground', ACCENT)
        self.root.option_add('*TCombobox*Listbox.font', base)
        self.root.option_add('*TCombobox*Listbox.borderWidth', 0)
        self.root.option_add('*TCombobox*Listbox.relief', 'flat')
        s.configure('.', background=PANEL, foreground=TEXT, fieldbackground=CARD, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, troughcolor=CARD, focuscolor=ACCENT, font=base)
        s.configure('TFrame', background=PANEL)
        s.configure('TLabel', background=PANEL, foreground=TEXT)
        s.configure('Muted.TLabel', background=PANEL, foreground=MUTED, font=(UI_FONT, 9))
        s.configure('Restart.TLabel', background=PANEL, foreground=ACCENT_HI, font=(UI_FONT, 8, 'bold'))
        s.configure('Section.TLabel', background=PANEL, foreground=TEXT, font=heading_font(19, UI_FONT))
        s.configure('Slash.TLabel', background=PANEL, foreground=ACCENT, font=heading_font(19, UI_FONT))
        s.configure('Tile.TLabel', background=PANEL, foreground=MUTED, font=heading_font(14, UI_FONT))
        s.configure('TRadiobutton', background=PANEL, foreground=TEXT, indicatorcolor=CARD)
        s.map('TRadiobutton', background=[('active', PANEL)], indicatorcolor=[('selected', ACCENT)])
        s.configure('Warning.TLabel', background='#2a1414', foreground='#e0a0a0', padding=(10, 6))
        s.configure('TSpinbox', foreground=TEXT, arrowcolor=MUTED, insertcolor=TEXT, padding=4)
        s.configure('Play.TButton', background=ACCENT, foreground='#f4ece0', font=(*heading_font(15), 'bold'),
                    padding=(36, 10), borderwidth=0)
        s.map('Play.TButton', background=[('active', ACCENT_HI), ('disabled', '#1c222e')],
              foreground=[('disabled', '#8a7a78')])
        s.configure('Horizontal.TProgressbar', background=ACCENT, troughcolor=CARD, bordercolor=LINE)

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

    def folder(self, parent, key, title, prompt, hint=None, on_change=None, store='app'):
        ttk = self.ttk
        var = self.var(key, store)
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

        def scroll_to(widget):  # brings a section of this page to the top
            canvas.update_idletasks()
            canvas.yview_moveto(max(0, widget.winfo_y() - self.px(8)) / max(1, inner.winfo_height()))
        outer.scroll_to = scroll_to
        self.pages[name] = outer
        return inner

    # ---- layout ------------------------------------------------------------------------------
    def build(self):
        tk, ttk = self.tk, self.ttk
        # issn 0.3.6: a side bar (the brand, the pages with their icons, the hints and links at the
        # bottom) instead of tabs across the top: seven pages fit, and the content gets the height.
        side = tk.Frame(self.root, bg=BG, width=self.px(232))
        side.pack(side='left', fill='y')
        side.pack_propagate(False)
        brand = tk.Frame(side, bg=BG)
        brand.pack(anchor='w', padx=self.px(24), pady=(self.px(20), self.px(18)))
        tk.Label(brand, text='INFAMOUS', bg=BG, fg=TEXT, font=heading_font(30, UI_FONT, every_language=True),
                 bd=0, pady=0).pack(anchor='w')
        tk.Label(brand, text=f'SECOND SON  /  PC  ·  v{VERSION}', bg=BG, fg=ACCENT_HI,
                 font=heading_font(12, UI_FONT, every_language=True), bd=0, pady=0).pack(anchor='w')
        self.nav, self.current_page = {}, None
        titles = {'home': _('Play'), 'picture': _('Picture & performance'), 'controls': _('Controls', 'Управление'),
                  'game': _('Game & saves'), 'trophies': _('Achievements'), 'news': _("What's new", 'Что нового'),
                  'customize': _('Customize'), 'help': _('Help', 'Помощь'), 'graphics': _('More graphics'),
                  'files': _('Patches & mods'), 'pc': _('Your PC'), 'advanced': _('Advanced', 'Дополнительно')}
        icon_font = ('Segoe Fluent Icons' if 'Segoe Fluent Icons' in FONT_FAMILIES else 'Segoe MDL2 Assets', 12)
        self.expert_rows = []
        for name in PAGES:
            if name == EXPERT_PAGES[0]:
                self.build_expert_switch(side)
            row = tk.Frame(side, bg=BG, cursor='hand2', height=self.px(40))
            row.pack(fill='x', padx=(0, self.px(14)), pady=1)
            row.pack_propagate(False)
            if name in EXPERT_PAGES:
                self.expert_rows.append(row)
            marker = tk.Frame(row, bg=BG, width=self.px(3))
            marker.pack(side='left', fill='y')
            widgets = [row]
            if WINDOWS and NAV_ICONS.get(name):
                icon = tk.Label(row, text=NAV_ICONS[name], bg=BG, fg=MUTED, font=icon_font, width=2)
                icon.pack(side='left', padx=(self.px(18), self.px(8)))
                widgets.append(icon)
            label = tk.Label(row, text=titles[name], bg=BG, fg=MUTED, font=(UI_FONT, 11), anchor='w')
            label.pack(side='left', padx=(0 if len(widgets) > 1 else self.px(22), 0), fill='x', expand=True)
            widgets.append(label)
            for widget in widgets:
                widget.bind('<Button-1>', lambda _e, n=name: self.show(n))
                widget.bind('<Enter>', lambda _e, n=name: self.paint_nav(n, hover=True))
                widget.bind('<Leave>', lambda _e, n=name: self.paint_nav(n, hover=False))
            self.nav[name] = (row, marker, widgets[1:])
        self.show_expert_rows()
        foot = tk.Frame(side, bg=BG)
        foot.pack(side='bottom', fill='x', padx=self.px(24), pady=self.px(18))
        self.side_note = tk.Label(foot, text=_("In the game: Insert or R3+L2\nopens the port's menu."),
                                  bg=BG, fg=MUTED, font=(UI_FONT, 9), justify='left', anchor='w')
        self.side_note.pack(anchor='w')
        main = tk.Frame(self.root, bg=PANEL)
        main.pack(side='left', fill='both', expand=True)
        # A thin neon line over the content: smoke red into neon cyan.
        line = tk.Frame(main, bg=BG, height=2)
        line.pack(fill='x')
        tk.Frame(line, bg=ACCENT, height=2).place(relx=0, rely=0, relwidth=0.62, relheight=1)
        tk.Frame(line, bg=NEON_PINK, height=2).place(relx=0.62, rely=0, relwidth=0.2, relheight=1)
        tk.Frame(line, bg=NEON, height=2).place(relx=0.82, rely=0, relwidth=0.18, relheight=1)
        # Notices (an available update) appear here, under the line.
        self.notice = tk.Frame(main, bg=BG)
        self.notice_anchor = line
        self.update_box = None

        right = tk.Frame(main, bg=PANEL)
        right.pack(side='top', fill='both', expand=True)
        bar = tk.Frame(right, bg=BG, height=self.px(78))
        bar.pack(fill='x', side='bottom')
        bar.pack_propagate(False)
        self.status = tk.Label(bar, text='', bg=BG, fg=MUTED, font=(UI_FONT, 10), anchor='w', justify='left')
        self.status.pack(side='left', padx=self.px(28))
        self.play_button = PlayButton(self, bar, self.play, self.px(230), self.px(54))
        self.play_button.configure(text=heading_text(_('PLAY', 'ИГРАТЬ')))
        self.play_button.pack(side='right', padx=(self.px(12), self.px(28)), pady=self.px(12))
        self.save_button = ttk.Button(bar, text=_('Save', 'Сохранить'), command=self.save, state='disabled',
                                      style='Bar.TButton')
        self.save_button.pack(side='right', pady=self.px(12))
        self.content = tk.Frame(right, bg=PANEL)
        self.content.pack(fill='both', expand=True)

        self.pages = {}
        self.build_home()
        self.build_picture()
        self.build_controls_page()
        self.build_game()
        self.build_trophies()
        self.build_news()
        self.build_customize()
        self.build_help()
        self.build_graphics()
        self.build_files()
        self.build_pc()
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

    def paint_nav(self, name, hover=False):
        """A side bar row: the page shown has the red marker and a lighter row; hover lights it."""
        row, marker, widgets = self.nav[name]
        current = name == self.current_page
        bg = NAV_ACTIVE if current else NAV_HOVER if hover else BG
        row.configure(bg=bg)
        marker.configure(bg=ACCENT if current else bg)
        for widget in widgets:
            widget.configure(bg=bg, fg=TEXT if current or hover else MUTED)

    def build_expert_switch(self, side):
        """issn 11-05: the line and the "Advanced view" switch between the player's pages and the
        expert's (graphics, patches and mods, the PC, advanced), as bbport's launcher."""
        tk = self.tk
        tk.Frame(side, bg=LINE, height=1).pack(fill='x', padx=(self.px(24), self.px(24)), pady=(self.px(10), self.px(6)))
        holder = tk.Frame(side, bg=BG, cursor='hand2')
        holder.pack(fill='x', padx=(self.px(24), self.px(24)), pady=(0, self.px(4)))
        self.expert_label = tk.Label(holder, text='', bg=BG, fg=MUTED, font=(UI_FONT, 9, 'bold'), anchor='w')
        self.expert_label.pack(side='left', fill='x', expand=True)
        for widget in (holder, self.expert_label):
            widget.bind('<Button-1>', lambda _e: self.set_expert(not self.app.get('expert_view')))
        self.expert_holder = holder

    def set_expert(self, on):
        self.app['expert_view'] = bool(on)
        # Kept at once (a view, not a setting to Save): only this key changes in settings.json.
        if getattr(self, 'saved', None):
            self.saved['app']['expert_view'] = bool(on)
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(CONFIG_FILE, json.dumps({**load_json(CONFIG_FILE, {}), 'expert_view': bool(on)},
                                                 indent=2, ensure_ascii=False))
        except OSError:
            pass
        self.show_expert_rows()
        if not on and self.current_page in EXPERT_PAGES:
            self.show('home')

    def show_expert_rows(self):
        on = bool(self.app.get('expert_view'))
        self.expert_label.configure(text=('▾  ' if on else '▸  ') + _('Advanced view').upper())
        for row in self.expert_rows:
            if on:
                row.pack(fill='x', padx=(0, self.px(14)), pady=1)
            else:
                row.pack_forget()

    def show(self, name):
        if name in EXPERT_PAGES and not self.app.get('expert_view'):
            self.set_expert(True)
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill='both', expand=True)
        self.current_page = name
        for n in self.nav:
            self.paint_nav(n)
        for method in PAGE_REFRESH.get(name, ()):
            getattr(self, method)()

    def build_news(self):
        """What's new: the latest releases from GitHub with their notes, newest first; the newest
        one, when it is newer than this launcher, with its Update button."""
        f = self.scrolled_page('news', _("What's new", 'Что нового'),
                               _('The latest versions of the port and what changed in each.'))
        self.news_body = self.ttk.Frame(f)
        self.news_body.grid(row=self.next_row(f), column=0, columnspan=2, sticky='we')
        self.news_body.columnconfigure(0, weight=1)
        self.news_state = None  # None: not asked yet, 'loading', 'done'
        self.news_message(_('Open this page to load the notes.'))

    def news_message(self, text):
        for child in self.news_body.winfo_children():
            child.destroy()
        self.ttk.Label(self.news_body, text=text, style='Muted.TLabel', wraplength=self.px(680),
                       justify='left').grid(row=0, column=0, sticky='w', pady=(8, 0))

    def refresh_news(self):
        if self.news_state in ('loading', 'done') or not UPDATES:
            return
        self.news_state = 'loading'
        self.news_message(_('Loading the release notes from GitHub…'))

        def work():
            try:
                releases = release_list()
            except (OSError, ValueError, KeyError) as failure:
                def failed(error=failure):
                    self.news_state = None
                    self.news_message(_('Could not load the release notes: {}').format(error))
                self.ui_calls.put(failed)
                return

            def show():
                self.news_state = 'done'
                self.show_news(releases)
            self.ui_calls.put(show)
        threading.Thread(target=work, daemon=True).start()

    def show_news(self, releases):
        tk, ttk = self.tk, self.ttk
        for child in self.news_body.winfo_children():
            child.destroy()
        if not releases:
            self.news_message(_('No releases yet.'))
            return
        for index, release in enumerate(releases):
            box = tk.Frame(self.news_body, bg=CARD, padx=self.px(18), pady=self.px(14))
            box.grid(row=index, column=0, sticky='we', pady=(self.px(10), 0))
            box.columnconfigure(1, weight=1)
            colour = NEON if index == 0 else LINE
            tk.Frame(box, bg=colour, width=self.px(4)).grid(row=0, column=0, rowspan=4, sticky='nsw',
                                                             padx=(0, self.px(14)))
            top = tk.Frame(box, bg=CARD)
            top.grid(row=0, column=1, sticky='we')
            tk.Label(top, text=heading_text(release['title'].upper()), bg=CARD, fg=TEXT,
                     font=heading_font(20, UI_FONT), anchor='w', justify='left',
                     wraplength=self.px(560)).pack(side='left')
            newer = version_tuple(release['version']) > version_tuple(VERSION)
            same = version_tuple(release['version']) == version_tuple(VERSION)
            badge = _('New') if newer else _('Installed') if same else ''
            if badge:
                tk.Label(top, text=badge.upper(), bg=ACCENT if newer else LINE, fg=TEXT,
                         font=(UI_FONT, 8, 'bold'), padx=self.px(6)).pack(side='left', padx=self.px(10))
            tk.Label(box, text=release['date'], bg=CARD, fg=MUTED, font=(UI_FONT, 9), anchor='w').grid(
                row=1, column=1, sticky='w')
            tk.Label(box, text=release['text'], bg=CARD, fg=TEXT, font=(UI_FONT, 10), anchor='w',
                     justify='left', wraplength=self.px(680)).grid(row=2, column=1, sticky='w',
                                                                   pady=(self.px(8), 0))
            buttons = tk.Frame(box, bg=CARD)
            buttons.grid(row=3, column=1, sticky='w', pady=(self.px(10), 0))
            if index == 0 and newer and release['zip'] and WINDOWS:
                ttk.Button(buttons, text=_('Update', 'Обновить'), style='Card.Accent.TButton',
                           command=lambda r=release: self.install_update(r['version'], r['zip'], r['page'])).pack(
                    side='left', padx=(0, 6))
            ttk.Button(buttons, text=_('Open on GitHub'), style='Card.TButton',
                       command=lambda u=release['page']: open_url(u)).pack(side='left')

    def build_help(self):
        """Help: the installation and the last game log checked for the problems the launcher knows
        (issn_help), each with what it means and the steps that fix it."""
        tk, ttk = self.tk, self.ttk
        f = self.scrolled_page('help', _('Help', 'Помощь'),
                               _('Checks your installation and the last game log, and shows how to fix what it finds.'))
        bar = ttk.Frame(f)
        bar.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(4, 0))
        ttk.Button(bar, text=_('Check again'), command=self.run_help).pack(side='left')
        ttk.Button(bar, text=_('Save a bug report'), command=self.bug_report).pack(side='left', padx=6)
        ttk.Button(bar, text=_('Open the log'), command=lambda: self.open_path(run_log_path(self.app))).pack(side='left')
        ttk.Button(bar, text=_('Ask on the Discord'), command=lambda: open_url(DISCORD_URL)).pack(side='left', padx=6)
        self.help_body = ttk.Frame(f)
        self.help_body.grid(row=self.next_row(f), column=0, columnspan=2, sticky='we', pady=(6, 0))
        self.help_body.columnconfigure(0, weight=1)
        self.build_about(f)  # issn 11-05: the author, the credits and the support links end the Help page

    def run_help(self):
        try:
            log = run_log_path(self.app).read_text(errors='replace')
        except OSError:
            log = ''
        topics = issn_help.check(PORT_DIR, self.var('game_dir', 'app').get(), self.var('update_dir', 'app').get(), log)
        self.help_topics = topics
        self.show_help(topics, issn_help.crash_line(log) if 'crash' in topics else '')

    def show_help(self, topics, crash=''):
        tk = self.tk
        for child in self.help_body.winfo_children():
            child.destroy()
        if not topics:
            box = tk.Frame(self.help_body, bg=CARD, padx=self.px(18), pady=self.px(14))
            box.grid(row=0, column=0, sticky='we', pady=(self.px(10), 0))
            tk.Frame(box, bg=NEON, width=self.px(4)).pack(side='left', fill='y', padx=(0, self.px(14)))
            tk.Label(box, text=_('No problem found in the installation or the last game log.'), bg=CARD, fg=TEXT,
                     font=(UI_FONT, 11, 'bold'), anchor='w', justify='left').pack(anchor='w')
            tk.Label(box, text=_('Something still wrong? Save a bug report and send it on the Discord.'), bg=CARD,
                     fg=MUTED, font=(UI_FONT, 9), anchor='w', justify='left').pack(anchor='w', pady=(self.px(4), 0))
            return
        for index, topic in enumerate(topics):
            title, meaning, steps = issn_help.TOPICS[topic]
            box = tk.Frame(self.help_body, bg=CARD, padx=self.px(18), pady=self.px(14))
            box.grid(row=index, column=0, sticky='we', pady=(self.px(10), 0))
            box.columnconfigure(1, weight=1)
            tk.Frame(box, bg=ACCENT, width=self.px(4)).grid(row=0, column=0, rowspan=4, sticky='nsw',
                                                            padx=(0, self.px(14)))
            tk.Label(box, text=_(title), bg=CARD, fg=TEXT, font=(UI_FONT, 11, 'bold'), anchor='w',
                     justify='left').grid(row=0, column=1, sticky='w')
            tk.Label(box, text=_(meaning), bg=CARD, fg=MUTED, font=(UI_FONT, 10), anchor='w', justify='left',
                     wraplength=self.px(680)).grid(row=1, column=1, sticky='w', pady=(self.px(4), 0))
            if topic == 'crash' and crash:
                tk.Label(box, text=crash, bg=CARD, fg=ACCENT_HI, font=('Consolas' if WINDOWS else 'monospace', 9),
                         anchor='w', justify='left', wraplength=self.px(680)).grid(row=2, column=1, sticky='w',
                                                                                    pady=(self.px(6), 0))
            text = '\n'.join(f'{n}. {_(step)}' for n, step in enumerate(steps, 1))
            tk.Label(box, text=text, bg=CARD, fg=TEXT, font=(UI_FONT, 10), anchor='w', justify='left',
                     wraplength=self.px(680)).grid(row=3, column=1, sticky='w', pady=(self.px(8), 0))

    def build_home(self):
        tk, ttk = self.tk, self.ttk
        page = tk.Frame(self.content, bg=BG)
        self.pages['home'] = page
        quick = tk.Frame(page, bg=PANEL)
        quick.pack(fill='x', side='bottom')
        inner = ttk.Frame(quick, padding=(32, 12, 32, 14))
        inner.pack(fill='x')
        # issn 0.3.6 (user 10-30): Resolution on Home too; draw distance stays on Performance.
        items = ((_('Resolution'), 'render_height', 'app', RESOLUTIONS),
                 (_('Anti-aliasing'), 'anti_aliasing', 'app', ANTI_ALIASING),
                 (_('Frame rate limit'), 'fps_limit', 'ini', FPS_LIMITS),
                 (_('Display mode'), 'display_mode', 'ini', DISPLAY_MODES))
        for index, (title, key, store, options) in enumerate(items):
            cell = ttk.Frame(inner)
            cell.grid(row=0, column=index, sticky='we', padx=(0, self.px(18) if index < len(items) - 1 else 0))
            inner.columnconfigure(index, weight=1, uniform='tile')
            ttk.Label(cell, text=heading_text(title.upper()), style='Tile.TLabel').pack(anchor='w')
            self.choice(cell, key, store, options, width=12).pack(fill='x', pady=(2, 0))
        self.banner_key = self.banner_photo = None
        self.banner = tk.Canvas(page, bg=BG, highlightthickness=0, bd=0)
        self.banner.pack(fill='both', expand=True)
        self.banner.bind('<Configure>', lambda _e: self.draw_banner())
        self.banner_button = ttk.Button(self.banner, text=_('Choose the game folder'),
                                        command=lambda: self.show('game'))
        # issn 11-11: which game Play starts; each keeps its own folders (switch_game).
        self.game_pick = ttk.Combobox(self.banner, values=[name for _id, name in GAMES], state='readonly', width=24)
        self.game_pick.bind('<<ComboboxSelected>>', lambda _e: self.choose_game(GAMES[self.game_pick.current()][0]))

    def choose_game(self, game):
        """The game picker of the Play page: the current settings are saved, the chosen game's own
        folders come back, and the page shows that game."""
        if game == current_game(self.app):
            return
        if self.process is not None:
            self.messagebox.showinfo(APP_NAME, _('Close the game before choosing another one.'))
            self.banner_key = None
            self.draw_banner()
            return
        if not self.collect():
            return
        self.app = switch_game(self.app, game)
        try:
            atomic_write(CONFIG_FILE, json.dumps(self.app, indent=2, ensure_ascii=False))
        except OSError:
            self.messagebox.showerror(APP_NAME, _('Could not save all settings. Check write permissions and free disk space, then try again.'))
        for key in GAME_SLOT_KEYS:  # the Game page's folder fields and the theme
            self.var(key, 'app').set(self.app[key])
        if issn_themes.known(self.app.get('launcher_theme')) != THEME_NAME:
            self.theme_picked()  # the window again in the game's theme (the settings stay)
        else:
            self.banner_key = None
            self.draw_banner()
            self.refresh_status()
        if not self.app.get('game_dir'):
            self.show('game')

    def draw_banner(self):
        """The home page: the dump's own art (sce_sys/pic1.png) behind the game's name."""
        c = self.banner
        c.delete('all')
        w, h = max(c.winfo_width(), 400), max(c.winfo_height(), 200)
        folder = self.meta_dir()
        info = game_info(folder)
        art = game_art(self.var('game_dir', 'app').get(), self.var('update_dir', 'app').get())
        key = (str(art), w, h, THEME_NAME)
        if self.banner_key != key:
            self.banner_key, self.banner_photo = key, hero_image(art, w, h)
        if self.banner_photo:
            c.create_image(0, 0, image=self.banner_photo, anchor='nw', tags=('art',))
        x, base = self.px(40), h - self.px(34)
        title_face = heading_font(min(96, max(54, h // 4)), UI_FONT, every_language=True)
        game = current_game(self.app)
        self.game_pick.current([gid for gid, _name in GAMES].index(game))
        if game_picker_shown(self.app):
            c.create_window(w - self.px(24), self.px(24), window=self.game_pick, anchor='ne')
        c.create_text(x, base, text='FIRST LIGHT' if game == 'first_light' else 'SECOND SON', anchor='sw', fill=ACCENT_HI,
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
        # issn: the author, bottom right; a click opens his GitHub.
        credit = c.create_text(w - self.px(24), h - self.px(18), text=_('Made by {}').format(AUTHOR), anchor='se',
                               fill=MUTED, font=(UI_FONT, 10, 'bold'), activefill=NEON)
        c.tag_bind(credit, '<Button-1>', lambda _e: open_url(AUTHOR_LINKS[0][1]))
        # issn: and, left of it, the way to the support section (Pix, Ko-fi).
        support = c.create_text(c.bbox(credit)[0] - self.px(18), h - self.px(18), text='♥ ' + _('Support the project'),
                                anchor='se', fill=NEON_PINK, font=(UI_FONT, 10, 'bold'), activefill=TEXT)
        c.tag_bind(support, '<Button-1>', lambda _e: self.show_support())
        # issn: and the Discord server, left of that.
        discord = c.create_text(c.bbox(support)[0] - self.px(18), h - self.px(18), text='💬 Discord',
                                anchor='se', fill=DISCORD_BLUE, font=(UI_FONT, 10, 'bold'), activefill=TEXT)
        c.tag_bind(discord, '<Button-1>', lambda _e: open_url(DISCORD_URL))
        for item in (credit, support, discord):
            c.tag_bind(item, '<Enter>', lambda _e: c.configure(cursor='hand2'))
            c.tag_bind(item, '<Leave>', lambda _e: c.configure(cursor=''))
        if getattr(self, 'banner_fx', None) is None or self.banner_fx.canvas is not c:
            self.banner_fx = BannerFx(self, c)
        self.banner_fx.reset(w, h)

    def build_pc_check(self, f):
        """Your PC: the graphics card, processor and memory found here, what to expect in the city,
        and three goals that set the graphics for this PC (issn_hardware.settings_for)."""
        tk, ttk = self.tk, self.ttk
        self.section(f, _('Your PC'), top=4)
        card = tk.Frame(f, bg=CARD, padx=self.px(18), pady=self.px(14))
        card.grid(row=self.next_row(f), column=0, columnspan=2, sticky='we', pady=(self.px(8), 0))
        card.columnconfigure(1, weight=1)
        self.pc_lines = tk.Label(card, text=_('Looking at this PC…'), bg=CARD, fg=TEXT, font=(UI_FONT, 10),
                                 justify='left', anchor='w')
        self.pc_lines.grid(row=0, column=0, columnspan=2, sticky='w')
        self.pc_verdict = tk.Label(card, text='', bg=CARD, fg=NEON, font=(UI_FONT, 10, 'bold'), justify='left',
                                   anchor='w', wraplength=self.px(680))
        self.pc_verdict.grid(row=1, column=0, columnspan=2, sticky='w', pady=(self.px(8), 0))
        goals = tk.Frame(card, bg=CARD)
        goals.grid(row=2, column=0, columnspan=2, sticky='w', pady=(self.px(12), 0))
        tk.Label(goals, text=_('Set up the graphics for this PC:'), bg=CARD, fg=MUTED, font=(UI_FONT, 9)).pack(
            anchor='w', pady=(0, self.px(6)))
        # Recommended on a row of its own, the three goals under it (four in one row did not fit
        # the window in Portuguese).
        first = tk.Frame(goals, bg=CARD)
        first.pack(anchor='w')
        buttons = tk.Frame(goals, bg=CARD)
        buttons.pack(anchor='w', pady=(self.px(6), 0))
        self.pc_goal_buttons = []
        for goal, label in (('recommended', _('Recommended (best on most PCs)')),
                            ('handheld', _('Steam Deck / weak PC (40-45 FPS)')),
                            ('smooth', _('Smooth (up to 120 FPS)')), ('balanced', _('Balanced (60 FPS, sharpest)')),
                            ('quiet', _('Quiet (60 FPS, lighter on the card)'))):
            button = ttk.Button(first if goal in ('recommended', 'handheld') else buttons, text=label, state='disabled',
                                style='Card.Accent.TButton' if goal == 'recommended' else 'Card.TButton',
                                command=lambda g=goal: self.apply_goal(g))
            button.pack(side='left', padx=(0, self.px(6)))
            self.pc_goal_buttons.append(button)
        self.pc_applied = tk.Label(card, text='', bg=CARD, fg=MUTED, font=(UI_FONT, 9), justify='left', anchor='w',
                                   wraplength=self.px(680))
        self.pc_applied.grid(row=3, column=0, columnspan=2, sticky='w', pady=(self.px(8), 0))
        self.pc = None

        def work():
            try:
                found = issn_hardware.detect(list_gpus())
            except Exception:  # a detection problem never stops the launcher
                found = issn_hardware.Pc()
            self.ui_calls.put(lambda: self.show_pc(found))
        threading.Thread(target=work, daemon=True).start()

    def show_pc(self, pc):
        self.pc = pc
        self.fill_optiscaler_box()
        unknown = _('not recognised')
        lines = [_('Graphics card: {}').format(pc.gpu_name or unknown),
                 _('Processor: {}').format(pc.cpu_name or unknown),
                 _('Memory: {} GB').format(pc.ram_gb) if pc.ram_gb else _('Memory: {}').format(unknown)]
        self.pc_lines.configure(text='\n'.join(lines))
        fps = issn_hardware.expected_fps(pc)
        verdict = {'great': _('Runs it very well'), 'good': _('Runs it well'), 'minimum': _('Runs it, at lower settings'),
                   'below': _('Below what the game needs'), 'unknown': _('Not in the list yet')}[issn_hardware.tier(pc)]
        text = verdict
        if fps:
            text += ' · ' + _('about {} FPS in the open city at 1080p').format(fps)
        limit = issn_hardware.limit(pc)
        if limit:
            text += ' · ' + (_('the processor sets the limit') if limit == 'cpu' else _('the graphics card sets the limit'))
        if pc.deck:
            text += '\n' + _('Steam Deck found: "Steam Deck / weak PC" sets the game for it (40-45 FPS).')
        elif pc.integrated:
            text += '\n' + _('This is an integrated graphics chip: a graphics card is needed for this game.')
        if pc.ram_gb and pc.ram_gb < 16:
            text += '\n' + _('16 GB of memory or more is recommended.')
        self.pc_verdict.configure(text=text, fg=NEON if issn_hardware.tier(pc) in ('great', 'good') else ACCENT_HI)
        for button in self.pc_goal_buttons + getattr(self, 'quick_goal_buttons', []):
            try:
                button.configure(state='normal')
            except self.tk.TclError:  # a button of a window rebuilt since (theme, language)
                pass

    def apply_goal(self, goal):
        """Sets the graphics for the goal on this PC; Save keeps them."""
        if self.pc is None:
            return
        screen = self.root.winfo_screenheight()
        chosen, reasons = issn_hardware.settings_for(self.pc, goal, screen_height=max(screen, 1080),
                                                     refresh_hz=screen_refresh_rate())
        for store, values in (('app', chosen['app']), ('ini', chosen['ini'])):
            for key, value in values.items():
                if store == 'app' and key not in APP_DEFAULTS or store == 'ini' and key not in INI_DEFAULTS:
                    continue
                var = self.var(key, store)
                var.set(value == '1' if store == 'ini' and key in INI_FLAGS else value)
        app = chosen['app']
        resolution = next((_(*text) for value, text in RESOLUTIONS if value == app['render_height']), app['render_height'])
        notes = [_('Resolution: {}').format(resolution),
                 _('Frame rate limit: {}').format(next((_(*text) for value, text in FPS_LIMITS
                                                         if value == chosen['ini']['fps_limit']),
                                                        chosen['ini']['fps_limit'])),
                 _('Anti-aliasing: {}').format('FSR 4' if app['anti_aliasing'] == 'fsr4' else _('Off')
                                               if app['anti_aliasing'] == 'off' else _("the game's own")),
                 _('Performance mode: {}').format({'': _('off'), 'low': _('Balanced'), 'office': _('Maximum')}[app['perf_mode']]),
                 _('Texture filtering: {}x').format(app['aniso'])]
        if goal == 'handheld':
            notes.append(_('Draw distance: {}').format(_('Short (faster)')))
        if goal in ('recommended', 'handheld'):
            notes.append(_('Frame pacing: {}').format(
                next((_(*text) for value, text in FRAME_PACING if value == app['frame_pacing']), '')))
            notes.append(_('Shader compilation: in the background'))
        why = []
        for kind, value in reasons:
            if kind == 'paced':
                why.append(_('a {} Hz screen shows {} FPS evenly').format(*value))
            elif kind == 'cpu':
                why.append(_('the processor holds about {} FPS in the city').format(value))
            elif kind == 'ram':
                why.append(_('{} GB of memory').format(value))
            elif kind == 'vram':
                why.append(_('{} GB of video memory').format(value))
            elif kind == 'deck40':
                why.append(_('a 60 Hz screen: set the Steam Deck to 40 Hz (quick menu > Performance > Refresh '
                             'rate) so every frame shows for the same time'))
            elif kind == 'handheld':
                why.append(_('a 60 Hz screen holds 40 FPS evenly only at 40 Hz or with FreeSync / G-Sync'))
        text = _('Set: {}.').format(' · '.join(notes))
        if why:
            text += ' ' + _('Because: {}.').format(', '.join(why))
        text += ' ' + _('Press Save to keep it; every setting below can still be changed.')
        for label in (self.pc_applied, getattr(self, 'quick_applied', None)):
            try:
                if label is not None:
                    label.configure(text=text)
            except self.tk.TclError:
                pass

    def build_picture(self):
        """Picture & performance: the window, the size, the anti-aliasing, the frame rate and the game's
        own detail and effects - what a player changes (issn 11-05: Graphics and Performance joined)."""
        ttk = self.ttk
        f = self.scrolled_page('picture', _('Picture & performance'), _('How the game looks and how fast it runs.'))
        self.section(f, _('Quick setup'), top=4)
        quick = ttk.Frame(f)
        quick.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(6, 0))
        for goal, label in (('recommended', _('Recommended (best on most PCs)')),
                            ('handheld', _('Steam Deck / weak PC (40-45 FPS)'))):
            button = ttk.Button(quick, text=label, state='disabled',
                                style='Accent.TButton' if goal == 'recommended' else 'TButton',
                                command=lambda g=goal: self.apply_goal(g))
            button.pack(side='left', padx=(0, self.px(6)))
            self.quick_goal_buttons = getattr(self, 'quick_goal_buttons', []) + [button]
        self.note(f, _('Sets everything below for this PC; Save keeps it. Advanced view > Your PC has the other '
                       'goals and what the launcher found.'), top=4)
        self.quick_applied = ttk.Label(f, text='', style='Muted.TLabel', wraplength=self.px(680), justify='left')
        self.quick_applied.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(4, 0))
        self.section(f, _('Window', 'Окно'))
        self.build_display(f, section=False)
        self.row(f, _('Screen shape'), self.choice(f, 'screen_shape', 'app', SCREEN_SHAPES, width=44),
                 _('Ultrawide and other screens: the game fills the shape and sees more at the sides (or above and '
                   'below on 16:10); the HUD moves to the edges and movies keep their shape with bars. Automatic '
                   'follows the window, so play in fullscreen on an ultrawide monitor.'), restart=True)
        self.section(f, _('Resolution'))
        self.row(f, _('Resolution'), self.choice(f, 'render_height', 'app', RESOLUTIONS, width=44),
                 _('The size the game draws at. Lower is faster; higher is sharper and heavier (2K and 4K need '
                   'more memory, given by itself). The picture is then fitted to the window, and the HUD keeps '
                   'its layout. On wider screens the width grows with the shape.'), restart=True)
        self.row(f, _('FSR 4 / DLSS quality'), self.choice(f, 'upscale_quality', 'app', UPSCALE_QUALITY, width=44),
                 _('With FSR 4, XeSS or DLSS (Anti-aliasing below): the game draws smaller than the window and '
                   'the upscaler rebuilds a sharp picture at the window size. Quality looks closest to native, '
                   'Ultra Performance is the fastest. When not Off it decides the size instead of Resolution.'),
                 restart=True)
        self.row(f, _('Texture filtering'), self.choice(f, 'aniso', 'app', ANISO, width=44),
                 _('Anisotropic filtering: ground, roads and walls seen at an angle stay sharp in the distance. '
                   'Costs a little FPS on weak graphics cards; it does not change stutter.'), restart=True)
        self.section(f, _('Anti-aliasing'))
        self.row(f, _('Anti-aliasing'), self.choice(f, 'anti_aliasing', 'app', ANTI_ALIASING, width=44),
                 _("Replaces the game's own temporal anti-aliasing. FSR 4 (included) needs a Radeon RX 7000 or "
                   "9000; XeSS runs on most recent cards (Arc, Radeon RX 5000+, GeForce GTX 10+) with Intel's "
                   "file; DLSS needs an NVIDIA RTX card and NVIDIA's files (below). Without what they need the "
                   "game keeps its own.") if WINDOWS else
                 _("Replaces the game's own temporal anti-aliasing. FSR 4 (included) needs a Radeon RX 7000 or "
                   "9000 and a recent Mesa (RADV); without it the game keeps its own."), restart=True)
        self.note(f, _('Off: no anti-aliasing at all, the sharpest picture with jagged edges (no smearing in '
                       'motion).'), top=2)
        if WINDOWS:  # DLSS (Streamline) and XeSS: their vendors' files, fetched on demand
            self.vendor_state = {}
            for key, open_folder in (('xess', self.open_xess_folder), ('dlss', self.open_dlss_folder)):
                package = UPSCALER_PACKAGES[key]
                holder = ttk.Frame(f)
                holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(10, 0))
                ttk.Button(holder, text=_('Download {} now').format(package.name),
                           command=lambda k=key: self.download_upscaler(k)).pack(side='left')
                ttk.Button(holder, text=_('Open the {} folder').format(package.name),
                           command=open_folder).pack(side='left', padx=6)
                self.vendor_state[key] = ttk.Label(holder, text='', style='Muted.TLabel')
                self.vendor_state[key].pack(side='left', padx=10)
            self.dlss_state = self.vendor_state['dlss']
            self.xess_state = self.vendor_state['xess']
            self.note(f, _("XeSS and DLSS use Intel's and NVIDIA's own files, which the port may not include: the "
                           "launcher downloads them from Intel's and NVIDIA's GitHub releases (XeSS SDK 3.0.2, about "
                           "60 MB; Streamline 2.14.16, about 35 MB) when you pick one, and checks each file."), top=6)
            self.var('anti_aliasing', 'app').trace_add('write', lambda *_a: self.anti_aliasing_picked())
        self.section(f, _('Frame rate', 'Частота кадров'))
        self.row(f, _('Frame rate limit'), self.choice(f, 'fps_limit', 'ini', FPS_LIMITS),
                 _('The highest frame rate. The game keeps its speed at any frame rate; Unlimited runs as fast '
                   'as the PC allows.'), restart=True)
        self.row(f, _('Frame pacing'), self.choice(f, 'frame_pacing', 'app', FRAME_PACING, width=44),
                 _('For screens without G-Sync / FreeSync: when the game runs between half and the full '
                   'refresh rate, some frames stay on screen longer than others and motion judders even at a '
                   'high FPS. Half or a third shows every frame for the same time (V-Sync on). Pick the one '
                   'your PC holds in the heaviest places.'), restart=True)
        self.check(f, 'frame_gen', 'app', _('Frame generation (FSR 3.1, test)'),
                   _('Makes a frame between every two of the game: 60 FPS on the screen become 120. Needs FSR 4, '
                     'XeSS or DLSS as anti-aliasing (above). Adds a little input delay; best with a base of '
                     '60 FPS or more, and with Frame pacing at half on fast screens (72 becomes 144 on 144 Hz).'))
        self.section(f, _('Detail'))
        self.row(f, _('Draw distance'), self.choice(f, 'draw_distance', 'app', DRAW_DISTANCE),
                 _('How far buildings and details keep their full detail in the open city. Long and Very long '
                   'show less pop-in than the PS4 and cost some FPS.'), restart=True)
        self.row(f, _('Performance mode'), self.choice(f, 'perf_mode', 'app', PERF_MODES),
                 _('The game\'s own lower settings, from axylpup\'s 1.1: shorter detail distance, one shadow '
                   'level less, lighter particles and fewer high-resolution textures nearby. Figures are '
                   'from his PC in the heaviest city view.'), restart=True)
        # issn 11-04 (Discord poll): the game's own effects (gpu issn_postfx.h), also in the game's menu.
        self.section(f, _('Effects'))
        self.check(f, 'motion_blur', 'ini', _('Motion blur'),
                   _('The blur when the camera or Delsin moves fast. Off gives a sharp picture in motion; '
                     'it also changes at once in the game (Insert > Image).'))
        self.check(f, 'chromatic_aberration', 'ini', _('Chromatic aberration'),
                   _('The red and blue fringes toward the edges of the screen. Turning it off helps players '
                     'who feel sick with it; it also changes at once in the game (Insert > Image).'))
        # issn 11-05: sharpening 0-100 % (gpu issn_sharpen.h), also a slider in the game.
        spin = self.ttk.Spinbox(f, from_=0, to=100, increment=5, width=8, textvariable=self.var('sharpening', 'ini'))
        self.row(f, _('Sharpening (%)'), spin,
                 _('Makes the picture crisper; 0 is off, 30-50 is a good start. Applied once: by FSR 4 or '
                   'OptiScaler when they are in use, otherwise by the port before the HUD. Also a slider in '
                   'the game (Insert > Image).'))
        self.section(f, _('Overlay'))
        self.check(f, 'show_fps', 'ini', _('Show performance overlay'),
                   _('Items, size and corner: in-game menu (Insert / R3+L2) > Overlay. F11 hides it in the game.'))
        self.note(f, _('Screenshots: F12 (saved in the user\\screenshots folder).') if WINDOWS else
                  _('Screenshots: F12 (saved in the user/screenshots folder).'), top=10)
        if WINDOWS:
            self.refresh_dlss_state()

    # ---- OptiScaler (optional, a separate program; issn_downloads does the files) -------------
    def build_optiscaler(self, f):
        """The OptiScaler section: its output, Download and install / Remove / How to use, the state."""
        ttk = self.ttk
        self.section(f, _('OptiScaler (optional)'))
        self.note(f, _('A separate program by the OptiScaler team that takes the port\'s XeSS input and turns it '
                       'into another upscaler. Not part of the port: the launcher downloads it from the project\'s '
                       'GitHub when you ask and can take it out again.'), top=2)
        var = self.var('optiscaler_output', 'app')
        self.optiscaler_box = ttk.Combobox(f, state='readonly', width=44)
        self.optiscaler_box.bind('<<ComboboxSelected>>', lambda _e: var.set(
            self.optiscaler_values[self.optiscaler_box.current()]))
        self.row(f, _('OptiScaler output'), self.optiscaler_box,
                 _('What OptiScaler makes of the XeSS input. FSR 2.2 is the one tested with the port; FSR 4 '
                   'goes through DirectX 12 and needs a Radeon RX 7000 or 9000. Written to OptiScaler.ini at '
                   'once; it applies when the game starts.'), restart=True)
        self.fill_optiscaler_box()
        var.trace_add('write', lambda *_a: self.optiscaler_output_picked())
        holder = ttk.Frame(f)
        holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(10, 0))
        self.optiscaler_install_button = ttk.Button(holder, text=_('Download and install'),
                                                    command=self.install_optiscaler)
        self.optiscaler_install_button.pack(side='left')
        self.optiscaler_remove_button = ttk.Button(holder, text=_('Remove'), command=self.remove_optiscaler)
        self.optiscaler_remove_button.pack(side='left', padx=6)
        ttk.Button(holder, text=_('How to use'), command=self.optiscaler_help).pack(side='left')
        self.optiscaler_label = ttk.Label(f, text='', style='Muted.TLabel', wraplength=self.px(680), justify='left')
        self.optiscaler_label.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(6, 0))
        self.optiscaler_busy = False
        self.var('anti_aliasing', 'app').trace_add('write', lambda *_a: self.refresh_optiscaler())
        self.refresh_optiscaler()

    def fill_optiscaler_box(self):
        """The outputs for this card (DLSS on NVIDIA RTX only); the choice stays when it is offered."""
        box = getattr(self, 'optiscaler_box', None)
        if box is None:
            return
        pc = getattr(self, 'pc', None)
        var = self.var('optiscaler_output', 'app')
        current = var.get()
        options = optiscaler_choices(pc)
        if current not in [key for key, _t in options]:
            if pc is None:  # the card is not checked yet: the saved choice stays offered
                options = [option for option in OPTISCALER_CHOICES if option in options or option[0] == current]
            else:
                current = 'fsr22'
        self.optiscaler_values = [key for key, _t in options]
        box.configure(values=[_(*text) for _k, text in options])
        if current not in self.optiscaler_values:
            current = 'fsr22'
        box.current(self.optiscaler_values.index(current))
        if var.get() != current:
            var.set(current)  # a choice this card cannot use (DLSS on a Radeon)

    def optiscaler_text(self, state):
        if state is None:
            return _('Not installed.')
        if state['foreign']:
            return _('bin\\winmm.dll is another program, not OptiScaler: the launcher leaves it alone.')
        key = {'0x77': 'F8', '0x2d': 'Insert', '0x24': 'Home', 'auto': 'Insert'}.get(
            (state['menu_key'] or 'auto').lower(), state['menu_key'] or 'Insert')
        text = _('Installed: OptiScaler {} · output {} · its menu in the game: {}.').format(
            state['version'] or '?', optiscaler_output_name(state['upscaler']), key)
        if state['by_hand']:
            text += ' ' + _('Unpacked by hand: Remove takes its files out; Download and install replaces it '
                            'with the launcher\'s, set for the port.')
        if key in ('Insert', 'Home'):
            text += ' ' + _('{} is also a key of the port: install it again from here to move its menu to F8.').format(key)
        if self.var('anti_aliasing', 'app').get() != 'xess':
            text += ' ' + _("⚠ The port's anti-aliasing is not XeSS, so OptiScaler gets nothing: choose XeSS in "
                            'Anti-aliasing above.')
        return text

    def refresh_optiscaler(self):
        label = getattr(self, 'optiscaler_label', None)
        if label is None or self.optiscaler_busy:
            return
        try:
            state = issn_downloads.optiscaler_state(optiscaler_bin())
        except OSError:
            state = None
        label.configure(text=self.optiscaler_text(state))
        installed = state is not None and not state['foreign']
        self.optiscaler_install_button.configure(
            text=_('Update / install again') if installed else _('Download and install'),
            state='disabled' if state and state['foreign'] else 'normal')
        self.optiscaler_remove_button.configure(state='normal' if installed else 'disabled')

    def optiscaler_show(self, text):
        label = getattr(self, 'optiscaler_label', None)
        if label is not None:
            label.configure(text=text)
        if not self.process:
            self.status.configure(text=text, fg=TEXT)

    def optiscaler_game_open(self):
        if self.process:
            self.messagebox.showinfo(APP_NAME, _('Close the game first: it is using the OptiScaler files.'))
            return True
        return False

    def optiscaler_output_picked(self):
        """A new output: into the installed OptiScaler.ini at once (DLSS: NVIDIA's files first)."""
        output = self.var('optiscaler_output', 'app').get()
        box = getattr(self, 'optiscaler_box', None)
        if box is not None and output in getattr(self, 'optiscaler_values', []):
            box.current(self.optiscaler_values.index(output))  # set from elsewhere (defaults)
        try:
            state = issn_downloads.optiscaler_state(optiscaler_bin())
        except OSError:
            state = None
        if not state or state['foreign'] or self.optiscaler_busy:
            return
        if output == 'dlss' and upscaler_missing('dlss'):
            self.download_upscaler('dlss', then=lambda: self.apply_optiscaler_output(output))
            return
        self.apply_optiscaler_output(output)

    def apply_optiscaler_output(self, output):
        try:
            value = issn_downloads.set_output(optiscaler_bin(), output, dlss_folder() / 'nvngx_dlss.dll')
        except OSError as error:
            self.optiscaler_show(_('OptiScaler.ini could not be changed: {}').format(error))
            return
        self.refresh_optiscaler()
        self.status.configure(text=_('OptiScaler output: {} (the next time the game starts).').format(
            optiscaler_output_name(value)), fg=TEXT)

    def install_optiscaler(self):
        """Download and install: the newest stable release from the project's GitHub, set for the
        port (issn_downloads.install); then the port's anti-aliasing goes to XeSS."""
        if self.optiscaler_busy or self.optiscaler_game_open():
            return
        bin_dir = optiscaler_bin()
        if not self.messagebox.askyesno(APP_NAME, _(
                'OptiScaler is a separate program by the OptiScaler team (GPL-3.0), not part of the port.\n\n'
                'The launcher downloads its newest stable release from github.com/optiscaler/OptiScaler '
                '(about 55 MB), checks it and puts it in {}, set for the port: its menu on F8, the output you '
                "chose. The port's anti-aliasing is set to XeSS, which is what OptiScaler reads.\n\n"
                'Download and install it now?').format(bin_dir)):
            return
        self.optiscaler_busy = True
        self.optiscaler_install_button.configure(state='disabled')
        self.optiscaler_remove_button.configure(state='disabled')
        output = self.var('optiscaler_output', 'app').get()
        dlss_dll = dlss_folder() / 'nvngx_dlss.dll'

        def progress(what):
            def report(done, total):
                percent = done * 100 // total if total else 0
                if percent != report.shown:
                    report.shown = percent
                    self.ui_calls.put(lambda p=percent: self.optiscaler_show(what.format(p)))
            report.shown = -1
            return report

        def work():
            archive = None
            try:
                try:
                    release = issn_downloads.optiscaler_release()
                except (OSError, ValueError):
                    release = issn_downloads.OPTISCALER_KNOWN  # GitHub's API unreachable: the tested one
                name = re.sub(r'[^A-Za-z0-9._-]', '_', release.url.rsplit('/', 1)[-1]) or 'optiscaler.7z'
                archive = Path(tempfile.gettempdir()) / 'issn-optiscaler' / name
                issn_downloads.download(release, archive, progress(_('Downloading OptiScaler {}… {{}}%')
                                                                   .format(release.tag)))
                issn_downloads.install(archive, bin_dir, output, release.tag,
                                       progress(_('Installing OptiScaler… {}%')), dlss_dll)
            except (OSError, ValueError) as error:
                self.ui_calls.put(lambda e=error: finish(e))
                return
            finally:
                if archive is not None:
                    try:
                        archive.unlink(missing_ok=True)
                    except OSError:
                        pass
            self.ui_calls.put(lambda: finish(None, release.tag))

        def finish(error, tag=''):
            self.optiscaler_busy = False
            self.refresh_optiscaler()
            if error:
                self.optiscaler_show(_('OptiScaler was not installed: {}').format(error))
                return
            if self.var('anti_aliasing', 'app').get() != 'xess':
                self.var('anti_aliasing', 'app').set('xess')  # fetches XeSS's files when missing
            self.collect()
            if output == 'dlss' and upscaler_missing('dlss'):
                self.download_upscaler('dlss', then=lambda: self.apply_optiscaler_output('dlss'))
            self.refresh_optiscaler()
            if not self.process:  # the bar still showed "Installing… 100%"
                self.status.configure(text=_('OptiScaler {} installed.').format(tag), fg=TEXT)
            self.messagebox.showinfo(APP_NAME, _(
                "OptiScaler {} is installed. The port's anti-aliasing is now XeSS.\n\nIn the game, F8 opens "
                "OptiScaler's menu (Insert stays the port's). Remove takes it out again.").format(tag))
        self.optiscaler_show(_('Looking for the newest OptiScaler…'))
        threading.Thread(target=work, daemon=True).start()

    def remove_optiscaler(self):
        if self.optiscaler_busy or self.optiscaler_game_open():
            return
        if not self.messagebox.askyesno(APP_NAME, _(
                "Remove OptiScaler? Its files leave the bin folder and the port runs as before. The port's "
                'anti-aliasing stays XeSS; change it above if you want.')):
            return
        try:
            removed = issn_downloads.remove(optiscaler_bin())
        except OSError as error:
            self.refresh_optiscaler()
            self.messagebox.showerror(APP_NAME, _('OptiScaler was not removed completely: {}').format(error))
            return
        self.refresh_optiscaler()
        self.optiscaler_show(_('OptiScaler removed ({} files).').format(len(removed)))

    def offer_optiscaler_xess(self):
        """At Play: OptiScaler installed but the port's anti-aliasing is not XeSS (it then gets
        nothing). Asked once per launcher session; yes sets XeSS (its files download before the start)."""
        if not WINDOWS or self.app.get('anti_aliasing') == 'xess' or getattr(self, 'optiscaler_asked', False):
            return
        try:
            state = issn_downloads.optiscaler_state(optiscaler_bin())
        except OSError:
            return
        if not state or state['foreign']:
            return
        self.optiscaler_asked = True
        if self.messagebox.askyesno(APP_NAME, _(
                "OptiScaler is installed, but the port's anti-aliasing is not XeSS, so OptiScaler gets "
                'nothing and the game runs without it.\n\nSwitch the anti-aliasing to XeSS now?')):
            self.quiet_anti_aliasing = True  # Play fetches XeSS's files itself, then starts
            try:
                self.var('anti_aliasing', 'app').set('xess')
            finally:
                self.quiet_anti_aliasing = False
            self.collect()

    def optiscaler_help(self):
        if self.messagebox.askyesno(APP_NAME, _(
                'OptiScaler (optional)\n\n'
                "What it is: a separate program (OptiScaler team, GPL-3.0) that takes the picture, depth and "
                'motion the port gives XeSS and runs another upscaler on them: FSR 4, FSR 3.1, XeSS, DLSS or '
                'FSR 2.2.\n\n'
                "1. Download and install. The port's anti-aliasing must be XeSS (the launcher sets it): with "
                'anything else OptiScaler gets nothing.\n'
                '2. Choose the output here (it applies when the game starts). The size the game draws at is '
                "still the port's 'FSR 4 / DLSS quality'.\n"
                "3. In the game, F8 opens OptiScaler's menu (output, sharpness, debug views). Insert is still "
                "the port's menu and Home its anti-aliasing switch. Page Up, Page Down and End are OptiScaler's (its "
                "FPS counter and frame generation).\n"
                '4. To go back: Remove. Every file it added leaves the bin folder.\n\n'
                'Third-party: problems with OptiScaler itself go to its project, not to the port.\n'
                'Project: github.com/optiscaler/OptiScaler · License: GPL-3.0\n'
                'Open the project page (source code and license)?')):
            open_url(issn_downloads.OPTISCALER_PAGE)

    def refresh_dlss_state(self):
        for key, label in getattr(self, 'vendor_state', {}).items():
            if key in self.upscaler_downloads:
                continue  # the download shows its progress there
            missing = upscaler_missing(key)
            name = UPSCALER_PACKAGES[key].name
            label.configure(text=_('{} files: ready').format(name) if not missing else
                            _('{} files: not downloaded yet').format(name))

    def anti_aliasing_picked(self):
        """XeSS or DLSS chosen: their files download at once (not at Play)."""
        key = self.var('anti_aliasing', 'app').get()
        if getattr(self, 'quiet_anti_aliasing', False):
            return
        if key in UPSCALER_PACKAGES and WINDOWS and upscaler_missing(key):
            self.download_upscaler(key)

    def download_upscaler(self, key, then=None):
        """Fetches XeSS's or DLSS's files from the vendor's release in the background; `then` runs on
        the UI thread afterwards (success or not)."""
        if key in self.upscaler_downloads:
            return
        package = UPSCALER_PACKAGES[key]
        folder = upscaler_folder(key)
        self.upscaler_downloads.add(key)
        label = getattr(self, 'vendor_state', {}).get(key)

        def show(text):
            if label is not None:
                label.configure(text=text)
            if not self.process:
                self.status.configure(text=text, fg=TEXT)

        def progress(done, total):
            percent = done * 100 // total if total else 0
            if percent != progress.shown:
                progress.shown = percent
                self.ui_calls.put(lambda p=percent: show(_('Downloading {} files… {}%').format(package.name, p)))
        progress.shown = -1

        def finish(error=None):
            self.upscaler_downloads.discard(key)
            self.refresh_dlss_state()
            if error:
                show(_('{} download failed: {}').format(package.name, error))
            else:
                show(_('{} files: ready').format(package.name))
            if then:
                then()

        def work():
            try:
                issn_downloads.fetch(package, folder, progress)
            except (OSError, zipfile.BadZipFile, ValueError) as error:
                self.ui_calls.put(lambda e=error: finish(e))
                return
            self.ui_calls.put(finish)
        show(_('Downloading {} files…').format(package.name))
        threading.Thread(target=work, daemon=True).start()

    def open_xess_folder(self):
        folder = xess_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.messagebox.showerror(APP_NAME, str(error))
            return
        self.open_path(folder)
        self.refresh_dlss_state()

    def open_dlss_folder(self):
        folder = dlss_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.messagebox.showerror(APP_NAME, str(error))
            return
        self.open_path(folder)
        self.refresh_dlss_state()

    def build_display(self, f, section=True):
        """Window: the display mode, VSync and HDR."""
        if section:
            self.section(f, _('Window', 'Окно'), top=4)
        self.row(f, _('Display mode'), self.choice(f, 'display_mode', 'ini', DISPLAY_MODES),
                 _('Borderless covers the screen without changing the display mode.'))
        self.check(f, 'vsync', 'ini', _('VSync'), _('Off allows tearing for the lowest latency.'), restart=True)
        self.check(f, 'hdr', 'app', _('Allow HDR output', 'Разрешить HDR'),
                   _('When HDR is on in Windows and the display supports it.',
                     'Если HDR включён в Windows и монитор его поддерживает.') if WINDOWS else
                   _('When the desktop (KDE Plasma 6, GNOME 48 or gamescope) has HDR on and the display supports it.'))

    def build_graphics(self):
        """More graphics (advanced view): video memory, shader compilation, the graphics card and
        OptiScaler - what most players never need to touch."""
        f = self.scrolled_page('graphics', _('More graphics'),
                               _('Video memory, shaders, the graphics card and OptiScaler.'))
        self.section(f, _('Memory and shaders'), top=4)
        self.row(f, _('Video memory limit'), self.choice(f, 'vram_limit', 'app', VRAM_LIMITS),
                 _('How much video memory the game may fill before it frees textures it is not using. Lower it '
                   'if the game closes with "Device lost" or the log says the video memory is nearly full, '
                   'mainly on 4-8 GB cards and laptops.'), restart=True)
        self.row(f, _('Shader compilation'), self.choice(f, 'shader_compile', 'app', SHADER_COMPILE),
                 _('The first time a place or effect appears its shaders are compiled. In the background the '
                   'game keeps running and what they draw shows up a moment later; in the frame the game '
                   'waits for them (a stutter). Either way they are kept in the cache for the next time.'),
                 restart=True)
        self.section(f, _('Graphics card'))
        gpus = list_gpus()
        cards = [('', ('Automatic',))] + [(row[0], (row[1],)) for row in gpus]
        self.row(f, _('Graphics card'), self.choice(f, 'gpu_id', 'app', cards),
                 _('The GPU the game runs on. Choose your dedicated card if the game uses the integrated one.'),
                 restart=True)
        if WINDOWS:
            self.build_optiscaler(f)

    def build_pc(self):
        """Your PC (advanced view): what the launcher found here, what to expect and the three goals."""
        f = self.scrolled_page('pc', _('Your PC'), _('Your graphics card, processor and memory, and what to expect.'))
        self.build_pc_check(f)

    def build_controls_page(self):
        f = self.scrolled_page('controls', _('Controls', 'Управление'),
                               _('Controller, keyboard and mouse, the button icons and the sensitivity.'))
        self.build_controls(f)

    def build_files(self):
        """Patches & mods (advanced view): the game's code patches, third-party patches and mods."""
        f = self.scrolled_page('files', _('Patches & mods'), _("The game's patches, third-party patches and mods."))
        self.build_patches(f)
        self.build_mods(f)

    def build_game(self):
        f = self.scrolled_page('game', _('Game & saves'), _('Your game dump, your saves and the game language.'))
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
        self.check(f, 'skip_intro', 'app', _('Skip the intro logos'),
                   _('The game opens straight on its title screen, without the PlayStation and Sucker Punch '
                     'logos.'), restart=True)
        # issn 11-06: DLC from the player's own add-on dumps (scripts/content_profile.py).
        self.section(f, _('DLC'))
        self.folder(f, 'addcont_dir', _('DLC folder (optional)'), _('Choose the folder with your DLC dumps'),
                    _("The folder with the dumps of your own DLC (Cole's Legacy, Cole's jacket, the vests): each "
                      'one with its param.sfo and license in Sc0 or sce_sys. Read when the game starts; '
                      "Cole's Legacy is free on the PlayStation Store."), store='ini',
                    on_change=lambda: self.addcont_note.configure(text=addcont_summary(self.var('addcont_dir', 'ini').get(), self.meta_dir())))
        self.addcont_note = ttk.Label(f, text=addcont_summary(self.var('addcont_dir', 'ini').get(), self.meta_dir()),
                                      style='Muted.TLabel', wraplength=self.px(620), justify='left')
        self.addcont_note.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(2, 0))
        # issn 11-05: Paper Trail's missions without infamouspapertrail.com (src/paper_trail_hook.h).
        self.section(f, _('Paper Trail'))
        self.check(f, 'paper_trail', 'ini', _('Paper Trail without internet'),
                   _("Paper Trail's missions were unlocked by a website that closed in 2018. With this on, "
                     "the port answers for it: every step counts as done and the missions open once the "
                     'story reaches them (about a third of the way in).'), restart=True)

    def user_dir(self):
        return Path(self.var('user_dir', 'app').get() or DATA_DIR / 'user')

    # ---- issn 11-04: achievements (the game's trophies, scripts/trophies.py) -----------------------
    def build_trophies(self):
        """The game's trophies: icons from the player's own trophy file, which ones this PC unlocked
        (user/trophies-<title ID>.txt, written by the game) and when; names with the player's own key."""
        tk, ttk = self.tk, self.ttk
        f = self.scrolled_page('trophies', _('Achievements'),
                               _("The game's trophies: the ones unlocked in this port are in color, with the date."))
        self.trophy_summary = ttk.Label(f, text='', wraplength=self.px(680), justify='left')
        self.trophy_summary.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(4, 0))
        self.trophy_progress = ttk.Progressbar(f, orient='horizontal', length=self.px(420), maximum=100)
        self.trophy_progress.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(6, 4))
        self.trophy_list = tk.Frame(f, bg=PANEL)
        self.trophy_list.grid(row=self.next_row(f), column=0, columnspan=2, sticky='we', pady=(8, 0))
        self.trophy_list.columnconfigure(0, weight=1)
        self.section(f, _('In the game'))
        self.check(f, 'trophy_note', 'ini', _('Trophy popup'),
                   _("The trophy's icon and name in a top corner for a few seconds when the game unlocks one. "
                     'Also in Insert > Overlay.'))
        self.section(f, _('Trophy names (optional)'))
        self.note(f, _("The names and descriptions in the game's trophy file are encrypted. Without a key the port "
                       'uses its own list (English, Portuguese and Spanish, written from public trophy lists). '
                       'If you have your own PS4 trophy key (the same one shadPS4 asks for), paste it here to see '
                       "the game's own texts: it stays only on this PC, in user\\trophy-key.txt, and the port never "
                       'ships or sends it. A name shows once its trophy is unlocked.'), top=4)
        holder = ttk.Frame(f)
        self.trophy_key_entry = ttk.Entry(holder, width=40, show='•')
        self.trophy_key_entry.pack(side='left')
        ttk.Button(holder, text=_('Save key'), command=self.save_trophy_key).pack(side='left', padx=(6, 0))
        ttk.Button(holder, text=_('Remove key'), command=self.remove_trophy_key).pack(side='left', padx=(6, 0))
        self.row(f, _('Trophy key'), holder)
        self.trophy_key_state = ttk.Label(f, text='', style='Muted.TLabel', wraplength=self.px(680), justify='left')
        self.trophy_key_state.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(4, 0))
        self.trophy_icons, self.trophy_cache = [], {}

    def trophy_folders(self):
        return [self.var('update_dir', 'app').get(), self.var('game_dir', 'app').get()]

    def trophy_texts(self, trp):
        """({id: text} in the game language, a note on where the names come from): the game's own
        texts with the saved key, else the port's list (scripts/trophy_list.py)."""
        key = trophies.read_key(self.user_dir())
        language = game_language({**self.app, 'game_language': self.var('game_language', 'app').get()})
        cache = ('texts', str(trp), key, language)
        if cache not in self.trophy_cache:
            try:
                texts, source = trophies.game_texts(trp, language, *self.trophy_folders(), key=key)
                note = (_('Key saved on this PC: the names are the game\'s own.') if source == 'key' else
                        _("No key: the names come from the port's own list.") if source == 'list' else
                        _('No key: names are not shown.'))
                self.trophy_cache[cache] = (texts, note)
            except (OSError, ValueError, KeyError, trophies.ElementTree.ParseError) as error:
                self.trophy_cache[cache] = ({}, _('The key is saved but the names could not be read: {}').format(error))
        return self.trophy_cache[cache]

    def trophy_icon(self, trp, entries, trophy_id, unlocked):
        """The icon as a small PhotoImage: in color when unlocked, dim and grey when not."""
        cache = ('icon', str(trp), trophy_id, unlocked)
        if cache not in self.trophy_cache:
            photo = None
            data = trophies.icon(trp, entries, trophy_id)
            if data:
                try:
                    import io
                    from PIL import Image, ImageEnhance, ImageOps, ImageTk
                    size = self.px(56)
                    image = Image.open(io.BytesIO(data)).convert('RGBA').resize((size, size), Image.LANCZOS)
                    if not unlocked:
                        alpha = image.getchannel('A')
                        image = ImageEnhance.Brightness(ImageOps.grayscale(image).convert('RGBA')).enhance(0.45)
                        image.putalpha(alpha)
                    backdrop = Image.new('RGBA', image.size, CARD)
                    photo = ImageTk.PhotoImage(Image.alpha_composite(backdrop, image).convert('RGB'), master=self.root)
                except (ImportError, OSError, ValueError):
                    photo = None
            self.trophy_cache[cache] = photo
        return self.trophy_cache[cache]

    def refresh_trophies(self):
        tk = self.tk
        for child in self.trophy_list.winfo_children():
            child.destroy()
        self.trophy_icons = []
        trp, ids, unlocked = trophies.summary(self.user_dir(), *self.trophy_folders())
        texts, key_note = self.trophy_texts(trp) if trp else ({}, '')
        self.trophy_key_state.configure(text=key_note)
        if not trp:
            self.trophy_summary.configure(text=_('No trophy file in the game folder (sce_sys\\trophy\\trophy00.trp, '
                                                 'or Sc0\\trophy beside it). Choose the game folder on the Game page.'))
            self.trophy_progress.configure(value=0)
            return
        entries = trophies.read_entries(trp)
        done = sum(1 for i in ids if i in unlocked)
        percent = round(100 * done / len(ids)) if ids else 0
        platinum = next((i for i, t in texts.items() if t['grade'] == 'P'), 0 if ids and ids[0] == 0 else None)
        line = _('{} of {} unlocked ({}%)').format(done, len(ids), percent)
        if platinum is not None and platinum in unlocked:
            line += '  ·  ' + _('Platinum! Every trophy unlocked')
        # issn 11-11: each game has its own trophies (user/trophies-<title ID>.txt); say which one.
        if name := trophies.game_name(*self.trophy_folders()):
            line = f'{name}  ·  {line}'
        self.trophy_summary.configure(text=line)
        self.trophy_progress.configure(value=percent)
        grades = {'P': (_('Platinum'), '#c8e6ff'), 'G': (_('Gold'), '#ffcc4d'), 'S': (_('Silver'), '#d2d6e0'),
                  'B': (_('Bronze'), '#db8f5c')}
        # Unlocked first, newest first; then the others in the game's order.
        newest = sorted((i for i in ids if i in unlocked), key=lambda i: (unlocked[i], i), reverse=True)
        for row, trophy_id in enumerate(newest + [i for i in ids if i not in unlocked]):
            got = trophy_id in unlocked
            text = texts.get(trophy_id, {})
            card = tk.Frame(self.trophy_list, bg=CARD, highlightthickness=1, highlightbackground=LINE)
            card.grid(row=row, column=0, sticky='we', pady=(0, self.px(4)))
            card.columnconfigure(1, weight=1)
            photo = self.trophy_icon(trp, entries, trophy_id, got)
            if photo:
                self.trophy_icons.append(photo)
                tk.Label(card, image=photo, bg=CARD, bd=0).grid(row=0, column=0, rowspan=2, padx=self.px(8),
                                                               pady=self.px(6))
            # issn 11-05 (João): a trophy's name and description show once it is unlocked; until
            # then only its number, icon and grade.
            name = text.get('name') if got and text.get('name') else _('Trophy {}').format(trophy_id)
            tk.Label(card, text=name, bg=CARD, fg=TEXT if got else MUTED, font=(UI_FONT, 11, 'bold'),
                     anchor='w').grid(row=0, column=1, sticky='w', pady=(self.px(8), 0))
            detail = text.get('detail', '') if got else ''
            when = _('Unlocked {}').format(unlocked[trophy_id]) if got else _('Locked')
            tk.Label(card, text=f'{detail}\n{when}' if detail else when, bg=CARD, fg=MUTED, font=(UI_FONT, 9),
                     anchor='w', justify='left', wraplength=self.px(520)).grid(row=1, column=1, sticky='w',
                                                                               pady=(0, self.px(8)))
            grade = grades.get(text.get('grade') or ('P' if trophy_id == platinum else ''))
            if grade:
                tk.Label(card, text=grade[0], bg=CARD, fg=grade[1] if got else MUTED,
                         font=(UI_FONT, 9, 'bold')).grid(row=0, column=2, rowspan=2, padx=self.px(12))

    def save_trophy_key(self):
        key = trophies.parse_key(self.trophy_key_entry.get())
        if key is None:
            self.messagebox.showerror(APP_NAME, _('A trophy key is 32 hexadecimal digits (0-9, A-F).'))
            return
        trp = trophies.trophy_file(*self.trophy_folders())
        if trp:  # a key that does not open this game's file is not kept
            try:
                trophies.read_texts(trp, trophies.npbind_file(*self.trophy_folders()), key, '1')
            except (OSError, ValueError, KeyError, trophies.ElementTree.ParseError) as error:
                self.messagebox.showerror(APP_NAME, _('This key does not open the trophy names: {}').format(error))
                return
        try:
            trophies.save_key(self.user_dir(), key)
        except OSError as error:
            self.messagebox.showerror(APP_NAME, _('Could not save the key: {}').format(error))
            return
        self.trophy_key_entry.delete(0, 'end')
        self.refresh_trophies()

    def remove_trophy_key(self):
        try:
            trophies.save_key(self.user_dir(), None)
            trophies.remove_names(self.user_dir())
        except OSError as error:
            self.messagebox.showerror(APP_NAME, _('Could not remove the key: {}').format(error))
            return
        self.refresh_trophies()

    def build_save_backups(self, f):
        """run.py backs the saves up before every start; one of them can be restored here."""
        ttk = self.ttk
        holder = ttk.Frame(f)
        self.backup_choice = ttk.Combobox(holder, state='readonly', width=30)
        self.backup_choice.pack(side='left')
        ttk.Button(holder, text=_('Restore', 'Восстановить'), command=self.restore_backup).pack(side='left', padx=(6, 0))
        ttk.Button(holder, text=_('Refresh', 'Обновить'), command=self.refresh_backups).pack(side='left', padx=(6, 0))
        self.check(f, 'save_backups', 'app', _('Back up saves before a start'),
                   _('A copy of your saves before each start when they changed (kept in '
                     'user\\savedata_backups). Off: no new copies; the old ones stay and can still be restored.'))
        self.row(f, _('Save backups', 'Резервные копии'), holder,
                 _('Made before every start when the saves changed, newest first. Kept: the last {}, one a '
                   'day for {} days and the ones made before a restore or an import. Restoring backs up the '
                   'current saves first.').format(save_backup.KEEP, save_backup.DAYS))
        self.row(f, _('Import saves'),
                 ttk.Button(f, text=_('Import from another port…'), command=self.import_saves),
                 _('Brings your progress from another PC port of the game (HayatoG\'s iss_pc, or an older '
                   'folder of this one): pick its folder. The current saves are backed up first.'))
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

    def import_saves(self):
        """Saves from another port of the game (same layout, scripts/save_backup.py): its launcher's
        data folder when it can be found, otherwise the folder the player picks."""
        if self.process:
            self.messagebox.showwarning(APP_NAME, _('Close the game before importing saves.'))
            return
        source = None
        for found in save_backup.other_port_dirs():
            if self.messagebox.askyesno(APP_NAME, _('Saves of another port were found in {}. Import them?').format(found)):
                source = found
                break
        if source is None:
            picked = self.filedialog.askdirectory(
                title=_('Choose the other port\'s folder (or its user / savedata folder)'), initialdir=str(Path.home()))
            if not picked:
                return
            source = Path(picked)
            if not save_backup.import_source(source):
                self.messagebox.showerror(APP_NAME, _('No inFAMOUS Second Son saves in {}. Choose the other port\'s '
                                                      'folder, or its user or savedata folder.').format(source))
                return
            if not self.messagebox.askyesno(APP_NAME, _('Replace the current saves with the ones in {}? '
                                                        'The current saves are backed up first.').format(source)):
                return
        try:  # the game's own title ID (region); saves of another region are renamed to it
            title = str(game_sfo(Path(self.app['game_dir'] or '.')).get('TITLE_ID') or save_backup.TITLE)
        except (OSError, ValueError):
            title = save_backup.TITLE
        try:
            safety, slots = save_backup.import_saves(self.user_dir(), source, title=title)
        except (OSError, ValueError) as error:
            self.messagebox.showerror(APP_NAME, _('Could not import: {}').format(error))
            return
        self.refresh_backups()
        self.refresh_status()
        self.messagebox.showinfo(APP_NAME, _('{} saves imported from {}.').format(slots, source) +
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
        from patches import ALWAYS_ON, EXCLUSIVE_PREFIX, ONE_OF, PRO_ONLY_PREFIX
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
        groups = {}  # issn 11-06: one resolution / field of view patch at a time (patches.ONE_OF)
        for meta in metas:
            name = meta.get('Name')
            if name.startswith(EXCLUSIVE_PREFIX) or name.startswith('Frame Rate Patch') or name in ALWAYS_ON:
                continue
            if name.startswith(PRO_ONLY_PREFIX):  # needs the PS4 Pro mode; the port runs as a base PS4
                continue
            group = next((g for g, match in ONE_OF if match(name)), None)
            var = tk.BooleanVar(value=name in on and not (group and any(v.get() for v in groups.get(group, []))))
            self.game_patch_vars[name] = var
            if group:
                groups.setdefault(group, []).append(var)
                var.trace_add('write', lambda *_a, v=var, g=group: self.one_patch_of(groups[g], v))
            author = meta.get('Author')
            self.check(f, None, None, name + (f'  ({author})' if author else ''), meta.get('Note'), var=var)
        self.game_patch_title = title_id

    @staticmethod
    def one_patch_of(group, var):
        """Ticking a patch of a ONE_OF group unticks the others."""
        if var.get():
            for other in group:
                if other is not var and other.get():
                    other.set(False)

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
                   'Two fingers: hold Select/Back + RB. Spray can: Insert > Controls > Spray can tilts the pad, LB '
                   'rattles it, the right stick aims. Automatic turns it off on a DualShock 4 or DualSense.'),
                 restart=True)
        self.row(f, _('Button icons'), self.choice(f, 'glyph_style', 'app', GLYPH_STYLES, width=44),
                 _('The buttons the game shows in its hints and menus. Keyboard and mouse shows the keys '
                   '(they follow your key changes above) and the mouse buttons; Automatic picks the keyboard '
                   'when no controller is connected at start, Xbox letters for Xbox and other pads.'),
                 restart=True)
        # issn 11-04: the same sensitivities as the in-game menu (Insert > Controls), in issn.ini.
        for key, title, low, high, hint in (
                ('pad_sensitivity', _('Controller sensitivity'), 0.25, 3.0,
                 _('The right stick (camera). Below 1: slower. Above 1: faster, even past the top speed of the game. '
                   'Also in the game: Insert > Controls.')),
                ('mouse_sensitivity', _('Mouse sensitivity'), 0.1, 5.0,
                 _('How far the camera turns per mouse movement. Also in the game: Insert > Controls, or - / =.'))):
            spin = self.ttk.Spinbox(f, from_=low, to=high, increment=0.05, format='%.2f', width=8,
                                    textvariable=self.var(key, 'ini'))
            self.row(f, title, spin, hint)
        self.row(f, _('Stick dead zone'), self.choice(f, 'stick_deadzone', 'app', STICK_DEADZONE),
                 _('How far a stick must move before the game sees it. Raise it if Delsin walks or the '
                   'camera turns on its own while you do not touch the stick (drift).'),
                 restart=True)
        self.row(f, _('Spray can stick speed'), self.choice(f, 'spray_stick', 'app', SPRAY_STICK),
                 _('How fast the right stick turns the spray can while the pad is tilted (Insert > Controls > Spray can). A light '
                   'push moves it a little, a full push at this speed. In the game, - / = change it too.'),
                 restart=True)
        self.note(f, _('Keyboard and mouse: WASD moves, IJKL turns the camera. Click in the game window and the '
                       'mouse aims (1:1, as in axylpup\'s build); Y or F7 lets the mouse go and takes it back, '
                       '- / = change its speed. Left click R2 (shoot), right click L2 (aim). Spray can: hold Z '
                       'to tilt the pad, tap Z or hold X to rattle it, move the mouse to aim. R: the touchpad (held, '
                       'both thumbs: the drains, the car on Reggie); R + W/A/S/D: a swipe (doors, wreckage, the '
                       'Mobile Command Center core: hold R + W and click to hit it); G: its left side (photo mode). Arrows: '
                       'd-pad. F12: screenshot. Insert: the port\'s menu.'), top=8)
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
        self.note(f, _("The mouse's middle and side buttons can be keys too: Mouse Middle, Mouse 4, Mouse 5 (also with Change in Insert > Controls)."))  # issn 11-06: runtime_pad.c mouse_keys
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

    def build_about(self, f):
        """Advanced > About: the author, his links, the support links when there are any, credits."""
        ttk = self.ttk
        self.section(f, _('About'))
        self.note(f, _('Made by {}').format(AUTHOR), top=2)
        holder = ttk.Frame(f)
        holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(6, 0))
        for label, url in AUTHOR_LINKS + [('Discord', DISCORD_URL)]:
            ttk.Button(holder, text=label, command=lambda u=url: open_url(u)).pack(side='left', padx=(0, 6))
        self.note(f, _('Thanks to {}.').format(CREDITS), top=8)
        self.build_support(f)

    def build_support(self, f):
        """Advanced > Support the project: Pix (QR code and key) for Brazil, Ko-fi for everyone else.
        The Home page's 'Support' link scrolls here."""
        tk, ttk = self.tk, self.ttk
        self.section(f, _('Support the project'))
        self.support_anchor = f.grid_slaves(row=self.next_row(f) - 2, column=0)[0]
        self.note(f, _("The port is free and always will be. If it gave you some good moments and you'd like to help "
                       "keep it and the next projects going, or just say thanks, any amount is welcome and means a "
                       "lot. Thank you! ♥"), top=4)
        self.note(f, _("Donations only support the work: they don't unlock anything, and the port never includes "
                       "game files."), top=4)
        cards = ttk.Frame(f)
        cards.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(10, 0))

        def card(column, title):
            box = tk.Frame(cards, bg=CARD, padx=self.px(16), pady=self.px(12))
            box.grid(row=0, column=column, sticky='nsw', padx=(0, self.px(12)))
            tk.Label(box, text=heading_text(title.upper()), bg=CARD, fg=TEXT, font=heading_font(17, UI_FONT)).pack(anchor='w')
            return box

        def copier(parent, label, text):
            button = ttk.Button(parent, text=label, style='Card.TButton')

            def copy():
                self.root.clipboard_clear()
                self.root.clipboard_append(text)
                button.configure(text=_('Copied ✓'))
                self.root.after(1600, lambda: button.configure(text=label))
            button.configure(command=copy)
            return button

        pix = card(0, _('Pix (Brazil)'))
        try:
            photo = tk.PhotoImage(file=str(PIX_QR))
            if self.px(100) < 150 and photo.width() > 200:
                photo = photo.subsample(2)
            self.pix_photo = photo  # kept alive
            tk.Label(pix, image=photo, bg=CARD, bd=0).pack(anchor='w', pady=(8, 6))
        except (tk.TclError, OSError):
            pass
        tk.Label(pix, text=_('Random key'), bg=CARD, fg=MUTED, font=(UI_FONT, 9)).pack(anchor='w')
        tk.Label(pix, text=PIX_KEY, bg=CARD, fg=TEXT, font=(UI_FONT, 9, 'bold')).pack(anchor='w', pady=(0, 6))
        row = tk.Frame(pix, bg=CARD)
        row.pack(anchor='w')
        copier(row, _('Copy key'), PIX_KEY).pack(side='left', padx=(0, 6))
        copier(row, _('Copy Pix code'), pix_payload()).pack(side='left')
        world = card(1, _('Any country'))
        tk.Label(world, text=_('Card or PayPal, in any currency, no account needed.'), bg=CARD, fg=MUTED,
                 font=(UI_FONT, 10), wraplength=self.px(240), justify='left').pack(anchor='w', pady=(8, 10))
        for label, url in SUPPORT_LINKS:
            ttk.Button(world, text=label, style='Card.TButton', command=lambda u=url: open_url(u)).pack(anchor='w', pady=(0, 6))

    def show_support(self):
        self.show('help')
        anchor = getattr(self, 'support_anchor', None)
        if anchor is not None:
            self.pages['help'].scroll_to(anchor)

    def link_buttons(self, parent, links):
        holder = self.ttk.Frame(parent)
        for label, url in links:
            self.ttk.Button(holder, text=label, command=lambda u=url: open_url(u)).pack(side='left', padx=(0, 6))
        return holder

    def build_customize(self):
        """Customize (issn 11-05, as bbport's "Launcher customization"): the launcher's language, what it
        does when the game starts, updates and the shortcuts. The look (themes) comes next."""
        ttk = self.ttk
        f = self.scrolled_page('customize', _('Customize'),
                               _("The launcher's look, its language, what it does while you play, and shortcuts."))
        # issn 11-05: a theme for each of Delsin's powers (issn_themes), applied at once.
        self.section(f, _('Look'), top=4)
        themes = [(name, (f'{_(issn_themes.NAMES[name][0])}: {_(issn_themes.NAMES[name][1])}',))
                  for name in issn_themes.ORDER]
        self.row(f, _('Theme'), self.choice(f, 'launcher_theme', 'app', themes, width=44),
                 _("The launcher's colours and what moves on the Play page. It changes at once."))
        self.var('launcher_theme', 'app').trace_add('write', lambda *_a: self.root.after_idle(self.theme_picked))
        self.row(f, _('Effects'), self.choice(f, 'launcher_fx', 'app', FX_LEVELS, width=44),
                 _('The animation on the Play page. It stops by itself while the game runs.'))
        self.var('launcher_fx', 'app').trace_add('write', lambda *_a: self.root.after_idle(self.draw_banner))
        holder = ttk.Frame(f)
        holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(10, 0))
        ttk.Button(holder, text=_('Open the new launcher (test)'), style='Accent.TButton',
                   command=self.open_new_launcher).pack(side='left')
        self.note(f, _('The new look (as in the preview), shown by Windows\' WebView2, with every page of this '
                       'launcher. It remembers the choice, and its side bar has a button back to this one.'), top=4)
        self.section(f, _('Launcher', 'Лаунчер'))
        self.row(f, _('Launcher language', 'Язык лаунчера'), self.choice(f, 'ui_language', 'app', UI_LANGUAGES),
                 _('Applies when the launcher opens again.', 'Применится при следующем открытии лаунчера.'))
        self.section(f, _('While you play'))
        self.check(f, 'close_on_play', 'app', _('Close the launcher when the game starts', 'Закрывать лаунчер при запуске игры'))
        if WINDOWS and UPDATES:
            self.section(f, _('Updates'))
            self.check(f, 'check_updates', 'app', _('Check for updates when the launcher opens',
                                                    'Проверять обновления при открытии лаунчера'))
            holder = ttk.Frame(f)
            holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(10, 0))
            ttk.Button(holder, text=_('Check for updates', 'Проверить обновления'),
                       command=lambda: threading.Thread(target=self.check_update, args=(True,), daemon=True).start()
                       ).pack(side='left')
            ttk.Label(holder, text=f'v{VERSION}', style='Muted.TLabel').pack(side='left', padx=10)
        self.section(f, _('Shortcuts'))
        holder = ttk.Frame(f)
        holder.grid(row=self.next_row(f), column=0, columnspan=2, sticky='w', pady=(6, 0))
        ttk.Button(holder, text=_('Desktop shortcut', 'Ярлык на рабочем столе'), command=self.shortcut).pack(side='left')
        ttk.Button(holder, text=_('Add to Steam'), command=self.add_to_steam).pack(side='left', padx=6)
        self.note(f, _('The desktop shortcut opens this launcher; the Steam entry starts the game straight away with '
                       'these settings.'), top=4)

    def open_new_launcher(self):
        """Customize > Open the new launcher (test): saves, remembers the choice and starts the launcher
        again in the new window (issn_webui); it comes back here by itself when it cannot open."""
        if not self.collect():
            return
        self.app['new_launcher'] = True
        atomic_write(CONFIG_FILE, json.dumps({**load_json(CONFIG_FILE, {}), 'new_launcher': True}, indent=2,
                                             ensure_ascii=False))
        command = [sys.executable] if FROZEN else [sys.executable, str(Path(__file__).resolve())]
        subprocess.Popen(command, cwd=PORT_DIR)
        self.root.destroy()

    def theme_picked(self):
        """Customize > Theme: the window again in the theme's colours (the settings stay in their
        variables, as for a new language)."""
        name = issn_themes.known(self.var('launcher_theme', 'app').get())
        if name == THEME_NAME:
            return
        use_theme(name)
        self.root.configure(bg=BG)
        self.banner_fx = None
        self.rebuild(LANG)

    def build_advanced(self):
        tk, ttk = self.tk, self.ttk
        f = self.scrolled_page('advanced', _('Advanced', 'Дополнительно'),
                               _('Files, diagnostics and the log.'))
        if not WINDOWS:
            self.build_linux_options(f)
        self.section(f, _('Files'), top=4 if WINDOWS else 22)
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
        self.check(body, 'seed_any_profile', 'app', _('Prepare the drawing shaders on any graphics card (test)'),
                   _('NVIDIA and older AMD cards: the first start also prepares the shaders that otherwise compile '
                     'while playing. A test: send the log of the first start and say if anything looks wrong.'),
                   restart=True)
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
        if self.current_page == 'files':
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
        # The keys go to issn.ini too, where the game's menu keeps its changes (they win there).
        keys = control_map(state['app'].get('controls', {}), 'key')
        if keys != str(_disk.get('key_map', '')):
            edited['key_map'] = keys
        for key in MENU_KEYS:  # what the game's menu shows next time
            value = menu_ini_value(state['app'], key)
            if f'menu_{key}' in _disk and str(_disk[f'menu_{key}']) != value:
                edited[f'menu_{key}'] = value
            elif f'menu_{key}' not in _disk and key in state['app']:
                edited[f'menu_{key}'] = value
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
        self.offer_optiscaler_xess()
        # XeSS / DLSS chosen without their files: fetched first, then the game starts (with the
        # game's own anti-aliasing if the download failed: the GPU library falls back).
        upscaler = self.app.get('anti_aliasing')
        if WINDOWS and upscaler in UPSCALER_PACKAGES and upscaler_missing(upscaler) \
                and upscaler not in self.upscaler_tried:
            self.upscaler_tried.add(upscaler)
            self.download_upscaler(upscaler, then=self.play)
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
        first_line = optiscaler_log_line(self.app)  # OptiScaler in bin\: said at the top of the log
        if first_line:
            self.append(first_line)
            run_log.write(first_line)
            if direct_log:
                try:
                    direct_log.write(first_line.encode('utf-8'))
                    direct_log.flush()
                except OSError:
                    pass
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

    def adopt_menu_settings(self):
        """The picture settings changed in the game's menu (menu_* in issn.ini) into the launcher's
        controls, so the next Play uses them and the launcher shows them."""
        ini, _lines = load_ini()
        for key, value in menu_settings(ini).items():
            if self.app.get(key) == value:
                continue
            self.app[key] = value
            if key in self.vars:
                self.vars[key].set(value)

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
                    self.adopt_menu_settings()
                    self.refresh_status()
                    if item[0] not in (0, None):
                        self.root.after(800, self.after_failed_run)
                else:
                    if 'Entering original x86-64 code' in item:
                        self.status.configure(text=_('The game is running.', 'Игра запущена.'), fg=TEXT)
                    elif 'restarting through run.py' in item:
                        self.status.configure(text=_('Restarting with the new settings…', 'Перезапуск с новыми настройками…'), fg=TEXT)
                    self.append(item)
        except queue.Empty:
            pass
        self.root.after(100, self.drain_output)

    def after_failed_run(self):
        """The game ended with an error: the Help page reads its log and opens when it names a cause."""
        if self.process:
            return
        self.run_help()
        if self.help_topics:
            self.show('help')
            self.status.configure(text=_('The game ended with an error: Help shows what the log says.'), fg=ACCENT_HI)

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
        self.update_button = ttk.Button(box, text=_('Update', 'Обновить'), style='Card.Accent.TButton',
                                        command=lambda: self.install_update(version, url, page))
        self.update_button.pack(side='right', padx=(0, self.px(14)), pady=self.px(6))
        ttk.Button(box, text=_("What's new", 'Что нового'), style='Card.TButton', command=lambda: self.show('news')).pack(
            side='right', padx=(0, self.px(8)), pady=self.px(6))
        box.pack(fill='x')
        self.notice.pack(fill='x', after=self.notice_anchor)
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
        """Shader and pipeline caches; they are rebuilt while playing (the package's seed stays)."""
        if self.process:
            return
        clear_shader_cache(Path(self.app['user_dir'] or DATA_DIR / 'user'))
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
        first_line = optiscaler_log_line(settings)
        if first_line:
            log.write(first_line)
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


def release_text(body, limit=2200):
    """A release's notes as plain text for the What's new page: Markdown headings, emphasis,
    code and links reduced to their words, list items as bullets, at most `limit` characters."""
    lines = []
    for line in (body or '').replace('\r', '').split('\n'):
        line = re.sub(r'^#{1,6}\s*', '', line.rstrip())
        line = re.sub(r'^\s*[-*+]\s+', '• ', line)
        line = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', line)          # images
        line = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', line)       # links: their text
        line = re.sub(r'(\*\*|__|`)', '', line)
        line = re.sub(r'(?<![\w*])\*(?!\s)([^*]+)(?<!\s)\*', r'\1', line)
        if line.strip() or (lines and lines[-1]):
            lines.append(line)
    text = '\n'.join(lines).strip()
    if len(text) > limit:
        text = text[:limit].rsplit('\n', 1)[0].rstrip() + '\n…'
    return text


def release_list():
    """The latest GitHub releases, newest first: version, title, date, notes, zip URL, page URL."""
    request = urllib.request.Request(RELEASES_LIST, headers={'Accept': 'application/vnd.github+json',
                                                             'User-Agent': 'issn-launcher'})
    with urllib.request.urlopen(request, timeout=15) as response:
        releases = json.load(response)
    out = []
    for release in releases:
        if release.get('draft'):
            continue
        version = '.'.join(re.findall(r'\d+', release.get('tag_name', '')))
        out.append({'version': version, 'title': release.get('name') or release.get('tag_name') or version,
                    'date': (release.get('published_at') or '')[:10], 'text': release_text(release.get('body')),
                    'zip': next((asset['browser_download_url'] for asset in release.get('assets', [])
                                 if asset.get('name', '').lower().endswith('.zip')), None),
                    'page': release.get('html_url') or RELEASES_PAGE})
    return out


def install_shader_seed(source, target):
    """The release's shader seed (user/cache/<shader-seed.txt>) into the installation: the update
    copy skips user/ (saves, the player's caches), so without this an update from the launcher
    came without its seed and every shader compiled while playing. A folder already there stays."""
    try:
        name = (Path(source) / 'shader-seed.txt').read_text().strip()
    except OSError:
        return False
    if not name or not all(c.isalnum() or c in '-_.' for c in name) or name.startswith('.'):
        return False
    seed = Path(source) / 'user' / 'cache' / name
    dest = Path(target) / 'user' / 'cache' / name
    if not (seed / 'seed-stamp.bin').is_file() or dest.exists():
        return False
    return place_shader_seed(seed, dest)


def place_shader_seed(seed, dest, tries=20, pause=0.5):
    """Copies the seed folder to `dest` through `dest`.part and a rename. Never raises.
    10-30: on Windows the rename failed right after the copy (an antivirus scanning the new files
    holds them), the update went on, and the player's game started on an empty cache folder with
    only issn-seed-<version>.part left: every shader compiled while playing. The rename is tried
    again for a while, then the seed is copied straight under its own name."""
    dest = Path(dest)
    part = dest.with_name(dest.name + '.part')
    try:
        shutil.rmtree(part, ignore_errors=True)
        shutil.copytree(seed, part, dirs_exist_ok=True)
        for attempt in range(tries):
            try:
                part.rename(dest)
                return True
            except OSError:
                if attempt + 1 < tries:
                    time.sleep(pause)
        shutil.copytree(seed, dest, dirs_exist_ok=True)
        shutil.rmtree(part, ignore_errors=True)
        return True
    except OSError as error:
        print(f'Shader seed: could not place it in {dest.parent} ({error})', flush=True)
        return False


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
            install_shader_seed(PORT_DIR, target)
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
    settings = {**APP_DEFAULTS, **migrate_settings(load_json(CONFIG_FILE, {}))}
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
    # issn 11-05: the new launcher (launcher/web in WebView2, issn_webui) when chosen; the classic
    # window below when it cannot open, or with --classic.
    if '--classic' not in args and ('--new-ui' in args or settings.get('new_launcher')):
        try:
            import issn_webui
            if issn_webui.run(sys.modules[__name__], settings):
                return
        except Exception as error:  # noqa: BLE001 - the classic window still opens
            print(f'New launcher failed: {error}', file=sys.stderr)
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
