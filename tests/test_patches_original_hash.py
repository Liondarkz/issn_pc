"""OriginalHash lines (patch files of the inFAMOUS Second Son shadPS4 build) and the built-in
inFAMOUS Second Son patch file."""
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from paths import ROOT
from patches import (EBOOT_BASE, GAMES, compile_patches, fnv1a64, game_profile, selected_patches)

ISSN = ROOT / 'patches' / 'inFAMOUS_Second_Son.xml'

XML = '''<?xml version="1.0"?>
<Patch>
  <Metadata Title="t" Name="Width" AppVer="01.00" AppElf="eboot.bin" isEnabled="true">
    <PatchList>
      <Line Type="bytes" Address="{a1:#x}" Value="000A0000" OriginalHash="{h1}"/>
      <Line Type="bytes" Address="{a2:#x}" Value="A0050000" OriginalHash="{h2}"/>
    </PatchList>
  </Metadata>
  <Metadata Title="t" Name="NoHash" AppVer="01.00" AppElf="eboot.bin" isEnabled="true">
    <PatchList>
      <Line Type="bytes" Address="{a3:#x}" Value="90"/>
    </PatchList>
  </Metadata>
</Patch>
'''


class OriginalHashTests(unittest.TestCase):
    def setUp(self):
        self.image = bytearray(0x1000)
        struct.pack_into('<II', self.image, 0x100, 1920, 1080)
        self.segments = [(0, len(self.image))]
        self.tmp = tempfile.TemporaryDirectory()
        self.xml = Path(self.tmp.name) / 'p.xml'
        self.xml.write_text(XML.format(a1=EBOOT_BASE + 0x100, a2=EBOOT_BASE + 0x104, a3=EBOOT_BASE + 0x200,
                                       h1=f'{fnv1a64(struct.pack("<I", 1920)):016x}',
                                       h2=f'{fnv1a64(struct.pack("<I", 1080)):016x}'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_known_values_of_the_second_son_file(self):
        # Its resolution lines replace 1920 and 1080: these are their OriginalHash values.
        self.assertEqual(f'{fnv1a64(struct.pack("<I", 1920)):016x}', '0dd91daa965465c8')
        self.assertEqual(f'{fnv1a64(struct.pack("<I", 1080)):016x}', '2aaa2a3cd88034c1')

    def test_matching_bytes_are_patched(self):
        skipped = {}
        writes = compile_patches(self.xml, ['Width', 'NoHash'], None, self.segments, bytes(self.image), skipped)
        self.assertEqual(skipped, {})
        self.assertEqual(writes, [(0x100, bytes.fromhex('000A0000')), (0x104, bytes.fromhex('A0050000')),
                                  (0x200, b'\x90')])

    def test_other_version_skips_the_whole_patch(self):
        struct.pack_into('<I', self.image, 0x104, 720)  # second line no longer matches
        skipped = {}
        writes = compile_patches(self.xml, ['Width', 'NoHash'], None, self.segments, bytes(self.image), skipped)
        self.assertIn('another game version', skipped['Width'])
        self.assertEqual(writes, [(0x200, b'\x90')])  # nothing of Width, not even its first line

    def test_without_skipped_a_mismatch_is_an_error(self):
        struct.pack_into('<I', self.image, 0x100, 1280)
        with self.assertRaises(ValueError):
            compile_patches(self.xml, ['Width'], None, self.segments, bytes(self.image))


class SecondSonFileTests(unittest.TestCase):
    def test_registered_by_title_id_and_title(self):
        for title_id in ('CUSA00004', 'CUSA00223'):
            self.assertEqual(game_profile(title_id)[1], ISSN)
        self.assertEqual(game_profile('CUSA99999', 'inFAMOUS™ Second Son')[1], ISSN)
        self.assertIn(ISSN, [g[1] for g in GAMES])

    def test_every_line_is_checked(self):
        lines = list(ET.parse(ISSN).getroot().iter('Line'))
        self.assertGreater(len(lines), 30)
        for line in lines:
            self.assertRegex(line.get('OriginalHash', ''), r'^[0-9a-f]{16}$')
            self.assertEqual(line.get('Type'), 'bytes')

    def test_default_selection_is_base_mode_safe(self):
        names = selected_patches(ISSN, '01.00', '')
        self.assertEqual(names, ['Anti-freeze guard (collision loop)',
                                 'Bigger light list (needed for native 4K)'])


if __name__ == '__main__':
    unittest.main()


class EbootBaseTests(unittest.TestCase):
    """issn: a patch Address is relative to 0x400000 for a relocatable eboot (segments from 0),
    absolute for one linked at 0x400000 (ET_SCE_EXEC, inFAMOUS Second Son's SDK)."""

    @staticmethod
    def elf(vaddr):
        data = bytearray(64 + 56)
        struct.pack_into('<Q', data, 0x20, 64)
        struct.pack_into('<HH', data, 0x36, 56, 1)
        struct.pack_into('<IIQQQQQQ', data, 64, 1, 5, 0, vaddr, vaddr, 0x1000, 0x1000, 0x1000)
        return bytes(data)

    def test_base(self):
        from patches import eboot_base
        self.assertEqual(eboot_base(self.elf(0)), EBOOT_BASE)
        self.assertEqual(eboot_base(self.elf(0x400000)), 0)

    def test_absolute_addresses_with_base_zero(self):
        image = bytearray(0x402000)
        struct.pack_into('<I', image, 0x401000, 1920)
        with tempfile.TemporaryDirectory() as tmp:
            xml = Path(tmp) / 'p.xml'
            xml.write_text(XML.format(a1=0x401000, a2=0x401004, a3=0x401100,
                                      h1=f'{fnv1a64(struct.pack("<I", 1920)):016x}',
                                      h2=f'{fnv1a64(bytes(4)):016x}'))
            skipped = {}
            writes = compile_patches(xml, ['Width'], None, [(0x400000, 0x402000)], bytes(image), skipped, 0)
            self.assertEqual(skipped, {})
            self.assertEqual(writes[0], (0x401000, bytes.fromhex('000A0000')))
            # The same file read as relative lands outside: a version mismatch, skipped, not fatal.
            skipped = {}
            self.assertEqual(compile_patches(xml, ['Width'], None, [(0x400000, 0x402000)], bytes(image), skipped), [])
            self.assertIn('another game version', skipped['Width'])
