import datetime
import tempfile
import unittest
from pathlib import Path

import paths  # noqa: F401  (puts scripts/ on sys.path)
import save_backup


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


class SaveBackupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.user = Path(self.temp.name)
        self.save = self.user / 'savedata' / '1' / 'CUSA01623' / 'AUTOSAVE' / 'data.bin'
        self.clock = datetime.datetime(2026, 10, 9, 18, 0, 0)

    def tearDown(self):
        self.temp.cleanup()

    def tick(self):
        self.clock += datetime.timedelta(minutes=1)
        return self.clock

    def test_copies_savedata(self):
        write(self.save, 'v1')
        made = save_backup.backup(self.user, now=self.tick())
        self.assertEqual(made.name, '20261009-180100')
        self.assertEqual((made / self.save.relative_to(self.user / 'savedata')).read_text(), 'v1')

    def test_no_backup_without_saves(self):
        self.assertIsNone(save_backup.backup(self.user, now=self.tick()))
        (self.user / 'savedata').mkdir()
        self.assertIsNone(save_backup.backup(self.user, now=self.tick()))
        self.assertEqual(save_backup.list_backups(self.user), [])

    def test_same_second_gets_unique_name(self):
        write(self.save, 'v1')
        first = save_backup.backup(self.user, now=self.clock)
        write(self.save, 'v2')
        second = save_backup.backup(self.user, now=self.clock)
        self.assertNotEqual(first, second)
        self.assertEqual(len(save_backup.list_backups(self.user)), 2)

    def test_unchanged_saves_are_not_backed_up_again(self):
        write(self.save, 'v1')
        first = save_backup.backup(self.user, now=self.tick())
        self.assertIsNone(save_backup.backup(self.user, now=self.tick()))
        write(self.save, 'v2')
        self.assertIsNotNone(save_backup.backup(self.user, now=self.tick()))
        self.assertEqual(save_backup.list_backups(self.user)[1], first.name)

    def test_keeps_newest_ten(self):
        made = []
        for i in range(12):
            write(self.save, f'v{i}')
            made.append(save_backup.backup(self.user, now=self.tick()))
        kept = save_backup.list_backups(self.user)
        self.assertEqual(len(kept), save_backup.KEEP)
        self.assertEqual(kept[0], made[-1].name)  # newest first
        self.assertFalse(made[0].exists())
        self.assertFalse(made[1].exists())

    def test_keeps_one_a_day_and_the_ones_before_an_import(self):
        # issn_pc 10-31: yesterday's progress and the copy made before an import outlive a day of
        # test starts.
        write(self.save, 'yesterday')
        yesterday = save_backup.backup(self.user, now=self.tick())
        self.clock += datetime.timedelta(days=1)
        write(self.save, 'mine')
        importing = self.user / 'other' / 'savedata' / '1' / 'CUSA00223' / 'infamousssAuto0' / 'dis.dat'
        write(importing, 'tester')
        safety, _ = save_backup.import_saves(self.user, self.user / 'other', now=self.tick())
        self.assertTrue(safety.name.endswith('-before-import'))
        for i in range(20):
            write(self.save, f'test start {i}')
            save_backup.backup(self.user, now=self.tick())
        names = save_backup.list_backups(self.user)
        self.assertIn(yesterday.name, names)
        self.assertIn(safety.name, names)
        self.assertEqual(len(names), save_backup.KEEP + 2)

    def test_kept_rules(self):
        names = [f'202610{d:02d}-1200{m:02d}' for d in range(31, 0, -1) for m in (2, 1)]
        stay = save_backup.kept(names, keep=3, days=4, protected=1)
        self.assertEqual(sorted(stay, reverse=True), [
            '20261031-120002', '20261031-120001', '20261030-120002', '20261029-120002', '20261028-120002'])
        tagged = ['20261031-120000-before-import', '20261001-120000-before-restore', '20261001-110000-before-import']
        stay = save_backup.kept(tagged, keep=0, days=0, protected=2)
        self.assertEqual(stay, set(tagged[:2]))

    def test_restore_backs_up_current_first(self):
        write(self.save, 'old')
        old = save_backup.backup(self.user, now=self.tick())
        write(self.save, 'new')
        write(self.save.parent / 'extra.bin', 'x')
        safety = save_backup.restore(self.user, old.name, now=self.tick())
        self.assertEqual(self.save.read_text(), 'old')
        self.assertFalse((self.save.parent / 'extra.bin').exists())
        self.assertEqual((safety / self.save.relative_to(self.user / 'savedata')).read_text(), 'new')

    def test_restore_oldest_when_full(self):
        write(self.save, 'oldest')
        oldest = save_backup.backup(self.user, now=self.tick())
        for i in range(save_backup.KEEP - 1):
            write(self.save, f'later {i}')
            save_backup.backup(self.user, now=self.tick())
        save_backup.restore(self.user, oldest.name, now=self.tick())
        self.assertEqual(self.save.read_text(), 'oldest')
        self.assertEqual(len(save_backup.list_backups(self.user)), save_backup.KEEP)

    def test_restore_rejects_unknown_or_unsafe_names(self):
        write(self.save, 'v')
        for name in ('missing', '..', '../savedata', ''):
            with self.assertRaises(ValueError):
                save_backup.restore(self.user, name, now=self.tick())
        self.assertEqual(self.save.read_text(), 'v')


class SaveImportTest(unittest.TestCase):
    """Saves from another port of the game (HayatoG's iss_pc): same layout, copied as they are."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.ours = root / 'issn-windows' / 'user'
        self.other = root / 'iss_pc'
        title = self.other / 'user' / 'savedata' / '1' / 'CUSA00223'
        write(title / 'infamousssAuto0' / 'data.sav', 'theirs')
        write(title / 'infamousssAuto0.sce_sys' / 'param.bin', 'param')
        write(title / 'infamousssAuto1' / 'data.sav', 'theirs 2')
        write(title.with_name('CUSA00223.memory') / 'memory.dat', 'memory')
        self.clock = datetime.datetime(2026, 10, 30, 13, 0, 0)

    def tearDown(self):
        self.temp.cleanup()

    def test_any_level_of_the_other_port_folder_is_found(self):
        savedata = self.other / 'user' / 'savedata'
        for folder in (self.other, self.other / 'user', savedata, savedata / '1', savedata / '1' / 'CUSA00223'):
            found = save_backup.import_source(folder)
            self.assertEqual(sorted(p.name for p in found['1']), ['CUSA00223', 'CUSA00223.memory'], folder)
        self.assertEqual(save_backup.import_source(Path(self.temp.name) / 'nothing'), {})

    def test_import_replaces_and_backs_up_the_current_saves(self):
        mine = self.ours / 'savedata' / '1' / 'CUSA00223' / 'infamousssAuto0' / 'data.sav'
        write(mine, 'mine')
        safety, slots = save_backup.import_saves(self.ours, self.other, now=self.clock)
        self.assertEqual(slots, 2)
        self.assertEqual(mine.read_text(), 'theirs')
        self.assertEqual((self.ours / 'savedata' / '1' / 'CUSA00223.memory' / 'memory.dat').read_text(), 'memory')
        self.assertEqual((self.ours / 'savedata' / '1' / 'CUSA00223' / 'infamousssAuto0.sce_sys' / 'param.bin').read_text(), 'param')
        self.assertEqual((safety / '1' / 'CUSA00223' / 'infamousssAuto0' / 'data.sav').read_text(), 'mine')
        self.assertFalse(list((self.ours / 'savedata' / '1').glob('*.importing')))

    def test_import_into_an_empty_port_needs_no_backup(self):
        safety, slots = save_backup.import_saves(self.ours, self.other / 'user', now=self.clock)
        self.assertIsNone(safety)
        self.assertEqual(slots, 2)

    def test_refuses_nothing_or_its_own_saves(self):
        with self.assertRaises(ValueError):
            save_backup.import_saves(self.ours, Path(self.temp.name) / 'nothing')
        write(self.ours / 'savedata' / '1' / 'CUSA00223' / 'infamousssUser0' / 'd', 'mine')
        with self.assertRaises(ValueError):
            save_backup.import_saves(self.ours, self.ours)

    def test_another_region_goes_into_the_games_title_folder(self):
        europe = Path(self.temp.name) / 'eu' / 'savedata' / '1' / 'CUSA00004'
        write(europe / 'infamousssUser0' / 'dis.dat', 'european')
        write(europe / 'infamousssUser0.sce_sys' / 'param.bin', 'param')
        self.assertEqual([p.name for p in save_backup.import_source(europe.parent.parent.parent)['1']], ['CUSA00004'])
        safety, slots = save_backup.import_saves(self.ours, europe.parent.parent.parent, now=self.clock,
                                                 title='CUSA00223')
        self.assertEqual(slots, 1)
        self.assertEqual((self.ours / 'savedata' / '1' / 'CUSA00223' / 'infamousssUser0' / 'dis.dat').read_text(),
                         'european')
        self.assertFalse((self.ours / 'savedata' / '1' / 'CUSA00004').exists())

    def test_a_folder_of_another_game_is_not_a_save(self):
        other = Path(self.temp.name) / 'other' / 'savedata' / '1' / 'CUSA01623'
        write(other / 'AUTOSAVE' / 'data.bin', 'kratos')
        self.assertEqual(save_backup.import_source(other.parent.parent.parent), {})

    def test_finds_the_other_launcher_data_folder(self):
        appdata = Path(self.temp.name) / 'appdata'
        write(appdata / 'inFAMOUS-PC' / 'launcher.json', '{"user_dir": "%s"}' % (self.other / 'user').as_posix())
        self.assertEqual(save_backup.other_port_dirs({'APPDATA': str(appdata)}, nt=True), [self.other / 'user'])
        self.assertEqual(save_backup.other_port_dirs({'APPDATA': str(appdata / 'x')}, nt=True), [])


if __name__ == '__main__':
    unittest.main()
