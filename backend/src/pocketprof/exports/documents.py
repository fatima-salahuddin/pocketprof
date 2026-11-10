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


Builder = Callable[..., Document]
KINDS: dict[str, Builder] = {"thread": thread_document, "notes": notes_document}
