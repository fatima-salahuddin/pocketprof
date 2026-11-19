"""M3: notes generator (FR-8) and PDF export (FR-9). Pure functions first, then the API against the seeded
sample class with a fake model (M3_HANDOFF section 7)."""
from __future__ import annotations

import pymupdf
import pytest
from conftest import BrokenLLM, FakeLLM

from pocketprof.exports import documents, pdf
from pocketprof.generation import notes as notes_mod
from pocketprof.generation.notes import apply_budget, parse_markdown, renumber, select_chunks

WEEK5_NOTES = """# Linked lists

Here are your notes.

## What a linked list is
- A singly linked list is a chain of nodes [S1].
- Each node points to the next one [S1].

## Insertion
- Insert at the head in O(1) [S2][S99].

## Something uncited
- This sentence cites nothing.
"""


@pytest.fixture
def fake_notes_llm(monkeypatch):
    def install(answer: str) -> FakeLLM:
        llm = FakeLLM(answer)
        monkeypatch.setattr(notes_mod, "get_client", lambda gen: llm)
        return llm
    return install


# ---------- pure functions ----------

def test_parse_markdown_title_sections_and_preamble():
    title, sections = parse_markdown(WEEK5_NOTES)
    assert title == "Linked lists"
    assert [s["heading"] for s in sections] == ["What a linked list is", "Insertion", "Something uncited"]
    assert "Here are your notes" not in " ".join(s["body"] for s in sections)  # preamble dropped


def test_parse_markdown_without_sections_is_one_section():
    title, sections = parse_markdown("- A stack is LIFO [S1].")
    assert title is None and sections == [{"heading": "", "body": "- A stack is LIFO [S1]."}]


def test_renumber_follows_first_use_and_drops_uncited_and_invalid():
    sections = [{"heading": "A [S4]", "body": "x [S4] then y [S2]."}, {"heading": "B", "body": "z [S2], w [S9]"},
                {"heading": "C", "body": "no citation here"}]
    kept, order = renumber(sections, n_sources=5)
    assert order == [4, 2]
    assert [s["heading"] for s in kept] == ["A", "B"]
    assert kept[0]["body"] == "x [S1] then y [S2]." and kept[0]["citations"] == [1, 2]
    assert kept[1]["body"] == "z [S2], w" and kept[1]["citations"] == [2]


def test_budget_keeps_the_first_chunk_of_every_file():
    payloads = [{"chunk_id": f"c:{f}:{i}", "material_id": f, "text": "word " * 100} for f in (1, 2, 3) for i in range(3)]
    kept, truncated = apply_budget(payloads, max_words=350)
    assert truncated
    assert {p["material_id"] for p in kept} == {1, 2, 3}
    assert sum(len(p["text"].split()) for p in kept) <= 350 + 300


def test_select_chunks_returns_one_week_of_one_course_in_order(seeded):
    chunks = select_chunks(seeded, {"week": 5})
    assert chunks and {c["week"] for c in chunks} == {5} and {c["course_id"] for c in chunks} == {seeded}
    assert chunks == sorted(chunks, key=notes_mod.teaching_order)
    assert select_chunks(seeded, {"week": 12}) == []


# ---------- PDF ----------

def _text(data: bytes) -> tuple[str, int]:
    doc = pymupdf.open("pdf", data)
    return "".join(p.get_text() for p in doc), doc.page_count


def test_pdf_has_title_sources_and_escapes_markup():
    body = pdf.markdown_to_html("## Part\n- **Bold** fact [S1] <script>alert(1)</script>\n- second [S2]")
    data = pdf.render(pdf.Document("Week 5 notes", "CS-2001 · Week 5", body,
                                   [{"n": 1, "file": "L5.pptx", "location": "slides 2-3", "week": 5},
                                    {"n": 2, "file": "Notes.pdf", "location": "page 1", "week": None}]))
    assert data.startswith(b"%PDF")
    text, pages = _text(data)
    assert "Week 5 notes" in text and "[1] L5.pptx, slides 2-3 (week 5)" in text and "[2] Notes.pdf, page 1" in text
    assert "<script>alert(1)</script>" in text  # shown as text, never interpreted
    assert pages == 1


def test_long_pdf_spans_pages():
    body = pdf.markdown_to_html("\n\n".join(f"Paragraph {i} about linked lists and hashing [S1]." for i in range(400)))
    _, pages = _text(pdf.render(pdf.Document("Long", "sub", body, [{"n": 1, "file": "f", "location": "p1"}])))
    assert pages > 1


def test_thread_sources_are_merged_and_renumbered():
    book = documents.SourceBook()
    a = book.rewrite("A [S1] and B [S2].", [{"n": 1, "chunk_id": "x"}, {"n": 2, "chunk_id": "y"}])
    b = book.rewrite("Again B [S1].", [{"n": 1, "chunk_id": "y"}])
    assert a == "A [S1] and B [S2]." and b == "Again B [S2]." and len(book.sources) == 2


# ---------- API ----------

def test_options_list_weeks_topics_and_files(client, student, seeded):
    o = client.get(f"/api/v1/courses/{seeded}/notes/options", headers=student).json()
    assert o["weeks"] == [3, 4, 5, 6]
    assert "Hashing" in o["topics"] and "Linked lists" in o["topics"]
    assert any(f["title"] == "Lecture5_Linked_Lists.pptx" for f in o["files"])


def test_week_notes_are_cited_and_grounded_in_that_week(client, student, seeded, fake_notes_llm):
    llm = fake_notes_llm(WEEK5_NOTES)
    r = client.post(f"/api/v1/courses/{seeded}/notes", headers=student, json={"week": 5})
    assert r.status_code == 201, r.text
    n = r.json()
    assert n["outcome"] == "generated" and n["title"] == "Linked lists" and n["scope_label"] == "Week 5"
    assert [s["heading"] for s in n["sections"]] == ["What a linked list is", "Insertion"]  # uncited dropped
    assert all(s["citations"] for s in n["sections"])
    assert "[S99]" not in n["sections"][1]["body"]
    assert [s["n"] for s in n["sources"]] == [1, 2] and {s["week"] for s in n["sources"]} == {5}
    system, messages = llm.calls[0]
    assert "NOT_IN_MATERIAL" in system and "Week 5" in system and "<S1 " in messages[-1].content
    # saved, listed and reopened with citations intact
    assert any(x["id"] == n["id"] for x in client.get(f"/api/v1/notes?course_id={seeded}", headers=student).json())
    again = client.get(f"/api/v1/notes/{n['id']}", headers=student).json()
    assert again["sections"] == n["sections"] and again["sources"] == n["sources"]


def test_empty_selection_is_refused_without_calling_the_model(client, student, seeded, fake_notes_llm):
    llm = fake_notes_llm("never used [S1]")
    n = client.post(f"/api/v1/courses/{seeded}/notes", headers=student, json={"week": 12}).json()
    assert n["outcome"] == "refused" and "Week 12" in n["notice"] and llm.calls == []


def test_unrelated_free_text_topic_is_refused_without_calling_the_model(client, student, seeded, fake_notes_llm):
    llm = fake_notes_llm("never used [S1]")
    n = client.post(f"/api/v1/courses/{seeded}/notes", headers=student, json={"topic": "cricket world cup final"}).json()
    assert n["outcome"] == "refused" and llm.calls == []


def test_topic_tag_matches_case_insensitively(client, student, seeded, fake_notes_llm):
    fake_notes_llm("# Hashing\n## Collisions\n- Two keys can hash to the same index [S1].")
    n = client.post(f"/api/v1/courses/{seeded}/notes", headers=student, json={"topic": "hashing"}).json()
    assert n["outcome"] == "generated" and n["selection"] == {"topic": "Hashing"}
    assert {s["topic"] for s in n["sources"]} == {"Hashing"}


def test_one_file_notes(client, student, seeded, fake_notes_llm):
    o = client.get(f"/api/v1/courses/{seeded}/notes/options", headers=student).json()
    fid = next(f["id"] for f in o["files"] if f["title"] == "Week4_BST_Notes.pdf")
    fake_notes_llm("# BST\n## Deleting\n- Use the in-order successor [S1].")
    n = client.post(f"/api/v1/courses/{seeded}/notes", headers=student, json={"material_id": fid}).json()
    assert n["outcome"] == "generated" and {s["file"] for s in n["sources"]} == {"Week4_BST_Notes.pdf"}


def test_sentinel_becomes_a_refusal(client, student, seeded, fake_notes_llm):
    fake_notes_llm("NOT_IN_MATERIAL")
    assert client.post(f"/api/v1/courses/{seeded}/notes", headers=student,
                       json={"week": 3}).json()["outcome"] == "refused"


def test_uncited_notes_fail_closed(client, student, seeded, fake_notes_llm):
    fake_notes_llm("# Stacks\n## Idea\n- A stack is LIFO.")
    n = client.post(f"/api/v1/courses/{seeded}/notes", headers=student, json={"week": 3}).json()
    assert n["outcome"] == "fallback" and n["sections"] == [] and n["sources"]


def test_model_failure_lists_the_material(client, student, seeded, monkeypatch):
    monkeypatch.setattr(notes_mod, "get_client", lambda gen: BrokenLLM())
    n = client.post(f"/api/v1/courses/{seeded}/notes", headers=student, json={"week": 6}).json()
    assert n["outcome"] == "fallback" and "could not write notes" in n["notice"]
    assert {s["file"] for s in n["sources"]} >= {"Week6_Hashing.md"}


@pytest.mark.parametrize("body", [{}, {"week": 3, "topic": "Hashing"}, {"week": 99}])
def test_selection_is_validated(client, student, seeded, body):
    assert client.post(f"/api/v1/courses/{seeded}/notes", headers=student, json=body).status_code == 422


def test_teacher_can_make_notes_for_own_course(client, teacher, seeded, fake_notes_llm):
    fake_notes_llm("# Queues\n## FIFO\n- A queue is FIFO [S1].")
    assert client.post(f"/api/v1/courses/{seeded}/notes", headers=teacher, json={"week": 3}).status_code == 201


def test_notes_access_control(client, student, peer, outsider, other_teacher, seeded, fake_notes_llm):
    fake_notes_llm("# Q\n## A\n- A queue is FIFO [S1].")
    assert client.post(f"/api/v1/courses/{seeded}/notes", headers=outsider, json={"week": 3}).status_code == 404
    assert client.post(f"/api/v1/courses/{seeded}/notes", headers=other_teacher, json={"week": 3}).status_code == 403
    mine = client.post(f"/api/v1/courses/{seeded}/notes", headers=student, json={"week": 3}).json()
    for method in ("get", "delete"):
        assert getattr(client, method)(f"/api/v1/notes/{mine['id']}", headers=peer).status_code == 404
    assert client.post("/api/v1/exports", headers=peer, json={"kind": "notes", "ref": mine["id"]}).status_code == 404
    assert client.get(f"/api/v1/notes/{mine['id']}", headers=student).status_code == 200
    assert client.delete(f"/api/v1/notes/{mine['id']}", headers=student).status_code == 204
    assert client.get(f"/api/v1/notes/{mine['id']}", headers=student).status_code == 404


def test_export_notes_and_thread_as_pdf(client, student, peer, seeded, fake_notes_llm, monkeypatch):
    fake_notes_llm(WEEK5_NOTES)
    note = client.post(f"/api/v1/courses/{seeded}/notes", headers=student, json={"week": 5}).json()
    r = client.post("/api/v1/exports", headers=student, json={"kind": "notes", "ref": note["id"]})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("attachment;") and "notes.pdf" in r.headers["content-disposition"]
    text, _ = _text(r.content)
    assert "Linked lists" in text and "Lecture5_Linked_Lists.pptx" in text

    from pocketprof.generation import responder as responder_mod
    monkeypatch.setattr(responder_mod, "get_client", lambda gen: FakeLLM("Use the in-order successor [S1]."))
    chat = client.post(f"/api/v1/courses/{seeded}/chat", headers=student,
                       json={"message": "How do I delete a node with two children?"})
    thread_id = int(chat.text.split('"thread_id": ')[1].split(",")[0].split("}")[0])
    r = client.post("/api/v1/exports", headers=student, json={"kind": "thread", "ref": thread_id})
    assert r.status_code == 200
    text, _ = _text(r.content)
    assert "You" in text and "PocketProf" in text and "in-order successor" in text and "Week4_BST_Notes.pdf" in text
    assert client.post("/api/v1/exports", headers=peer, json={"kind": "thread", "ref": thread_id}).status_code == 404
