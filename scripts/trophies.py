# SPDX-License-Identifier: GPL-2.0-or-later
"""The game's trophies (run.py, launcher's Achievements page).

- The trophy file: sce_sys/trophy/trophy00.trp of the player's dump (or Sc0/trophy/ beside it,
  where PKG extractors put it). Its icons (TROPnnn.PNG) are plain PNG files and their count is
  the number of trophies. Read from the player's folder at run time; never copied into the port.
- user/trophies.txt: the trophies unlocked on this PC, one "id date" line each (written by the
  game, src/runtime_trophy.c).
- The texts (names, descriptions, grades) are encrypted (TROP*.ESFM). They open only with the
  player's own trophy key, which the launcher keeps in user/trophy-key.txt on their PC; the port
  never ships one. With it, run.py writes user/trophy-names.txt for the in-game popup.

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
UNLOCKED_FILE = 'trophies.txt'
NAMES_FILE = 'trophy-names.txt'
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
def load_unlocked(user_dir):
    """{id: 'YYYY-MM-DD HH:MM:SS'} from user/trophies.txt ({} when there is none)."""
    unlocked = {}
    try:
        lines = (Path(user_dir) / UNLOCKED_FILE).read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return unlocked
    for line in lines:
        match = re.match(r'\s*(\d+)\s*(.*)$', line)
        if match and int(match.group(1)) < 128:
            unlocked.setdefault(int(match.group(1)), match.group(2).strip())
    return unlocked


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


def write_names(user_dir, texts):
    """user/trophy-names.txt for the game (src/runtime_trophy.c): id, grade, base, name."""
    lines = ['# Trophy names from the player\'s own key (not shipped). id\tgrade\tbase\tname']
    for trophy_id, text in sorted(texts.items()):
        name = text['name'].replace('\t', ' ')
        lines.append(f"{trophy_id}\t{text['grade'] or '-'}\t{int(bool(text['base']))}\t{name}")
    path = Path(user_dir) / NAMES_FILE
    part = path.with_name(path.name + '.part')
    part.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    os.replace(part, path)


def prepare(env, user_dir):
    """run.py, before a start: ISSN_TROPHY_FILE for the game, user/trophy-names.txt from the key
    (removed without one, so names never outlive the key). Returns the line for the log."""
    folders = [env.get('ISSN_PATCH_DIR'), env.get('ISSN_GAME_DIR')]
    trp = trophy_file(*folders)
    names = Path(user_dir) / NAMES_FILE
    unlocked = len(load_unlocked(user_dir))
    if trp is None:
        env.pop('ISSN_TROPHY_FILE', None)
        names.unlink(missing_ok=True)
        return f'Trophies: no trophy file (sce_sys/{TROPHY_FILE}) in the game folder; {unlocked} unlocked on this PC'
    env['ISSN_TROPHY_FILE'] = str(trp)
    try:
        count = len(trophy_ids(read_entries(trp)))
    except (OSError, ValueError) as error:
        return f'Trophies: {error}'
    key = read_key(user_dir)
    note = 'no names (no trophy key)'
    if key is not None:
        try:
            texts = read_texts(trp, npbind_file(*folders), key, env.get('ISSN_LANGUAGE', '1'))
            write_names(user_dir, texts)
            note = f'names: {sum(1 for t in texts.values() if t["name"])}'
        except (OSError, ValueError, KeyError, ElementTree.ParseError) as error:
            names.unlink(missing_ok=True)
            note = f'no names ({error})'
    else:
        names.unlink(missing_ok=True)
    return f'Trophies: {count} in {trp}, {unlocked} unlocked on this PC, {note}'


def summary(user_dir, *folders):
    """For the launcher: (trophy file or None, [ids], {id: date unlocked})."""
    trp = trophy_file(*folders)
    ids = []
    if trp is not None:
        try:
            ids = trophy_ids(read_entries(trp))
        except (OSError, ValueError):
            trp = None
    return trp, ids, load_unlocked(user_dir)
