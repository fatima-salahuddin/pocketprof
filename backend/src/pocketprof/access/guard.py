"""AccessGuard (M8, FR-15, SDS 2.2 "authorise before reading").

Every route receives a Principal from `current_principal`, and every course-scoped route goes through
`course_for`, which checks role plus enrolment (students) or ownership (teachers) before any store is
touched. Admins re-check their role against the database on each request, not just the token claim.
"""
from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.errors import Problem, forbidden, not_found
from ..core.security import decode_access_token
from ..db import get_db
from ..db.models import Course, Enrollment, User


@dataclass(frozen=True)
class Principal:
    id: int
    role: str
    email: str
    name: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _unauthorized(detail: str) -> Problem:
    return Problem(401, "Unauthorized", detail, "/problems/unauthorized")


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise _unauthorized("Sign in to continue.")
    try:
        claims = decode_access_token(header[7:].strip())
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Your session has expired.") from None
    except jwt.PyJWTError:
        raise _unauthorized("Invalid access token.") from None
    user = db.get(User, int(claims["sub"]))
    if user is None or not user.is_active:
        raise _unauthorized("This account is not active.")
    # The database is authoritative for the role: a demoted admin loses access immediately.
    principal = Principal(user.id, user.role, user.email, user.name)
    request.state.principal = principal
    return principal


def require_role(*roles: str):
    def dep(p: Principal = Depends(current_principal)) -> Principal:
        if p.role not in roles:
            raise forbidden(f"This action needs the {' or '.join(roles)} role.")
        return p
    return dep


def can_access_course(db: Session, p: Principal, course: Course, *, write: bool = False) -> bool:
    if p.is_admin:
        return True
    if p.role == "teacher":
        return course.teacher_id == p.id
    if write:
        return False
    return db.scalar(select(Enrollment).where(Enrollment.user_id == p.id,
                                              Enrollment.course_id == course.id)) is not None


def course_for(db: Session, p: Principal, course_id: int, *, write: bool = False) -> Course:
    """Load a course the principal may use, or raise. Unknown and forbidden look the same to students,
    so course IDs cannot be probed."""
    course = db.get(Course, course_id)
    if course is None or not can_access_course(db, p, course, write=write):
        if course is not None and p.role != "student":
            raise forbidden("You do not own this course." if p.role == "teacher" else
                            "You are not enrolled in this course.")
        raise not_found("Course")
    return course
