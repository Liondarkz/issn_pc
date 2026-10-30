#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Backups of the game's saves: <user>/savedata copied to <user>/savedata_backups/<time>.

run.py makes one before every start; the launcher lists them and restores one. A restore
first backs up the current saves, so it can always be undone.
"""
import datetime
from pathlib import Path
import shutil

KEEP = 10
STAMP = '%Y%m%d-%H%M%S'


def backups_dir(user_dir):
    return Path(user_dir) / 'savedata_backups'


def list_backups(user_dir):
    """Backup names, newest first."""
    root = backups_dir(user_dir)
    return sorted((p.name for p in root.iterdir() if p.is_dir()), reverse=True) if root.is_dir() else []


def _has_saves(savedata):
    return savedata.is_dir() and any(p.is_file() for p in savedata.rglob('*'))


def _prune(user_dir, keep=KEEP):
    for name in list_backups(user_dir)[keep:]:
        shutil.rmtree(backups_dir(user_dir) / name, ignore_errors=True)


def _copy(user_dir, now):
    savedata = Path(user_dir) / 'savedata'
    if not _has_saves(savedata):
        return None
    root = backups_dir(user_dir)
    root.mkdir(parents=True, exist_ok=True)
    base = (now or datetime.datetime.now()).strftime(STAMP)
    target, n = root / base, 2
    while target.exists():
        target, n = root / f'{base}-{n}', n + 1
    shutil.copytree(savedata, target)
    return target


def backup(user_dir, now=None, keep=KEEP):
    """Copies savedata; returns the new folder, or None when there is nothing to save."""
    made = _copy(user_dir, now)
    if made:
        _prune(user_dir, keep)
    return made


def restore(user_dir, name, now=None):
    """Replaces savedata with backup `name`; returns the backup of the saves it replaced."""
    if not name or name not in list_backups(user_dir):
        raise ValueError(f'No save backup named {name!r}')
    source = backups_dir(user_dir) / name
    savedata = Path(user_dir) / 'savedata'
    # Pruning waits until the copy is done: the restored backup may be the oldest one.
    safety = _copy(user_dir, now)
    staging = Path(user_dir) / 'savedata.restoring'
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(source, staging)
    shutil.rmtree(savedata, ignore_errors=True)
    staging.rename(savedata)
    _prune(user_dir)
    return safety



# ------------------------------------------------------------------------------ import
# Saves from another PC port of the game (HayatoG's iss_pc, or an older copy of this one): the
# same runtime, the same layout, savedata/<user id>/<title id>/<slot>/ with <slot>.sce_sys beside
# it and <title id>.memory/memory.dat, so the folders are copied as they are.
TITLE = 'CUSA00223'
USER_ID = '1'  # src/runtime_services.c USER_ID


def other_port_dirs(env=None, nt=None):
    """Data folders of HayatoG's launcher that exist (its pointer file, %APPDATA%\\inFAMOUS-PC\\
    launcher.json or ~/.config/infamous-pc/launcher.json, key "user_dir")."""
    import json
    import os
    env = os.environ if env is None else env
    nt = (os.name == 'nt') if nt is None else nt
    if nt:
        pointer = Path(env.get('APPDATA') or Path.home() / 'AppData' / 'Roaming') / 'inFAMOUS-PC' / 'launcher.json'
    else:
        pointer = Path(env.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'infamous-pc' / 'launcher.json'
    found = []
    for name in ('ISS_USER_DIR', 'TLG_USER_DIR'):
        if env.get(name):
            found.append(Path(env[name]))
    try:
        data = json.loads(pointer.read_text(encoding='utf-8'))
        if isinstance(data, dict) and str(data.get('user_dir') or '').strip():
            found.append(Path(data['user_dir']))
    except (OSError, ValueError):
        pass
    return [p for p in found if import_source(p)]


def import_source(folder):
    """The title folders to import from `folder`, as {user id: [paths]}: `folder` may be the other
    port's own folder, its user/data folder, its savedata folder, savedata/<user id>, or the
    CUSA00223 folder itself. Empty when there are no saves there."""
    folder = Path(folder)
    if folder.name.lower() == TITLE.lower() and folder.is_dir():
        uid = folder.parent.name if folder.parent.name.isdigit() else USER_ID
        memory = folder.with_name(folder.name + '.memory')
        return {uid: [folder] + ([memory] if memory.is_dir() else [])} if _has_saves(folder) else {}
    for savedata in (folder / 'user' / 'savedata', folder / 'savedata', folder):
        if not savedata.is_dir():
            continue
        users = [savedata] if savedata.name.isdigit() else [p for p in savedata.iterdir() if p.is_dir() and p.name.isdigit()]
        found = {}
        for user in users:
            titles = [p for p in user.iterdir() if p.is_dir() and p.name.lower().startswith(TITLE.lower())]
            if any(_has_saves(p) for p in titles):
                found[user.name] = titles
        if found:
            return found
    return {}


def import_saves(user_dir, folder, now=None):
    """Replaces this port's saves of the game with those in `folder` (import_source). The current
    saves are backed up first; returns (the backup or None, slots imported). Raises ValueError when
    there is nothing to import or `folder` is this port's own saves."""
    savedata = Path(user_dir) / 'savedata'
    found = import_source(folder)
    if not found:
        raise ValueError(f'No inFAMOUS Second Son saves in {folder}')
    for paths in found.values():
        for path in paths:
            if path.resolve().is_relative_to(savedata.resolve()):
                raise ValueError('That folder holds the saves this launcher already uses')
    safety = _copy(user_dir, now)
    slots = 0
    for uid, paths in found.items():
        for path in paths:
            target = savedata / uid / path.name
            staging = target.with_name(target.name + '.importing')
            shutil.rmtree(staging, ignore_errors=True)
            shutil.copytree(path, staging)
            shutil.rmtree(target, ignore_errors=True)
            staging.rename(target)
            if not path.name.endswith('.memory'):
                slots += sum(1 for p in target.iterdir() if p.is_dir() and not p.name.endswith('.sce_sys'))
    _prune(user_dir)
    return safety, slots
