"""/courses/{id}/insights: what students ask, for the teacher (FR-19 first slice; C-8).

Staff never see who asked what. Recent questions are listed without any identity. Aggregates per week
and topic report the number of distinct students behind them, and any row from fewer than the minimum
cohort is suppressed in the query itself, before it leaves the database layer (SDS 4.6).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..access.guard import Principal, course_for, require_role
from ..arms import load_arm
from ..db import get_db
from ..db.models import QueryLog

router = APIRouter(tags=["insights"])
teacher_or_admin = require_role("teacher", "admin")


@router.get("/courses/{course_id}/questions")
def questions(course_id: int, limit: int = 50, p: Principal = Depends(teacher_or_admin), db: Session = Depends(get_db)):
    c = course_for(db, p, course_id, write=True)
    rows = db.execute(select(QueryLog.question, QueryLog.outcome, QueryLog.week, QueryLog.topic, QueryLog.ts)
                      .where(QueryLog.course_id == c.id).order_by(QueryLog.id.desc()).limit(min(limit, 200))).all()
    return [{"question": r.question, "outcome": r.outcome, "week": r.week, "topic": r.topic, "ts": r.ts}
            for r in rows]


@router.get("/courses/{course_id}/signal")
def signal(course_id: int, week: int | None = None, p: Principal = Depends(teacher_or_admin),
           db: Session = Depends(get_db)):
    c = course_for(db, p, course_id, write=True)
    min_cohort = load_arm(c.active_arm).analytics.min_cohort
    students = func.count(func.distinct(QueryLog.student_ref))
    q = (select(QueryLog.week, QueryLog.topic, func.count().label("questions"), students.label("students"),
                func.sum(case((QueryLog.outcome == "refused", 1), else_=0)).label("refused"))
         .where(QueryLog.course_id == c.id, QueryLog.outcome != "smalltalk")
         .group_by(QueryLog.week, QueryLog.topic)
         .having(students >= min_cohort)
         .order_by(func.count().desc()))
    if week is not None:
        q = q.where(QueryLog.week == week)
    rows = db.execute(q).all()
    totals = db.execute(select(func.count(), students).where(QueryLog.course_id == c.id,
                                                             QueryLog.outcome != "smalltalk")).one()
    return {"min_cohort": min_cohort, "total_questions": totals[0], "total_students": totals[1],
            "rows": [{"week": r.week, "topic": r.topic or "General", "questions": r.questions, "students": r.students,
                      "refusal_rate": round(r.refused / r.questions, 3) if r.questions else 0.0} for r in rows]}
