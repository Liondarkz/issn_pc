# SPDX-License-Identifier: GPL-2.0-or-later
"""inFAMOUS Second Son's button icons, redrawn for the keyboard and mouse or an Xbox controller.

The game writes its prompts with private-use characters (U+F080 Circle ... U+F094 D-pad down;
the menus' U+F08E / U+F08F become Cross / Circle at run time) and draws them from one icon font
in each language package, art/cache/lang_<language>_text.xpps. build() writes copies of those
packages whose icon atlas has the cells of those characters redrawn (a keycap with the key's
name, a mouse with the button pressed, or the Xbox letters), and returns the guest paths run.py
mounts them on. The game folder is never written, and the copies stay on the player's PC.

KCAP package (little-endian; offsets relative to the data start `doff` at file +0x28):
    +0x18 u32 table offset; table: 6 x u64 {0, nsec, 0, nitem, 0, extra}, nsec x 0x28 sections,
          nitem x {u32 size, u32 off, u32 flags} items
Language package, data offset 0: the font set; +0x40, 8 x u64 font objects (the first is not one).
Font object: +0x28 the 8-byte hash of its texture, +0x38 u64 glyph array, +0x40 u64 count;
glyph = 32 bytes {u16 code, u16 x, u16 y, u16 w, u16 h, ...} in pixels of the upright atlas.
Texture object (an item of 0x60 bytes): +0x28 the T# (word0 = data offset of level 0, data
format 10 = RGBA8, tiling index 13 = 1D thin micro-tiled), +0x48 its hash. The atlas rows are
stored bottom-up, and each 8x8 micro tile holds its 64 pixels in Morton-like order (the
shadPS4 micro_32bpp detiler's table)."""
import hashlib
import json
from pathlib import Path
import shutil
import struct

VERSION = 1  # of the drawing: a change redraws the cached copies
PACKAGES = 'art/cache'
FONT_FILE = Path(__file__).resolve().parent.parent / 'launcher' / 'issn_font.ttf'

# Icon character -> game button (the atlas shows these; checked against the game's own
# u_button_* / u_btn_* strings).
ICON_BUTTONS = {
    0xF080: 'circle', 0xF081: 'square', 0xF082: 'triangle', 0xF083: 'cross',
    0xF084: 'l1', 0xF085: 'l2', 0xF086: 'r1', 0xF087: 'r2', 0xF088: 'options', 0xF089: 'options',
    0xF08A: 'lstick', 0xF08B: 'l3', 0xF08C: 'rstick', 0xF08D: 'r3',
    0xF090: 'dpad', 0xF091: 'right', 0xF092: 'up', 0xF093: 'left', 0xF094: 'down', 0xF098: 'touchpad',
}
# The port's default keyboard (src/runtime_pad.c): button -> label. L2 / R2 are the mouse buttons.
KEYBOARD = {'cross': 'SPACE', 'circle': 'SHIFT', 'square': 'C', 'triangle': 'F', 'l1': 'Q', 'r1': 'E',
            'l2': 'mouse:right', 'r2': 'mouse:left', 'l3': 'U', 'r3': 'O', 'options': 'ENTER',
            'touchpad': 'G', 'up': 'arrow:up', 'down': 'arrow:down', 'left': 'arrow:left',
            'right': 'arrow:right', 'dpad': 'arrows', 'lstick': 'WASD', 'rstick': 'mouse:'}
XBOX = {'cross': ('A', (108, 196, 74)), 'circle': ('B', (232, 72, 66)), 'square': ('X', (66, 142, 232)),
        'triangle': ('Y', (244, 196, 48)), 'l1': ('LB', None), 'r1': ('RB', None), 'l2': ('LT', None),
        'r2': ('RT', None), 'l3': ('LS', None), 'r3': ('RS', None), 'options': ('menu', None),
        'touchpad': ('view', None)}
# Key names of ISSN_KEY_MAP (SDL names) shortened to fit a keycap.
SHORT_NAMES = {'left shift': 'SHIFT', 'right shift': 'SHIFT', 'left ctrl': 'CTRL', 'right ctrl': 'CTRL',
               'left alt': 'ALT', 'right alt': 'ALT', 'return': 'ENTER', 'enter': 'ENTER', 'space': 'SPACE',
               'backspace': 'BKSP', 'escape': 'ESC', 'tab': 'TAB', 'caps lock': 'CAPS', 'up': 'arrow:up',
               'down': 'arrow:down', 'left': 'arrow:left', 'right': 'arrow:right'}


class PackageError(ValueError):
    """Not a language package with the icon font this module knows."""


def detile_order():
    """(col, row) of each of the 64 pixels of a 32bpp micro tile, in stored order."""
    table = (0x11011000, 0x31213020, 0x13031202, 0x33233222, 0x51415040, 0x71617060, 0x53435242,
             0x73637262, 0x15051404, 0x35253424, 0x17071606, 0x37273626, 0x55455444, 0x75657464,
             0x57475646, 0x77677666)
    return [((table[i >> 2] >> (8 * (i % 4))) >> 4 & 0xF, (table[i >> 2] >> (8 * (i % 4))) & 0xF)
            for i in range(64)]


ORDER = detile_order()


def detile(data, width, height):
    """Upright RGBA rows (top first) of a 1D thin micro-tiled RGBA8 level stored bottom-up."""
    out = bytearray(width * height * 4)
    tiles_x = width // 8
    for tile in range(tiles_x * (height // 8)):
        tx, ty = tile % tiles_x, tile // tiles_x
        base = tile * 256
        for i, (col, row) in enumerate(ORDER):
            x, y = tx * 8 + col, height - 1 - (ty * 8 + row)
            out[(y * width + x) * 4:(y * width + x) * 4 + 4] = data[base + i * 4:base + i * 4 + 4]
    return bytes(out)


def retile(pixels, width, height):
    """The inverse of detile()."""
    out = bytearray(width * height * 4)
    tiles_x = width // 8
    for tile in range(tiles_x * (height // 8)):
        tx, ty = tile % tiles_x, tile // tiles_x
        base = tile * 256
        for i, (col, row) in enumerate(ORDER):
            x, y = tx * 8 + col, height - 1 - (ty * 8 + row)
            out[base + i * 4:base + i * 4 + 4] = pixels[(y * width + x) * 4:(y * width + x) * 4 + 4]
    return bytes(out)


def icon_font(data):
    """(doff, {code: (x, y, w, h)}, texture data offset, width, height) of the icon font."""
    if data[:4] != b'KCAP' or len(data) < 0x98:
        raise PackageError('not a KCAP package')
    table, = struct.unpack_from('<I', data, 0x18)
    doff, dsize = struct.unpack_from('<II', data, 0x28)
    _, nsec, _, nitem, _, _ = struct.unpack_from('<6Q', data, table)
    items = [struct.unpack_from('<III', data, table + 48 + nsec * 0x28 + 12 * i) for i in range(nitem)]

    def u64(rel):
        return struct.unpack_from('<Q', data, doff + rel)[0]

    textures = {}
    for size, off, _flags in items:
        if size == 0x60 and off + 0x50 <= dsize:
            words = struct.unpack_from('<4I', data, doff + off + 0x28)
            textures[data[doff + off + 0x48:doff + off + 0x50]] = words
    for slot in range(8):
        font = u64(0x40 + 8 * slot)
        if not 0 <= font < dsize - 0x48:
            continue
        glyphs, count = u64(font + 0x38), u64(font + 0x40)
        if not (0 < count < 4096 and 0 <= glyphs and glyphs + count * 32 <= dsize):
            continue
        cells = {}
        for i in range(count):
            code, x, y, w, h = struct.unpack_from('<5H', data, doff + glyphs + 32 * i)
            cells[code] = (x, y, w, h)
        if not any(code in cells for code in ICON_BUTTONS):
            continue
        words = textures.get(data[doff + font + 0x28:doff + font + 0x30])
        if not words:
            raise PackageError('the icon font has no texture')
        base, w1, w2, w3 = words
        width, height = (w2 & 0x3FFF) + 1, ((w2 >> 14) & 0x3FFF) + 1
        if (w1 >> 20) & 0x3F != 10 or (w3 >> 20) & 0x1F != 13 or width % 8 or height % 8:
            raise PackageError('the icon atlas is not an RGBA8 micro-tiled texture')
        if base + width * height * 4 > dsize:
            raise PackageError('the icon atlas runs past the package')
        return doff, cells, base, width, height
    raise PackageError('no icon font in this package')


def key_labels(key_map=''):
    """Button -> label of the keyboard, with a ISSN_KEY_MAP value ('cross=Q,circle=Left Shift')."""
    labels = dict(KEYBOARD)
    for entry in (key_map or '').split(','):
        button, _, key = (part.strip() for part in entry.partition('='))
        button = button.casefold()
        if button in labels and key and not labels[button].startswith(('mouse', 'arrow')):
            labels[button] = SHORT_NAMES.get(key.casefold(), key.upper()[:6])
    return labels


def _font(size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(str(FONT_FILE), size)
    except OSError:
        return ImageFont.load_default()


def _text_fit(draw, text, box_w, box_h):
    size = int(box_h)
    while size > 6:
        font = _font(size)
        x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
        if x1 - x0 <= box_w and y1 - y0 <= box_h:
            return font, (x0, y0, x1, y1)
        size -= 2
    font = _font(6)
    return font, draw.textbbox((0, 0), text, font=font)


def draw_cell(button, label, style, w, h, scale=4):
    """An RGBA image (w x h) of one button's icon: white shapes on transparent, like the game's."""
    from PIL import Image, ImageDraw
    W, H = w * scale, h * scale
    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    white, clear = (255, 255, 255, 255), (0, 0, 0, 0)
    pad = scale * 3
    if style == 'xbox':
        text, color = label
        if button in ('cross', 'circle', 'square', 'triangle'):
            draw.ellipse((pad, pad, W - pad, H - pad), fill=white)
            font, (x0, y0, x1, y1) = _text_fit(draw, text, W * 0.5, H * 0.55)
            draw.text(((W - (x1 - x0)) / 2 - x0, (H - (y1 - y0)) / 2 - y0), text, font=font,
                      fill=color + (255,))
        elif text in ('menu', 'view'):
            draw.rounded_rectangle((pad, pad + H * 0.12, W - pad, H - pad - H * 0.12), H * 0.3, fill=white)
            cx, cy, r = W / 2, H / 2, H * 0.22
            t = max(2, int(r * 0.3))
            if text == 'menu':
                for dy in (-0.6, 0, 0.6):
                    draw.rectangle((cx - r, cy + dy * r - t / 2, cx + r, cy + dy * r + t / 2), fill=clear)
            else:
                for dx, dy in ((-0.3, -0.25), (0.3, 0.25)):
                    draw.rectangle((cx + (dx - 0.6) * r, cy + (dy - 0.5) * r, cx + (dx + 0.6) * r,
                                    cy + (dy + 0.5) * r), outline=clear, width=t)
        else:  # LB RB LT RT LS RS: a plate with the letters cut out
            draw.rounded_rectangle((pad, pad + H * 0.08, W - pad, H - pad - H * 0.08), H * 0.22, fill=white)
            font, (x0, y0, x1, y1) = _text_fit(draw, text, W * 0.7, H * 0.55)
            draw.text(((W - (x1 - x0)) / 2 - x0, (H - (y1 - y0)) / 2 - y0), text, font=font, fill=clear)
    elif label.startswith('mouse:'):
        side = label[len('mouse:'):]
        mw, mh = H * 0.62, H - 2 * pad
        x0, y0 = (W - mw) / 2, pad
        t = max(2, int(scale * 2.5))
        draw.rounded_rectangle((x0, y0, x0 + mw, y0 + mh), mw * 0.48, fill=white)
        draw.rounded_rectangle((x0 + t, y0 + t, x0 + mw - t, y0 + mh - t), mw * 0.42, fill=clear)
        split = y0 + mh * 0.42
        draw.rectangle((x0, split - t / 2, x0 + mw, split + t / 2), fill=white)
        draw.rectangle((W / 2 - t / 2, y0, W / 2 + t / 2, split), fill=white)
        if side in ('left', 'right'):  # the pressed button filled
            mask = Image.new('L', (W, H), 0)
            md = ImageDraw.Draw(mask)
            md.rounded_rectangle((x0, y0, x0 + mw, y0 + mh), mw * 0.48, fill=255)
            md.rectangle((0, split, W, H), fill=0)
            md.rectangle((W / 2, 0, W, H) if side == 'left' else (0, 0, W / 2, H), fill=0)
            img.paste(white, mask=mask)
    elif label.startswith('arrow') or label == 'arrows':
        draw.rounded_rectangle((pad, pad, W - pad, H - pad), H * 0.18, fill=white)
        cx, cy, r = W / 2, H / 2, H * 0.28
        dirs = {'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0)}
        for name in (dirs if label == 'arrows' else (label.split(':')[1],)):
            ax, ay = dirs[name]
            scale_r = 0.55 if label == 'arrows' else 1.0
            px, py = cx + ax * r * 0.45 * (label == 'arrows'), cy + ay * r * 0.45 * (label == 'arrows')
            rr = r * scale_r
            draw.polygon([(px + ax * rr, py + ay * rr), (px - ax * rr * 0.4 - ay * rr * 0.8, py - ay * rr * 0.4 - ax * rr * 0.8),
                          (px - ax * rr * 0.4 + ay * rr * 0.8, py - ay * rr * 0.4 + ax * rr * 0.8)], fill=clear)
    else:  # a keycap: a white key with its name cut out
        draw.rounded_rectangle((pad, pad + H * 0.04, W - pad, H - pad - H * 0.04), H * 0.18, fill=white)
        font, (x0, y0, x1, y1) = _text_fit(draw, label, W - 4 * pad, H * (0.6 if len(label) <= 2 else 0.42))
        draw.text(((W - (x1 - x0)) / 2 - x0, (H - (y1 - y0)) / 2 - y0), label, font=font, fill=clear)
    return img.resize((w, h), Image.LANCZOS)


def redraw(data, style, key_map=''):
    """A copy of the package bytes with the icon cells redrawn for `style` (keyboard / xbox)."""
    from PIL import Image
    doff, cells, base, width, height = icon_font(data)
    start = doff + base
    atlas = Image.frombytes('RGBA', (width, height), detile(data[start:start + width * height * 4], width, height))
    labels = key_labels(key_map) if style == 'keyboard' else XBOX
    for code, button in ICON_BUTTONS.items():
        if code not in cells or button not in labels:
            continue
        x, y, w, h = cells[code]
        if not w or not h or x + w > width or y + h > height:
            continue
        atlas.paste(draw_cell(button, labels[button], style, w, h), (x, y))
    out = bytearray(data)
    out[start:start + width * height * 4] = retile(atlas.tobytes(), width, height)
    return bytes(out)


def build(game, out, style, key_map=''):
    """{guest path: host copy} of the redrawn language packages (style keyboard or xbox); {} for
    another style or a game folder without them. Copies are reused while nothing changed."""
    if style not in ('keyboard', 'xbox'):
        shutil.rmtree(Path(out) / 'glyphs', ignore_errors=True)
        return {}
    folder = Path(game) / PACKAGES
    sources = sorted(p for p in folder.glob('lang_*_text.xpps')) if folder.is_dir() else []
    target = Path(out) / 'glyphs' / style
    target.mkdir(parents=True, exist_ok=True)
    stamp_file = target / 'stamp.json'
    try:
        stamps = json.loads(stamp_file.read_text())
    except (OSError, ValueError):
        stamps = {}
    mounts, new_stamps = {}, {}
    for source in sources:
        stat = source.stat()
        key = hashlib.sha256(f'{VERSION}|{style}|{key_map}|{stat.st_size}|{stat.st_mtime_ns}'.encode()).hexdigest()
        copy = target / source.name
        if stamps.get(source.name) != key or not copy.is_file():
            try:
                copy.write_bytes(redraw(source.read_bytes(), style, key_map))
            except PackageError:
                continue
        new_stamps[source.name] = key
        mounts[f'/app0/{PACKAGES}/{source.name}'] = str(copy)
    stamp_file.write_text(json.dumps(new_stamps))
    return mounts
