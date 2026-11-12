"""Typed request and response models for /api/v1 (SDS 2.3: contracts fixed as Pydantic models)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

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
    reveal: bool = Field(default=False, description="M7: give the full answer to the thread's last question "
                                                     "after a scaffolded reply (SRS UC-02 6a)")


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


# ----- notes and export (M3) -----

class NotesIn(BaseModel):
    week: int | None = Field(default=None, ge=1, le=30)
    lecture: int | None = Field(default=None, ge=1, le=200)
    topic: str | None = Field(default=None, min_length=2, max_length=200)
    material_id: int | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> NotesIn:
        given = [k for k in ("week", "lecture", "topic", "material_id") if getattr(self, k) is not None]
        if len(given) != 1:
            raise ValueError("choose exactly one of week, lecture, topic or material_id")
        return self


class NoteSummary(ORM):
    id: int
    course_id: int
    title: str
    scope_label: str
    outcome: str
    created_at: datetime


class NoteOut(NoteSummary):
    selection: dict
    sections: list
    sources: list
    notice: str | None
    truncated: bool
    model: str | None


class NotesOptionsOut(BaseModel):
    weeks: list[int]
    lectures: list[int]
    topics: list[str]
    files: list[dict]


class ExportIn(BaseModel):
    kind: Literal["thread", "notes", "paper"]
    ref: int = Field(description="thread id, note id, or course id for a practice paper")
    topic: str | None = Field(default=None, max_length=200, description="paper only: limit to one topic")


# ----- practice (M4) -----

PracticeKind = Literal["mcq", "tf", "short"]


class PracticeGenerateIn(BaseModel):
    week: int | None = Field(default=None, ge=1, le=30)
    topic: str | None = Field(default=None, min_length=2, max_length=200)
    material_id: int | None = None
    n: int = Field(default=5, ge=1, le=20)
    kinds: list[PracticeKind] = Field(default_factory=lambda: ["mcq", "tf", "short"], min_length=1)

    @model_validator(mode="after")
    def exactly_one(self) -> PracticeGenerateIn:
        given = [k for k in ("week", "topic", "material_id") if getattr(self, k) is not None]
        if len(given) != 1:
            raise ValueError("choose exactly one of week, topic or material_id")
        return self


class PracticeItemOut(ORM):
    """The teacher's view of an item: everything, including the answer and the source it came from."""
    id: int
    course_id: int
    kind: str
    question: str
    options: list | None
    answer: str
    explanation: str | None
    topic: str | None
    week: int | None
    source: dict
    status: str
    reject_reason: str | None
    edited: bool
    retired_reason: str | None
    created_at: datetime
    validated_at: datetime | None
    released_at: datetime | None
    model: str | None
    stats: dict | None = None


class PracticeReviewIn(BaseModel):
    action: Literal["approve", "reject", "retire"]
    reason: str | None = Field(default=None, max_length=500)
    question: str | None = Field(default=None, max_length=800)
    options: list[str] | None = Field(default=None, min_length=4, max_length=4)
    answer: str | None = Field(default=None, max_length=1500)
    explanation: str | None = Field(default=None, max_length=800)


class AttemptIn(BaseModel):
    answer: str = Field(default="", max_length=2000)
    self_correct: bool | None = Field(default=None, description="short answers: did your answer match?")


# ----- style (M5) -----

class CalibrationAnswer(BaseModel):
    prompt_id: str = Field(max_length=32)
    answer: str = Field(default="", max_length=4000)


class CalibrationIn(BaseModel):
    answers: list[CalibrationAnswer] = Field(max_length=20)


class StyleRuleIn(BaseModel):
    id: str | None = Field(default=None, max_length=16)
    text: str = Field(max_length=400)
    enabled: bool = True


class StyleUpdateIn(BaseModel):
    rules: list[StyleRuleIn] | None = Field(default=None, max_length=12)
    status: Literal["draft", "active", "off"] | None = None


class ExemplarReviewIn(BaseModel):
    status: Literal["approved", "rejected", "pending"] | None = None
    text: str | None = Field(default=None, max_length=4000)


class StylePreviewIn(BaseModel):
    course_id: int
    question: str = Field(min_length=3, max_length=500)
