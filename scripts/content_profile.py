"""Prepare explicit offline AppContent metadata; SKU is a probe setting, not a license check.

issn 11-06: add-ons (DLC). inFAMOUS Second Son only asks sceAppContentGetAddcontInfo whether an
entitlement label exists (it reads no add-on files: claude/dlc-estudo.md), so an add-on is a label
taken from the param.sfo of the player's own add-on dump. Nothing is invented: no folder, no
add-on. The game's own UNLOCKAPPLICATIO label is never answered."""
import argparse
import json
import os
import re
import struct
from pathlib import Path
from prepare import load_sfo, sfo

# Entitlement labels the 01.07 eboot asks for (table at 0xd76ce0, ids 1-7), with the add-on's
# title as its param.sfo gives it. Id 0, UNLOCKAPPLICATIO, is the game itself, not an add-on.
ADDONS = {
    'UNLOCKCOLEMISSIO': "Cole's Legacy",
    'UNLOCKCOLEJACKET': "Cole's Jacket",
    'UNLOCKVESTHANDPR': "Cole's Legendary Vest (Collector's Edition)",
    'UNLOCKVESTJOS000': 'JOS vest',
    'UNLOCKVESTPENNYA': 'Penny Arcade vest',
    'UNLOCKVESTEXIST0': 'EXIST vest',
    'UNLOCKVESTSKELET': 'iam8bit vest',
}
LABEL_OFFSET = 20       # UP9000-CUSA00223_00-<label>
SFO_PLACES = ('Sc0', 'sce_sys', 'Image0/sce_sys')
LICENSES = ('license.dat', 'license.info')
MAX_FOLDERS = 400       # folders looked at under the chosen one (three levels deep)


def _candidates(folder):
    """The chosen folder and three levels of subfolders (one add-on per folder in every dump layout
    seen: <dlc>/Sc0/param.sfo from PKG dumpers, addcont/<title>/<dlc>/sce_sys/param.sfo)."""
    found, level = [folder], [folder]
    for _ in range(3):
        nxt = []
        for parent in level:
            try:
                children = sorted(p for p in parent.iterdir() if p.is_dir())
            except OSError:
                continue
            for child in children:
                if child.name in SFO_PLACES or child.name in ('Image0', 'Sc0'):
                    continue
                nxt.append(child)
                if len(found) + len(nxt) >= MAX_FOLDERS:
                    return found + nxt
        found += nxt
        level = nxt
    return found


def windows_path(text, nt=None):
    """MSYS2 leaves some arguments as /d/Games/... (e.g. with brackets or @ in the name) when it
    starts a Windows Python; turn that spelling into D:/Games/... on Windows."""
    text = str(text)
    if (os.name == 'nt' if nt is None else nt):
        match = re.match(r'^/([A-Za-z])(/.*)?$', text)
        if match:
            return f'{match.group(1).upper()}:{match.group(2) or "/"}'
    return text


def scan_addons(folder, title_id, off=()):
    """Add-ons in `folder` for the game `title_id`: (found, skipped). Each found entry is a dict
    with label, name, title, content_id, folder and on; each skipped entry is (folder, reason).
    Only folders that hold a param.sfo are reported."""
    folder, off = Path(windows_path(folder)), set(off)
    found, skipped, seen = [], [], set()
    if not folder.is_dir():
        return found, [(str(folder), 'folder not found')]
    for place in _candidates(folder):
        sfo_path = next((place / sub / 'param.sfo' for sub in SFO_PLACES if (place / sub / 'param.sfo').is_file()), None)
        if sfo_path is None:
            continue
        where = str(place)
        try:
            values = load_sfo(sfo_path)
        except (OSError, ValueError) as error:
            skipped.append((where, f'unreadable param.sfo ({error})'))
            continue
        content_id = values.get('CONTENT_ID')
        if values.get('CATEGORY') != 'ac':
            skipped.append((where, f"not an add-on (CATEGORY {values.get('CATEGORY')!r})"))
            continue
        if not isinstance(content_id, str) or len(content_id) <= LABEL_OFFSET:
            skipped.append((where, 'param.sfo has no valid CONTENT_ID'))
            continue
        owner = content_id[7:16]
        if owner != title_id or values.get('TITLE_ID', title_id) != title_id:
            skipped.append((where, f'add-on for {owner}, the game is {title_id}'))
            continue
        label = content_id[LABEL_OFFSET:]
        if label not in ADDONS:
            skipped.append((where, f'label {label} is not one the game asks for'))
            continue
        if not any((sfo_path.parent / name).is_file() for name in LICENSES):
            skipped.append((where, 'no license.dat or license.info next to param.sfo (not a dump)'))
            continue
        if label in seen:
            skipped.append((where, f'{label} already found in another folder'))
            continue
        seen.add(label)
        found.append(dict(label=label, name=ADDONS[label], title=values.get('TITLE', ''),
                          content_id=content_id, folder=where, on=label not in off))
    return found, skipped


def prepare(game, out, sku='full', addcont=None, addcont_off=()):
    values=sfo((game/'sce_sys/param.sfo').read_bytes())
    params=[values.get(f'USER_DEFINED_PARAM_{i}',0) for i in range(1,5)]
    if any(type(v) is not int or not 0<=v<=0xffffffff for v in params):
        raise ValueError('invalid user-defined parameter')
    found, skipped = scan_addons(addcont, values.get('TITLE_ID', ''), addcont_off) if addcont else ([], [])
    mounted=[a for a in found if a['on']]
    profile=dict(title_id=values.get('TITLE_ID'),sku=sku,sku_source='explicit probe setting',
                 user_params=params, addcont_dir=str(addcont) if addcont else None,
                 mounted_addons=mounted, disabled_addons=[a for a in found if not a['on']],
                 skipped_addons=[dict(folder=f, reason=r) for f, r in skipped], boot_attr=0)
    out.mkdir(parents=True,exist_ok=True)
    head=struct.pack('<8s5I',b'G3CONT02' if mounted else b'G3CONT01',{'full':3,'trial':1}[sku],*params)
    if mounted:
        head+=struct.pack('<I',len(mounted))+b''.join(a['label'].encode('ascii').ljust(16,b'\0') for a in mounted)
    (out/'content.bin').write_bytes(head)
    (out/'content-profile.json').write_text(json.dumps(profile,indent=2)+'\n')
    print(f'AppContent profile: SKU={sku} (probe setting), user params={params}, mounted add-ons={len(mounted)}')
    if addcont:
        print(f'AppContent add-on folder: {addcont}')
    for a in found:
        state = 'on' if a['on'] else 'off (turned off in the launcher)'
        print(f"  add-on {a['label']} \"{a['name']}\" {state}: {a['folder']}")
    for where, reason in skipped:
        print(f'  add-on folder skipped: {where}: {reason}')
    if addcont and not found and not skipped:
        print('  no add-on param.sfo found there (looked in Sc0, sce_sys and Image0/sce_sys, three levels deep)')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('game',type=Path)
    p.add_argument('--out',type=Path,default=Path(__file__).resolve().parent.parent/'out')
    p.add_argument('--sku',choices=['full','trial'],default='full')
    p.add_argument('--addcont',type=Path,help="folder with the player's own add-on dumps")
    p.add_argument('--addcont-off',default='',help='comma-separated labels left off')
    a=p.parse_args()
    prepare(a.game,a.out,a.sku,a.addcont,[x.strip() for x in a.addcont_off.split(',') if x.strip()])
