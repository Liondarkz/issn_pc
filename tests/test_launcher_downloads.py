"""launcher/issn_downloads.py: a few members out of a remote zip, through range requests."""
import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from paths import ROOT
sys.path.insert(0, str(ROOT / 'launcher'))
import issn_downloads as dl  # noqa: E402


class FakeResponse(io.BytesIO):
    def __init__(self, data, status, total, url):
        super().__init__(data)
        self.status = status
        self.headers = {'Content-Range': f'bytes 0-0/{total}'}
        self._url = url

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def server(blob, ranges=True):
    requests = []

    def opener(request, timeout=0):
        spec = request.headers.get('Range', '')
        requests.append(spec)
        start, end = (int(x) for x in spec.split('=')[1].split('-'))
        if not ranges:
            return FakeResponse(blob, 200, len(blob), 'https://storage/x.zip')
        return FakeResponse(blob[start:end + 1], 206, len(blob), 'https://storage/x.zip')
    return opener, requests


def make_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class DownloadsTest(unittest.TestCase):
    def setUp(self):
        self.dll = bytes(range(256)) * 9000
        self.big = __import__('random').Random(1).randbytes(6 << 20)  # a member never asked for
        self.blob = make_zip({'SDK/bin/x64/development/a.dll': b'debug build', 'SDK/samples/big.bin': self.big,
                              'SDK/bin/x64/a.dll': self.dll, 'SDK/license.txt': b'terms'})
        self.package = dl.Package('Test', 'https://example.invalid/x.zip',
                                  (('bin/x64/a.dll', 'a.dll', hashlib.sha256(self.dll).hexdigest()),),
                                  'license.txt')

    def test_fetches_only_the_members_asked_for(self):
        opener, requests = server(self.blob)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(dl.missing(self.package, tmp), ['a.dll'])
            seen = []
            fetched = dl.fetch(self.package, tmp, progress=lambda d, t: seen.append((d, t)), opener=opener)
            self.assertEqual((Path(tmp) / 'a.dll').read_bytes(), self.dll)  # not the development one
            self.assertEqual((Path(tmp) / 'Test-license.txt').read_bytes(), b'terms')
            self.assertEqual(dl.missing(self.package, tmp), [])
            self.assertEqual(seen[-1][0], seen[-1][1])
            self.assertFalse(list(Path(tmp).glob('*.part')))
        self.assertLess(fetched, len(self.blob) // 2)  # the big member stayed remote

    def test_a_wrong_checksum_keeps_nothing(self):
        opener, _requests = server(self.blob)
        bad = dl.Package('Test', self.package.url, (('bin/x64/a.dll', 'a.dll', '0' * 64),), '')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OSError):
                dl.fetch(bad, tmp, opener=opener)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_a_missing_member_or_no_ranges_is_an_error(self):
        opener, _requests = server(self.blob)
        absent = dl.Package('Test', self.package.url, (('bin/x64/b.dll', 'b.dll', ''),), '')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OSError):
                dl.fetch(absent, tmp, opener=opener)
            opener, _requests = server(self.blob, ranges=False)
            with self.assertRaises(OSError):
                dl.fetch(self.package, tmp, opener=opener)

    def test_vendor_packages_are_pinned(self):
        for package in (dl.XESS, dl.DLSS):
            self.assertTrue(package.url.startswith('https://github.com/'))
            for _member, _name, sha in package.files:
                self.assertEqual(len(sha), 64)
        self.assertEqual([name for _m, name, _s in dl.DLSS.files],
                         ['sl.interposer.dll', 'sl.common.dll', 'sl.dlss.dll', 'sl.pcl.dll', 'nvngx_dlss.dll'])


if __name__ == '__main__':
    unittest.main()
