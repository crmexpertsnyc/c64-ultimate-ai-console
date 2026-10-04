import pytest
from starlette.websockets import WebSocketDisconnect

from app import auth


@pytest.fixture(autouse=True)
def _reset_failures():
    auth._failures.clear()
    yield
    auth._failures.clear()


def test_hashing():
    h = auth.hash_password("secret123")
    assert h.startswith("pbkdf2_sha256$") and "secret123" not in h
    assert auth.verify_password("secret123", h) and not auth.verify_password("nope", h)


def test_no_password_means_open(app_client):
    with app_client() as c:
        assert c.get("/api/library").status_code == 200
        assert c.get("/api/auth/status").json()["enabled"] is False


def test_password_protects_api_and_websockets(app_client):
    with app_client() as c:
        # Set a password (the test client is a remote device; no password yet, so allowed).
        r = c.put("/api/auth/password", json={"new": "retro64!"})
        assert r.json()["enabled"] is True
        c.cookies.clear()                                   # another device, not signed in
        assert c.get("/api/library").status_code == 401
        assert c.get("/api/library").json()["kind"] == "auth"
        assert c.get("/api/health").status_code == 200      # public
        assert c.get("/").status_code == 200                # app shell loads (shows sign-in)
        st = c.get("/api/auth/status").json()
        assert st == {"enabled": True, "local": False, "signedIn": False, "remoteDevices": 0}
        with pytest.raises(WebSocketDisconnect), c.websocket_connect("/ws") as ws:
            ws.receive_json()
        assert c.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
        assert c.post("/api/auth/login", json={"password": "retro64!"}).status_code == 200
        assert c.get("/api/library").status_code == 200
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "status"
        c.post("/api/auth/logout")
        c.cookies.clear()
        assert c.get("/api/library").status_code == 401


def test_generic_settings_cannot_set_password(app_client):
    with app_client() as c:
        c.put("/api/settings", json={"APP_PASSWORD_HASH": "pbkdf2_sha256$1$AAAA$AAAA"})
        assert c.get("/api/auth/status").json()["enabled"] is False
        assert "APP_PASSWORD_HASH_SET" in c.get("/api/settings").json()  # redacted like other secrets


def test_change_needs_current_password_and_signs_others_out(app_client):
    with app_client() as c:
        c.put("/api/auth/password", json={"new": "first-pass"})
        old_cookie = c.cookies.get(auth.COOKIE)
        assert c.put("/api/auth/password", json={"current": "bad", "new": "second-pass"}).status_code == 403
        assert c.put("/api/auth/password", json={"current": "first-pass", "new": "second-pass"}).status_code == 200
        c.cookies.clear()
        c.cookies.set(auth.COOKIE, old_cookie)            # a device still holding the old session
        assert c.get("/api/library").status_code == 401
        c.cookies.clear()
        assert c.post("/api/auth/login", json={"password": "second-pass"}).status_code == 200
        assert c.put("/api/auth/password", json={"current": "second-pass", "new": ""}).json()["enabled"] is False
        c.cookies.clear()
        assert c.get("/api/library").status_code == 200    # password removed: open again


def test_guessing_is_rate_limited(app_client):
    with app_client() as c:
        c.put("/api/auth/password", json={"new": "retro64!"})
        c.cookies.clear()
        codes = [c.post("/api/auth/login", json={"password": f"guess{i}"}).status_code for i in range(6)]
        assert codes[:5] == [401] * 5 and codes[5] == 429
        assert c.post("/api/auth/login", json={"password": "retro64!"}).status_code == 429  # locked for a while


def test_local_detection():
    base = {"type": "http", "client": ("127.0.0.1", 5000), "headers": [(b"host", b"127.0.0.1:8064")]}
    assert auth.is_local(base)
    assert auth.is_local({**base, "headers": [(b"host", b"localhost:8064")]})
    # Tailscale Serve connects from 127.0.0.1 but is a remote device.
    assert not auth.is_local({**base, "headers": [(b"host", b"console.tailnet-example.ts.net"),
                                                   (b"x-forwarded-for", b"100.112.132.47")]})
    assert not auth.is_local({**base, "headers": [(b"host", b"127.0.0.1:8064"), (b"tailscale-user-login", b"me")]})
    assert not auth.is_local({**base, "client": ("192.168.1.50", 5000)})


def test_remote_devices_counted_without_password(app_client):
    from app import auth
    auth._remote_seen.clear()
    with app_client() as client:
        client.get("/api/health")                   # the test client is not "this computer"
        st = client.get("/api/auth/status").json()
    assert st["enabled"] is False and st["local"] is False and st["remoteDevices"] >= 1
