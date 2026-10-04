"""Read-only parsers for C64 media files (never writes to the source file).

* D64/D71/D81: disk name and directory listing; extraction of a PRG by name
* T64: directory and extraction to PRG
* SID: PSID/RSID header (title, author, released, songs)
* CRT: cartridge name
* MOD: song title
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

FILE_TYPES = {0: "DEL", 1: "SEQ", 2: "PRG", 3: "USR", 4: "REL", 5: "CBM"}


def petscii_to_str(data: bytes) -> str:
    out = []
    for b in data:
        if b in (0xA0, 0x00):
            break
        if 0x41 <= b <= 0x5A or 0x20 <= b <= 0x3F:
            out.append(chr(b))
        elif 0xC1 <= b <= 0xDA:
            out.append(chr(b - 0x80))
        elif 0x61 <= b <= 0x7A:
            out.append(chr(b - 0x20))
        else:
            out.append("?")
    return "".join(out).rstrip()


@dataclass
class DirEntry:
    name: str
    type: str
    blocks: int
    track: int
    sector: int
    closed: bool


# ---------------------------------------------------------------- disk images
def _d64_sectors(track: int) -> int:
    if track <= 17:
        return 21
    if track <= 24:
        return 19
    if track <= 30:
        return 18
    return 17


def _d64_offset(track: int, sector: int) -> int:
    t = track if track <= 35 else track  # 40-track images keep 17 sectors past 35
    base = sum(_d64_sectors(i) for i in range(1, t))
    return (base + sector) * 256


def _d71_offset(track: int, sector: int) -> int:
    if track <= 35:
        return _d64_offset(track, sector)
    return 683 * 256 + _d64_offset(track - 35, sector)


def _d81_offset(track: int, sector: int) -> int:
    return ((track - 1) * 40 + sector) * 256


class DiskImage:
    def __init__(self, data: bytes, fmt: str):
        self.data = data
        self.fmt = fmt.lower()
        if self.fmt == "d81":
            self._off, self._dir_ts, self._name_at = _d81_offset, (40, 3), (_d81_offset(40, 0), 0x04)
        elif self.fmt == "d71":
            self._off, self._dir_ts, self._name_at = _d71_offset, (18, 1), (_d64_offset(18, 0), 0x90)
        elif self.fmt == "d64":
            self._off, self._dir_ts, self._name_at = _d64_offset, (18, 1), (_d64_offset(18, 0), 0x90)
        else:
            raise ValueError(f"unsupported disk format {fmt} (G64/G71 are GCR images and are not parsed)")

    def _sector(self, track: int, sector: int) -> bytes | None:
        off = self._off(track, sector)
        if off < 0 or off + 256 > len(self.data):
            return None
        return self.data[off:off + 256]

    @property
    def disk_name(self) -> str:
        base, rel = self._name_at
        return petscii_to_str(self.data[base + rel: base + rel + 16]) if base + rel + 16 <= len(self.data) else ""

    def directory(self, limit: int = 296) -> list[DirEntry]:
        entries: list[DirEntry] = []
        track, sector = self._dir_ts
        seen: set[tuple[int, int]] = set()
        while track and (track, sector) not in seen and len(entries) < limit:
            seen.add((track, sector))
            sec = self._sector(track, sector)
            if sec is None:
                break
            for i in range(8):
                e = sec[i * 32:(i + 1) * 32]
                ftype = e[2]
                if ftype == 0:
                    continue
                entries.append(DirEntry(
                    name=petscii_to_str(e[5:21]),
                    type=FILE_TYPES.get(ftype & 0x07, "???"),
                    blocks=e[30] | (e[31] << 8),
                    track=e[3], sector=e[4], closed=bool(ftype & 0x80),
                ))
            track, sector = sec[0], sec[1]
        return entries

    def read_file(self, entry: DirEntry, max_bytes: int = 202 * 254) -> bytes:
        out = bytearray()
        track, sector = entry.track, entry.sector
        seen: set[tuple[int, int]] = set()
        while track and (track, sector) not in seen:
            seen.add((track, sector))
            sec = self._sector(track, sector)
            if sec is None:
                raise ValueError("broken sector chain")
            nt, ns = sec[0], sec[1]
            out += sec[2:] if nt else sec[2:ns + 1]
            if len(out) > max_bytes:
                raise ValueError("file too large")
            track, sector = nt, ns
        return bytes(out)

    def first_prg(self) -> tuple[DirEntry, bytes] | None:
        for entry in self.directory():
            if entry.type == "PRG" and entry.closed:
                return entry, self.read_file(entry)
        return None

    def load_address(self, entry: DirEntry) -> int | None:
        off = self._off(entry.track, entry.sector) if entry.track else -1
        if off < 0 or off + 4 > len(self.data):
            return None
        return self.data[off + 2] | (self.data[off + 3] << 8)

    def boot_entry(self) -> DirEntry | None:
        """The program the disk most likely boots with.

        ``LOAD"*",8,1`` loads the first directory entry, but many releases put directory art, a
        note or a data part first. Prefer the first closed PRG that loads at $0801 (a BASIC
        ``SYS`` starter, the normal way C64 programs start); otherwise the first closed PRG."""
        prgs = [e for e in self.directory() if e.type == "PRG" and e.closed and e.blocks > 0]
        if not prgs:
            return None
        for e in prgs:
            if self.load_address(e) == 0x0801:
                return e
        return prgs[0]

    def boot_prg(self) -> tuple[DirEntry, bytes] | None:
        entry = self.boot_entry()
        return (entry, self.read_file(entry)) if entry else None


def loadable_name(name: str) -> bool:
    """A directory name that can be typed safely inside LOAD"...": printable, no quotes."""
    return 0 < len(name) <= 16 and all(" " <= c <= "_" and c != '"' for c in name)


# ----------------------------------------------------------------------- T64
def t64_entries(data: bytes) -> list[dict]:
    # Signature is "C64 tape image file" or "C64S tape file" (both start with C64).
    if len(data) < 64 or not data.startswith(b"C64"):
        raise ValueError("not a T64 image")
    max_entries = struct.unpack_from("<H", data, 0x22)[0]
    out = []
    for i in range(max(1, min(max_entries, 256))):
        off = 0x40 + i * 32
        if off + 32 > len(data):
            break
        etype, ftype, start, end, _res, offset = struct.unpack_from("<BBHHHI", data, off)
        if etype == 0:
            continue
        out.append({"name": petscii_to_str(data[off + 16:off + 32]).strip(), "start": start, "end": end,
                    "offset": offset, "fileType": ftype})
    return out


def t64_extract_prg(data: bytes, index: int = 0) -> tuple[str, bytes]:
    entries = t64_entries(data)
    if not entries:
        raise ValueError("T64 contains no files")
    e = entries[index]
    length = (e["end"] - e["start"]) & 0xFFFF
    body = data[e["offset"]:e["offset"] + length]
    if len(body) < length:  # many T64s have a wrong end address; take what is there
        length = len(body)
    return e["name"], struct.pack("<H", e["start"]) + body[:length]


# ----------------------------------------------------------------- headers
def sid_header(data: bytes) -> dict:
    if len(data) < 0x76 or data[:4] not in (b"PSID", b"RSID"):
        return {}
    songs, start_song = struct.unpack_from(">HH", data, 0x0E)

    def s(a: int) -> str:
        return data[a:a + 32].split(b"\x00")[0].decode("latin-1").strip()

    return {"type": data[:4].decode(), "title": s(0x16), "author": s(0x36), "released": s(0x56),
            "songs": songs, "startSong": start_song}


def crt_header(data: bytes) -> dict:
    if len(data) < 0x40 or not data.startswith(b"C64 CARTRIDGE"):
        return {}
    hw_type = struct.unpack_from(">H", data, 0x16)[0]
    return {"name": data[0x20:0x40].split(b"\x00")[0].decode("latin-1").strip(), "hardwareType": hw_type}


def mod_header(data: bytes) -> dict:
    if len(data) < 1084:
        return {}
    return {"title": data[:20].split(b"\x00")[0].decode("latin-1").strip(),
            "signature": data[1080:1084].decode("latin-1", errors="replace")}
