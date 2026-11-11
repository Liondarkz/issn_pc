"""Clear shader cache keeps the package's seed when the user folder is the port's own (11-07: an
RTX 5090 player lost issn-seed-0.3.7 that way and compiled every shader while fighting)."""
from pathlib import Path
import tempfile
import unittest

import paths  # noqa: F401
from test_launcher_controls import launcher


class ClearCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.port = Path(temporary.name) / 'port'
        self.cache = self.port / 'user' / 'cache'
        (self.cache / 'issn-seed-0.3.7').mkdir(parents=True)
        (self.cache / 'issn-seed-0.3.7' / 'seed-stamp.bin').write_bytes(b'stamp')
        (self.cache / 'CUSA00223').mkdir()
        (self.cache / 'CUSA00223' / 'x.bin').write_bytes(b'x')
        (self.cache / 'loose.bin').write_bytes(b'y')
        (self.port / 'shader-seed.txt').write_text('issn-seed-0.3.7\n')

    def test_the_shipped_seed_stays(self):
        self.assertEqual(launcher.clear_shader_cache(self.port / 'user', self.port), 'issn-seed-0.3.7')
        self.assertEqual(sorted(p.name for p in self.cache.iterdir()), ['issn-seed-0.3.7'])
        self.assertTrue((self.cache / 'issn-seed-0.3.7' / 'seed-stamp.bin').is_file())

    def test_a_user_folder_elsewhere_is_emptied(self):
        other = self.port.parent / 'Documents' / 'user'
        (other / 'cache' / 'issn-seed-0.3.7').mkdir(parents=True)
        (other / 'cache' / 'issn-seed-0.3.7' / 'seed-stamp.bin').write_bytes(b'copy')
        self.assertEqual(launcher.clear_shader_cache(other, self.port), '')  # run.py copies it again
        self.assertEqual(list((other / 'cache').iterdir()), [])
        self.assertTrue((self.cache / 'issn-seed-0.3.7').is_dir())

    def test_no_seed_listed_or_no_cache(self):
        (self.port / 'shader-seed.txt').write_text('')
        self.assertEqual(launcher.clear_shader_cache(self.port / 'user', self.port), '')
        self.assertEqual(list(self.cache.iterdir()), [])
        self.assertEqual(launcher.clear_shader_cache(self.port / 'nothing', self.port), '')


if __name__ == '__main__':
    unittest.main()
