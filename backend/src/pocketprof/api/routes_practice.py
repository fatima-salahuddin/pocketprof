"""Practice routes (M4: FR-10, FR-17, FR-18; SDS Table 5; SRS UC-07, UC-10).

Teacher (course owner):  POST /courses/{id}/practice/generate     -> DRAFT items
                         GET  /courses/{id}/validation             -> the queue, with counts (UI-8)
                         GET  /courses/{id}/practice/items         -> every item, with cohort stats
                         PATCH /practice/{item}                    -> approve (with edits) | reject | retire
Student (enrolled):      GET  /courses/{id}/practice/due           -> the session, mastery
                         POST /practice/{item}/attempts            -> marked, rescheduled, mastery updated
                         POST /practice/{item}/reveal              -> a short answer's model answer

Students only ever see RELEASED items, and never the answer before they attempt. Teachers see per-item
accuracy only once enough distinct students have attempted it (minimum cohort, C-8).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..access.audit import record
from ..access.guard import Principal, course_for, require_role
from ..arms import load_arm
from ..config import get_settings
from ..core.errors import bad_request, not_found
from ..db import get_db
from ..db.models import ITEM_STATES, Course, PracticeAttempt, PracticeItem, User
from ..practice.generator import PracticeGenerator
from ..practice.lifecycle import approve, refresh, reject, retire
from ..practice.tracker import MasteryTracker
from .middleware import RateLimiter, too_many
from .schemas import AttemptIn, PracticeGenerateIn, PracticeItemOut, PracticeReviewIn

router = APIRouter(tags=["practice"])
staff = require_role("teacher", "admin")
student_only = require_role("student")
_limiter = RateLimiter(get_settings().rate_limit_per_minute)


def _counts(db: Session, course_id: int) -> dict[str, int]:
    rows = db.execute(select(PracticeItem.status, func.count()).where(PracticeItem.course_id == course_id)
                      .group_by(PracticeItem.status)).all()
    out = dict.fromkeys(ITEM_STATES, 0)
    out.update({s: n for s, n in rows})
    return out


def _stats(db: Session, course: Course, item_ids: list[int]) -> dict[int, dict]:
    """Per-item attempts from distinct students; suppressed below the minimum cohort (C-8, SDS 4.6)."""
    if not item_ids:
        return {}
    k = load_arm(course.active_arm).analytics.min_cohort
    students = func.count(func.distinct(PracticeAttempt.student_ref))
    rows = db.execute(select(PracticeAttempt.item_id, students, func.count(),
                             func.sum(case((PracticeAttempt.correct, 1), else_=0)))
                      .where(PracticeAttempt.item_id.in_(item_ids)).group_by(PracticeAttempt.item_id)).all()
    out = {}
    for item_id, n_students, n, right in rows:
        if n_students < k:
            out[item_id] = {"suppressed": True, "min_cohort": k}
        else:
            out[item_id] = {"suppressed": False, "students": n_students, "attempts": n,
                            "accuracy": round((right or 0) / n, 3) if n else None}
    return out


def _out(item: PracticeItem, stats: dict | None = None) -> PracticeItemOut:
    o = PracticeItemOut.model_validate(item)
    o.stats = stats
    return o


# ---------- teacher ----------

@router.post("/courses/{course_id}/practice/generate", status_code=201)
def generate(course_id: int, body: PracticeGenerateIn, p: Principal = Depends(staff), db: Session = Depends(get_db)):
    c = course_for(db, p, course_id, write=True)
    wait = _limiter.check(f"user:{p.id}")
    if wait is not None:
        return too_many(wait)
    res = PracticeGenerator(db, c, db.get(User, p.id)).generate(
        body.model_dump(include={"week", "topic", "material_id"}, exclude_none=True), body.n, list(body.kinds))
    record(db, p.id, "practice.generate", f"course:{c.id}", created=len(res["items"]), label=res["label"])
    db.commit()
    return {"label": res["label"], "requested": res["requested"], "discarded": res["discarded"],
            "items": [_out(i) for i in res["items"]], "counts": _counts(db, c.id)}


@router.get("/courses/{course_id}/validation")
def validation_queue(course_id: int, p: Principal = Depends(staff), db: Session = Depends(get_db)):
    """UC-10: each DRAFT item beside the chunk it was generated from."""
    c = course_for(db, p, course_id, write=True)
    refresh(db, c)
    drafts = db.scalars(select(PracticeItem).where(PracticeItem.course_id == c.id, PracticeItem.status == "draft")
                        .order_by(PracticeItem.id)).all()
    return {"counts": _counts(db, c.id), "items": [_out(i) for i in drafts]}


@router.get("/courses/{course_id}/practice/items", response_model=list[PracticeItemOut])
def items(course_id: int, status: str | None = None, p: Principal = Depends(staff), db: Session = Depends(get_db)):
    c = course_for(db, p, course_id, write=True)
    refresh(db, c)
    q = select(PracticeItem).where(PracticeItem.course_id == c.id).order_by(PracticeItem.id.desc())
    if status:
        if status not in ITEM_STATES:
            raise bad_request(f"status must be one of {', '.join(ITEM_STATES)}")
        q = q.where(PracticeItem.status == status)
    rows = list(db.scalars(q).all())
    stats = _stats(db, c, [i.id for i in rows])
    return [_out(i, stats.get(i.id)) for i in rows]


def _staff_item(db: Session, p: Principal, item_id: int) -> tuple[Course, PracticeItem]:
    item = db.get(PracticeItem, item_id)
    if item is None:
        raise not_found("Practice question")
    return course_for(db, p, item.course_id, write=True), item


@router.patch("/practice/{item_id}", response_model=PracticeItemOut)
def review(item_id: int, body: PracticeReviewIn, p: Principal = Depends(staff), db: Session = Depends(get_db)):
    """UC-10 step 2: approve, edit then approve, or reject with a reason. Staff may also retire an item."""
    c, item = _staff_item(db, p, item_id)
    if body.action == "approve":
        edits = body.model_dump(include={"question", "options", "answer", "explanation"}, exclude_none=True)
        approve(c, item, p.id, edits or None)
    elif body.action == "reject":
        reject(item, body.reason)
    else:
        if item.status == "retired":
            raise bad_request("This question is already retired.")
        retire(item, body.reason or "Retired by the teacher.")
    record(db, p.id, f"practice.{body.action}", f"item:{item.id}", status=item.status, edited=item.edited)
    db.flush()
    return _out(item)


# ---------- student ----------

@router.get("/courses/{course_id}/practice/due")
def due(course_id: int, p: Principal = Depends(student_only), db: Session = Depends(get_db)):
    c = course_for(db, p, course_id)
    view = MasteryTracker(db, c, db.get(User, p.id)).view()
    db.commit()
    return view


def _student_item(db: Session, p: Principal, item_id: int) -> tuple[Course, PracticeItem]:
    item = db.get(PracticeItem, item_id)
    if item is None:
        raise not_found("Practice question")
    return course_for(db, p, item.course_id), item   # 404 for a course the student has not joined


@router.post("/practice/{item_id}/attempts")
def attempt(item_id: int, body: AttemptIn, p: Principal = Depends(student_only), db: Session = Depends(get_db)):
    c, item = _student_item(db, p, item_id)
    result = MasteryTracker(db, c, db.get(User, p.id)).attempt(item, body.answer, body.self_correct)
    db.commit()
    return result


@router.post("/practice/{item_id}/reveal")
def reveal(item_id: int, p: Principal = Depends(student_only), db: Session = Depends(get_db)):
    """A short answer is self-marked: the student writes theirs first, then compares with the model answer."""
    c, item = _student_item(db, p, item_id)
    MasteryTracker(db, c, db.get(User, p.id)).released_item(item)
    db.commit()
    if item.kind != "short":
        raise bad_request("Only short-answer questions are self-marked; answer this one to see if you are right.")
    return {"answer": item.answer, "explanation": item.explanation, "source": item.source}
