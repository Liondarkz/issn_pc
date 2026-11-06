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


# What launcher/web/app.js draws (renderRow and renderSpecial).
ROW_TYPES = ('choice', 'check', 'folder', 'segments', 'themes', 'number', 'note', 'buttons', 'card', 'cards', 'progress',
             'pick', 'entry', 'trophies', 'controls', 'mods', 'log')


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
                    self.assertIn(row['type'], ROW_TYPES, name)

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

    def test_sharpening_row(self):
        # issn 11-05: off by default, steps of 5 %, a value set by the game's slider is listed too.
        def row():
            rows = [r for sec in self.api.page('picture')['sections'] for r in sec['rows']]
            return next(r for r in rows if r.get('key') == 'sharpening')
        self.assertEqual((row()['value'], row()['store']), ('0', 'ini'))
        self.api.set_value('sharpening', 'ini', '37')
        self.assertIn('sharpening=37', (self.tmp / 'issn.ini').read_text(encoding='utf-8').splitlines())
        self.assertEqual(row()['value'], '37')
        self.assertIn('37', [v for v, _text in row()['options']])

    def test_a_goal_writes_its_settings(self):
        self.api._detect()
        result = self.api.apply_goal('handheld')
        self.assertIn('message', result)
        settings = json.loads(launcher.CONFIG_FILE.read_text(encoding='utf-8'))
        self.assertEqual((settings['render_height'], settings['perf_mode'], settings['launcher_goal']),
                         ('720', 'office', 'handheld'))

    def test_play_without_a_game_folder_points_to_it(self):
        self.assertEqual(self.api.play()['page'], 'game')

    def test_every_page_is_here_now(self):
        # 11-05: no page says "still on the classic launcher" any more.
        for name in launcher.PAGES:
            if name != 'home':
                self.assertNotIn('placeholder', self.api.page(name), name)
        buttons = [b for section in self.api.page('advanced')['sections'] for row in section['rows']
                   for b in row.get('buttons', []) if b]
        for b in buttons:  # every button's action exists on the Python side
            self.assertTrue(not b['action'] or hasattr(self.api, '_do_' + b['action']), b)

    def test_controls_write_only_what_differs_and_the_key_map(self):
        self.api.set_control('key', 'cross', 'K')
        self.api.set_control('pad', 'circle', 'x')
        self.api.set_control('key', 'square', 'C')  # the default: not written
        settings = json.loads(launcher.CONFIG_FILE.read_text(encoding='utf-8'))
        self.assertEqual(settings['controls'], {'key': {'cross': 'K'}, 'pad': {'circle': 'x'}})
        self.assertIn('key_map=cross=K', (self.tmp / 'issn.ini').read_text(encoding='utf-8'))
        table = next(row for section in self.api.page('controls')['sections'] for row in section['rows']
                     if row['type'] == 'controls')['rows']
        self.assertEqual(next(r['key'] for r in table if r['button'] == 'cross'), 'K')
        self.api.action('reset_controls')
        settings = json.loads(launcher.CONFIG_FILE.read_text(encoding='utf-8'))
        self.assertEqual(settings['controls'], {'key': {}, 'pad': {}})

    def test_sensitivity_is_a_number_with_two_decimals(self):
        self.api.set_value('mouse_sensitivity', 'ini', '1.5')
        self.assertIn('mouse_sensitivity=1.50', (self.tmp / 'issn.ini').read_text(encoding='utf-8'))
        self.assertIn('toast', self.api.set_value('mouse_sensitivity', 'ini', 'fast'))

    def test_mods_order_and_switches(self):
        for name in ('ModA', 'ModB'):
            (self.tmp / 'data' / 'mods' / name / 'dvdroot_ps4').mkdir(parents=True)
        self.api.action('mod_move', ['ModB', -1])
        self.api.action('mod_toggle', ['ModA', False])
        self.assertEqual(json.loads((self.tmp / 'data' / 'mods.json').read_text()),
                         {'order': ['ModB', 'ModA'], 'disabled': ['ModA']})
        row = next(row for section in self.api.page('files')['sections'] for row in section['rows']
                   if row['type'] == 'mods')
        self.assertEqual([(m['name'], m['on']) for m in row['items']], [('ModB', True), ('ModA', False)])

    def test_trophies_list_unlocked_first_with_names_only_when_unlocked(self):
        T = launcher.trophies
        with mock.patch.object(T, 'summary', return_value=(self.tmp / 't.trp', [0, 1, 2], {2: '2026-11-05'})), \
                mock.patch.object(T, 'read_entries', return_value=[]), mock.patch.object(T, 'icon', return_value=None), \
                mock.patch.object(T, 'game_texts', return_value=({0: {'name': 'Plat', 'grade': 'P'},
                                                                  2: {'name': 'Two', 'grade': 'B'}}, 'list')):
            rows = [row for section in self.api.page('trophies')['sections'] for row in section['rows']]
        items = next(row for row in rows if row['type'] == 'trophies')['items']
        self.assertEqual([i['name'] for i in items], ['Two', 'Trophy 0', 'Trophy 1'])
        self.assertEqual(next(row for row in rows if row['type'] == 'progress')['value'], 33)

    def test_an_incomplete_game_folder_says_what_is_missing(self):
        game = self.tmp / 'game'
        game.mkdir()
        (game / 'eboot.bin').write_bytes(b'x')
        self.api.set_value('game_dir', 'app', str(game))
        with mock.patch.object(launcher, 'game_info', return_value=('inFAMOUS Second Son', '01.07')), \
                mock.patch.object(launcher, 'system_files_beside', return_value=None):
            result = self.api.play()
        self.assertEqual(result['page'], 'game')
        self.assertIn('alert', result)

    def test_long_work_reports_and_rebuilds(self):
        done = []
        seq = self.api.status()['seq']
        self.api._job('test', lambda: done.append(1), 'failed: {}')
        for _i in range(50):
            if not self.api.status()['busy']:
                break
            __import__('time').sleep(0.02)
        self.assertEqual(done, [1])
        self.assertGreater(self.api.status()['seq'], seq)


if __name__ == '__main__':
    unittest.main()


def test_stale_webview_cache_is_cleared_when_the_page_changes(tmp_path):
    """issn 11-06: WebView2 kept the old app.js after an update (empty rows, blank pages)."""
    web = tmp_path / 'web'
    web.mkdir()
    for name in ('index.html', 'app.js', 'app.css'):
        (web / name).write_text(name, encoding='utf-8')
    storage = tmp_path / 'webview'
    cache = storage / 'EBWebView' / 'Default' / 'Cache'
    kept = storage / 'EBWebView' / 'Default' / 'Local Storage'
    cache.mkdir(parents=True)
    kept.mkdir(parents=True)
    stamp = issn_webui.web_stamp(web)
    assert issn_webui.clear_stale_cache(storage, stamp)
    assert not cache.exists() and kept.exists()
    cache.mkdir()
    assert not issn_webui.clear_stale_cache(storage, stamp)  # same files: the cache stays
    assert cache.exists()
    (web / 'app.js').write_text('a newer app.js', encoding='utf-8')
    assert issn_webui.clear_stale_cache(storage, issn_webui.web_stamp(web))
    assert not cache.exists()


def test_page_files_are_loaded_with_the_stamp():
    html = (issn_webui.WEB_DIR / 'index.html').read_text(encoding='utf-8')
    assert 'app.js?v=' in html and 'app.css?v=' in html
    assert '<script src="app.js"></script>' not in html
