#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Startup graphics options of issn.ini (gpu/shim/issn_graphics.h) for run.py.

The in-game menu and the launcher write them; run.py turns them into game patches and
environment variables at every start, so "Apply and restart" uses the new values. After a
restart the game confirms them (restart_unconfirmed=0) once it shows frames; when a launch
never did, the next start goes back to the last confirmed values.
"""
import json
from pathlib import Path

RESOLUTIONS = ('native', '480p', '720p', '1440p', '1800p', '4K')
ENGINE_FPS = (60, 120, 240)
DEFAULTS = {'render_resolution': 'native', 'engine_fps': '120', 'deferred_readback': '1',
            'stale_readback': '1', 'async_shaders': '1'}
RESOLUTION_PREFIX = 'Resolution Patch'
FPS_PREFIX = 'Frame Rate Patch'
TEXTURE_FIX = 'Bug Fix - Texture Corruption Fix'


def read_ini(path):
    values = {}
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return values
    for line in lines:
        if '=' in line and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return values


def write_keys(path, updates):
    """Rewrites `updates` in place and appends missing keys, keeping every other line."""
    path = Path(path)
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        lines = ['# issn settings (in-game menu: Insert / R3+L2)']
    done, out = set(), []
    for line in lines:
        key = line.split('=', 1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else None
        if key in updates:
            out.append(f'{key}={updates[key]}')
            done.add(key)
        else:
            out.append(line)
    out += [f'{k}={v}' for k, v in updates.items() if k not in done]
    path.write_text('\n'.join(out) + '\n', encoding='utf-8')


def startup_options(ini):
    """The startup options, normalized; unknown values fall back to the defaults."""
    options = {key: ini.get(key, value) for key, value in DEFAULTS.items()}
    if options['render_resolution'] not in RESOLUTIONS:
        options['render_resolution'] = 'native'
    if options['engine_fps'] not in {str(f) for f in ENGINE_FPS}:
        options['engine_fps'] = '120'
    for key in ('deferred_readback', 'stale_readback', 'async_shaders'):
        options[key] = '0' if options[key] == '0' else '1'
    return options


def prepare(config, state_path):
    """(options, reverted): the startup options of this launch, restoring the last confirmed
    ones when the previous launch after a restart never confirmed its own."""
    ini = read_ini(config)
    state = {}
    try:
        state = json.loads(Path(state_path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    options = startup_options(ini)
    reverted = False
    if ini.get('restart_unconfirmed') == '1':
        if state.get('attempt') and state.get('last_good'):
            options = startup_options(state['last_good'])
            write_keys(config, {**options, 'restart_unconfirmed': '0'})
            state['attempt'] = False
            reverted = True
        else:
            state['attempt'] = True
    else:
        state = {'last_good': options, 'attempt': False}
    Path(state_path).write_text(json.dumps(state, indent=2), encoding='utf-8')
    return options, reverted


def patch_names(names, options, texture_fix_available=True):
    """The launcher's patch list with the resolution and frame rate patches of `options`."""
    names = [n for n in names if not n.startswith(RESOLUTION_PREFIX) and not n.startswith(FPS_PREFIX)]
    resolution = options['render_resolution']
    if resolution != 'native':
        names = [n for n in names if n != TEXTURE_FIX] + [f'{RESOLUTION_PREFIX} - {resolution}']
    elif texture_fix_available and TEXTURE_FIX not in names:
        names.insert(0, TEXTURE_FIX)
    if options['engine_fps'] != '60':
        names.append(f'{FPS_PREFIX} - {options["engine_fps"]} FPS')
    return names


def restrict_to_available(names, available):
    """names without the patches this XML lacks. A missing frame rate patch is replaced by the
    highest one it has below the requested rate (the 240 FPS patch of the first XML has no
    counterpart in the second)."""
    kept = [n for n in names if n in available]
    for name in names:
        if name in available:
            continue
        print(f'Patches: {name} is not available for this game build: skipped', flush=True)
        if name.startswith(f'{FPS_PREFIX} - '):
            wanted = int(name.split(' - ')[1].split()[0])
            lower = [int(n.split(' - ')[1].split()[0]) for n in available if n.startswith(f'{FPS_PREFIX} - ')
                     and int(n.split(' - ')[1].split()[0]) <= wanted]
            if lower:
                kept.append(f'{FPS_PREFIX} - {max(lower)} FPS')
                print(f'Patches: using {kept[-1]} instead', flush=True)
    return kept


def applied_engine_fps(names):
    """The engine rate the applied patch list gives: 60 without a frame rate patch."""
    return max((int(n.split(' - ')[1].split()[0]) for n in names if n.startswith(f'{FPS_PREFIX} - ')), default=60)


def environment(options):
    return {'ISSN_DEFERRED_READBACK': options['deferred_readback'],
            'ISSN_STALE_READBACK': options['stale_readback'],
            'ISSN_ASYNC_SHADERS': options['async_shaders']}


# issn-axy (tester 10-31): "Apply and restart" in the menu also starts the anti-aliasing, the
# upscaler quality and frame generation chosen there (menu_* keys, the launcher's values). The
# launcher's own start keeps its environment; a menu restart (restart_unconfirmed=1) takes these.
AA_SWITCHES = ('SHADPS4_DLSS', 'SHADPS4_DLSS_UPSCALE', 'ISSN_FSR4', 'ISSN_XESS')
UPSCALE_FACTORS = ('1.5', '1.7', '2', '3')


def menu_environment(ini, windows):
    """(set, remove) for the environment of a restart from the in-game menu, as the launcher's
    game_environment would make them from the same choices; ({}, ()) when the ini has none."""
    if 'menu_anti_aliasing' not in ini:
        return {}, ()
    aa = ini.get('menu_anti_aliasing', 'game')
    env = {}
    if aa == 'fsr4':
        env['ISSN_FSR4'] = '1'
    elif aa == 'xess' and windows:
        env['ISSN_XESS'] = '1'
    elif aa == 'dlss' and windows:
        env['SHADPS4_DLSS'] = '1'
        env['SHADPS4_DLSS_UPSCALE'] = '1'
    if ini.get('menu_frame_gen') == '1' and any(k in env for k in AA_SWITCHES):
        env['ISSN_FRAME_GEN'] = '1'
    if ini.get('menu_upscale_quality') in UPSCALE_FACTORS:
        env['ISSN_UPSCALE_FACTOR'] = ini['menu_upscale_quality']
    remove = tuple(k for k in (*AA_SWITCHES, 'ISSN_UPSCALE_FACTOR', 'ISSN_FRAME_GEN') if k not in env)
    return env, remove

