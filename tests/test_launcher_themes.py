"""The launcher's themes (issn_themes): every theme has every colour the window and issn_theme use,
Smoke is the look until now, and use_theme puts a theme's colours in the module's constants."""
import sys
import unittest

from paths import ROOT
sys.path.insert(0, str(ROOT / 'launcher'))
import issn_themes  # noqa: E402

KEYS = {'bg', 'panel', 'card', 'line', 'field', 'card_hover', 'hover', 'border', 'border_strong', 'border_hover',
        'text', 'muted', 'accent', 'accent_hi', 'accent_dark', 'neon', 'pink', 'ember', 'nav_active', 'nav_hover'}


class ThemesTest(unittest.TestCase):
    def test_every_theme_has_every_colour(self):
        self.assertEqual(set(issn_themes.ORDER), set(issn_themes.THEMES))
        self.assertEqual(set(issn_themes.NAMES), set(issn_themes.THEMES))
        for name, colors in issn_themes.THEMES.items():
            self.assertEqual(set(colors), KEYS, name)
            for key, value in colors.items():
                self.assertRegex(value, r'^#[0-9a-f]{6}$', (name, key))

    def test_smoke_is_the_look_until_now(self):
        smoke = issn_themes.THEMES['smoke']
        self.assertEqual(issn_themes.DEFAULT, 'smoke')
        self.assertEqual((smoke['bg'], smoke['panel'], smoke['card'], smoke['accent'], smoke['neon']),
                         ('#09090c', '#101116', '#181a21', '#e2263b', '#24d3f2'))

    def test_an_unknown_name_gives_the_default(self):
        self.assertEqual(issn_themes.known('rainbow'), 'smoke')
        self.assertIs(issn_themes.colors(None), issn_themes.THEMES['smoke'])


class UseThemeTest(unittest.TestCase):
    def test_use_theme_sets_the_constants(self):
        try:
            import issn_launcher_win as launcher
        except ImportError as error:  # tkinter / Pillow missing here
            self.skipTest(str(error))
        try:
            launcher.use_theme('video')
            self.assertEqual(launcher.THEME_NAME, 'video')
            self.assertEqual(launcher.ACCENT, issn_themes.THEMES['video']['accent'])
            self.assertEqual(launcher.THEME_COLORS['panel'], issn_themes.THEMES['video']['panel'])
            launcher.use_theme('nothing')
            self.assertEqual(launcher.THEME_NAME, 'smoke')
        finally:
            launcher.use_theme('smoke')


if __name__ == '__main__':
    unittest.main()
