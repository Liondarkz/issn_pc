"""launcher/issn_downloads.py: OptiScaler from its release .7z into bin\\, set for the port, and out
again. No network: the release archive is written here (LZMA2 + BCJ2, the layout of v0.9.4)."""
import binascii
import hashlib
import io
import json
import lzma
import os
import random
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from unittest import mock

from paths import ROOT
sys.path.insert(0, str(ROOT / 'launcher'))
import issn_downloads as dl  # noqa: E402
import issn_hardware  # noqa: E402
import issn_launcher_win as launcher  # noqa: E402


# ---- a small 7z writer (what 7-Zip makes of a release: text in LZMA2, DLLs through BCJ2) ----

def num(n):
    for k in range(8):
        if n < 1 << (7 * (k + 1)):
            return bytes([(0xFF << (8 - k)) & 0xFF | (n >> (8 * k))]) + n.to_bytes(8, 'little')[:k]
    return b'\xff' + n.to_bytes(8, 'little')


def bits(flags):
    out = bytearray((len(flags) + 7) // 8)
    for i, flag in enumerate(flags):
        if flag:
            out[i // 8] |= 0x80 >> (i % 8)
    return bytes(out)


class RangeEncoder:
    def __init__(self):
        self.low, self.range, self.cache, self.cache_size, self.out = 0, 0xFFFFFFFF, 0, 1, bytearray()

    def bit(self, probs, index, value):
        p = probs[index]
        bound = (self.range >> 11) * p
        if value:
            self.low += bound
            self.range -= bound
            probs[index] = p - (p >> 5)
        else:
            self.range = bound
            probs[index] = p + ((2048 - p) >> 5)
        while self.range < 1 << 24:
            self.range <<= 8
            self.shift_low()

    def shift_low(self):
        if (self.low & 0xFFFFFFFF) < 0xFF000000 or self.low >> 32:
            carry, temp = self.low >> 32, self.cache
            while True:
                self.out.append((temp + carry) & 0xFF)
                temp = 0xFF
                self.cache_size -= 1
                if not self.cache_size:
                    break
            self.cache = (self.low >> 24) & 0xFF
        self.cache_size += 1
        self.low = (self.low & 0x00FFFFFF) << 8

    def finish(self):
        for _ in range(5):
            self.shift_low()
        return bytes(self.out)


def bcj2_encode(data, choose=lambda pos: pos % 3 != 1):
    """7-Zip's BCJ2 x86 filter, encoding: (main, call, jump, rc). choose(pos) picks the jumps
    converted (the others stay in main), so both branches of the decoder run."""
    main, call, jump, rc, probs = bytearray(), bytearray(), bytearray(), RangeEncoder(), [1024] * 258
    i, prev = 0, 0
    while i < len(data):
        b = data[i]
        main.append(b)
        i += 1
        if not ((b & 0xFE) == 0xE8 or (prev == 0x0F and (b & 0xF0) == 0x80)):
            prev = b
            continue
        if i == len(data):
            break  # the last byte: no bit (the decoder stops there)
        index = prev if b == 0xE8 else 256 if b == 0xE9 else 257
        if i + 4 <= len(data) and choose(i):
            rc.bit(probs, index, 1)
            rel = int.from_bytes(data[i:i + 4], 'little')
            (call if b == 0xE8 else jump).extend(((rel + i + 4) & 0xFFFFFFFF).to_bytes(4, 'big'))
            prev = data[i + 3]
            i += 4
        else:
            rc.bit(probs, index, 0)
            prev = b
    return bytes(main), bytes(call), bytes(jump), rc.finish()


def lzma1(data):
    return lzma.compress(data, format=lzma.FORMAT_RAW, filters=[
        {'id': lzma.FILTER_LZMA1, 'dict_size': 1 << 16, 'lc': 3, 'lp': 0, 'pb': 2}])


def lzma2(data):
    return lzma.compress(data, format=lzma.FORMAT_RAW, filters=[{'id': lzma.FILTER_LZMA2, 'dict_size': 1 << 16}])


def coder(method, props=b'', ins=1, outs=1):
    flag = len(method) | (0x10 if (ins, outs) != (1, 1) else 0) | (0x20 if props else 0)
    out = bytes([flag]) + method
    if flag & 0x10:
        out += num(ins) + num(outs)
    if props:
        out += num(len(props)) + props
    return out


LZMA1_PROPS = bytes([93]) + (1 << 16).to_bytes(4, 'little')
LZMA2_PROPS = bytes([8])  # 64 KiB


def make_7z(text_files, binary_files, folders=(), damage=None):
    """A .7z like OptiScaler's: text_files in an LZMA2 folder, binary_files through BCJ2 (call
    and jump in LZMA, main in LZMA2, the range coder stored), `folders` as empty directories."""
    packs, folder_records, unpack_sizes, sub_counts, sub_sizes, crcs = [], [], [], [], [], []
    text = b''.join(text_files.values())
    packs.append(lzma2(text))
    folder_records.append(num(1) + coder(dl.LZMA2, LZMA2_PROPS))
    unpack_sizes.append([len(text)])
    sub_counts.append(len(text_files))
    sub_sizes.append([len(d) for d in text_files.values()])
    crcs += [binascii.crc32(d) for d in text_files.values()]
    binary = b''.join(binary_files.values())
    main, call, jump, rc = bcj2_encode(binary)
    packs += [lzma2(main), rc, lzma1(jump), lzma1(call)]
    folder_records.append(
        num(4) + coder(dl.LZMA1, LZMA1_PROPS) + coder(dl.LZMA1, LZMA1_PROPS) + coder(dl.LZMA2, LZMA2_PROPS)
        + coder(dl.BCJ2, ins=4) + num(3) + num(2) + num(4) + num(0) + num(5) + num(1)
        + num(2) + num(6) + num(1) + num(0))
    unpack_sizes.append([len(call), len(jump), len(main), len(binary)])
    sub_counts.append(len(binary_files))
    sub_sizes.append([len(d) for d in binary_files.values()])
    crcs += [binascii.crc32(d) for d in binary_files.values()]
    header = b'\x01\x04\x06' + num(0) + num(len(packs)) + b'\x09' + b''.join(num(len(p)) for p in packs) + b'\x00'
    header += b'\x07\x0b' + num(len(folder_records)) + b'\x00' + b''.join(folder_records) + b'\x0c'
    header += b''.join(num(s) for sizes in unpack_sizes for s in sizes) + b'\x00'
    header += b'\x08\x0d' + b''.join(num(n) for n in sub_counts) + b'\x09'
    header += b''.join(num(s) for sizes in sub_sizes for s in sizes[:-1])
    header += b'\x0a\x01' + b''.join(struct.pack('<I', c) for c in crcs) + b'\x00\x00'
    names = list(folders) + list(text_files) + list(binary_files)
    empty = [True] * len(folders) + [False] * (len(text_files) + len(binary_files))
    header += b'\x05' + num(len(names))
    if folders:
        header += b'\x0e' + num(len(bits(empty))) + bits(empty)
        header += b'\x0f' + num(len(bits([False] * len(folders)))) + bits([False] * len(folders))
    encoded = b'\x00' + b''.join(n.encode('utf-16-le') + b'\x00\x00' for n in names)
    header += b'\x11' + num(len(encoded)) + encoded + b'\x00\x00'
    body = b''.join(packs)
    if damage is not None:
        body = bytearray(body)
        body[damage] ^= 0x55
        body = bytes(body)
    tail = struct.pack('<QQI', len(body), len(header), binascii.crc32(header))
    return dl.SIGNATURE + b'\x00\x04' + struct.pack('<I', binascii.crc32(tail)) + tail + body + header


def fake_code(size, seed):
    """Bytes with plenty of E8/E9/0F 8x, like x86 code."""
    rnd = random.Random(seed)
    out = bytearray()
    while len(out) < size:
        out += rnd.choice([b'\xe8', b'\xe9', b'\x0f\x84', b'\x0f', b'\x48\x89'])
        out += rnd.randbytes(rnd.randint(0, 6))
    out[-1] = 0xE8  # a jump as the very last byte
    return bytes(out[:size - 1]) + b'\xe8'


INI = '''; -------------------------------------------------------
[Upscalers]
; -------------------------------------------------------
; Select Upscaler for Dx12 games
; xess, fsr21, fsr22, fsr31 (also for FSR4), dlss - Default (auto) is xess
Dx12Upscaler=auto

; Select Upscaler for Vulkan games
; fsr21 (native VK), fsr22 (native VK), fsr31 (native VK), xess (native VK), fsr21_12 (VKon12), fsr31_12 (VKon12, FSR4), dlss - Default (auto) is fsr22
VulkanUpscaler=auto

[Menu]
; Integer value - Default (auto) is 0x2D which equals VK_INSERT/Insert key
ShortcutKey=auto

; Shortcut key for FG enabled/disabled
FGShortcutKey=auto
'''.replace('\n', '\r\n')


def release_7z(ini=INI, dll=b'MZ OptiScaler v0.9.4-final (7534ad0) ' + b'\x90' * 64):
    text = {'!! README_EXTRACT ALL FILES TO GAME FOLDER !!.txt': b'readme', 'OptiScaler.ini': ini.encode(),
            'Licenses/XeSS_LICENSE.txt': b'terms', 'setup_windows.bat': b'@echo off'}
    binary = {'amd_fidelityfx_vk.dll': fake_code(20000, 1), 'D3D12_Optiscaler/D3D12Core.dll': fake_code(3000, 2),
              'OptiScaler.dll': dll + fake_code(9000, 3)}
    return make_7z(text, binary, folders=('D3D12_Optiscaler', 'Licenses')), text, binary


def port_bin(root):
    """A bin\\ with the port's own files, which nothing here may touch."""
    bin_dir = Path(root) / 'bin'
    (bin_dir / 'xess').mkdir(parents=True)
    (bin_dir / 'dlss').mkdir()
    for name in ('issn-probe.exe', 'SDL3.dll', 'xess/libxess.dll', 'dlss/nvngx_dlss.dll'):
        (bin_dir / name).write_bytes(b'port ' + name.encode())
    return bin_dir


def tree(folder):
    return sorted(p.relative_to(folder).as_posix() for p in Path(folder).rglob('*') if p.is_file())


PORT_FILES = ['SDL3.dll', 'dlss/nvngx_dlss.dll', 'issn-probe.exe', 'xess/libxess.dll']


class SevenZipTest(unittest.TestCase):
    def test_reads_lzma2_and_bcj2_folders_and_checks_every_file(self):
        blob, text, binary = release_7z()
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'r.7z'
            archive.write_bytes(blob)
            seen = []
            with dl.SevenZip(archive) as seven:
                self.assertEqual([e.name for e in seven.entries if e.is_dir], ['D3D12_Optiscaler', 'Licenses'])
                seven.extract(lambda e: Path(tmp) / 'x' / e.name, progress=lambda d, t: seen.append((d, t)))
            for name, data in {**text, **binary}.items():
                self.assertEqual((Path(tmp) / 'x' / name).read_bytes(), data, name)
            self.assertEqual(seen[-1][0], seen[-1][1])
            self.assertFalse(list(Path(tmp).rglob('*.part')))

    def test_bcj2_converts_and_keeps_jumps(self):
        data = fake_code(50000, 7)
        main, call, jump, rc = bcj2_encode(data)
        self.assertTrue(call and jump and len(main) < len(data))  # both kinds were converted
        chunks = [main[i:i + 777] for i in range(0, len(main), 777)]  # cuts land anywhere
        self.assertEqual(b''.join(dl._bcj2(iter(chunks), call, jump, rc, len(data))), data)

    def test_a_damaged_archive_is_an_error_and_keeps_nothing(self):
        text, binary = {'a.txt': random.Random(4).randbytes(3000)}, {'b.dll': fake_code(5000, 5)}
        size = len(lzma2(text['a.txt']))
        for damage in (size // 2, size + 200):  # in the LZMA2 text, in the BCJ2 main stream
            with tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / 'r.7z'
                archive.write_bytes(make_7z(text, binary, damage=damage))
                with self.assertRaises(OSError), dl.SevenZip(archive) as seven:
                    seven.extract(lambda e: Path(tmp) / 'x' / e.name)
                kept = [name for name in tree(Path(tmp) / 'x')] if (Path(tmp) / 'x').exists() else []
                self.assertEqual([name for name in kept if (Path(tmp) / 'x' / name).read_bytes()
                                  == {**text, **binary}.get(name)], kept)  # only whole, checked files

    def test_not_a_7z_or_cut_short(self):
        blob, _t, _b = release_7z()
        with tempfile.TemporaryDirectory() as tmp:
            for data in (b'PK\x03\x04 zip', blob[:len(blob) // 2]):
                archive = Path(tmp) / 'r.7z'
                archive.write_bytes(data)
                with self.assertRaises(OSError):
                    dl.SevenZip(archive)

    @unittest.skipUnless(os.environ.get('ISSN_OPTISCALER_7Z'), 'ISSN_OPTISCALER_7Z: a real release to read')
    def test_a_real_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = dl.install(os.environ['ISSN_OPTISCALER_7Z'], port_bin(tmp), 'fsr22', 'real')
            self.assertIn('winmm.dll', files)


class IniTest(unittest.TestCase):
    def test_configure_sets_the_upscaler_and_the_menu_key_and_nothing_else(self):
        out = dl.configure(INI, 'fsr4')
        changed = [(a, b) for a, b in zip(INI.split('\r\n'), out.split('\r\n')) if a != b]
        self.assertEqual(changed, [('VulkanUpscaler=auto', 'VulkanUpscaler=fsr31_12'),
                                   ('ShortcutKey=auto', 'ShortcutKey=0x77')])
        self.assertEqual(dl.ini_get(out, 'Menu', 'FGShortcutKey'), 'auto')

    def test_output_names_follow_the_release(self):
        for output, value in (('fsr4', 'fsr31_12'), ('fsr31', 'fsr31'), ('xess', 'xess'), ('dlss', 'dlss'),
                              ('fsr22', 'fsr22')):
            self.assertEqual(dl.vulkan_upscaler_value(INI, output), value)
        master = INI.replace('fsr31 (native VK)', 'ffx (native VK)').replace('fsr31_12 (VKon12, FSR4)',
                                                                             'ffx_12 (VKon12, FSR4)')
        self.assertEqual(dl.vulkan_upscaler_value(master, 'fsr4'), 'ffx_12')
        self.assertEqual(dl.vulkan_upscaler_value(master, 'fsr31'), 'ffx')

    def test_the_spaced_form_of_its_menu_and_missing_keys(self):
        spaced = '[Upscalers]\nVulkanUpscaler = fsr22\n'
        self.assertEqual(dl.ini_set(spaced, 'upscalers', 'vulkanupscaler', 'xess'), '[Upscalers]\nVulkanUpscaler = xess\n')
        added = dl.ini_set(spaced, 'Menu', 'ShortcutKey', '0x77')
        self.assertEqual(dl.ini_get(added, 'Menu', 'ShortcutKey'), '0x77')
        self.assertEqual(dl.ini_get(added, 'Upscalers', 'VulkanUpscaler'), 'fsr22')
        self.assertEqual(dl.ini_get(dl.ini_set('[A]\nx=1\n\n[B]\n', 'A', 'y', '2'), 'A', 'y'), '2')


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = port_bin(self.root)
        self.blob, self.text, self.binary = release_7z()
        self.archive = self.root / 'release.7z'
        self.archive.write_bytes(self.blob)

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_puts_winmm_and_its_files_beside_the_port(self):
        files = dl.install(self.archive, self.bin, 'fsr22', 'v0.9.4')
        self.assertEqual(sorted(files, key=str.lower), sorted(
            ['winmm.dll', 'OptiScaler.ini', 'amd_fidelityfx_vk.dll', 'D3D12_Optiscaler/D3D12Core.dll',
             'optiscaler-licenses/XeSS_LICENSE.txt'], key=str.lower))
        self.assertEqual(tree(self.bin), sorted(PORT_FILES + files + ['optiscaler.json']))
        self.assertEqual((self.bin / 'winmm.dll').read_bytes(), self.binary['OptiScaler.dll'])
        for name in PORT_FILES:
            self.assertEqual((self.bin / name).read_bytes(), b'port ' + name.encode())
        ini = (self.bin / 'OptiScaler.ini').read_bytes().decode()
        self.assertIn('\r\nVulkanUpscaler=fsr22\r\n', ini)
        self.assertIn('\r\nShortcutKey=0x77\r\n', ini)
        self.assertEqual(dl.optiscaler_state(self.bin),
                         {'version': 'v0.9.4', 'by_hand': False, 'upscaler': 'fsr22', 'menu_key': '0x77',
                          'foreign': False})
        self.assertFalse((self.bin / '.optiscaler-new').exists())

    def test_remove_takes_out_everything_it_put_and_what_optiscaler_wrote(self):
        dl.install(self.archive, self.bin, 'fsr4', 'v0.9.4')
        (self.bin / 'OptiScaler.log').write_text('log')
        removed = dl.remove(self.bin)
        self.assertIn('winmm.dll', removed)
        self.assertIn('OptiScaler.log', removed)
        self.assertEqual(tree(self.bin), PORT_FILES)
        self.assertEqual(sorted(p.name for p in self.bin.iterdir()), ['SDL3.dll', 'dlss', 'issn-probe.exe', 'xess'])
        self.assertIsNone(dl.optiscaler_state(self.bin))

    def test_a_copy_unpacked_by_hand_is_recognised_and_removed(self):
        with dl.SevenZip(self.archive) as seven:  # what João did: everything into bin\, the DLL renamed
            seven.extract(lambda e: self.bin / ('winmm.dll' if e.name == 'OptiScaler.dll' else e.name))
        state = dl.optiscaler_state(self.bin)
        self.assertEqual((state['version'], state['by_hand'], state['foreign']), ('v0.9.4-final', True, False))
        dl.remove(self.bin)
        self.assertEqual(tree(self.bin), PORT_FILES)

    def test_installing_over_a_copy_by_hand_then_removing_leaves_nothing_of_it(self):
        with dl.SevenZip(self.archive) as seven:
            seven.extract(lambda e: self.bin / ('winmm.dll' if e.name == 'OptiScaler.dll' else e.name))
        dl.install(self.archive, self.bin, 'fsr22', 'v0.9.4')
        self.assertEqual(dl.optiscaler_state(self.bin)['menu_key'], '0x77')
        dl.remove(self.bin)
        self.assertEqual(tree(self.bin), PORT_FILES)

    def test_another_winmm_is_left_alone(self):
        (self.bin / 'winmm.dll').write_bytes(b'MZ something else')
        self.assertTrue(dl.optiscaler_state(self.bin)['foreign'])
        with self.assertRaises(OSError):
            dl.install(self.archive, self.bin, 'fsr22', 'v0.9.4')
        self.assertEqual(dl.remove(self.bin), [])
        self.assertEqual((self.bin / 'winmm.dll').read_bytes(), b'MZ something else')

    def test_reinstall_and_changing_the_output(self):
        dl.install(self.archive, self.bin, 'fsr22', 'v0.9.3')
        dl.install(self.archive, self.bin, 'xess', 'v0.9.4')
        self.assertEqual(dl.optiscaler_state(self.bin)['version'], 'v0.9.4')
        self.assertEqual(dl.set_output(self.bin, 'fsr31'), 'fsr31')
        self.assertEqual(dl.optiscaler_state(self.bin)['upscaler'], 'fsr31')
        self.assertEqual(dl.set_output(self.bin, 'dlss', dlss_dll=self.bin / 'dlss' / 'nvngx_dlss.dll'), 'dlss')
        self.assertEqual((self.bin / 'nvngx_dlss.dll').read_bytes(), b'port dlss/nvngx_dlss.dll')
        self.assertIn('nvngx_dlss.dll', json.loads((self.bin / 'optiscaler.json').read_text())['files'])
        dl.remove(self.bin)
        self.assertEqual(tree(self.bin), PORT_FILES)  # the copy goes, the port's own DLSS stays

    def test_an_unsafe_name_in_the_archive_is_refused(self):
        archive = self.root / 'bad.7z'
        archive.write_bytes(make_7z({'OptiScaler.ini': b'x', '../evil.txt': b'x'}, {'OptiScaler.dll': b'MZ'}))
        with self.assertRaises(OSError):
            dl.install(archive, self.bin, 'fsr22', 'v1')
        self.assertFalse((self.root / 'evil.txt').exists())
        self.assertIsNone(dl.optiscaler_state(self.bin))

    def test_a_release_without_optiscaler_dll_is_refused(self):
        archive = self.root / 'other.7z'
        archive.write_bytes(make_7z({'readme.txt': b'x'}, {'other.dll': b'MZ'}))
        with self.assertRaises(OSError):
            dl.install(archive, self.bin, 'fsr22', 'v1')
        self.assertEqual(tree(self.bin), PORT_FILES)


class FakeResponse(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ReleaseTest(unittest.TestCase):
    def api(self, **release):
        body = json.dumps({'tag_name': 'v0.9.5', 'prerelease': False, 'assets': [
            {'name': 'Optiscaler_0.9.5-final.7z', 'size': 10, 'digest': 'sha256:' + 'ab' * 32,
             'browser_download_url': dl.OPTISCALER_DOWNLOADS + 'v0.9.5/Optiscaler_0.9.5-final.7z'}],
            **release}).encode()
        return lambda request, timeout=0: FakeResponse(body)

    def test_latest_stable_release_and_its_checksum(self):
        release = dl.optiscaler_release(self.api())
        self.assertEqual((release.tag, release.sha256, release.size), ('v0.9.5', 'ab' * 32, 10))
        self.assertTrue(release.url.startswith('https://github.com/optiscaler/OptiScaler/releases/download/'))

    def test_pre_releases_and_files_elsewhere_are_refused(self):
        with self.assertRaises(ValueError):
            dl.optiscaler_release(self.api(prerelease=True))
        with self.assertRaises(ValueError):
            dl.optiscaler_release(self.api(assets=[{'name': 'Optiscaler.7z', 'digest': 'sha256:' + '0' * 64,
                                                    'browser_download_url': 'https://example.invalid/Optiscaler.7z'}]))

    def test_the_known_release_is_pinned(self):
        known = dl.OPTISCALER_KNOWN
        self.assertTrue(known.url.startswith(dl.OPTISCALER_DOWNLOADS))
        self.assertEqual(len(known.sha256), 64)

    def test_download_checks_the_file(self):
        data = b'7z' * 1000
        opener = lambda request, timeout=0: FakeResponse(data)  # noqa: E731
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'o.7z'
            good = dl.Release('v1', 'https://x', hashlib.sha256(data).hexdigest(), len(data))
            seen = []
            dl.download(good, target, progress=lambda d, t: seen.append((d, t)), opener=opener)
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(seen[-1], (len(data), len(data)))
            target.unlink()
            for bad in (dl.Release('v1', 'https://x', '0' * 64, 0), dl.Release('v1', 'https://x', '', 0)):
                with self.assertRaises(OSError):
                    dl.download(bad, target, opener=opener)
                self.assertEqual(list(Path(tmp).iterdir()), [])


class LauncherTest(unittest.TestCase):
    """The launcher's side: what it offers, and the line at the top of last_run.log."""

    def test_outputs_follow_the_graphics_card(self):
        def keys(name):
            return [k for k, _t in launcher.optiscaler_choices(issn_hardware.Pc(gpu_name=name) if name else None)]
        self.assertEqual(keys('AMD Radeon RX 9060 XT'), ['fsr4', 'fsr31', 'xess', 'fsr22'])
        self.assertEqual(keys('NVIDIA GeForce RTX 4070'), ['fsr31', 'xess', 'dlss', 'fsr22'])
        self.assertEqual(keys('NVIDIA GeForce GTX 1660'), ['fsr31', 'xess', 'fsr22'])
        self.assertEqual(keys('Intel(R) Arc(TM) A770'), ['fsr31', 'xess', 'fsr22'])
        self.assertEqual(keys(''), ['fsr4', 'fsr31', 'xess', 'fsr22'])  # not checked yet
        self.assertEqual(launcher.APP_DEFAULTS['optiscaler_output'], 'fsr22')
        self.assertEqual(set(dict(launcher.OPTISCALER_CHOICES)),
                         set(dl.OPTISCALER_OUTPUTS))

    def test_outputs_are_shown_by_their_names(self):
        for value, name in (('fsr31_12', 'FSR 4'), ('ffx_12', 'FSR 4'), ('FFX', 'FSR 3.1'), ('fsr31', 'FSR 3.1'),
                            ('xess', 'XeSS'), ('dlss', 'DLSS'), ('fsr22', 'FSR 2.2'), ('fsr21', 'fsr21'),
                            (None, '?')):
            self.assertEqual(launcher.optiscaler_output_name(value), name, value)

    def test_the_log_says_when_optiscaler_is_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = port_bin(tmp)
            archive = Path(tmp) / 'r.7z'
            archive.write_bytes(release_7z()[0])
            with mock.patch.object(launcher, 'WINDOWS', True), \
                    mock.patch.object(launcher, 'optiscaler_bin', return_value=bin_dir):
                self.assertEqual(launcher.optiscaler_log_line({'anti_aliasing': 'xess'}), '')
                dl.install(archive, bin_dir, 'fsr4', 'v0.9.4')
                line = launcher.optiscaler_log_line({'anti_aliasing': 'xess'})
                self.assertEqual(line, 'OptiScaler v0.9.4 installed by the launcher: bin\\winmm.dll, '
                                       'VulkanUpscaler=fsr31_12, menu key 0x77 (OptiScaler.ini)\n')
                self.assertIn('not XeSS: OptiScaler gets no input', launcher.optiscaler_log_line({'anti_aliasing': 'fsr4'}))
                dl.remove(bin_dir)
                (bin_dir / 'winmm.dll').write_bytes(b'MZ other')
                self.assertIn('another program', launcher.optiscaler_log_line({}))
        with mock.patch.object(launcher, 'WINDOWS', False):
            self.assertEqual(launcher.optiscaler_log_line({}), '')

    def test_play_writes_the_line_first(self):
        owner = launcher.Launcher.__new__(launcher.Launcher)
        owner.process = None
        owner.app = {**launcher.APP_DEFAULTS, 'game_dir': 'synthetic-game', 'anti_aliasing': 'game'}
        for name in ('collect', 'offer_system_files', 'set_log', 'play_button', 'status', 'append',
                     'offer_optiscaler_xess'):
            setattr(owner, name, mock.Mock(return_value=True))
        log = mock.Mock()
        with mock.patch.object(launcher, 'game_info', return_value=('inFAMOUS Second Son', '01.07')), \
                mock.patch.object(launcher, 'missing_game_files', return_value=[]), \
                mock.patch.object(launcher, 'open_play_log', return_value=log), \
                mock.patch.object(launcher, 'optiscaler_log_line', return_value='OptiScaler v0.9.4 installed\n'), \
                mock.patch.object(launcher.subprocess, 'Popen', return_value=mock.Mock()), \
                mock.patch.object(launcher, 'GameJob'), \
                mock.patch.object(launcher.threading, 'Thread'):
            owner.play()
        log.write.assert_called_once_with('OptiScaler v0.9.4 installed\n')
        owner.offer_optiscaler_xess.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
