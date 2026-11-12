"""scripts/game_profiles.py: settings chosen by the title ID of the game."""
import tempfile
import unittest
from pathlib import Path

from paths import ROOT  # noqa: F401  (sys.path for scripts/)
import game_profiles
from test_issn_inspect import sfo_fixture


def game(root, title):
    folder = Path(root) / title
    (folder / 'sce_sys').mkdir(parents=True)
    (folder / 'sce_sys' / 'param.sfo').write_bytes(sfo_fixture({'TITLE_ID': title, 'APP_VER': '01.00'}))
    return folder


class GameProfileTests(unittest.TestCase):
    def test_second_son_gets_axylpup_core_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            for title in ('CUSA00004', 'CUSA00223'):
                found, env = game_profiles.environment(game(tmp, title), {})
                self.assertEqual(found, title)
                self.assertEqual(env['ISSN_READBACKS'], '2')
                # axylpup's release switches and per-game config (gpu/VENDOR.txt).
                self.assertEqual(env['SHADPS4_CP_SPLIT'], '2')
                self.assertEqual(env['ISSN_READBACK_LINEAR'], '0')
                self.assertNotIn('SHADPS4_DLSS', env)
                # The fence addresses are the EU 01.00 eboot's.
                self.assertEqual('ISSN_WATCHDOG_PEEK' in env, title == 'CUSA00004')

    def test_first_light_starts_with_second_son_settings_and_its_own_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            for title in game_profiles.INFAMOUS_FIRST_LIGHT:
                found, env = game_profiles.environment(game(tmp, title), {})
                self.assertEqual((found, env['SHADPS4_CP_SPLIT'], env['ISSN_TSC']), (title, '2', 'host'))
                self.assertEqual(env.get('ISSN_LANGUAGE'), '17' if title == 'CUSA00575' else None)
                # The native colour path blackens First Light's shadowed sides (axylpup PR #68).
                self.assertEqual((env['SHADPS4_NATIVE_COLOUR'], env['SHADPS4_NATIVE_COLOUR_UNTEXTURED']), ('0', '0'))
                self.assertEqual(game_profiles.cache_name(title, 'issn-seed-0.3.7'), f'{title}-first-light')
            self.assertEqual(game_profiles.cache_name('CUSA00223', 'issn-seed-0.3.7'), 'issn-seed-0.3.7')
            self.assertIsNone(game_profiles.cache_name('CUSA00223', None))
            # A dump with its param.sfo in the Sc0 folder beside Image0.
            folder = Path(tmp) / 'dump' / 'Image0'
            folder.mkdir(parents=True)
            (folder.parent / 'Sc0').mkdir()
            (folder.parent / 'Sc0' / 'param.sfo').write_bytes(sfo_fixture({'TITLE_ID': 'CUSA00575', 'APP_VER': '01.04'}))
            self.assertEqual(game_profiles.title_id(folder), 'CUSA00575')

    def test_other_games_get_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(game_profiles.environment(game(tmp, 'CUSA01623'), {}), ('CUSA01623', {}))

    def test_explicit_values_and_opt_out_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = game(tmp, 'CUSA00004')
            self.assertNotIn('ISSN_READBACKS',
                             game_profiles.environment(folder, {'ISSN_READBACKS': '0'})[1])
            self.assertEqual(game_profiles.environment(folder, {'ISSN_GAME_PROFILE': '0'})[1], {})

    def test_missing_sfo(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(game_profiles.environment(Path(tmp), {}), (None, {}))


if __name__ == '__main__':
    unittest.main()
