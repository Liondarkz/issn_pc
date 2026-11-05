"""The new launcher's Python side (launcher/issn_webui.py): what the page gets and what it writes."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from paths import ROOT
sys.path.insert(0, str(ROOT / 'launcher'))
try:
    import issn_launcher_win as launcher
    import issn_webui
except ImportError as error:  # tkinter / Pillow missing here
    launcher = None
    REASON = str(error)


@unittest.skipIf(launcher is None, 'the launcher module needs tkinter and Pillow')
class WebUiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patches = [mock.patch.object(launcher, 'CONFIG_DIR', self.tmp / 'config'),
                   mock.patch.object(launcher, 'CONFIG_FILE', self.tmp / 'config' / 'settings.json'),
                   mock.patch.object(launcher, 'DATA_DIR', self.tmp / 'data'),
                   mock.patch.object(launcher, 'LANG', 'en'),
                   mock.patch.object(launcher, 'list_gpus', return_value=[('0', 'AMD Radeon RX 9060 XT', 'discrete')]),
                   mock.patch.dict(os.environ, {'ISSN_CONFIG': str(self.tmp / 'issn.ini')})]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        (self.tmp / 'data').mkdir()
        self.api = issn_webui.Api(launcher)
        self.addCleanup(launcher.use_theme, 'smoke')

    def test_state_lists_the_pages_themes_and_play_page(self):
        state = self.api.state()
        self.assertEqual([p['id'] for p in state['pages']], list(launcher.PAGES))
        self.assertEqual({p['id'] for p in state['pages'] if p['expert']}, set(launcher.EXPERT_PAGES))
        self.assertEqual([t['id'] for t in state['themes']], list(launcher.issn_themes.ORDER))
        self.assertEqual(state['theme'], 'smoke')
        self.assertEqual({g['id'] for g in state['home']['goals']}, {'recommended', 'handheld', 'smooth', 'balanced'})
        names = [c['name'] for c in state['home']['checks']]
        self.assertIn('Game', names)
        self.assertEqual(next(c for c in state['home']['checks'] if c['name'] == 'Game')['state'], 'bad')

    def test_every_page_has_a_title_and_known_row_types(self):
        for name in launcher.PAGES:
            if name == 'home':
                continue
            schema = self.api.page(name)
            self.assertTrue(schema['title'], name)
            for section in schema['sections']:
                for row in section['rows']:
                    self.assertIn(row['type'], ('choice', 'check', 'folder', 'segments', 'themes'), name)

    def test_settings_are_written_at_once(self):
        self.api.set_value('launcher_theme', 'app', 'neon')
        self.api.set_value('anti_aliasing', 'app', 'off')
        self.api.set_value('vsync', 'ini', False)
        self.api.set_value('fps_limit', 'ini', '45')
        settings = json.loads(launcher.CONFIG_FILE.read_text(encoding='utf-8'))
        self.assertEqual((settings['launcher_theme'], settings['anti_aliasing']), ('neon', 'off'))
        ini = (self.tmp / 'issn.ini').read_text(encoding='utf-8').splitlines()
        self.assertIn('vsync=0', ini)
        self.assertIn('fps_limit=45', ini)
        self.assertIn('menu_anti_aliasing=off', ini)  # the game's menu shows it next time
        self.assertEqual(self.api.state()['theme'], 'neon')

    def test_a_goal_writes_its_settings(self):
        self.api._detect()
        result = self.api.apply_goal('handheld')
        self.assertIn('message', result)
        settings = json.loads(launcher.CONFIG_FILE.read_text(encoding='utf-8'))
        self.assertEqual((settings['render_height'], settings['perf_mode'], settings['launcher_goal']),
                         ('720', 'office', 'handheld'))

    def test_play_without_a_game_folder_points_to_it(self):
        self.assertEqual(self.api.play()['page'], 'game')


if __name__ == '__main__':
    unittest.main()
