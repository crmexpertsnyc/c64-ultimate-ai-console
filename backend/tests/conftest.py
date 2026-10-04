from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import ConfigStore, Settings  # noqa: E402
from app.ultimate.capabilities import CapabilityMatrix, CapabilityProber  # noqa: E402
from app.ultimate.client import UltimateClient  # noqa: E402
from app.ultimate.input import InputController  # noqa: E402
from app.ultimate.simulator import SimulatedUltimate  # noqa: E402
from app.ultimate.transport import SimulatedTransport  # noqa: E402


async def build(profile: str = "modern"):
    sim = SimulatedUltimate(profile=profile, load_delay=0.05)
    client = UltimateClient(SimulatedTransport(sim))
    caps = CapabilityMatrix()
    caps.__dict__.update((await CapabilityProber(client).probe()).__dict__)
    inputs = InputController(client, caps, type_delay_ms=0)
    return sim, client, caps, inputs


@pytest.fixture
def make_config(tmp_path):
    def _make(**overrides) -> ConfigStore:
        base = {"DATA_DIR": str(tmp_path / "data"), "SIMULATE_C64": True, "CORS_ORIGINS": "",
                "COVER_ART_ONLINE": False, "NEWS_MONITOR": False, **overrides}
        return ConfigStore(Settings(_env_file=None, **base), overrides_file=tmp_path / "data" / "settings.json")
    return _make


@pytest.fixture
def app_client(make_config):
    from fastapi.testclient import TestClient

    from app.main import create_app

    def _make(**overrides):
        return TestClient(create_app(make_config(**overrides)))
    return _make


@pytest.fixture(autouse=True)
def _no_archive_lookups(monkeypatch):
    """Tests never reach the online archives (Ask looks games up there); test_sources opts back in with fakes."""
    from app.services import sources

    async def none(title, limit=3):  # noqa: ANN001, ARG001
        return []

    monkeypatch.setattr(sources, "archive_matches", none)
