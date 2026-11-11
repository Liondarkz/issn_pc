# SPDX-License-Identifier: GPL-2.0-or-later
"""The game's trophies (run.py, launcher's Achievements page).

- The trophy file: sce_sys/trophy/trophy00.trp of the player's dump (or Sc0/trophy/ beside it,
  where PKG extractors put it). Its icons (TROPnnn.PNG) are plain PNG files and their count is
  the number of trophies. Read from the player's folder at run time; never copied into the port.
- user/trophies-<title ID>.txt: the trophies unlocked on this PC, one "id date" line each (written
  by the game, src/runtime_services.c). One file per game: Second Son and First Light both number
  their trophies from 0. run.py gives the game its title ID (ISSN_TROPHY_TITLE).
- user/trophies.txt: the one file of 0.3.7 and before (Second Son was the only game); it becomes
  Second Son's file the first time Second Son is chosen (migrate_legacy). First Light never reads it.
- The texts (names, descriptions, grades) are encrypted (TROP*.ESFM). They open only with the
  player's own trophy key, which the launcher keeps in user/trophy-key.txt on their PC; the port
  never ships one. Without it, the names of inFAMOUS Second Son and First Light come from
  trophy_list.py (written by hand from public lists). Either way run.py writes
  user/trophy-names-<title ID>.txt for the in-game popup.

ESFM: AES-128-CBC. The file key is the NP communication id (npbind.dat, "NPWRnnnnn_nn" padded to
16 bytes with zeros) encrypted with the trophy key (CBC, zero IV); each ESFM is a 16-byte IV and
the encrypted XML, which ends at its last '>'.
"""
import os
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ElementTree

TRP_MAGIC = 0xDCA24D00
TROPHY_FILE = 'trophy/trophy00.trp'
UNLOCKED_FILE = 'trophies.txt'  # the shared file of 0.3.7 and before (migrate_legacy)
NAMES_FILE = 'trophy-names.txt'  # likewise; the game reads trophy-names-<title ID>.txt now
FIRST_LIGHT_FILE = 'trophies-first-light.txt'  # First Light's own file in 887b2d1 (migrate_legacy)
KEY_FILE = 'trophy-key.txt'
GRADES = {'P': 'platinum', 'G': 'gold', 'S': 'silver', 'B': 'bronze'}


# ---- where the files are ---------------------------------------------------------------------
def system_file(relative, *folders):
    """`relative` (e.g. 'trophy/trophy00.trp') in a folder's sce_sys/, else in the Sc0/ beside it
    (PKG extractors: Image0/ holds the game, Sc0/ the system files); the first found, or None."""
    for folder in folders:
        if not folder:
            continue
        folder = Path(folder)
        places = [folder / 'sce_sys']
        try:
            places += [entry for entry in folder.parent.iterdir()
                       if entry.is_dir() and entry.name.casefold() == 'sc0' and entry != folder]
        except OSError:
            pass
        for place in places:
            if (place / relative).is_file():
                return place / relative
    return None


def trophy_file(*folders):
    return system_file(TROPHY_FILE, *folders)


def npbind_file(*folders):
    return system_file('npbind.dat', *folders)


# ---- the trophy file -------------------------------------------------------------------------
def read_entries(path):
    """{name: (offset, length, flag)} of a .trp file; ValueError when it is not one."""
    with open(path, 'rb') as f:
        head = f.read(0x60)
        if len(head) < 0x60 or struct.unpack_from('>I', head)[0] != TRP_MAGIC:
            raise ValueError(f'{path}: not a trophy file')
        count, size = struct.unpack_from('>II', head, 16)
        if count > 4096 or not 52 <= size <= 1024:
            raise ValueError(f'{path}: not a trophy file')
        table = f.read(count * size)
    if len(table) < count * size:
        raise ValueError(f'{path}: cut short')
    entries = {}
    for i in range(count):
        entry = table[i * size:(i + 1) * size]
        name = entry[:32].split(b'\0', 1)[0].decode('ascii', 'replace')
        offset, length, flag = struct.unpack_from('>QQI', entry, 32)
        entries[name] = (offset, length, flag)
    return entries


def icon_id(name):
    """The trophy id of an entry named TROPnnn.PNG, else None."""
    match = re.fullmatch(r'TROP(\d{3})\.PNG', name)
    return int(match.group(1)) if match else None


def trophy_ids(entries):
    return sorted(i for i in map(icon_id, entries) if i is not None)


def read_entry(path, entry):
    offset, length, _flag = entry
    with open(path, 'rb') as f:
        f.seek(offset)
        data = f.read(length)
    if len(data) != length:
        raise ValueError(f'{path}: cut short')
    return data


def icon(path, entries, trophy_id):
    """PNG bytes of a trophy's icon, or None."""
    entry = entries.get(f'TROP{trophy_id:03d}.PNG')
    try:
        data = read_entry(path, entry) if entry else None
    except (OSError, ValueError):
        return None
    return data if data and data.startswith(b'\x89PNG') else None


# ---- what this PC unlocked ---------------------------------------------------------------------
def clean_title(title):
    """A title ID usable in a file name ('CUSA00223'), else None."""
    title = str(title or '').strip().upper()
    return title if re.fullmatch(r'[A-Z]{4}\d{5}', title) else None


def unlocked_path(user_dir, title):
    """user/trophies-<title ID>.txt; the old shared user/trophies.txt when the game is unknown."""
    title = clean_title(title)
    return Path(user_dir) / (f'trophies-{title}.txt' if title else UNLOCKED_FILE)


def names_path(user_dir, title):
    title = clean_title(title)
    return Path(user_dir) / (f'trophy-names-{title}.txt' if title else NAMES_FILE)


def _read_unlocked(path):
    unlocked = {}
    try:
        lines = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return unlocked
    for line in lines:
        match = re.match(r'\s*(\d+)\s*(.*)$', line)
        if match and int(match.group(1)) < 128:
            unlocked.setdefault(int(match.group(1)), match.group(2).strip())
    return unlocked


def load_unlocked(user_dir, title=None):
    """{id: 'YYYY-MM-DD HH:MM:SS'} of the game's file ({} when there is none)."""
    return _read_unlocked(unlocked_path(user_dir, title))


def migrate_legacy(user_dir, title):
    """issn 11-11: user/trophies.txt (0.3.7 and before, when Second Son was the only game) becomes
    Second Son's file, lines and dates unchanged; the shared names file goes (run.py writes it at
    every start). Only for Second Son: First Light never takes the old file. When Second Son's
    file already exists, the old file only adds the trophies it lacks. Returns the number of
    trophies moved, or None when there was nothing to move. First Light's trophies-first-light.txt
    (the work branch's first fix, 887b2d1) becomes First Light's file the same way."""
    import trophy_list
    user_dir = Path(user_dir)
    (user_dir / NAMES_FILE).unlink(missing_ok=True)
    title = clean_title(title)
    if title in trophy_list.TITLE_IDS:
        legacy = user_dir / UNLOCKED_FILE
    elif title in trophy_list.FIRST_LIGHT_TITLE_IDS:
        legacy = user_dir / FIRST_LIGHT_FILE  # the work branch's file for a few hours (887b2d1)
    else:
        return None
    if not legacy.is_file():
        return None
    target = unlocked_path(user_dir, title)
    moved = _read_unlocked(legacy)
    if not target.exists():
        os.replace(legacy, target)
        return len(moved)
    have = _read_unlocked(target)
    extra = [f'{i} {when}'.rstrip() for i, when in sorted(moved.items()) if i not in have]
    if extra:
        text = target.read_text(encoding='utf-8', errors='replace')
        part = target.with_name(target.name + '.part')
        part.write_text(text + ('' if not text or text.endswith('\n') else '\n') + '\n'.join(extra) + '\n',
                        encoding='utf-8')
        os.replace(part, target)
    legacy.unlink()
    return len(extra)


# ---- AES-128 (the texts; standard library only: the launcher is a PyInstaller build) ----------
def _tables():
    sbox, inverse = [0] * 256, [0] * 256
    p = q = 1
    while True:  # p runs over GF(2^8)* by 3, q over the inverses by 1/3
        p ^= (p << 1) ^ (0x1B if p & 0x80 else 0)
        p &= 0xFF
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        value = q ^ ((q << 1 | q >> 7) & 0xFF) ^ ((q << 2 | q >> 6) & 0xFF) \
            ^ ((q << 3 | q >> 5) & 0xFF) ^ ((q << 4 | q >> 4) & 0xFF) ^ 0x63
        sbox[p] = value
        inverse[value] = p
        if p == 1:
            break
    sbox[0], inverse[0x63] = 0x63, 0
    return sbox, inverse


SBOX, INV_SBOX = _tables()


def _mul(a, b):
    result = 0
    while b:
        if b & 1:
            result ^= a
        a = ((a << 1) ^ (0x1B if a & 0x80 else 0)) & 0xFF
        b >>= 1
    return result


MUL = {n: [_mul(i, n) for i in range(256)] for n in (2, 3, 9, 11, 13, 14)}


def expand_key(key):
    if len(key) != 16:
        raise ValueError('AES-128 needs a 16-byte key')
    words = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        word = list(words[i - 1])
        if i % 4 == 0:
            word = [SBOX[b] for b in word[1:] + word[:1]]
            word[0] ^= rcon
            rcon = _mul(rcon, 2)
        words.append([a ^ b for a, b in zip(words[i - 4], word)])
    return [sum(words[r * 4:r * 4 + 4], []) for r in range(11)]


def _add(state, key):
    return [a ^ b for a, b in zip(state, key)]


def encrypt_block(keys, block):
    s = _add(list(block), keys[0])
    for r in range(1, 11):
        s = [SBOX[b] for b in s]
        s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]  # ShiftRows (column-major state)
        if r < 10:
            m2, m3 = MUL[2], MUL[3]
            out = []
            for c in range(4):
                a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                out += [m2[a0] ^ m3[a1] ^ a2 ^ a3, a0 ^ m2[a1] ^ m3[a2] ^ a3,
                        a0 ^ a1 ^ m2[a2] ^ m3[a3], m3[a0] ^ a1 ^ a2 ^ m2[a3]]
            s = out
        s = _add(s, keys[r])
    return bytes(s)


def decrypt_block(keys, block):
    s = _add(list(block), keys[10])
    for r in range(9, -1, -1):
        s = [s[(i - 4 * (i % 4)) % 16] for i in range(16)]  # InvShiftRows
        s = [INV_SBOX[b] for b in s]
        s = _add(s, keys[r])
        if r > 0:
            m9, m11, m13, m14 = MUL[9], MUL[11], MUL[13], MUL[14]
            out = []
            for c in range(4):
                a0, a1, a2, a3 = s[4 * c:4 * c + 4]
                out += [m14[a0] ^ m11[a1] ^ m13[a2] ^ m9[a3], m9[a0] ^ m14[a1] ^ m11[a2] ^ m13[a3],
                        m13[a0] ^ m9[a1] ^ m14[a2] ^ m11[a3], m11[a0] ^ m13[a1] ^ m9[a2] ^ m14[a3]]
            s = out
    return bytes(s)


def cbc_encrypt(key, iv, data):
    keys, previous, out = expand_key(key), bytes(iv), bytearray()
    for i in range(0, len(data) - len(data) % 16, 16):
        previous = encrypt_block(keys, bytes(a ^ b for a, b in zip(data[i:i + 16], previous)))
        out += previous
    return bytes(out)


def cbc_decrypt(key, iv, data):
    keys, previous, out = expand_key(key), bytes(iv), bytearray()
    for i in range(0, len(data) - len(data) % 16, 16):
        block = data[i:i + 16]
        out += bytes(a ^ b for a, b in zip(decrypt_block(keys, block), previous))
        previous = block
    return bytes(out)


# ---- the texts -----------------------------------------------------------------------------------
def parse_key(text):
    """The trophy key as 16 bytes from 32 hex digits (spaces, dashes and 0x allowed), else None."""
    digits = re.sub(r'0x|[\s:-]', '', str(text or ''), flags=re.IGNORECASE)
    return bytes.fromhex(digits) if re.fullmatch(r'[0-9a-fA-F]{32}', digits) else None


def read_key(user_dir):
    try:
        return parse_key((Path(user_dir) / KEY_FILE).read_text(encoding='ascii', errors='replace'))
    except OSError:
        return None


def save_key(user_dir, key):
    """Keeps the key on this PC only (user/trophy-key.txt); None removes it."""
    path = Path(user_dir) / KEY_FILE
    if key is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key.hex().upper() + '\n', encoding='ascii')


def np_comm_id(data):
    """The NP communication id in an npbind.dat ('NPWR12345_00'), or None."""
    match = re.search(rb'NPWR\d{5}_\d{2}', data or b'')
    return match.group().decode() if match else None


def file_key(trophy_key, comm_id):
    return cbc_encrypt(trophy_key, bytes(16), comm_id.encode('ascii').ljust(16, b'\0')[:16])


def decrypt_esfm(data, key):
    """An ESFM entry as XML bytes; ValueError when the key does not open it."""
    if len(data) < 32:
        raise ValueError('trophy text too short')
    plain = cbc_decrypt(key, data[:16], data[16:])
    if not plain.lstrip(b'\xef\xbb\xbf').startswith(b'<?xml'):
        raise ValueError('the trophy key does not open this trophy file')
    return plain[:plain.rfind(b'>') + 1]


def _trophies_of(xml_bytes):
    root = ElementTree.fromstring(xml_bytes)
    return {int(node.get('id', '-1')): node for node in root.iter('trophy') if node.get('id', '').strip().isdigit()}


def read_texts(trp, npbind, key, language=1):
    """{id: {'grade', 'base', 'hidden', 'name', 'detail'}} in the game language (PS4 language id:
    TROP_17.ESFM for Brazilian Portuguese; TROP.ESFM when the game has no such file).
    ValueError with the reason when they cannot be read."""
    if key is None:
        raise ValueError('no trophy key')
    try:
        comm = np_comm_id(Path(npbind).read_bytes()) if npbind else None
    except OSError:
        comm = None
    if not comm:
        raise ValueError('no NP communication id (npbind.dat) in the game folder')
    entries = read_entries(trp)
    secret = file_key(key, comm)
    conf = _trophies_of(decrypt_esfm(read_entry(trp, entries['TROPCONF.ESFM']), secret)) \
        if 'TROPCONF.ESFM' in entries else {}
    name = f'TROP_{int(language):02d}.ESFM'
    texts_entry = entries.get(name) or entries.get('TROP.ESFM')
    if texts_entry is None:
        raise ValueError('no trophy texts in the trophy file')
    texts = _trophies_of(decrypt_esfm(read_entry(trp, texts_entry), secret))
    result = {}
    for trophy_id in sorted(set(conf) | set(texts)):
        c, t = conf.get(trophy_id), texts.get(trophy_id)
        grade = (c.get('ttype', '') if c is not None else '').upper()[:1]
        pid = c.get('pid') if c is not None else None
        result[trophy_id] = {
            'grade': grade if grade in GRADES else '',
            'base': grade != 'P' and pid is not None and pid.strip().lstrip('-').isdigit() and int(pid) != -1,
            'hidden': (c.get('hidden', 'no') if c is not None else 'no').lower() == 'yes',
            'name': ' '.join(((t if t is not None else c).findtext('name') or '').split()),
            'detail': ' '.join(((t if t is not None else c).findtext('detail') or '').split()),
        }
    return result


def game_ids(*folders):
    """(title ID, NP communication id) of the game in the folders; None for what is not found."""
    title = comm = None
    sfo = system_file('param.sfo', *folders)
    if sfo is not None:
        try:
            from prepare import load_sfo
            title = load_sfo(sfo).get('TITLE_ID')
        except (ImportError, OSError, ValueError):
            title = None
    npbind = npbind_file(*folders)
    if npbind is not None:
        try:
            comm = np_comm_id(npbind.read_bytes())
        except OSError:
            comm = None
    return title, comm


def builtin_texts(trp, language, *folders):
    """The port's own list (trophy_list.py) for the game of this trophy file: Second Son's or
    First Light's, found by the number of trophies and the title ID (or Second Son's NP
    communication id); else None."""
    import trophy_list
    try:
        count = len(trophy_ids(read_entries(trp)))
    except (OSError, ValueError):
        return None
    title, comm = game_ids(*folders)
    for title_ids, comm_id, trophies in trophy_list.LISTS:
        if count == len(trophies) and (title in title_ids or (comm_id and comm == comm_id)):
            return trophy_list.texts(language, trophies)
    return None


def game_texts(trp, language, *folders, key=None):
    """(texts, source): the game's own texts with the player's key ('key'), else the port's list
    ('list'), else ({}, None). ValueError from a key that does not open the file is passed on."""
    if key is not None:
        return read_texts(trp, npbind_file(*folders), key, language), 'key'
    texts = builtin_texts(trp, language, *folders)
    return (texts, 'list') if texts else ({}, None)


def write_names(user_dir, texts, title=None):
    """user/trophy-names-<title ID>.txt for the game (src/runtime_trophy.c): id, grade, base, name."""
    lines = ['# Trophy names from the player\'s own key (not shipped). id\tgrade\tbase\tname']
    for trophy_id, text in sorted(texts.items()):
        name = text['name'].replace('\t', ' ')
        lines.append(f"{trophy_id}\t{text['grade'] or '-'}\t{int(bool(text['base']))}\t{name}")
    path = names_path(user_dir, title)
    part = path.with_name(path.name + '.part')
    part.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    os.replace(part, path)


def game_title(*folders):
    """The title ID of the game in the folders (param.sfo, update first), or None."""
    return clean_title(game_ids(*folders)[0])


def prepare(env, user_dir, title=None):
    """run.py, before a start: ISSN_TROPHY_FILE and ISSN_TROPHY_TITLE for the game (its own
    trophies-<title ID>.txt), user/trophy-names-<title ID>.txt from the key or the port's list
    (removed without them, so names never outlive the key). Returns the line for the log."""
    folders = [env.get('ISSN_PATCH_DIR'), env.get('ISSN_GAME_DIR')]
    title = clean_title(title) or game_title(*folders)
    if title:
        env['ISSN_TROPHY_TITLE'] = title
    else:
        env.pop('ISSN_TROPHY_TITLE', None)
    moved = migrate_legacy(user_dir, title)
    trp = trophy_file(*folders)
    names = names_path(user_dir, title)
    unlocked = len(load_unlocked(user_dir, title))
    where = f'{unlocked} unlocked on this PC ({unlocked_path(user_dir, title).name})'
    if moved is not None:
        where += f'; {moved} moved from the old {UNLOCKED_FILE}'
    if trp is None:
        env.pop('ISSN_TROPHY_FILE', None)
        names.unlink(missing_ok=True)
        return f'Trophies: no trophy file (sce_sys/{TROPHY_FILE}) in the game folder; {where}'
    env['ISSN_TROPHY_FILE'] = str(trp)
    try:
        count = len(trophy_ids(read_entries(trp)))
    except (OSError, ValueError) as error:
        return f'Trophies: {error}'
    key = read_key(user_dir)
    language = env.get('ISSN_LANGUAGE', '1')
    try:
        texts, source = game_texts(trp, language, *folders, key=key)
    except (OSError, ValueError, KeyError, ElementTree.ParseError) as error:
        texts, source = {}, None
        note = f'no names ({error})'
    else:
        note = 'no names (no trophy key)'
    if texts:
        write_names(user_dir, texts, title)
        origin = 'the trophy key' if source == 'key' else "the port's list"
        note = f'names: {sum(1 for t in texts.values() if t["name"])} ({origin})'
    else:
        names.unlink(missing_ok=True)
    return f'Trophies: {count} in {trp}, {where}, {note}'


def game_name(*folders):
    """'inFAMOUS Second Son' / 'inFAMOUS First Light' for the game in the folders, else its title
    ID, else '' (the launcher's Achievements page says which game it shows)."""
    import trophy_list
    title = game_title(*folders)
    if title in trophy_list.TITLE_IDS:
        return 'inFAMOUS Second Son'
    if title in trophy_list.FIRST_LIGHT_TITLE_IDS:
        return 'inFAMOUS First Light'
    return title or ''


def remove_names(user_dir):
    """Every names file (the key was removed): run.py writes them again at the next start."""
    for path in [Path(user_dir) / NAMES_FILE, *Path(user_dir).glob('trophy-names-*.txt')]:
        path.unlink(missing_ok=True)


def summary(user_dir, *folders):
    """For the launcher: (trophy file or None, [ids], {id: date unlocked}) of the game chosen in
    the folders, from its own trophies-<title ID>.txt (the old shared file moved first)."""
    title = game_title(*folders)
    migrate_legacy(user_dir, title)
    trp = trophy_file(*folders)
    ids = []
    if trp is not None:
        try:
            ids = trophy_ids(read_entries(trp))
        except (OSError, ValueError):
            trp = None
    return trp, ids, load_unlocked(user_dir, title)
