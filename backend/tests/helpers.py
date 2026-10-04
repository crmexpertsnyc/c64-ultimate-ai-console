"""Test helpers: build tiny but valid C64 media files."""

from __future__ import annotations

import struct

from app.library.media import _d64_offset

D64_SIZE = 174848


def petscii_name(name: str) -> bytes:
    raw = name.upper().encode("ascii")[:16]
    return raw + b"\xa0" * (16 - len(raw))


def make_d64(disk_name: str = "TESTDISK", files: list[tuple[str, bytes]] | None = None) -> bytes:
    """D64 with PRG files. Each file (payload incl. 2-byte load address) must fit in one sector."""
    files = files if files is not None else [("GAME", b"\x01\x08" + b"\xea" * 20)]
    img = bytearray(D64_SIZE)
    bam = _d64_offset(18, 0)
    img[bam] = 18
    img[bam + 1] = 1
    img[bam + 0x90:bam + 0xA0] = petscii_name(disk_name)
    dir_off = _d64_offset(18, 1)
    img[dir_off] = 0
    img[dir_off + 1] = 0xFF
    for i, (name, payload) in enumerate(files):
        assert len(payload) <= 254
        e = dir_off + i * 32
        img[e + 2] = 0x82  # closed PRG
        img[e + 3] = 17
        img[e + 4] = i
        img[e + 5:e + 21] = petscii_name(name)
        img[e + 30] = 1
        so = _d64_offset(17, i)
        img[so] = 0
        img[so + 1] = len(payload) + 1
        img[so + 2:so + 2 + len(payload)] = payload
    return bytes(img)


def make_t64(name: str = "TAPEGAME", start: int = 0x0801, body: bytes = b"\xea" * 16) -> bytes:
    header = b"C64 tape image file".ljust(32, b"\x00")
    header += struct.pack("<HHH", 0x0100, 1, 1) + b"\x00\x00" + b"TAPE".ljust(24, b" ")
    entry = struct.pack("<BBHHHI", 1, 0x82, start, start + len(body), 0, 64 + 32) + b"\x00" * 4
    entry += name.encode().ljust(16, b" ")
    return header + entry + body


def make_sid(title: str = "Commando", author: str = "Rob Hubbard", released: str = "1985 Elite") -> bytes:
    data = bytearray(0x7C)
    data[0:4] = b"PSID"
    struct.pack_into(">HH", data, 0x04, 2, 0x7C)
    struct.pack_into(">HH", data, 0x0E, 3, 1)
    data[0x16:0x16 + len(title)] = title.encode()
    data[0x36:0x36 + len(author)] = author.encode()
    data[0x56:0x56 + len(released)] = released.encode()
    return bytes(data) + b"\x00\x10" + b"\x60" * 32
