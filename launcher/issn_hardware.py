# SPDX-License-Identifier: GPL-2.0-or-later
"""What this PC can do with inFAMOUS Second Son, and the settings for the player's goal.

The idea and the score scale come from the Bloodborne port's hardware check (Supermedo's
bloodborne_pc, launcher/bbport_hardware.py, GPL-2.0-or-later: a score per graphics card and per
processor generation); the numbers here are tuned for this port. Second Son leans on the
processor in the open city: the reference PC (Ryzen 7 3700X + RX 9060 XT) runs it at about 95 FPS
there at 1080p, a Ryzen 5 1600 at about 60. So the processor sets the frame rate, the graphics
card the resolution it holds.

No Tk here (tests/test_launcher_hardware.py); the launcher shows the result and applies it.
"""
import ctypes
import re
import sys
from dataclasses import dataclass, field

# Graphics cards: (regex on the Vulkan name, score, video memory in GB). The score is roughly
# relative raster speed (RX 9060 XT 16 GB = 160). The first match wins, so longer names first.
GPUS = [
    (r'rtx 5090', 430, 32), (r'rtx 5080', 300, 16), (r'rtx 5070 ti', 270, 16), (r'rtx 5070', 215, 12),
    (r'rtx 5060 ti', 162, 16), (r'rtx 5060', 140, 8), (r'rtx 5050', 112, 8),
    (r'rtx 4090', 360, 24), (r'rtx 4080', 280, 16), (r'rtx 4070 ti', 230, 12), (r'rtx 4070 super', 205, 12),
    (r'rtx 4070', 175, 12), (r'rtx 4060 ti', 141, 8), (r'rtx 4060', 115, 8), (r'rtx 4050', 92, 6),
    (r'rtx 3090', 205, 24), (r'rtx 3080 ti', 192, 12), (r'rtx 3080', 178, 10), (r'rtx 3070 ti', 148, 8),
    (r'rtx 3070', 140, 8), (r'rtx 3060 ti', 125, 8), (r'rtx 3060', 98, 12), (r'rtx 3050', 70, 8),
    (r'rtx 2080 ti', 155, 11), (r'rtx 2080', 130, 8), (r'rtx 2070', 110, 8), (r'rtx 2060 super', 100, 8),
    (r'rtx 2060', 88, 6), (r'gtx 1660', 72, 6), (r'gtx 1650', 52, 4), (r'gtx 1080 ti', 118, 11),
    (r'gtx 1080', 92, 8), (r'gtx 1070', 80, 8), (r'gtx 1060', 58, 6), (r'gtx 1050', 34, 4),
    (r'gtx 9[78]0', 56, 4),
    (r'rx 9070 xt', 275, 16), (r'rx 9070', 245, 16), (r'rx 9060 xt', 160, 16), (r'rx 9060', 140, 8),
    (r'rx 7900 xtx', 290, 24), (r'rx 7900', 250, 20), (r'rx 7800 xt', 205, 16), (r'rx 7700', 170, 12),
    (r'rx 7600', 122, 8), (r'rx 69[05]0 xt', 220, 16), (r'rx 6800 xt', 200, 16), (r'rx 6800', 175, 16),
    (r'rx 67[05]0', 145, 12), (r'rx 66[05]0 xt', 118, 8), (r'rx 6600', 100, 8), (r'rx 65[05]0', 48, 4),
    (r'rx 64[05]0', 40, 4), (r'rx 5700', 108, 8), (r'rx 5600', 88, 6), (r'rx 5500', 62, 4),
    (r'vega', 90, 8), (r'rx 5[789]0', 66, 8), (r'rx 4[78]0', 60, 8),
    (r'arc b580', 130, 12), (r'arc b570', 115, 10), (r'arc a770', 120, 16), (r'arc a750', 108, 8),
    (r'arc a580', 95, 8), (r'arc a380', 45, 6),
    (r'radeon 8[0-9]0m', 60, 4), (r'radeon 7[0-9]0m', 45, 4), (r'radeon(\(tm\))? graphics', 25, 2),
    (r'intel.*(iris|uhd|arc graphics)', 25, 2),
]

# Processors: score of the desktop chip (Ryzen 7 3700X = 130). Intel Core per generation,
# Ryzen per series, each with its i3/i5/i7/i9 or Ryzen 3/5/7/9 tier.
INTEL_CORE = {14: (142, 192, 222, 235), 13: (138, 188, 215, 230), 12: (132, 168, 195, 205),
              11: (105, 135, 145, 148), 10: (100, 120, 132, 140), 9: (95, 112, 122, 130),
              8: (92, 108, 118, 118), 7: (78, 92, 105, 120), 6: (72, 88, 98, 98), 5: (62, 78, 88, 88),
              4: (62, 78, 88, 88)}
RYZEN = {9000: (200, 215, 235, 242), 8000: (140, 172, 185, 185), 7000: (150, 200, 222, 228),
         6000: (130, 140, 155, 160), 5000: (118, 160, 180, 188), 4000: (85, 105, 115, 118),
         3000: (100, 120, 130, 138), 2000: (72, 92, 102, 102), 1000: (68, 85, 95, 95)}
CORE_ULTRA = {'2': (188, 215, 228), '1': (125, 140, 148)}  # Core Ultra 5 / 7 / 9 by series

# City frame rate per point of score (calibrated on the tester's 3700X: ~95 FPS) and the
# graphics card's at 1080p (RX 9060 XT: ~120 FPS when the processor allows it).
CPU_FPS_PER_POINT = 0.73
GPU_FPS_PER_POINT = 0.75
LAPTOP_SHARE = 0.75  # laptop chips hold lower clocks under load


@dataclass
class Pc:
    gpu_name: str = ''
    gpu_score: int = 0
    vram_gb: int = 0
    integrated: bool = False
    cpu_name: str = ''
    cpu_score: int = 0
    ram_gb: int = 0
    notes: list = field(default_factory=list)

    @property
    def vendor(self):
        name = self.gpu_name.lower()
        return 'nvidia' if 'nvidia' in name or 'geforce' in name else 'amd' if re.search(r'amd|radeon', name) \
            else 'intel' if 'intel' in name else ''

    @property
    def fsr4(self):
        """FSR 4 runs on RX 7000 and RX 9000 cards."""
        return bool(re.search(r'rx [79]\d{3}', self.gpu_name.lower()))

    @property
    def dlss(self):
        return bool(re.search(r'rtx', self.gpu_name.lower()))


def gpu_entry(name):
    """(score, video memory GB) for a Vulkan device name, or (0, 0) when unknown."""
    text = ' '.join(re.sub(r'\((r|tm)\)', ' ', name.lower()).split())
    for pattern, score, vram in GPUS:
        if re.search(pattern, text):
            if re.search(r'laptop|mobile|max-q', text):
                score = round(score * LAPTOP_SHARE)
            return score, vram
    return 0, 0


def cpu_score(name):
    """The processor's score from its Windows name, or 0 when unknown."""
    n = ' '.join(re.sub(r'\((R|TM|tm|r)\)', ' ', name).split())
    laptop = False
    score = 0
    m = re.search(r'\bCore\s+Ultra\s+([579])\s+(\d)\d\d', n)
    if m:
        score = CORE_ULTRA.get(m.group(2), CORE_ULTRA['2'])['579'.index(m.group(1))]
    elif (m := re.search(r'\bi([3579])-(\d{4,5})([A-Z]*)', n)):
        tier, digits = '3579'.index(m.group(1)), m.group(2)
        gen = int(digits[:2]) if len(digits) == 5 else int(digits[0])
        scores = INTEL_CORE.get(min(gen, 14))
        score = scores[tier] if scores else 0
        laptop = bool(re.match(r'(H(?!X)|U|Y|P|M|G\d)', m.group(3)))
    elif (m := re.search(r'\bRyzen\s+([3579])\s+(?:PRO\s+)?(\d)\d{3}([A-Z]*)', n)):
        scores = RYZEN.get(int(m.group(2)) * 1000)
        score = scores['3579'.index(m.group(1))] if scores else 0
        laptop = bool(re.match(r'(H(?!X)|U|HS)', m.group(3)))
    elif re.search(r'\bRyzen\s+AI\b', n):
        score = 185
    return round(score * LAPTOP_SHARE) if laptop and score else score


# ---- detection on this PC -----------------------------------------------------------------------
def processor_name():
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
                return ' '.join(str(winreg.QueryValueEx(key, 'ProcessorNameString')[0]).split())
        except OSError:
            return ''
    try:
        with open('/proc/cpuinfo') as f:
            for line in f:
                if line.startswith('model name'):
                    return ' '.join(line.split(':', 1)[1].split())
    except OSError:
        pass
    return ''


def memory_gb():
    """Installed memory in GB."""
    if sys.platform == 'win32':
        try:
            kb = ctypes.c_ulonglong()
            if ctypes.windll.kernel32.GetPhysicallyInstalledSystemMemory(ctypes.byref(kb)):
                return round(kb.value / 1024 ** 2)
        except (AttributeError, OSError):
            pass
        return 0
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    return round(int(line.split()[1]) / 1024 ** 2)
    except OSError:
        pass
    return 0


def detect(gpus):
    """A Pc for this machine; `gpus` is the launcher's parse_gpu_list rows (index, name, type, ...),
    the game's card first when the tool marks it."""
    pc = Pc()
    rows = sorted(gpus, key=lambda row: (not (len(row) > 4 and row[4] == '1'), 'discrete' not in row[2]))
    if rows:
        pc.gpu_name = rows[0][1]
        pc.integrated = 'discrete' not in rows[0][2]
        pc.gpu_score, pc.vram_gb = gpu_entry(pc.gpu_name)
    pc.cpu_name = processor_name()
    pc.cpu_score = cpu_score(pc.cpu_name)
    pc.ram_gb = memory_gb()
    return pc


# ---- what to expect, and the settings for a goal ------------------------------------------------
RESOLUTION_PIXELS = {'720': 1280 * 720, '900': 1600 * 900, '': 1920 * 1080, '1440': 2560 * 1440,
                     '1800': 3200 * 1800, '2160': 3840 * 2160}


def city_fps(pc):
    """The frame rate the processor allows in the open city (None: unknown processor)."""
    return round(pc.cpu_score * CPU_FPS_PER_POINT) if pc.cpu_score else None


def gpu_fps(pc, render_height=''):
    """The frame rate the graphics card holds at a render size (None: unknown card). Frame time
    grows a little slower than the pixel count (part of it does not depend on the size)."""
    if not pc.gpu_score:
        return None
    ratio = RESOLUTION_PIXELS.get(render_height, RESOLUTION_PIXELS['']) / RESOLUTION_PIXELS['']
    return round(pc.gpu_score * GPU_FPS_PER_POINT / (0.25 + 0.75 * ratio))


def expected_fps(pc, render_height=''):
    values = [v for v in (city_fps(pc), gpu_fps(pc, render_height)) if v]
    return min(values) if values else None


def limit(pc):
    """'cpu' or 'gpu': what sets the frame rate at 1080p, or '' when unknown."""
    c, g = city_fps(pc), gpu_fps(pc)
    if not c or not g:
        return ''
    return 'cpu' if c <= g else 'gpu'


TIERS = ('unknown', 'below', 'minimum', 'good', 'great')


def tier(pc):
    fps = expected_fps(pc)
    if fps is None:
        return 'unknown'
    if fps < 30 or (pc.ram_gb and pc.ram_gb < 12) or pc.integrated:
        return 'below'
    if fps < 55:
        return 'minimum'
    if fps < 90:
        return 'good'
    return 'great'


GOALS = ('smooth', 'balanced', 'quiet')


def settings_for(pc, goal, screen_height=1080):
    """The launcher settings (app keys and issn.ini keys) for the goal on this PC, and the reasons,
    as (settings, reasons). smooth: the highest frame rate up to 120; balanced: 60 FPS with the
    sharpest picture the card holds; quiet: 60 FPS with less work for the card."""
    app, ini, reasons = {}, {}, []
    cpu = city_fps(pc) or 60
    heights = [h for h in ('720', '900', '', '1440', '1800', '2160')
               if (int(h) if h else 1080) <= max(screen_height, 1080)]
    target = 120 if goal == 'smooth' else 60
    if goal == 'smooth' and cpu < 100:
        target = 60 if cpu < 75 else 90
        reasons.append(('cpu', target))
    # The sharpest render size the card holds at the target (with a margin).
    margin = 1.15 if goal == 'quiet' else 1.0
    pick = '720'
    for h in heights:
        g = gpu_fps(pc, h)
        if g is None or g >= target * margin:
            pick = h
    if goal == 'quiet' and pick not in ('720', '900', ''):
        pick = ''
    app['render_height'] = pick
    # Anti-aliasing: FSR 4 on the cards that run it, else the game's own.
    app['anti_aliasing'] = 'fsr4' if pc.fsr4 else 'game'
    app['upscale_quality'] = ''
    # The game's lower settings when the processor cannot hold the target in the city.
    app['perf_mode'] = '' if cpu >= target * 1.05 else 'low' if cpu >= target * 0.8 else 'office'
    if app['perf_mode']:
        reasons.append(('perf', app['perf_mode']))
    app['aniso'] = '16' if (gpu_fps(pc, pick) or 0) >= target * 1.3 else '8'
    ini['fps_limit'] = str(target if target in (60, 120) else 120)
    ini['vsync'] = '1'
    app['frame_pacing'] = ''
    if pc.ram_gb and pc.ram_gb < 16:
        reasons.append(('ram', pc.ram_gb))
    if pc.vram_gb and pc.vram_gb < 6:
        reasons.append(('vram', pc.vram_gb))
    return {'app': app, 'ini': ini}, reasons
