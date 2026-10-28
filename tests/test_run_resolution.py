from paths import ROOT
import sys
import unittest

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import run
import patches


class InfamousResolutionTests(unittest.TestCase):
    def test_render_lines(self):
        self.assertEqual(run.infamous_render_lines({}, []), 1080)
        self.assertEqual(run.infamous_render_lines({'ISSN_RENDER_HEIGHT': '2160'}, []), 2160)
        self.assertEqual(run.infamous_render_lines({'ISSN_RENDER_SCALE': '67'}, []), 724)
        self.assertEqual(run.infamous_render_lines({}, ['Native 2560x1440 (render and output)']), 1440)

    def test_direct_memory_above_1080(self):
        self.assertEqual(run.infamous_dmem_mb({}, []), 0)
        self.assertEqual(run.infamous_dmem_mb({'ISSN_RENDER_SCALE': '50'}, []), 0)
        self.assertEqual(run.infamous_dmem_mb({'ISSN_RENDER_HEIGHT': '2160'}, []), 5056 + 4096)
        self.assertEqual(run.infamous_dmem_mb({}, ['Native 3840x2160 (render and output)']), 5056 + 4096)
        self.assertTrue(5056 < run.infamous_dmem_mb({'ISSN_RENDER_HEIGHT': '1440'}, []) < 5056 + 4096)

    def test_segment_page_tail(self):
        # A segment ends at the next 4 KiB page (inFAMOUS 1.07's code at 0xcc55b8 -> 0xcc6000).
        import struct
        elf = bytearray(0x40 + 56)
        struct.pack_into('<Q', elf, 0x20, 0x40)
        struct.pack_into('<HH', elf, 0x36, 56, 1)
        struct.pack_into('<IIQQQQQQ', elf, 0x40, 1, 5, 0, 0, 0, 0x5b8, 0x5b8, 0x4000)
        self.assertEqual(patches.eboot_segments(bytes(elf)), [(0, 0x1000)])
        self.assertEqual(len(patches.eboot_image(bytes(elf))), 0x1000)


if __name__ == '__main__':
    unittest.main()
