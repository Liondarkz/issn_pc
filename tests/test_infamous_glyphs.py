"""scripts/infamous_glyphs.py on a synthetic language package (no game data)."""
from paths import ROOT
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(ROOT / 'scripts'))
import infamous_glyphs as g

W = H = 64  # the icon atlas


def package(cells):
    """A KCAP with the font set at data 0, a texture object, one icon font and its atlas."""
    doff = 0x200
    items = []  # (size, off, flags)
    data = bytearray(0x2000 + W * H * 4)
    tex_hash = b'TEXHASH!'
    # texture object at 0x100: T# at +0x28, hash at +0x48
    atlas_off = 0x2000
    t = 0x100
    struct.pack_into('<4I', data, t + 0x28, atlas_off, 10 << 20, (W - 1) | ((H - 1) << 14), 13 << 20)
    data[t + 0x48:t + 0x50] = tex_hash
    # font object at 0x400, glyphs at 0x500
    f, glyphs = 0x400, 0x500
    struct.pack_into('<Q', data, 0x40, 0x1F00)        # not a font (out of the glyph checks)
    struct.pack_into('<Q', data, 0x48, f)
    data[f + 0x28:f + 0x30] = tex_hash
    struct.pack_into('<QQ', data, f + 0x38, glyphs, len(cells))
    for i, (code, rect) in enumerate(cells.items()):
        struct.pack_into('<5H', data, glyphs + 32 * i, code, *rect)
    for i in range(W * H):  # a recognisable pattern
        data[atlas_off + 4 * i:atlas_off + 4 * i + 4] = bytes((i & 0xFF, i >> 8 & 0xFF, 7, 255))
    items = [(0x80, 0, 0x60010), (0x60, t, 0x100010), (0x200, f, 0x10), (W * H * 4, atlas_off, 0xA0100)]
    table = bytearray(struct.pack('<6Q', 0, 0, 0, len(items), 0, 0))
    for item in items:
        table += struct.pack('<III', *item)
    header = bytearray(0x98)
    header[:4] = b'KCAP'
    struct.pack_into('<II', header, 0x18, 0x98, len(table))
    struct.pack_into('<II', header, 0x28, doff, len(data))
    out = header + table
    out += bytes(doff - len(out))
    return bytes(out + data), doff + atlas_off


class InfamousGlyphTests(unittest.TestCase):
    def test_tiling_round_trip(self):
        raw = bytes(range(256)) * (W * H * 4 // 256)
        self.assertEqual(g.retile(g.detile(raw, W, H), W, H), raw)
        # the first stored pixel of the first tile is the bottom-left one of the upright image
        up = g.detile(raw, W, H)
        self.assertEqual(up[(H - 1) * W * 4:(H - 1) * W * 4 + 4], raw[:4])

    def test_redraw_changes_only_the_button_cells(self):
        data, atlas = package({0xF083: (0, 0, 16, 16), 0xF087: (16, 0, 24, 16), 0x41: (40, 40, 8, 8)})
        doff, cells, base, w, h = g.icon_font(data)
        self.assertEqual((w, h, cells[0xF083]), (W, H, (0, 0, 16, 16)))
        out = g.redraw(data, 'keyboard')
        self.assertEqual(len(out), len(data))
        self.assertEqual(out[:atlas], data[:atlas])  # everything before the atlas is kept
        before = g.detile(data[atlas:atlas + W * H * 4], W, H)
        after = g.detile(out[atlas:atlas + W * H * 4], W, H)
        changed = {(i // 4) % W // 8 + (i // 4) // W // 8 * 8 for i in range(0, len(before), 4)
                   if before[i:i + 4] != after[i:i + 4]}
        # cells (0..16, 0..16) and (16..40, 0..16): 8-pixel blocks 0-4 of rows 0-1 only
        self.assertTrue(changed)
        self.assertTrue(all(b % 8 < 5 and b // 8 < 2 for b in changed), changed)
        self.assertNotEqual(g.redraw(data, 'xbox'), out)

    def test_not_a_language_package(self):
        with self.assertRaises(g.PackageError):
            g.icon_font(b'KCAP' + bytes(0x200))
        data, _ = package({0x41: (0, 0, 8, 8)})
        with self.assertRaises(g.PackageError):
            g.icon_font(data)

    def test_key_labels_follow_the_key_map(self):
        labels = g.key_labels('cross=Q,circle=Left Shift,r2=K,triangle=Return')
        self.assertEqual((labels['cross'], labels['circle'], labels['triangle']), ('Q', 'SHIFT', 'ENTER'))
        self.assertEqual(labels['r2'], 'mouse:left')  # R2 stays the left mouse button
        self.assertEqual(g.key_labels('')['square'], 'C')

    def test_keyboard_words_for_the_touchpad(self):
        strings = (b'\0u_trainer_smi_push_car\0Press and Hold the Touch Pad Button with both thumbs\0\0\0'
                   b'u_hint_vor_door\0Swipe the Touch Pad to open the door\0'
                   b'u_x\0Press the Touch Pad Button to drain Smoke Sources\0')
        out = g.retext(strings, 'english', g.key_labels(''))
        self.assertEqual(len(out), len(strings))
        self.assertIn(b'\0Hold R + G together (both thumbs)   ', out)
        self.assertIn(b'\0G + an arrow key opens the door     \0', out)
        self.assertIn(b'\0Press G to drain Smoke Sources', out)
        self.assertEqual(out.count(b'\0'), strings.count(b'\0'))  # every string where it was
        self.assertEqual(g.retext(strings, 'french', g.key_labels('')), strings)

    def test_build_mounts_copies(self):
        data, _ = package({0xF080: (0, 0, 16, 16)})
        with tempfile.TemporaryDirectory() as tmp:
            game, out = Path(tmp) / 'game', Path(tmp) / 'out'
            (game / 'art/cache').mkdir(parents=True)
            (game / 'art/cache/lang_english_text.xpps').write_bytes(data)
            (game / 'art/cache/lang_english_text_p.xpps').write_bytes(b'not used')
            mounts = g.build(game, out, 'keyboard')
            self.assertEqual(list(mounts), ['/app0/art/cache/lang_english_text.xpps'])
            copy = Path(mounts['/app0/art/cache/lang_english_text.xpps'])
            self.assertNotEqual(copy.read_bytes(), data)
            self.assertEqual((game / 'art/cache/lang_english_text.xpps').read_bytes(), data)
            stamp = copy.stat().st_mtime_ns
            self.assertEqual(g.build(game, out, 'keyboard'), mounts)  # reused
            self.assertEqual(copy.stat().st_mtime_ns, stamp)
            self.assertEqual(g.build(game, out, 'off'), {})
            self.assertFalse((out / 'glyphs').exists())


if __name__ == '__main__':
    unittest.main()
