import asyncio

import httpx

from app.services.power_plug import PowerPlug


class _S:
    def __init__(self, kind, host):
        self.POWER_PLUG_TYPE, self.POWER_PLUG_HOST = kind, host


def _plug(kind, state):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        q = str(req.url)
        if "turn=on" in q or "on=true" in q or "Power%20On" in q:
            state["on"] = True
            state.setdefault("history", []).append("on")
        elif "turn=off" in q or "on=false" in q or "Power%20Off" in q:
            state["on"] = False
            state.setdefault("history", []).append("off")
        if kind == "shelly":
            return httpx.Response(200, json={"ison": state["on"]})
        if kind == "shelly-gen2":
            return httpx.Response(200, json={"output": state["on"]})
        return httpx.Response(200, json={"POWER": "ON" if state["on"] else "OFF"})
    return PowerPlug(lambda: _S(kind, "192.168.1.50"), http=httpx.AsyncClient(transport=httpx.MockTransport(handler))), seen


def test_plug_types_and_power_cycle():
    for kind, on_url in [("shelly", "/relay/0?turn=on"), ("shelly-gen2", "/rpc/Switch.Set?id=0&on=true"),
                         ("tasmota", "/cm?cmnd=Power%20On")]:
        state = {"on": True}                                   # the plug is on, the C64 was soft-powered off
        plug, seen = _plug(kind, state)
        assert plug.configured
        asyncio.run(plug.power_cycle_on(off_seconds=0))
        assert state["on"] and seen[-1] == f"http://192.168.1.50{on_url}"
        assert state["history"] == ["off", "on"]                 # a clean power cycle
    assert not PowerPlug(lambda: _S("shelly", "http://evil/x?y")).configured   # only a bare host or IP
    assert not PowerPlug(lambda: _S("", "192.168.1.50")).configured


def test_power_endpoints(app_client):
    with app_client() as c:
        st = c.get("/api/device/power").json()
        assert st["c64"] == "on" and st["plug"]["configured"] is False
        r = c.post("/api/device/power-on")
        assert r.status_code == 409 and "smart plug" in r.json()["detail"]
        assert c.post("/api/device/power-off", json={"confirm": False}).status_code == 400
        assert c.post("/api/device/power-off", json={"confirm": True}).json()["ok"]
        assert c.get("/api/device/power").json()["c64"] == "off"
