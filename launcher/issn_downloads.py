# SPDX-License-Identifier: GPL-2.0-or-later
"""The vendors' upscaler files the port cannot ship (Intel's libxess.dll, NVIDIA's Streamline and
DLSS DLLs), fetched by the launcher straight from the vendors' own GitHub releases.

Only the files needed are read out of each release zip: the zip's directory and those members are
fetched with HTTP range requests, so XeSS costs ~60 MB of the 73 MB zip and DLSS ~33 MB
of 263 MB. Each file is checked against the SHA-256 of that release before it is kept.

OptiScaler (optiscaler/OptiScaler, GPL-3.0, a separate program the player may add): the newest
stable release's .7z is downloaded whole, read with the 7z reader below (standard library only:
LZMA, LZMA2 and 7-Zip's BCJ2 filter, what its releases use) and put into bin\\ beside
issn-probe.exe, its OptiScaler.dll as winmm.dll. optiscaler.json there lists what was put, for the
Remove button.

No Tk here (tests/test_launcher_downloads.py, tests/test_launcher_optiscaler.py).
"""
import binascii
import hashlib
import io
import json
import lzma
import os
import re
import shutil
import struct
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


# ---------------------------------------------------------------------------------------------
# A .7z reader (no encryption, no multi-volume): LZMA, LZMA2, BCJ2 and stored data, which is what
# OptiScaler's releases use (v0.9.4: the text files in an LZMA2 block, the DLLs in an
# LZMA2 + LZMA + BCJ2 block). Python has no 7z module and the launcher adds no packages.

SIGNATURE = b'7z\xbc\xaf\x27\x1c'
COPY, LZMA1, LZMA2, BCJ2 = b'\x00', b'\x03\x01\x01', b'\x21', b'\x03\x03\x01\x1b'
CHUNK = 8 << 20


class Bytes:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def byte(self):
        if self.pos >= len(self.data):
            raise OSError('7z: the header ends too soon')
        self.pos += 1
        return self.data[self.pos - 1]

    def take(self, n):
        if self.pos + n > len(self.data):
            raise OSError('7z: the header ends too soon')
        self.pos += n
        return self.data[self.pos - n:self.pos]

    def number(self):
        first, mask, value = self.byte(), 0x80, 0
        for i in range(8):
            if not first & mask:
                return value | ((first & (mask - 1)) << (8 * i))
            value |= self.byte() << (8 * i)
            mask >>= 1
        return value

    def bits(self, count):
        out, byte, mask = [], 0, 0
        for _ in range(count):
            if not mask:
                byte, mask = self.byte(), 0x80
            out.append(bool(byte & mask))
            mask >>= 1
        return out

    def defined(self, count):
        return [True] * count if self.byte() else self.bits(count)


class Folder:
    def __init__(self):
        self.coders = []      # (method id, in streams, out streams, properties)
        self.bonds = []       # (in index, out index)
        self.packed = []      # in indices fed by packed streams, in pack order
        self.sizes = []       # unpacked size of every out stream
        self.crc = None


def _folder(r):
    folder = Folder()
    for _ in range(r.number()):
        flag = r.byte()
        method = r.take(flag & 0x0f)
        ins, outs = (r.number(), r.number()) if flag & 0x10 else (1, 1)
        props = r.take(r.number()) if flag & 0x20 else b''
        if flag & 0x80:
            raise OSError('7z: alternative methods are not supported')
        folder.coders.append((method, ins, outs, props))
    total_in = sum(c[1] for c in folder.coders)
    total_out = sum(c[2] for c in folder.coders)
    folder.bonds = [(r.number(), r.number()) for _ in range(total_out - 1)]
    count = total_in - len(folder.bonds)
    if count == 1:
        bound = {i for i, _o in folder.bonds}
        folder.packed = [i for i in range(total_in) if i not in bound]
    else:
        folder.packed = [r.number() for _ in range(count)]
    return folder


def _streams(r):
    """StreamsInfo -> (pack position, pack sizes, folders, per-folder substream sizes and CRCs)."""
    pack_pos, pack_sizes, folders = 0, [], []
    sub_counts, sub_sizes, sub_crcs = None, None, None
    while True:
        kind = r.byte()
        if kind == 0x00:
            break
        if kind == 0x06:  # PackInfo
            pack_pos = r.number()
            count = r.number()
            while (k := r.byte()) != 0x00:
                if k == 0x09:
                    pack_sizes = [r.number() for _ in range(count)]
                elif k == 0x0a:
                    defined = r.defined(count)
                    [r.take(4) for d in defined if d]
                else:
                    raise OSError(f'7z: unknown pack property {k}')
        elif kind == 0x07:  # UnpackInfo
            if r.byte() != 0x0b:
                raise OSError('7z: folders expected')
            count = r.number()
            if r.byte():
                raise OSError('7z: external folders are not supported')
            folders = [_folder(r) for _ in range(count)]
            if r.byte() != 0x0c:
                raise OSError('7z: unpack sizes expected')
            for folder in folders:
                folder.sizes = [r.number() for _ in range(sum(c[2] for c in folder.coders))]
            while (k := r.byte()) != 0x00:
                if k == 0x0a:
                    defined = r.defined(len(folders))
                    for folder, d in zip(folders, defined):
                        if d:
                            folder.crc = struct.unpack('<I', r.take(4))[0]
                else:
                    raise OSError(f'7z: unknown unpack property {k}')
        elif kind == 0x08:  # SubStreamsInfo
            sub_counts = [1] * len(folders)
            while (k := r.byte()) != 0x00:
                if k == 0x0d:
                    sub_counts = [r.number() for _ in folders]
                elif k == 0x09:
                    sub_sizes = []
                    for folder, n in zip(folders, sub_counts):
                        if not n:
                            sub_sizes.append([])
                            continue
                        sizes = [r.number() for _ in range(n - 1)]
                        sizes.append(_final_size(folder) - sum(sizes))
                        sub_sizes.append(sizes)
                elif k == 0x0a:
                    unknown = sum(n for f, n in zip(folders, sub_counts) if not (n == 1 and f.crc is not None))
                    defined = iter(r.defined(unknown))
                    sub_crcs = []
                    for folder, n in zip(folders, sub_counts):
                        if n == 1 and folder.crc is not None:
                            sub_crcs.append([folder.crc])
                        else:
                            sub_crcs.append([struct.unpack('<I', r.take(4))[0] if next(defined) else None
                                             for _ in range(n)])
                else:
                    raise OSError(f'7z: unknown substream property {k}')
        else:
            raise OSError(f'7z: unknown streams property {kind}')
    if sub_counts is None:
        sub_counts = [1] * len(folders)
    if sub_sizes is None:
        sub_sizes = [[_final_size(f)] if n else [] for f, n in zip(folders, sub_counts)]
    if sub_crcs is None:
        sub_crcs = [[f.crc] if n == 1 else [None] * n for f, n in zip(folders, sub_counts)]
    return pack_pos, pack_sizes, folders, sub_sizes, sub_crcs


def _final_size(folder):
    bound = {o for _i, o in folder.bonds}
    outs = [i for i in range(len(folder.sizes)) if i not in bound]
    return folder.sizes[outs[0]]


def _files(r):
    count = r.number()
    empty_stream, empty_file, names = [False] * count, [], [''] * count
    while (kind := r.byte()) != 0x00:
        data = Bytes(r.take(r.number()))
        if kind == 0x0e:
            empty_stream = data.bits(count)
        elif kind == 0x0f:
            empty_file = data.bits(sum(empty_stream))
        elif kind == 0x11:
            if data.byte():
                raise OSError('7z: external names are not supported')
            names = data.data[1:].decode('utf-16-le').split('\0')[:count]
    empty_file = iter(empty_file + [False] * count)
    return [(name, empty, empty and not next(empty_file))
            for name, empty in zip(names, empty_stream)]  # (name, no data, is a folder)


class Entry:
    def __init__(self, name, size, crc, is_dir, folder):
        self.name, self.size, self.crc, self.is_dir, self.folder = name.replace('\\', '/'), size, crc, is_dir, folder


def _lzma_filters(method, props, size):
    limit = max(4096, min(size, 1 << 30))
    if method == LZMA2:
        p = props[0] if props else 0
        dictionary = 0xFFFFFFFF if p >= 40 else (2 | (p & 1)) << (p // 2 + 11)
        return [{'id': lzma.FILTER_LZMA2, 'dict_size': max(4096, min(dictionary, limit))}]
    d = props[0]
    dictionary = struct.unpack('<I', props[1:5])[0]
    return [{'id': lzma.FILTER_LZMA1, 'lc': d % 9, 'lp': d // 9 % 5, 'pb': d // 45,
             'dict_size': max(4096, min(dictionary, limit))}]


def _lzma(source, filters, size):
    decoder = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filters)
    left = size

    def take(out):
        nonlocal left
        out = out[:left]
        left -= len(out)
        return out
    for data in source:
        out = decoder.decompress(data, CHUNK)
        if out:
            yield take(out)
        while left and not decoder.needs_input and not decoder.eof:
            out = decoder.decompress(b'', CHUNK)
            if not out:
                break
            yield take(out)
        if not left:
            return
    while left and not decoder.eof:
        out = decoder.decompress(b'', CHUNK)
        if not out:
            break
        yield take(out)
    if left:
        raise OSError('7z: a stream ends too soon')


_JUMP = re.compile(rb'[\xe8\xe9]|\x0f[\x80-\x8f]')


def _bcj2(main, call, jump, rc, size):
    """7-Zip's BCJ2 x86 branch converter (the 0303011B method), decoding."""
    if len(rc) < 5:
        raise OSError('7z: BCJ2 range coder stream too short')
    code, rng, rpos = int.from_bytes(rc[1:5], 'big'), 0xFFFFFFFF, 5
    probs = [1024] * 258
    cpos = jpos = 0
    done, prev = 0, 0
    search = _JUMP.search
    for buf in main:
        n, pos, pieces = len(buf), 0, []
        while pos < n:
            b = buf[pos]
            if (b & 0xFE) == 0xE8 or (prev == 0x0F and (b & 0xF0) == 0x80):
                j, before = pos, prev
            else:
                m = search(buf, pos)
                if m is None:
                    pieces.append(buf[pos:])
                    done += n - pos
                    prev = buf[n - 1]
                    break
                j = m.start()
                if buf[j] == 0x0F:
                    j += 1
                before = buf[j - 1]
                b = buf[j]
            pieces.append(buf[pos:j + 1])
            done += j + 1 - pos
            pos = j + 1
            if done >= size:
                break
            index = before if b == 0xE8 else 256 if b == 0xE9 else 257
            ttt = probs[index]
            bound = (rng >> 11) * ttt
            if code < bound:
                rng = bound
                probs[index] = ttt + ((2048 - ttt) >> 5)
                prev = b
            else:
                rng -= bound
                code -= bound
                probs[index] = ttt - (ttt >> 5)
                if b == 0xE8:
                    src = call[cpos:cpos + 4]
                    cpos += 4
                else:
                    src = jump[jpos:jpos + 4]
                    jpos += 4
                if len(src) != 4:
                    raise OSError('7z: BCJ2 call stream too short')
                dest = (int.from_bytes(src, 'big') - done - 4) & 0xFFFFFFFF
                pieces.append(dest.to_bytes(4, 'little'))
                done += 4
                prev = dest >> 24
            if rng < 0x1000000:
                if rpos >= len(rc):
                    raise OSError('7z: BCJ2 range coder stream too short')
                rng = (rng << 8) & 0xFFFFFFFF
                code = ((code << 8) | rc[rpos]) & 0xFFFFFFFF
                rpos += 1
        if pieces:
            yield b''.join(pieces)
        if done >= size:
            return
    if done < size:
        raise OSError('7z: BCJ2 main stream too short')


class SevenZip:
    """The files of a .7z archive (no encryption, no multi-volume): entries, then extract()."""

    def __init__(self, path):
        self.file = open(path, 'rb')
        try:
            self._read_header()
        except (struct.error, UnicodeDecodeError, IndexError, lzma.LZMAError) as error:
            self.file.close()
            raise OSError(f'7z: unreadable archive ({error})') from error
        except BaseException:
            self.file.close()
            raise

    def close(self):
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def _read_header(self):
        start = self.file.read(32)
        if len(start) != 32 or start[:6] != SIGNATURE:
            raise OSError('not a 7z archive')
        if binascii.crc32(start[12:32]) != struct.unpack('<I', start[8:12])[0]:
            raise OSError('7z: damaged start header')
        offset, size, crc = struct.unpack('<QQI', start[12:32])
        self.file.seek(32 + offset)
        header = self.file.read(size)
        if len(header) != size or binascii.crc32(header) != crc:
            raise OSError('7z: damaged header (incomplete download?)')
        r = Bytes(header)
        kind = r.byte()
        while kind == 0x17:  # the header itself is packed
            pack_pos, pack_sizes, folders, _sizes, _crcs = _streams(r)
            if not folders:
                raise OSError('7z: empty packed header')
            data = b''.join(self._folder_output(folders[0], 32 + pack_pos, pack_sizes))
            if folders[0].crc is not None and binascii.crc32(data) != folders[0].crc:
                raise OSError('7z: damaged packed header')
            r = Bytes(data)
            kind = r.byte()
        if kind != 0x01:
            raise OSError('7z: no header')
        self.pack_pos, self.pack_sizes, self.folders = 0, [], []
        sub_sizes, sub_crcs, files = [], [], []
        while (kind := r.byte()) != 0x00:
            if kind == 0x02:  # archive properties
                while r.byte():
                    r.take(r.number())
            elif kind == 0x04:
                self.pack_pos, self.pack_sizes, self.folders, sub_sizes, sub_crcs = _streams(r)
            elif kind == 0x05:
                files = _files(r)
            else:
                raise OSError(f'7z: header property {kind} is not supported')
        streams = [(i, size, crc) for i, (sizes, crcs) in enumerate(zip(sub_sizes, sub_crcs))
                   for size, crc in zip(sizes, crcs)]
        self.entries, at = [], 0
        for name, empty, is_dir in files:
            if empty:
                self.entries.append(Entry(name, 0, None, is_dir, None))
                continue
            if at >= len(streams):
                raise OSError('7z: more files than data streams')
            folder, size, crc = streams[at]
            at += 1
            self.entries.append(Entry(name, size, crc, False, folder))

    def _packed(self, offset, size):
        left = size
        while left:
            self.file.seek(offset)
            data = self.file.read(min(left, 1 << 20))
            if not data:
                raise OSError('7z: the archive ends too soon (incomplete download?)')
            offset += len(data)
            left -= len(data)
            yield data

    def _folder_output(self, folder, base, pack_sizes, first_pack=0):
        """Chunks of the folder's unpacked data. base: where its first packed stream starts."""
        offsets, at = [], base + sum(pack_sizes[:first_pack])
        for k in range(len(folder.packed)):
            offsets.append((at, pack_sizes[first_pack + k]))
            at += pack_sizes[first_pack + k]
        packed = dict(zip(folder.packed, offsets))
        bonds = dict(folder.bonds)  # in index -> out index
        in_first, out_first, out_owner = [], [], {}
        i = o = 0
        for index, (_m, ins, outs, _p) in enumerate(folder.coders):
            in_first.append(i)
            out_first.append(o)
            for k in range(outs):
                out_owner[o + k] = index
            i += ins
            o += outs

        def stream_in(index):
            if index in packed:
                return self._packed(*packed[index])
            if index not in bonds:
                raise OSError('7z: unbound coder input')
            return stream_out(bonds[index])

        def stream_out(index):
            coder = out_owner[index]
            method, ins, outs, props = folder.coders[coder]
            size = folder.sizes[index]
            first = in_first[coder]
            if outs != 1:
                raise OSError('7z: coders with several outputs are not supported')
            if method in (LZMA1, LZMA2) and ins == 1:
                return _lzma(stream_in(first), _lzma_filters(method, props, size), size)
            if method == COPY and ins == 1:
                return stream_in(first)
            if method == BCJ2 and ins == 4:
                side = [b''.join(stream_in(first + k)) for k in (1, 2, 3)]
                return _bcj2(stream_in(first), side[0], side[1], side[2], size)
            raise OSError(f'7z: compression method {method.hex()} is not supported')
        bound = {o for _i, o in folder.bonds}
        final = [k for k in range(len(folder.sizes)) if k not in bound]
        if len(final) != 1:
            raise OSError('7z: a folder with several outputs')
        return stream_out(final[0])

    def extract(self, wanted, progress=None):
        """wanted(entry) -> a target Path or None. Every file kept is checked against its CRC32
        and written as <target>.part first. progress(done, total) in unpacked bytes. Raises
        OSError (a damaged archive too)."""
        try:
            self._extract(wanted, progress)
        except (lzma.LZMAError, struct.error, IndexError, ValueError) as error:
            raise OSError(f'7z: damaged archive ({error})') from error

    def _extract(self, wanted, progress):
        targets = {id(e): wanted(e) for e in self.entries if not e.is_dir}
        folders = sorted({e.folder for e in self.entries if e.folder is not None and targets[id(e)]})
        total = sum(_final_size(self.folders[k]) for k in folders)
        done = 0
        for target in (t for e in self.entries if e.folder is None and not e.is_dir
                       for t in [targets[id(e)]] if t):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'')
        first_pack = [0]
        for folder in self.folders:
            first_pack.append(first_pack[-1] + len(folder.packed))
        for k in folders:
            members = [e for e in self.entries if e.folder == k]
            chunks = self._folder_output(self.folders[k], 32 + self.pack_pos, self.pack_sizes, first_pack[k])
            buffer, offset = b'', 0
            for entry in members:
                target = targets[id(entry)]
                out = None
                if target:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    out = open(target.with_name(target.name + '.part'), 'wb')
                crc, left = 0, entry.size
                try:
                    while left:
                        if offset >= len(buffer):
                            buffer, offset = next(chunks, None), 0
                            if buffer is None:
                                raise OSError('7z: the data ends too soon')
                            done += len(buffer)
                            if progress:
                                progress(done, total)
                        piece = buffer[offset:offset + left]
                        offset += len(piece)
                        left -= len(piece)
                        if out:
                            out.write(piece)
                            crc = binascii.crc32(piece, crc)
                except BaseException:
                    if out:
                        out.close()
                        target.with_name(target.name + '.part').unlink(missing_ok=True)
                    raise
                if out:
                    out.close()
                    part = target.with_name(target.name + '.part')
                    if entry.crc is not None and crc != entry.crc:
                        part.unlink(missing_ok=True)
                        raise OSError(f'{entry.name}: damaged in the archive (CRC)')
                    part.replace(target)


# ---------------------------------------------------------------------------------------------
# OptiScaler (github.com/optiscaler/OptiScaler, GPL-3.0): never in the port's zip; the launcher
# downloads the project's own release on the player's request and can take it out again.
# How it meets the port: issn-probe.exe imports WINMM.dll, so OptiScaler.dll named winmm.dll in
# bin\ is loaded by it; OptiScaler then catches the port's libxess.dll and gets the colour, depth,
# motion vectors and jitter of the port's XeSS (anti-aliasing XeSS), on any card.

OPTISCALER_PAGE = 'https://github.com/optiscaler/OptiScaler'
OPTISCALER_LICENSE = 'https://github.com/optiscaler/OptiScaler/blob/master/LICENSE'
OPTISCALER_API = 'https://api.github.com/repos/optiscaler/OptiScaler/releases/latest'
OPTISCALER_DOWNLOADS = 'https://github.com/optiscaler/OptiScaler/releases/download/'
MANIFEST = 'optiscaler.json'
MENU_KEY = '0x77'  # F8: Insert opens the port's menu and Home switches its anti-aliasing


@dataclass(frozen=True)
class Release:
    tag: str
    url: str
    sha256: str  # '' when GitHub gave none
    size: int


# Used when GitHub's API cannot be reached (rate limit, proxy): v0.9.4, the one João tested on
# 2026-11-02/04 (the same OptiScaler.dll, SHA-256 fbfb6676…).
OPTISCALER_KNOWN = Release(
    'v0.9.4', OPTISCALER_DOWNLOADS + 'v0.9.4/Optiscaler_0.9.4-final.20260718._MM.7z',
    '575cb4df866116093df75af607e37fd70e10f5163e0f23fd5c804142e80ef0ad', 55016448)

# What the anti-aliasing chooser offers -> the VulkanUpscaler values, newest name first (0.9.4
# writes fsr31 / fsr31_12; the master branch's README calls them ffx / ffx_12).
OPTISCALER_OUTPUTS = {'fsr4': ('fsr31_12', 'ffx_12'), 'fsr31': ('fsr31', 'ffx'), 'xess': ('xess',),
                      'dlss': ('dlss',), 'fsr22': ('fsr22',)}
# Release files not put into bin\ (instructions for other games, setup scripts).
SKIPPED = ('!! README_EXTRACT ALL FILES TO GAME FOLDER !!.txt', 'setup_linux.sh', 'setup_windows.bat')
# The files of a release unpacked by hand (no optiscaler.json): what Remove takes away then. The
# names of v0.9.4, plus what OptiScaler writes itself; nothing of the port is called like this.
HAND_FILES = ('winmm.dll', 'OptiScaler.ini', 'OptiScaler.log', 'amd_fidelityfx_dx12.dll',
              'amd_fidelityfx_framegeneration_dx12.dll', 'amd_fidelityfx_upscaler_dx12.dll',
              'amd_fidelityfx_vk.dll', 'D3D12_Optiscaler/D3D12Core.dll', 'dlssg_to_fsr3_amd_is_better.dll',
              'dlssg_to_fsr3.log', 'fakenvapi.dll', 'fakenvapi.ini', 'fakenvapi.log', 'libxell.dll',
              'libxess.dll', 'libxess_dx11.dll', 'libxess_fg.dll', 'Licenses/DirectX_LICENSE.txt',
              'Licenses/FidelityFX_v1_LICENSE.md', 'Licenses/FidelityFX_v2_LICENSE.md',
              'Licenses/XeSS_LICENSE.txt') + SKIPPED
# Written next to what was installed, and taken away by Remove whatever the manifest says.
WRITTEN = ('OptiScaler.ini', 'OptiScaler.log', 'dlssg_to_fsr3.log', 'fakenvapi.log', MANIFEST)


def optiscaler_release(opener=None):
    """The newest stable release (GitHub's 'latest': no pre-releases, no nightlies) and its .7z.
    Raises OSError or ValueError."""
    request = urllib.request.Request(OPTISCALER_API, headers={'Accept': 'application/vnd.github+json',
                                                              'User-Agent': USER_AGENT})
    with (opener or urllib.request.urlopen)(request, timeout=20) as response:
        release = json.load(response)
    if release.get('prerelease') or release.get('draft'):
        raise ValueError('the latest OptiScaler release is a pre-release')
    tag = str(release.get('tag_name', ''))
    for asset in release.get('assets', []):
        name, url = str(asset.get('name', '')), str(asset.get('browser_download_url', ''))
        if name.lower().startswith('optiscaler') and name.lower().endswith('.7z') \
                and url.startswith(OPTISCALER_DOWNLOADS):
            digest = str(asset.get('digest') or '')
            sha = digest[7:].lower() if digest.startswith('sha256:') else ''
            if tag == OPTISCALER_KNOWN.tag and not sha:
                sha = OPTISCALER_KNOWN.sha256
            return Release(tag, url, sha, int(asset.get('size') or 0))
    raise ValueError(f'OptiScaler {tag}: no .7z in the release')


def download(release, path, progress=None, opener=None):
    """The release's file into `path` (through path.part), checked against its SHA-256."""
    if not release.sha256:
        raise OSError(f'OptiScaler {release.tag}: GitHub gave no checksum for the file, not installed')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + '.part')
    request = urllib.request.Request(release.url, headers={'User-Agent': USER_AGENT})
    digest, done = hashlib.sha256(), 0
    try:
        with (opener or urllib.request.urlopen)(request, timeout=60) as response, open(part, 'wb') as out:
            total = int(response.headers.get('Content-Length') or release.size or 0)
            while chunk := response.read(1 << 20):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if digest.hexdigest() != release.sha256:
            raise OSError(f'OptiScaler {release.tag}: the download does not match the release (checksum)')
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)
    return path


def ini_set(text, section, key, value):
    """The INI text with `key` of [section] set to `value`, comments and order kept (OptiScaler
    writes 'Key=value' and, from its menu, 'Key = value'). A missing key or section is added."""
    newline = '\r\n' if '\r\n' in text else '\n'
    lines = text.split(newline)
    current, section_end, found = None, None, False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            if current == section.lower() and section_end is None:
                section_end = i
            current = stripped[1:-1].strip().lower()
            continue
        if current == section.lower() and not stripped.startswith(';') and '=' in stripped:
            name = stripped.split('=', 1)[0].strip()
            if name.lower() == key.lower():
                spaced = ' = ' if re.match(r'\s*[^=]*\s=\s', line) else '='
                lines[i] = f'{name}{spaced}{value}'
                found = True
    if not found:
        if current == section.lower() and section_end is None:
            section_end = len(lines)
            while section_end and not lines[section_end - 1].strip():
                section_end -= 1
        if section_end is None:
            lines += ['', f'[{section}]', f'{key}={value}']
        else:
            lines.insert(section_end, f'{key}={value}')
    return newline.join(lines)


def ini_get(text, section, key):
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            current = stripped[1:-1].strip().lower()
        elif current == section.lower() and not stripped.startswith(';') and '=' in stripped:
            name, value = stripped.split('=', 1)
            if name.strip().lower() == key.lower():
                return value.strip()
    return None


def vulkan_upscaler_value(text, output):
    """The VulkanUpscaler value for one of OPTISCALER_OUTPUTS, by the names this release's own
    OptiScaler.ini lists in the comment right above the key."""
    names = OPTISCALER_OUTPUTS.get(output, OPTISCALER_OUTPUTS['fsr22'])
    current, comment = None, []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            current, comment = stripped[1:-1].strip().lower(), []
        elif current != 'upscalers':
            continue
        elif stripped.startswith(';'):
            comment.append(stripped.lower())
        elif stripped.split('=', 1)[0].strip().lower() == 'vulkanupscaler':
            listed = set(re.findall(r'\b([a-z][a-z0-9_]*)\s*\(', ' '.join(comment)))
            return next((name for name in names if name in listed), names[0])
        else:
            comment = []
    return names[0]


# issn 11-05: outputs whose own pass does not sharpen (XeSS, DLSS 2.5.1 on): OptiScaler's RCAS
# ([CAS]) does it; FSR 2 / 3.1 / 4 sharpen by [Sharpness] Sharpness themselves.
NO_OWN_SHARPENING = ('xess', 'dlss')


def configure_sharpening(text, percent, upscaler):
    """The port's sharpening (0-100 %, issn.ini sharpening) in an OptiScaler.ini: OptiScaler takes
    the XeSS input and upscales, so it is the one place that sharpens (the port's own pass stays
    off then). Sharpness fixed to percent / 100 (0 = off, the port's default); RCAS only for an
    output without sharpening of its own, so nothing is sharpened twice."""
    p = max(0, min(100, int(percent)))
    text = ini_set(text, 'Sharpness', 'OverrideSharpness', 'true')
    text = ini_set(text, 'Sharpness', 'Sharpness', f'{p / 100:.2f}')
    rcas = p > 0 and (upscaler or '').strip().lower() in NO_OWN_SHARPENING
    return ini_set(text, 'CAS', 'Enabled', 'true' if rcas else 'false')


def set_sharpening(bin_dir, percent):
    """configure_sharpening on the OptiScaler.ini in bin\\ (the game is not running). None when
    OptiScaler is not there; else the Sharpness written."""
    state = optiscaler_state(bin_dir)
    ini = Path(bin_dir) / 'OptiScaler.ini'
    if not state or state['foreign'] or not ini.is_file():
        return None
    text = _read_text(ini)
    changed = configure_sharpening(text, percent, ini_get(text, 'Upscalers', 'VulkanUpscaler'))
    if changed != text:
        _write_text(ini, changed)
    return ini_get(changed, 'Sharpness', 'Sharpness')


def configure(text, output):
    """The port's settings in an OptiScaler.ini: the upscaler for Vulkan and the menu on F8."""
    text = ini_set(text, 'Upscalers', 'VulkanUpscaler', vulkan_upscaler_value(text, output))
    return ini_set(text, 'Menu', 'ShortcutKey', MENU_KEY)


def _read_text(path):
    return Path(path).read_bytes().decode('utf-8', errors='surrogateescape')


def _write_text(path, text):
    part = Path(path).with_name(Path(path).name + '.part')
    part.write_bytes(text.encode('utf-8', errors='surrogateescape'))
    os.replace(part, path)


def _installed_name(member):
    """Where a file of the release goes in bin\\ (None: not installed)."""
    name = member.replace('\\', '/').strip('/')
    parts = name.split('/')
    if not name or name.startswith('/') or ':' in name or any(p in ('', '.', '..') for p in parts):
        raise OSError(f'OptiScaler: unsafe name in the archive: {member!r}')
    if name in SKIPPED:
        return None
    if name.lower() == 'optiscaler.dll':
        return 'winmm.dll'
    if parts[0].lower() == 'licenses':
        return '/'.join(['optiscaler-licenses'] + parts[1:])
    return name


def is_optiscaler(dll):
    try:
        with open(dll, 'rb') as file:
            return b'OptiScaler' in file.read()
    except OSError:
        return False


def _dll_version(dll):
    try:
        match = re.search(rb'OptiScaler v(\d+\.\d+[0-9A-Za-z.\-]*)', Path(dll).read_bytes())
    except OSError:
        return ''
    return 'v' + match.group(1).decode() if match else ''


def optiscaler_state(bin_dir):
    """None, or {'version', 'by_hand' (no optiscaler.json), 'upscaler' (VulkanUpscaler),
    'menu_key' (ShortcutKey), 'foreign' (a winmm.dll that is not OptiScaler)}."""
    bin_dir = Path(bin_dir)
    dll = bin_dir / 'winmm.dll'
    manifest = _manifest(bin_dir)
    if not dll.is_file():
        return None
    if manifest is None and not is_optiscaler(dll):
        return {'version': '', 'by_hand': True, 'upscaler': None, 'menu_key': None, 'foreign': True}
    try:
        text = _read_text(bin_dir / 'OptiScaler.ini')
    except OSError:
        text = ''
    version = (manifest or {}).get('version') or _dll_version(dll)
    return {'version': version, 'by_hand': manifest is None, 'upscaler': ini_get(text, 'Upscalers', 'VulkanUpscaler'),
            'menu_key': ini_get(text, 'Menu', 'ShortcutKey'), 'foreign': False}


def _manifest(bin_dir):
    try:
        data = json.loads((Path(bin_dir) / MANIFEST).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get('files'), list) else None


def install(archive, bin_dir, output, version, progress=None, dlss_dll=None):
    """OptiScaler from its release .7z into bin_dir: OptiScaler.dll as winmm.dll, its DLLs beside
    it, its licenses in optiscaler-licenses\\, OptiScaler.ini set for the port (configure()).
    Unpacked into bin\\.optiscaler-new first (every file checked); then moved into place.
    Returns the list of files put (relative to bin_dir)."""
    bin_dir = Path(bin_dir)
    current = optiscaler_state(bin_dir)
    if current and current['foreign']:
        raise OSError(f'{bin_dir / "winmm.dll"} is another program, not OptiScaler: not replaced')
    staging = bin_dir / '.optiscaler-new'
    shutil.rmtree(staging, ignore_errors=True)
    try:
        with SevenZip(archive) as seven:
            plan = {}
            for entry in seven.entries:
                if not entry.is_dir:
                    target = _installed_name(entry.name)
                    if target:
                        plan[entry.name] = target
            if 'winmm.dll' not in plan.values() or 'OptiScaler.ini' not in plan.values():
                raise OSError('OptiScaler: OptiScaler.dll or OptiScaler.ini is not in the download')
            seven.extract(lambda e: staging / plan[e.name] if e.name in plan else None, progress)
        ini = staging / 'OptiScaler.ini'
        _write_text(ini, configure(_read_text(ini), output))
        files = sorted(plan.values(), key=str.lower)
        if output == 'dlss' and dlss_dll and Path(dlss_dll).is_file():
            shutil.copyfile(dlss_dll, staging / 'nvngx_dlss.dll')
            files.append('nvngx_dlss.dll')
        old = _manifest(bin_dir) or {}
        # Over a copy unpacked by hand, its leftovers (README, setup scripts, Licenses\) join the
        # list so Remove takes them too.
        by_hand = [n for n in HAND_FILES if current and current['by_hand'] and (bin_dir / n).is_file()]
        listed = sorted(set(files) | set(old.get('files', [])) | set(by_hand), key=str.lower)
        # The list first, so even a move cut short (a file in use) can be removed.
        _write_text(bin_dir / MANIFEST, json.dumps({'version': version, 'files': listed}, indent=2))
        for name in files:
            target = bin_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.replace(staging / name, target)
            except OSError as error:
                raise OSError(f'{target}: {error.strerror or error} (close the game and try again)') from error
        return files
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _inside(bin_dir, name):
    """bin_dir / name, or None for a name that would leave bin_dir."""
    parts = str(name).replace('\\', '/').split('/')
    if not name or ':' in str(name) or any(p in ('', '.', '..') for p in parts):
        return None
    return Path(bin_dir).joinpath(*parts)


def remove(bin_dir):
    """Takes OptiScaler out of bin_dir: what optiscaler.json lists (or, for a copy unpacked by
    hand, HAND_FILES) and what it writes itself. Nothing else is touched. Returns the files
    removed; raises OSError naming the files that could not be (in use by the game)."""
    bin_dir = Path(bin_dir)
    manifest = _manifest(bin_dir)
    if manifest is not None:
        names = [str(n) for n in manifest['files']]
    else:
        state = optiscaler_state(bin_dir)
        names = list(HAND_FILES) if state and not state['foreign'] else []
    if manifest is not None or names:
        names += [n for n in WRITTEN if n != MANIFEST and n not in names]
    removed, failed = [], []
    for name in names:
        target = _inside(bin_dir, name)
        if target is None or not target.is_file():
            continue
        try:
            target.unlink()
            removed.append(name)
        except OSError:
            failed.append(name)
    for folder in ('D3D12_Optiscaler', 'optiscaler-licenses', 'Licenses'):
        try:
            (bin_dir / folder).rmdir()  # only when empty: nothing else is ever deleted
        except OSError:
            pass
    if failed:
        raise OSError('could not remove (close the game and try again): ' + ', '.join(failed))
    (bin_dir / MANIFEST).unlink(missing_ok=True)
    return removed


def set_output(bin_dir, output, dlss_dll=None):
    """The installed OptiScaler.ini's VulkanUpscaler for `output`; with DLSS, the port's
    nvngx_dlss.dll copied next to it (OptiScaler looks for it there). Returns the value set."""
    bin_dir = Path(bin_dir)
    ini = bin_dir / 'OptiScaler.ini'
    text = _read_text(ini)
    value = vulkan_upscaler_value(text, output)
    _write_text(ini, ini_set(text, 'Upscalers', 'VulkanUpscaler', value))
    if output == 'dlss' and dlss_dll and Path(dlss_dll).is_file() and not (bin_dir / 'nvngx_dlss.dll').is_file():
        shutil.copyfile(dlss_dll, bin_dir / 'nvngx_dlss.dll')
        manifest = _manifest(bin_dir)
        if manifest is not None and 'nvngx_dlss.dll' not in manifest['files']:
            manifest['files'].append('nvngx_dlss.dll')
            _write_text(bin_dir / MANIFEST, json.dumps(manifest, indent=2))
    return value
