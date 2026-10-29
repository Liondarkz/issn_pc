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


if __name__ == '__main__':
    unittest.main()
