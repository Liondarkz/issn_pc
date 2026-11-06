# SPDX-License-Identifier: GPL-2.0-or-later
"""The new launcher window (issn 11-05, João: "the look of the preview, even if the whole launcher
has to change"): the page in launcher/web (HTML, CSS and JavaScript) shown by pywebview in
Windows' own WebView2 (Edge), with this module as its Python side. Everything it reads and writes
is the classic launcher's (issn_launcher_win, passed in as `L`): settings.json, issn.ini, the game
checks, the hardware goals and the way the game starts. The classic Tk window stays as the
fallback: without pywebview or WebView2 the launcher opens it, and the side bar has a button for it.

11-05 (later): every page of the classic launcher is here too (Controls, Achievements, What's new,
Help, Patches & mods, Your PC, Advanced, OptiScaler, the save backups, updates and shortcuts).

The page asks for data (state, page) and sends what the player does (set_value, action, play);
an answer can carry `toast`, `alert`, `confirm` {text, action, args, no}, `pick` {action, args},
`page`, `reload` or `play`, and the page follows it (launcher/web/app.js handle()). Long work
(downloads, installs) runs in a thread: its progress is the status line, and when it ends the
page rebuilds itself (status()['seq'])."""
import base64
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

WEB_DIR = Path(__file__).resolve().parent / 'web'

# The texts of the page's fixed labels (data-t in index.html and app.js), translated with _().
PAGE_TEXTS = ('Advanced view', 'Classic launcher', 'PLAY', 'RUNNING', 'Ready check', 'Your goal', 'THEME', 'restart',
              'Browse…', 'Open the classic launcher', "In the game: Insert or R3+L2\nopens the port's menu.",
              'Yes', 'No', 'OK', 'Copied ✓', 'GAME BUTTON', 'KEYBOARD', 'GAMEPAD', 'Error',
              'This page could not be loaded. Send a print of it on the Discord.')

# The news line of the Play page for this version.
NEWS = ('New in 0.3.7:', 'the black city lights up by itself')

GOALS = (('recommended', 'Recommended (best on most PCs)'), ('handheld', 'Steam Deck / weak PC (40-45 FPS)'),
         ('smooth', 'Smooth (up to 120 FPS)'), ('balanced', 'Balanced (60 FPS, sharpest)'),
         ('quiet', 'Quiet (60 FPS, lighter on the card)'))


def button(action, label, args=None, confirm=None, primary=False, url=None, copy=None):
    """A button of a row: an action of the Python side (with its arguments), a link or a text to copy."""
    return {'action': action, 'label': label, 'args': list(args or []), 'confirm': confirm, 'primary': primary,
            'url': url, 'copy': copy}


def note(text):
    return {'type': 'note', 'text': text}


def card(title, text='', tone='', steps=(), mono='', buttons=(), badge='', date='', image=''):
    return {'type': 'card', 'title': title, 'text': text, 'tone': tone, 'steps': list(steps), 'mono': mono,
            'buttons': list(buttons), 'badge': badge, 'date': date, 'image': image}


def buttons(*items, hint=''):
    return {'type': 'buttons', 'buttons': list(items), 'hint': hint}


class Api:
    """What the page calls (window.pywebview.api.<name>): every method returns plain data."""

    def __init__(self, L):
        self._L = L
        self._window = None
        self._process = None
        self._status = ''
        self._pc = None
        self._seq = 0
        self._jobs = set()
        self._goto = ''
        self._news = None          # None: not asked yet; 'loading'; a list; or the error text
        self._declined = set()     # PKG folders whose system files the player did not want copied
        self._optiscaler_asked = False
        self._upscaler_tried = set()
        self._trophy_cache = {}
        threading.Thread(target=self._detect, daemon=True).start()

    # ---- settings ---------------------------------------------------------------------------
    def _settings(self):
        L = self._L
        app = {**L.APP_DEFAULTS, **L.migrate_settings(L.load_json(L.CONFIG_FILE, {}))}
        ini = L.load_ini()[0]
        return L.merge_ini_keys({**app, **L.menu_settings(ini)}, ini)

    def _ini(self):
        return self._L.load_ini()[0]

    def _write_app(self, values):
        L = self._L
        current = {**L.APP_DEFAULTS, **L.migrate_settings(L.load_json(L.CONFIG_FILE, {}))}
        current.update(values)
        L.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        L.atomic_write(L.CONFIG_FILE, json.dumps(current, indent=2, ensure_ascii=False))
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
            if key in ('pad_sensitivity', 'mouse_sensitivity'):
                try:
                    value = f'{float(value):.2f}'
                except (TypeError, ValueError):
                    return {'toast': L._('Not a number.')}
            self._write_ini({key: value})
            return {}
        if key in ('expert_view', 'skip_intro', 'close_on_play', 'check_updates', 'hdr', 'frame_gen',
                   'save_backups', 'mods_enabled', 'frame_stats', 'seed_any_profile', 'vk_validation'):
            value = bool(value)
        self._write_app({key: value})
        if key == 'launcher_theme':
            L.use_theme(value)
        if key == 'anti_aliasing' and L.WINDOWS and value in L.UPSCALER_PACKAGES and L.upscaler_missing(value):
            self._download_upscaler(value)  # XeSS / DLSS chosen: their files now, not at Play
            return {'reload': True}
        if key == 'optiscaler_output':
            return self._optiscaler_output(value)
        if key in ('anti_aliasing', 'game_dir', 'update_dir', 'user_dir', 'patches_dir', 'mods_dir'):
            return {'reload': True}
        return {}

    # ---- the PC and the goals -----------------------------------------------------------------
    def _detect(self):
        L = self._L
        try:
            self._pc = L.issn_hardware.detect(L.list_gpus())
        except Exception:  # noqa: BLE001 - never stops the window
            self._pc = L.issn_hardware.Pc()
        self._seq += 1

    def apply_goal(self, goal):
        """The graphics for the goal on this PC (issn_hardware.settings_for), written at once; the
        answer says what was set and why (the classic Your PC page's text)."""
        L, H = self._L, self._L.issn_hardware
        _ = L._
        if self._pc is None:
            return {'message': _('Looking at this PC…')}
        chosen, reasons = H.settings_for(self._pc, goal, screen_height=1080, refresh_hz=L.screen_refresh_rate())
        app = {k: v for k, v in chosen['app'].items() if k in L.APP_DEFAULTS}
        app['launcher_goal'] = goal
        self._write_app(app)
        self._write_ini({k: v for k, v in chosen['ini'].items() if k in L.INI_DEFAULTS})

        def label(options, value):
            return next((_(*text) for v, text in options if v == value), value)
        notes = [_('Resolution: {}').format(label(L.RESOLUTIONS, app.get('render_height'))),
                 _('Frame rate limit: {}').format(label(L.FPS_LIMITS, chosen['ini'].get('fps_limit'))),
                 _('Anti-aliasing: {}').format('FSR 4' if app.get('anti_aliasing') == 'fsr4' else _('Off')
                                               if app.get('anti_aliasing') == 'off' else _("the game's own")),
                 _('Performance mode: {}').format({'': _('off'), 'low': _('Balanced'), 'office': _('Maximum')}.get(
                     app.get('perf_mode', ''), '')),
                 _('Texture filtering: {}x').format(app.get('aniso'))]
        if goal == 'handheld':
            notes.append(_('Draw distance: {}').format(_('Short (faster)')))
        why = []
        for kind, value in reasons:
            if kind == 'paced':
                why.append(_('a {} Hz screen shows {} FPS evenly').format(*value))
            elif kind == 'cpu':
                why.append(_('the processor holds about {} FPS in the city').format(value))
            elif kind == 'ram':
                why.append(_('{} GB of memory').format(value))
            elif kind == 'vram':
                why.append(_('{} GB of video memory').format(value))
            elif kind == 'deck40':
                why.append(_('a 60 Hz screen: set the Steam Deck to 40 Hz (quick menu > Performance > Refresh '
                             'rate) so every frame shows for the same time'))
            elif kind == 'handheld':
                why.append(_('a 60 Hz screen holds 40 FPS evenly only at 40 Hz or with FreeSync / G-Sync'))
        text = _('Set: {}.').format(' · '.join(notes))
        if why:
            text += ' ' + _('Because: {}.').format(', '.join(why))
        self._goal_text = text
        return {'message': text}

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
        opti = self._optiscaler_state()
        checks.append({'state': 'ok' if opti and not opti['foreign'] else 'warn', 'name': 'OptiScaler',
                       'text': (_('installed') + (f" ({opti.get('version')})" if opti.get('version') else ''))
                       if opti and not opti['foreign'] else _('not installed (optional)'), 'page': 'graphics'})
        goal = s.get('launcher_goal') or 'recommended'
        note_text = ''
        if pc is not None:
            fps = L.issn_hardware.expected_fps(pc, s.get('render_height') or '')
            if fps:
                note_text = _('About {} FPS expected in the open city at this resolution.').format(fps)
        return {'art': self._art(folder), 'chip': chip, 'sub': self._last_save(s), 'checks': checks,
                'goals': [{'id': 'recommended', 'label': _('Recommended'), 'sub': _('best on most PCs')},
                          {'id': 'handheld', 'label': _('Steam Deck / weak PC'), 'sub': '40-45 FPS'},
                          {'id': 'smooth', 'label': _('Smooth'), 'sub': _('up to 120 FPS')},
                          {'id': 'balanced', 'label': _('Balanced'), 'sub': _('60 FPS, sharpest')}],
                'goal': goal, 'goal_note': note_text,
                'news': {'title': _(NEWS[0]), 'text': _(NEWS[1])}}

    def _user_dir(self, s=None):
        s = s if s is not None else self._settings()
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
                'text': _('ready') if ready else _('prepared on the first start (a few minutes)'), 'page': 'advanced'}

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

        def number(key, label, low, high, hint=''):
            value = ini.get(key, L.INI_DEFAULTS.get(key, '1.0'))
            return {'type': 'number', 'key': key, 'store': 'ini', 'label': label, 'hint': hint, 'value': str(value),
                    'min': low, 'max': high, 'step': 0.05}

        builder = getattr(self, f'_page_{name}', None)
        if builder is None:
            return {'title': self._title(name), 'subtitle': '', 'sections': []}
        schema = builder(s, ini, choice, check, folder, number)
        schema.setdefault('title', self._title(name))
        return schema

    def _page_picture(self, s, ini, choice, check, folder, number):
        L = self._L
        _ = L._
        sections = [
            {'title': _('Quick setup'), 'rows': [
                buttons(button('goal', _('Recommended (best on most PCs)'), ['recommended'], primary=True),
                        button('goal', _('Steam Deck / weak PC (40-45 FPS)'), ['handheld']),
                        hint=_('Sets everything below for this PC. Advanced view > Your PC has the other goals and '
                               'what the launcher found.'))]},
            {'title': _('Window'), 'rows': [
                choice('display_mode', 'ini', _('Display mode'), L.DISPLAY_MODES,
                       _('Borderless covers the screen without changing the display mode.')),
                check('vsync', 'ini', _('VSync'), _('Off allows tearing for the lowest latency.'), True),
                check('hdr', 'app', _('Allow HDR output'), _('When HDR is on in Windows and the display supports it.')),
                choice('screen_shape', 'app', _('Screen shape'), L.SCREEN_SHAPES,
                       _('Ultrawide and other screens: the game fills the shape and sees more at the sides (or above and '
                         'below on 16:10); the HUD moves to the edges and movies keep their shape with bars. Automatic '
                         'follows the window, so play in fullscreen on an ultrawide monitor.'), True)]},
            {'title': _('Resolution'), 'rows': [
                choice('render_height', 'app', _('Resolution'), L.RESOLUTIONS,
                       _('The size the game draws at. Lower is faster; higher is sharper and heavier (2K and 4K need '
                         'more memory, given by itself). The picture is then fitted to the window, and the HUD keeps '
                         'its layout. On wider screens the width grows with the shape.'), True),
                choice('upscale_quality', 'app', _('FSR 4 / DLSS quality'), L.UPSCALE_QUALITY,
                       _('With FSR 4, XeSS or DLSS (Anti-aliasing below): the game draws smaller than the window and '
                         'the upscaler rebuilds a sharp picture at the window size. Quality looks closest to native, '
                         'Ultra Performance is the fastest. When not Off it decides the size instead of Resolution.'),
                       True),
                choice('aniso', 'app', _('Texture filtering'), L.ANISO,
                       _('Anisotropic filtering: ground, roads and walls seen at an angle stay sharp in the distance. '
                         'Costs a little FPS on weak graphics cards; it does not change stutter.'), True)]},
            {'title': _('Anti-aliasing'), 'rows': self._anti_aliasing_rows(choice)},
            {'title': _('Frame rate'), 'rows': [
                choice('fps_limit', 'ini', _('Frame rate limit'), L.FPS_LIMITS,
                       _('The highest frame rate. The game keeps its speed at any frame rate; Unlimited runs as fast '
                         'as the PC allows.'), True),
                choice('frame_pacing', 'app', _('Frame pacing'), L.FRAME_PACING,
                       _('For screens without G-Sync / FreeSync: when the game runs between half and the full '
                         'refresh rate, some frames stay on screen longer than others and motion judders even at a '
                         'high FPS. Half or a third shows every frame for the same time (V-Sync on). Pick the one '
                         'your PC holds in the heaviest places.'), True),
                check('frame_gen', 'app', _('Frame generation (FSR 3.1, test)'),
                      _('Makes a frame between every two of the game: 60 FPS on the screen become 120. Needs FSR 4, '
                        'XeSS or DLSS as anti-aliasing (above). Adds a little input delay; best with a base of '
                        '60 FPS or more, and with Frame pacing at half on fast screens (72 becomes 144 on 144 Hz).'))]},
            {'title': _('Detail'), 'rows': [
                choice('draw_distance', 'app', _('Draw distance'), L.DRAW_DISTANCE,
                       _('How far buildings and details keep their full detail in the open city. Long and Very long '
                         'show less pop-in than the PS4 and cost some FPS.'), True),
                choice('perf_mode', 'app', _('Performance mode'), L.PERF_MODES,
                       _("The game's own lower settings, from axylpup's 1.1: shorter detail distance, one shadow "
                         'level less, lighter particles and fewer high-resolution textures nearby. Figures are '
                         'from his PC in the heaviest city view.'), True)]},
            {'title': _('Effects'), 'rows': [
                check('motion_blur', 'ini', _('Motion blur'),
                      _('The blur when the camera or Delsin moves fast. Off gives a sharp picture in motion; '
                        'it also changes at once in the game (Insert > Image).')),
                check('chromatic_aberration', 'ini', _('Chromatic aberration'),
                      _('The red and blue fringes toward the edges of the screen. Turning it off helps players '
                        'who feel sick with it; it also changes at once in the game (Insert > Image).')),
                self._sharpening_row(choice)]},
            {'title': _('Overlay'), 'rows': [
                check('show_fps', 'ini', _('Show performance overlay'),
                      _('Items, size and corner: in-game menu (Insert / R3+L2) > Overlay. F11 hides it in the game.')),
                note(_('Screenshots: F12 (saved in the user\\screenshots folder).') if L.WINDOWS else
                     _('Screenshots: F12 (saved in the user/screenshots folder).'))]}]
        return {'subtitle': _('How the game looks and how fast it runs.'), 'sections': sections}

    def _sharpening_row(self, choice):
        # issn 11-05 (the sharpening conversation): 0-100 % in steps of 5; a value set in the game's
        # slider is listed too.
        L = self._L
        _ = L._
        row = choice('sharpening', 'ini', _('Sharpening'), L.SHARPENING,
                     _('Crisper picture; applied once (FSR 4, OptiScaler or the port). Slider in the game: '
                       'Insert > Image.'))
        row['value'] = str(L.sharpening_percent(row['value']))
        if row['value'] not in [v for v, _text in row['options']]:
            row['options'].append([row['value'], row['value'] + '%'])
            row['options'].sort(key=lambda o: int(o[0]))
        return row

    def _anti_aliasing_rows(self, choice):
        L = self._L
        _ = L._
        rows = [choice('anti_aliasing', 'app', _('Anti-aliasing'), L.ANTI_ALIASING,
                       _("Replaces the game's own temporal anti-aliasing. FSR 4 (included) needs a Radeon RX 7000 or "
                         "9000; XeSS runs on most recent cards (Arc, Radeon RX 5000+, GeForce GTX 10+) with Intel's "
                         "file; DLSS needs an NVIDIA RTX card and NVIDIA's files (below). Without what they need the "
                         "game keeps its own.") if L.WINDOWS else
                       _("Replaces the game's own temporal anti-aliasing. FSR 4 (included) needs a Radeon RX 7000 or "
                         "9000 and a recent Mesa (RADV); without it the game keeps its own."), True),
                note(_('Off: no anti-aliasing at all, the sharpest picture with jagged edges (no smearing in '
                       'motion).'))]
        if L.WINDOWS:
            for key in ('xess', 'dlss'):
                name = L.UPSCALER_PACKAGES[key].name
                state = (_('Downloading {} files…').format(name) if f'upscaler:{key}' in self._jobs else
                         _('{} files: ready').format(name) if not L.upscaler_missing(key) else
                         _('{} files: not downloaded yet').format(name))
                rows.append(buttons(button('download_upscaler', _('Download {} now').format(name), [key]),
                                    button('open_path', _('Open the {} folder').format(name), [key]), hint=state))
            rows.append(note(_("XeSS and DLSS use Intel's and NVIDIA's own files, which the port may not include: the "
                               "launcher downloads them from Intel's and NVIDIA's GitHub releases (XeSS SDK 3.0.2, about "
                               "60 MB; Streamline 2.14.16, about 35 MB) when you pick one, and checks each file.")))
        return rows

    def _page_controls(self, s, ini, choice, check, folder, number):
        L = self._L
        _ = L._
        style = s.get('pad_style') if s.get('pad_style') in L.PAD_STYLES else 'playstation'
        saved = {kind: L.control_values(s.get('controls'), kind) for kind in ('key', 'pad')}
        labels = L.PAD_STYLES[style]
        table = []
        for game_button, key, pad in L.CONTROLS:
            row = {'button': game_button, 'name': L.PS_BUTTON_NAMES[game_button],
                   'key': saved['key'].get(game_button) or key, 'default_key': key}
            if pad:
                row['pad'] = saved['pad'].get(game_button) or pad
                row['pad_options'] = [[name, label] for name, label in labels.items()]
            else:
                row['fixed'] = (_('L1 + Touchpad right') if game_button == 'touchpad_left'
                                else _('{} trigger').format(L.PS_BUTTON_NAMES[game_button]))
            table.append(row)
        sections = [
            {'title': _('Controls'), 'rows': [
                note(_('Which key or gamepad button presses each button of the game.')),
                {'type': 'segments', 'key': 'pad_style', 'store': 'app', 'label': _('Gamepad names'), 'value': style,
                 'options': [['playstation', 'PlayStation'], ['xbox', 'Xbox']], 'hint': '', 'reload': True},
                choice('pad_emulation', 'app', _('Controller without touchpad'), L.PAD_EMULATION,
                       _('For Xbox, 8BitDo and other pads without a touchpad or motion sensors. Touchpad click: '
                         'Select/Back (with L1: the left side). Swipe: hold Select/Back and push the right stick. '
                         'Two fingers: hold Select/Back + RB. Spray can: Insert > Controls > Spray can tilts the pad, LB '
                         'rattles it, the right stick aims. Automatic turns it off on a DualShock 4 or DualSense.'), True),
                choice('glyph_style', 'app', _('Button icons'), L.GLYPH_STYLES,
                       _('The buttons the game shows in its hints and menus. Keyboard and mouse shows the keys '
                         '(they follow your key changes above) and the mouse buttons; Automatic picks the keyboard '
                         'when no controller is connected at start, Xbox letters for Xbox and other pads.'), True),
                number('pad_sensitivity', _('Controller sensitivity'), 0.25, 3.0,
                       _('The right stick (camera). Below 1: slower. Above 1: small stick moves turn the camera more. '
                         'Also in the game: Insert > Controls.')),
                number('mouse_sensitivity', _('Mouse sensitivity'), 0.1, 5.0,
                       _('How far the camera turns per mouse movement. Also in the game: Insert > Controls, or - / =.')),
                choice('stick_deadzone', 'app', _('Stick dead zone'), L.STICK_DEADZONE,
                       _('How far a stick must move before the game sees it. Raise it if Delsin walks or the '
                         'camera turns on its own while you do not touch the stick (drift).'), True),
                choice('spray_stick', 'app', _('Spray can stick speed'), L.SPRAY_STICK,
                       _('How fast the right stick turns the spray can while the pad is tilted (Insert > Controls > '
                         'Spray can). A light push moves it a little, a full push at this speed. In the game, - / = '
                         'change it too.'), True)]},
            {'title': _('Buttons'), 'rows': [
                note(_('Keyboard and mouse: WASD moves, IJKL turns the camera. Click in the game window and the '
                       "mouse aims (1:1, as in axylpup's build); Y or F7 lets the mouse go and takes it back, "
                       '- / = change its speed. Left click R2 (shoot), right click L2 (aim). Spray can: hold Z '
                       'to tilt the pad, tap Z or hold X to rattle it, move the mouse to aim. R: the touchpad (held, '
                       'both thumbs: the drains, the car on Reggie); R + W/A/S/D: a swipe (doors, wreckage, the '
                       'Mobile Command Center core: hold R + W and click to hit it); G: its left side (photo mode). '
                       "Arrows: d-pad. F12: screenshot. Insert: the port's menu.")),
                {'type': 'controls', 'rows': table, 'style': style},
                note(_('Keys use SDL names: letters, digits, Space, Return, Tab, Left Shift, Left Ctrl, Left Alt, '
                       'Backspace, Up, Down, Left, Right, F1 to F12. A name the game does not know keeps the default '
                       '(the log says which). L2/R2 on the gamepad stay on the triggers. Touchpad right opens the '
                       "game's pause menu; Touchpad left (photo mode) is L1 + Touchpad right on the gamepad. Shake moves "
                       'the motion sensors like a shaken controller (the spray can), for pads without them. Insert '
                       "and R3+L2 always open the port's menu. Applied when the game starts.")),
                buttons(button('reset_controls', _('Reset to defaults'),
                               confirm=_('Put every key and gamepad button back to the default?')))]}]
        return {'subtitle': _('Controller, keyboard and mouse, the button icons and the sensitivity.'), 'sections': sections}

    def set_control(self, kind, game_button, value):
        """One key or gamepad button of the Controls table: settings.json (only the ones that differ
        from the default) and, for keys, issn.ini key_map, which the game's menu also changes."""
        L = self._L
        s = self._settings()
        values = {}
        for row_button, key, pad in L.CONTROLS:
            default = key if kind == 'key' else pad
            if default:
                values[row_button] = L.control_values(s.get('controls'), kind).get(row_button) or default
        if game_button not in values:
            return {}
        values[game_button] = str(value or '').strip() or values[game_button]
        controls = dict(s.get('controls') or {})
        controls[kind] = L.control_changes(values, kind)
        self._write_app({'controls': controls})
        if kind == 'key':
            self._write_ini({'key_map': L.control_map(controls, 'key')})
        return {}

    def _page_game(self, s, ini, choice, check, folder, number):
        L = self._L
        _ = L._
        user = self._user_dir(s)
        backups = L.save_backup.list_backups(user)
        backup_rows = [check('save_backups', 'app', _('Back up saves before a start'),
                             _('A copy of your saves before each start when they changed (kept in '
                               'user\\savedata_backups). Off: no new copies; the old ones stay and can still be restored.'))]
        if backups:
            backup_rows.append({'type': 'pick', 'id': 'backup', 'label': _('Save backups'), 'options': backups,
                                'buttons': [button('restore_backup', _('Restore'))],
                                'hint': _('Made before every start when the saves changed, newest first. Kept: the last '
                                          '{}, one a day for {} days and the ones made before a restore or an import. '
                                          'Restoring backs up the current saves first.').format(
                                              L.save_backup.KEEP, L.save_backup.DAYS)})
        backup_rows.append(buttons(button('import_saves', _('Import from another port…')),
                                   hint=_("Brings your progress from another PC port of the game (HayatoG's iss_pc, or "
                                          'an older folder of this one): pick its folder. The current saves are backed '
                                          'up first.')))
        sections = [
            {'title': _('Folders'), 'rows': [
                folder('game_dir', _('Game folder'),
                       _('Your own dump of the game (eboot.bin, sce_module, sce_sys): inFAMOUS Second Son, '
                         'any region and version.')),
                folder('update_dir', _('Update folder (optional)'),
                       _('Only if the update is dumped apart from the game: it is laid over the game folder '
                         'without changing either. Leave empty when the game folder already has the update.')),
                folder('user_dir', _('Saves folder'),
                       _('Empty: {} (shader caches are kept there too).').format(L.DATA_DIR / 'user'))]},
            {'title': _('Saves'), 'rows': backup_rows},
            {'title': _('Language'), 'rows': [
                choice('game_language', 'app', _('Game language'), L.LANGUAGES,
                       _('Text and voices. Automatic follows the launcher language (Portuguese: the Brazilian dub).'),
                       True),
                check('skip_intro', 'app', _('Skip the intro logos'),
                      _('The game opens straight on its title screen, without the PlayStation and Sucker Punch '
                        'logos.'), True)]},
            {'title': _('Paper Trail'), 'rows': [
                check('paper_trail', 'ini', _('Paper Trail without internet'),
                      _("Paper Trail's missions were unlocked by a website that closed in 2018. With this on, "
                        "the port answers for it: every step counts as done and the missions open once the "
                        'story reaches them (about a third of the way in).'), True)]}]
        problems = L.issn_help.game_problems(s.get('game_dir'), s.get('update_dir') or '') \
            if hasattr(L.issn_help, 'game_problems') else []
        if problems:
            sections.insert(0, {'title': '', 'rows': [self._topic_card(topic) for topic in problems]})
        return {'subtitle': _('Your game dump, your saves and the game language.'), 'sections': sections}

    def _trophy_folders(self, s):
        return [s.get('update_dir') or '', s.get('game_dir') or '']

    def _page_trophies(self, s, ini, choice, check, folder, number):
        L = self._L
        _ = L._
        T = L.trophies
        user = self._user_dir(s)
        folders = self._trophy_folders(s)
        trp, ids, unlocked = T.summary(user, *folders)
        rows = []
        key = T.read_key(user)
        key_note = ''
        if not trp:
            rows.append(note(_('No trophy file in the game folder (sce_sys\\trophy\\trophy00.trp, or Sc0\\trophy beside '
                               'it). Choose the game folder on the Game page.')))
        else:
            language = L.game_language(s)
            try:
                texts, source = T.game_texts(trp, language, *folders, key=key)
                key_note = (_("Key saved on this PC: the names are the game's own.") if source == 'key' else
                            _("No key: the names come from the port's own list.") if source == 'list' else
                            _('No key: names are not shown.'))
            except (OSError, ValueError, KeyError, T.ElementTree.ParseError) as error:
                texts, key_note = {}, _('The key is saved but the names could not be read: {}').format(error)
            entries = T.read_entries(trp)
            done = sum(1 for i in ids if i in unlocked)
            percent = round(100 * done / len(ids)) if ids else 0
            platinum = next((i for i, t in texts.items() if t.get('grade') == 'P'), 0 if ids and ids[0] == 0 else None)
            line = _('{} of {} unlocked ({}%)').format(done, len(ids), percent)
            if platinum is not None and platinum in unlocked:
                line += '  ·  ' + _('Platinum! Every trophy unlocked')
            rows.append({'type': 'progress', 'value': percent, 'text': line})
            grades = {'P': (_('Platinum'), '#c8e6ff'), 'G': (_('Gold'), '#ffcc4d'), 'S': (_('Silver'), '#d2d6e0'),
                      'B': (_('Bronze'), '#db8f5c')}
            newest = sorted((i for i in ids if i in unlocked), key=lambda i: (unlocked[i], i), reverse=True)
            items = []
            for trophy_id in newest + [i for i in ids if i not in unlocked]:
                got = trophy_id in unlocked
                text = texts.get(trophy_id, {})
                grade = grades.get(text.get('grade') or ('P' if trophy_id == platinum else ''))
                items.append({'icon': self._trophy_icon(trp, entries, trophy_id, got), 'got': got,
                              'name': text.get('name') if got and text.get('name') else _('Trophy {}').format(trophy_id),
                              'detail': text.get('detail', '') if got else '',
                              'when': _('Unlocked {}').format(unlocked[trophy_id]) if got else _('Locked'),
                              'grade': grade[0] if grade else '', 'color': grade[1] if grade else ''})
            rows.append({'type': 'trophies', 'items': items})
        sections = [
            {'title': '', 'rows': rows},
            {'title': _('In the game'), 'rows': [
                check('trophy_note', 'ini', _('Trophy popup'),
                      _("The trophy's icon and name in a top corner for a few seconds when the game unlocks one. "
                        'Also in Insert > Overlay.'))]},
            {'title': _('Trophy names (optional)'), 'rows': [
                note(_("The names and descriptions in the game's trophy file are encrypted. Without a key the port "
                       'uses its own list (English, Portuguese and Spanish, written from public trophy lists). '
                       'If you have your own PS4 trophy key (the same one shadPS4 asks for), paste it here to see '
                       "the game's own texts: it stays only on this PC, in user\\trophy-key.txt, and the port never "
                       'ships or sends it. A name shows once its trophy is unlocked.')),
                {'type': 'entry', 'id': 'trophy_key', 'label': _('Trophy key'), 'secret': True, 'hint': key_note,
                 'buttons': [button('save_trophy_key', _('Save key')),
                             button('remove_trophy_key', _('Remove key'), confirm=_('Remove the saved trophy key?'))
                             if key else None]}]}]
        return {'subtitle': _("The game's trophies: the ones unlocked in this port are in color, with the date."),
                'sections': sections}

    def _trophy_icon(self, trp, entries, trophy_id, unlocked):
        cache = (str(trp), trophy_id, unlocked)
        if cache not in self._trophy_cache:
            url = ''
            data = self._L.trophies.icon(trp, entries, trophy_id)
            if data:
                try:
                    from PIL import Image, ImageEnhance, ImageOps
                    image = Image.open(io.BytesIO(data)).convert('RGBA').resize((112, 112), Image.LANCZOS)
                    if not unlocked:
                        alpha = image.getchannel('A')
                        image = ImageEnhance.Brightness(ImageOps.grayscale(image).convert('RGBA')).enhance(0.45)
                        image.putalpha(alpha)
                    buffer = io.BytesIO()
                    image.save(buffer, 'PNG', optimize=True)
                    url = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
                except (ImportError, OSError, ValueError):
                    url = ''
            self._trophy_cache[cache] = url
        return self._trophy_cache[cache]

    def _page_news(self, s, ini, choice, check, folder, number):
        L = self._L
        _ = L._
        rows = []
        if not L.UPDATES:
            rows.append(note(_('No releases yet.')))
        elif self._news is None or self._news == 'loading':
            if self._news is None:
                self._news = 'loading'
                threading.Thread(target=self._load_news, daemon=True).start()
            rows.append(note(_('Loading the release notes from GitHub…')))
        elif isinstance(self._news, str):
            rows.append(card(_('Could not load the release notes: {}').format(self._news), tone='bad',
                             buttons=[button('reload_news', _('Check again'))]))
        elif not self._news:
            rows.append(note(_('No releases yet.')))
        else:
            for index, release in enumerate(self._news):
                newer = L.version_tuple(release['version']) > L.version_tuple(L.VERSION)
                same = L.version_tuple(release['version']) == L.version_tuple(L.VERSION)
                actions = []
                if index == 0 and newer and release['zip'] and L.WINDOWS:
                    actions.append(button('install_update', _('Update'), [release['version'], release['zip'],
                                                                           release['page']], primary=True,
                                          confirm=_('Install version {} now? The launcher closes, installs it and opens '
                                                    'again. Saves and settings are kept.').format(release['version'])))
                actions.append(button('open_url', _('Open on GitHub'), url=release['page']))
                rows.append(card(release['title'], release['text'], tone='accent' if index == 0 else '',
                                 badge=_('New') if newer else _('Installed') if same else '', date=release['date'],
                                 buttons=actions))
        return {'subtitle': _('The latest versions of the port and what changed in each.'), 'sections': [
            {'title': '', 'rows': rows}]}

    def _load_news(self):
        try:
            self._news = self._L.release_list()
        except (OSError, ValueError, KeyError) as error:
            self._news = str(error) or type(error).__name__
        self._seq += 1

    def _topic_card(self, topic, crash=''):
        L = self._L
        _ = L._
        title, meaning, steps = L.issn_help.TOPICS[topic]
        return card(_(title), _(meaning), tone='warn', steps=[_(step) for step in steps], mono=crash)

    def _help_topics(self, s):
        L = self._L
        try:
            log = L.run_log_path(s).read_text(errors='replace')
        except OSError:
            log = ''
        topics = L.issn_help.check(L.PORT_DIR, s.get('game_dir') or '', s.get('update_dir') or '', log)
        return topics, (L.issn_help.crash_line(log) if 'crash' in topics else '')

    def _page_help(self, s, ini, choice, check, folder, number):
        L = self._L
        _ = L._
        topics, crash = self._help_topics(s)
        found = [self._topic_card(topic, crash if topic == 'crash' else '') for topic in topics] or [
            card(_('No problem found in the installation or the last game log.'),
                 _('Something still wrong? Save a bug report and send it on the Discord.'), tone='ok')]
        about = [note(_('Made by {}').format(L.AUTHOR)),
                 buttons(*[button('open_url', label, url=url) for label, url in L.AUTHOR_LINKS + [('Discord', L.DISCORD_URL)]]),
                 note(_('Thanks to {}.').format(L.CREDITS))]
        qr = ''
        try:
            qr = 'data:image/png;base64,' + base64.b64encode(Path(L.PIX_QR).read_bytes()).decode()
        except OSError:
            pass
        support = [note(_("The port is free and always will be. If it gave you some good moments and you'd like to help "
                          "keep it and the next projects going, or just say thanks, any amount is welcome and means a "
                          "lot. Thank you! ♥")),
                   note(_("Donations only support the work: they don't unlock anything, and the port never includes "
                          "game files.")),
                   {'type': 'cards', 'cards': [
                       card(_('Pix (Brazil)'), _('Random key') + '\n' + L.PIX_KEY, image=qr,
                            buttons=[button('', _('Copy key'), copy=L.PIX_KEY),
                                     button('', _('Copy Pix code'), copy=L.pix_payload())]),
                       card(_('Any country'), _('Card or PayPal, in any currency, no account needed.'),
                            buttons=[button('open_url', label, url=url) for label, url in L.SUPPORT_LINKS])]}]
        return {'subtitle': _('Checks your installation and the last game log, and shows how to fix what it finds.'),
                'sections': [
                    {'title': '', 'rows': [buttons(button('reload', _('Check again')),
                                                   button('bug_report', _('Save a bug report')),
                                                   button('open_path', _('Open the log'), ['log']),
                                                   button('open_url', _('Ask on the Discord'), url=L.DISCORD_URL))]
                     + found},
                    {'title': _('About'), 'rows': about},
                    {'title': _('Support the project'), 'rows': support}]}

    def _optiscaler_state(self):
        L = self._L
        try:
            return L.issn_downloads.optiscaler_state(L.optiscaler_bin())
        except Exception:  # noqa: BLE001 - not on Windows, or the folder cannot be read
            return None

    def _page_graphics(self, s, ini, choice, check, folder, number):
        L = self._L
        _ = L._
        cards = [('', ('Automatic',))] + [(row[0], (row[1],)) for row in L.list_gpus()]
        sections = [
            {'title': _('Memory and shaders'), 'rows': [
                choice('vram_limit', 'app', _('Video memory limit'), L.VRAM_LIMITS,
                       _('How much video memory the game may fill before it frees textures it is not using. Lower it '
                         'if the game closes with "Device lost" or the log says the video memory is nearly full, '
                         'mainly on 4-8 GB cards and laptops.'), True),
                choice('shader_compile', 'app', _('Shader compilation'), L.SHADER_COMPILE,
                       _('The first time a place or effect appears its shaders are compiled. In the background the '
                         'game keeps running and what they draw shows up a moment later; in the frame the game '
                         'waits for them (a stutter). Either way they are kept in the cache for the next time.'), True)]},
            {'title': _('Graphics card'), 'rows': [
                choice('gpu_id', 'app', _('Graphics card'), cards,
                       _('The GPU the game runs on. Choose your dedicated card if the game uses the integrated one.'),
                       True)]}]
        if L.WINDOWS:
            sections.append({'title': _('OptiScaler (optional)'), 'rows': self._optiscaler_rows(s, choice)})
        return {'subtitle': _('Video memory, shaders, the graphics card and OptiScaler.'), 'sections': sections}

    def _optiscaler_rows(self, s, choice):
        L = self._L
        _ = L._
        state = self._optiscaler_state()
        options = L.optiscaler_choices(self._pc)
        current = s.get('optiscaler_output') or 'fsr22'
        if current not in [key for key, _t in options]:
            options = [o for o in L.OPTISCALER_CHOICES if o in options or o[0] == current] if self._pc is None else options
        installed = state is not None and not state['foreign']
        if 'optiscaler' in self._jobs:
            text = self._status
        elif state is None:
            text = _('Not installed.')
        elif state['foreign']:
            text = _('bin\\winmm.dll is another program, not OptiScaler: the launcher leaves it alone.')
        else:
            key = {'0x77': 'F8', '0x2d': 'Insert', '0x24': 'Home', 'auto': 'Insert'}.get(
                (state['menu_key'] or 'auto').lower(), state['menu_key'] or 'Insert')
            text = _('Installed: OptiScaler {} · output {} · its menu in the game: {}.').format(
                state['version'] or '?', L.optiscaler_output_name(state['upscaler']), key)
            if state['by_hand']:
                text += ' ' + _("Unpacked by hand: Remove takes its files out; Download and install replaces it "
                                "with the launcher's, set for the port.")
            if key in ('Insert', 'Home'):
                text += ' ' + _('{} is also a key of the port: install it again from here to move its menu to F8.').format(key)
            if s.get('anti_aliasing') != 'xess':
                text += ' ' + _("⚠ The port's anti-aliasing is not XeSS, so OptiScaler gets nothing: choose XeSS in "
                                'Anti-aliasing above.')
        actions = []
        if not (state and state['foreign']) and 'optiscaler' not in self._jobs:
            actions.append(button('optiscaler_install', _('Update / install again') if installed else _('Download and install'),
                                  primary=not installed, confirm=_(
                                      'OptiScaler is a separate program by the OptiScaler team (GPL-3.0), not part of the port.\n\n'
                                      'The launcher downloads its newest stable release from github.com/optiscaler/OptiScaler '
                                      '(about 55 MB), checks it and puts it in {}, set for the port: its menu on F8, the output you '
                                      "chose. The port's anti-aliasing is set to XeSS, which is what OptiScaler reads.\n\n"
                                      'Download and install it now?').format(L.optiscaler_bin())))
        if installed and 'optiscaler' not in self._jobs:
            actions.append(button('optiscaler_remove', _('Remove'), confirm=_(
                "Remove OptiScaler? Its files leave the bin folder and the port runs as before. The port's "
                'anti-aliasing stays XeSS; change it above if you want.')))
        actions.append(button('open_url', _('Open the project page (source code and license)?').rstrip('?'),
                              url=L.issn_downloads.OPTISCALER_PAGE))
        return [note(_("A separate program by the OptiScaler team that takes the port's XeSS input and turns it "
                       "into another upscaler. Not part of the port: the launcher downloads it from the project's "
                       'GitHub when you ask and can take it out again.')),
                {**choice('optiscaler_output', 'app', _('OptiScaler output'), options,
                          _('What OptiScaler makes of the XeSS input. FSR 2.2 is the one tested with the port; FSR 4 '
                            'goes through DirectX 12 and needs a Radeon RX 7000 or 9000. Written to OptiScaler.ini at '
                            'once; it applies when the game starts.'), True), 'value': current},
                buttons(*actions, hint=text),
                card(_('How to use'), _('Third-party: problems with OptiScaler itself go to its project, not to the port.'),
                     steps=[
                    _("Download and install. The port's anti-aliasing must be XeSS (the launcher sets it): with "
                      'anything else OptiScaler gets nothing.'),
                    _("Choose the output here (it applies when the game starts). The size the game draws at is "
                      "still the port's 'FSR 4 / DLSS quality'."),
                    _("In the game, F8 opens OptiScaler's menu (output, sharpness, debug views). Insert is still "
                      "the port's menu and Home its anti-aliasing switch. Page Up, Page Down and End are OptiScaler's "
                      '(its FPS counter and frame generation).'),
                    _('To go back: Remove. Every file it added leaves the bin folder.')])]

    def _page_files(self, s, ini, choice, check, folder, number):
        L = self._L
        _ = L._
        import xml.etree.ElementTree as ET
        from mods import conflicts, discover
        from patches import ALWAYS_ON, EXCLUSIVE_PREFIX, external_patches
        meta = L.meta_folder(s.get('game_dir'), s.get('update_dir'))
        title_id, profile = L.game_profile_of(meta)
        game_rows = []
        if not profile:
            game_rows.append(note(_('No built-in patches for this game.')))
        else:
            _ids, xml, version = profile[:3]
            metas = [m for m in ET.parse(xml).getroot().iter('Metadata') if m.get('AppVer') == version]
            chosen = s.get('game_patches', {}).get(title_id)
            on = set(chosen) if chosen is not None else {m.get('Name') for m in metas
                                                         if m.get('isEnabled', 'false').lower() == 'true'}
            game_rows.append(note(_('{} · version {} · {}. Resolution and frame rate patches follow Performance.').format(
                title_id, version, xml.name)))
            for item in metas:
                name = item.get('Name')
                if name.startswith(EXCLUSIVE_PREFIX) or name.startswith('Frame Rate Patch') or name in ALWAYS_ON:
                    continue
                author = item.get('Author')
                game_rows.append({'type': 'check', 'key': name, 'store': 'game_patch', 'value': name in on,
                                  'label': name + (f'  ({author})' if author else ''), 'hint': item.get('Note') or '',
                                  'restart': False})
        patch_dir = Path(s.get('patches_dir') or L.DATA_DIR / 'patches')
        found = external_patches(patch_dir, profile[2], profile[1], profile[0]) if profile else []
        picked = L.load_json(L.DATA_DIR / 'patches.json', {})
        patch_rows = [folder('patches_dir', _('Patches folder'),
                             _('shadPS4/GoldHEN XML patch files for this game version. Empty: {}').format(
                                 L.DATA_DIR / 'patches'))]
        if not found:
            patch_rows.append(note(_('No patch files for this game version yet.')))
        for key, _path, item in found:
            on = key in picked.get('enabled', []) or (key not in picked.get('disabled', []) and
                                                       item.get('isEnabled', 'false').lower() == 'true')
            author = item.get('Author')
            patch_rows.append({'type': 'check', 'key': key, 'store': 'patch', 'value': on, 'restart': False, 'hint': '',
                               'label': key + (f'  —  {author}' if author else '')})
        mod_root = Path(s.get('mods_dir') or L.DATA_DIR / 'mods')
        profile_mods = L.load_json(L.DATA_DIR / 'mods.json', {})
        available = discover(mod_root)
        order = [n for n in profile_mods.get('order', []) if n in available]
        order += [n for n in available if n not in order]
        disabled = set(profile_mods.get('disabled', []))
        mod_rows = [check('mods_enabled', 'app', _('Load mods'), _('Loose-file mods, each in its own folder with dvdroot_ps4.')),
                    folder('mods_dir', _('Mods folder'), _('Empty: {}').format(L.DATA_DIR / 'mods'))]
        if not available:
            mod_rows.append(note(_('No mods in {} yet.').format(mod_root)))
        else:
            clashes = conflicts(mod_root, [n for n in order if n not in disabled])
            lines = [_('⚠ {} file(s) replaced by more than one mod (the last one wins):').format(len(clashes))] if clashes else []
            lines += [f'   {path}: {" > ".join(owners)}' for path, owners in clashes[:12]]
            if len(clashes) > 12:
                lines.append(_('   … and {} more').format(len(clashes) - 12))
            mod_rows.append({'type': 'mods', 'items': [{'name': n, 'on': n not in disabled} for n in order],
                             'hint': _('Lower in the list loads later and wins conflicts.') if len(order) > 1 else '',
                             'warning': '\n'.join(lines)})
            presets = sorted(L.load_json(L.MOD_PRESETS, {}))
            mod_rows.append({'type': 'entry', 'id': 'preset', 'label': _('Preset'), 'secret': False, 'hint': '',
                             'suggestions': presets,
                             'buttons': [button('preset_load', _('Load')), button('preset_save', _('Save')),
                                         button('preset_delete', _('Delete'))]})
        return {'subtitle': _("The game's patches, third-party patches and mods."), 'sections': [
            {'title': _('Game patches'), 'rows': game_rows},
            {'title': _('Third-party patches'), 'rows': patch_rows},
            {'title': _('Mods'), 'rows': mod_rows}]}

    def _page_pc(self, s, ini, choice, check, folder, number):
        L = self._L
        H = L.issn_hardware
        _ = L._
        pc = self._pc
        if pc is None:
            rows = [card(_('Your PC'), _('Looking at this PC…'))]
        else:
            unknown = _('not recognised')
            lines = [_('Graphics card: {}').format(pc.gpu_name or unknown),
                     _('Processor: {}').format(pc.cpu_name or unknown),
                     _('Memory: {} GB').format(pc.ram_gb) if pc.ram_gb else _('Memory: {}').format(unknown)]
            verdict = {'great': _('Runs it very well'), 'good': _('Runs it well'), 'minimum': _('Runs it, at lower settings'),
                       'below': _('Below what the game needs'), 'unknown': _('Not in the list yet')}[H.tier(pc)]
            fps = H.expected_fps(pc)
            if fps:
                verdict += ' · ' + _('about {} FPS in the open city at 1080p').format(fps)
            limit = H.limit(pc)
            if limit:
                verdict += ' · ' + (_('the processor sets the limit') if limit == 'cpu' else _('the graphics card sets the limit'))
            extra = []
            if pc.deck:
                extra.append(_('Steam Deck found: "Steam Deck / weak PC" sets the game for it (40-45 FPS).'))
            elif pc.integrated:
                extra.append(_('This is an integrated graphics chip: a graphics card is needed for this game.'))
            if pc.ram_gb and pc.ram_gb < 16:
                extra.append(_('16 GB of memory or more is recommended.'))
            rows = [card(verdict, '\n'.join(lines + extra), tone='ok' if H.tier(pc) in ('great', 'good') else 'warn'),
                    buttons(*[button('goal', _(label), [goal], primary=goal == 'recommended') for goal, label in GOALS],
                            hint=_('Set up the graphics for this PC:') + (
                                ' ' + getattr(self, '_goal_text', '') if getattr(self, '_goal_text', '') else ''))]
        return {'subtitle': _('Your graphics card, processor and memory, and what to expect.'), 'sections': [
            {'title': _('Your PC'), 'rows': rows}]}

    def _page_advanced(self, s, ini, choice, check, folder, number):
        L = self._L
        _ = L._
        try:
            log = L.run_log_path(s).read_text(encoding='utf-8', errors='replace').splitlines()[-400:]
        except OSError:
            log = []
        return {'subtitle': _('Files, diagnostics and the log.'), 'sections': [
            {'title': _('Files'), 'rows': [
                buttons(button('open_path', _('Port folder'), ['data']), button('open_path', 'issn.ini', ['ini']),
                        button('bug_report', _('Save a bug report')),
                        hint=_('A zip on the desktop with the last log and your settings, to send with a bug.')),
                buttons(button('clear_cache', _('Clear shader cache'),
                               confirm=_('Clear the shader cache? The next start stutters for a few minutes while it is '
                                         'rebuilt.')),
                        hint=_('If the game only shows a black screen, this usually helps.'))]},
            {'title': _('Diagnostics (for development)'), 'rows': [
                check('frame_stats', 'app', _('Frame statistics in the log (every 5 s)')),
                check('seed_any_profile', 'app', _('Prepare the drawing shaders on any graphics card (test)'),
                      _('NVIDIA and older AMD cards: the first start also prepares the shaders that otherwise compile '
                        'while playing. A test: send the log of the first start and say if anything looks wrong.'), True),
                check('vk_validation', 'app', _('Vulkan validation layers (needs the Vulkan SDK; much slower)'))]},
            {'title': _('Log'), 'rows': [{'type': 'log', 'text': '\n'.join(log),
                                          'buttons': [button('', _('Copy'), copy='\n'.join(log)),
                                                      button('open_path', _('Open the log'), ['log'])]}]}]}

    def _page_customize(self, s, ini, choice, check, folder, number):
        L = self._L
        _ = L._
        sections = [
            {'title': _('Theme'), 'rows': [{'type': 'themes'}]},
            {'title': _('Look'), 'rows': [
                {**choice('launcher_fx', 'app', _('Effects'), L.FX_LEVELS,
                          _('The animation on the Play page. It stops by itself while the game runs.')),
                 'type': 'segments'}]},
            {'title': _('Launcher'), 'rows': [
                choice('ui_language', 'app', _('Launcher language'), L.UI_LANGUAGES,
                       _('Applies when the launcher opens again.')),
                check('close_on_play', 'app', _('Close the launcher when the game starts'))]}]
        if L.WINDOWS and L.UPDATES:
            sections.append({'title': _('Updates'), 'rows': [
                check('check_updates', 'app', _('Check for updates when the launcher opens')),
                buttons(button('check_update', _('Check for updates')), hint=f'v{L.VERSION}')]})
        sections.append({'title': _('Shortcuts'), 'rows': [
            buttons(button('shortcut', _('Desktop shortcut')), button('add_to_steam', _('Add to Steam')),
                    hint=_('The desktop shortcut opens this launcher; the Steam entry starts the game straight away with '
                           'these settings.'))]})
        return {'subtitle': _("The launcher's look, its language, what it does while you play, and shortcuts."),
                'sections': sections}

    # ---- actions --------------------------------------------------------------------------------
    def choose_folder(self, key, current=''):
        import webview
        chosen = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=current or str(self._L.PORT_DIR))
        return str(Path(chosen[0])) if chosen else ''

    def action(self, name, args=None):
        """A button of a page. Every answer is plain data the page follows (see the module's text)."""
        args = list(args or [])
        method = getattr(self, f'_do_{name}', None)
        if method is None:
            return {}
        try:
            return method(*args) or {}
        except (OSError, ValueError) as error:
            return {'alert': str(error)}

    def _do_discord(self):
        self._L.open_url(self._L.DISCORD_URL)

    def _do_open_url(self, url=''):
        if url:
            self._L.open_url(url)

    def _do_reload(self):
        return {'reload': True}

    def _do_goal(self, goal):
        result = self.apply_goal(goal)
        return {'toast': result.get('message'), 'reload': True}

    def _do_open_path(self, which):
        L = self._L
        s = self._settings()
        path = {'data': L.DATA_DIR, 'ini': L.ini_path(), 'log': L.run_log_path(s),
                'xess': L.xess_folder() if L.WINDOWS else None,
                'dlss': L.dlss_folder() if L.WINDOWS else None}.get(which)
        if path is None:
            return {}
        path = Path(path)
        if path.suffix not in ('.ini', '.log') and not path.exists():
            path.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            return {'toast': L._('No game log yet: press Play first.') if which == 'log' else str(path)}
        if L.WINDOWS:
            os.startfile(str(path))  # noqa: S606 - the player's own folder or file
        else:
            subprocess.Popen(['xdg-open', str(path)], env=L.host_environment(), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {}

    def _do_bug_report(self):
        """The last run's log, issn.ini and the launcher settings in a zip on the desktop (no game files)."""
        L = self._L
        s = self._settings()
        desktop = Path.home() / 'Desktop'
        target = (desktop if desktop.is_dir() else L.DATA_DIR) / time.strftime('issn-bug-report-%Y%m%d-%H%M%S.zip')
        import platform
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
            for path in (L.run_log_path(s), L.ini_path(), L.CONFIG_FILE, L.DATA_DIR / 'out' / 'patch_status.json'):
                if Path(path).is_file():
                    z.write(path, Path(path).name)
            z.writestr('version.txt', f'launcher v{L.VERSION}\n{platform.platform()}\n{platform.processor()}\n')
        self._status = L._('Bug report saved: {}').format(target)
        return {'toast': self._status}

    def _do_clear_cache(self):
        L = self._L
        if self._process is not None:
            return {'alert': L._('Close the game first.')}
        shutil.rmtree(self._user_dir() / 'cache', ignore_errors=True)
        return {'toast': L._('Shader cache cleared. The next start stutters for a few minutes while it is rebuilt.'),
                'reload': True}

    def _do_restore_backup(self, name=''):
        L = self._L
        _ = L._
        if not name:
            return {}
        if self._process is not None:
            return {'alert': _('Close the game before restoring saves.')}
        return {'confirm': {'text': _('Replace the current saves with the backup {}? The current saves are backed up '
                                      'first.').format(name), 'action': 'restore_backup_now', 'args': [name]}}

    def _do_restore_backup_now(self, name):
        L = self._L
        _ = L._
        safety = L.save_backup.restore(self._user_dir(), name)
        return {'alert': _('Saves restored from {}.').format(name) +
                (_(' Previous saves: {}.').format(safety.name) if safety else ''), 'reload': True}

    def _do_import_saves(self, source='', confirmed=False):
        """Saves from another port: its data folder when found (asked first), else a folder the player picks."""
        L = self._L
        _ = L._
        if self._process is not None:
            return {'alert': _('Close the game before importing saves.')}
        if not source:
            for found in L.save_backup.other_port_dirs():
                return {'confirm': {'text': _('Saves of another port were found in {}. Import them?').format(found),
                                    'action': 'import_saves', 'args': [str(found), True],
                                    'no': {'action': 'import_saves_pick'}}}
            return self._do_import_saves_pick()
        source = Path(source)
        if not confirmed:
            if not L.save_backup.import_source(source):
                return {'alert': _("No inFAMOUS Second Son saves in {}. Choose the other port's folder, or its user or "
                                   'savedata folder.').format(source)}
            return {'confirm': {'text': _('Replace the current saves with the ones in {}? The current saves are backed '
                                          'up first.').format(source), 'action': 'import_saves', 'args': [str(source), True]}}
        s = self._settings()
        try:  # the game's own title ID (region); saves of another region are renamed to it
            title = str(L.game_sfo(Path(s.get('game_dir') or '.')).get('TITLE_ID') or L.save_backup.TITLE)
        except (OSError, ValueError):
            title = L.save_backup.TITLE
        safety, slots = L.save_backup.import_saves(self._user_dir(s), source, title=title)
        return {'alert': _('{} saves imported from {}.').format(slots, source) +
                (_(' Previous saves: {}.').format(safety.name) if safety else ''), 'reload': True}

    def _do_import_saves_pick(self):
        return {'pick': {'action': 'import_saves', 'args': []}}

    def _do_save_trophy_key(self, text=''):
        L = self._L
        _ = L._
        T = L.trophies
        key = T.parse_key(text)
        if key is None:
            return {'alert': _('A trophy key is 32 hexadecimal digits (0-9, A-F).')}
        s = self._settings()
        folders = self._trophy_folders(s)
        trp = T.trophy_file(*folders)
        if trp:  # a key that does not open this game's file is not kept
            try:
                T.read_texts(trp, T.npbind_file(*folders), key, '1')
            except (OSError, ValueError, KeyError, T.ElementTree.ParseError) as error:
                return {'alert': _('This key does not open the trophy names: {}').format(error)}
        T.save_key(self._user_dir(s), key)
        return {'reload': True}

    def _do_remove_trophy_key(self):
        path = self._user_dir() / 'trophy-key.txt'
        if path.exists():
            path.unlink()
        return {'reload': True}

    def _do_reset_controls(self):
        s = self._settings()
        self._write_app({'controls': {'key': {}, 'pad': {}}})
        self._write_ini({'key_map': ''})
        del s
        return {'reload': True}

    def _do_reload_news(self):
        self._news = None
        return {'reload': True}

    def _do_check_update(self):
        self._news = None
        return {'page': 'news'}

    def _do_install_update(self, version, url, page):
        """The newest release over this installation: downloaded and unpacked here, then its own
        launcher copies itself in once this one has closed (classic install_update)."""
        L = self._L
        _ = L._
        if self._process is not None:
            return {'alert': _('Close the game before updating.')}
        if not L.FROZEN or not url:  # a source tree updates with git
            L.open_url(page)
            return {}
        if 'update' in self._jobs:
            return {}

        def work():
            shutil.rmtree(L.UPDATE_DIR, ignore_errors=True)
            L.UPDATE_DIR.mkdir(parents=True, exist_ok=True)
            archive = L.UPDATE_DIR / 'update.zip'
            import urllib.request
            request = urllib.request.Request(url, headers={'User-Agent': 'issn-launcher'})
            with urllib.request.urlopen(request, timeout=60) as response, open(archive, 'wb') as out:
                total, done, shown = int(response.headers.get('Content-Length') or 0), 0, -1
                while chunk := response.read(1 << 20):
                    out.write(chunk)
                    done += len(chunk)
                    percent = done * 100 // total if total else 0
                    if percent != shown:
                        shown = percent
                        self._status = _('Downloading version {}… {}%').format(version, percent)
            with zipfile.ZipFile(archive) as package:
                package.extractall(L.UPDATE_DIR / 'new')
            archive.unlink()
            new = next((p.parent for p in (L.UPDATE_DIR / 'new').rglob('inFAMOUS Second Son.exe')), None)
            if not new:
                raise OSError('inFAMOUS Second Son.exe is missing from the download')
            subprocess.Popen([str(new / 'inFAMOUS Second Son.exe'), '--install-update', str(L.PORT_DIR), str(os.getpid())],
                             cwd=str(new), stdin=subprocess.DEVNULL, creationflags=L.NO_WINDOW)
            if self._window is not None:
                self._window.destroy()
        self._job('update', work, _('Update failed: {}'))
        return {}

    def _do_download_upscaler(self, key):
        self._download_upscaler(key)
        return {'reload': True}

    def _download_upscaler(self, key, then=None):
        L = self._L
        _ = L._
        package = L.UPSCALER_PACKAGES[key]

        def work():
            def progress(done, total):
                self._status = _('Downloading {} files… {}%').format(package.name, done * 100 // total if total else 0)
            L.issn_downloads.fetch(package, L.upscaler_folder(key), progress)
            self._status = _('{} files: ready').format(package.name)
        self._status = _('Downloading {} files…').format(package.name)
        self._job(f'upscaler:{key}', work, _('{} download failed: {}').format(package.name, '{}'), then)

    def _job(self, name, work, failed_text, then=None):
        """Long work in a thread: one of each at a time; its progress is the status line and the
        page rebuilds itself when it ends (status()['seq'])."""
        if name in self._jobs:
            return

        def run():
            error = None
            try:
                work()
            except (OSError, ValueError, zipfile.BadZipFile) as failure:
                error = failure
                self._status = failed_text.format(failure)
            finally:
                self._jobs.discard(name)
                self._seq += 1
            if then:
                then(error)
        self._jobs.add(name)
        self._seq += 1
        threading.Thread(target=run, daemon=True).start()

    def _do_optiscaler_install(self):
        """The newest stable OptiScaler from the project's GitHub, set for the port; then the port's
        anti-aliasing goes to XeSS (and DLSS's files come when that is the output)."""
        L = self._L
        _ = L._
        if self._process is not None:
            return {'alert': _('Close the game first: it is using the OptiScaler files.')}
        bin_dir = L.optiscaler_bin()
        output = self._settings().get('optiscaler_output') or 'fsr22'
        dlss_dll = L.dlss_folder() / 'nvngx_dlss.dll'

        def progress(what):
            def report(done, total):
                self._status = what.format(done * 100 // total if total else 0)
            return report

        def work():
            archive = None
            try:
                try:
                    release = L.issn_downloads.optiscaler_release()
                except (OSError, ValueError):
                    release = L.issn_downloads.OPTISCALER_KNOWN  # GitHub's API unreachable: the tested one
                name = re.sub(r'[^A-Za-z0-9._-]', '_', release.url.rsplit('/', 1)[-1]) or 'optiscaler.7z'
                archive = Path(tempfile.gettempdir()) / 'issn-optiscaler' / name
                L.issn_downloads.download(release, archive, progress(_('Downloading OptiScaler {}… {{}}%').format(release.tag)))
                L.issn_downloads.install(archive, bin_dir, output, release.tag,
                                         progress(_('Installing OptiScaler… {}%')), dlss_dll)
            finally:
                if archive is not None:
                    try:
                        archive.unlink(missing_ok=True)
                    except OSError:
                        pass
            self._write_app({'anti_aliasing': 'xess'})
            self._status = _('OptiScaler {} installed.').format(release.tag) + ' ' + _(
                "The port's anti-aliasing is now XeSS.") + ' ' + _("In the game, F8 opens OptiScaler's menu.")

        def then(error):
            if error is None:
                if L.upscaler_missing('xess'):
                    self._download_upscaler('xess')
                if output == 'dlss' and L.upscaler_missing('dlss'):
                    self._download_upscaler('dlss', then=lambda e: e or self._optiscaler_output('dlss'))
        self._status = _('Looking for the newest OptiScaler…')
        self._job('optiscaler', work, _('OptiScaler was not installed: {}'), then)
        return {'reload': True}

    def _do_optiscaler_remove(self):
        L = self._L
        _ = L._
        if self._process is not None:
            return {'alert': _('Close the game first: it is using the OptiScaler files.')}
        removed = L.issn_downloads.remove(L.optiscaler_bin())
        return {'toast': _('OptiScaler removed ({} files).').format(len(removed)), 'reload': True}

    def _optiscaler_output(self, output):
        """A new output into the installed OptiScaler.ini at once (DLSS: NVIDIA's files first)."""
        L = self._L
        _ = L._
        state = self._optiscaler_state()
        if not state or state['foreign'] or 'optiscaler' in self._jobs:
            return {}
        if output == 'dlss' and L.upscaler_missing('dlss'):
            self._download_upscaler('dlss', then=lambda e: e or self._optiscaler_output('dlss'))
            return {'reload': True}
        value = L.issn_downloads.set_output(L.optiscaler_bin(), output, L.dlss_folder() / 'nvngx_dlss.dll')
        self._status = _('OptiScaler output: {} (the next time the game starts).').format(L.optiscaler_output_name(value))
        self._seq += 1
        return {'toast': self._status, 'reload': True}

    def _do_set_aa_xess(self):
        self._write_app({'anti_aliasing': 'xess'})
        return {'play': True}

    def _do_decline_optiscaler(self):
        return {'play': True}

    def _do_adopt_system_files(self, source, folder):
        L = self._L
        _ = L._

        def work():
            L.adopt_system_files(Path(source), folder)
            self._status = _('The system files are copied: press Play again.')
        self._status = _('Copying the system files…')
        self._job('system_files', work, _('Could not copy the files: {}'))
        return {}

    def _do_decline_system_files(self, folder):
        self._declined.add(str(folder))
        return {'play': True}

    def _do_shortcut(self):
        """inFAMOUS Second Son.lnk on the desktop (classic shortcut())."""
        L = self._L
        _ = L._
        if not L.WINDOWS:
            launcher = os.environ.get('APPIMAGE') or (sys.executable if L.FROZEN else '')
            command = f'"{launcher}"' if launcher else f'python3 "{Path(L.__file__).resolve()}"'
            target = Path.home() / '.local' / 'share' / 'applications' / 'issn.desktop'
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(L.desktop_entry(command, L.PORT_DIR / 'launcher' / 'issn.png'), encoding='utf-8')
            target.chmod(0o755)
            return {'alert': _('Shortcut created:') + '\n' + str(target)}
        if L.FROZEN:
            target, arguments, icon = sys.executable, '', f'{sys.executable},0'
        else:
            pythonw = Path(sys.executable).with_name('pythonw.exe')
            target = str(pythonw if pythonw.exists() else sys.executable)
            arguments, icon = Path(L.__file__).resolve(), L.PORT_DIR / 'launcher' / 'issn.ico'
        script = ('$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath("Desktop")'
                  '+"\\inFAMOUS Second Son.lnk");'
                  f'$s.TargetPath="{target}";$s.WorkingDirectory="{L.PORT_DIR}";$s.IconLocation="{icon}";'
                  + (f"$s.Arguments='\"{arguments}\"';" if arguments else '') + '$s.Save()')
        result = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True,
                                creationflags=L.NO_WINDOW)
        if result.returncode == 0:
            return {'toast': _('Shortcut created on the desktop.')}
        return {'alert': result.stderr.decode(errors='replace')[:400]}

    def _do_add_to_steam(self, close_steam=False):
        """A non-Steam shortcut with the game's pictures (scripts/steam_shortcut.py); an open Steam is
        closed first when the player agrees, and opened again afterwards."""
        L = self._L
        _ = L._
        S = L.steam_shortcut
        game = self._settings().get('game_dir')
        if not L.game_info(game):
            return {'alert': _('Choose the game folder with eboot.bin (CUSA00004).'), 'page': 'game'}
        if S.steam_running() and not close_steam:
            return {'confirm': {'text': _('Steam is open. Close Steam, add the game and open Steam again?'),
                                'action': 'add_to_steam', 'args': [True]}}
        command, icon = L.steam_launch_command(), L.steam_icon(game)

        def work():
            try:
                if close_steam and not S.close_steam():
                    raise S.SteamError('still_running')
                outcome = S.add_to_steam(game, command, icon)
            except S.SteamError as error:
                text = {'running': _('Steam is open. Close it completely (Steam > Exit) and click again: Steam '
                                     'rewrites its shortcuts when it closes.'),
                        'still_running': _('Steam did not close. Close it (Steam > Exit) and click again.'),
                        'no_steam': _('Steam was not found for this user.'),
                        'no_art': _('The game folder has no sce_sys/pic1.png for the pictures.')}
                raise OSError(text.get(error.code, str(error))) from error
            finally:
                if close_steam:
                    S.start_steam()
            self._status = (_('The shortcut "{}" was updated in Steam (accounts: {}), with its icon, capsules, background '
                              'and logo. Open Steam to see it in the library.') if outcome['updated'] else
                            _('The shortcut "{}" was added to Steam (accounts: {}), with its icon, capsules, background '
                              'and logo. Open Steam to see it in the library.')).format(S.APP_NAME, outcome['accounts'])
        self._status = _('Adding the game to Steam…')
        self._job('steam', work, '{}')
        return {}

    def _do_preset_save(self, name=''):
        L = self._L
        name = (name or '').strip()
        if not name:
            return {'alert': L._('Type a name for the preset first.')}
        mods = L.load_json(L.DATA_DIR / 'mods.json', {})
        presets = L.load_json(L.MOD_PRESETS, {})
        presets[name] = {'order': mods.get('order', []), 'disabled': mods.get('disabled', [])}
        L.MOD_PRESETS.write_text(json.dumps(presets, indent=2), encoding='utf-8')
        return {'reload': True}

    def _do_preset_load(self, name=''):
        L = self._L
        from mods import apply_preset, discover
        preset = L.load_json(L.MOD_PRESETS, {}).get((name or '').strip())
        if not isinstance(preset, dict):
            return {}
        root = Path(self._settings().get('mods_dir') or L.DATA_DIR / 'mods')
        order, disabled = apply_preset(preset, list(discover(root)))
        L.atomic_write(L.DATA_DIR / 'mods.json', json.dumps({'order': order, 'disabled': sorted(disabled)}, indent=2))
        return {'reload': True}

    def _do_preset_delete(self, name=''):
        L = self._L
        presets = L.load_json(L.MOD_PRESETS, {})
        if presets.pop((name or '').strip(), None) is not None:
            L.MOD_PRESETS.write_text(json.dumps(presets, indent=2), encoding='utf-8')
        return {'reload': True}

    def _mods_profile(self):
        L = self._L
        from mods import discover
        root = Path(self._settings().get('mods_dir') or L.DATA_DIR / 'mods')
        available = discover(root)
        profile = L.load_json(L.DATA_DIR / 'mods.json', {})
        order = [n for n in profile.get('order', []) if n in available]
        order += [n for n in available if n not in order]
        return order, set(profile.get('disabled', []))

    def _do_mod_toggle(self, name, on):
        order, disabled = self._mods_profile()
        disabled.discard(name) if on else disabled.add(name)
        self._L.atomic_write(self._L.DATA_DIR / 'mods.json',
                             json.dumps({'order': order, 'disabled': sorted(disabled)}, indent=2))
        return {'reload': True}

    def _do_mod_move(self, name, step):
        order, disabled = self._mods_profile()
        if name in order:
            index = order.index(name)
            other = index + int(step)
            if 0 <= other < len(order):
                order[index], order[other] = order[other], order[index]
        self._L.atomic_write(self._L.DATA_DIR / 'mods.json',
                             json.dumps({'order': order, 'disabled': sorted(disabled)}, indent=2))
        return {'reload': True}

    def set_choice(self, store, key, on):
        """A check of a list: a game patch (settings.json game_patches) or a third-party patch
        (patches.json)."""
        L = self._L
        if store == 'patch':
            chosen = L.load_json(L.DATA_DIR / 'patches.json', {})
            enabled, disabled = set(chosen.get('enabled', [])), set(chosen.get('disabled', []))
            (enabled if on else disabled).add(key)
            (disabled if on else enabled).discard(key)
            L.atomic_write(L.DATA_DIR / 'patches.json',
                           json.dumps({'enabled': sorted(enabled), 'disabled': sorted(disabled)}, indent=2))
        elif store == 'game_patch':
            import xml.etree.ElementTree as ET
            from patches import ALWAYS_ON, EXCLUSIVE_PREFIX
            s = self._settings()
            title_id, profile = L.game_profile_of(L.meta_folder(s.get('game_dir'), s.get('update_dir')))
            if not profile:
                return {}
            _ids, xml, version = profile[:3]
            metas = [m for m in ET.parse(xml).getroot().iter('Metadata') if m.get('AppVer') == version]
            chosen = s.get('game_patches', {}).get(title_id)
            current = set(chosen) if chosen is not None else {m.get('Name') for m in metas
                                                              if m.get('isEnabled', 'false').lower() == 'true'}
            current.add(key) if on else current.discard(key)
            shown = [m.get('Name') for m in metas if not (m.get('Name').startswith(EXCLUSIVE_PREFIX) or
                                                          m.get('Name').startswith('Frame Rate Patch') or
                                                          m.get('Name') in ALWAYS_ON)]
            self._write_app({'game_patches': {**s.get('game_patches', {}), title_id: [n for n in shown if n in current]}})
        return {}

    def status(self):
        goto, self._goto = self._goto, ''
        return {'text': self._status, 'running': self._process is not None, 'seq': self._seq, 'goto': goto,
                'busy': bool(self._jobs)}

    def play(self):
        """PLAY: the classic launcher's checks in its order, each answered on the page (a question,
        a message, a download first), then the game in its own window."""
        L = self._L
        _ = L._
        s = self._settings()
        if self._process is not None or 'system_files' in self._jobs:
            return {}
        game_dir = s.get('game_dir') or ''
        if not L.game_info(game_dir):
            return {'message': _('Choose the game folder with eboot.bin (CUSA00004).'), 'page': 'game'}
        update_dir = s.get('update_dir') or ''
        folders = [(game_dir, False)]
        if update_dir and not L.same_folder(update_dir, game_dir) and (Path(update_dir) / 'eboot.bin').is_file():
            folders.append((update_dir, True))
        for folder, is_update in folders:  # a PKG extraction: its system files beside the game
            if str(folder) in self._declined:
                continue
            reference = (L.game_sfo(game_dir) or None) if is_update else None
            source = L.system_files_beside(folder, reference, game_dir if is_update else None)
            if source:
                return {'confirm': {'text': _('The files in {} look like a PKG extraction: the system files (param.sfo, '
                                              'icon0.png, pic1.png and the rest) are in\n\n{}\n\nCopy all of them into '
                                              'its sce_sys folder now? Files already there are kept.').format(folder, source),
                                    'action': 'adopt_system_files', 'args': [str(source), str(folder)],
                                    'no': {'action': 'decline_system_files', 'args': [str(folder)]}}}
        if L.WINDOWS and s.get('anti_aliasing') != 'xess' and not self._optiscaler_asked:
            state = self._optiscaler_state()
            if state and not state['foreign']:
                self._optiscaler_asked = True
                return {'confirm': {'text': _("OptiScaler is installed, but the port's anti-aliasing is not XeSS, so "
                                              'OptiScaler gets nothing and the game runs without it.\n\nSwitch the '
                                              'anti-aliasing to XeSS now?'),
                                    'action': 'set_aa_xess', 'no': {'action': 'decline_optiscaler'}}}
        upscaler = s.get('anti_aliasing')
        if L.WINDOWS and upscaler in L.UPSCALER_PACKAGES and L.upscaler_missing(upscaler) \
                and upscaler not in self._upscaler_tried:
            # XeSS / DLSS without their files: fetched first, then the game starts (with the game's
            # own anti-aliasing if the download failed: the GPU library falls back).
            self._upscaler_tried.add(upscaler)
            self._download_upscaler(upscaler, then=lambda _e: self._start_after_download())
            return {}
        missing = L.missing_game_files(game_dir)
        if missing:
            return {'alert': _('The game dump is incomplete. Missing or empty files:\n\n{}\n\nDump the game again, or '
                               'copy these files from a complete dump.').format('\n'.join(L.describe_missing(missing))),
                    'page': 'game'}
        if update_dir and L.same_folder(update_dir, game_dir):
            return {'alert': _('Choose two separate folders for the game and its update, or leave the update folder '
                               'empty.'), 'page': 'game'}
        missing = L.missing_game_files(update_dir, L.REQUIRED_UPDATE_FILES) if update_dir else []
        if missing:
            return {'alert': _('The update folder {} is incomplete. Missing or empty files:\n\n{}\n\nChoose the folder of '
                               'the update (eboot.bin, sce_sys), or leave it empty.').format(
                                   update_dir, '\n'.join(L.describe_missing(missing))), 'page': 'game'}
        self._status = _('Preparing the game; it opens in its own window…')
        threading.Thread(target=self._run, args=(s,), daemon=True).start()
        return {}

    def _start_after_download(self):
        result = self.play()
        if result.get('alert') or result.get('message'):
            self._status = (result.get('alert') or result.get('message')).split('\n')[0]
            self._goto = result.get('page', '')
        self._seq += 1

    def _run(self, settings):
        L = self._L
        _ = L._
        self._process = True
        code = None
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
        if code not in (0, None):  # the game ended with an error: Help reads its log
            topics, _crash = self._help_topics(self._settings())
            if topics:
                self._status = _('The game ended with an error: Help shows what the log says.')
                self._goto = 'help'
        self._seq += 1

    def classic(self):
        """Opens the classic Tk launcher (and keeps it as the choice) and closes this window."""
        L = self._L
        self._write_app({'new_launcher': False})
        command = [sys.executable, '--classic'] if L.FROZEN else [sys.executable, str(Path(L.__file__).resolve()), '--classic']
        subprocess.Popen(command, cwd=L.PORT_DIR, creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0))
        if self._window is not None:
            self._window.destroy()


def web_stamp(web):
    """A short stamp that changes whenever a file of the page changes (size or time)."""
    parts = []
    for name in ('index.html', 'app.js', 'app.css'):
        try:
            info = (web / name).stat()
            parts.append(f'{info.st_size}-{int(info.st_mtime)}')
        except OSError:
            parts.append('0')
    return '-'.join(parts)


def clear_stale_cache(storage, stamp):
    """WebView2 keeps app.js and app.css in its own cache (under %APPDATA%), and after an update it kept
    showing the old page with the new Python (issn 11-06, João: empty rows, blank pages). pywebview serves the
    page from 127.0.0.1:42001 (the same address every time) and its no-cache header is lost (bottle's
    static_file sends only Last-Modified), so the old files counted as fresh. When the page's files change, the
    cache folders go; the settings it stores (Local Storage) stay. The ?v= stamp on the address does the rest."""
    marker = storage / 'page-stamp.txt'
    try:
        if marker.read_text(encoding='utf-8').strip() == stamp:
            return False
    except OSError:
        pass
    for profile in storage.glob('**/EBWebView/*') if storage.exists() else []:
        for name in ('Cache', 'Code Cache', 'GPUCache', 'Service Worker'):
            shutil.rmtree(profile / name, ignore_errors=True)
    try:
        storage.mkdir(parents=True, exist_ok=True)
        marker.write_text(stamp, encoding='utf-8')
    except OSError:
        pass
    return True


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
        stamp = web_stamp(web)
        clear_stale_cache(L.CONFIG_DIR / 'webview', stamp)
        window = webview.create_window(f'{L.APP_NAME} (PC)', url=str(web / 'index.html') + '?v=' + stamp, js_api=api,
                                       width=1280, height=780, min_size=(1100, 680), background_color=L.BG)
        api._window = window
        webview.start(gui='edgechromium' if L.WINDOWS else None, private_mode=False,
                      storage_path=str(L.CONFIG_DIR / 'webview'))
    except Exception as error:  # noqa: BLE001 - WebView2 missing or broken: the classic window opens
        print(f'New launcher unavailable: {error}', file=sys.stderr)
        return False
    return True
