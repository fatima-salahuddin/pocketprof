"""Typed request and response models for /api/v1 (SDS 2.3: contracts fixed as Pydantic models)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# A format check only: institutional and internal domains (e.g. .local) must be accepted.
Email = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, max_length=255,
                                        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ----- auth -----

class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)


class SignupIn(BaseModel):
    email: Email
    name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=200)


class UserOut(ORM):
    id: int
    email: str
    name: str
    role: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


# ----- courses -----

class CourseOut(ORM):
    id: int
    code: str
    title: str
    section: str | None
    teacher_name: str
    join_code: str | None = None          # only for the course's teacher and admins
    source: Literal["google", "upload"]
    gc_link: str | None = None
    status: str
    status_msg: str | None
    last_synced_at: datetime | None
    n_chunks: int
    n_materials: int = 0
    term_start: date | None = None
    active_arm: str | None = None


class CourseCreateIn(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    title: str = Field(min_length=2, max_length=200)
    section: str | None = Field(default=None, max_length=64)
    term_start: date | None = None


class CourseUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    section: str | None = Field(default=None, max_length=64)
    term_start: date | None = None


class LinkGoogleIn(BaseModel):
    class_code: str = Field(min_length=4, max_length=32)
    code: str | None = Field(default=None, max_length=32, description="course code shown in PocketProf, e.g. CS-2001")
    term_start: date | None = None


class JoinIn(BaseModel):
    join_code: str = Field(min_length=4, max_length=32)


class MaterialOut(ORM):
    id: int
    source: str
    kind: str
    post_title: str | None
    topic: str | None
    title: str
    url: str | None
    post_url: str | None
    mime: str
    published_at: datetime | None
    week: int | None
    status: str
    note: str | None
    n_chunks: int
    ocr_pages: int
    updated_at: datetime | None


class RunOut(ORM):
    id: int
    course_id: int
    trigger: str
    arm: str
    config_hash: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    counts: dict
    error: str | None


class RunDetailOut(RunOut):
    items: list[dict]


# ----- chat -----

class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    thread_id: int | None = None
    mode: Literal["direct", "scaffolded"] | None = None


class MessageOut(ORM):
    id: int
    role: str
    content: str
    mode: str | None
    scope: dict | None
    citations: list | None
    outcome: str | None
    created_at: datetime


class ThreadOut(ORM):
    id: int
    course_id: int
    title: str
    created_at: datetime
    updated_at: datetime | None


class ThreadDetailOut(ThreadOut):
    messages: list[MessageOut]


# ----- admin -----

class UserCreateIn(BaseModel):
    email: Email
    name: str = Field(min_length=2, max_length=120)
    role: Literal["student", "teacher", "admin"]
    password: str = Field(min_length=8, max_length=200)


class UserAdminOut(UserOut):
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None


class UserPatchIn(BaseModel):
    role: Literal["student", "teacher", "admin"] | None = None
    is_active: bool | None = None


class ArmOut(BaseModel):
    arm: str
    description: str
    config_hash: str
    index_version: str
    config: dict


class SetArmIn(BaseModel):
    arm: str


class EvalRunIn(BaseModel):
    course_id: int
    query_set: str = "ds-v1"
    split: Literal["dev", "test", "all"] = "dev"
    arm: str = "shipped-v1"
    experiment: str = "regression"
    generate: bool = True
