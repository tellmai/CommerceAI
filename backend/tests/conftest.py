import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy.engine import make_url

TEST_URL = os.getenv("TEST_DATABASE_URL")
if TEST_URL:
    if not (make_url(TEST_URL).database or "").endswith("_test"):
        raise RuntimeError("Integration tests require a separate database ending in _test")
    os.environ["DATABASE_URL"] = TEST_URL
os.environ["OWNER_PASSWORD"] = "commerceai-test-password"


@pytest.fixture(scope="session")
def seeded_database():
    if not TEST_URL:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL test database")
    from app.db import Base, engine

    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    Base.metadata.drop_all(engine)
    migration = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        env=os.environ.copy(),
    )
    assert migration.returncode == 0, migration.stderr[-4000:]
    from app.seed import seed_demo

    seed_demo()
    yield
    engine.dispose()


@pytest.fixture
def client(seeded_database):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.post("/api/v1/auth/login", json={"password": "commerceai-test-password"})
        assert response.status_code == 200
        client.headers.update({"X-CSRF-Token": response.json()["csrf"], "X-Workspace": "demo"})
        yield client


@pytest.fixture
def evidence(client):
    from uuid import uuid4
    from datetime import UTC, datetime

    source = client.post(
        "/api/v1/sources",
        json={
            "name": "测试来源" + str(uuid4()),
            "platform": "TEST",
            "permission_scope": ["*"],
            "permission_note": "本地隔离测试数据",
        },
    )
    assert source.status_code == 201, source.text
    record = client.post(
        "/api/v1/evidence",
        json={
            "source_id": source.json()["id"],
            "locator": "test://" + str(uuid4()),
            "observed_at": datetime.now(UTC).isoformat(),
        },
    )
    assert record.status_code == 201, record.text
    return record.json(), source.json()
