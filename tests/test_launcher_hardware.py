"""launcher/issn_hardware.py: the PC rating and the settings for a goal (no detection here)."""
import sys
import unittest

from paths import ROOT
sys.path.insert(0, str(ROOT / 'launcher'))
import issn_hardware as hw  # noqa: E402


def pc(gpu, cpu, ram=32):
    score, vram = hw.gpu_entry(gpu)
    return hw.Pc(gpu_name=gpu, gpu_score=score, vram_gb=vram, cpu_name=cpu, cpu_score=hw.cpu_score(cpu),
                 ram_gb=ram)


class HardwareTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(hw.gpu_entry('AMD Radeon RX 9060 XT')[0], 160)
        self.assertEqual(hw.gpu_entry('NVIDIA GeForce RTX 5060')[0], 140)
        self.assertEqual(hw.gpu_entry('NVIDIA GeForce RTX 3060 Ti')[0], 125)  # not the 3060
        self.assertLess(hw.gpu_entry('NVIDIA GeForce RTX 4060 Laptop GPU')[0], hw.gpu_entry('NVIDIA GeForce RTX 4060')[0])
        self.assertEqual(hw.gpu_entry('llvmpipe (LLVM 18)'), (0, 0))
        self.assertEqual(hw.cpu_score('AMD Ryzen 7 3700X 8-Core Processor'), 130)
        self.assertEqual(hw.cpu_score('AMD Ryzen 5 1600 Six-Core Processor'), 85)
        self.assertEqual(hw.cpu_score('12th Gen Intel(R) Core(TM) i5-12400F'), 168)
        self.assertEqual(hw.cpu_score('Intel(R) Core(TM) i7-7820HK CPU @ 2.90GHz'), round(105 * hw.LAPTOP_SHARE))
        self.assertEqual(hw.cpu_score('Intel(R) Core(TM) Ultra 7 265K'), 215)
        self.assertEqual(hw.cpu_score('Some ARM chip'), 0)

    def test_reference_pc(self):
        joao = pc('AMD Radeon RX 9060 XT', 'AMD Ryzen 7 3700X 8-Core Processor')
        self.assertTrue(90 <= hw.city_fps(joao) <= 100)  # measured: about 95 in the city
        self.assertEqual(hw.limit(joao), 'cpu')
        self.assertEqual(hw.tier(joao), 'great')
        self.assertTrue(joao.fsr4)
        s, reasons = hw.settings_for(joao, 'balanced')
        self.assertEqual(s['ini']['fps_limit'], '60')
        self.assertEqual(s['app']['anti_aliasing'], 'fsr4')
        self.assertIn(s['app']['render_height'], ('1440', ''))
        self.assertEqual(s['app']['perf_mode'], '')

    def test_slow_processor_gets_the_lower_settings(self):
        old = pc('NVIDIA GeForce RTX 2060', 'AMD Ryzen 5 1600 Six-Core Processor', ram=16)
        self.assertEqual(hw.limit(old), 'cpu')
        s, reasons = hw.settings_for(old, 'smooth')
        self.assertEqual(s['ini']['fps_limit'], '60')  # the processor holds about 60
        self.assertEqual(s['app']['anti_aliasing'], 'game')  # no FSR 4 on NVIDIA
        self.assertIn(('cpu', 60), reasons)
        self.assertNotEqual(s['app']['perf_mode'], '')

    def test_quiet_stays_at_1080(self):
        big = pc('NVIDIA GeForce RTX 4090', 'AMD Ryzen 7 7800X3D 8-Core Processor')
        s, _r = hw.settings_for(big, 'quiet', screen_height=2160)
        self.assertEqual(s['app']['render_height'], '')
        s, _r = hw.settings_for(big, 'balanced', screen_height=2160)
        self.assertEqual(s['app']['render_height'], '2160')

    def test_recommended(self):
        joao = pc('AMD Radeon RX 9060 XT', 'AMD Ryzen 7 3700X 8-Core Processor')
        s, reasons = hw.settings_for(joao, 'recommended', refresh_hz=144)
        self.assertEqual((s['app']['frame_pacing'], s['ini']['fps_limit'], s['ini']['vsync']), ('2', '0', '1'))
        self.assertEqual((s['app']['perf_mode'], s['app']['draw_distance'], s['app']['shader_compile']),
                         ('low', '', ''))
        self.assertIn(('paced', (144, 72)), reasons)
        # 240 Hz: half is 120, more than this processor holds in the city - a third, 80.
        s, _r = hw.settings_for(joao, 'recommended', refresh_hz=240)
        self.assertEqual(s['app']['frame_pacing'], '3')
        # A 60 Hz screen: no pacing (half would be 30), the limit at 60.
        s, _r = hw.settings_for(joao, 'recommended', refresh_hz=60)
        self.assertEqual((s['app']['frame_pacing'], s['ini']['fps_limit']), ('', '60'))

    def test_unknown_pc(self):
        self.assertEqual(hw.tier(hw.Pc()), 'unknown')
        s, _r = hw.settings_for(hw.Pc(), 'balanced')
        self.assertEqual(s['ini']['fps_limit'], '60')


if __name__ == '__main__':
    unittest.main()
