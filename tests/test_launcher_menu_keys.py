"""Settings the game's menu (Image tab) changes for the next start: menu_* keys in issn.ini."""
import sys
import unittest

from paths import ROOT
sys.path.insert(0, str(ROOT / 'launcher'))
import issn_launcher_win as launcher  # noqa: E402


class MenuKeysTest(unittest.TestCase):
    def test_valid_values_are_taken_over(self):
        ini = {'menu_anti_aliasing': 'fsr4', 'menu_upscale_quality': '1.5', 'menu_aniso': '16',
               'menu_draw_distance': '1.5', 'menu_frame_gen': '1'}
        self.assertEqual(launcher.menu_settings(ini), {'anti_aliasing': 'fsr4', 'upscale_quality': '1.5',
                                                       'aniso': '16', 'draw_distance': '1.5',
                                                       'frame_gen': True})

    def test_empty_values_mean_the_defaults(self):
        ini = {'menu_upscale_quality': '', 'menu_aniso': '', 'menu_draw_distance': '', 'menu_frame_gen': '0'}
        self.assertEqual(launcher.menu_settings(ini), {'upscale_quality': '', 'aniso': '', 'draw_distance': '',
                                                       'frame_gen': False})

    def test_unknown_values_are_ignored(self):
        ini = {'menu_anti_aliasing': 'fsr9', 'menu_upscale_quality': '7', 'menu_frame_gen': 'yes'}
        self.assertEqual(launcher.menu_settings(ini), {})
        self.assertEqual(launcher.menu_settings({}), {})

    def test_written_back_as_the_game_reads_them(self):
        app = {'anti_aliasing': 'fsr4', 'upscale_quality': '2', 'aniso': '', 'draw_distance': '0.6',
               'frame_gen': True}
        written = {f'menu_{k}': launcher.menu_ini_value(app, k) for k in launcher.MENU_KEYS}
        self.assertEqual(launcher.menu_settings(written), app)


if __name__ == '__main__':
    unittest.main()
