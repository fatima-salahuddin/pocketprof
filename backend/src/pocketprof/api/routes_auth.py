"""/auth: password login, rotating refresh tokens (httpOnly cookie), sign-up, current user."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ..access import accounts
from ..access.guard import Principal, current_principal
from ..config import get_settings
from ..core.errors import Problem, forbidden
from ..db import get_db
from ..db.models import User
from .schemas import LoginIn, SignupIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])
COOKIE = "pp_refresh"


def _set_cookie(response: Response, raw: str) -> None:
    s = get_settings()
    response.set_cookie(COOKIE, raw, max_age=s.refresh_token_days * 86400, httponly=True,
                        secure=not s.is_dev, samesite="lax", path="/api/v1/auth")


def _out(pair: accounts.TokenPair, response: Response) -> TokenOut:
    _set_cookie(response, pair.refresh_token)
    return TokenOut(access_token=pair.access_token, expires_in=pair.expires_in,
                    user=UserOut.model_validate(pair.user))


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    return _out(accounts.login(db, body.email, body.password), response)


@router.post("/refresh", response_model=TokenOut)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(COOKIE)
    if not raw:
        raise Problem(401, "Unauthorized", "Please sign in.", "/problems/session-ended")
    return _out(accounts.refresh(db, raw), response)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(COOKIE)
    if raw:
        accounts.logout(db, raw)
    response.delete_cookie(COOKIE, path="/api/v1/auth")
    response.status_code = 204
    return response


@router.post("/signup", response_model=TokenOut, status_code=201)
def signup(body: SignupIn, response: Response, db: Session = Depends(get_db)):
    """Students may register themselves (configurable); teachers and admins are created by an admin."""
    if not get_settings().allow_signup:
        raise forbidden("Self sign-up is turned off. Ask your administrator for an account.")
    user = accounts.create_user(db, email=body.email, name=body.name, role="student", password=body.password)
    return _out(accounts.login(db, user.email, body.password), response)


@router.get("/me", response_model=UserOut)
def me(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return UserOut.model_validate(db.get(User, p.id))
