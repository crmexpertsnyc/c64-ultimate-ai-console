def test_home_hub_sections(app_client):
    with app_client() as c:
        h = c.get("/api/home").json()
        assert set(h) == {"thisWeek", "events", "achievements", "hardware"}
        assert set(h["thisWeek"]) == {"digest", "releases", "news", "videos"}
        assert h["events"]["homeCountry"] == "United States" and h["events"]["items"] == []
        assert h["hardware"] == {"products": [], "deals": []} and h["achievements"] == []
