"""/admin: users and roster import (FR-21), provisioning (FR-1, FR-23), configuration arms (FR-22),
evaluation runs (FR-25) and the audit log."""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access import accounts, audit
from ..access.guard import Principal, require_role
from ..arms import list_arms, load_arm
from ..core.errors import bad_request, conflict, not_found
from ..db import get_db
from ..db.models import AuditLog, Course, Enrollment, ExperimentRun, User
from ..evaluation.runner import start_run
from ..ingestion.provisioning import ProvisioningJob
from .routes_courses import course_out
from .schemas import ArmOut, CourseOut, EvalRunIn, SetArmIn, UserAdminOut, UserCreateIn, UserPatchIn

router = APIRouter(prefix="/admin", tags=["admin"])
admin = require_role("admin")


# ----- users -----

@router.get("/users", response_model=list[UserAdminOut])
def users(role: str | None = None, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    q = select(User).order_by(User.role, User.name)
    if role:
        q = q.where(User.role == role)
    return [UserAdminOut.model_validate(u) for u in db.scalars(q).all()]


@router.post("/users", response_model=UserAdminOut, status_code=201)
def create_user(body: UserCreateIn, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    u = accounts.create_user(db, email=body.email, name=body.name, role=body.role, password=body.password)
    audit.record(db, p.id, "user.create", f"user:{u.id}", role=u.role)
    return UserAdminOut.model_validate(u)


@router.patch("/users/{user_id}", response_model=UserAdminOut)
def patch_user(user_id: int, body: UserPatchIn, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise not_found("User")
    if u.id == p.id and (body.role not in (None, "admin") or body.is_active is False):
        raise conflict("You cannot demote or deactivate your own account.")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(u, field, value)
    audit.record(db, p.id, "user.update", f"user:{u.id}", **body.model_dump(exclude_unset=True))
    return UserAdminOut.model_validate(u)


@router.post("/roster")
async def roster(file: UploadFile = File(...), p: Principal = Depends(admin), db: Session = Depends(get_db)):
    """CSV with columns email, name, role, password[, course_code]. Existing users are left as they are
    and, if a course_code is given, enrolled. Every row is validated before anything is written."""
    text = (await file.read(2 * 1024 * 1024)).decode("utf-8", errors="replace").lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    need = {"email", "name", "role"}
    if not need <= {f.strip().lower() for f in reader.fieldnames or []}:
        raise bad_request("The roster needs the columns email, name and role (password and course_code optional).")
    rows, errors = [], []
    courses = {c.code.upper(): c for c in db.scalars(select(Course)).all()}
    for i, raw in enumerate(reader, start=2):
        r = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        if r["role"] not in ("student", "teacher", "admin"):
            errors.append({"row": i, "message": f"role must be student, teacher or admin, not '{r['role']}'"})
        if "@" not in r["email"]:
            errors.append({"row": i, "message": "invalid email"})
        code = r.get("course_code", "").upper()
        if code and code not in courses:
            errors.append({"row": i, "message": f"unknown course code {code}"})
        rows.append(r)
    if errors:
        raise bad_request("Some roster rows are invalid; nothing was imported.", errors)
    created = enrolled = existing = 0
    for r in rows:
        u = db.scalar(select(User).where(User.email == accounts.normalise_email(r["email"])))
        if u is None:
            u = accounts.create_user(db, email=r["email"], name=r["name"], role=r["role"],
                                     password=r.get("password") or None)
            created += 1
        else:
            existing += 1
        code = r.get("course_code", "").upper()
        if code and u.role == "student" and db.get(Enrollment, (u.id, courses[code].id)) is None:
            db.add(Enrollment(user_id=u.id, course_id=courses[code].id))
            enrolled += 1
    audit.record(db, p.id, "roster.import", None, created=created, existing=existing, enrolled=enrolled)
    return {"created": created, "existing": existing, "enrolled": enrolled}


# ----- courses, provisioning and arms -----

@router.get("/courses", response_model=list[CourseOut])
def all_courses(p: Principal = Depends(admin), db: Session = Depends(get_db)):
    return [course_out(db, c, p) for c in db.scalars(select(Course).order_by(Course.id)).all()]


@router.post("/courses/{course_id}/provision", status_code=202)
def provision(course_id: int, full: bool = False, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    c = db.get(Course, course_id)
    if c is None:
        raise not_found("Course")
    if not ProvisioningJob(c.id, "admin", force=full).start():
        raise conflict("This course is already syncing.")
    audit.record(db, p.id, "course.provision", f"course:{c.id}", full=full)
    return {"started": True}


def _arm_out(a) -> ArmOut:
    return ArmOut(arm=a.arm, description=a.description, config_hash=a.config_hash, index_version=a.index_version,
                  config=a.model_dump(exclude={"arm", "description"}))


@router.get("/arms", response_model=list[ArmOut])
def arms(p: Principal = Depends(admin)):
    return [_arm_out(a) for a in list_arms()]


@router.put("/courses/{course_id}/config", response_model=ArmOut)
def set_arm(course_id: int, body: SetArmIn, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    c = db.get(Course, course_id)
    if c is None:
        raise not_found("Course")
    try:
        arm = load_arm(body.arm)
    except KeyError as e:
        raise bad_request(str(e)) from None
    previous = c.active_arm
    c.active_arm = arm.arm
    audit.record(db, p.id, "course.set_arm", f"course:{c.id}", arm=arm.arm, previous=previous,
                 config_hash=arm.config_hash)
    db.commit()
    if c.index_version != arm.index_version:
        ProvisioningJob(c.id, "admin").start()  # chunking or embedding changed: re-index under the new arm
    return _arm_out(arm)


# ----- evaluation (M9) -----

@router.post("/eval/runs", status_code=202)
def eval_run(body: EvalRunIn, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    if db.get(Course, body.course_id) is None:
        raise not_found("Course")
    run_id = start_run(body.course_id, body.query_set, body.split, body.arm, body.experiment, generate=body.generate)
    audit.record(db, p.id, "eval.run", f"experiment_run:{run_id}", arm=body.arm, split=body.split)
    return {"run_id": run_id}


@router.get("/eval/runs")
def eval_runs(p: Principal = Depends(admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(ExperimentRun).order_by(ExperimentRun.id.desc()).limit(50)).all()
    return [{"id": r.id, "experiment": r.experiment, "arm": r.arm, "config_hash": r.config_hash,
             "course_id": r.course_id, "query_set": r.query_set, "query_set_version": r.query_set_version,
             "split": r.split, "model": r.model, "prompt_version": r.prompt_version, "index_version": r.index_version,
             "status": r.status, "started_at": r.started_at, "finished_at": r.finished_at, "metrics": r.metrics}
            for r in rows]


@router.get("/eval/runs/{run_id}")
def eval_run_detail(run_id: int, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    r = db.get(ExperimentRun, run_id)
    if r is None:
        raise not_found("Evaluation run")
    return {"id": r.id, "experiment": r.experiment, "arm": r.arm, "config_hash": r.config_hash, "status": r.status,
            "metrics": r.metrics, "results": r.results, "model": r.model, "split": r.split}


# ----- audit -----

@router.get("/audit")
def audit_log(limit: int = 100, p: Principal = Depends(admin), db: Session = Depends(get_db)):
    rows = db.execute(select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.actor_id)
                      .order_by(AuditLog.id.desc()).limit(min(limit, 500))).all()
    return [{"id": a.id, "actor": email, "action": a.action, "target": a.target, "detail": a.detail, "ts": a.ts}
            for a, email in rows]
