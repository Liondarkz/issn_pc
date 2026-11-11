"""Read-only combined view of a dumped base game and its separate update folder.

The update (eboot.bin, sce_sys, ...) is laid over the base game in a temporary folder, so
neither dump is modified. Whatever the update does not touch is linked as a whole (a symlink,
or a junction / hard link on Windows without the symlink privilege, see mods.make_link), so
a 47 GB game costs a handful of links. Names are matched without regard to case.
"""
from pathlib import Path
import shutil
import tempfile

from mods import is_link, make_link, remove_link


def overlay(source, dest):
    """Lays source over dest: its files replace dest's, folders are merged, names are
    case-insensitive. dest holds links to the base; a folder both have is made a real one."""
    entries = {entry.name.casefold(): entry for entry in dest.iterdir()}
    for item in source.iterdir():
        match = entries.get(item.name.casefold(), dest / item.name)
        if item.is_dir() and match.exists() and match.is_dir():
            if is_link(match):  # a linked folder of the base: replace the link by a merged copy
                inner = match.resolve(strict=True)
                remove(match)
                match.mkdir()
                for child in inner.iterdir():
                    make_link(match / child.name, child)
            overlay(item, match)
        else:
            if match.exists() or is_link(match):
                remove(match)
            make_link(match, item)


def remove(path):
    """Removes a file, link or folder of a view without following links into the dumps."""
    path = Path(path)
    if is_link(path):
        # remove_link also drops the entry the Windows junction/hard-link fallback recorded in
        # mods.LINKED; without it is_link() keeps reporting a recreated real folder as a link.
        remove_link(path)
    elif path.is_dir():
        for child in list(path.iterdir()):
            remove(child)
        path.rmdir()
    else:
        path.unlink()


def system_folder(folder):
    """The Sc0 folder beside a dump's Image0 when the game folder has no sce_sys/param.sfo of
    its own (PKG extractors: <dump>/Image0 is the game, <dump>/Sc0 its system files), else None."""
    folder = Path(folder)
    beside = folder.parent / 'Sc0'
    if not (folder / 'sce_sys' / 'param.sfo').is_file() and (beside / 'param.sfo').is_file():
        return beside
    return None


def needs_view(base):
    """True when the game folder only runs through a view (its system files are in Sc0)."""
    return system_folder(base) is not None


def lay_system_files(source, target):
    """Lays a Sc0 folder over the view's sce_sys (made when the game has none)."""
    names = {entry.name.casefold(): entry for entry in target.iterdir()}
    match = names.get('sce_sys', target / 'sce_sys')
    if not match.exists():
        match.mkdir()
    elif is_link(match):  # the game's own sce_sys: replace the link by a merged copy
        inner = match.resolve(strict=True)
        remove(match)
        match.mkdir()
        for child in inner.iterdir():
            make_link(match / child.name, child)
    overlay(source, match)


def create_view(base, update, out):
    """The folder holding base with update over it (update may be None), made in out; the
    caller removes it. A Sc0 folder beside either one becomes part of the view's sce_sys."""
    from patches import game_title_id
    base = Path(base).resolve()
    update = Path(update).resolve() if update else None
    if update:
        base_id, update_id = game_title_id(base), game_title_id(update)
        if not base_id or base_id != update_id:
            raise ValueError(f'Game and update are not the same title: {base_id} / {update_id}')
        if base == update or base in update.parents or update in base.parents:
            raise ValueError('Choose two separate folders for the game and its update')
        if not (update / 'eboot.bin').is_file():
            raise ValueError('The update folder needs eboot.bin and sce_sys/param.sfo')
    Path(out).mkdir(parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix='game-update-', dir=out))
    try:
        for entry in base.iterdir():
            make_link(target / entry.name, entry)
        for folder in (base, update):
            if not folder:
                continue
            system = system_folder(folder)
            if folder is update:
                overlay(update, target)
            if system:
                lay_system_files(system, target)
    except BaseException:
        remove_view(target)
        raise
    return target


def remove_view(path):
    """Deletes a view made by create_view (the dumps stay untouched)."""
    if Path(path).exists():
        remove(path)
