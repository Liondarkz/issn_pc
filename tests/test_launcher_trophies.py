"""The launcher's Achievements page: the trophies of the player's trophy file, which ones this PC
unlocked, the popup switch (issn.ini trophy_note) and a key that is only kept when it opens the
file (a synthetic file and a made-up key; no real one is in the port)."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import paths  # noqa: F401
import trophies
from test_launcher_controls import launcher
from test_trophies import CONF, COMM, KEY, PT, esfm, make_trp, png


class LauncherTrophiesTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Tk display unavailable: {error}')
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        self.game, self.user = folder / 'dump/Image0', folder / 'user'
        (self.game / 'sce_sys').mkdir(parents=True)
        self.user.mkdir()
        make_trp(folder / 'dump/Sc0/trophy/trophy00.trp',
                 [('TROP000.PNG', png(0), 0), ('TROP001.PNG', png(1), 0), ('TROP002.PNG', png(2), 0),
                  ('TROP003.PNG', png(3), 0), ('TROPCONF.ESFM', esfm(CONF), 3), ('TROP.ESFM', esfm(CONF), 3),
                  ('TROP_17.ESFM', esfm(PT), 3)])
        (folder / 'dump/Sc0/npbind.dat').write_bytes(bytes(64) + COMM.encode() + bytes(20))
        (self.user / trophies.UNLOCKED_FILE).write_text('2 2026-11-04 23:50:12\n', encoding='utf-8')
        config = folder / 'config'
        config.mkdir()
        config_file = config / 'settings.json'
        config_file.write_text(json.dumps({**launcher.APP_DEFAULTS, 'game_dir': str(self.game), 'user_dir': str(self.user),
                                           'game_language': '17', 'ui_language': 'en', 'check_updates': False}),
                               encoding='utf-8')
        for name, value in [('CONFIG_DIR', config), ('CONFIG_FILE', config_file), ('DATA_DIR', folder)]:
            patch = mock.patch.object(launcher, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        gpu = mock.patch.object(launcher, 'list_gpus', return_value=[])
        gpu.start()
        self.addCleanup(gpu.stop)
        self.app = launcher.Launcher(self.root, tk, ttk, filedialog, messagebox)
        self.app.messagebox = mock.Mock()

    def cards(self):
        return self.app.trophy_list.winfo_children()

    def labels(self, card):
        return [w.cget('text') for w in card.winfo_children() if w.winfo_class() == 'Label' and w.cget('text')]

    def test_page_lists_every_trophy_unlocked_first(self):
        self.app.show('trophies')
        self.assertEqual(self.app.trophy_summary.cget('text'), '1 of 4 unlocked (25%)')
        self.assertEqual(len(self.cards()), 4)
        self.assertEqual(self.labels(self.cards()[0])[:2], ['Trophy 2', 'Unlocked 2026-11-04 23:50:12'])
        self.assertEqual(self.labels(self.cards()[1])[:2], ['Trophy 0', 'Locked'])
        self.assertIn('No key', self.app.trophy_key_state.cget('text'))
        self.assertTrue(self.app.var('trophy_note', 'ini').get())  # the popup is on by default

    def test_a_key_is_kept_only_when_it_opens_the_file(self):
        self.app.show('trophies')
        self.app.trophy_key_entry.insert(0, '00' * 16)
        self.app.save_trophy_key()
        self.assertIsNone(trophies.read_key(self.user))
        self.app.messagebox.showerror.assert_called_once()
        self.app.trophy_key_entry.delete(0, 'end')
        self.app.trophy_key_entry.insert(0, KEY.hex())
        self.app.save_trophy_key()
        self.assertEqual(trophies.read_key(self.user), KEY)
        # Names in the game language (Brazilian Portuguese: TROP_17); a locked hidden trophy stays hidden.
        texts = [self.labels(card)[0] for card in self.cards()]
        self.assertEqual(texts, ['Segundo', 'Tudo', 'Primeiro passo', 'Extra um'])
        self.assertIn('Key saved', self.app.trophy_key_state.cget('text'))
        (self.user / trophies.UNLOCKED_FILE).write_text('', encoding='utf-8')
        self.app.refresh_trophies()
        self.assertIn('Hidden trophy', [self.labels(card)[0] for card in self.cards()])
        self.app.remove_trophy_key()
        self.assertIsNone(trophies.read_key(self.user))
        self.assertEqual(self.labels(self.cards()[0])[0], 'Trophy 0')


if __name__ == '__main__':
    unittest.main()
