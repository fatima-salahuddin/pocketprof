"""/courses/{id}/chat (Server-Sent Events) and /threads (FR-4 to FR-7, FR-14, FR-16)."""
from __future__ import annotations

import asyncio
import json
import threading

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import iterate_in_threadpool

from ..access.guard import Principal, course_for, current_principal
from ..config import get_settings
from ..core.errors import not_found
from ..db import get_db, session_scope
from ..db.models import Conversation, Course, User
from ..generation.responder import ChatResponder
from .middleware import CHAT_OUTCOMES, RateLimiter, too_many
from .schemas import ChatIn, MessageOut, ThreadDetailOut, ThreadOut

router = APIRouter(tags=["chat"])
_limiter = RateLimiter(get_settings().rate_limit_per_minute)


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


@router.post("/courses/{course_id}/chat")
async def chat(course_id: int, body: ChatIn, request: Request, p: Principal = Depends(current_principal),
               db: Session = Depends(get_db)):
    # Authorise before reading (SDS 8.1 step 2): the guard runs before any collection is touched.
    course_for(db, p, course_id)
    wait = _limiter.check(f"user:{p.id}")
    if wait is not None:
        return too_many(wait)
    stop = threading.Event()

    def events():
        # The stream outlives the request's session, so it opens its own unit of work.
        with session_scope() as s:
            course = s.get(Course, course_id)
            user = s.get(User, p.id)
            responder = ChatResponder(s, course, user)
            try:
                for ev in responder.respond(body.message, thread_id=body.thread_id, mode=body.mode, stop=stop):
                    if ev.type == "done":
                        CHAT_OUTCOMES.labels(ev.data["outcome"]).inc()
                        s.commit()
                    yield _sse(ev.type, ev.data)
            except Exception as e:  # report inside the stream; the HTTP status is already 200
                detail = getattr(e, "detail", None) or "Something went wrong while answering."
                yield _sse("error", {"detail": str(detail)})
                raise

    async def guarded():
        async def watch():
            while not stop.is_set():
                if await request.is_disconnected():
                    stop.set()  # the student closed the page: stop generating
                    return
                await asyncio.sleep(0.5)

        watcher = asyncio.create_task(watch())
        try:
            async for chunk in iterate_in_threadpool(events()):
                yield chunk
        finally:
            stop.set()
            watcher.cancel()

    return StreamingResponse(guarded(), media_type="text/event-stream",
                             headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


@router.get("/threads", response_model=list[ThreadOut])
def threads(course_id: int | None = None, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    q = select(Conversation).where(Conversation.user_id == p.id).order_by(Conversation.updated_at.desc()).limit(100)
    if course_id is not None:
        course_for(db, p, course_id)
        q = q.where(Conversation.course_id == course_id)
    return [ThreadOut.model_validate(c) for c in db.scalars(q).all()]


def _own_thread(db: Session, p: Principal, thread_id: int) -> Conversation:
    conv = db.get(Conversation, thread_id)
    if conv is None or conv.user_id != p.id:
        raise not_found("Thread")
    course_for(db, p, conv.course_id)  # enrolment may have ended since the thread was created
    return conv


@router.get("/threads/{thread_id}", response_model=ThreadDetailOut)
def thread(thread_id: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    conv = _own_thread(db, p, thread_id)
    return ThreadDetailOut(id=conv.id, course_id=conv.course_id, title=conv.title, created_at=conv.created_at,
                           updated_at=conv.updated_at,
                           messages=[MessageOut.model_validate(m) for m in conv.messages])


@router.delete("/threads/{thread_id}", status_code=204)
def delete_thread(thread_id: int, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """Conversations are deletable on request (SDS Table 6, retention)."""
    db.delete(_own_thread(db, p, thread_id))
