"""/courses/{id}/notes, /notes and /exports (M3: FR-8, FR-9; SDS Table 5).

Every course route authorises with course_for before any store is read. Notes and threads are private to
their owner: someone else's ID returns 404, and enrolment is re-checked because it may have ended since the
note was made. Logs carry identifiers and timings only, never note text.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..access.guard import Principal, course_for, current_principal
from ..config import get_settings
from ..core.errors import not_found
from ..db import get_db
from ..db.models import Material, Note, User
from ..exports import documents, pdf
from ..generation.notes import NotesGenerator
from .middleware import RateLimiter, too_many
from .routes_chat import _own_thread
from .schemas import ExportIn, NoteOut, NotesIn, NotesOptionsOut, NoteSummary

router = APIRouter(tags=["notes"])
_limiter = RateLimiter(get_settings().rate_limit_per_minute)


@router.get("/courses/{course_id}/notes/options", response_model=NotesOptionsOut)
def options(course_id: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """What can be selected: weeks, lectures and topic tags that have indexed material, and the files."""
    c = course_for(db, p, course_id)
    indexed = (Material.course_id == c.id, Material.status == "indexed")
    weeks = db.scalars(select(Material.week).distinct().where(*indexed, Material.week.is_not(None))).all()
    lectures = db.scalars(select(Material.lecture_no).distinct().where(*indexed, Material.lecture_no.is_not(None))).all()
    topics = db.scalars(select(Material.topic).distinct().where(*indexed, Material.topic.is_not(None))).all()
    files = db.execute(select(Material.id, Material.title, Material.week, Material.topic, Material.kind)
                       .where(*indexed, Material.n_chunks > 0)
                       .order_by(Material.week, Material.published_at, Material.title)).all()
    return NotesOptionsOut(weeks=sorted(weeks), lectures=sorted(lectures), topics=sorted(topics, key=str.lower),
                           files=[{"id": f.id, "title": f.title, "week": f.week, "topic": f.topic, "kind": f.kind}
                                  for f in files])


@router.post("/courses/{course_id}/notes", response_model=NoteOut, status_code=201)
def create_note(course_id: int, body: NotesIn, p: Principal = Depends(current_principal),
                db: Session = Depends(get_db)):
    c = course_for(db, p, course_id)  # authorise before reading (SDS 8.1 step 2)
    wait = _limiter.check(f"user:{p.id}")
    if wait is not None:
        return too_many(wait)
    note = NotesGenerator(db, c, db.get(User, p.id)).generate(body.model_dump(exclude_none=True))
    db.commit()
    return NoteOut.model_validate(note)


@router.get("/notes", response_model=list[NoteSummary])
def list_notes(course_id: int | None = None, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    q = select(Note).where(Note.user_id == p.id).order_by(Note.id.desc()).limit(100)
    if course_id is not None:
        course_for(db, p, course_id)
        q = q.where(Note.course_id == course_id)
    return [NoteSummary.model_validate(n) for n in db.scalars(q).all()]


def _own_note(db: Session, p: Principal, note_id: int) -> Note:
    note = db.get(Note, note_id)
    if note is None or note.user_id != p.id:
        raise not_found("Note")
    course_for(db, p, note.course_id)  # enrolment may have ended since the note was made
    return note


@router.get("/notes/{note_id}", response_model=NoteOut)
def get_note(note_id: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return NoteOut.model_validate(_own_note(db, p, note_id))


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(note_id: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    db.delete(_own_note(db, p, note_id))


@router.post("/exports", response_class=Response,
             responses={200: {"content": {"application/pdf": {}}, "description": "The PDF"}})
def export(body: ExportIn, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """FR-9: a conversation, a set of notes or a practice paper, as a PDF."""
    if body.kind == "thread":
        doc = documents.thread_document(db, _own_thread(db, p, body.ref))
    elif body.kind == "notes":
        doc = documents.notes_document(db, _own_note(db, p, body.ref))
    else:
        course = course_for(db, p, body.ref)
        doc = documents.KINDS["paper"](db, course, p, topic=body.topic)
    return Response(content=pdf.render(doc), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{doc.filename}"',
                             "Cache-Control": "no-store"})
