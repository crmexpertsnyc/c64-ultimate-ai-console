def test_netplay_room_and_signaling(app_client):
    import sys
    sys.path.insert(0, "tests")
    from test_recommend import _game
    with app_client() as c:
        gid = _game(c, "Bubble Bobble")
        room = c.post("/api/netplay/rooms", json={"gameId": gid}).json()
        code = room["code"]
        assert len(code) == 6 and room["title"] == "Bubble Bobble" and room["stun"]
        assert c.get("/api/netplay/rooms/NOPE00").status_code == 404
        assert c.post("/api/netplay/rooms", json={"gameId": 9999}).status_code == 404
        with c.websocket_connect(f"/ws/netplay/{code}?role=host") as host:
            info = c.get(f"/api/netplay/rooms/{code}").json()
            assert info["hostOnline"] and info["guests"] == 0
            with c.websocket_connect(f"/ws/netplay/{code}?role=guest&name=Maya") as guest:
                welcome = guest.receive_json()
                assert welcome["type"] == "welcome" and welcome["hostOnline"]
                joined = host.receive_json()
                assert joined == {"type": "guest-joined", "guestId": welcome["guestId"], "name": "Maya"}
                host.send_json({"type": "offer", "to": welcome["guestId"], "data": {"sdp": "v=0"}})
                assert guest.receive_json() == {"type": "offer", "data": {"sdp": "v=0"}}
                guest.send_json({"type": "hack", "data": "x"})          # unknown types are dropped
                guest.send_json({"type": "answer", "data": {"sdp": "v=1"}})
                assert host.receive_json() == {"type": "answer", "from": welcome["guestId"], "data": {"sdp": "v=1"}}
            assert host.receive_json() == {"type": "guest-left", "guestId": welcome["guestId"]}
        assert c.get(f"/api/netplay/rooms/{code}").status_code == 404      # host left → room closed
