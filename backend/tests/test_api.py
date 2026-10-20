"""Integration tests against the real app, the seeded sample course and the real vector index
(SDS Table 11: authorisation matrix, isolation, chat main scenario, incremental provisioning)."""
from __future__ import annotations

import json

import pytest
from conftest import login

from pocketprof.generation import responder as responder_mod


def sse_events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        ev = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if ev:
            out.append((ev, data))
    return out


def ask(client, headers, course_id, message, **extra) -> dict:
    r = client.post(f"/api/v1/courses/{course_id}/chat", headers=headers, json={"message": message, **extra})
    assert r.status_code == 200, r.text
    events = sse_events(r.text)
    done = [d for e, d in events if e == "done"]
    assert done, events
    return {"events": events, **done[0]}


class FakeLLM:
    """Stands in for the model so tests are deterministic; records what it was asked."""
    name = "fake-llm"

    def __init__(self, answer: str):
        self.answer, self.calls = answer, []

    def stream(self, system, messages, *, max_tokens, stop):
        self.calls.append((system, messages))
        for i in range(0, len(self.answer), 7):
            yield self.answer[i:i + 7]


@pytest.fixture
def fake_llm(monkeypatch):
    def install(answer: str) -> FakeLLM:
        llm = FakeLLM(answer)
        monkeypatch.setattr(responder_mod, "get_client", lambda gen: llm)
        return llm
    return install


# ----- authentication -----

def test_login_rejects_bad_password(client):
    r = client.post("/api/v1/auth/login", json={"email": "student@pocketprof.local", "password": "nope"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")


def test_refresh_rotation_and_reuse_detection(client):
    r = client.post("/api/v1/auth/login", json={"email": "teacher@pocketprof.local", "password": "seed-password-1"})
    first = r.cookies.get("pp_refresh")
    r2 = client.post("/api/v1/auth/refresh", cookies={"pp_refresh": first})
    assert r2.status_code == 200
    second = r2.cookies.get("pp_refresh")
    assert second and second != first
    # Presenting the rotated token again signals theft: the whole family is revoked.
    assert client.post("/api/v1/auth/refresh", cookies={"pp_refresh": first}).status_code == 401
    assert client.post("/api/v1/auth/refresh", cookies={"pp_refresh": second}).status_code == 401


def test_requests_without_token_are_rejected(client, seeded):
    assert client.get("/api/v1/courses").status_code == 401
    assert client.post(f"/api/v1/courses/{seeded}/chat", json={"message": "hi"}).status_code == 401


# ----- authorisation matrix (FR-15) -----

def test_student_sees_only_enrolled_courses(client, student, seeded):
    courses = client.get("/api/v1/courses", headers=student).json()
    assert [c["id"] for c in courses] == [seeded]
    assert courses[0]["join_code"] is None  # staff-only field


def test_role_checks(client, student, teacher, admin, seeded):
    assert client.get(f"/api/v1/courses/{seeded}/materials", headers=student).status_code == 403
    assert client.get(f"/api/v1/courses/{seeded}/materials", headers=teacher).status_code == 200
    assert client.get("/api/v1/admin/users", headers=teacher).status_code == 403
    assert client.get("/api/v1/admin/users", headers=admin).status_code == 200
    assert client.get(f"/api/v1/courses/{seeded}/questions", headers=student).status_code == 403


@pytest.fixture(scope="module")
def other_course(client, admin):
    """A second teacher with their own upload course, for isolation tests."""
    r = client.post("/api/v1/admin/users", headers=admin, json={"email": "t2@pocketprof.local", "name": "Teacher Two",
                                                                "role": "teacher", "password": "teacher-two-pw"})
    assert r.status_code == 201, r.text
    t2 = login(client, "t2@pocketprof.local", "teacher-two-pw")
    c = client.post("/api/v1/courses", headers=t2, json={"code": "MT-1001", "title": "Calculus"}).json()
    files = {"files": ("Limits.txt", b"Calculus. The limit of sin(x)/x as x approaches 0 equals 1. "
                                     b"L'Hopital's rule applies to indeterminate forms such as 0/0.", "text/plain")}
    assert client.post(f"/api/v1/courses/{c['id']}/materials", headers=t2, files=files).status_code == 201
    from pocketprof.ingestion.provisioning import ProvisioningJob
    ProvisioningJob(c["id"], "manual").run()  # synchronous, so the test does not race the background run
    return c["id"], t2


def test_teacher_cannot_touch_another_teachers_course(client, teacher, other_course):
    cid, _ = other_course
    assert client.get(f"/api/v1/courses/{cid}/materials", headers=teacher).status_code == 403
    assert client.post(f"/api/v1/courses/{cid}/sync", headers=teacher).status_code == 403


def test_student_cannot_chat_in_a_course_they_have_not_joined(client, student, other_course):
    cid, _ = other_course
    r = client.post(f"/api/v1/courses/{cid}/chat", headers=student, json={"message": "what is a limit?"})
    assert r.status_code == 404  # unknown and forbidden look the same to students


def test_course_isolation_across_many_queries(seeded, other_course):
    """No query in one course may return the other course's chunks (SDS Table 9; scaled-down 500-query test)."""
    from pocketprof.arms import load_arm
    from pocketprof.retrieval.retriever import retrieve
    from pocketprof.retrieval.scoper import Scope
    cid, _ = other_course
    arm = load_arm("shipped-v1")
    probes = ["limit of sin x over x", "L'Hopital rule", "calculus", "stack", "binary search tree", "hash table",
              "indeterminate forms", "quiz", "deadline", "linked list"] * 5
    for q in probes:
        for course in (seeded, cid):
            for h in retrieve(course, q, arm, Scope()).hits:
                assert h.payload["course_id"] == course


# ----- chat: the main scenario (FR-4 to FR-7) -----

def test_answer_with_citations(client, student, seeded, fake_llm):
    llm = fake_llm("Replace the node with its in-order successor, then delete the successor [S1].")
    res = ask(client, student, seeded, "How do I delete a node with two children?")
    assert res["outcome"] == "answered"
    assert res["citations"] and res["citations"][0]["file"] == "Week4_BST_Notes.pdf"
    system, messages = llm.calls[0]
    assert "NOT_IN_MATERIAL" in system and "<S1 " in messages[-1].content
    deltas = "".join(d["text"] for e, d in res["events"] if e == "delta")
    assert deltas.strip() == res["answer"]


def test_out_of_material_question_is_refused_without_calling_the_model(client, student, seeded, fake_llm):
    llm = fake_llm("This should never be produced [S1].")
    res = ask(client, student, seeded, "Who won the cricket world cup?")
    assert res["outcome"] == "refused" and not res["citations"]
    assert llm.calls == []


def test_sentinel_becomes_refusal_and_never_streams(client, student, seeded, fake_llm):
    fake_llm("NOT_IN_MATERIAL")
    res = ask(client, student, seeded, "What is the duration of the final exam for stacks?")
    assert res["outcome"] == "refused"
    assert not [d for e, d in res["events"] if e == "delta"]


def test_uncited_answer_is_not_shown(client, student, seeded, fake_llm):
    fake_llm("A stack is a data structure that follows LIFO order.")
    res = ask(client, student, seeded, "What is a stack?")
    assert res["outcome"] == "fallback" and res["answer"] == "" and res["show_passages"]


def test_model_failure_fails_closed(client, student, seeded, monkeypatch):
    class Broken:
        name = "broken"

        def stream(self, *a, **k):
            raise ConnectionError("down")
            yield  # pragma: no cover

    monkeypatch.setattr(responder_mod, "get_client", lambda gen: Broken())
    res = ask(client, student, seeded, "Explain the circular queue full condition")
    assert res["outcome"] == "fallback" and res["sources"] and "could not answer" in res["notice"]


def test_scoped_question_filters_to_the_week(client, student, seeded, fake_llm):
    fake_llm("Week 5 covered singly linked lists, insertion and deletion [S1].")
    res = ask(client, student, seeded, "What did we cover in week 5?")
    meta = [d for e, d in res["events"] if e == "meta"][0]
    assert meta["scope"]["week"] == 5
    assert {s["week"] for s in meta["sources"]} == {5}


def test_threads_keep_history_and_citations(client, student, seeded, fake_llm):
    fake_llm("Quiz 3 is on Monday 26 October 2026 [S1].")
    first = ask(client, student, seeded, "When is quiz 3?")
    llm = fake_llm("It covers linked lists and hashing [S1].")
    ask(client, student, seeded, "and what does it cover?", thread_id=first["thread_id"])
    assert any("quiz 3" in m.content.lower() for m in llm.calls[0][1][:-1])  # earlier turn sent as history
    t = client.get(f"/api/v1/threads/{first['thread_id']}", headers=student).json()
    assert [m["role"] for m in t["messages"]] == ["user", "assistant", "user", "assistant"]
    assert t["messages"][1]["citations"]


def test_teacher_sees_questions_without_identity(client, teacher, seeded):
    rows = client.get(f"/api/v1/courses/{seeded}/questions", headers=teacher).json()
    assert rows and all(set(r) == {"question", "outcome", "week", "topic", "ts"} for r in rows)
    signal = client.get(f"/api/v1/courses/{seeded}/signal", headers=teacher).json()
    assert signal["rows"] == []  # one student is below the minimum cohort of 5, so nothing is shown


# ----- ingestion through the API (FR-3, FR-4, FR-24) -----

def wait_idle(course_id: int, timeout_s: float = 60) -> None:
    import time

    from pocketprof.ingestion.provisioning import is_running
    end = time.time() + timeout_s
    while is_running(course_id) and time.time() < end:
        time.sleep(0.1)


def test_upload_replace_and_withdraw(client, other_course):
    from pocketprof.ingestion.provisioning import ProvisioningJob
    cid, t2 = other_course
    runs = lambda: client.get(f"/api/v1/courses/{cid}/runs", headers=t2).json()  # noqa: E731
    wait_idle(cid)
    ProvisioningJob(cid, "manual").run()
    assert runs()[0]["counts"]["unchanged"] == 1 and runs()[0]["counts"]["parsed"] == 0

    files = {"files": ("Limits.txt", b"Updated notes. The limit of (1 + 1/n)^n as n grows is e.", "text/plain")}
    client.post(f"/api/v1/courses/{cid}/materials", headers=t2, files=files)  # starts its own sync
    wait_idle(cid)
    latest = runs()[0]
    assert latest["trigger"] == "upload" and latest["counts"]["parsed"] == 1

    mats = client.get(f"/api/v1/courses/{cid}/materials", headers=t2).json()
    assert client.delete(f"/api/v1/courses/{cid}/materials/{mats[0]['id']}", headers=t2).status_code == 204
    course = client.get(f"/api/v1/courses/{cid}", headers=t2).json()
    assert course["n_chunks"] == 0


def test_bad_upload_is_rejected(client, other_course):
    cid, t2 = other_course
    r = client.post(f"/api/v1/courses/{cid}/materials", headers=t2,
                    files={"files": ("fake.pdf", b"not a pdf at all", "application/pdf")})
    assert r.status_code == 422 and "not a PDF" in r.json()["detail"]


def test_calendar_validation_reports_every_bad_row(client, other_course):
    cid, t2 = other_course
    csv = "date,week\n2026-09-14,1\nnot-a-date,2\n2026-09-28,99\n"
    r = client.put(f"/api/v1/courses/{cid}/calendar", headers=t2, files={"file": ("cal.csv", csv, "text/csv")})
    assert r.status_code == 422
    assert [e["row"] for e in r.json()["errors"]] == [3, 4]


def test_health_and_metrics(client):
    assert client.get("/health").json()["status"] == "ok"
    assert "pocketprof_http_requests_total" in client.get("/metrics").text
