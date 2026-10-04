import asyncio

from app.services.scheduler import DAY, Job, summarize


def test_summaries():
    assert summarize({"added": 3, "errors": {"CSDb": "x"}}) == "3 added, couldn't reach CSDb"
    assert summarize({"latest": "1.2.0", "newer": True}) == "latest 1.2.0 — newer than yours"
    assert summarize(4) == "4 updated" and summarize(None) == "done"


def test_jobs_listing_configure_and_run(app_client):
    with app_client() as c:
        sch = c.app.state.container.scheduler
        keys = {j["key"] for j in c.get("/api/updates").json()["jobs"]}
        assert {"news", "release-stats", "events-csdb", "events-research", "firmware", "magazines",
                "hardware-catalog", "price-watches"} <= keys
        jobs = {j["key"]: j for j in c.get("/api/updates").json()["jobs"]}
        assert jobs["price-watches"]["blocked"] and jobs["events-research"]["blocked"]     # no eBay keys / no AI
        assert jobs["news"]["every"] == 1800 and jobs["news"]["nextAt"]                     # never run → due now
        # change the interval / switch off
        r = c.patch("/api/updates/events-csdb", json={"every": 7 * DAY}).json()
        assert r["every"] == 7 * DAY
        assert c.patch("/api/updates/events-csdb", json={"every": 123}).status_code == 400
        assert c.patch("/api/updates/events-csdb", json={"enabled": False}).json()["nextAt"] is None
        assert c.patch("/api/updates/nope", json={"enabled": False}).status_code == 404
        # run a job: its result and timing are remembered
        calls = []

        async def work():
            calls.append(1)
            return {"added": 2}

        def boom():
            raise RuntimeError("site down")

        sch.add(Job("test-ok", "Test", "news", DAY, work))
        sch.add(Job("test-bad", "Broken", "news", DAY, boom))
        asyncio.run(sch.run_job("test-ok"))
        asyncio.run(sch.run_job("test-bad"))
        jobs = {j["key"]: j for j in c.get("/api/updates").json()["jobs"]}
        assert jobs["test-ok"]["lastOk"] and jobs["test-ok"]["lastSummary"] == "2 added" and calls == [1]
        assert jobs["test-bad"]["lastOk"] is False and "site down" in jobs["test-bad"]["lastError"]
        assert "test-ok" not in sch._due() and "news" in sch._due() and "events-csdb" not in sch._due()
