# SPDX-License-Identifier: GPL-2.0-or-later
"""inFAMOUS Second Son's in-world button prompts, redrawn for the keyboard and mouse or an Xbox pad.

The prompts drawn in the world (not text) are BC3 textures: in art/cache/core_common.sprig.xpps
the subdue and execute discs (Triangle, Square), the d-pad discs of the karma choices and of
"start mission" (one direction lit), and the small karma discs with the d-pad's Down lit; in
core_seaport.sprig.xpps the spray can's painted R2 (orange, grey). build() writes copies of those
packages with them redrawn (the key's cap, the arrow keys, the mouse, the Xbox letter) and returns
them for run.py to mount over the originals; the game folder is never written.

Textures are found by the 8-byte hash at +0x48 of their 0x60-byte object (the T# at +0x28: word0
data offset, format 37 = BC3, tiling 13 = 1D thin), checked against their size and mip count, so
another build of the package without them is left alone. Levels follow shadPS4's UpdateSize for a
micro-tiled block format: pitch and height in 4x4 blocks aligned to 8, the pitch grown until the
level is a multiple of 256 bytes, levels one after another; each 8x8 micro tile holds its 64
blocks in the micro detiler's order, rows bottom-up. Only the blocks whose pixels changed are
encoded again; every other byte stays the game's."""
import hashlib
import json
import mmap
import shutil
import struct
from pathlib import Path

import infamous_glyphs as glyphs

VERSION = 3  # of the drawing: a change redraws the cached copies
PACKAGE = 'core_common.sprig.xpps'
BC3, THIN_1D, TEX_2D = 37, 13, 9

# The touchpad drawn under every touchpad prompt (a fingerprint to hold, two for both thumbs, an
# arrow to swipe): its key goes in its corner (G; the View button on an Xbox pad).
TOUCHPAD = bytes.fromhex('59666b0a8697b628')
TOUCHPAD_PROMPT = ('touchpad', (512, 512), 10, 'touchpad')
# package -> {texture hash -> (button, (width, height), mip levels, kind)}
PROMPTS = {
    'core_common.sprig.xpps': {
        bytes.fromhex('0e56e2aa9a59c237'): ('triangle', (128, 128), 8, 'disc'),  # subdue (blue)
        bytes.fromhex('a54687fd3c3f0041'): ('square', (128, 128), 8, 'disc'),    # execute (red)
        bytes.fromhex('8d2f945eed7bd661'): ('dpad', (256, 256), 9, 'dpad'),      # Up lit (start a mission)
        bytes.fromhex('749cd5aff5fcfe6a'): ('dpad', (256, 256), 9, 'dpad'),      # Left red / Right blue
        bytes.fromhex('99aa4ba93936c7a5'): ('down', (64, 64), 7, 'small'),       # Down lit, blue
        bytes.fromhex('b46e68a14157a536'): ('down', (64, 64), 7, 'small'),       # Down lit, red
    },
    'core_seaport.sprig.xpps': {
        bytes.fromhex('c49ce6e8ab97b628'): ('r2', (128, 256), 9, 'spray'),       # spray here (orange)
        bytes.fromhex('7a324f4b1343fcba'): ('r2', (128, 256), 9, 'spray'),       # (grey)
        bytes.fromhex('f837ecc1a2681118'): ('r2', (128, 256), 9, 'spray'),       # (orange, smaller)
        TOUCHPAD: TOUCHPAD_PROMPT,
    },
    # the missions that carry their own copy of the touchpad (the prologue's car and wreckage, the
    # longhouse door)
    'msn_smi.sprig.xpps': {TOUCHPAD: TOUCHPAD_PROMPT},
    'msn_vor.sprig.xpps': {TOUCHPAD: TOUCHPAD_PROMPT},
}
ALL_PROMPTS = {digest: prompt for prompts in PROMPTS.values() for digest, prompt in prompts.items()}
XBOX_LETTERS = {'triangle': 'Y', 'square': 'X', 'cross': 'A', 'circle': 'B'}


def mip_layout(width, height, levels, block_bytes=16):
    """[(offset, blocks wide, blocks high, aligned pitch in blocks)] of each level, and the total."""
    out, offset = [], 0
    for level in range(levels):
        bw, bh = max(((width >> level) + 3) // 4, 1), max(((height >> level) + 3) // 4, 1)
        pitch, rows = (bw + 7) & ~7, (bh + 7) & ~7
        while pitch * rows * block_bytes % 256:
            pitch += 8
        out.append((offset, bw, bh, pitch))
        offset += pitch * rows * block_bytes
    return out, offset


SLOT = {pos: i for i, pos in enumerate(glyphs.ORDER)}  # (col, row) in a micro tile -> stored index


def block_offset(bx, by, pitch, block_bytes=16):
    tile = (by // 8) * (pitch // 8) + bx // 8
    return (tile * 64 + SLOT[(bx % 8, by % 8)]) * block_bytes


def read_level(data, start, width, height, bw, bh, pitch):
    """The upright RGBA image of one BC3 level (width, height a multiple of 4)."""
    from PIL import Image
    raw = bytearray(bw * bh * 16)
    for by in range(bh):
        for bx in range(bw):
            o = start + block_offset(bx, by, pitch)
            raw[(by * bw + bx) * 16:(by * bw + bx) * 16 + 16] = data[o:o + 16]
    image = Image.frombytes('RGBA', (bw * 4, bh * 4), bytes(raw), 'bcn', 3)
    return image.crop((0, 0, width, height)).transpose(Image.FLIP_TOP_BOTTOM)


def encode_bc3(pixels):
    """16 bytes of BC3 for 16 (r, g, b, a) pixels in rows."""
    alphas = [p[3] for p in pixels]
    a0, a1 = max(alphas), min(alphas)
    abits = 0
    if a0 != a1:
        for i, a in enumerate(alphas):
            t = (2 * (a - a1) * 7 + (a0 - a1)) // (2 * (a0 - a1))  # 0..7, rounded
            abits |= (0 if t == 7 else 1 if t == 0 else 8 - t) << (3 * i)
    points = [p[:3] for p in pixels if p[3] >= 16] or [p[:3] for p in pixels]
    n = len(points)
    mean = [sum(p[c] for p in points) / n for c in range(3)]
    cov = [[sum((p[i] - mean[i]) * (p[j] - mean[j]) for p in points) for j in range(3)] for i in range(3)]
    axis = [1.0, 1.0, 1.0]
    for _ in range(8):
        axis = [sum(cov[i][j] * axis[j] for j in range(3)) for i in range(3)]
        norm = max(abs(v) for v in axis)
        if norm < 1e-9:
            axis = [1.0, 1.0, 1.0]
            break
        axis = [v / norm for v in axis]
    proj = [sum((p[c] - mean[c]) * axis[c] for c in range(3)) for p in points]
    lo, hi = points[proj.index(min(proj))], points[proj.index(max(proj))]
    inset = [(hi[c] - lo[c]) / 16 for c in range(3)]
    hi = [hi[c] - inset[c] for c in range(3)]
    lo = [lo[c] + inset[c] for c in range(3)]

    def pack(c):
        r, g, b = (min(max(int(round(v)), 0), 255) for v in c)
        return ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | (b * 31 + 127) // 255

    def unpack(v):
        r, g, b = v >> 11, (v >> 5) & 63, v & 31
        return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))

    c0, c1 = pack(hi), pack(lo)
    if c0 < c1:
        c0, c1 = c1, c0
    e0, e1 = unpack(c0), unpack(c1)
    palette = [e0, e1, tuple((2 * e0[c] + e1[c]) // 3 for c in range(3)),
               tuple((e0[c] + 2 * e1[c]) // 3 for c in range(3))]
    cbits = 0
    if c0 != c1:
        for i, p in enumerate(pixels):
            best = min(range(4), key=lambda k: sum((p[c] - palette[k][c]) ** 2 for c in range(3)))
            cbits |= best << (2 * i)
    return bytes((a0, a1)) + abits.to_bytes(6, 'little') + struct.pack('<HHI', c0, c1, cbits)


def find(data, doff, dsize):
    """[(texture data start, button, (width, height), levels, kind)] of the prompts in this package."""
    found = []
    for digest, (button, size, levels, kind) in ALL_PROMPTS.items():
        pos = data.find(digest)
        while pos >= 0:
            item = pos - 0x48 - doff
            if item >= 0 and doff + item + 0x38 <= len(data):
                base, w1, w2, w3 = struct.unpack_from('<4I', data, doff + item + 0x28)
                width, height = (w2 & 0x3FFF) + 1, ((w2 >> 14) & 0x3FFF) + 1
                if ((w1 >> 20) & 0x3F, (w3 >> 20) & 0x1F, w3 >> 28, ((w3 >> 16) & 0xF) + 1, width, height) == \
                        (BC3, THIN_1D, TEX_2D, levels) + size and \
                        base + mip_layout(*size, levels)[1] <= dsize:
                    found.append((doff + base, button, size, levels, kind))
                    break
            pos = data.find(digest, pos + 1)
    return found


def _tint(image, color):
    """White shapes of `image` in `color` (alpha kept)."""
    from PIL import Image
    out = Image.new('RGBA', image.size, tuple(color[:3]) + (0,))
    out.putalpha(image.getchannel('A'))
    return out


def _average(image, test, box=None, brightest=1.0):
    """The mean colour of the pixels passing `test` (of the brightest fraction of them)."""
    px = image.load()
    x0, y0, x1, y1 = box or (0, 0, image.width, image.height)
    picked = [px[x, y] for y in range(int(y0), int(y1)) for x in range(int(x0), int(x1)) if test(x, y, px[x, y])]
    if not picked:
        return None
    picked.sort(key=lambda p: -sum(p[:3]))
    picked = picked[:max(1, int(len(picked) * brightest))]
    return tuple(sum(p[c] for p in picked) // len(picked) for c in range(3))


def _erase(image, radius, color, feather):
    """`image` with a disc of `color` (opaque) over its centre."""
    from PIL import Image, ImageDraw, ImageFilter
    s = image.width
    mask = Image.new('L', image.size, 0)
    ImageDraw.Draw(mask).ellipse((s / 2 - radius, s / 2 - radius, s / 2 + radius, s / 2 + radius), fill=255)
    if feather:
        mask = mask.filter(ImageFilter.GaussianBlur(feather))
    return Image.composite(Image.new('RGBA', image.size, tuple(color) + (255,)), image, mask)


def _glow(image, symbol, at, strength=0.55):
    """`symbol` pasted at `at` over a soft glow of itself."""
    from PIL import Image, ImageFilter
    layer = Image.new('RGBA', image.size, (0, 0, 0, 0))
    layer.alpha_composite(symbol, at)
    glow = layer.filter(ImageFilter.GaussianBlur(max(1, image.width / 64)))
    glow.putalpha(glow.getchannel('A').point(lambda a: int(a * strength)))
    out = image.copy()
    out.alpha_composite(glow)
    out.alpha_composite(layer)
    return out


def _disc(image, button, label, style):
    """Subdue / execute: the symbol inside the dark disc becomes the key (or the Xbox letter)."""
    from PIL import Image, ImageDraw
    s, c = image.width, image.width / 2

    def ring(r0, r1):
        return lambda x, y, p: r0 * s <= ((x - c) ** 2 + (y - c) ** 2) ** 0.5 < r1 * s

    color = _average(image, lambda x, y, p: ring(0, 0.25)(x, y, p) and max(p[:3]) > 110 and
                     max(p[:3]) - min(p[:3]) > 70, brightest=0.5) or (235, 235, 235)
    dark = _average(image, lambda x, y, p: ring(0.28, 0.33)(x, y, p) and p[3] > 200 and sum(p[:3]) < 150) or (12, 12, 16)
    out = _erase(image, 0.29 * s, dark, s / 90)
    if style == 'xbox':
        big = Image.new('RGBA', (s * 4, s * 4), (0, 0, 0, 0))
        draw = ImageDraw.Draw(big)
        text = XBOX_LETTERS.get(button, label)
        stroke = s // 16
        font, (x0, y0, x1, y1) = glyphs._text_fit(draw, text, s * 4 * 0.36 - 2 * stroke, s * 4 * 0.40 - 2 * stroke)
        draw.text(((s * 4 - (x1 - x0)) / 2 - x0, (s * 4 - (y1 - y0)) / 2 - y0), text, font=font,
                  fill=(255, 255, 255, 255), stroke_width=stroke, stroke_fill=(255, 255, 255, 255))
        symbol = _tint(big.resize((s, s), Image.LANCZOS), color)
        return _glow(out, symbol, (0, 0))
    size = int(round(0.42 * s))
    symbol = _tint(glyphs.draw_cell(button, label, 'keyboard', size, size), color)
    return _glow(out, symbol, (int(round(c - size / 2)), int(round(c - size / 2))))


ARROWS = {'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0)}


def _dpad(image):
    """A d-pad disc: the four buttons become the arrow keys (inverted T), each keeping its colour."""
    s, c = image.width, image.width / 2
    patch = 0.03 * s
    colors = {}
    for name, (dx, dy) in ARROWS.items():
        x, y = c + dx * 0.14 * s, c + dy * 0.14 * s
        colors[name] = _average(image, lambda *_: True, (x - patch, y - patch, x + patch, y + patch),
                                brightest=0.5) or (128, 128, 128)
        if max(colors[name]) < 60:  # a dark sample: not a button there; plain grey
            colors[name] = (128, 128, 128)
    dark = []
    for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        x, y = c + dx * 0.15 * s, c + dy * 0.15 * s
        dark.append(_average(image, lambda *_: True, (x - patch, y - patch, x + patch, y + patch)) or (10, 10, 10))
    dark = tuple(min(d[i] for d in dark) for i in range(3))
    out = _erase(image, 0.27 * s, dark, s / 200)
    cell = int(round(0.15 * s))
    left, top = c - 1.5 * cell, c - cell
    places = {'up': (1, 0), 'left': (0, 1), 'down': (1, 1), 'right': (2, 1)}
    for name, (col, row) in places.items():
        key = _tint(glyphs.draw_cell(name, f'arrow:{name}', 'keyboard', cell, cell), colors[name])
        out.alpha_composite(key, (int(round(left + col * cell)), int(round(top + row * cell))))
    return out


def _small(image):
    """The small karma disc with Down lit: one Down arrow key, in the lit colour, on the disc."""
    s, c = image.width, image.width / 2

    def ring(r0, r1):
        return lambda x, y, p: r0 * s <= ((x - c) ** 2 + (y - c) ** 2) ** 0.5 < r1 * s

    disc = _average(image, lambda x, y, p: ring(0.26, 0.34)(x, y, p) and p[3] > 200 and
                    max(p[:3]) - min(p[:3]) > 60) or (60, 140, 230)
    lit = _average(image, lambda x, y, p: ring(0, 0.3)(x, y, p) and min(p[:3]) > 190) or (240, 240, 240)
    out = _erase(image, 0.31 * s, disc, s / 64)
    size = int(round(0.46 * s))
    key = _tint(glyphs.draw_cell('down', 'arrow:down', 'keyboard', size, size), lit)
    out.alpha_composite(key, (int(round(c - size / 2)), int(round(c - size / 2))))
    return out


def _spray(image, label, style):
    """The painted R2 of the spray can: the white stencil painted over with the paint around it,
    then the mouse with its left button (or RT) stencilled in its place."""
    from PIL import Image, ImageDraw
    w, h = image.size
    px = image.load()
    white = [(x, y) for y in range(h) for x in range(w) if px[x, y][3] > 100 and min(px[x, y][:3]) > 185]
    if len(white) < 50:
        return None
    x0, y0 = min(p[0] for p in white), min(p[1] for p in white)
    x1, y1 = max(p[0] for p in white) + 1, max(p[1] for p in white) + 1
    paint = _average(image, lambda x, y, p: p[3] > 200 and min(p[:3]) < 160 and max(p[:3]) > 90,
                     (max(0, x0 - 12), max(0, y0 - 12), min(w, x1 + 12), min(h, y1 + 12)), brightest=0.6)
    if not paint:
        return None
    # the stencil and its soft edge (whiter than the paint, 2 pixels around) take the paint
    from PIL import ImageFilter
    lum = sum(paint) / 3
    mask = Image.new('L', image.size, 0)
    mpx = mask.load()
    for y in range(max(0, y0 - 2), min(h, y1 + 2)):
        for x in range(max(0, x0 - 2), min(w, x1 + 2)):
            p = px[x, y]
            if p[3] > 60 and (min(p[:3]) > 150 or sum(p[:3]) / 3 > lum + 12):
                mpx[x, y] = 255
    mask = mask.filter(ImageFilter.MaxFilter(5))
    painted = image.copy()
    painted.paste(Image.new('RGB', image.size, paint), mask=mask)
    painted.putalpha(image.getchannel('A'))
    out = painted
    side = max(x1 - x0, y1 - y0) + 6
    ink = (238, 236, 228)
    if style == 'xbox':
        big = Image.new('RGBA', (side * 4, side * 4), (0, 0, 0, 0))
        draw = ImageDraw.Draw(big)
        font, (a0, b0, a1, b1) = glyphs._text_fit(draw, 'RT', side * 4 * 0.9, side * 4 * 0.62)
        draw.text(((side * 4 - (a1 - a0)) / 2 - a0, (side * 4 - (b1 - b0)) / 2 - b0), 'RT', font=font, fill=ink + (255,))
        symbol = big.resize((side, side), Image.LANCZOS)
    else:
        symbol = _tint(glyphs.draw_cell('r2', label, 'keyboard', side, side), ink)
    out.alpha_composite(symbol, (int(round((x0 + x1 - side) / 2)), int(round((y0 + y1 - side) / 2))))
    return out


def _touchpad(image, label, style):
    """The touchpad of the touchpad prompts with its key (or the View button) on its top-left corner."""
    w, h = image.size
    px = image.load()
    # the pad: the rows and columns mostly dark and opaque (the splatter around it is not)
    dark = [[px[x, y][3] > 180 and sum(px[x, y][:3]) < 240 for x in range(w)] for y in range(h)]
    rows = [y for y in range(h) if sum(dark[y]) > w * 0.25]
    cols = [x for x in range(w) if sum(dark[y][x] for y in range(h)) > h * 0.12]
    if not rows or not cols:
        return None
    x0, y0, x1, y1 = cols[0], rows[0], cols[-1] + 1, rows[-1] + 1
    side = int(round((y1 - y0) * 0.42))
    if style == 'xbox':
        key = glyphs.draw_cell('touchpad', ('view', None), 'xbox', side, side)
    else:
        key = glyphs.draw_cell('touchpad', label, 'keyboard', side, side)
    from PIL import Image, ImageFilter
    shadow = Image.new('RGBA', key.size, (0, 0, 0, 0))
    shadow.putalpha(key.getchannel('A').filter(ImageFilter.MaxFilter(5)))
    out = image.copy()
    at = (int(x0 - side * 0.22), int(y0 - side * 0.22))
    out.alpha_composite(shadow, (max(0, at[0]), max(0, at[1])))
    out.alpha_composite(key, (max(0, at[0]), max(0, at[1])))
    return out


def redraw(image, button, kind, style, labels):
    """The level-0 image of one prompt redrawn for `style`, or None when it stays the game's."""
    if kind == 'touchpad':
        return _touchpad(image, labels.get('touchpad', 'G'), style)
    if kind == 'spray':
        return _spray(image, labels.get(button, 'mouse:left') if style == 'keyboard' else 'RT', style)
    if kind == 'disc':
        return _disc(image, button, labels.get(button, '?') if style == 'keyboard' else XBOX_LETTERS[button], style)
    if style != 'keyboard':  # an Xbox pad has a d-pad too
        return None
    return _dpad(image) if kind == 'dpad' else _small(image)


def write_texture(out, start, width, height, levels, original, edited):
    """Writes the levels of `edited` (level 0, upright) into the file `out` at `start`: the blocks
    that differ from `original` (level 0 as the game has it), each level resized from level 0."""
    from PIL import Image
    layout, _ = mip_layout(width, height, levels)
    written = 0
    for level, (offset, bw, bh, pitch) in enumerate(layout):
        size = (width >> level, height >> level)
        if min(size) < 4:  # levels under 4 pixels stay the game's
            break
        if level:
            new = edited.resize(size, Image.LANCZOS)
            old = original.resize(size, Image.LANCZOS)
        else:
            new, old = edited, original
        new = new.transpose(Image.FLIP_TOP_BOTTOM)  # stored bottom-up
        old = old.transpose(Image.FLIP_TOP_BOTTOM)
        npx, opx = new.load(), old.load()
        for by in range(bh):
            for bx in range(bw):
                block = [npx[bx * 4 + i, by * 4 + j] for j in range(4) for i in range(4)]
                if block == [opx[bx * 4 + i, by * 4 + j] for j in range(4) for i in range(4)]:
                    continue  # unchanged there: the game's own block stays
                out.seek(start + offset + block_offset(bx, by, pitch))
                out.write(encode_bc3(block))
                written += 1
    return written


def patch(source, dest, style, key_map=''):
    """Writes `dest`, a copy of `source` with the prompts redrawn; the number of prompts changed."""
    from PIL import Image
    labels = glyphs.key_labels(key_map)
    with open(source, 'rb') as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        if data[:4] != b'KCAP' or len(data) < 0x98:
            raise glyphs.PackageError('not a KCAP package')
        doff, dsize = struct.unpack_from('<II', data, 0x28)
        jobs = []
        for start, button, size, levels, kind in find(data, doff, dsize):
            layout, _ = mip_layout(*size, levels)
            _, bw, bh, pitch = layout[0]
            original = read_level(data, start, *size, bw, bh, pitch)
            edited = redraw(original, button, kind, style, labels)
            if edited is not None:
                jobs.append((start, size, levels, original, edited))
        if not jobs:
            return 0
        partial = Path(str(dest) + '.part')
        shutil.copyfile(source, partial)
        try:
            with open(partial, 'r+b') as out:
                for start, size, levels, original, edited in jobs:
                    write_texture(out, start, *size, levels, original, edited)
            partial.replace(dest)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    return len(jobs)


def build(game, target, style, key_map=''):
    """{guest path: host copy} of the redrawn prompt packages in `target` (each reused while
    nothing changed); none for a package the game folder lacks or with nothing to redraw."""
    stamp_file = Path(target) / 'prompts.json'
    try:
        stamps = json.loads(stamp_file.read_text())
        stamps = stamps if isinstance(stamps, dict) and 'key' not in stamps else {}
    except (OSError, ValueError):
        stamps = {}
    labels = glyphs.key_labels(key_map) if style == 'keyboard' else {}
    mounts, new_stamps = {}, {}
    for name in PROMPTS:
        source = Path(game) / glyphs.PACKAGES / name
        copy = Path(target) / name
        if not source.is_file():
            copy.unlink(missing_ok=True)
            continue
        stat = source.stat()
        key = hashlib.sha256(f'{VERSION}|{style}|{labels.get("triangle")}|{labels.get("square")}|{labels.get("touchpad")}|'
                             f'{stat.st_size}|{stat.st_mtime_ns}'.encode()).hexdigest()
        stamp = stamps.get(name, {})
        if stamp.get('key') != key or not (copy.is_file() or stamp.get('count') == 0):
            count = patch(source, copy, style, key_map)
            if not count:
                copy.unlink(missing_ok=True)
            stamp = {'key': key, 'count': count}
        new_stamps[name] = stamp
        if copy.is_file():
            mounts[f'/app0/{glyphs.PACKAGES}/{name}'] = str(copy)
    stamp_file.write_text(json.dumps(new_stamps))
    return mounts
