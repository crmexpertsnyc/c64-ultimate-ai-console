import hashlib

import pytest
from helpers import make_d64, make_sid, make_t64

from app.library.media import DiskImage, sid_header, t64_extract_prg
from app.library.repository import LibraryRepository
from app.library.scanner import parse_filename, scan_root
from app.models.db import init_db


@pytest.mark.parametrize("name,title,disk,year,publisher", [
    ("Summer Games Disk 1.d64", "Summer Games", 1, None, None),
    ("Summer Games Disk 2.d64", "Summer Games", 2, None, None),
    ("Last Ninja, The (1987)(System 3)(Side B).d64", "The Last Ninja", 2, 1987, "System 3"),
    ("Impossible Mission (1984)(Epyx)[cr FLT].d64", "Impossible Mission", 1, 1984, "Epyx"),
    ("bruce_lee.prg", "Bruce Lee", 1, None, None),
    ("World Class Leaderboard 2.d64", "World Class Leaderboard 2", 1, None, None),
    ("Zak McKracken (Disk 2 of 2).d64", "Zak McKracken", 2, None, None),
    ("ELITE-side_a.d64", "Elite", 1, None, None),
])
def test_parse_filename(name, title, disk, year, publisher):
    p = parse_filename(name)
    assert p.title == title
    assert p.disk_number == disk
    assert p.year == year
    assert p.publisher == publisher


def test_disk_image_directory_and_prg():
    img = DiskImage(make_d64("MY DISK", [("BRUCE LEE", b"\x01\x08ABC")]), "d64")
    assert img.disk_name == "MY DISK"
    entries = img.directory()
    assert entries[0].name == "BRUCE LEE" and entries[0].type == "PRG"
    entry, data = img.first_prg()
    assert data == b"\x01\x08ABC"


def test_t64_and_sid():
    name, prg = t64_extract_prg(make_t64("TAPEGAME", 0x0801, b"\x01\x02\x03"))
    assert name == "TAPEGAME" and prg == b"\x01\x08\x01\x02\x03"
    h = sid_header(make_sid())
    assert h["title"] == "Commando" and h["author"] == "Rob Hubbard" and h["songs"] == 3


def test_scan_groups_multidisk_and_never_modifies_sources(tmp_path):
    lib = tmp_path / "Games"
    (lib / "Epyx").mkdir(parents=True)
    files = {
        lib / "Epyx" / "Summer Games Disk 1.d64": make_d64("SUMMER1"),
        lib / "Epyx" / "Summer Games Disk 2.d64": make_d64("SUMMER2"),
        lib / "Bruce Lee (1984)(Datasoft).prg": b"\x01\x08" + b"\xea" * 10,
        lib / "Commando.sid": make_sid(),
        lib / "notes.txt": b"ignore me",
    }
    for p, data in files.items():
        p.write_bytes(data)
    digests = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}

    sf = init_db(f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}")
    with sf() as s:
        result = scan_root(s, str(lib))
    assert result.files_found == 4
    assert result.games_created == 3

    with sf() as s:
        repo = LibraryRepository(s)
        games, total = repo.search("summer")
        assert total == 1
        g = games[0]
        assert g.num_disks == 2 and sorted(m.disk_number for m in g.media) == [1, 2]
        assert g.media[0].info["diskName"] in ("SUMMER1", "SUMMER2")
        bruce = repo.find_best("bruce lee")[0][0]
        assert bruce.year == 1984 and bruce.publisher == "Datasoft" and bruce.format == "prg"
        sid = repo.find_best("commando", category="music")[0][0]
        assert sid.format == "sid" and sid.publisher == "Rob Hubbard"

    # Rescan is idempotent; removed files are only flagged.
    (lib / "Commando.sid").unlink()
    files.pop(lib / "Commando.sid")
    with sf() as s:
        again = scan_root(s, str(lib))
    assert again.games_created == 0 and again.media_added == 0 and again.missing == 1
    for p in files:
        assert hashlib.sha256(p.read_bytes()).hexdigest() == digests[p]


def test_fuzzy_match(tmp_path):
    lib = tmp_path / "g"
    lib.mkdir()
    for n in ("Impossible Mission.d64", "Impossible Mission II.d64", "Bruce Lee.d64", "Boulder Dash.prg"):
        (lib / n).write_bytes(make_d64() if n.endswith("d64") else b"\x01\x08\x00")
    sf = init_db(f"sqlite:///{(tmp_path / 'db.sqlite').as_posix()}")
    with sf() as s:
        scan_root(s, str(lib))
        repo = LibraryRepository(s)
        assert repo.find_best("impossible mission")[0][0].title == "Impossible Mission"
        assert repo.find_best("boulderdash")[0][0].title == "Boulder Dash"
        assert repo.find_best("bruse lee")[0][0].title == "Bruce Lee"
        assert repo.find_best("zzzz") == []


def test_boot_entry_prefers_basic_start_program():
    # First file is a data/art part loading at $4000; the real starter loads at $0801.
    img = DiskImage(make_d64("DEMO", [("ARTWORK", b"\x00\x40" + b"\x11" * 20), ("DEMO", b"\x01\x08" + b"\xea" * 20)]), "d64")
    assert img.directory()[0].name == "ARTWORK"
    assert img.boot_entry().name == "DEMO"
    entry, data = img.boot_prg()
    assert data[:2] == b"\x01\x08"
    only_data = DiskImage(make_d64("X", [("PART1", b"\x00\x40\x01"), ("PART2", b"\x00\x50\x02")]), "d64")
    assert only_data.boot_entry().name == "PART1"  # no $0801 file: fall back to the first PRG


def test_extract_playable_is_safe(tmp_path):
    import io
    import zipfile

    from app.services.catalog import extract_playable
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Game/Game Disk 1.d64", make_d64("G1"))
        z.writestr("Game/Game Disk 2.d64", make_d64("G2"))
        z.writestr("../../evil.prg", b"\x01\x08\x60")
        z.writestr("readme.txt", "hello")
    out = extract_playable(buf.getvalue(), tmp_path)
    assert sorted(out) == ["Game Disk 1.d64", "Game Disk 2.d64", "evil.prg"]
    assert all((tmp_path / n).parent == tmp_path for n in out)  # flattened, nothing outside the folder
    assert not (tmp_path.parent / "evil.prg").exists()
