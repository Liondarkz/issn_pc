# SPDX-License-Identifier: GPL-2.0-or-later
"""The launcher's themes, one for each of Delsin's powers (issn 11-05, João's request): the colours
of the window and the effect drawn over the Play page's banner (issn_launcher_win.BannerFx).

Smoke is the launcher's look until now (dark concrete, smoke red, neon cyan), so nothing changes
for a player who never opens Customize. Each theme lists the colours the launcher's module keeps
as constants (BG, PANEL, ...) and the ones issn_theme draws its widgets with."""

DEFAULT = 'smoke'
ORDER = ('smoke', 'neon', 'video', 'concrete')

# Names and one line for the Customize page (English; issn_lang translates).
NAMES = {
    'smoke': ('Smoke', 'Red and embers, smoke rising'),
    'neon': ('Neon', 'Pink and cyan, light running'),
    'video': ('Video', 'Blue, glitches and TV lines'),
    'concrete': ('Concrete', 'Grey and orange, cracks and dust'),
}

_BASE = {'text': '#f2f2f4', 'muted': '#8c90a0'}

THEMES = {
    # The launcher's colours until 0.3.7 (issn_launcher_win before themes).
    'smoke': {**_BASE, 'bg': '#09090c', 'panel': '#101116', 'card': '#181a21', 'line': '#262935',
              'field': '#14161d', 'card_hover': '#1d2029', 'hover': '#242836', 'border': '#262935',
              'border_strong': '#323645', 'border_hover': '#454b60',
              'accent': '#e2263b', 'accent_hi': '#ff4359', 'accent_dark': '#a8172a',
              'neon': '#24d3f2', 'pink': '#ff3ea5', 'ember': '#ff8a3d',
              'nav_active': '#16181f', 'nav_hover': '#111319'},
    'neon': {**_BASE, 'bg': '#0b0811', 'panel': '#100c17', 'card': '#1a1424', 'line': '#2a2236',
             'field': '#150f1e', 'card_hover': '#21192d', 'hover': '#2a2138', 'border': '#2a2236',
             'border_strong': '#3a2f4a', 'border_hover': '#54446a',
             'accent': '#e8318f', 'accent_hi': '#ff5aac', 'accent_dark': '#a51d63',
             'neon': '#24d3f2', 'pink': '#ff3ea5', 'ember': '#9d6bff',
             'nav_active': '#1a1324', 'nav_hover': '#140f1c'},
    'video': {**_BASE, 'bg': '#060a12', 'panel': '#0b111c', 'card': '#111b2b', 'line': '#1c2b44',
              'field': '#0e1624', 'card_hover': '#152238', 'hover': '#1b2b45', 'border': '#1c2b44',
              'border_strong': '#27395a', 'border_hover': '#3a5583',
              'accent': '#2f7cf0', 'accent_hi': '#5b9bff', 'accent_dark': '#1d55b0',
              'neon': '#7cf0ff', 'pink': '#ff3ea5', 'ember': '#7cf0ff',
              'nav_active': '#111c2e', 'nav_hover': '#0d1524'},
    'concrete': {**_BASE, 'bg': '#121211', 'panel': '#181816', 'card': '#22211e', 'line': '#34322e',
                 'field': '#1c1b19', 'card_hover': '#2a2925', 'hover': '#33312c', 'border': '#34322e',
                 'border_strong': '#45423c', 'border_hover': '#5d5850',
                 'accent': '#e8761a', 'accent_hi': '#ff9a3d', 'accent_dark': '#a8520f',
                 'neon': '#e8dccb', 'pink': '#ff9a3d', 'ember': '#d8cdb9',
                 'nav_active': '#24231f', 'nav_hover': '#1c1b18'},
}


def colors(name):
    """The colours of theme `name` (an unknown name: the default's)."""
    return THEMES.get(name) or THEMES[DEFAULT]


def known(name):
    return name if name in THEMES else DEFAULT
