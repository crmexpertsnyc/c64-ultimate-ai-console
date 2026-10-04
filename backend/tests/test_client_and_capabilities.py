import logging

import httpx
import pytest
from conftest import build

from app.logging_setup import RedactingFilter, register_secret
from app.ultimate.capabilities import classify
from app.ultimate.client import UltimateAuthError, UltimateClient, parse_drives
from app.ultimate.transport import HttpTransport, UltimateResponse


def _mock_client(handler, password="s3cret-pass"):
    transport = HttpTransport("http://c64u.local", password=password, transport=httpx.MockTransport(handler))
    return UltimateClient(transport)


async def test_password_header_sent_and_version_parsed():
    seen = {}

    def handler(request: httpx.Request):
        seen["pw"] = request.headers.get("X-Password")
        return httpx.Response(200, json={"version": "0.1", "errors": []})

    client = _mock_client(handler)
    v = await client.version()
    assert v.api_version == "0.1"
    assert seen["pw"] == "s3cret-pass"


async def test_403_raises_auth_error():
    client = _mock_client(lambda r: httpx.Response(403, text="Forbidden"))
    with pytest.raises(UltimateAuthError):
        await client.info()


async def test_writemem_uses_put_for_small_and_post_for_large():
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.params.get("data"), len(request.content)))
        return httpx.Response(200, json={"errors": []})

    client = _mock_client(handler)
    await client.write_memory(0xD020, b"\x05\x04")
    await client.write_memory(0x4000, bytes(200))
    assert calls[0] == ("PUT", "0504", 0)
    assert calls[1][0] == "POST" and calls[1][2] == 200


async def test_soft_errors_in_200_json_raise():
    from app.ultimate.client import UltimateError
    client = _mock_client(lambda r: httpx.Response(200, json={"errors": ["file not found"]}))
    with pytest.raises(UltimateError):
        await client.run_prg("/Usb0/x.prg")


def test_parse_drives_variants():
    doc = {"drives": [{"a": {"enabled": True, "bus_id": 8, "type": "1541", "image_file": "x.d64"}},
                      {"b": {"enabled": False, "bus_id": 9, "type": "1541"}}]}
    d = parse_drives(doc)
    assert [x.id for x in d] == ["a", "b"] and d[0].mounted and not d[1].mounted
    assert parse_drives({"a": {"enabled": True}})[0].id == "a"
    # Real C64 Ultimate 1.1.0s2 puts the full path in image_file.
    real = parse_drives({"drives": [{"a": {"enabled": True, "image_file": "/Temp/t2rab-1-notw.d64", "image_path": ""}}]})
    assert real[0].image_file == "t2rab-1-notw.d64" and real[0].image_path == "/Temp"


async def test_telnet_banner_is_sanitised(monkeypatch):
    import asyncio

    from app.ultimate import capabilities

    class R:
        async def read(self, n):
            return b"\xff\xfe\"\x1b[0;37;2m\x1b[1;1H*** C64 Ultimate ***"

    class W:
        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def fake_open(host, port):
        return R(), W()

    monkeypatch.setattr(asyncio, "open_connection", fake_open)
    state, evidence = await capabilities._tcp_probe("1.2.3.4", 23)
    assert state == "supported" and evidence.endswith("*** C64 Ultimate ***")
    assert "\x1b" not in evidence and "\xff" not in evidence


def test_classify():
    assert classify(UltimateResponse(400, b'{"errors":["Missing parameter"]}'))[0] == "supported"
    assert classify(UltimateResponse(404, b'{"errors":["Unknown route"]}'))[0] == "unsupported"
    assert classify(UltimateResponse(404, b'{"errors":["File not found"]}'))[0] == "supported"
    assert classify(UltimateResponse(405, b""))[0] == "unsupported"
    assert classify(UltimateResponse(403, b""))[0] == "unknown"


async def test_modern_capabilities():
    _, _, caps, inputs = await build("modern")
    d = caps.to_dict()
    assert d["firmware"] == "1.2.0"
    for name in ("menuScreen", "directKeyboard", "directJoystick", "memoryRead", "memoryWrite", "runPrg",
                 "runCrt", "mountDisk", "sidPlayback", "videoStream"):
        assert d["capabilities"][name], name
    # Side-effect routes are never probed, so they are not claimed as verified.
    assert d["capabilities"]["machineReset"] is False and d["usable"]["machineReset"] is True
    assert inputs.mode == "rest"


async def test_legacy_capabilities_do_not_fake_support():
    sim, _, caps, inputs = await build("legacy")
    d = caps.to_dict()
    assert d["firmware"] == "1.1.0s2"
    assert d["capabilities"]["directKeyboard"] is False
    assert d["capabilities"]["directJoystick"] is False
    assert d["capabilities"]["menuScreen"] is False
    # Bare 404 identical to the unknown-route baseline → decided, not left ambiguous.
    assert d["details"]["menuScreen"]["state"] == "unsupported"
    assert d["capabilities"]["legacyKeyboard"] is True
    assert inputs.mode == "legacy"
    # Probing must not have changed machine state.
    assert sim.reset_count == 0 and not sim.menu_open and sim.running is None
    assert all(d["image_file"] == "" for k, d in sim.drives.items() if "image_file" in d)


async def test_record_use_promotes_and_demotes():
    _, _, caps, _ = await build("modern")
    caps.record_use("machineReset", True)
    assert caps.supported("machineReset")
    caps.record_use("driveRom", False, not_found=True)
    assert caps.state("driveRom") == "unsupported"


def test_password_redacted_from_logs(caplog):
    register_secret("hunter2-pw")
    logger = logging.getLogger("c64.test")
    handler = logging.Handler()
    records = []
    handler.emit = records.append
    handler.addFilter(RedactingFilter())
    logger.addHandler(handler)
    logger.warning("sending X-Password: hunter2-pw to device, password=hunter2-pw")
    logger.removeHandler(handler)
    assert "hunter2-pw" not in records[0].getMessage()


def test_telnet_filter_strips_negotiation_across_chunks():
    from app.ultimate.telnet import TelnetFilter, escape_outgoing
    f = TelnetFilter()
    out = f.feed(b"\xff\xfe\x22\xff") + f.feed(b"\xfb\x01Hello \xff\xff") + f.feed(b"\xff\xfa\x18\x01\xff\xf0World")
    assert out == b"Hello \xffWorld"
    assert escape_outgoing(b"a\xffb") == b"a\xff\xffb"
