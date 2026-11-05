"""The launcher's settings pages: which exist, in sidebar order, and what each refreshes when shown."""
import sys
import unittest

from paths import ROOT
sys.path.insert(0, str(ROOT / 'launcher'))
import issn_launcher_win as launcher  # noqa: E402


class PagesTest(unittest.TestCase):
    def test_sidebar_lists_the_players_pages_then_the_experts(self):
        # issn 11-05: the new layout (as bbport's launcher): eight pages for everyone, four under
        # "Advanced view".
        self.assertEqual(launcher.PAGES, ('home', 'picture', 'controls', 'game', 'trophies', 'news', 'customize',
                                          'help', 'graphics', 'files', 'pc', 'advanced'))
        self.assertEqual(launcher.EXPERT_PAGES, ('graphics', 'files', 'pc', 'advanced'))
        self.assertEqual(launcher.PAGES[-len(launcher.EXPERT_PAGES):], launcher.EXPERT_PAGES)
        self.assertFalse(launcher.APP_DEFAULTS['expert_view'])

    def test_every_page_has_an_icon(self):
        self.assertEqual(set(launcher.NAV_ICONS), set(launcher.PAGES))

    def test_showing_a_page_refreshes_what_it_holds(self):
        self.assertEqual(launcher.PAGE_REFRESH['home'], ('refresh_status',))
        self.assertNotIn('picture', launcher.PAGE_REFRESH)  # nothing on it is a list to rebuild
        self.assertEqual(launcher.PAGE_REFRESH['files'], ('refresh_lists', 'refresh_game_patches'))
        self.assertEqual(launcher.PAGE_REFRESH['trophies'], ('refresh_trophies',))  # the game may have unlocked more

    def test_every_refreshed_page_is_a_sidebar_page(self):
        self.assertTrue(set(launcher.PAGE_REFRESH) <= set(launcher.PAGES))


if __name__ == '__main__':
    unittest.main()
