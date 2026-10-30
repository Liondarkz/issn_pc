# SPDX-License-Identifier: GPL-2.0-or-later
"""The vendors' upscaler files the port cannot ship (Intel's libxess.dll, NVIDIA's Streamline and
DLSS DLLs), fetched by the launcher straight from the vendors' own GitHub releases.

Only the files needed are read out of each release zip: the zip's directory and those members are
fetched with HTTP range requests, so XeSS costs ~60 MB of the 73 MB zip and DLSS ~33 MB
of 263 MB. Each file is checked against the SHA-256 of that release before it is kept.

No Tk here (tests/test_launcher_downloads.py).
"""
import hashlib
import io
import os
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = 'issn-launcher'


@dataclass(frozen=True)
class Package:
    name: str      # shown to the player
    url: str       # the vendor's release zip
    files: tuple   # (path inside the zip, file name in the folder, SHA-256 or '')
    license: str   # a member kept next to the files (the vendor's terms), or ''


# Intel XeSS SDK 3.0.2 (Intel Simplified Software License: use and redistribution allowed).
XESS = Package(
    'XeSS', 'https://github.com/intel/xess/releases/download/v3.0.2/XeSS_SDK_3.0.2.zip',
    (('bin/libxess.dll', 'libxess.dll',
      '251659dd84a3e84de67c886a4186e01f3eca49b00641906fe38bb6b807e5d5b7'),),
    'LICENSE.txt')
# NVIDIA Streamline SDK 2.14.16 (the version the port is built against, vk_dlss.cpp).
DLSS = Package(
    'DLSS', 'https://github.com/NVIDIA-RTX/Streamline/releases/download/v2.14.16/streamline-sdk-v2.14.16.zip',
    (('bin/x64/sl.interposer.dll', 'sl.interposer.dll',
      'b1d7395aebd7b9722841d64dcf7433680020b6a8e421f851c16d5ba0c2182edf'),
     ('bin/x64/sl.common.dll', 'sl.common.dll',
      '38eea3b4209afc455636ea372b5933d65fcc13becec6c84d101dcfc4d8648f75'),
     ('bin/x64/sl.dlss.dll', 'sl.dlss.dll',
      '18871eea87c90345da97d8109faa81ea7d5840e8d99b51b55b0e46cfa177f84e'),
     ('bin/x64/sl.pcl.dll', 'sl.pcl.dll',
      '5698c38e722318681f215c9fe68e45b9bedd51378acc699d8eee26e961e57770'),
     ('bin/x64/nvngx_dlss.dll', 'nvngx_dlss.dll',
      'd00b5b0ab29c3ca101b70c3a5ecb3a01b7ef33766a3383a28a18623083e6123e')),
    'bin/x64/nvngx_dlss.license.txt')


class RangeFile(io.RawIOBase):
    """A read-only, seekable file over HTTP range requests (what zipfile needs to read a
    directory and a few members of a remote zip)."""

    BLOCK = 1 << 20

    def __init__(self, url, opener=None):
        super().__init__()
        self.opener = opener or urllib.request.urlopen
        self.position = 0
        self.cache_start, self.cache = -1, b''
        # The release link redirects to a signed storage URL: resolve it once.
        request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Range': 'bytes=0-0'})
        with self.opener(request, timeout=60) as response:
            self.url = response.geturl()
            total = response.headers.get('Content-Range', '').rpartition('/')[2]
            if response.status != 206 or not total.isdigit():
                raise OSError('the server does not send parts of the file')
            self.size = int(total)
        self.fetched = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.position, io.SEEK_END: self.size}[whence]
        self.position = max(0, base + offset)
        return self.position

    def _get(self, start, end):
        request = urllib.request.Request(self.url, headers={'User-Agent': USER_AGENT,
                                                            'Range': f'bytes={start}-{end - 1}'})
        with self.opener(request, timeout=60) as response:
            data = response.read()
        if len(data) != end - start:
            raise OSError('short read from the server')
        self.fetched += len(data)
        return data

    def readinto(self, buffer):
        want = min(len(buffer), self.size - self.position)
        if want <= 0:
            return 0
        start, end = self.position, self.position + want
        if not (self.cache_start <= start and end <= self.cache_start + len(self.cache)):
            if want >= self.BLOCK:
                data = self._get(start, end)  # a large read goes straight through
                buffer[:want] = data
                self.position = end
                return want
            block_end = min(self.size, start + self.BLOCK)
            self.cache_start, self.cache = start, self._get(start, block_end)
        offset = start - self.cache_start
        buffer[:want] = self.cache[offset:offset + want]
        self.position = end
        return want


def missing(package, folder):
    """The package's files not yet in `folder`."""
    return [name for _member, name, _sha in package.files if not (Path(folder) / name).is_file()]


def _member(archive, path):
    """The zip entry ending in `path` (the SDK zips may have a top folder), case-insensitive."""
    want = path.lower().strip('/')
    for info in archive.infolist():
        name = info.filename.lower()
        if name == want or name.endswith('/' + want):
            return info
    return None


def fetch(package, folder, progress=None, opener=None):
    """Puts the package's files into `folder` from the vendor's release. progress(done, total) is
    called with bytes of the files written. Raises OSError (network, a file missing from the
    release, a checksum that does not match)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    remote = RangeFile(package.url, opener)
    with zipfile.ZipFile(io.BufferedReader(remote, buffer_size=RangeFile.BLOCK)) as archive:
        wanted = []
        for path, name, sha in package.files:
            info = _member(archive, path)
            if info is None:
                raise OSError(f'{path} is not in the {package.name} download')
            wanted.append((info, name, sha))
        if package.license:
            info = _member(archive, package.license)
            if info is not None:
                wanted.append((info, f'{package.name}-{Path(package.license).name}', ''))
        total, done = sum(info.file_size for info, _n, _s in wanted), 0
        for info, name, sha in wanted:
            target = folder / name
            part = target.with_name(target.name + '.part')
            digest = hashlib.sha256()
            with archive.open(info) as source, open(part, 'wb') as out:
                while chunk := source.read(1 << 20):
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
            if sha and digest.hexdigest() != sha:
                part.unlink(missing_ok=True)
                raise OSError(f'{name}: the download does not match the {package.name} release')
            os.replace(part, target)
    return remote.fetched
