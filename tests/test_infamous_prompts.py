"""scripts/infamous_prompts.py on a synthetic package (no game data)."""
from paths import ROOT
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(ROOT / 'scripts'))
import infamous_prompts as p

from PIL import Image, ImageDraw

TRIANGLE = bytes.fromhex('0e56e2aa9a59c237')
DPAD = bytes.fromhex('8d2f945eed7bd661')


def disc(size, symbol=(70, 150, 240)):
    """A dark disc with a coloured triangle, like the game's subdue prompt."""
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    c = size / 2
    draw.ellipse((c - 0.36 * size, c - 0.36 * size, c + 0.36 * size, c + 0.36 * size), fill=(12, 14, 20, 255))
    draw.polygon([(c, c - 0.2 * size), (c + 0.18 * size, c + 0.14 * size), (c - 0.18 * size, c + 0.14 * size)],
                 fill=symbol + (255,))
    return image


def package(digest=TRIANGLE, size=128, levels=8):
    """A KCAP with one BC3 texture object (the prompt's hash) and its levels."""
    doff, t, base = 0x200, 0x100, 0x400
    _, total = p.mip_layout(size, size, levels)
    texture = io.BytesIO(bytes(total))
    p.write_texture(texture, 0, size, levels, Image.new('RGBA', (size, size), (1, 2, 3, 0)), disc(size))
    data = bytearray(base) + texture.getvalue() + bytes(0x100)
    struct.pack_into('<4I', data, t + 0x28, base, p.BC3 << 20, (size - 1) | ((size - 1) << 14),
                     (p.TEX_2D << 28) | ((levels - 1) << 16) | (p.THIN_1D << 20))
    data[t + 0x48:t + 0x50] = digest
    items = [(0x60, t, 0x100010), (total, base, 0xA0100)]
    table = bytearray(struct.pack('<6Q', 0, 0, 0, len(items), 0, 0))
    for item in items:
        table += struct.pack('<III', *item)
    header = bytearray(0x98)
    header[:4] = b'KCAP'
    struct.pack_into('<II', header, 0x18, 0x98, len(table))
    struct.pack_into('<II', header, 0x28, doff, len(data))
    out = header + table
    out += bytes(doff - len(out))
    return bytes(out + data), doff + base, total


class InfamousPromptTests(unittest.TestCase):
    def test_mip_layout_matches_the_game(self):
        # core_common: the execute disc (128, 8 levels) ends exactly where the subdue disc starts
        layout, total = p.mip_layout(128, 128, 8)
        self.assertEqual([o for o, *_ in layout], [0, 0x4000, 0x5000, 0x5400, 0x5800, 0x5C00, 0x6000, 0x6400])
        self.assertEqual(total, 0x6800)
        self.assertEqual(p.mip_layout(256, 256, 9)[1], 0x16800)
        self.assertEqual(p.mip_layout(64, 64, 7)[1], 0x2800)

    def test_bc3_round_trip(self):
        pixels = [(10 * i, 255 - 12 * i, 40 + 5 * i, 255 - 16 * i) for i in range(16)]
        decoded = Image.frombytes('RGBA', (4, 4), p.encode_bc3(pixels), 'bcn', 3).load()
        for i, want in enumerate(pixels):  # a ramp over four colours: within half a step
            got = decoded[i % 4, i // 4]
            self.assertLess(max(abs(a - b) for a, b in zip(want, got)), 32, (want, got))
        flat = [(200, 100, 50, 128)] * 16
        got = Image.frombytes('RGBA', (4, 4), p.encode_bc3(flat), 'bcn', 3).getpixel((1, 1))
        self.assertLess(max(abs(a - b) for a, b in zip(flat[0], got)), 6)

    def test_patch_redraws_only_the_prompt(self):
        data, start, total = package()
        with tempfile.TemporaryDirectory() as tmp:
            source, dest = Path(tmp) / 'core.xpps', Path(tmp) / 'copy.xpps'
            source.write_bytes(data)
            self.assertEqual(p.patch(source, dest, 'keyboard'), 1)
            out = dest.read_bytes()
            self.assertEqual(len(out), len(data))
            self.assertEqual(out[:start], data[:start])
            self.assertEqual(out[start + total:], data[start + total:])
            layout, _ = p.mip_layout(128, 128, 8)
            _, bw, bh, pitch = layout[0]
            before = p.read_level(data, start, 128, 128, bw, bh, pitch)
            after = p.read_level(out, start, 128, 128, bw, bh, pitch)
            self.assertNotEqual(before.getpixel((64, 64)), after.getpixel((64, 64)))
            self.assertEqual(before.getpixel((2, 2)), after.getpixel((2, 2)))  # outside the disc
            self.assertGreater(after.getpixel((64, 50))[2], 150)  # the key keeps the prompt's blue
            p.patch(source, Path(tmp) / 'xbox.xpps', 'xbox')
            self.assertNotEqual((Path(tmp) / 'xbox.xpps').read_bytes(), out)
            self.assertFalse(Path(str(dest) + '.part').exists())

    def test_xbox_keeps_the_dpad(self):
        data, _, _ = package(DPAD, 256, 9)
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / 'game'
            (game / 'art/cache').mkdir(parents=True)
            (game / 'art/cache' / p.PACKAGE).write_bytes(data)
            target = Path(tmp) / 'out'
            target.mkdir()
            self.assertEqual(p.build(game, target, 'xbox'), {})
            mounts = p.build(game, target, 'keyboard')
            copy = Path(mounts['/app0/art/cache/core_common.sprig.xpps'])
            stamp = copy.stat().st_mtime_ns
            self.assertEqual(p.build(game, target, 'keyboard'), mounts)  # reused
            self.assertEqual(copy.stat().st_mtime_ns, stamp)

    def test_other_packages_are_left_alone(self):
        data, _, _ = package(b'NOTOURS!')
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'core.xpps'
            source.write_bytes(data)
            self.assertEqual(p.patch(source, Path(tmp) / 'copy.xpps', 'keyboard'), 0)
            self.assertFalse((Path(tmp) / 'copy.xpps').exists())


if __name__ == '__main__':
    unittest.main()
