"""Unit tests for the deterministic components (SDS Table 11): parsers, chunker, scope resolver, citation
mapping, calendar, security primitives."""
from __future__ import annotations

from datetime import date

import pytest

from pocketprof import sample_course
from pocketprof.arms import Chunking, load_arm
from pocketprof.core import security
from pocketprof.generation import citations
from pocketprof.ingestion import parsers
from pocketprof.ingestion.calendar import CourseCalendar
from pocketprof.ingestion.chunker import chunk_segments, n_tokens
from pocketprof.ingestion.parsers import Segment
from pocketprof.retrieval import scoper

TODAY = date(2026, 10, 21)  # a Wednesday
CAL = CourseCalendar(term_start=date(2026, 9, 14), days={})  # week 1 = 14-20 Sep; 21 Oct is week 6


# ----- parsers -----

def test_pptx_slides_keep_numbers_titles_and_drop_empty():
    data = sample_course.make_pptx([("Stacks", "LIFO order"), ("", ""), ("Queues", "FIFO order")])
    segs = parsers.parse(data, parsers.PPTX).segments
    assert [s.slide for s in segs] == [1, 3]
    assert segs[0].heading == "Stacks" and "LIFO" in segs[0].text


def test_pdf_pages_and_repeated_footer_removed():
    data = sample_course.make_pdf([f"Page {i} body text about trees and heaps." for i in range(1, 5)])
    segs = parsers.parse(data, parsers.PDF).segments
    assert [s.page for s in segs] == [1, 2, 3, 4]
    assert all("CS-2001 Data Structures" not in s.text for s in segs)  # running footer stripped


def test_docx_sections_follow_headings_and_tables_stay_in_section():
    data = sample_course.make_docx([("Task", "Write a checker."), ("Marking", "Total 10 marks.")], "Brief")
    segs = parsers.parse(data, parsers.DOCX).segments
    headings = [s.heading for s in segs]
    assert "Task" in headings and "Marking" in headings


@pytest.mark.parametrize("name,data,ok", [
    ("notes.pdf", b"%PDF-1.7 ...", True),
    ("notes.pdf", b"PK\x03\x04 not a pdf", False),
    ("slides.pptx", b"%PDF-1.7", False),
    ("macro.docx", b"PK\x03\x04....vbaProject.bin....", False),
    ("tool.exe", b"MZ", False),
    ("readme.txt", b"hello", True),
])
def test_upload_content_signature(name, data, ok):
    if ok:
        assert parsers.sniff_mime(name, data)
    else:
        with pytest.raises(parsers.UnsupportedType):
            parsers.sniff_mime(name, data)


# ----- chunker -----

def test_slides_merge_until_minimum_and_never_exceed_size():
    cfg = Chunking(size=60, overlap=0.15, min=20)
    segs = [Segment(f"slide {i}", f"Slide {i} talks about topic number {i} briefly.", slide=i) for i in range(1, 9)]
    chunks = chunk_segments(segs, cfg)
    assert all(n_tokens(c.text) <= cfg.size for c in chunks)
    assert len(chunks) < len(segs)                       # small slides were merged
    assert chunks[0].location.startswith("slides 1-")


def test_prose_overlap_carries_text_across_boundary():
    sentences = " ".join(f"Sentence number {i} explains a detail of hashing." for i in range(60))
    chunks = chunk_segments([Segment("text", sentences)], Chunking(size=80, overlap=0.2, min=0))
    assert len(chunks) > 2
    last_unit = chunks[0].text.split("\n\n")[-1]
    assert chunks[1].text.startswith(last_unit) or last_unit in chunks[1].text  # carried across the boundary


def test_chunking_is_deterministic():
    segs = [Segment("page 1", "Alpha beta gamma. " * 200, page=1)]
    cfg = load_arm("shipped-v1").chunking
    assert [c.text for c in chunk_segments(segs, cfg)] == [c.text for c in chunk_segments(segs, cfg)]


def test_heading_starts_new_chunk():
    segs = [Segment("introduction", "Short intro."), Segment("section: Heaps", "Heaps are trees.", heading="Heaps")]
    chunks = chunk_segments(segs, Chunking(size=384, overlap=0.15, min=128))
    assert len(chunks) == 2 and chunks[1].heading == "Heaps"


def test_fixed_splitter_baseline():
    segs = [Segment("text", "word " * 1000)]
    chunks = chunk_segments(segs, Chunking(size=100, overlap=0.0, min=0, splitter="fixed"))
    assert all(n_tokens(c.text) <= 100 for c in chunks) and len(chunks) == 10


# ----- scope resolution (FR-7) -----

@pytest.mark.parametrize("q,kind,expect", [
    ("what did we cover in week 5", "week", 5),
    ("explain wk 3 slides", "week", 3),
    ("what was covered last week", "week", 5),
    ("summarise this week", "week", 6),
    ("what did we cover on Tuesday?", "date", date(2026, 10, 20)),
    ("notes from 22 Sep please", "date", date(2026, 9, 22)),
    ("what was taught on Sep 22nd", "date", date(2026, 9, 22)),
    ("lecture on 22/09", "date", date(2026, 9, 22)),
    ("what did we do yesterday", "date", date(2026, 10, 20)),
])
def test_scope_resolution(q, kind, expect):
    s = scoper.resolve(q, CAL, today=TODAY)
    assert s.kind == kind
    assert (s.week if kind == "week" else s.day) == expect


@pytest.mark.parametrize("q", [
    "when is the mid on 2 November",        # future date: an event, not a class
    "is the quiz on Monday?",               # weekday without a past-teaching cue
    "I got 3/10 marks in quiz 2",           # a score, not a date
    "how do heaps work",
])
def test_no_scope(q):
    assert not scoper.resolve(q, CAL, today=TODAY).active


def test_date_scope_widens_to_its_week():
    s = scoper.resolve("what was taught on Tuesday", CAL, today=TODAY)
    wider = s.widen_to_week()
    assert wider.kind == "week" and wider.week == 6 and wider.notes


# ----- citations (FR-5) -----

def test_tag_normalisation_and_validation():
    text = citations.normalise_tags("A [S1, S3]. B [s 2]. C [S9].")
    assert text == "A [S1][S3]. B [S2]. C [S9]."
    assert citations.cited_numbers(text, 3) == [1, 3, 2]
    assert "[S9]" not in citations.strip_invalid_tags(text, 3)


@pytest.mark.parametrize("text,refusal", [
    ("NOT_IN_MATERIAL", True),
    ("**NOT_IN_MATERIAL**", True),
    ("The provided course material contains no information about cricket [S1].", True),
    ("The excerpts do **not** state the date of Mid 2.", True),
    ("Mid 2 is on 2 November [S1]. The duration is not covered in the material.", False),
    ("A stack is LIFO [S1].", False),
])
def test_refusal_detection(text, refusal):
    assert citations.is_refusal(text) is refusal


# ----- calendar -----

def test_week_of_uses_calendar_then_term_start():
    assert CAL.week_of(date(2026, 9, 14)) == 1
    assert CAL.week_of(date(2026, 9, 20)) == 1
    assert CAL.week_of(date(2026, 10, 21)) == 6
    assert CAL.week_of(date(2026, 9, 1)) is None


# ----- security -----

def test_password_hash_and_tokens():
    h = security.hash_password("correct horse")
    assert security.verify_password("correct horse", h) and not security.verify_password("wrong", h)
    token, ttl = security.create_access_token(7, "student")
    claims = security.decode_access_token(token)
    assert claims["sub"] == "7" and claims["role"] == "student" and ttl == 1800


def test_pseudonym_is_per_course_and_stable():
    a1, a2 = security.student_ref(1, 10), security.student_ref(1, 11)
    assert a1 != a2 and a1 == security.student_ref(1, 10) and len(a1) == 16


def test_secret_encryption_roundtrip():
    enc = security.encrypt_secret('{"refresh_token": "x"}')
    assert "refresh_token" not in enc and security.decrypt_secret(enc) == '{"refresh_token": "x"}'
