"""NotesGenerator (M3, FR-8; SDS Table 1, Table 5).

    selection (week | lecture | topic | one file) -> every chunk of that slice, in teaching order
        -> nothing?  -> refused, no model call
        -> word budget (first chunk of every file always kept) -> notes prompt -> model
        -> parse "# title" and "## sections" -> drop sections without a valid citation
        -> renumber the cited sources 1..k -> generated | refused | fallback

Notes are about a whole slice of the course, not one question, so the material is collected with a
filtered scroll (no ranking) rather than the top-k search chat uses. A free-text topic that is not one of
the class's topic tags falls back to retrieval, and refuses when retrieval abstains.

Grounded by construction: the same SOURCES format, refusal sentinel and citation helpers as chat. A section
that cites nothing is dropped, never shown, and a model failure lists the matching material instead of
notes (fail closed).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

from qdrant_client import models
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..arms import RunConfig, load_arm
from ..core.errors import bad_request, not_found
from ..db.models import Course, Material, Note, User
from ..retrieval.retriever import retrieve
from ..retrieval.scoper import Scope
from ..retrieval.vector_index import collection_name, get_index
from . import citations, prompts
from .llm import GenerationRefused, get_client

log = logging.getLogger("pocketprof.generation.notes")

SELECTION_KEYS = ("week", "lecture", "topic", "material_id")
_TAG = re.compile(r"\[S(\d+)\]")
_FIELD = {"week": "week", "lecture": "lecture_no", "topic": "topic", "material_id": "material_id"}


# ---------- selecting the material ----------

def _seq(chunk_id: str) -> int:
    tail = chunk_id.rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def teaching_order(p: dict) -> tuple:
    """Date, then file, then slide or page, then the chunk's sequence within its file."""
    return (p.get("date") or "", p.get("file") or "", p.get("slide") or p.get("page") or 0,
            _seq(p.get("chunk_id", "")))


def select_chunks(course_id: int, selection: dict[str, Any]) -> list[dict]:
    """Every chunk of one course matching the selection, in teaching order. The caller has already
    authorised the course; only that course's collection is read, and every payload is re-checked."""
    must = [models.FieldCondition(key="course_id", match=models.MatchValue(value=course_id))]
    for sel_key, field in _FIELD.items():
        if sel_key in selection:
            must.append(models.FieldCondition(key=field, match=models.MatchValue(value=selection[sel_key])))
            break
    payloads = get_index().scroll_payloads(collection_name(course_id), models.Filter(must=must))
    payloads = [p for p in payloads if p.get("course_id") == course_id]  # defence in depth
    return sorted(payloads, key=teaching_order)


def apply_budget(payloads: list[dict], max_words: int) -> tuple[list[dict], bool]:
    """Keep chunks in teaching order up to `max_words`, but always keep the first chunk of every file so
    each file in the slice is represented. Returns (kept, truncated)."""
    first_of_file: set[str] = set()
    seen_files: set = set()
    for p in payloads:
        f = p.get("material_id") or p.get("file")
        if f not in seen_files:
            seen_files.add(f)
            first_of_file.add(p["chunk_id"])
    mandatory = sum(len(p.get("text", "").split()) for p in payloads if p["chunk_id"] in first_of_file)
    room = max_words - mandatory
    kept = []
    for p in payloads:
        words = len(p.get("text", "").split())
        if p["chunk_id"] in first_of_file:
            kept.append(p)
        elif words <= room:
            kept.append(p)
            room -= words
    return kept, len(kept) < len(payloads)


# ---------- parsing and citations (pure functions) ----------

def parse_markdown(text: str) -> tuple[str | None, list[dict]]:
    """'# title' then '## heading' sections. Text before the first section is dropped (it is usually an
    uncited preamble); a reply with no '##' at all becomes a single untitled section."""
    title: str | None = None
    sections: list[dict] = []
    current: dict | None = None
    loose: list[str] = []
    for line in text.replace("\r", "").split("\n"):
        h1 = re.match(r"^#\s+(.+)", line)
        h2 = re.match(r"^#{2,3}\s+(.+)", line)
        if h1 and title is None and current is None:
            title = h1.group(1).strip()
        elif h2:
            current = {"heading": h2.group(1).strip(), "body": []}
            sections.append(current)
        elif current is not None:
            current["body"].append(line)
        else:
            loose.append(line)
    out = [{"heading": s["heading"], "body": "\n".join(s["body"]).strip()} for s in sections]
    if not out and "\n".join(loose).strip():
        out = [{"heading": "", "body": "\n".join(loose).strip()}]
    return title, out


def renumber(sections: list[dict], n_sources: int) -> tuple[list[dict], list[int]]:
    """Drop invalid tags and uncited sections, then renumber the sources actually cited to 1..k in order
    of first use. Returns (sections with a `citations` list, old source numbers in their new order)."""
    kept = []
    for s in sections:
        heading = _TAG.sub("", s["heading"]).strip()
        body = citations.strip_invalid_tags(citations.normalise_tags(s["body"]), n_sources).strip()
        if not citations.cited_numbers(body, n_sources):
            continue  # FR-8: every section shown carries a citation; an uncited one is dropped
        kept.append({"heading": heading, "body": body})
    order: list[int] = []
    for s in kept:
        for n in citations.cited_numbers(s["body"], n_sources):
            if n not in order:
                order.append(n)
    new = {old: i for i, old in enumerate(order, start=1)}
    for s in kept:
        s["body"] = _TAG.sub(lambda m: f"[S{new[int(m.group(1))]}]", s["body"])
        s["citations"] = citations.cited_numbers(s["body"], len(order))
    return kept, order


def is_sentinel(text: str) -> bool:
    t = text.strip().lstrip("*_`\"' ").upper()
    return t.startswith(prompts.REFUSAL_SENTINEL) or (prompts.REFUSAL_SENTINEL in text and len(text.strip()) < 80)


def source_record(n: int, p: dict) -> dict:
    return {"n": n, "chunk_id": p.get("chunk_id"), "kind": p.get("kind"), "file": p.get("file"),
            "location": p.get("location"), "week": p.get("week"), "date": p.get("date"),
            "post_title": p.get("post_title"), "topic": p.get("topic"),
            "url": p.get("url") or p.get("post_url"), "snippet": (p.get("text") or "")[:600]}


def material_list(payloads: list[dict]) -> list[dict]:
    """Fallback content: one entry per file with the locations that matched."""
    by_file: dict[Any, dict] = {}
    locations: dict[Any, list[str]] = {}
    for p in payloads:
        key = p.get("material_id") or p.get("file")
        if key not in by_file:
            by_file[key] = source_record(len(by_file) + 1, p)
            locations[key] = []
        loc = p.get("location")
        if loc and loc not in locations[key]:
            locations[key].append(loc)
    for key, rec in by_file.items():
        locs = locations[key]
        if locs:
            rec["location"] = ", ".join(locs[:6]) + (" ..." if len(locs) > 6 else "")
    return list(by_file.values())


# ---------- selection (shared with practice generation, M4) ----------

def resolve_selection(db: Session, course: Course, selection: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Exactly one of week, lecture, topic or material_id -> (canonical selection, label). A topic that
    matches a topic tag (any case) becomes that tag; any other text becomes a free-text topic."""
    given = {k: v for k, v in selection.items() if k in SELECTION_KEYS and v not in (None, "")}
    if len(given) != 1:
        raise bad_request("Choose exactly one of: week, lecture, topic or file.")
    key, value = next(iter(given.items()))
    if key == "week":
        return {"week": int(value)}, f"Week {int(value)}"
    if key == "lecture":
        return {"lecture": int(value)}, f"Lecture {int(value)}"
    if key == "material_id":
        m = db.get(Material, int(value))
        if m is None or m.course_id != course.id or m.status != "indexed":
            raise not_found("File")
        return {"material_id": m.id}, m.title
    text = str(value).strip()
    canonical = db.scalar(select(Material.topic).where(
        Material.course_id == course.id, Material.status == "indexed",
        func.lower(Material.topic) == text.lower()).limit(1))
    if canonical:
        return {"topic": canonical}, canonical
    return {"topic_text": text}, f'"{text}"'


def collect_chunks(course: Course, arm: RunConfig, selection: dict[str, Any]) -> list[dict]:
    """The chunks behind a resolved selection, in teaching order. A free-text topic uses retrieval (with a
    larger top-k) and returns nothing when retrieval abstains."""
    if "topic_text" in selection:
        n = arm.notes.topic_top_k
        wide = arm.model_copy(update={"retrieval": arm.retrieval.model_copy(update={"top_k": n})})
        result = retrieve(course.id, selection["topic_text"], wide, Scope())
        if result.abstain or not result.hits:
            return []
        return sorted((h.payload for h in result.hits), key=teaching_order)
    return select_chunks(course.id, selection)


# ---------- the generator ----------

class NotesGenerator:
    def __init__(self, db: Session, course: Course, user: User, *, arm: RunConfig | None = None):
        self.db, self.course, self.user = db, course, user
        self.arm = arm or load_arm(course.active_arm)

    def validate(self, selection: dict[str, Any]) -> tuple[dict[str, Any], str]:
        return resolve_selection(self.db, self.course, selection)

    def _collect(self, selection: dict[str, Any]) -> list[dict]:
        return collect_chunks(self.course, self.arm, selection)

    def _style(self, query: str) -> str:
        from ..style.service import style_block  # M5: empty unless the teacher has an active style profile
        return style_block(self.db, self.course, self.arm, query)

    def generate(self, selection: dict[str, Any], *, stop: threading.Event | None = None) -> Note:
        t0 = time.perf_counter()
        selection, label = self.validate(selection)
        payloads = self._collect(selection)
        if not payloads:
            what = "about " + label if "topic_text" in selection else "for " + label
            return self._save(selection, label, outcome="refused", t0=t0,
                              notice=f"Nothing in the class material was posted {what}. If you meant a different "
                                     "week, topic or file, pick it from the list, or check with your teacher.")
        payloads, truncated = apply_budget(payloads, self.arm.notes.word_budget)

        client = get_client(self.arm.generation)
        if client is None:
            return self._fallback(selection, label, payloads, truncated, t0, "No language model is configured, "
                                  "so here is the class material for this selection instead of notes.")
        ctx = prompts.CourseContext(self.course.code, self.course.title, self.course.teacher.name)
        system, messages = prompts.notes_prompt(ctx, label, payloads, style_block=self._style(label))
        max_tokens = max(self.arm.notes.max_tokens, self.arm.generation.max_tokens)
        try:
            text = "".join(client.stream(system, messages, max_tokens=max_tokens, stop=stop or threading.Event()))
        except GenerationRefused:
            return self._fallback(selection, label, payloads, truncated, t0, "The language model declined this "
                                  "request, so here is the class material for this selection instead.")
        except Exception as e:
            log.warning("notes generation failed", extra={"course_id": self.course.id, "error": type(e).__name__})
            reason = "it is busy right now" if "RateLimit" in type(e).__name__ else "it could not be reached"
            return self._fallback(selection, label, payloads, truncated, t0, f"The language model could not write "
                                  f"notes ({reason}), so here is the class material for this selection instead.")

        text = citations.normalise_tags(text).strip()
        if not text:
            return self._fallback(selection, label, payloads, truncated, t0, "The language model returned nothing, "
                                  "so here is the class material for this selection instead.")
        if is_sentinel(text):
            return self._save(selection, label, outcome="refused", t0=t0, truncated=truncated,
                              notice=f"The class material for {label} has nothing PocketProf could turn into notes.")
        title, sections = parse_markdown(text)
        sections, order = renumber(sections, len(payloads))
        if len(sections) < max(1, self.arm.notes.min_sections):
            log.info("uncited notes discarded", extra={"course_id": self.course.id})
            return self._fallback(selection, label, payloads, truncated, t0, "The notes could not be tied to the "
                                  "class material, so they are not shown. Here is the material for this selection.")
        sources = [source_record(i, payloads[old - 1]) for i, old in enumerate(order, start=1)]
        title = _TAG.sub("", title or "").strip() or f"{label} notes"
        return self._save(selection, label, outcome="generated", t0=t0, title=title, sections=sections,
                          sources=sources, truncated=truncated, model=client.name)

    def _fallback(self, selection, label, payloads, truncated, t0, notice) -> Note:
        return self._save(selection, label, outcome="fallback", t0=t0, sources=material_list(payloads),
                          truncated=truncated, notice=notice)

    def _save(self, selection: dict, label: str, *, outcome: str, t0: float, title: str | None = None,
              sections: list | None = None, sources: list | None = None, truncated: bool = False,
              notice: str | None = None, model: str | None = None) -> Note:
        note = Note(user_id=self.user.id, course_id=self.course.id, selection=selection, scope_label=label[:200],
                    title=(title or f"{label} notes")[:200], sections=sections or [], sources=sources or [],
                    outcome=outcome, notice=notice, truncated=truncated, model=model,
                    config_hash=self.arm.config_hash, prompt_version=prompts.NOTES_PROMPT_VERSION)
        self.db.add(note)
        self.db.flush()
        log.info("notes", extra={"course_id": self.course.id, "note_id": note.id, "outcome": outcome,
                                 "ms": int((time.perf_counter() - t0) * 1000)})
        return note
