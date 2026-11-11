"""The game's trophies for run.py and the launcher (scripts/trophies.py): the trophy file found
beside the dump, its icons, AES as published (FIPS-197, SP 800-38A), and texts opened with a key
(a made-up key and trophy file here: no real key is in the port)."""
from pathlib import Path
import struct
import tempfile
import unittest

import paths  # noqa: F401
import trophies
import trophy_list

KEY = bytes.fromhex('00112233445566778899AABBCCDDEEFF')  # made up for the test
COMM = 'NPWR99999_00'

CONF = ('<?xml version="1.0" encoding="UTF-8"?>\n<trophyconf version="1.1" platform="ps4">'
        '<npcommid>NPWR99999_00</npcommid>'
        '<trophy id="000" hidden="no" ttype="P" pid="-1"><name>All</name><detail>Every trophy</detail></trophy>'
        '<trophy id="001" hidden="no" ttype="B" pid="000"><name>First</name><detail>Do it</detail></trophy>'
        '<trophy id="002" hidden="yes" ttype="G" pid="000"><name>Second</name><detail>Hidden</detail></trophy>'
        '<trophy id="003" hidden="no" ttype="S"><name>Extra</name><detail>Not for the platinum</detail></trophy>'
        '</trophyconf>')
PT = ('<?xml version="1.0" encoding="UTF-8"?>\n<trophyconf>'
      '<trophy id="000"><name>Tudo</name><detail>Todos</detail></trophy>'
      '<trophy id="001"><name>Primeiro  passo</name><detail>Faça</detail></trophy>'
      '<trophy id="002"><name>Segundo</name><detail>Oculto</detail></trophy>'
      '<trophy id="003"><name>Extra\tum</name><detail>Fora</detail></trophy></trophyconf>')


def esfm(xml, key=KEY, comm=COMM):
    data = xml.encode('utf-8')
    data += b'\0' * (-len(data) % 16)
    iv = bytes(range(16, 32))
    return iv + trophies.cbc_encrypt(trophies.file_key(key, comm), iv, data)


def make_trp(path, entries):
    """entries: [(name, bytes, flag)] laid out as the PS4 does (big-endian, 64-byte entries)."""
    data_start = 0x60 + 64 * len(entries)
    table, blobs, offset = b'', b'', data_start
    for name, blob, flag in entries:
        table += name.encode().ljust(32, b'\0') + struct.pack('>QQI', offset, len(blob), flag).ljust(32, b'\0')
        blobs += blob
        offset += len(blob)
    head = struct.pack('>IIQIII', trophies.TRP_MAGIC, 3, data_start + len(blobs), len(entries), 64, 0).ljust(0x60, b'\0')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head + table + blobs)


def sfo_bytes(**values):
    """A minimal param.sfo (PSF 1.1) holding string values."""
    entries, keys, data = b'', b'', b''
    for key, value in values.items():
        text = value.encode() + b'\0'
        entries += struct.pack('<HHIII', len(keys), 0x204, len(text), len(text), len(data))
        keys += key.encode() + b'\0'
        data += text
    keys_offset = 20 + len(entries)
    return (struct.pack('<4sIIII', b'\0PSF', 0x101, keys_offset, keys_offset + len(keys), len(values))
            + entries + keys + data)


def png(n):
    return b'\x89PNG\r\n\x1a\n' + bytes([n]) * 8


class AesTest(unittest.TestCase):
    def test_published_vectors(self):
        keys = trophies.expand_key(bytes(range(16)))
        block = bytes.fromhex('00112233445566778899aabbccddeeff')
        self.assertEqual(trophies.encrypt_block(keys, block).hex(), '69c4e0d86a7b0430d8cdb78070b4c55a')
        self.assertEqual(trophies.decrypt_block(keys, trophies.encrypt_block(keys, block)), block)
        key, iv = bytes.fromhex('2b7e151628aed2a6abf7158809cf4f3c'), bytes(range(16))
        plain = bytes.fromhex('6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51')
        cipher = trophies.cbc_encrypt(key, iv, plain)
        self.assertEqual(cipher.hex(), '7649abac8119b246cee98e9b12e9197d5086cb9b507219ee95db113a917678b2')
        self.assertEqual(trophies.cbc_decrypt(key, iv, cipher), plain)


class TrophyFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        # A PKG extractor's layout: the game in Image0/, the system files in Sc0/ beside it.
        self.game, self.user = root / 'dump/Image0', root / 'user'
        (self.game / 'sce_sys/trophy').mkdir(parents=True)  # empty, like the tester's dump
        self.user.mkdir()
        self.trp = root / 'dump/Sc0/trophy/trophy00.trp'
        make_trp(self.trp, [('ICON0.PNG', png(9), 0), ('TROP000.PNG', png(0), 0), ('TROP001.PNG', png(1), 0),
                            ('TROP002.PNG', png(2), 0), ('TROP003.PNG', png(3), 0),
                            ('TROPCONF.ESFM', esfm(CONF), 3), ('TROP.ESFM', esfm(CONF), 3),
                            ('TROP_17.ESFM', esfm(PT), 3)])
        (root / 'dump/Sc0/npbind.dat').write_bytes(b'\xd2\x94\xa0\x18' + bytes(60) + COMM.encode() + bytes(20))

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_file_beside_the_dump_gives_count_and_icons(self):
        self.assertEqual(trophies.trophy_file(None, self.game), self.trp)
        entries = trophies.read_entries(self.trp)
        self.assertEqual(trophies.trophy_ids(entries), [0, 1, 2, 3])
        self.assertEqual(trophies.icon(self.trp, entries, 2), png(2))
        self.assertIsNone(trophies.icon(self.trp, entries, 4))
        self.assertEqual(trophies.npbind_file(self.game).name, 'npbind.dat')
        with self.assertRaises(ValueError):
            trophies.read_entries(self.game.parent / 'Sc0/npbind.dat')

    def test_the_file_in_sce_sys_comes_first(self):
        own = self.game / 'sce_sys' / trophies.TROPHY_FILE
        own.write_bytes(self.trp.read_bytes())
        self.assertEqual(trophies.trophy_file(self.game), own)

    def test_texts_open_only_with_the_right_key(self):
        npbind = trophies.npbind_file(self.game)
        texts = trophies.read_texts(self.trp, npbind, KEY, '17')
        self.assertEqual(texts[0], {'grade': 'P', 'base': False, 'hidden': False, 'name': 'Tudo', 'detail': 'Todos'})
        self.assertEqual(texts[1]['name'], 'Primeiro passo')
        self.assertTrue(texts[2]['hidden'] and texts[2]['base'] and texts[2]['grade'] == 'G')
        self.assertFalse(texts[3]['base'])  # no pid: not needed for the platinum
        self.assertEqual(trophies.read_texts(self.trp, npbind, KEY, 1)[1]['name'], 'First')  # no TROP_01: TROP.ESFM
        with self.assertRaisesRegex(ValueError, 'does not open'):
            trophies.read_texts(self.trp, npbind, bytes(16), 17)
        with self.assertRaisesRegex(ValueError, 'npbind'):
            trophies.read_texts(self.trp, None, KEY, 17)

    def test_names_file_for_the_game(self):
        trophies.write_names(self.user, trophies.read_texts(self.trp, trophies.npbind_file(self.game), KEY, 17))
        lines = (self.user / trophies.NAMES_FILE).read_text(encoding='utf-8').splitlines()
        self.assertTrue(lines[0].startswith('#'))
        self.assertEqual(lines[1:], ['0\tP\t0\tTudo', '1\tB\t1\tPrimeiro passo', '2\tG\t1\tSegundo', '3\tS\t0\tExtra um'])

    def test_key_text_and_its_file(self):
        self.assertEqual(trophies.parse_key('0x00112233-44556677 8899aabb:ccddeeff'), KEY)
        self.assertIsNone(trophies.parse_key('0011'))
        self.assertIsNone(trophies.parse_key('zz' * 16))
        self.assertIsNone(trophies.read_key(self.user))
        trophies.save_key(self.user, KEY)
        self.assertEqual(trophies.read_key(self.user), KEY)
        trophies.save_key(self.user, None)
        self.assertFalse((self.user / trophies.KEY_FILE).exists())

    def test_unlocked_list_written_by_the_game(self):
        (self.user / trophies.UNLOCKED_FILE).write_text(
            '# comment\n3 2026-11-04 23:50:12\n1 2026-11-05 00:01:00\nbad\n200 x\n', encoding='utf-8')
        self.assertEqual(trophies.load_unlocked(self.user), {3: '2026-11-04 23:50:12', 1: '2026-11-05 00:01:00'})
        trp, ids, unlocked = trophies.summary(self.user, self.game)
        self.assertEqual((trp, ids, len(unlocked)), (self.trp, [0, 1, 2, 3], 2))

    def test_prepare_for_a_start(self):
        env = {'ISSN_GAME_DIR': str(self.game), 'ISSN_LANGUAGE': '17'}
        line = trophies.prepare(env, self.user)
        self.assertEqual(env['ISSN_TROPHY_FILE'], str(self.trp))
        self.assertIn('4 in', line)
        self.assertIn('no trophy key', line)
        self.assertFalse((self.user / trophies.NAMES_FILE).exists())
        trophies.save_key(self.user, KEY)
        self.assertIn('names: 4', trophies.prepare(env, self.user))
        self.assertTrue((self.user / trophies.NAMES_FILE).is_file())
        trophies.save_key(self.user, bytes(16))  # a wrong key: the old names go
        self.assertIn('does not open', trophies.prepare(env, self.user))
        self.assertFalse((self.user / trophies.NAMES_FILE).exists())
        env = {'ISSN_GAME_DIR': str(self.user), 'ISSN_TROPHY_FILE': 'stale'}
        self.assertIn('no trophy file', trophies.prepare(env, self.user))
        self.assertNotIn('ISSN_TROPHY_FILE', env)


class BuiltinListTest(unittest.TestCase):
    """The port's own names for inFAMOUS Second Son (no key): 48 trophies, the platinum first."""

    def test_list_shape(self):
        self.assertEqual(len(trophy_list.TROPHIES), 48)
        grades = [t[0] for t in trophy_list.TROPHIES]
        self.assertEqual((grades.count('P'), grades.count('G'), grades.count('S'), grades.count('B')), (1, 2, 11, 34))
        self.assertEqual(grades[0], 'P')
        for row in trophy_list.TROPHIES:
            self.assertEqual(len(row), 7)
            self.assertTrue(all(text.strip() == text and text for text in row[1:]), row)

    def test_first_light_shape(self):
        grades = [t[0] for t in trophy_list.FIRST_LIGHT]
        self.assertEqual(len(grades), 25)
        self.assertEqual((grades.count('P'), grades.count('G'), grades.count('S'), grades.count('B')), (1, 7, 11, 6))
        for row in trophy_list.FIRST_LIGHT:
            self.assertEqual(len(row), 7)
            self.assertTrue(all(text.strip() == text and text for text in row[1:]), row)

    def test_languages(self):
        self.assertEqual(trophy_list.texts(17)[6]['name'], 'Coragem')
        self.assertEqual(trophy_list.texts('1')[14]['name'], 'Everybody Out')
        self.assertEqual(trophy_list.texts(20)[39]['name'], 'Autocarnicería total')
        self.assertEqual(trophy_list.texts('x')[0]['name'], 'Enjoy Your Powers')
        texts = trophy_list.texts(17)
        self.assertFalse(texts[0]['base'])
        self.assertTrue(all(texts[i]['base'] for i in range(1, 48)))

    def test_used_only_for_this_game(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            game, user = root / 'dump/Image0', root / 'user'
            (game / 'sce_sys').mkdir(parents=True)
            user.mkdir()
            trp = root / 'dump/Sc0/trophy/trophy00.trp'
            make_trp(trp, [(f'TROP{i:03d}.PNG', png(i), 0) for i in range(48)])
            env = {'ISSN_GAME_DIR': str(game), 'ISSN_LANGUAGE': '17'}
            # Another game (no title ID, no NP id): no names.
            self.assertIn('no names', trophies.prepare(env, user))
            self.assertFalse((user / trophies.NAMES_FILE).exists())
            # inFAMOUS Second Son's NP id in npbind.dat beside it: the port's list.
            (root / 'dump/Sc0/npbind.dat').write_bytes(bytes(64) + trophy_list.COMM_ID.encode() + bytes(20))
            self.assertIn("names: 48 (the port's list)", trophies.prepare(env, user))
            lines = (user / trophies.NAMES_FILE).read_text(encoding='utf-8').splitlines()
            self.assertEqual(lines[1], '0\tP\t0\tAproveite seus poderes')
            self.assertEqual(lines[7], '6\tB\t1\tCoragem')
            # First Light (its own title ID, 25 trophies): its list; Second Son's ids do not apply.
            fl = root / 'fl/Image0'
            (fl / 'sce_sys').mkdir(parents=True)
            fl_trp = root / 'fl/Sc0/trophy/trophy00.trp'
            make_trp(fl_trp, [(f'TROP{i:03d}.PNG', png(i), 0) for i in range(25)])
            (root / 'fl/Sc0/param.sfo').write_bytes(sfo_bytes(TITLE_ID='CUSA00575'))
            fl_texts = trophies.builtin_texts(fl_trp, 17, fl)
            self.assertEqual((len(fl_texts), fl_texts[0]['grade'], fl_texts[1]['name']), (25, 'P', 'Perdida'))
            self.assertEqual(trophies.builtin_texts(fl_trp, 1, fl)[24]['name'], 'Fish in a Barrel')
            self.assertIsNone(trophies.builtin_texts(trp, 17, fl))  # 48 trophies with First Light's id
            # First Light's unlocks go to a file of their own (its ids are other trophies).
            self.assertEqual(trophies.unlocked_file(fl), 'trophies-first-light.txt')
            self.assertEqual(trophies.unlocked_file(game), 'trophies.txt')
            (root / 'trophies.txt').write_text('6 2026-11-05 00:47:49\n')
            (root / 'trophies-first-light.txt').write_text('1 2026-11-11 15:50:43\n')
            self.assertEqual(list(trophies.summary(root, fl)[2]), [1])
            self.assertEqual(list(trophies.summary(root, game)[2]), [6])
            env = {'ISSN_GAME_DIR': str(fl)}
            self.assertIn('1 unlocked', trophies.prepare(env, root))
            self.assertEqual(env['ISSN_TROPHY_BOOK'], 'trophies-first-light.txt')
            # A different number of trophies is another game, whatever the ids say.
            make_trp(trp, [(f'TROP{i:03d}.PNG', png(i), 0) for i in range(10)])
            self.assertIsNone(trophies.builtin_texts(trp, 17, game))


if __name__ == '__main__':
    unittest.main()
