# SPDX-License-Identifier: GPL-2.0-or-later
"""The new launcher window (issn 11-05, João: "the look of the preview, even if the whole launcher
has to change"): the page in launcher/web (HTML, CSS and JavaScript) shown by pywebview in
Windows' own WebView2 (Edge), with this module as its Python side. Everything it reads and writes
is the classic launcher's (issn_launcher_win, passed in as `L`): settings.json, issn.ini, the game
checks, the hardware goals and the way the game starts. The classic Tk window stays as the
fallback: without pywebview or WebView2 the launcher opens it, and the page has a button for it.

Test phase: pages that are not here yet say so and open the classic launcher."""
import base64
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import threading

WEB_DIR = Path(__file__).resolve().parent / 'web'

# The texts of the page's fixed labels (data-t in index.html), translated with the launcher's _().
PAGE_TEXTS = ('Advanced view', 'Classic launcher', 'PLAY', 'RUNNING', 'Ready check', 'Your goal', 'THEME', 'restart',
              'Browse…', 'Open the classic launcher', "In the game: Insert or R3+L2\nopens the port's menu.")

# The news line of the Play page for this version.
NEWS = ('New in 0.3.7:', 'the black city lights up by itself')


class Api:
    """What the page calls (window.pywebview.api.<name>): every method returns plain data."""

    def __init__(self, L):
        self._L = L
        self._window = None
        self._process = None
        self._status = ''
        self._pc = None
        threading.Thread(target=self._detect, daemon=True).start()

    # ---- settings ---------------------------------------------------------------------------
    def _settings(self):
        L = self._L
        app = {**L.APP_DEFAULTS, **L.migrate_settings(L.load_json(L.CONFIG_FILE, {}))}
        return {**app, **L.menu_settings(L.load_ini()[0])}

    def _ini(self):
        return self._L.load_ini()[0]

    def _write_app(self, values):
        L = self._L
        current = {**L.APP_DEFAULTS, **L.migrate_settings(L.load_json(L.CONFIG_FILE, {}))}
        current.update(values)
        L.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        L.atomic_write(L.CONFIG_FILE, __import__('json').dumps(current, indent=2, ensure_ascii=False))
        # The game's menu shows these for the next start (issn_launcher_win.MENU_KEYS).
        menu = {f'menu_{key}': L.menu_ini_value(current, key) for key in values if key in L.MENU_KEYS}
        if menu:
            L.save_ini(menu, L.load_ini()[1])

    def _write_ini(self, values):
        L = self._L
        L.save_ini({key: ('1' if value in (True, '1', 1) else '0') if key in L.INI_FLAGS else str(value)
                    for key, value in values.items()}, L.load_ini()[1])

    def set_value(self, key, store, value):
        """One setting from the page; written at once (the page has no Save button)."""
        L = self._L
        if store == 'ini':
            self._write_ini({key: value})
        else:
            if key in ('expert_view', 'skip_intro', 'close_on_play', 'check_updates', 'hdr', 'frame_gen',
                       'save_backups'):
                value = bool(value)
            self._write_app({key: value})
            if key == 'launcher_theme':
                L.use_theme(value)
        return {}

    # ---- the PC and the goals -----------------------------------------------------------------
    def _detect(self):
        L = self._L
        try:
            self._pc = L.issn_hardware.detect(L.list_gpus())
        except Exception:  # noqa: BLE001 - never stops the window
            self._pc = L.issn_hardware.Pc()

    def apply_goal(self, goal):
        L, H = self._L, self._L.issn_hardware
        if self._pc is None:
            return {'message': L._('Looking at this PC…')}
        chosen, _reasons = H.settings_for(self._pc, goal, screen_height=1080, refresh_hz=L.screen_refresh_rate())
        app = {k: v for k, v in chosen['app'].items() if k in L.APP_DEFAULTS}
        app['launcher_goal'] = goal
        self._write_app(app)
        self._write_ini({k: v for k, v in chosen['ini'].items() if k in L.INI_DEFAULTS})
        resolution = next((L._(*text) for value, text in L.RESOLUTIONS if value == app.get('render_height')), '')
        limit = chosen['ini'].get('fps_limit', '')
        rate = L._('paced') if limit == '0' else limit
        return {'message': L._('Set: {}.').format(' · '.join(filter(None, [
            L._('Resolution: {}').format(resolution), L._('Frame rate limit: {}').format(rate)])))}

    # ---- the page's data ------------------------------------------------------------------------
    def state(self):
        L = self._L
        s = self._settings()
        texts = {text: L._(text) for text in PAGE_TEXTS}
        pages = [{'id': name, 'title': self._title(name), 'expert': name in L.EXPERT_PAGES} for name in L.PAGES]
        return {
            'version': L.VERSION, 'theme': L.issn_themes.known(s.get('launcher_theme')),
            'fx': s.get('launcher_fx') or 'full', 'expert': bool(s.get('expert_view')), 'text': texts,
            'pages': pages, 'themes': self._themes(), 'home': self._home(s), 'font': self._font(),
            'running': self._process is not None,
        }

    def _title(self, name):
        _ = self._L._
        return {'home': _('Play'), 'picture': _('Picture & performance'), 'controls': _('Controls'),
                'game': _('Game & saves'), 'trophies': _('Achievements'), 'news': _("What's new"),
                'customize': _('Customize'), 'help': _('Help'), 'graphics': _('More graphics'),
                'files': _('Patches & mods'), 'pc': _('Your PC'), 'advanced': _('Advanced')}[name]

    def _themes(self):
        L = self._L
        out = []
        for name in L.issn_themes.ORDER:
            c = L.issn_themes.colors(name)
            title, desc = L.issn_themes.NAMES[name]
            out.append({'id': name, 'name': L._(title), 'desc': L._(desc), 'accent': c['accent'],
                        'accent2': c['neon'] if name != 'smoke' else c['ember'],
                        'hero': f"linear-gradient(120deg,{c['card']},{c['panel']} 55%,{c['line']})"})
        return out

    _font_cache = None

    def _font(self):
        if Api._font_cache is None:
            try:
                data = (Path(self._L.FONT_FILE)).read_bytes()
                Api._font_cache = 'data:font/ttf;base64,' + base64.b64encode(data).decode()
            except OSError:
                Api._font_cache = ''
        return Api._font_cache

    _art_cache = (None, '')

    def _art(self, folder):
        path = Path(folder or '.') / 'sce_sys' / 'pic1.png'
        key = (str(path), path.stat().st_mtime if path.is_file() else 0)
        if Api._art_cache[0] == key:
            return Api._art_cache[1]
        url = ''
        if path.is_file():
            try:
                from PIL import Image
                with Image.open(path) as source:
                    image = source.convert('RGB')
                image.thumbnail((1600, 1600))
                buffer = io.BytesIO()
                image.save(buffer, 'JPEG', quality=84)
                url = 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()
            except Exception:  # noqa: BLE001 - no art, the theme's colours stay
                url = ''
        Api._art_cache = (key, url)
        return url

    def _home(self, s):
        L = self._L
        _ = L._
        folder = L.meta_folder(s.get('game_dir'), s.get('update_dir'))
        info = L.game_info(folder)
        chip = ''
        if info:
            title_id = L.game_profile_of(folder)[0]
            chip = f'{title_id or "?"} · ' + _('version {}').format(info[1])
        checks = []
        pc = self._pc
        if pc is None:
            checks.append({'state': 'warn', 'name': _('Graphics card'), 'text': _('Looking at this PC…'), 'page': 'pc'})
        else:
            gpu = pc.gpu_name or _('not recognised')
            checks.append({'state': 'ok' if pc.gpu_score else 'warn', 'name': _('Graphics card'),
                           'text': gpu + (f', {pc.vram_gb} GB' if pc.vram_gb else ''), 'page': 'pc'})
        ready = L.ready_to_play(s.get('game_dir'), s.get('update_dir') or '')
        checks.append({'state': 'ok' if ready else 'bad', 'name': _('Game'),
                       'text': chip if ready and chip else _('Choose the game folder'), 'page': 'game'})
        checks.append(self._shader_check(s))
        checks.append(self._save_check(s))
        checks.append(self._trophy_check(s))
        try:
            opti = L.issn_downloads.optiscaler_state(L.optiscaler_bin())
        except Exception:  # noqa: BLE001
            opti = None
        checks.append({'state': 'ok' if opti else 'warn', 'name': 'OptiScaler',
                       'text': (_('installed') + (f" ({opti.get('version')})" if opti.get('version') else '')) if opti
                       else _('not installed (optional)'), 'page': 'graphics'})
        goal = s.get('launcher_goal') or 'recommended'
        note = ''
        if pc is not None:
            fps = L.issn_hardware.expected_fps(pc, s.get('render_height') or '')
            if fps:
                note = _('About {} FPS expected in the open city at this resolution.').format(fps)
        return {'art': self._art(folder), 'chip': chip, 'sub': self._last_save(s), 'checks': checks,
                'goals': [{'id': 'recommended', 'label': _('Recommended'), 'sub': _('best on most PCs')},
                          {'id': 'handheld', 'label': _('Steam Deck / weak PC'), 'sub': '40-45 FPS'},
                          {'id': 'smooth', 'label': _('Smooth'), 'sub': _('up to 120 FPS')},
                          {'id': 'balanced', 'label': _('Balanced'), 'sub': _('60 FPS, sharpest')}],
                'goal': goal, 'goal_note': note,
                'news': {'title': _(NEWS[0]), 'text': _(NEWS[1])}}

    def _user_dir(self, s):
        return Path(s.get('user_dir') or self._L.DATA_DIR / 'user')

    def _shader_check(self, s):
        _ = self._L._
        try:
            name = (self._L.PORT_DIR / 'shader-seed.txt').read_text().strip()
        except OSError:
            name = ''
        cache = self._user_dir(s) / 'cache' / name if name else None
        ready = bool(cache and cache.is_dir() and any(cache.iterdir()))
        return {'state': 'ok' if ready else 'warn', 'name': _('Shaders'),
                'text': _('ready') if ready else _('prepared on the first start (a few minutes)'), 'page': 'graphics'}

    def _save_check(self, s):
        _ = self._L._
        saves = self._user_dir(s) / 'savedata'
        found = saves.is_dir() and any(saves.rglob('*'))
        return {'state': 'ok' if found else 'warn', 'name': _('Saves'),
                'text': _('found') if found else _('none yet: the game makes them'), 'page': 'game'}

    def _trophy_check(self, s):
        _ = self._L._
        try:
            lines = [line for line in (self._user_dir(s) / 'trophies.txt').read_text(encoding='utf-8').splitlines()
                     if line.strip() and not line.startswith('#')]
        except OSError:
            lines = []
        return {'state': 'ok', 'name': _('Achievements'), 'text': _('{} of 48').format(len(lines)), 'page': 'trophies'}

    def _last_save(self, s):
        try:
            log = self._L.run_log_path(s).read_text(encoding='utf-8', errors='replace')
        except OSError:
            return ''
        found = re.findall(r'^Game saved: (?:\([^)]*\) )?(.+?)\s*$', log, re.M)
        return self._L._('Last save: {}').format(found[-1]) if found else ''

    # ---- settings pages -------------------------------------------------------------------------
    def page(self, name):
        L = self._L
        _ = L._
        s, ini = self._settings(), self._ini()

        def choice(key, store, label, options, hint='', restart=False):
            value = (ini.get(key, L.INI_DEFAULTS.get(key, '')) if store == 'ini' else s.get(key, ''))
            return {'type': 'choice', 'key': key, 'store': store, 'label': label, 'hint': hint, 'restart': restart,
                    'value': str(value if value is not None else ''), 'options': [[v, _(*text)] for v, text in options]}

        def check(key, store, label, hint='', restart=False):
            value = ini.get(key, L.INI_DEFAULTS.get(key, '0')) in ('1', 1, True) if store == 'ini' else bool(s.get(key))
            return {'type': 'check', 'key': key, 'store': store, 'label': label, 'hint': hint, 'restart': restart,
                    'value': value}

        def folder(key, label, hint=''):
            return {'type': 'folder', 'key': key, 'store': 'app', 'label': label, 'hint': hint, 'value': s.get(key) or ''}

        title = self._title(name)
        if name == 'picture':
            return {'title': title, 'subtitle': _('How the game looks and how fast it runs.'), 'sections': [
                {'title': _('Window'), 'rows': [
                    choice('display_mode', 'ini', _('Display mode'), L.DISPLAY_MODES,
                           _('Borderless covers the screen without changing the display mode.')),
                    check('vsync', 'ini', _('VSync'), _('Off allows tearing for the lowest latency.'), True),
                    check('hdr', 'app', _('Allow HDR output'), _('When HDR is on in Windows and the display supports it.')),
                    choice('screen_shape', 'app', _('Screen shape'), L.SCREEN_SHAPES, restart=True)]},
                {'title': _('Resolution'), 'rows': [
                    choice('render_height', 'app', _('Resolution'), L.RESOLUTIONS, restart=True),
                    choice('upscale_quality', 'app', _('FSR 4 / DLSS quality'), L.UPSCALE_QUALITY, restart=True),
                    choice('aniso', 'app', _('Texture filtering'), L.ANISO, restart=True)]},
                {'title': _('Anti-aliasing'), 'rows': [
                    choice('anti_aliasing', 'app', _('Anti-aliasing'), L.ANTI_ALIASING, restart=True)]},
                {'title': _('Frame rate'), 'rows': [
                    choice('fps_limit', 'ini', _('Frame rate limit'), L.FPS_LIMITS, restart=True),
                    choice('frame_pacing', 'app', _('Frame pacing'), L.FRAME_PACING, restart=True),
                    check('frame_gen', 'app', _('Frame generation (FSR 3.1, test)'))]},
                {'title': _('Detail'), 'rows': [
                    choice('draw_distance', 'app', _('Draw distance'), L.DRAW_DISTANCE, restart=True),
                    choice('perf_mode', 'app', _('Performance mode'), L.PERF_MODES, restart=True)]},
                {'title': _('Effects'), 'rows': [
                    check('motion_blur', 'ini', _('Motion blur')),
                    check('chromatic_aberration', 'ini', _('Chromatic aberration'))]},
                {'title': _('Overlay'), 'rows': [
                    check('show_fps', 'ini', _('Show performance overlay'),
                          _('Items, size and corner: in-game menu (Insert / R3+L2) > Overlay. F11 hides it in the game.'))]}]}
        if name == 'game':
            return {'title': title, 'subtitle': _('Your game dump, your saves and the game language.'), 'sections': [
                {'title': _('Folders'), 'rows': [
                    folder('game_dir', _('Game folder')), folder('update_dir', _('Update folder (optional)')),
                    folder('user_dir', _('Saves folder'))]},
                {'title': _('Language'), 'rows': [
                    choice('game_language', 'app', _('Game language'), L.LANGUAGES, restart=True),
                    check('skip_intro', 'app', _('Skip the intro logos'), restart=True)]}]}
        if name == 'graphics':
            cards = [('', ('Automatic',))] + [(row[0], (row[1],)) for row in L.list_gpus()]
            return {'title': title, 'subtitle': _('Video memory, shaders, the graphics card and OptiScaler.'), 'sections': [
                {'title': _('Memory and shaders'), 'rows': [
                    choice('vram_limit', 'app', _('Video memory limit'), L.VRAM_LIMITS, restart=True),
                    choice('shader_compile', 'app', _('Shader compilation'), L.SHADER_COMPILE, restart=True)]},
                {'title': _('Graphics card'), 'rows': [choice('gpu_id', 'app', _('Graphics card'), cards, restart=True)]}],
                'placeholder': _('OptiScaler is still on the classic launcher for now.')}
        if name == 'customize':
            return {'title': title, 'subtitle': _("The launcher's look, its language, what it does while you play, and shortcuts."),
                    'sections': [
                        {'title': _('Theme'), 'rows': [{'type': 'themes'}]},
                        {'title': _('Look'), 'rows': [
                            {**choice('launcher_fx', 'app', _('Effects'), L.FX_LEVELS,
                                      _('The animation on the Play page. It stops by itself while the game runs.')),
                             'type': 'segments'}]},
                        {'title': _('Launcher'), 'rows': [
                            choice('ui_language', 'app', _('Launcher language'), L.UI_LANGUAGES,
                                   _('Applies when the launcher opens again.')),
                            check('close_on_play', 'app', _('Close the launcher when the game starts')),
                            check('check_updates', 'app', _('Check for updates when the launcher opens'))]}]}
        return {'title': title, 'subtitle': '', 'sections': [],
                'placeholder': _('This page is still on the classic launcher while the new one is being made.')}

    # ---- actions --------------------------------------------------------------------------------
    def choose_folder(self, key, current=''):
        import webview
        chosen = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=current or str(self._L.PORT_DIR))
        return str(Path(chosen[0])) if chosen else ''

    def action(self, name):
        L = self._L
        if name == 'discord':
            L.open_url(L.DISCORD_URL)
        return ''

    def status(self):
        return {'text': self._status, 'running': self._process is not None}

    def play(self):
        L = self._L
        _ = L._
        s = self._settings()
        if not L.game_info(s.get('game_dir')):
            return {'message': _('Choose the game folder with eboot.bin (CUSA00004).'), 'page': 'game'}
        if not L.ready_to_play(s.get('game_dir'), s.get('update_dir') or ''):
            return {'message': _('The game folder is incomplete: the classic launcher says what is missing.'), 'page': 'game'}
        upscaler = s.get('anti_aliasing')
        if L.WINDOWS and upscaler in L.UPSCALER_PACKAGES and L.upscaler_missing(upscaler):
            return {'message': _("{}'s files are not here yet: the classic launcher downloads them (or pick another "
                                 'anti-aliasing).').format(upscaler.upper()), 'page': 'picture'}
        if self._process is not None:
            return {}
        self._status = _('Preparing the game; it opens in its own window…')
        threading.Thread(target=self._run, args=(s,), daemon=True).start()
        return {}

    def _run(self, settings):
        L = self._L
        _ = L._
        self._process = True
        try:
            if self._window is not None and settings.get('close_on_play'):
                self._window.minimize()
            code = L.play_without_window(settings)
            self._status = _('The game closed.') if not code else _('The game exited (code {}).').format(code).strip()
        except Exception as error:  # noqa: BLE001
            self._status = _('Could not start: {}').format(error)
        finally:
            self._process = None
            if self._window is not None:
                try:
                    self._window.restore()
                except Exception:  # noqa: BLE001
                    pass

    def classic(self):
        """Opens the classic Tk launcher (and keeps it as the choice) and closes this window."""
        L = self._L
        self._write_app({'new_launcher': False})
        command = [sys.executable, '--classic'] if L.FROZEN else [sys.executable, str(Path(L.__file__).resolve()), '--classic']
        subprocess.Popen(command, cwd=L.PORT_DIR, creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0))
        if self._window is not None:
            self._window.destroy()


def run(L, settings):
    """Shows the new launcher. False when it cannot (no pywebview, no WebView2): the caller opens the
    classic one."""
    try:
        import webview
    except ImportError:
        return False
    L.use_theme(settings.get('launcher_theme'))
    api = Api(L)
    try:
        web = L.PORT_DIR / 'launcher' / 'web' if L.FROZEN else WEB_DIR  # package.sh copies it there
        window = webview.create_window(f'{L.APP_NAME} (PC)', url=str(web / 'index.html'), js_api=api,
                                       width=1280, height=780, min_size=(1100, 680), background_color=L.BG)
        api._window = window
        webview.start(gui='edgechromium' if L.WINDOWS else None, private_mode=False,
                      storage_path=str(L.CONFIG_DIR / 'webview'))
    except Exception as error:  # noqa: BLE001 - WebView2 missing or broken: the classic window opens
        print(f'New launcher unavailable: {error}', file=sys.stderr)
        return False
    return True
