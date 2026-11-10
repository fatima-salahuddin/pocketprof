"""M4: practice generation (FR-10), teacher validation (FR-17) and spaced practice with mastery (FR-18).
Deterministic rules first, then the whole lifecycle through the API with a fake model."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pymupdf
import pytest
from conftest import FakeLLM

from pocketprof.arms import Practice
from pocketprof.practice import generator as gen_mod
from pocketprof.practice.generator import clean_item, is_delivered, parse_items
from pocketprof.practice.spacing import SpacingPolicy, mastery_estimate

ITEMS = [
    {"kind": "mcq", "source": "S1", "question": "Which order does a stack follow?",
     "options": ["A) FIFO", "B) LIFO", "C) Random", "D) Sorted"], "answer": "B",
     "explanation": "A stack is Last In, First Out."},
    {"kind": "tf", "source": "[S2]", "question": "push(x) on a stack runs in O(1) time.", "answer": "True",
     "explanation": "All four stack operations run in O(1)."},
    {"kind": "short", "source": "S1", "question": "What is stack underflow?",
     "answer": "Popping from an empty stack.", "explanation": "Underflow happens when you pop from an empty stack."},
    {"kind": "mcq", "source": "S1", "question": "Broken item with three options?", "options": ["a", "b", "c"],
     "answer": "A"},
]


@pytest.fixture
def practice_llm(monkeypatch):
    def install(items=ITEMS) -> FakeLLM:
        llm = FakeLLM("```json\n" + json.dumps(items) + "\n```")
        monkeypatch.setattr(gen_mod, "get_client", lambda gen: llm)
        return llm
    return install


# ---------- deterministic rules (SDS 4.5) ----------

def test_leitner_boxes_and_intervals():
    p = SpacingPolicy(Practice())
    assert [p.next_box(b, True) for b in range(1, 6)] == [2, 3, 4, 5, 5]
    assert [p.next_box(b, False) for b in range(1, 6)] == [1, 1, 1, 1, 1]
    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    assert [(p.next_due(now, b) - now).days for b in range(1, 6)] == [1, 2, 4, 8, 16]


def test_mastery_is_recency_weighted():
    assert mastery_estimate([]) is None
    assert mastery_estimate([True]) == 1.0
    assert mastery_estimate([False, True]) == round(0.8 / 1.8, 4)    # the most recent attempt weighs most
    assert mastery_estimate([True, False]) == round(1 / 1.8, 4)
    assert mastery_estimate([False] + [True] * 20, window=10) == round(sum(0.8 ** k for k in range(1, 10)) /
                                                                       sum(0.8 ** k for k in range(10)), 4)


def test_item_validation():
    assert clean_item(ITEMS[0], 3, ["mcq"])["options"] == ["FIFO", "LIFO", "Random", "Sorted"]
    assert clean_item({**ITEMS[0], "answer": "LIFO"}, 3, ["mcq"])["answer"] == "B"
    assert clean_item({**ITEMS[0], "answer": "1"}, 3, ["mcq"])["answer"] == "B"
    assert clean_item(ITEMS[1], 3, ["tf"])["answer"] == "true"
    assert clean_item(ITEMS[3], 3, ["mcq"]) is None                         # three options
    assert clean_item({**ITEMS[0], "source": "S9"}, 3, ["mcq"]) is None     # cites a source that does not exist
    assert clean_item(ITEMS[0], 3, ["tf"]) is None                          # kind not requested
    assert clean_item({**ITEMS[0], "options": ["a", "a", "b", "c"]}, 3, ["mcq"]) is None


def test_parse_items_discards_bad_and_duplicate_items():
    text = "Here you go:\n" + json.dumps(ITEMS + [ITEMS[0]])
    items, discarded = parse_items(text, 3, ["mcq", "tf", "short"], 10)
    assert [i["kind"] for i in items] == ["mcq", "tf", "short"] and discarded == 2
    assert parse_items("not json at all", 3, ["mcq"], 10) == ([], 0)


def test_delivered_means_posted_on_or_before_today():
    class C:
        term_start = date(2026, 9, 7)
    today = date(2026, 10, 1)
    assert is_delivered(C, day="2026-09-30", week=None, today=today)
    assert not is_delivered(C, day="2026-10-02", week=None, today=today)
    assert is_delivered(C, day=None, week=4, today=today) and not is_delivered(C, day=None, week=5, today=today)


# ---------- the lifecycle through the API ----------

def _generate(client, h, cid, **body):
    return client.post(f"/api/v1/courses/{cid}/practice/generate", headers=h, json={"n": 5, **body})


def test_generation_needs_a_teacher_of_the_course(client, student, other_teacher, seeded, practice_llm):
    practice_llm()
    assert _generate(client, student, seeded, week=3).status_code == 403
    assert _generate(client, other_teacher, seeded, week=3).status_code == 403


def test_generation_only_from_delivered_material(client, teacher, seeded, practice_llm, monkeypatch):
    llm = practice_llm()
    monkeypatch.setattr(gen_mod, "now_utc", lambda: datetime(2020, 1, 1, tzinfo=timezone.utc))
    r = _generate(client, teacher, seeded, week=3)
    assert r.status_code == 422 and "not been taught yet" in r.json()["detail"] and llm.calls == []


def test_generation_without_a_model_is_503(client, teacher, seeded, monkeypatch):
    monkeypatch.setattr(gen_mod, "get_client", lambda gen: None)
    assert _generate(client, teacher, seeded, week=3).status_code == 503


@pytest.fixture(scope="module")
def drafts(client, teacher, seeded):
    """Week 3 items generated once (a model is faked for this module's generation only)."""
    mp = pytest.MonkeyPatch()
    llm = FakeLLM(json.dumps(ITEMS))
    mp.setattr(gen_mod, "get_client", lambda gen: llm)
    try:
        r = _generate(client, teacher, seeded, week=3)
    finally:
        mp.undo()
    assert r.status_code == 201, r.text
    body = r.json()
    body["system"], body["prompt"] = llm.calls[0][0], llm.calls[0][1][-1].content
    return body


def test_generated_items_are_drafts_pinned_to_their_source(drafts):
    items = drafts["items"]
    assert [i["kind"] for i in items] == ["mcq", "tf", "short"] and drafts["discarded"] == 1
    assert all(i["status"] == "draft" and i["source"]["chunk_id"] and i["source"]["week"] == 3 for i in items)
    assert 'kind="Announcement"' not in drafts["prompt"]  # Classroom announcements are logistics, not content
    assert "JSON array" in drafts["system"] and "<S1 " in drafts["prompt"]


def test_students_never_see_drafts(client, student, seeded, drafts):
    view = client.get(f"/api/v1/courses/{seeded}/practice/due", headers=student).json()
    ids = {i["id"] for i in view["session"] + view["optional"]}
    assert not ids & {i["id"] for i in drafts["items"]}


def test_validation_queue_approve_edit_reject(client, teacher, student, seeded, drafts):
    mcq, tf, short = drafts["items"]
    queue = client.get(f"/api/v1/courses/{seeded}/validation", headers=teacher).json()
    assert {mcq["id"], tf["id"], short["id"]} <= {i["id"] for i in queue["items"]}
    assert queue["counts"]["draft"] >= 3
    assert client.get(f"/api/v1/courses/{seeded}/validation", headers=student).status_code == 403

    r = client.patch(f"/api/v1/practice/{mcq['id']}", headers=teacher,
                     json={"action": "approve", "question": "Which order does a stack follow? (Week 3)"})
    assert r.status_code == 200 and r.json()["status"] == "released" and r.json()["edited"]  # week 3 is delivered
    assert client.patch(f"/api/v1/practice/{short['id']}", headers=teacher, json={"action": "approve"}).json()["status"] \
        == "released"
    assert client.patch(f"/api/v1/practice/{tf['id']}", headers=teacher, json={"action": "reject"}).status_code == 422
    r = client.patch(f"/api/v1/practice/{tf['id']}", headers=teacher, json={"action": "reject", "reason": "Too easy"})
    assert r.json()["status"] == "rejected" and r.json()["reject_reason"] == "Too easy"
    bad = client.patch(f"/api/v1/practice/{mcq['id']}", headers=teacher, json={"action": "approve", "answer": "Z"})
    assert bad.status_code == 409  # already released; edits go through approve on draft/approved items only


def test_session_attempts_spacing_and_mastery(client, student, peer, seeded, drafts):
    mcq, tf, short = drafts["items"]
    view = client.get(f"/api/v1/courses/{seeded}/practice/due", headers=student).json()
    shown = {i["id"]: i for i in view["session"]}
    assert mcq["id"] in shown and short["id"] in shown and tf["id"] not in shown  # rejected never shown
    assert "answer" not in shown[mcq["id"]] and "explanation" not in shown[mcq["id"]]

    r = client.post(f"/api/v1/practice/{mcq['id']}/attempts", headers=student, json={"answer": "b"}).json()
    assert r["correct"] and r["box"] == 2 and r["interval_days"] == 2 and r["correct_answer"] == "B"
    assert r["source"]["chunk_id"] == mcq["source"]["chunk_id"]
    assert r["mastery"]["topic"] == "Stacks and queues" and r["mastery"]["estimate"] == 1.0
    r = client.post(f"/api/v1/practice/{mcq['id']}/attempts", headers=student, json={"answer": "A"}).json()
    assert not r["correct"] and r["box"] == 1 and r["interval_days"] == 1
    assert r["mastery"]["estimate"] == round(0.8 / 1.8, 4)

    # short answers: reveal the model answer, then self-mark
    assert client.post(f"/api/v1/practice/{short['id']}/attempts", headers=student,
                       json={"answer": "popping too much"}).status_code == 422
    assert "empty stack" in client.post(f"/api/v1/practice/{short['id']}/reveal", headers=student).json()["answer"]
    r = client.post(f"/api/v1/practice/{short['id']}/attempts", headers=student,
                    json={"answer": "pop on empty", "self_correct": True}).json()
    assert r["correct"] and r["self_marked"]

    # attempted items are scheduled, so they leave the session until due; another student is unaffected
    later = client.get(f"/api/v1/courses/{seeded}/practice/due", headers=student).json()
    assert mcq["id"] not in {i["id"] for i in later["session"]}
    assert mcq["id"] in {i["id"] for i in client.get(f"/api/v1/courses/{seeded}/practice/due", headers=peer).json()["session"]}
    assert any(m["topic"] == "Stacks and queues" for m in later["mastery"])


def test_attempt_access(client, outsider, teacher, seeded, drafts):
    mcq = drafts["items"][0]
    assert client.post(f"/api/v1/practice/{mcq['id']}/attempts", headers=outsider, json={"answer": "B"}).status_code == 404
    assert client.post(f"/api/v1/practice/{mcq['id']}/attempts", headers=teacher, json={"answer": "B"}).status_code == 403
    assert client.post(f"/api/v1/practice/{mcq['id']}/attempts", json={"answer": "B"}).status_code == 401


def test_teacher_stats_respect_the_minimum_cohort(client, teacher, seeded, drafts):
    rows = client.get(f"/api/v1/courses/{seeded}/practice/items?status=released", headers=teacher).json()
    mcq = next(r for r in rows if r["id"] == drafts["items"][0]["id"])
    assert mcq["stats"] == {"suppressed": True, "min_cohort": 5}  # one student so far: nothing is shown


def test_practice_paper_export(client, student, teacher, seeded, drafts):
    r = client.post("/api/v1/exports", headers=student, json={"kind": "paper", "ref": seeded})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    text = "".join(p.get_text() for p in pymupdf.open("pdf", r.content))
    assert "practice paper" in text and "Answer key" in text and "Which order does a stack follow?" in text
    assert "Lecture3_Stacks_Queues.pptx" in text
    r = client.post("/api/v1/exports", headers=student, json={"kind": "paper", "ref": seeded, "topic": "Hashing"})
    assert r.status_code == 422
    assert client.post("/api/v1/exports", headers=teacher, json={"kind": "paper", "ref": seeded}).status_code == 200


def test_unknown_item_is_404(client, student, teacher):
    assert client.post("/api/v1/practice/999999/attempts", headers=student, json={"answer": "A"}).status_code == 404
    assert client.patch("/api/v1/practice/999999", headers=teacher, json={"action": "approve"}).status_code == 404


def test_no_week_selected_is_rejected(client, teacher, seeded):
    assert _generate(client, teacher, seeded).status_code == 422
    assert _generate(client, teacher, seeded, week=3, topic="Hashing").status_code == 422


def test_due_is_for_students(client, teacher, seeded):
    assert client.get(f"/api/v1/courses/{seeded}/practice/due", headers=teacher).status_code == 403


# Runs last: retiring the week 3 items changes what the tests above see.
def test_changed_source_file_retires_its_items(client, teacher, student, seeded, drafts):
    from pocketprof.db import session_scope
    from pocketprof.db.models import Material, PracticeItem
    short = drafts["items"][2]
    with session_scope() as db:
        item = db.get(PracticeItem, short["id"])
        mat = db.get(Material, item.material_id)
        original, mat.file_hash = mat.file_hash, "f" * 64
    try:
        r = client.post(f"/api/v1/practice/{short['id']}/attempts", headers=student, json={"answer": "x",
                                                                                          "self_correct": True})
        assert r.status_code == 409 and "retired" in r.json()["detail"]
        retired = client.get(f"/api/v1/courses/{seeded}/practice/items?status=retired", headers=teacher).json()
        assert {short["id"], drafts["items"][0]["id"]} <= {i["id"] for i in retired}  # same source file
    finally:
        with session_scope() as db:
            mat = db.get(Material, db.get(PracticeItem, short["id"]).material_id)
            mat.file_hash = original
