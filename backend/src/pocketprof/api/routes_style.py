"""/teachers/me/style and /teachers/me/calibration (M5, FR-11; SDS Table 5; SRS UC-09).

A teacher only ever reads and writes their own style profile; the profile is used only for that teacher's
courses. Calibration answers and exemplars are the teacher's own writing and are never shown to students.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..access.audit import record
from ..access.guard import Principal, course_for, require_role
from ..arms import load_arm
from ..core.errors import not_found
from ..db import get_db
from ..db.models import StyleExemplar, User
from ..style.service import MIN_ANSWERS, StyleService, calibration_prompts, nearest_exemplars, style_block
from .schemas import CalibrationIn, ExemplarReviewIn, StylePreviewIn, StyleUpdateIn

router = APIRouter(tags=["style"])
teacher_only = require_role("teacher")


def _view(svc: StyleService) -> dict:
    p = svc.profile()
    exemplars = svc.exemplars()
    return {
        "profile": None if p is None else {
            "status": p.status, "rules": p.rules, "measures": p.measures, "model": p.model,
            "calibrated": bool(p.calibration), "answers": {c["prompt_id"]: c["answer"] for c in p.calibration},
            "updated_at": p.updated_at},
        "exemplars": [{"id": e.id, "origin": e.origin, "prompt": e.prompt, "text": e.text, "source_ref": e.source_ref,
                       "status": e.status} for e in exemplars],
        "counts": {s: sum(1 for e in exemplars if e.status == s) for s in ("approved", "pending", "rejected")},
        "prompts": calibration_prompts(svc.db, svc.teacher.id), "min_answers": MIN_ANSWERS,
    }


@router.get("/teachers/me/style")
def get_style(p: Principal = Depends(teacher_only), db: Session = Depends(get_db)):
    return _view(StyleService(db, db.get(User, p.id)))


@router.get("/teachers/me/calibration")
def get_calibration(p: Principal = Depends(teacher_only), db: Session = Depends(get_db)):
    return {"prompts": calibration_prompts(db, p.id), "min_answers": MIN_ANSWERS}


@router.post("/teachers/me/calibration")
def calibrate(body: CalibrationIn, p: Principal = Depends(teacher_only), db: Session = Depends(get_db)):
    """UC-09: calibration answers -> rules and exemplars for the teacher to review (nothing is used yet)."""
    svc = StyleService(db, db.get(User, p.id))
    svc.calibrate([a.model_dump() for a in body.answers])
    record(db, p.id, "style.calibrate", f"teacher:{p.id}")
    db.commit()
    return _view(svc)


@router.patch("/teachers/me/style")
def update_style(body: StyleUpdateIn, p: Principal = Depends(teacher_only), db: Session = Depends(get_db)):
    svc = StyleService(db, db.get(User, p.id))
    svc.update(rules=[r.model_dump() for r in body.rules] if body.rules is not None else None, status=body.status)
    if body.status:
        record(db, p.id, f"style.{body.status}", f"teacher:{p.id}")
    db.commit()
    return _view(svc)


@router.patch("/teachers/me/style/exemplars/{exemplar_id}")
def review_exemplar(exemplar_id: int, body: ExemplarReviewIn, p: Principal = Depends(teacher_only),
                    db: Session = Depends(get_db)):
    ex = db.get(StyleExemplar, exemplar_id)
    if ex is None or ex.teacher_id != p.id:
        raise not_found("Example")
    svc = StyleService(db, db.get(User, p.id))
    svc.review_exemplar(ex, status=body.status, text=body.text)
    db.commit()
    return _view(svc)


@router.post("/teachers/me/style/preview")
def preview(body: StylePreviewIn, p: Principal = Depends(teacher_only), db: Session = Depends(get_db)):
    """What would be added to a request for this question in one of the teacher's courses."""
    c = course_for(db, p, body.course_id, write=True)
    arm = load_arm(c.active_arm)
    block = style_block(db, c, arm, body.question)
    return {"style_block": block, "mode": arm.style.mode,
            "exemplars": [e["exemplar_id"] for e in nearest_exemplars(p.id, arm, body.question)] if block else []}
