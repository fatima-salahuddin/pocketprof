"""M5: instructor style adaptation (FR-11; SRS UC-09; E4 arms). Calibration -> reviewable rules and
exemplars -> a STYLE block in generation requests for that teacher's courses only, which never loosens
grounding."""
from __future__ import annotations

import pytest
from conftest import FakeLLM
from test_api import ask

from pocketprof.arms import load_arm
from pocketprof.generation import responder as responder_mod
from pocketprof.style import service as style_mod
from pocketprof.style.service import GUARD, build_block, measure, parse_rules, rules_from_measures

ANSWERS = {
    "explain": "Think of a linked list as a train: each coach holds a value and is hooked to the next one. "
               "You reach any coach by walking from the engine, the head. Simple, na?",
    "confused": "No problem, beta. Let's go slowly. What do you think happens if two keys land on the same "
                "index? Try one small example on paper first, then we compare.",
    "everyday": "Imagine a stack of plates in the hostel mess: you always take the top plate first. For "
                "example, undo in your editor works the same way.",
    "mistake": "The most common mistake is forgetting the empty case. Before you pop, ask yourself: is the "
               "stack empty? Write that check first, every time.",
    "encourage": "One quiz does not define you. Look at which question cost you marks, practise two like it, "
                 "and you will see the difference next week.",
}
RULES_REPLY = """- Starts with a familiar everyday analogy before the formal idea.
- Uses short sentences and speaks to the student as "you".
- Asks the student to try a small example themselves.
- Mixes in a friendly Urdu-English word now and then.
- Stays warm and encouraging."""


@pytest.fixture
def style_llm(monkeypatch):
    llm = FakeLLM(RULES_REPLY)
    monkeypatch.setattr(style_mod, "get_client", lambda gen: llm)
    return llm


@pytest.fixture
def chat_llm(monkeypatch):
    def install(answer: str) -> FakeLLM:
        llm = FakeLLM(answer)
        monkeypatch.setattr(responder_mod, "get_client", lambda gen: llm)
        return llm
    return install


def _answers(client, teacher, keys=ANSWERS):
    prompts = client.get("/api/v1/teachers/me/calibration", headers=teacher).json()["prompts"]
    ids = {p["id"] for p in prompts}
    return [{"prompt_id": k, "answer": v} for k, v in keys.items() if k in ids]


@pytest.fixture(scope="module")
def switched_off(client, teacher):
    """Leave the shared teacher's style off for any test that runs after this module."""
    yield
    client.patch("/api/v1/teachers/me/style", headers=teacher, json={"status": "off"})


# ---------- pure functions ----------

def test_measures_and_fallback_rules():
    m = measure(["Imagine a queue at the bank. You join at the back, na? For example, a printer queue.",
                 "- push adds to the top\n- pop removes it\nWhat happens on an empty stack? It runs in O(1)."])
    assert m["examples_per_sample"] > 0 and m["questions_per_sample"] > 0 and m["big_o"]
    rules = rules_from_measures(m)
    assert 3 <= len(rules) <= 8 and any("Big-O" in r for r in rules)


def test_parse_rules_keeps_bullets_only():
    assert parse_rules("Here are the rules:\n- Uses analogies first.\n* Short sentences please.\nthanks") == \
        ["Uses analogies first.", "Short sentences please."]


def test_block_always_carries_the_guard():
    block = build_block(["Short sentences."], [{"text": "Think of a train."}])
    assert block.startswith(GUARD) and "<EXAMPLE>" in block


# ---------- calibration and review ----------

def test_calibration_is_for_teachers_and_uses_their_topics(client, teacher, student):
    assert client.get("/api/v1/teachers/me/calibration", headers=student).status_code == 403
    prompts = client.get("/api/v1/teachers/me/calibration", headers=teacher).json()["prompts"]
    text = " ".join(p["prompt"] for p in prompts)
    assert len(prompts) == 12 and "Linked lists" in text and "Quizzes" not in text


def test_too_few_answers_is_rejected(client, teacher, style_llm):
    r = client.post("/api/v1/teachers/me/calibration", headers=teacher,
                    json={"answers": _answers(client, teacher)[:4]})
    assert r.status_code == 422


def test_calibration_proposes_rules_and_exemplars_for_review(client, teacher, seeded, style_llm, switched_off):
    v = client.post("/api/v1/teachers/me/calibration", headers=teacher, json={"answers": _answers(client, teacher)}).json()
    assert v["profile"]["status"] == "draft"
    assert [r["text"] for r in v["profile"]["rules"]][0] == "Starts with a familiar everyday analogy before the formal idea."
    assert all(r["origin"] == "derived" and r["enabled"] for r in v["profile"]["rules"])
    cal = [e for e in v["exemplars"] if e["origin"] == "calibration"]
    mined = [e for e in v["exemplars"] if e["origin"] == "material"]
    assert len(cal) == 5 and all(e["status"] == "approved" for e in cal)
    assert mined and all(e["status"] == "pending" and e["source_ref"] for e in mined)
    assert not any("Quiz" in (e["source_ref"] or "") for e in mined)  # logistics are not style samples
    assert "EXAMPLES" in style_llm.calls[0][1][-1].content


def test_nothing_is_used_until_activated(client, student, seeded, chat_llm, switched_off):
    llm = chat_llm("A singly linked list is a chain of nodes [S1].")
    ask(client, student, seeded, "What is a singly linked list?")
    assert "STYLE" not in llm.calls[0][0]


def test_active_style_reaches_chat_and_notes_prompts(client, teacher, student, seeded, chat_llm, switched_off):
    v = client.patch("/api/v1/teachers/me/style", headers=teacher, json={"status": "active"}).json()
    assert v["profile"]["status"] == "active"
    llm = chat_llm("A singly linked list is a chain of nodes [S1].")
    res = ask(client, student, seeded, "What is a singly linked list?")
    system = llm.calls[0][0]
    assert res["outcome"] == "answered"
    assert system.index("Answer ONLY from the SOURCES") < system.index("STYLE") < system.index("MODE:")
    assert GUARD in system and "everyday analogy" in system
    assert "<EXAMPLE>" in system and "train" in system  # the linked-list calibration answer is the nearest


def test_style_never_loosens_grounding(client, teacher, student, seeded, chat_llm, switched_off):
    client.patch("/api/v1/teachers/me/style", headers=teacher, json={"status": "active"})
    llm = chat_llm("Should never be called [S1].")
    res = ask(client, student, seeded, "Who won the cricket world cup?")
    assert res["outcome"] == "refused" and llm.calls == []


def test_e4_arms_switch_the_block(client, teacher, seeded, switched_off):
    from pocketprof.db import session_scope
    from pocketprof.db.models import Course
    client.patch("/api/v1/teachers/me/style", headers=teacher, json={"status": "active"})
    with session_scope() as db:
        course = db.get(Course, seeded)
        q = "Explain linked lists"
        full = style_mod.style_block(db, course, load_arm("shipped-v1"), q)
        preamble = style_mod.style_block(db, course, load_arm("e4-preamble"), q)
        none = style_mod.style_block(db, course, load_arm("e4-no-style"), q)
    assert "<EXAMPLE>" in full and "everyday analogy" in preamble and "<EXAMPLE>" not in preamble and none == ""


def test_teacher_edits_rules_and_rejects_exemplars(client, teacher, seeded, switched_off):
    v = client.get("/api/v1/teachers/me/style", headers=teacher).json()
    rules = [{**r, "enabled": i != 0} for i, r in enumerate(v["profile"]["rules"])]
    rules.append({"text": "Always ends with one line the student can remember.", "enabled": True})
    v = client.patch("/api/v1/teachers/me/style", headers=teacher, json={"rules": rules, "status": "active"}).json()
    assert v["profile"]["rules"][-1]["origin"] == "teacher"
    p = client.post("/api/v1/teachers/me/style/preview", headers=teacher,
                    json={"course_id": seeded, "question": "Explain a linked list"}).json()
    assert "everyday analogy" not in p["style_block"] and "remember" in p["style_block"]
    train = next(e for e in v["exemplars"] if "train" in e["text"])
    assert train["id"] in p["exemplars"]
    r = client.patch(f"/api/v1/teachers/me/style/exemplars/{train['id']}", headers=teacher, json={"status": "rejected"})
    assert r.status_code == 200, r.text
    p = client.post("/api/v1/teachers/me/style/preview", headers=teacher,
                    json={"course_id": seeded, "question": "Explain a linked list"}).json()
    assert train["id"] not in p["exemplars"] and "train" not in p["style_block"]


def test_style_is_private_to_its_teacher(client, teacher, other_teacher, student, seeded, style_llm, switched_off):
    assert client.post("/api/v1/teachers/me/style/preview", headers=other_teacher,
                       json={"course_id": seeded, "question": "x y z"}).status_code == 403
    mine = client.get("/api/v1/teachers/me/style", headers=teacher).json()["exemplars"][0]["id"]
    assert client.patch(f"/api/v1/teachers/me/style/exemplars/{mine}", headers=other_teacher,
                        json={"status": "rejected"}).status_code == 404
    assert client.get("/api/v1/teachers/me/style", headers=student).status_code == 403


def test_without_a_model_rules_come_from_measurements(client, other_teacher, monkeypatch):
    monkeypatch.setattr(style_mod, "get_client", lambda gen: None)
    v = client.post("/api/v1/teachers/me/calibration", headers=other_teacher,
                    json={"answers": _answers(client, other_teacher)}).json()
    assert v["profile"]["rules"] and all(r["origin"] == "measured" for r in v["profile"]["rules"])
    assert v["profile"]["measures"]["samples"] == 5
