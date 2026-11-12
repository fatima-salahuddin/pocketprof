"""M7: response modes (FR-14). The full answer is one click away after a scaffolded reply (SRS UC-02 6a),
and solution leakage in scaffolded mode is measured by the evaluation harness (SRS AI guardrail)."""
from __future__ import annotations

import pytest
from conftest import FakeLLM
from test_api import ask

from pocketprof.evaluation import metrics, queryset
from pocketprof.generation import responder as responder_mod


@pytest.fixture
def chat_llm(monkeypatch):
    def install(answer: str) -> FakeLLM:
        llm = FakeLLM(answer)
        monkeypatch.setattr(responder_mod, "get_client", lambda gen: llm)
        return llm
    return install


def test_scaffolded_mode_reaches_the_prompt(client, student, seeded, chat_llm):
    llm = chat_llm("Think about which key is smallest in the right subtree [S1]. What would you try first?")
    res = ask(client, student, seeded, "How do I delete a node with two children?", mode="scaffolded")
    assert res["outcome"] == "answered"
    assert "MODE: scaffolded" in llm.calls[0][0]


def test_reveal_answers_the_original_question_in_full(client, student, teacher, seeded, chat_llm):
    chat_llm("Hint: look at the right subtree first [S1].")
    first = ask(client, student, seeded, "How do I delete a node with two children from a BST?", mode="scaffolded")
    before = client.get(f"/api/v1/courses/{seeded}/questions?limit=200", headers=teacher).json()

    llm = chat_llm("Replace the key with its in-order successor, then delete the successor [S1].")
    res = ask(client, student, seeded, "Show me the full answer.", thread_id=first["thread_id"], reveal=True)
    assert res["outcome"] == "answered"
    system, messages = llm.calls[0]
    assert "complete answer" in system and "MODE: scaffolded" not in system
    assert "delete a node with two children" in messages[-1].content  # the original question, not the button text

    t = client.get(f"/api/v1/threads/{first['thread_id']}", headers=student).json()
    assert [m["mode"] for m in t["messages"]][-2:] == ["reveal", "direct"]
    # A reveal is not a new question: the teacher's view does not count it.
    after = client.get(f"/api/v1/courses/{seeded}/questions?limit=200", headers=teacher).json()
    assert len(after) == len(before)
    assert not any(q["question"] == "Show me the full answer." for q in after)


def test_reveal_needs_a_thread(client, student, seeded):
    r = client.post(f"/api/v1/courses/{seeded}/chat", headers=student,
                    json={"message": "Show me the full answer.", "reveal": True})
    assert r.status_code == 422


# ---------- leak metric (E6) ----------

def _r(i: int, mode: str, leaked: bool, outcome: str = "answered") -> metrics.QueryResult:
    return metrics.QueryResult(id=f"w{i}", type="worked", answerable=True, declined=outcome != "answered",
                               outcome=outcome, cited=1, retrieved=5, gold_retrieved=1, has_gold_labels=True,
                               cited_gold=1, scope_expected=None, scope_resolved=None, scope_weeks=[], top_score=0.8,
                               retrieval_ms=10, total_ms=100, mode=mode, has_solution_labels=True,
                               discloses_solution=leaked)


def test_leak_rate_counts_answered_scaffolded_worked_problems():
    m = metrics.compute([_r(1, "scaffolded", True), _r(2, "scaffolded", False), _r(3, "scaffolded", False, "refused")],
                        generated=True)
    assert m["n_worked_answered"] == 2 and m["solution_disclosure_rate"] == 0.5 and m["leak_rate"] == 0.5


def test_direct_mode_reports_disclosure_but_no_leak_rate():
    m = metrics.compute([_r(1, "direct", True), _r(2, "direct", True)], generated=True)
    assert m["solution_disclosure_rate"] == 1.0 and m["leak_rate"] is None


def test_modes_query_set_is_labelled():
    qs = queryset.load("modes-v1")
    assert len(qs.queries) == 8 and all(q.solution and q.gold and q.type == "worked" for q in qs.queries)
    w03 = next(q for q in qs.queries if q.id == "w03")
    assert w03.discloses_solution("So the traversal prints **10, 20, 30, 40, 60**.")
    assert not w03.discloses_solution("Which subtree does in-order visit first? Try writing the order yourself.")
