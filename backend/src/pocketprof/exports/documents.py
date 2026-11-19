"""Export builders (M3, FR-9): each turns one stored object into a pdf.Document.

    thread  a conversation: one block per message, every assistant message's citations merged into one
            numbered Sources list for the whole document (markers rewritten to match)
    notes   a saved set of notes: title, scope line, sections, Sources
    paper   a practice paper (M4): released items, then an answer key with sources

A new kind is one more builder registered in KINDS; the route does not change.
"""
from __future__ import annotations

import html
import re
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Conversation, Course, Note
from .pdf import Document, markdown_to_html

_TAG = re.compile(r"\[S(\d+)\]")


def _date(d: datetime | None) -> str:
    return d.strftime("%d %b %Y").lstrip("0") if d else ""


def slug(*parts: str) -> str:
    text = "-".join(p for p in parts if p)
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")[:80] or "pocketprof"


class SourceBook:
    """One numbered Sources list shared by several blocks of text. Each block's local [Sk] markers are
    rewritten to the global number of the chunk they cite, so the same chunk always has the same number."""

    def __init__(self) -> None:
        self.sources: list[dict] = []
        self._by_key: dict[str, int] = {}

    def add(self, record: dict) -> int:
        key = record.get("chunk_id") or f"{record.get('file')}|{record.get('location')}"
        if key not in self._by_key:
            n = len(self.sources) + 1
            self._by_key[key] = n
            self.sources.append({**record, "n": n})
        return self._by_key[key]

    def rewrite(self, text: str, local: list[dict]) -> str:
        mapping = {int(c["n"]): self.add(c) for c in local}
        return _TAG.sub(lambda m: f"[S{mapping[int(m.group(1))]}]" if int(m.group(1)) in mapping else "", text)


def thread_document(db: Session, conv: Conversation) -> Document:
    course = db.get(Course, conv.course_id)
    book = SourceBook()
    blocks = []
    for m in conv.messages:
        if m.role == "user":
            blocks.append(f"<p class='who'>You</p><p>{html.escape(m.content)}</p>")
            continue
        blocks.append("<p class='who'>PocketProf</p>")
        if m.outcome == "answered":
            blocks.append(markdown_to_html(book.rewrite(m.content, m.citations or [])))
        elif m.outcome == "refused":
            blocks.append(f"<p class='notice'>Not in the class material. {html.escape(m.content)}</p>")
        elif m.outcome == "fallback":  # the passages shown on screen are not exported; the notice is
            blocks.append(f"<p class='notice'>{html.escape(m.content)}</p>")
        else:
            blocks.append(markdown_to_html(_TAG.sub("", m.content)))
    code = course.code if course else ""
    return Document(title=conv.title, subtitle=f"{code} · conversation · exported {_date(datetime.now())}",
                    body_html="".join(blocks), sources=book.sources, filename=f"{slug(code, 'conversation')}.pdf")


def notes_document(db: Session, note: Note) -> Document:
    course = db.get(Course, note.course_id)
    code = course.code if course else ""
    files = len({s.get("file") for s in note.sources})
    sub = f"{code} · {note.scope_label} · generated {_date(note.created_at)}"
    if note.outcome == "generated":
        sub += f" from {files} file{'s' if files != 1 else ''}"
        body = "".join((f"<h2>{html.escape(s['heading'])}</h2>" if s.get("heading") else "")
                       + markdown_to_html(s["body"]) for s in note.sections)
        if note.truncated:
            body += ("<p class='notice'>This selection holds more material than one set of notes can cover; "
                     "every file is represented, but some later passages were left out.</p>")
        sources = note.sources
    else:
        body = f"<p class='notice'>{html.escape(note.notice or '')}</p>"
        if note.outcome == "fallback" and note.sources:
            body += "<h2>Material for this selection</h2><ul>" + "".join(
                f"<li>{html.escape(s.get('file') or '')}{', ' + html.escape(s['location']) if s.get('location') else ''}"
                f"</li>" for s in note.sources) + "</ul>"
        sources = []
    return Document(title=note.title, subtitle=sub, body_html=body, sources=sources,
                    filename=f"{slug(code, note.scope_label, 'notes')}.pdf")


def paper_document(db: Session, course: Course, principal, *, topic: str | None = None) -> Document:
    """A practice paper (FR-9, FR-10): released items for students; staff also get approved items not yet
    released. Questions first, then an answer key whose [n] markers point at the Sources list."""
    from ..core.errors import bad_request
    from ..db.models import PracticeItem
    from ..practice.lifecycle import refresh
    refresh(db, course)
    states = ("released",) if principal.role == "student" else ("validated", "released")
    q = select(PracticeItem).where(PracticeItem.course_id == course.id, PracticeItem.status.in_(states))
    if topic:
        q = q.where(PracticeItem.topic == topic)
    items = list(db.scalars(q.order_by(PracticeItem.week, PracticeItem.topic, PracticeItem.id)))
    if not items:
        raise bad_request("There are no approved practice questions" + (f" for {topic}" if topic else "") + " yet.")
    book = SourceBook()
    questions, key = [], []
    for i, it in enumerate(items, start=1):
        n = book.add(it.source or {})
        block = f"<p><b>{i}.</b> {html.escape(it.question)}</p>"
        if it.kind == "mcq" and it.options:
            block += "<ol type='A'>" + "".join(f"<li>{html.escape(o)}</li>" for o in it.options) + "</ol>"
        elif it.kind == "tf":
            block += "<p class='sub'>True or false?</p>"
        else:
            block += "<p class='sub'>Answer in one to three sentences.</p><p>&nbsp;</p>"
        questions.append(block)
        answer = (f"{it.answer}. {html.escape(it.options['ABCD'.index(it.answer)])}" if it.kind == "mcq" and it.options
                  else html.escape(it.answer.capitalize() if it.kind == "tf" else it.answer))
        why = f" {html.escape(it.explanation)}" if it.explanation else ""
        key.append(f"<li><b>{answer}</b>{why} <sup>[{n}]</sup></li>")
    body = ("<h2>Questions</h2>" + "".join(questions) + "<h2>Answer key</h2><ol>" + "".join(key) + "</ol>")
    scope = topic or "all delivered topics"
    return Document(title=f"{course.code} practice paper", subtitle=f"{course.title} · {scope} · {len(items)} questions",
                    body_html=body, sources=book.sources, filename=f"{slug(course.code, topic or '', 'practice-paper')}.pdf")


Builder = Callable[..., Document]
KINDS: dict[str, Builder] = {"thread": thread_document, "notes": notes_document, "paper": paper_document}
