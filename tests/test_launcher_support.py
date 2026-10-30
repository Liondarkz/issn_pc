"""The launcher's support section: the Pix code behind launcher/pix_qr.png."""
import sys
import unittest

from paths import ROOT
sys.path.insert(0, str(ROOT / 'launcher'))
import issn_launcher_win as launcher  # noqa: E402


class PixTest(unittest.TestCase):
    def test_payload_is_a_static_br_code(self):
        code = launcher.pix_payload()
        self.assertTrue(code.startswith('000201' + '2658' + '0014br.gov.bcb.pix' + '0136' + launcher.PIX_KEY))
        self.assertIn('5303986', code)  # BRL
        self.assertNotIn('54', code[code.index('5303986') + 7:code.index('5802BR')])  # no fixed amount
        self.assertEqual(code[-8:-4], '6304')

    def test_crc_is_ccitt_false(self):
        # the CRC of the BR Code spec's own example (Banco Central, Manual do BR Code)
        body = launcher.pix_payload('123e4567-e12b-12d1-a456-426655440000', 'Fulano de Tal', 'BRASILIA')
        self.assertEqual(body[-4:], '1D3D')

    def test_qr_image_ships(self):
        self.assertTrue((ROOT / 'launcher' / 'pix_qr.png').is_file())
        self.assertEqual(launcher.PIX_QR.name, 'pix_qr.png')



class CommunityTest(unittest.TestCase):
    def test_discord_invite(self):
        self.assertTrue(launcher.DISCORD_URL.startswith('https://discord.gg/'))



class UpdateSeedTest(unittest.TestCase):
    def test_update_brings_the_shader_seed(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            new, old = Path(tmp) / 'new', Path(tmp) / 'old'
            seed = new / 'user' / 'cache' / 'issn-seed-0.3.6'
            seed.mkdir(parents=True)
            (old / 'user' / 'cache').mkdir(parents=True)
            (new / 'shader-seed.txt').write_text('issn-seed-0.3.6\n')
            self.assertFalse(launcher.install_shader_seed(new, old))  # no stamp: not a seed
            (seed / 'seed-stamp.bin').write_bytes(b'SSTP')
            self.assertTrue(launcher.install_shader_seed(new, old))
            self.assertTrue((old / 'user' / 'cache' / 'issn-seed-0.3.6' / 'seed-stamp.bin').is_file())
            self.assertFalse(launcher.install_shader_seed(new, old))  # already there: kept



class ReleaseNotesTest(unittest.TestCase):
    def test_markdown_becomes_plain_text(self):
        body = ('## Test build 0.3.5.1\r\n\r\n### Fixes\r\n- **Dark picture** at night: see '
                '[the log](https://example.com/x)\r\n* `user\\last_run.log`\r\n\r\n\r\n![img](a.png)Thanks')
        text = launcher.release_text(body)
        self.assertEqual(text.splitlines()[:4], ['Test build 0.3.5.1', '', 'Fixes',
                                                 '• Dark picture at night: see the log'])
        self.assertIn('• user\\last_run.log', text)
        self.assertNotIn('**', text)
        self.assertTrue(text.endswith('Thanks'))
        self.assertNotIn('\n\n\n', text)
        self.assertTrue(launcher.release_text('a\n' * 3000, limit=100).endswith('…'))


if __name__ == '__main__':
    unittest.main()
