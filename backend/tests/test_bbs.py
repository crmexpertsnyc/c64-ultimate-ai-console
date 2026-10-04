"""📟 BBS directory, relay destination policy, telnet negotiation and the WebSocket relay (against a local mock)."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest

from app.services import bbs_net
from app.services.bbs_net import (
    DO,
    DONT,
    ECHO,
    IAC,
    NAWS,
    SB,
    SE,
    SGA,
    TTYPE,
    WILL,
    WONT,
    DestinationError,
    TelnetClient,
    is_public_address,
    resolve_destination,
)
from app.services.bbs_sources import Record, normalize_host, parse_syncterm, parse_tbg_csv, petscii_hint

SYNCTERM_SAMPLE = """; Exported from Synchronix

[Ansi Place]
\tConnectionType=telnet
\tAddress=BBS.Example.ORG.
\tPort=23
\tComment=Awesome ANSI stomping ground.
A BBS since 1985.

[Secure Shell Only]
\tConnectionType=ssh
\tAddress=ssh.example.org
\tPort=22
\tComment=SSH only

[Commodore Fans]
\tConnectionType=telnet
\tAddress=commodore.example.org
\tPort=6400
\tComment=We love Commodore and the C64!

[Sixty Four]
\tConnectionType=telnet
\tAddress=c64.example.org
\tPort=6818
\tScreenMode=C64
\tComment=A game board
"""

TBG_SAMPLE = """bbsName, bbsSysop, newLogin, TelnetAddress, bbsPort, sshPort, WebAddress, location, Modem, software
Image Board,Sysop,NEW,image.example.org,6400,,http://image.example.org,"Oak Ridge, TN, USA",,"Image BBS"
Mystic Board,,,mystic.example.org,,,,"Portland, OR, USA",,"Mystic"
"""


# ------------------------------------------------------------------ sources
def test_syncterm_parser_keeps_telnet_and_never_guesses_petscii():
    recs = {r.name: r for r in parse_syncterm(SYNCTERM_SAMPLE)}
    assert set(recs) == {"Ansi Place", "Commodore Fans", "Sixty Four"}           # the ssh entry is skipped
    a = recs["Ansi Place"]
    assert (a.host, a.port, a.protocol) == ("bbs.example.org", 23, "telnet")      # normalized host
    assert a.description == "Awesome ANSI stomping ground. A BBS since 1985."     # wrapped comment joined
    assert a.ansi == "unverified" and a.petscii == "unknown"
    assert recs["Commodore Fans"].petscii == "unknown"                            # "Commodore" ≠ PETSCII
    assert recs["Sixty Four"].petscii == "unverified" and "ScreenMode=C64" in recs["Sixty Four"].compat_note
    assert all(r.source_url.startswith("https://syncterm") for r in recs.values())


def test_tbg_parser_and_petscii_hints():
    recs = {r.name: r for r in parse_tbg_csv(TBG_SAMPLE, "https://www.telnetbbsguide.com", "2026-10")}
    assert recs["Image Board"].petscii == "unverified" and recs["Image Board"].port == 6400
    assert recs["Image Board"].location == "Oak Ridge, TN, USA"
    assert recs["Mystic Board"].port == 23 and recs["Mystic Board"].petscii == "unknown"
    assert petscii_hint("Commodore and Amiga files", None) is None
    assert petscii_hint("PETSCII graphics welcome", None)
    assert normalize_host("telnet://Foo.Example.com:23/") == "foo.example.com"
    assert normalize_host("not a host!") is None


@pytest.fixture
def svc(app_client):
    client = app_client()
    with client:
        yield client, client.app.state.container.bbs


def test_import_dedupes_and_keeps_sources(svc):
    _client, bbs = svc
    r1 = Record(name="Board", host="bbs.example.org", port=23, source="syncterm", source_label="SyncTERM",
                source_url="https://syncterm.example/list", ansi="unverified")
    r2 = Record(name="Board (dup)", host="bbs.example.org", port=23, source="tbg", source_label="TBG",
                source_url="https://tbg.example", location="Somewhere", petscii="unverified")
    r3 = Record(name="Other port", host="bbs.example.org", port=6400, source="tbg", source_label="TBG",
                source_url="https://tbg.example")
    assert bbs.import_records([r1, r1]) == {"added": 1, "updated": 0}
    assert bbs.import_records([r2, r3]) == {"added": 1, "updated": 1}
    listing = bbs.list(admin=True)["boards"]
    assert len(listing) == 2
    b = next(x for x in listing if x["port"] == 23)
    assert b["name"] == "Board" and b["location"] == "Somewhere" and b["review"] == "pending"
    assert {s["source"] for s in b["sources"]} == {"syncterm", "tbg"} and all(s["seenAt"] for s in b["sources"])
    # an admin confirmation is never downgraded by a later import
    bbs.edit(b["id"], {"petscii": "confirmed"})
    bbs.import_records([r2])
    assert bbs.get(b["id"], admin=True)["petscii"] == "confirmed"
    # pending boards are hidden from non-admins
    assert bbs.list()["boards"] == []
    bbs.review(b["id"], "approved")
    assert [x["id"] for x in bbs.list()["boards"]] == [b["id"]]


def test_setup_pending_until_a_source_imports(svc, monkeypatch):
    _client, bbs = svc
    assert bbs.status()["setupPending"] is True

    class Down:
        label, url, terms, setting = "Down", "https://x.invalid", "", "BBS_SOURCE_SYNCTERM"

        async def fetch(self, http=None):  # noqa: ANN001, ARG002
            from app.services.bbs_sources import SourceError
            raise SourceError("unreachable")

    monkeypatch.setattr(bbs, "sources", {"syncterm": Down()})
    res = asyncio.run(bbs.refresh())
    assert res["errors"] == ["syncterm"] and bbs.status()["setupPending"] is True


def test_favorites_and_notes(svc):
    client, bbs = svc
    bbs.import_records([Record(name="Fav", host="fav.example.org", port=23, source="manual", source_label="m",
                               source_url="")])
    bid = bbs.list(admin=True)["boards"][0]["id"]
    bbs.review(bid, "approved")
    r = client.put(f"/api/bbs/boards/{bid}/me", json={"favorite": True, "notes": "  try the door games  "})
    assert r.status_code == 200 and r.json() == {"favorite": True, "notes": "try the door games",
                                                 "lastConnectedAt": None}
    data = client.get("/api/bbs", params={"favorites": True}).json()
    assert [b["id"] for b in data["boards"]] == [bid] and data["boards"][0]["notes"] == "try the door games"
    assert data["admin"] is False


def test_admin_endpoints_need_admin(svc, monkeypatch):
    client, bbs = svc
    assert client.post("/api/bbs/refresh").status_code == 403
    assert client.post("/api/bbs/boards", json={"name": "x", "host": "x.example.org"}).status_code == 403
    from app.api import bbs_api
    monkeypatch.setattr(bbs_api, "is_local", lambda scope: True)
    r = client.post("/api/bbs/boards", json={"name": "Hand", "host": "Hand.Example.org", "port": 6400})
    assert r.status_code == 200 and r.json()["review"] == "pending" and r.json()["host"] == "hand.example.org"
    assert client.post("/api/bbs/boards", json={"name": "Web", "host": "x.example.org", "port": 80}).status_code == 400
    bid = r.json()["id"]
    assert client.post(f"/api/bbs/boards/{bid}/approve").json()["approved"] is True
    assert client.patch(f"/api/bbs/boards/{bid}", json={"protocol": "raw"}).json()["protocol"] == "raw"


# ------------------------------------------------------------------ destination policy
@pytest.mark.parametrize("addr,ok", [
    ("93.184.216.34", True), ("2606:4700:4700::1111", True),
    ("127.0.0.1", False), ("10.1.2.3", False), ("172.16.0.1", False), ("192.168.1.167", False),
    ("169.254.169.254", False), ("100.64.0.1", False), ("0.0.0.0", False), ("224.0.0.1", False),
    ("240.0.0.1", False), ("255.255.255.255", False), ("192.0.2.1", False), ("198.18.0.1", False),
    ("::1", False), ("::", False), ("fe80::1", False), ("fc00::1", False), ("fd00:ec2::254", False),
    ("ff02::1", False), ("2001:db8::1", False),
    ("::ffff:127.0.0.1", False), ("::ffff:10.0.0.1", False), ("::ffff:93.184.216.34", True),
    ("64:ff9b::7f00:1", False), ("64:ff9b::a9fe:a9fe", False), ("64:ff9b::5db8:d822", True),
    ("2002:7f00:0001::1", False), ("2002:c0a8:0101::1", False), ("2001:0:4136:e378::1", False),
    ("not-an-ip", False),
])
def test_public_address_policy(addr, ok):
    assert is_public_address(addr) is ok


def _resolver(answers):
    calls = []

    async def resolve(host, port):  # noqa: ANN001
        calls.append(host)
        return answers
    resolve.calls = calls
    return resolve


def test_resolution_refuses_any_private_answer_and_bad_ports():
    async def run():
        assert await resolve_destination("bbs.example.org", 23, _resolver(["93.184.216.34"])) == ["93.184.216.34"]
        for answers in (["93.184.216.34", "127.0.0.1"], ["::ffff:192.168.1.1"], ["169.254.169.254"], []):
            with pytest.raises(DestinationError):
                await resolve_destination("bbs.example.org", 23, _resolver(answers))
        for port in (22, 25, 80, 443, 3306, 0, 70000):
            with pytest.raises(DestinationError):
                await resolve_destination("bbs.example.org", port, _resolver(["93.184.216.34"]))
    asyncio.run(run())


def test_connection_is_pinned_to_the_checked_ip():
    """DNS is asked once; the socket goes to the checked IP literal, so a second (rebinding) answer can't matter."""
    seen = []

    async def opener(ip, port):  # noqa: ANN001
        seen.append((ip, port))
        raise OSError("refused")

    answers = ["93.184.216.34"]
    resolver = _resolver(answers)

    async def run():
        with pytest.raises(DestinationError):
            await bbs_net.connect_pinned("rebind.example.org", 23, resolver=resolver, opener=opener, timeout=1)
    asyncio.run(run())
    answers[:] = ["127.0.0.1"]                       # what a rebinding DNS would answer next
    assert seen == [("93.184.216.34", 23)] and resolver.calls == ["rebind.example.org"]


# ------------------------------------------------------------------ telnet
def test_telnet_negotiation():
    t = TelnetClient("ANSI", 80, 25)
    data, rep = t.feed(bytes([IAC, WILL, ECHO, IAC, WILL, SGA, IAC, DO, TTYPE, IAC, DO, NAWS, IAC, DO, 39])
                       + b"Hi" + bytes([IAC, IAC]) + b"!")
    assert data == b"Hi\xff!"
    assert bytes([IAC, DO, ECHO]) in rep and bytes([IAC, DO, SGA]) in rep and bytes([IAC, WILL, TTYPE]) in rep
    assert bytes([IAC, WILL, NAWS, IAC, SB, NAWS, 0, 80, 0, 25, IAC, SE]) in rep
    assert bytes([IAC, WONT, 39]) in rep                                # unknown option refused
    _, rep2 = t.feed(bytes([IAC, WILL, ECHO]))                          # repeated: no reply (no loops)
    assert rep2 == b""
    _, rep3 = t.feed(bytes([IAC, SB, TTYPE, 1, IAC, SE]))
    assert rep3 == bytes([IAC, SB, TTYPE, 0]) + b"ANSI" + bytes([IAC, SE])
    _, rep4 = t.feed(bytes([IAC, WILL, 5]))                             # STATUS: refused
    assert rep4 == bytes([IAC, DONT, 5])
    # split across reads
    d1, _ = t.feed(bytes([IAC]))
    d2, _ = t.feed(bytes([IAC]) + b"x")
    assert d1 + d2 == b"\xffx"
    # outgoing: IAC doubled, CR → CR NUL without BINARY
    assert t.encode(b"a\r\xff") == b"a\r\x00\xff\xff"
    assert t.resize(100, 30) == bytes([IAC, SB, NAWS, 0, 100, 0, 30, IAC, SE])


def test_relay_limits():
    from app.services.bbs import RelayLimits
    lim = RelayLimits(max_sessions=3, per_client=2, connects=3, window=60)
    assert lim.acquire("a", "c1", 1) is None and lim.acquire("b", "c1", 1) is None
    assert "this device" in lim.acquire("c", "c1", 1)
    assert lim.acquire("d", "c2", 1) is None
    assert "maximum" in lim.acquire("e", "c3", 1)
    lim.release("a")
    assert lim.acquire("f", "c1", 1) is None
    lim.release("f")
    assert "too many" in lim.acquire("g", "c1", 1)                       # 3 starts in the window


# ------------------------------------------------------------------ the relay, end to end
class MockBoard:
    """A tiny telnet BBS on 127.0.0.1: negotiates, greets, echoes what it receives."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.received = bytearray()
        self.closed = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        conn, _ = self.sock.accept()
        conn.settimeout(10)
        conn.sendall(bytes([IAC, DO, TTYPE, IAC, WILL, ECHO, IAC, WILL, SGA, IAC, SB, TTYPE, 1, IAC, SE])
                     + b"WELCOME\r\n")
        try:
            while True:
                data = conn.recv(4096)
                if not data:
                    break
                self.received += data
                clean = data.replace(b"\r\x00", b"\r")
                if b"hi" in clean:
                    conn.sendall(b"ECHO:" + bytes([0xFF, 0xFF]) + b"hi\r\n")
        except OSError:
            pass
        finally:
            conn.close()
            self.closed.set()


def _approved_board(bbs, port=6400):
    bbs.import_records([Record(name="Mock", host="mock.example.org", port=port, source="manual", source_label="t",
                               source_url="")])
    bid = bbs.list(admin=True)["boards"][0]["id"]
    bbs.review(bid, "approved")
    return bid


def _wire(bbs, mock):
    """Resolve to a public address (as real DNS would), but have the 'network' deliver it to the local mock."""
    bbs.resolver = _resolver(["93.184.216.34"])
    dialed = []

    async def opener(ip, port):  # noqa: ANN001
        dialed.append((ip, port))
        return await asyncio.open_connection("127.0.0.1", mock.port)
    bbs.opener = opener
    return dialed


ORIGIN = {"origin": "http://testserver"}


def _recv_until(ws, want: bytes, limit=20):
    got = b""
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("bytes"):
            got += msg["bytes"]
            if want in got:
                return got
    raise AssertionError(f"never got {want!r}: {got!r}")


def test_relay_end_to_end(svc):
    client, bbs = svc
    mock = MockBoard()
    bid = _approved_board(bbs)
    dialed = _wire(bbs, mock)
    with client.websocket_connect(f"/ws/bbs/{bid}?mode=ansi", headers=ORIGIN) as ws:
        assert json.loads(ws.receive_text())["state"] == "connecting"
        assert json.loads(ws.receive_text())["state"] == "connected"
        got = _recv_until(ws, b"WELCOME")
        assert IAC not in got                                         # negotiation never reaches the browser
        ws.send_bytes(b"hi\r")
        assert b"ECHO:\xffhi" in _recv_until(ws, b"hi\r\n")             # IAC IAC → one 0xFF data byte
        assert len(bbs.limits.active) == 1
    assert mock.closed.wait(5)                                        # backend socket closed with the session
    assert dialed == [("93.184.216.34", 6400)]
    rx = bytes(mock.received)
    assert bytes([IAC, WILL, TTYPE]) in rx and bytes([IAC, SB, TTYPE, 0]) + b"ANSI" in rx
    assert b"hi\r\x00" in rx                                          # CR NUL (no BINARY)
    for _ in range(50):
        if not bbs.limits.active:
            break
        time.sleep(0.05)
    assert bbs.limits.active == {}
    assert bbs.get(bid)["lastConnectedAt"]


def test_relay_refusals(svc):
    client, bbs = svc
    from starlette.websockets import WebSocketDisconnect
    # wrong origin (another site's page) → closed before anything happens
    bid = _approved_board(bbs)
    evil = {"origin": "https://evil.example"}
    with pytest.raises(WebSocketDisconnect) as e, client.websocket_connect(f"/ws/bbs/{bid}", headers=evil) as ws:
        ws.receive_text()
    assert e.value.code == 4403
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(f"/ws/bbs/{bid}") as ws:          # no Origin at all
        ws.receive_text()
    # a pending board can't be opened
    bbs.review(bid, "pending")
    with client.websocket_connect(f"/ws/bbs/{bid}", headers=ORIGIN) as ws:
        assert "isn't approved" in json.loads(ws.receive_text())["detail"]
    # a board whose name resolves to a private address is refused (and never dialed)
    bbs.review(bid, "approved")
    bbs.resolver = _resolver(["192.168.1.167"])
    dialed = []

    async def opener(ip, port):  # noqa: ANN001
        dialed.append(ip)
        raise OSError
    bbs.opener = opener
    with client.websocket_connect(f"/ws/bbs/{bid}", headers=ORIGIN) as ws:
        assert json.loads(ws.receive_text())["state"] == "connecting"
        assert "private or reserved" in json.loads(ws.receive_text())["detail"]
    assert dialed == [] and bbs.limits.active == {}


def test_relay_session_limit(svc):
    client, bbs = svc
    bid = _approved_board(bbs)
    bbs.limits.max_sessions = 0
    with client.websocket_connect(f"/ws/bbs/{bid}", headers=ORIGIN) as ws:
        assert "maximum" in json.loads(ws.receive_text())["detail"]


def test_relay_idle_timeout(svc, monkeypatch):
    client, bbs = svc
    mock = MockBoard()
    bid = _approved_board(bbs)
    _wire(bbs, mock)
    from app.api import bbs_api
    monkeypatch.setattr(bbs_api, "idle_seconds", lambda c: 0.3)
    monkeypatch.setattr(bbs_api, "WATCH_EVERY", 0.05)
    with client.websocket_connect(f"/ws/bbs/{bid}", headers=ORIGIN) as ws:
        msgs = []
        for _ in range(30):
            m = ws.receive()
            if m.get("text"):
                msgs.append(json.loads(m["text"]))
                if msgs[-1].get("state") == "disconnected":
                    break
        assert "without typing" in msgs[-1]["detail"]
    assert mock.closed.wait(5)
