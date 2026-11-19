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


# ----- shared by the phase-2 tests (M3 notes, M4 practice, M5 style) -----

class FakeLLM:
    """Stands in for the model: deterministic, and records every request."""
    name = "fake-llm"

    def __init__(self, answer: str):
        self.answer, self.calls = answer, []

    def stream(self, system, messages, *, max_tokens, stop):
        self.calls.append((system, messages))
        for i in range(0, len(self.answer), 7):
            yield self.answer[i:i + 7]


class BrokenLLM:
    name = "broken"

    def stream(self, *a, **k):
        raise ConnectionError("down")
        yield  # pragma: no cover


def _signup(client, email: str, name: str, password: str = "phase-two-pw") -> dict:
    r = client.post("/api/v1/auth/signup", json={"email": email, "name": name, "password": password})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def peer(client, seeded):
    """A second student enrolled in the sample class."""
    h = _signup(client, "peer@pocketprof.local", "Peer Student")
    assert client.post("/api/v1/courses/join", headers=h, json={"join_code": "sample-ds"}).status_code == 200
    return h


@pytest.fixture(scope="session")
def outsider(client):
    """A student who has not joined the sample class."""
    return _signup(client, "outsider@pocketprof.local", "Outside Student")


@pytest.fixture(scope="session")
def other_teacher(client, admin):
    """A teacher who does not own the sample class."""
    r = client.post("/api/v1/admin/users", headers=admin, json={"email": "t3@pocketprof.local", "name": "Teacher Three",
                                                                "role": "teacher", "password": "teacher-three-pw"})
    assert r.status_code == 201, r.text
    return login(client, "t3@pocketprof.local", "teacher-three-pw")
