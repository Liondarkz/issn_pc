"""launcher/issn_help.py: the log hints and the installation checks."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from paths import ROOT
sys.path.insert(0, str(ROOT / 'launcher'))
import issn_help as help_  # noqa: E402


class HelpTest(unittest.TestCase):
    def test_log_hints(self):
        log = '\n'.join([
            'GPU [Render] <Warning> vk_pipeline_serialization.cpp:908 ReadSeedStamp: CacheRebuild: no valid '
            'seed-stamp.bin in the cache folder: no stage is rebuilt',
            'Fault: read access to 0000000000000010 at issn-probe.exe+0x1cfaaf (thread 1234)',
            'GPU [Render.Vulkan] <Warning> vk_presenter.cpp:1141 Present: Skipping frame! (601 in a row; window '
            '1920x1080, swapchain none, last acquire SuboptimalKHR, HDR true)',
        ])
        self.assertEqual(help_.hints_in_log(log), ['crash', 'skipping_frames', 'no_seed'])
        self.assertTrue(help_.crash_line(log).startswith('Fault: read access'))
        minimized = log + '\nSwapchain: the window\'s surface is 0x0 (minimized or off screen): frames are skipped'
        self.assertNotIn('skipping_frames', help_.hints_in_log(minimized))
        self.assertEqual(help_.hints_in_log('Present: Skipping frame! (1 in a row; window'), [])
        self.assertEqual(help_.hints_in_log("inFAMOUS version 01.00: this port needs the game's 01.07 update."),
                         ['version'])
        self.assertEqual(help_.hints_in_log('all fine\n'), [])

    def test_game_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            self.assertEqual(help_.game_problems(''), ['no_game'])
            self.assertEqual(help_.game_problems(tmp), ['no_game'])
            (game / 'eboot.bin').write_bytes(b'not the update')
            self.assertEqual(help_.game_problems(tmp), ['game_incomplete', 'version'])
            (game / 'sce_sys').mkdir()
            (game / 'sce_sys' / 'param.sfo').write_bytes(b'sfo')
            (game / 'art').mkdir()
            self.assertEqual(help_.game_problems(tmp), ['version'])
            old = help_.EBOOT_107_SHA256
            try:
                help_.EBOOT_107_SHA256 = hashlib.sha256(b'not the update').hexdigest()
                self.assertEqual(help_.game_problems(tmp), [])
            finally:
                help_.EBOOT_107_SHA256 = old

    def test_location(self):
        self.assertTrue(help_.location_problem(r'C:\Users\x\AppData\Local\Temp\Rar$EXa1234\issn-windows'))
        self.assertTrue(help_.location_problem(r'C:\Program Files\issn-windows'))
        self.assertTrue(help_.location_problem(r'C:\Users\x\OneDrive\Jogos\issn-windows'))
        self.assertFalse(help_.location_problem(r'C:\Users\Joao\Downloads\issn-windows'))

    def test_every_topic_has_its_texts(self):
        self.assertEqual(set(help_.TOPIC_ORDER), set(help_.TOPICS))
        for title, meaning, steps in help_.TOPICS.values():
            self.assertTrue(title and meaning and steps)


if __name__ == '__main__':
    unittest.main()
