# SPDX-License-Identifier: GPL-2.0-or-later
"""The launcher's widgets drawn by Pillow: rounded buttons and fields, switches, a chevron on the
drop-downs, a slim scroll bar and the PLAY button, instead of ttk clam's square boxes.

The technique is the Bloodborne port's launcher theme (Supermedo's bloodborne_pc,
launcher/bbport_theme.py, GPL-2.0-or-later): each image is drawn at four times its size and
scaled down so its corners stay smooth at any display scale, then stretched as a nine-slice ttk
element. ttk fills an image's transparent pixels with nothing, so each image is drawn over the
colour of the surface it sits on, and every style used on a card has a Card. twin.

The colours are the launcher's own (Seattle at night: wet concrete, Delsin's smoke red, neon).
"""
from PIL import Image, ImageDraw, ImageTk

SS = 4  # supersampling of the drawn images


def rgba(color, alpha=255):
    color = color.lstrip('#')
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,)


def mix(a, b, f):
    a, b = rgba(a), rgba(b)
    return '#' + ''.join(f'{round(x + (y - x) * f):02x}' for x, y in zip(a[:3], b[:3]))


def rounded(size, radius, fill=None, outline=None, width=1.0):
    """A rounded rectangle of `size` on a transparent image, drawn supersampled."""
    w, h = size
    image = Image.new('RGBA', (w * SS, h * SS), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle(
        (0, 0, w * SS - 1, h * SS - 1), radius * SS, fill=fill and rgba(fill), outline=outline and rgba(outline),
        width=max(1, round(width * SS)) if outline else 0)
    return image.resize(size, Image.LANCZOS)


def solid(image, bg):
    """`image` over the colour `bg`, without transparency."""
    base = Image.new('RGBA', image.size, rgba(bg))
    base.alpha_composite(image.convert('RGBA'))
    return base.convert('RGB')


def stretch(image, size, border):
    """A nine-slice image (corners of `border` px) enlarged to `size`: the corners stay, the edges
    and the middle are repeated (they are flat). ttk would tile a small middle; a big one is drawn
    once."""
    w, h = image.size
    W, H = max(size[0], w), max(size[1], h)
    bl, bt, br, bb = (border,) * 4 if isinstance(border, int) else border
    out = Image.new(image.mode, (W, H))
    xs = [(0, bl, 0, bl), (bl, w - br, bl, W - br), (w - br, w, W - br, W)]
    ys = [(0, bt, 0, bt), (bt, h - bb, bt, H - bb), (h - bb, h, H - bb, H)]
    for sx0, sx1, dx0, dx1 in xs:
        for sy0, sy1, dy0, dy1 in ys:
            if sx1 > sx0 and sy1 > sy0 and dx1 > dx0 and dy1 > dy0:
                out.paste(image.crop((sx0, sy0, sx1, sy1)).resize((dx1 - dx0, dy1 - dy0), Image.NEAREST), (dx0, dy0))
    return out


def vertical_gradient(size, top, bottom):
    w, h = size
    strip = Image.new('RGBA', (1, h))
    for y in range(h):
        strip.putpixel((0, y), rgba(mix(top, bottom, y / max(1, h - 1))))
    return strip.resize(size)


class Theme:
    """Draws the images (kept alive here: Tk keeps no reference) and fills a ttk.Style."""

    def __init__(self, root, dpi, colors):
        self.root, self.dpi, self.c = root, dpi, colors
        self.keep = []

    def px(self, value):
        return max(1, round(value * self.dpi))

    def photo(self, image):
        photo = ImageTk.PhotoImage(image, master=self.root)
        self.keep.append(photo)
        return photo

    def box(self, w, h, r, fill, outline, bg, big, border, width=1.0):
        image = solid(rounded((self.px(w), self.px(h)), self.px(r), fill, outline, self.dpi * width), bg)
        return self.photo(stretch(image, (self.px(big[0]), self.px(big[1])), self.px(border)))

    def switch(self, on, track, knob, bg, gap=10):
        w, h, d = self.px(34), self.px(18), self.px(12)
        size = (w + self.px(gap), h)
        image = Image.new('RGBA', (size[0] * SS, size[1] * SS), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), h * SS // 2, fill=rgba(track))
        x = w - (h - d) // 2 - d if on else (h - d) // 2
        draw.ellipse((x * SS, (h - d) / 2 * SS, (x + d) * SS, (h + d) / 2 * SS), fill=rgba(knob))
        return self.photo(solid(image.resize(size, Image.LANCZOS), bg))

    def chevron(self, color, bg, width=26, height=14):
        size = (self.px(width), self.px(height))
        image = Image.new('RGBA', (size[0] * SS, size[1] * SS), (0, 0, 0, 0))
        cx, cy, s = size[0] / 2 - self.px(3), size[1] / 2, self.px(3.4)
        pts = [(cx - s, cy - s / 2), (cx, cy + s / 2), (cx + s, cy - s / 2)]
        ImageDraw.Draw(image).line([(x * SS, y * SS) for x, y in pts], fill=rgba(color),
                                   width=round(1.6 * self.dpi * SS), joint='curve')
        return self.photo(solid(image.resize(size, Image.LANCZOS), bg))

    def play(self, top, bottom, line, bg):
        px = self.px
        w, h, r = px(160), px(54), px(10)
        fill = vertical_gradient((w * SS, h * SS), top, bottom)
        mask = rounded((w, h), r, '#ffffff').getchannel('A').resize((w * SS, h * SS))
        image = Image.new('RGBA', (w * SS, h * SS), (0, 0, 0, 0))
        image.paste(fill, (0, 0), mask)
        image = image.resize((w, h), Image.LANCZOS)
        image.alpha_composite(rounded((w, h), r, None, line, self.dpi))
        return self.photo(stretch(solid(image, bg), (px(520), h), (px(14), 0, px(14), 0)))

    def style(self, s, surfaces, fonts):
        """Fills the ttk.Style `s`. surfaces: {style prefix: colour under the widgets} ('' is the
        page). fonts: (body family, display font tuple for PLAY)."""
        c, px = self.c, self.px
        body, play_font = fonts
        s.theme_use('clam')
        for name in ('.', 'TButton', 'TCheckbutton', 'TRadiobutton', 'TCombobox', 'TEntry', 'TSpinbox',
                     'TScrollbar', 'TLabel', 'TFrame'):
            s.map(name, background=[])  # clam lightens hovered and disabled widgets; ours keep their surface
        s.configure('ComboboxPopdownFrame', background=c['field'], bordercolor=c['border_strong'], relief='solid',
                    borderwidth=1)
        label = lambda element: [(element, {'sticky': 'nswe', 'children': [
            ('Button.padding', {'sticky': 'nswe', 'children': [('Button.label', {'sticky': 'nswe'})]})]})]
        for prefix, bg in surfaces.items():
            tag = 'Issn' + prefix.rstrip('.')
            button = lambda fill, line, bg=bg: self.box(40, 34, 8, fill, line, bg, (420, 96), 10)
            s.element_create(f'{tag}.Button.border', 'image', button(c['card_hover'], c['border_strong']),
                             ('disabled', button(c['field'], c['border'])),
                             ('pressed', button(c['field'], c['accent'])),
                             ('active', button(c['hover'], c['accent'])),
                             border=px(10), padding=px(2), width=px(40), height=px(34), sticky='nsew')
            s.element_create(f'{tag}.Accent.border', 'image', button(c['accent'], c['accent_hi']),
                             ('disabled', button('#2a1416', '#3a2024')),
                             ('pressed', button(c['accent_dark'], c['accent_hi'])),
                             ('active', button(c['accent_hi'], '#ff7383')),
                             border=px(10), padding=px(2), width=px(40), height=px(34), sticky='nsew')
            s.layout(prefix + 'TButton', label(f'{tag}.Button.border'))
            s.layout(prefix + 'Accent.TButton', label(f'{tag}.Accent.border'))
            field = lambda fill, line, bg=bg: self.box(40, 34, 7, fill, line, bg, (640, 64), 9)
            s.element_create(f'{tag}.field', 'image', field(c['field'], c['border_strong']),
                             ('disabled', field(bg, c['border'])), ('focus', field(c['field'], c['neon'])),
                             ('hover', field(c['field'], c['border_hover'])),
                             border=px(9), padding=(px(3), px(2)), width=px(40), height=px(34), sticky='nsew')
            s.layout(prefix + 'TEntry', [(f'{tag}.field', {'sticky': 'nswe', 'children': [
                ('Entry.padding', {'sticky': 'nswe', 'children': [('Entry.textarea', {'sticky': 'nswe'})]})]})])
            s.layout(prefix + 'TCombobox', [(f'{tag}.field', {'sticky': 'nswe', 'children': [
                ('Issn.Combobox.downarrow', {'side': 'right', 'sticky': ''}),
                ('Combobox.padding', {'sticky': 'nswe', 'children': [('Combobox.textarea', {'sticky': 'nswe'})]})]})])
            s.element_create(f'{tag}.switch', 'image', self.switch(False, '#2a2d38', '#8c90a0', bg),
                             ('disabled', 'selected', self.switch(True, '#3a1518', '#7d7470', bg)),
                             ('disabled', self.switch(False, '#1b1c22', '#45474f', bg)),
                             ('active', 'selected', self.switch(True, c['accent_hi'], '#ffffff', bg)),
                             ('selected', self.switch(True, c['accent'], '#ffffff', bg)),
                             ('active', self.switch(False, '#343846', '#b0b4c2', bg)), sticky='')
            s.layout(prefix + 'TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
                (f'{tag}.switch', {'side': 'left', 'sticky': ''}),
                ('Checkbutton.label', {'side': 'left', 'sticky': 'nswe'})]})])
            s.configure(prefix + 'TButton', background=bg, foreground=c['text'], padding=(px(14), px(6)),
                        anchor='center', font=(body, 10))
            s.map(prefix + 'TButton', foreground=[('disabled', c['muted']), ('active', '#ffffff')])
            s.configure(prefix + 'Accent.TButton', background=bg, foreground='#ffffff', padding=(px(14), px(6)),
                        anchor='center', font=(body, 10, 'bold'))
            s.configure(prefix + 'TCheckbutton', background=bg, foreground=c['text'], padding=(0, px(4)))
            s.map(prefix + 'TCheckbutton', background=[('active', bg)],
                  foreground=[('disabled', c['muted']), ('active', '#ffffff')])
            for name in ('TEntry', 'TCombobox'):
                s.configure(prefix + name, background=bg, foreground=c['text'], padding=(px(9), px(5), px(4), px(5)),
                            insertcolor=c['text'], selectbackground=c['accent'], selectforeground='#ffffff')
                s.map(prefix + name, foreground=[('disabled', c['muted'])], fieldbackground=[('readonly', c['field'])],
                      selectbackground=[('readonly', c['field'])], selectforeground=[('readonly', c['text'])])
        s.element_create('Issn.Combobox.downarrow', 'image', self.chevron(c['neon'], c['field']),
                         ('disabled', self.chevron(c['muted'], c['panel'])), sticky='')
        # PLAY on the bottom bar.
        bar = surfaces.get('Bar.', c['bg'])
        s.element_create('Issn.Play.border', 'image', self.play('#ec3247', '#b8172a', '#ff6b7c', bar),
                         ('disabled', self.play('#262a35', '#1d2029', '#323645', bar)),
                         ('pressed', self.play('#b8172a', '#9a1222', '#ff6b7c', bar)),
                         ('active', self.play('#ff4a5e', '#cf1d31', '#ffffff', bar)),
                         border=px(14), padding=px(2), width=px(160), height=px(54), sticky='nsew')
        s.layout('Play.TButton', label('Issn.Play.border'))
        s.configure('Play.TButton', background=bar, foreground='#ffffff', font=play_font, padding=(px(44), px(10)))
        s.map('Play.TButton', foreground=[('disabled', '#6d7080')])
        # A slim scroll bar.
        thumb = lambda fill: self.photo(stretch(solid(rounded((px(8), px(28)), px(3), fill), c['panel']),
                                                (px(8), px(1600)), (0, px(5), 0, px(5))))
        s.element_create('Issn.Vertical.Scrollbar.trough', 'image',
                         self.photo(Image.new('RGB', (px(8), px(2400)), rgba(c['panel'])[:3])),
                         width=px(8), height=px(8), sticky='ns')
        s.element_create('Issn.Vertical.Scrollbar.thumb', 'image', thumb(c['border_strong']),
                         ('pressed', thumb(c['accent'])), ('active', thumb(c['border_hover'])),
                         border=(0, px(5)), width=px(8), height=px(28), sticky='nsew')
        s.layout('Vertical.TScrollbar', [('Issn.Vertical.Scrollbar.trough', {'sticky': 'ns', 'children': [
            ('Issn.Vertical.Scrollbar.thumb', {'expand': '1', 'sticky': 'nswe'})]})])
        s.configure('Vertical.TScrollbar', background=c['panel'])
