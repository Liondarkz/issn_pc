import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path

from paths import ROOT


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_tool = load('make_shader_seed', ROOT / 'tools' / 'make_shader_seed.py')
run = load('issn_run', ROOT / 'run.py')


def stamp_bytes(shaders=2):
    def string(text):
        data = text.encode()
        return struct.pack('<I', len(data)) + data
    body = struct.pack('<II', 0x50545353, 3) + string('CUSA00223') + string('01.07') + \
        string('issn_pc exe 0123') + struct.pack('<Q', 0x5dc0133075c46f0e) + struct.pack('<I', 4) + \
        b'\0' * 4 + b'\1' + struct.pack('<I', shaders)
    for i in range(shaders):
        body += struct.pack('<QIIB', 0xd99ad3 + i, 7, 64, 1) + b'\0'
    return body


class ShaderSeedTest(unittest.TestCase):
    def test_seed_keeps_numbers_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache, seed = Path(tmp) / 'cache', Path(tmp) / 'seed'
            cache.mkdir()
            (cache / 'seed-stamp.bin').write_bytes(stamp_bytes())
            for name in ('g_0x00000000000000aa.key', 'c_0x00000000000000bb.key', '0x00000000d99ad300.meta',
                         '0x00000000d99ad301.meta', '0x00000000d99ad300_0.spv', 'profile.bin',
                         'shader-index.bin', 'vs-formats.bin', 'precompile-retired.txt'):
                (cache / name).write_bytes(b'x')
            (cache / 'rebuild-differ.txt').write_text('0x00000000d99ad301 vs 0xd99ad3 1\n')
            stamp, keys, metas, dropped = seed_tool.make_seed(cache, seed)
            self.assertEqual((stamp['serial'], stamp['shaders'], keys, metas, dropped), ('CUSA00223', 2, 2, 1, 1))
            self.assertEqual(sorted(p.name for p in seed.iterdir()),
                             ['0x00000000d99ad300.meta', 'c_0x00000000000000bb.key', 'g_0x00000000000000aa.key',
                              'seed-graphics-keys.txt', 'seed-stamp.bin', 'vs-formats.bin'])
            self.assertEqual((seed / 'seed-graphics-keys.txt').read_text(), 'g_0x00000000000000aa\n')

    def test_seed_needs_a_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OSError):
                seed_tool.make_seed(Path(tmp), Path(tmp) / 'seed')
            (Path(tmp) / 'seed-stamp.bin').write_bytes(b'SSTP\3\0\0\0')
            with self.assertRaises(ValueError):
                seed_tool.make_seed(Path(tmp), Path(tmp) / 'seed')

    def test_run_uses_the_shipped_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            port, user = Path(tmp), Path(tmp) / 'user'
            self.assertIsNone(run.shader_seed_name(port, user))
            (port / 'shader-seed.txt').write_text('issn-seed-0.3.2\n')
            self.assertIsNone(run.shader_seed_name(port, user))  # not in this user folder
            (user / 'cache' / 'issn-seed-0.3.2').mkdir(parents=True)
            self.assertEqual(run.shader_seed_name(port, user), 'issn-seed-0.3.2')
            (port / 'shader-seed.txt').write_text('../escape\n')
            self.assertIsNone(run.shader_seed_name(port, user))

    def test_shipped_seed_reaches_another_user_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            port, user = Path(tmp) / 'port', Path(tmp) / 'Documents' / 'inFAMOUS-PC'
            shipped = port / 'user' / 'cache' / 'issn-seed-0.3.6'
            shipped.mkdir(parents=True)
            (port / 'shader-seed.txt').write_text('issn-seed-0.3.6\n')
            self.assertIsNone(run.shader_seed_name(port, user))  # no stamp: not a seed
            (shipped / 'seed-stamp.bin').write_bytes(b'SSTP')
            (shipped / 'a.key').write_bytes(b'k')
            self.assertEqual(run.shader_seed_name(port, user), 'issn-seed-0.3.6')
            self.assertEqual((user / 'cache' / 'issn-seed-0.3.6' / 'a.key').read_bytes(), b'k')
            (user / 'cache' / 'issn-seed-0.3.6' / 'a.key').write_bytes(b'played')
            run.shader_seed_name(port, user)  # a folder already there is never overwritten
            self.assertEqual((user / 'cache' / 'issn-seed-0.3.6' / 'a.key').read_bytes(), b'played')


if __name__ == '__main__':
    unittest.main()
