"""/courses: listing, creation, Google Classroom linking, joining, materials, calendar, sync and runs."""
from __future__ import annotations

import secrets
from datetime import date

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..access import audit
from ..access.guard import Principal, course_for, current_principal, require_role
from ..config import get_settings
from ..core.errors import bad_request, conflict, not_found
from ..db import get_db
from ..db.models import Course, Enrollment, IngestionRun, Material
from ..ingestion import calendar, google_classroom, uploads
from ..ingestion.provisioning import ProvisioningJob, is_running
from ..retrieval.vector_index import collection_name, get_index
from .schemas import (
    CourseCreateIn,
    CourseOut,
    CourseUpdateIn,
    JoinIn,
    LinkGoogleIn,
    MaterialOut,
    RunDetailOut,
    RunOut,
)

router = APIRouter(prefix="/courses", tags=["courses"])
teacher_or_admin = require_role("teacher", "admin")


def course_out(db: Session, c: Course, p: Principal) -> CourseOut:
    n_materials = db.scalar(select(func.count()).select_from(Material)
                            .where(Material.course_id == c.id, Material.status == "indexed")) or 0
    staff = p.is_admin or c.teacher_id == p.id
    return CourseOut(id=c.id, code=c.code, title=c.title, section=c.section, teacher_name=c.teacher.name,
                     join_code=c.join_code if staff else None, source="google" if c.gc_course_id else "upload",
                     gc_link=c.gc_link, status=c.status, status_msg=c.status_msg, last_synced_at=c.last_synced_at,
                     n_chunks=c.n_chunks, n_materials=n_materials, term_start=c.term_start,
                     active_arm=c.active_arm if staff else None)


def _new_course(db: Session, teacher_id: int, **fields) -> Course:
    c = Course(teacher_id=teacher_id, collection_name=f"pending_{secrets.token_hex(6)}",
               active_arm=get_settings().active_arm, **fields)
    db.add(c)
    db.flush()
    c.collection_name = collection_name(c.id)
    return c


@router.get("", response_model=list[CourseOut])
def list_courses(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    q = select(Course).order_by(Course.created_at.desc())
    if p.role == "teacher":
        q = q.where(Course.teacher_id == p.id)
    elif p.role == "student":
        q = q.join(Enrollment, Enrollment.course_id == Course.id).where(Enrollment.user_id == p.id)
    return [course_out(db, c, p) for c in db.scalars(q).all()]


@router.post("", response_model=CourseOut, status_code=201)
def create_course(body: CourseCreateIn, p: Principal = Depends(require_role("teacher")),
                  db: Session = Depends(get_db)):
    """An upload-only course (FR-24). Google-linked courses are created with POST /courses/link-google."""
    c = _new_course(db, p.id, code=body.code.strip().upper(), title=body.title.strip(), section=body.section,
                    term_start=body.term_start, join_code=secrets.token_urlsafe(6).replace("-", "x").replace("_", "y"))
    audit.record(db, p.id, "course.create", f"course:{c.id}")
    return course_out(db, c, p)


@router.post("/link-google", response_model=CourseOut, status_code=201)
def link_google(body: LinkGoogleIn, p: Principal = Depends(require_role("teacher")), db: Session = Depends(get_db)):
    gc = google_classroom.find_class_by_code(db, p.id, body.class_code)
    existing = db.scalar(select(Course).where(Course.gc_course_id == gc["id"]))
    if existing is not None:
        if existing.teacher_id != p.id:
            raise conflict("This class is already linked to PocketProf by another teacher.")
        return course_out(db, existing, p)
    code = (body.code or gc["name"].split()[0]).strip().upper()[:32]
    c = _new_course(db, p.id, code=code, title=gc["name"], section=gc.get("section"), join_code=gc["code"],
                    gc_course_id=gc["id"], gc_link=gc.get("link"), term_start=body.term_start)
    audit.record(db, p.id, "course.link_google", f"course:{c.id}", gc_course_id=gc["id"])
    db.commit()
    ProvisioningJob(c.id, "link").start()
    return course_out(db, c, p)


@router.post("/join", response_model=CourseOut)
def join(body: JoinIn, p: Principal = Depends(require_role("student")), db: Session = Depends(get_db)):
    """Students join with the class code their teacher shares (the Google Classroom class code for linked
    courses). With ENFORCE_ROSTER on, the student's email must also be on the Classroom roster."""
    c = db.scalar(select(Course).where(func.lower(Course.join_code) == body.join_code.strip().lower()))
    if c is None:
        raise not_found("A class with that code")
    if get_settings().enforce_roster and c.gc_course_id:
        roster = google_classroom.roster_emails(db, c.teacher_id, c.gc_course_id)
        if roster is not None and p.email.lower() not in roster:
            raise bad_request("Your email is not on this class's Google Classroom roster. Sign in with the "
                              "account you use for Classroom, or ask your teacher to add you.")
    if db.get(Enrollment, (p.id, c.id)) is None:
        db.add(Enrollment(user_id=p.id, course_id=c.id))
    return course_out(db, c, p)


@router.get("/{course_id}", response_model=CourseOut)
def get_course(course_id: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return course_out(db, course_for(db, p, course_id), p)


@router.patch("/{course_id}", response_model=CourseOut)
def update_course(course_id: int, body: CourseUpdateIn, p: Principal = Depends(teacher_or_admin),
                  db: Session = Depends(get_db)):
    c = course_for(db, p, course_id, write=True)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(c, field, value)
    audit.record(db, p.id, "course.update", f"course:{c.id}", **body.model_dump(exclude_unset=True, mode="json"))
    return course_out(db, c, p)


@router.delete("/{course_id}", status_code=204)
def delete_course(course_id: int, p: Principal = Depends(teacher_or_admin), db: Session = Depends(get_db)):
    c = course_for(db, p, course_id, write=True)
    if is_running(c.id):
        raise conflict("The course is syncing; try again when it finishes.")
    get_index().drop(c.collection_name)
    audit.record(db, p.id, "course.delete", f"course:{c.id}", code=c.code)
    db.delete(c)


# ----- students -----

@router.get("/{course_id}/students")
def students(course_id: int, p: Principal = Depends(teacher_or_admin), db: Session = Depends(get_db)):
    """Enrolment count only. Teachers never see an individual student's questions or activity (C-8)."""
    c = course_for(db, p, course_id, write=True)
    n = db.scalar(select(func.count()).select_from(Enrollment).where(Enrollment.course_id == c.id)) or 0
    return {"enrolled": n}


# ----- materials (FR-24) -----

@router.get("/{course_id}/materials", response_model=list[MaterialOut])
def list_materials(course_id: int, p: Principal = Depends(teacher_or_admin), db: Session = Depends(get_db)):
    c = course_for(db, p, course_id, write=True)
    rows = db.scalars(select(Material).where(Material.course_id == c.id)
                      .order_by(Material.published_at.desc().nulls_last(), Material.id)).all()
    return [MaterialOut.model_validate(r) for r in rows]


@router.post("/{course_id}/materials", response_model=list[MaterialOut], status_code=201)
async def upload_materials(course_id: int, files: list[UploadFile] = File(...), topic: str | None = Form(None),
                           week: int | None = Form(None), p: Principal = Depends(teacher_or_admin),
                           db: Session = Depends(get_db)):
    c = course_for(db, p, course_id, write=True)
    if week is not None and not 1 <= week <= 30:
        raise bad_request("Week must be between 1 and 30.")
    limit = get_settings().max_upload_mb * 1024 * 1024
    rows = []
    for f in files:
        data = await f.read(limit + 1)
        rows.append(uploads.store(db, c, f.filename or "upload", data, topic=topic, week=week))
    audit.record(db, p.id, "materials.upload", f"course:{c.id}", files=[f.filename for f in files])
    db.commit()
    ProvisioningJob(c.id, "upload").start()
    return [MaterialOut.model_validate(r) for r in rows]


@router.delete("/{course_id}/materials/{material_id}", status_code=204)
def withdraw_material(course_id: int, material_id: int, p: Principal = Depends(teacher_or_admin),
                      db: Session = Depends(get_db)):
    c = course_for(db, p, course_id, write=True)
    row = db.get(Material, material_id)
    if row is None or row.course_id != c.id:
        raise not_found("Material")
    if row.source != "upload":
        raise bad_request("Classroom material is withdrawn by removing it from Google Classroom.")
    get_index().delete_material(c.collection_name, row.id)
    uploads.withdraw(row)
    row.n_chunks = 0
    c.n_chunks = get_index().count(c.collection_name)
    audit.record(db, p.id, "materials.withdraw", f"material:{row.id}", title=row.title)


@router.put("/{course_id}/calendar")
async def put_calendar(course_id: int, file: UploadFile = File(...), p: Principal = Depends(teacher_or_admin),
                       db: Session = Depends(get_db)):
    c = course_for(db, p, course_id, write=True)
    text = (await file.read(1024 * 1024)).decode("utf-8", errors="replace")
    n = calendar.import_csv(db, c, text)
    audit.record(db, p.id, "calendar.import", f"course:{c.id}", rows=n)
    db.commit()
    ProvisioningJob(c.id, "manual").start()  # re-tag weeks on existing chunks
    return {"days": n}


@router.get("/{course_id}/calendar")
def get_calendar(course_id: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    c = course_for(db, p, course_id)
    cal = calendar.load(db, c)
    return {"term_start": cal.term_start, "current_week": cal.week_of(date.today()),
            "days": [{"date": d, "week": r.week, "lecture_no": r.lecture_no, "topics": r.topics}
                     for d, r in sorted(cal.days.items())]}


# ----- sync and run reports (FR-1 to FR-4) -----

@router.post("/{course_id}/sync", status_code=202)
def sync(course_id: int, full: bool = False, p: Principal = Depends(teacher_or_admin), db: Session = Depends(get_db)):
    c = course_for(db, p, course_id, write=True)
    if not ProvisioningJob(c.id, "manual", force=full).start():
        raise conflict("This course is already syncing.")
    audit.record(db, p.id, "course.sync", f"course:{c.id}", full=full)
    return {"started": True}


@router.get("/{course_id}/runs", response_model=list[RunOut])
def runs(course_id: int, limit: int = 20, p: Principal = Depends(teacher_or_admin), db: Session = Depends(get_db)):
    c = course_for(db, p, course_id, write=True)
    rows = db.scalars(select(IngestionRun).where(IngestionRun.course_id == c.id)
                      .order_by(IngestionRun.id.desc()).limit(min(limit, 100))).all()
    return [RunOut.model_validate(r) for r in rows]


@router.get("/{course_id}/runs/{run_id}", response_model=RunDetailOut)
def run_detail(course_id: int, run_id: int, p: Principal = Depends(teacher_or_admin), db: Session = Depends(get_db)):
    c = course_for(db, p, course_id, write=True)
    run = db.get(IngestionRun, run_id)
    if run is None or run.course_id != c.id:
        raise not_found("Run")
    return RunDetailOut.model_validate(run)
