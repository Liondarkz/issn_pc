from paths import ROOT
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import content_profile


def make_sfo(values):
    """A minimal param.sfo built here (text and 32-bit values), never one from a dump."""
    keys, data, index = b'', b'', b''
    for name, value in values.items():
        if isinstance(value, int):
            raw, fmt = struct.pack('<I', value), 0x404
        else:
            raw, fmt = value.encode() + b'\0', 0x204
        raw_padded = raw.ljust((len(raw) + 3) & ~3, b'\0')
        index += struct.pack('<HHIII', len(keys), fmt, len(raw), len(raw_padded), len(data))
        keys += name.encode() + b'\0'
        data += raw_padded
    keys = keys.ljust((len(keys) + 3) & ~3, b'\0')
    key_start = 20 + len(index)
    return struct.pack('<4sIIII', b'\0PSF', 0x101, key_start, key_start + len(keys), len(values)) + index + keys + data


def make_addon(root, name, content_id, place='Sc0', category='ac', license='license.dat', title_id=None):
    folder = root / name / place
    folder.mkdir(parents=True)
    values = {'CATEGORY': category, 'CONTENT_ID': content_id, 'FORMAT': 'obs',
              'TITLE_ID': title_id or content_id[7:16], 'TITLE': name}
    (folder / 'param.sfo').write_bytes(make_sfo(values))
    if license:
        (folder / license).write_bytes(b'\0' * 16)
    return root / name

EXE=ROOT/'out/content-test'

class ProfileTests(unittest.TestCase):
    def test_sfo_parameters_and_explicit_trial_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'sce_sys').mkdir();(p/'sce_sys/param.sfo').write_bytes(b'fixture')
            with patch.object(content_profile,'sfo',return_value={'USER_DEFINED_PARAM_1':13,'USER_DEFINED_PARAM_4':0xffffffff}):
                content_profile.prepare(p,p,'trial')
            self.assertEqual(struct.unpack('<8s5I',(p/'content.bin').read_bytes()),(b'G3CONT01',1,13,0,0,0xffffffff))

    def test_invalid_sfo_parameter_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'sce_sys').mkdir();(p/'sce_sys/param.sfo').write_bytes(b'fixture')
            with patch.object(content_profile,'sfo',return_value={'USER_DEFINED_PARAM_1':'13'}):
                with self.assertRaisesRegex(ValueError,'invalid user-defined'):
                    content_profile.prepare(p,p)

class AddonTests(unittest.TestCase):
    def test_dump_layouts_and_every_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # The layout of PKG dumpers: an empty Image0/sce_sys and the files in Sc0.
            (make_addon(root, 'legacy', 'UP9000-CUSA00223_00-UNLOCKCOLEMISSIO') / 'Image0' / 'sce_sys').mkdir(parents=True)
            make_addon(root / 'addcont' / 'CUSA00223', 'jos', 'UP9000-CUSA00223_00-UNLOCKVESTJOS000', place='sce_sys', license='license.info')
            make_addon(root, 'penny', 'UP9000-CUSA00223_00-UNLOCKVESTPENNYA')
            make_addon(root, 'app', 'UP9000-CUSA00223_00-UNLOCKAPPLICATIO')
            make_addon(root, 'eu', 'EP9000-CUSA00004_00-UNLOCKCOLEMISSIO')
            make_addon(root, 'nolicense', 'UP9000-CUSA00223_00-UNLOCKVESTEXIST0', license=None)
            make_addon(root, 'game', 'UP9000-CUSA00223_00-SECONDSONSHIP000', category='gp')
            make_addon(root, 'twice', 'UP9000-CUSA00223_00-UNLOCKCOLEMISSIO')
            found, skipped = content_profile.scan_addons(root, 'CUSA00223', off=['UNLOCKVESTPENNYA'])
            self.assertEqual({a['label']: a['on'] for a in found},
                             {'UNLOCKCOLEMISSIO': True, 'UNLOCKVESTJOS000': True, 'UNLOCKVESTPENNYA': False})
            reasons = {Path(f).name: r for f, r in skipped}
            self.assertIn('not one the game asks for', reasons['app'])
            self.assertIn('add-on for CUSA00004', reasons['eu'])
            self.assertIn('no license', reasons['nolicense'])
            self.assertIn('not an add-on', reasons['game'])
            self.assertIn('already found', reasons['twice'])

    def test_msys_drive_paths_become_windows_paths(self):
        cases = [('/d/Games/[x]-(@y)', 'D:/Games/[x]-(@y)'), ('/c', 'C:/'), ('D:\\Games', 'D:\\Games'), ('/tmp/x', '/tmp/x')]
        for given, wanted in cases:
            self.assertEqual(content_profile.windows_path(given, nt=True), wanted)
        self.assertEqual(content_profile.windows_path('/d/Games', nt=False), '/d/Games')

    def test_single_addon_folder_and_missing_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            one = make_addon(Path(tmp), 'jacket', 'UP9000-CUSA00223_00-UNLOCKCOLEJACKET')
            found, skipped = content_profile.scan_addons(one, 'CUSA00223')
            self.assertEqual([a['label'] for a in found], ['UNLOCKCOLEJACKET'])
            self.assertEqual(skipped, [])
            self.assertEqual(content_profile.scan_addons(Path(tmp) / 'nope', 'CUSA00223')[1][0][1], 'folder not found')

    def test_profile_writes_labels_only_for_addons_turned_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); (p / 'sce_sys').mkdir()
            (p / 'sce_sys/param.sfo').write_bytes(make_sfo({'TITLE_ID': 'CUSA00223', 'USER_DEFINED_PARAM_1': 5}))
            dlc = p / 'dlc'
            make_addon(dlc, 'legacy', 'UP9000-CUSA00223_00-UNLOCKCOLEMISSIO')
            make_addon(dlc, 'iam8bit', 'UP9000-CUSA00223_00-UNLOCKVESTSKELET')
            content_profile.prepare(p, p, 'full', dlc, ['UNLOCKVESTSKELET'])
            data = (p / 'content.bin').read_bytes()
            self.assertEqual(struct.unpack('<8s5II', data[:32]), (b'G3CONT02', 3, 5, 0, 0, 0, 1))
            self.assertEqual(data[32:], b'UNLOCKCOLEMISSIO')
            content_profile.prepare(p, p, 'full', dlc, ['UNLOCKVESTSKELET', 'UNLOCKCOLEMISSIO'])
            self.assertEqual((p / 'content.bin').read_bytes()[:8], b'G3CONT01')
            self.assertEqual(len((p / 'content.bin').read_bytes()), 28)


@unittest.skipUnless(EXE.exists(),'run bash build.sh --test first')
class ContentTests(unittest.TestCase):
    def run_case(self,*args):
        return subprocess.run([str(EXE.resolve()),*args],capture_output=True,text=True,timeout=5)

    def test_lifecycle_and_output_boundaries(self):
        r=self.run_case();self.assertEqual(r.returncode,0,r.stdout+r.stderr)

    def test_missing_profile_does_not_report_loaded(self):
        r=self.run_case('--missing');self.assertEqual(r.returncode,21)
        self.assertIn('needs --content-profile',r.stderr)

    def test_other_modules_are_counted_independently(self):
        r=self.run_case('--unknown');self.assertEqual(r.returncode,0,r.stdout+r.stderr)

    def test_addons_answer_only_their_labels(self):
        r=self.run_case('--addons');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('game asked for add-on UNLOCKCOLEMISSIO: installed',r.stdout)
        self.assertEqual(r.stdout.count('UNLOCKVESTJOS000 (service 0): not installed'),1)
