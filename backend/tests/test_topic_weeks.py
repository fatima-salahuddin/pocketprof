"""Weeks and lectures named in topic labels ("Week 5 - Normalisation") override the post date, so a class whose
material was all posted at once can still be asked about week 5. A week typed by the teacher on upload wins."""
from __future__ import annotations

import pytest
from conftest import FakeLLM
from test_api import ask, wait_idle

from pocketprof.generation import responder as responder_mod
from pocketprof.ingestion.calendar import lecture_from_label, week_from_label


@pytest.mark.parametrize("label,week", [
    ("Week 5 - Normalisation", 5), ("week-05", 5), ("Wk 5: SQL", 5), ("WEEK#5", 5), ("Week 5 & 6 revision", 5),
    ("Week 12", 12), ("Normalisation (week 5)", 5),
    ("Weekly quizzes", None), ("5 weeks of SQL", None), ("Week 0", None), ("Week 45", None), ("Midterm week", None),
    ("Normalisation", None), ("", None), (None, None),
])
def test_week_from_label(label, week):
    assert week_from_label(label) == week


@pytest.mark.parametrize("label,lecture", [("Lecture 9 - Heaps", 9), ("Lec-3", 3), ("lecture 012", 12),
                                           ("Lectures and labs", None), ("Week 5", None)])
def test_lecture_from_label(label, lecture):
    assert lecture_from_label(label) == lecture


@pytest.fixture(scope="module")
def labelled_course(client, other_teacher):
    """A course whose three files were all uploaded today: one under a 'Week 5' topic, one under a 'Week 5'
    topic with a week typed by the teacher, and one under a plain topic."""
    from pocketprof.ingestion.provisioning import ProvisioningJob
    c = client.post("/api/v1/courses", headers=other_teacher, json={"code": "CS-2005", "title": "Database Systems"}).json()
    uploads = [
        ("Normalisation.txt", b"Normalisation. Second normal form removes partial dependencies on part of a candidate "
                              b"key. Third normal form removes transitive dependencies.", "Week 5 - Normalisation", None),
        ("SQL.txt", b"SQL basics. WHERE filters rows before grouping and HAVING filters groups after aggregation.",
         "Week 5 - SQL", "2"),
        ("Intro.txt", b"Introduction. A database management system stores, retrieves and protects related data.",
         "Introduction", None),
    ]
    for name, data, topic, week in uploads:
        form = {"topic": topic} | ({"week": week} if week else {})
        r = client.post(f"/api/v1/courses/{c['id']}/materials", headers=other_teacher,
                        files={"files": (name, data, "text/plain")}, data=form)
        assert r.status_code == 201, r.text
        wait_idle(c["id"])
    ProvisioningJob(c["id"], "manual").run()
    mats = {m["title"]: m for m in client.get(f"/api/v1/courses/{c['id']}/materials", headers=other_teacher).json()}
    return c, mats


def test_topic_week_overrides_the_post_date(labelled_course):
    _, mats = labelled_course
    assert mats["Normalisation.txt"]["week"] == 5   # from the topic name, although posted today
    assert mats["SQL.txt"]["week"] == 2             # the teacher's own week wins over the topic name
    assert mats["Intro.txt"]["week"] == 1           # no label: the post date, in the first week of the course


def test_topic_week_reaches_the_index_and_scoped_questions(client, other_teacher, labelled_course, monkeypatch):
    from pocketprof.generation.notes import select_chunks
    c, _ = labelled_course
    week5 = select_chunks(c["id"], {"week": 5})
    assert {p["file"] for p in week5} == {"Normalisation.txt"}
    llm = FakeLLM("Week 5 covered second and third normal form [S1].")
    monkeypatch.setattr(responder_mod, "get_client", lambda gen: llm)
    res = ask(client, other_teacher, c["id"], "What did we cover in week 5?")
    meta = next(d for e, d in res["events"] if e == "meta")
    assert res["outcome"] == "answered" and meta["scope"]["week"] == 5
    assert {s["file"] for s in meta["sources"]} == {"Normalisation.txt"}
