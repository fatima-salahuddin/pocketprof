"""/google: connect a teacher's Google account and list the classes it teaches."""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..access import audit
from ..access.guard import Principal, require_role
from ..config import get_settings
from ..db import get_db
from ..db.models import GoogleAccount
from ..ingestion import google_classroom

log = logging.getLogger("pocketprof.api.google")
router = APIRouter(prefix="/google", tags=["google"])
teacher = require_role("teacher")


@router.get("/status")
def status(p: Principal = Depends(teacher), db: Session = Depends(get_db)):
    row = db.get(GoogleAccount, p.id)
    return {"configured": get_settings().google_configured, "connected": row is not None,
            "email": row.google_email if row else None,
            "roster_scope": bool(row and any("rosters" in s for s in (row.scopes or [])))}


@router.post("/connect")
def connect(return_to: str = "/teacher", p: Principal = Depends(teacher)):
    """Returns the Google consent URL; the client navigates there."""
    if not return_to.startswith("/"):
        return_to = "/teacher"
    return {"url": google_classroom.authorization_url(p.id, return_to)}


@router.get("/callback")
def callback(request: Request, state: str = "", error: str | None = None, db: Session = Depends(get_db)):
    """Google redirects the browser here. No bearer token: the signed state names the user."""
    web = get_settings().public_url.rstrip("/")
    if error:
        return RedirectResponse(f"{web}/teacher?google_error={quote(error)}")
    try:
        user_id, return_to = google_classroom.complete_authorization(db, str(request.url), state)
        audit.record(db, user_id, "google.connect")
        db.commit()
    except Exception as e:
        log.warning("google callback failed", extra={"error": type(e).__name__})
        detail = getattr(e, "detail", None) or "Google sign-in failed. Please try again."
        return RedirectResponse(f"{web}/teacher?google_error={quote(str(detail)[:200])}")
    return RedirectResponse(f"{web}{return_to}?google=connected")


@router.delete("/connection", status_code=204)
def disconnect(p: Principal = Depends(teacher), db: Session = Depends(get_db)):
    row = db.get(GoogleAccount, p.id)
    if row is not None:
        db.delete(row)
        audit.record(db, p.id, "google.disconnect")


@router.get("/classes")
def classes(p: Principal = Depends(teacher), db: Session = Depends(get_db)):
    return google_classroom.teaching_classes(db, p.id)
