# SPDX-License-Identifier: GPL-2.0-or-later
"""Help for the problems players run into: the launcher checks the installation and reads the
last game log for the messages it knows, and the Help page shows each problem with what it means
and the steps that fix it. After the Bloodborne port's Help page (Supermedo's bloodborne_pc,
launcher/bbport_help.py, GPL-2.0-or-later); the problems and the log messages are this port's.

No Tk here (tests/test_launcher_help.py). Texts are English; the launcher translates them.
"""
import hashlib
import re
from pathlib import Path

# The 01.07 eboot.bin (CUSA00223 and CUSA00004 share it) as the update unpacks it.
EBOOT_107_SHA256 = '2d1ca79630d7bbe6fa29575f59aa7996d43071d74ac8e4f7d40137967d9039ab'

# key: (title, what it means, [steps]).
TOPICS = {
    'no_game': (
        'No game folder yet',
        'The launcher needs the folder of your own dump of the game: the one with eboot.bin, sce_module and '
        'sce_sys.',
        ['Game > Game folder: choose the folder that has eboot.bin in it.',
         'If the 01.07 update is in a folder of its own, choose it in Game > Update folder.']),
    'game_incomplete': (
        'The game folder is incomplete',
        'eboot.bin is there but sce_sys\\param.sfo or the art folder is missing: the dump was not copied whole, '
        'or this is the update alone.',
        ['Copy the whole dump again (eboot.bin, sce_module, sce_sys and art).',
         'With the update in a folder of its own: the base game in Game folder, the update in Update folder.']),
    'version': (
        'The game is not on the 01.07 update',
        'The port is made for the 01.07 update (US CUSA00223 or EU CUSA00004). Another version stops at the start.',
        ['Apply the 01.07 update to your dump (the eboot.bin of 01.07 replaces the old one).',
         'Or choose the folder of the update in Game > Update folder.']),
    'cpu': (
        'This processor lacks instructions the game needs',
        'The game\'s code uses instruction sets this processor does not have, and the port could not translate '
        'them all.',
        ['Send your last_run.log on the Discord: the line starting with "STOP" names what is missing.']),
    'crash': (
        'The game closed with an error',
        'The log ends with a fault. Some are known and fixed in newer versions; others need the log to be found.',
        ['Update the port (What\'s new) if a newer version is out.',
         'Save a report (below) and send it on the Discord with what you were doing when it closed.']),
    'freeze': (
        'The game stopped responding',
        'No new frame for a long time: the game waited for something that never came.',
        ['Update the port if a newer version is out.',
         'Save a report and send it on the Discord, with where in the game it froze.']),
    'device_lost': (
        'The graphics driver stopped the game',
        'The graphics card reported "device lost": the driver reset itself, often from running out of video '
        'memory or an unstable overclock.',
        ['Lower Graphics > Resolution, or use FSR 4 / DLSS quality.',
         'Update the graphics driver; remove a GPU overclock or undervolt for a test.']),
    'memory': (
        'Not enough memory',
        'The game reserves several GB at its start, more at high resolutions.',
        ['Close other programs (browsers, recorders) before playing.',
         'Let Windows manage the page file (System > Advanced > Performance > Virtual memory: automatic).',
         'Lower Graphics > Resolution: 1440p and above need more memory.']),
    'no_seed': (
        'The shaders were not prepared before playing',
        'This start had no shader seed, so every new place and effect compiled its shaders while you played '
        '(stutter in the first hours).',
        ['Install the release zip again over the folder (the seed is in user\\cache), or update from the '
         'launcher.',
         'Let the first start finish its shader preparation (a few minutes, with a progress screen).']),
    'skipping_frames': (
        'The picture was not shown for a while',
        'The window was minimized, covered or lost by the graphics driver, so frames were skipped. If the screen '
        'stayed black while the game was in front, this is the cause.',
        ['Turn Graphics > Allow HDR output off.',
         'Play in Borderless instead of Fullscreen (Graphics > Display mode).',
         'Update the graphics driver.']),
    'dlss_files': (
        'DLSS could not start',
        'DLSS needs NVIDIA\'s Streamline files (not included) of the exact version the port uses.',
        ['Graphics > Open the DLSS folder, and copy the five files from Streamline SDK 2.14.16 (bin\\x64).',
         'A newer Streamline crashed the game for a tester: use 2.14.16.']),
    'xess_files': (
        'XeSS could not start',
        'XeSS needs Intel\'s libxess.dll (not included), and a graphics card with the Vulkan features it uses.',
        ['Graphics > Open the XeSS folder, and copy libxess.dll from the XeSS SDK zip (folder bin).',
         'Update the graphics driver. If XeSS still does not start, pick FSR 4 or the game\'s own.']),
    'location': (
        'The port is in a folder that causes problems',
        'Inside a zip or temporary folder, Program Files or a synced folder (OneDrive) the port cannot write '
        'its files reliably.',
        ['Extract the zip to a normal folder, for example C:\\Games\\inFAMOUS-PC, and start it from there.']),
}

# Log lines (regular expressions) and the topic each means. The first match per topic counts.
LOG_HINTS = [
    (r'version \S+: this port needs the game\'s 01\.07 update', 'version'),
    (r'^STOP: this CPU lacks', 'cpu'),
    (r'^STOP: no new frame for|^STOP: watchdog timeout', 'freeze'),
    (r'^Fault: |^Guest fault \(signal|^Host fault \(signal|^STOP: abort\(\)|^STOP: std::terminate', 'crash'),
    (r'Device lost|ErrorDeviceLost', 'device_lost'),
    (r'out of memory|error 1450|ERROR_NO_SYSTEM_RESOURCES|ErrorOutOfDeviceMemory', 'memory'),
    (r'CacheRebuild: no valid seed-stamp\.bin', 'no_seed'),
    (r'Skipping frame! \((?:[6-9]\d{2}|\d{4,}) in a row', 'skipping_frames'),
    (r'sl\.interposer.*(not found|missing)|Streamline.*(failed to initialize|not supported)', 'dlss_files'),
    (r'XeSS: .*libxess\.(dll|so) not found|XeSS: not available|XeSS unavailable: ', 'xess_files'),
]
TOPIC_ORDER = ['location', 'no_game', 'game_incomplete', 'version', 'cpu', 'memory', 'crash', 'freeze',
               'device_lost', 'skipping_frames', 'no_seed', 'dlss_files', 'xess_files']


def hints_in_log(text):
    """The topics the log's lines point to, in TOPIC_ORDER."""
    found = set()
    for line in (text or '').splitlines():
        for pattern, topic in LOG_HINTS:
            if topic not in found and re.search(pattern, line):
                found.add(topic)
    # Skipped frames while minimized are normal: only when the game also ended badly or the log
    # holds no "minimized" note.
    if 'skipping_frames' in found and re.search(r'surface is 0x0 \(minimized', text or ''):
        found.discard('skipping_frames')
    return [t for t in TOPIC_ORDER if t in found]


def crash_line(text):
    """The log's first fault or STOP line (shown with the crash topic), or ''."""
    for line in (text or '').splitlines():
        if re.match(r'(Fault: |Guest fault|Host fault|STOP: )', line):
            return line.strip()
    return ''


def game_problems(game_dir, update_dir=''):
    """Topics for the game folder: none chosen, incomplete, not 01.07."""
    if not game_dir or not (Path(game_dir) / 'eboot.bin').is_file():
        return ['no_game']
    game = Path(game_dir)
    problems = []
    if not (game / 'sce_sys' / 'param.sfo').is_file() or not (game / 'art').is_dir():
        problems.append('game_incomplete')
    eboot = Path(update_dir) / 'eboot.bin' if update_dir and (Path(update_dir) / 'eboot.bin').is_file() \
        else game / 'eboot.bin'
    try:
        digest = hashlib.sha256(eboot.read_bytes()).hexdigest()
    except OSError:
        digest = ''
    if digest and digest != EBOOT_107_SHA256:
        problems.append('version')
    return problems


ARCHIVE_FOLDER = re.compile(r'[\\/](?:rar\$ex[^\\/]*|7zo[^\\/]*|temp\d*_[^\\/]*\.zip)(?:[\\/]|$)', re.I)


def location_problem(port_dir):
    """True when the port runs from a temporary, protected or synced folder."""
    text = str(port_dir)
    return bool(ARCHIVE_FOLDER.search(text) or re.search(r'[\\/]Program Files( \(x86\))?[\\/]', text, re.I)
                or re.search(r'[\\/]OneDrive[^\\/]*[\\/]', text, re.I)
                or re.search(r'[\\/]AppData[\\/]Local[\\/]Temp[\\/]', text, re.I))


def check(port_dir, game_dir, update_dir, log_text):
    """Every topic for this installation and its last log, in TOPIC_ORDER."""
    found = set(game_problems(game_dir, update_dir)) | set(hints_in_log(log_text))
    if location_problem(port_dir):
        found.add('location')
    return [t for t in TOPIC_ORDER if t in found]
