import os
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from inspector.config import DashboardConfig, NodeConfig, Settings
from inspector.dashboard import create_app
from inspector.store import SqliteStore


@pytest_asyncio.fixture
async def app(tmp_path):
    db_path = tmp_path / "test.db"
    store = SqliteStore(str(db_path))
    await store.setup()
    cfg = Settings(
        nodes=[NodeConfig(name="dummy", host="127.0.0.1", user="nobody")],
        dashboard=DashboardConfig(enabled=True, host="127.0.0.1", port=8080, token_env="TK"),
    )
    os.environ["TK"] = "secret"
    return create_app(cfg, store)


def test_dashboard_requires_token(app):
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 401

    resp = client.get("/", headers={"X-Inspect-Token": "secret"})
    assert resp.status_code == 200
