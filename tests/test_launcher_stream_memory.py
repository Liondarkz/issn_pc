"""Saved launcher selection, legacy settings and both launch paths for stream memory."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from paths import ROOT
sys.path.insert(0, str(ROOT / 'launcher'))
import issn_launcher_win as launcher  # noqa: E402


class StreamMemoryEnvironmentTest(unittest.TestCase):
    def test_legacy_settings_and_invalid_values_use_cached_memory(self):
        legacy = dict(launcher.APP_DEFAULTS)
        legacy.pop('stream_memory')
        with mock.patch.dict(os.environ, {'ISSN_STREAM_MEMORY': 'device'}):
            self.assertEqual(launcher.game_environment(legacy)['ISSN_STREAM_MEMORY'], 'cached')
            for value in ('', 'invalid', None, [], 1):
                with self.subTest(value=value):
                    env = launcher.game_environment({**legacy, 'stream_memory': value})
                    self.assertEqual(env['ISSN_STREAM_MEMORY'], 'cached')

    def test_every_selection_overrides_inherited_environment_without_changing_it(self):
        with mock.patch.dict(os.environ, {'ISSN_STREAM_MEMORY': 'host'}):
            for value in ('cached', 'host', 'device'):
                with self.subTest(value=value):
                    env = launcher.game_environment({**launcher.APP_DEFAULTS, 'stream_memory': value})
                    self.assertEqual(env['ISSN_STREAM_MEMORY'], value)
                    self.assertEqual(os.environ['ISSN_STREAM_MEMORY'], 'host')

    def test_play_without_window_uses_saved_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            settings_path = Path(folder) / 'settings.json'
            for value in ('cached', 'host', 'device'):
                with self.subTest(value=value):
                    settings_path.write_text(json.dumps({'stream_memory': value}), encoding='utf-8')
                    settings = {**launcher.APP_DEFAULTS, **launcher.load_json(settings_path, {}),
                                'user_dir': folder}
                    process = mock.Mock()
                    process.stdout.readline.return_value = b''
                    process.wait.return_value = 0
                    with mock.patch.object(launcher, 'attach_stdio'), \
                            mock.patch.object(launcher.subprocess, 'Popen', return_value=process) as start:
                        self.assertEqual(launcher.play_without_window(settings), 0)
                    self.assertEqual(start.call_args.kwargs['env']['ISSN_STREAM_MEMORY'], value)


if __name__ == '__main__':
    unittest.main()
