"""Test configuration: an isolated data folder (SQLite + embedded Qdrant), no scheduler, no real model.

The embedding models are real (they are part of what is under test) and shared with the development
cache so they are downloaded only once.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="pocketprof-test-"))
_REPO = Path(__file__).resolve().parents[2]
os.environ.update({
    "ENV": "development", "DATA_DIR": str(_TMP), "MODEL_CACHE_DIR": str(_REPO / "data" / "models"),
    "DATABASE_URL": "", "QDRANT_URL": "", "RUN_SCHEDULER": "false", "SYNC_SECONDS": "0",
    "GEN_PROVIDER": "none", "GEN_BASE_URL": "", "GEN_API_KEY": "", "GOOGLE_CLIENT_ID": "", "GOOGLE_CLIENT_SECRET": "",
    "JWT_SECRET": "test-secret-test-secret-test-secret-123", "PSEUDONYM_KEY": "test-pseudonym",
    "SEED_PASSWORD": "seed-password-1", "FORCE_IPV4": "false", "RATE_LIMIT_PER_MINUTE": "1000",
})

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def seeded():
    """The sample course, indexed once for the whole session."""
    from pocketprof.seed import seed
    return seed()


@pytest.fixture(scope="session")
def client(seeded):
    from fastapi.testclient import TestClient

    from pocketprof.main import app
    with TestClient(app) as c:
        yield c


def login(client, email: str, password: str = "seed-password-1") -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def student(client):
    return login(client, "student@pocketprof.local")


@pytest.fixture(scope="session")
def teacher(client):
    return login(client, "teacher@pocketprof.local")


@pytest.fixture(scope="session")
def admin(client):
    return login(client, "admin@pocketprof.local")


def pytest_sessionfinish(session, exitstatus):
    from pocketprof.retrieval import vector_index
    vector_index._close()
    shutil.rmtree(_TMP, ignore_errors=True)
