from paths import ROOT  # noqa: F401  (puts scripts/ on the path)
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import game_update
from mods import is_link


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class GameUpdateTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.base, self.update, self.out = self.root / 'base', self.root / 'update', self.root / 'out'
        write(self.base / 'eboot.bin', 'base eboot')
        write(self.base / 'EXEC/WAD/A.WAD', 'wad')
        write(self.base / 'sce_sys/icon0.png', 'old icon')
        write(self.update / 'eboot.bin', 'update eboot')
        write(self.update / 'SCE_SYS/param.sfo', 'new sfo')
        write(self.update / 'SCE_SYS/icon0.png', 'new icon')

    def view(self, ids=('CUSA01623', 'CUSA01623')):
        with mock.patch('patches.game_title_id', side_effect=list(ids)):
            return game_update.create_view(self.base, self.update, self.out)

    def test_update_files_replace_base_files_and_the_rest_stays(self):
        view = self.view()
        self.assertEqual((view / 'eboot.bin').read_text(), 'update eboot')
        self.assertEqual((view / 'EXEC/WAD/A.WAD').read_text(), 'wad')
        self.assertEqual((view / 'sce_sys/icon0.png').read_text(), 'new icon')
        self.assertEqual((view / 'sce_sys/param.sfo').read_text(), 'new sfo')
        self.assertEqual([p.name for p in view.iterdir() if p.name.lower() == 'sce_sys'], ['sce_sys'])

    def test_folders_the_update_leaves_alone_are_linked_whole(self):
        view = self.view()
        self.assertTrue(is_link(view / 'EXEC'))
        self.assertFalse(is_link(view / 'sce_sys'))  # merged: base and update files together

    def test_removing_the_view_leaves_the_dumps_whole(self):
        view = self.view()
        game_update.remove_view(view)
        self.assertFalse(view.exists())
        self.assertEqual((self.base / 'EXEC/WAD/A.WAD').read_text(), 'wad')
        self.assertEqual((self.base / 'sce_sys/icon0.png').read_text(), 'old icon')
        self.assertEqual((self.update / 'SCE_SYS/param.sfo').read_text(), 'new sfo')

    def test_dumps_are_not_modified(self):
        self.view()
        self.assertEqual((self.base / 'eboot.bin').read_text(), 'base eboot')
        self.assertEqual((self.base / 'sce_sys/icon0.png').read_text(), 'old icon')

    def test_different_titles_and_nested_folders_are_refused(self):
        with self.assertRaises(ValueError):
            self.view(('CUSA01623', 'CUSA01715'))
        with mock.patch('patches.game_title_id', return_value='CUSA01623'):
            with self.assertRaises(ValueError):
                game_update.create_view(self.base, self.base / 'EXEC', self.out)
        self.assertEqual(list(self.out.iterdir()) if self.out.exists() else [], [])


    def test_system_files_beside_image0_join_the_view(self):
        # PKG extractors: <dump>/Image0 is the game and <dump>/Sc0 holds param.sfo and friends.
        game, patch = self.root / 'game' / 'Image0', self.root / 'patch' / 'Image0'
        write(game / 'eboot.bin', 'base eboot')
        write(game / 'sce_sys/keystone', 'key')
        write(game.parent / 'Sc0/param.sfo', 'base sfo')
        write(game.parent / 'Sc0/pic1.png', 'art')
        write(patch / 'eboot.bin', 'update eboot')
        write(patch.parent / 'Sc0/param.sfo', 'update sfo')
        self.assertTrue(game_update.needs_view(game))
        self.assertFalse(game_update.needs_view(self.base / '..' / 'update'))
        alone = game_update.create_view(game, None, self.out)
        self.assertEqual((alone / 'sce_sys/param.sfo').read_text(), 'base sfo')
        self.assertEqual((alone / 'sce_sys/keystone').read_text(), 'key')
        game_update.remove_view(alone)
        with mock.patch('patches.game_title_id', side_effect=['CUSA00575', 'CUSA00575']):
            view = game_update.create_view(game, patch, self.out)
        self.assertEqual((view / 'eboot.bin').read_text(), 'update eboot')
        self.assertEqual((view / 'sce_sys/param.sfo').read_text(), 'update sfo')
        self.assertEqual((view / 'sce_sys/pic1.png').read_text(), 'art')
        game_update.remove_view(view)
        self.assertEqual((game.parent / 'Sc0/param.sfo').read_text(), 'base sfo')
        self.assertEqual((game / 'sce_sys/keystone').read_text(), 'key')


if __name__ == '__main__':
    unittest.main()
