"""Practice item lifecycle (M4, FR-17; SDS 8.4, Figure 14; SRS UC-10).

    DRAFT --approve(edits?)--> VALIDATED --topic delivered--> RELEASED
    DRAFT --reject(reason)---> REJECTED
    any live state ----------> RETIRED   (source file replaced or withdrawn, or the teacher retires it)

Release and retirement are applied lazily, whenever a course's items are read: an approved item becomes
visible once its material's date has passed, and an item whose source file's hash no longer matches the
ledger is retired before any student can be drilled on content the teacher has replaced (UC-07 2a).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.errors import bad_request, conflict
from ..core.security import now_utc
from ..db.models import Course, Material, PracticeItem
from .generator import clean_item, is_delivered

LIVE = ("draft", "validated", "released")


def item_delivered(course: Course, item: PracticeItem) -> bool:
    return is_delivered(course, day=(item.source or {}).get("date"), week=item.week)


def retire(item: PracticeItem, reason: str) -> None:
    item.status, item.retired_at, item.retired_reason = "retired", now_utc(), reason[:200]


def retire_stale(db: Session, course: Course, items: list[PracticeItem] | None = None) -> int:
    """Retire every live item whose source material was withdrawn or replaced since generation."""
    if items is None:
        items = list(db.scalars(select(PracticeItem).where(PracticeItem.course_id == course.id,
                                                           PracticeItem.status.in_(LIVE))))
    mats = {m.id: m for m in db.scalars(select(Material).where(Material.course_id == course.id))}
    n = 0
    for it in items:
        if it.status not in LIVE:
            continue
        m = mats.get(it.material_id) if it.material_id else None
        if m is None or m.status != "indexed":
            retire(it, "The source material was withdrawn.")
            n += 1
        elif it.file_hash and m.file_hash != it.file_hash:
            retire(it, "The source file was replaced; this question may no longer match it.")
            n += 1
    return n


def release_due(db: Session, course: Course) -> int:
    n = 0
    for it in db.scalars(select(PracticeItem).where(PracticeItem.course_id == course.id,
                                                    PracticeItem.status == "validated")):
        if item_delivered(course, it):
            it.status, it.released_at = "released", now_utc()
            n += 1
    return n


def refresh(db: Session, course: Course) -> None:
    retire_stale(db, course)
    release_due(db, course)
    db.flush()


def apply_edits(item: PracticeItem, edits: dict[str, Any]) -> None:
    """A teacher's edits go through the same validation as generated items."""
    merged = {"kind": item.kind, "source": "S1", "question": item.question, "options": item.options,
              "answer": item.answer, "explanation": item.explanation}
    merged.update({k: v for k, v in edits.items() if v is not None})
    cleaned = clean_item(merged, 1, [merged["kind"]])
    if cleaned is None:
        raise bad_request("The edited question is not valid: check the question text, that a multiple-choice item "
                          "has four different options and an answer A-D, and that true/false answers are true or false.")
    changed = any(cleaned[k] != getattr(item, k) for k in ("kind", "question", "options", "answer", "explanation"))
    for k in ("kind", "question", "options", "answer", "explanation"):
        setattr(item, k, cleaned[k])
    item.edited = item.edited or changed


def approve(course: Course, item: PracticeItem, teacher_id: int, edits: dict[str, Any] | None = None) -> None:
    if item.status not in ("draft", "validated"):
        raise conflict(f"Only draft or approved questions can be approved; this one is {item.status}.")
    if edits:
        apply_edits(item, edits)
    item.status, item.validated_by, item.validated_at, item.reject_reason = "validated", teacher_id, now_utc(), None
    if item_delivered(course, item):
        item.status, item.released_at = "released", now_utc()


def reject(item: PracticeItem, reason: str | None) -> None:
    if item.status not in ("draft", "validated"):
        raise conflict(f"Only draft or approved questions can be rejected; this one is {item.status}.")
    if not reason or not reason.strip():
        raise bad_request("Say briefly why the question is rejected; the reason is kept for the acceptance metric.")
    item.status, item.reject_reason = "rejected", reason.strip()[:500]
