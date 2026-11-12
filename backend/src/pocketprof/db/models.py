"""Relational schema (SDS 6.1, 6.2, Figure 6). `courses` is the hub of the course_id relationships.

PostgreSQL holds every structural fact; Qdrant holds only vectors and the payload needed to filter them.
Analytics tables never store a user_id: they store student_ref, a per-course HMAC pseudonym.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

ROLES = ("student", "teacher", "admin")
MATERIAL_STATUSES = ("indexed", "skipped", "failed", "withdrawn")


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------- identity (M8) ----------

class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("role IN ('student','teacher','admin')", name="ck_users_role"),)


class RefreshToken(TimestampMixin, Base):
    """Rotating refresh tokens. Only the SHA-256 of a token is stored; reuse of a rotated token revokes
    the whole family (stolen-token detection)."""
    __tablename__ = "refresh_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family: Mapped[str] = mapped_column(String(32), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GoogleAccount(TimestampMixin, Base):
    """A teacher's Google connection. OAuth credentials are encrypted at rest."""
    __tablename__ = "google_accounts"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    google_email: Mapped[str] = mapped_column(String(255))
    google_name: Mapped[str | None] = mapped_column(String(255))
    credentials_enc: Mapped[str] = mapped_column(Text)
    scopes: Mapped[list[Any]] = mapped_column(JSON, default=list)


# ---------- courses ----------

class Course(TimestampMixin, Base):
    __tablename__ = "courses"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32))                    # e.g. CS-2001
    title: Mapped[str] = mapped_column(String(200))
    section: Mapped[str | None] = mapped_column(String(64))
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    collection_name: Mapped[str] = mapped_column(String(64), unique=True)
    join_code: Mapped[str] = mapped_column(String(32), unique=True)  # Classroom enrolment code or generated
    term_start: Mapped[date | None] = mapped_column(Date)             # week 1 starts on this Monday
    # Google Classroom link (null for upload-only courses)
    gc_course_id: Mapped[str | None] = mapped_column(String(64), index=True)
    gc_link: Mapped[str | None] = mapped_column(String(500))
    # Index state
    active_arm: Mapped[str] = mapped_column(String(64), default="shipped-v1")
    index_version: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="new")   # new | syncing | ready | error
    status_msg: Mapped[str | None] = mapped_column(String(500))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    n_chunks: Mapped[int] = mapped_column(Integer, default=0)

    teacher: Mapped[User] = relationship()
    materials: Mapped[list[Material]] = relationship(back_populates="course", cascade="all, delete-orphan")


class Enrollment(TimestampMixin, Base):
    __tablename__ = "enrollments"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)


class CalendarDay(Base):
    """Optional teaching calendar (calendar.csv). Without it, week numbers come from post dates."""
    __tablename__ = "calendar"
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    week: Mapped[int] = mapped_column(Integer)
    lecture_no: Mapped[int | None] = mapped_column(Integer)
    topics: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (Index("ix_calendar_course_week", "course_id", "week"),)


class CLO(Base):
    __tablename__ = "clos"
    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(16))
    description: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("course_id", "code"),)


# ---------- ingestion (M1) ----------

class Material(Base):
    """Materials ledger: one row per source document (a Drive file, a post's text, an uploaded file).
    `version` is the source's own change marker (Drive md5/modifiedTime, Classroom updateTime) and lets an
    unchanged document skip even the download; `file_hash` is the SHA-256 of the content actually parsed."""
    __tablename__ = "materials"
    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16))          # google | upload
    source_ref: Mapped[str] = mapped_column(String(255))     # stable key within the course
    kind: Mapped[str] = mapped_column(String(24))            # Material | Assignment | Announcement | Upload
    post_title: Mapped[str | None] = mapped_column(String(300))
    topic: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str | None] = mapped_column(String(1000))
    post_url: Mapped[str | None] = mapped_column(String(1000))
    mime: Mapped[str] = mapped_column(String(160), default="")
    version: Mapped[str | None] = mapped_column(String(160))
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    week: Mapped[int | None] = mapped_column(Integer)
    lecture_no: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="indexed")
    note: Mapped[str | None] = mapped_column(String(500))
    n_chunks: Mapped[int] = mapped_column(Integer, default=0)
    ocr_pages: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    course: Mapped[Course] = relationship(back_populates="materials")
    __table_args__ = (
        UniqueConstraint("course_id", "source_ref", name="uq_materials_course_ref"),
        CheckConstraint("status IN ('indexed','skipped','failed','withdrawn')", name="ck_materials_status"),
        CheckConstraint("file_hash IS NULL OR length(file_hash) = 64", name="ck_materials_hash"),
    )


class ParsedCache(Base):
    """Content-addressed parse cache: the same bytes (same SHA-256) are never parsed twice, even when a
    file is re-posted, copied to another course or re-uploaded."""
    __tablename__ = "parsed_cache"
    file_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    parser: Mapped[str] = mapped_column(String(32), primary_key=True)
    segments: Mapped[list[Any]] = mapped_column(JSON)
    ocr_pages: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IngestionRun(Base):
    """Run report (FR-4): what one provisioning run did, file by file."""
    __tablename__ = "ingestion_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(16))         # manual | scheduler | upload | admin | link
    arm: Mapped[str] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | done | failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    items: Mapped[list[Any]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)


# ---------- conversations (M2, M7) ----------

class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    messages: Mapped[list[Message]] = relationship(back_populates="conversation", cascade="all, delete-orphan",
                                                   order_by="Message.id")
    __table_args__ = (Index("ix_conversations_user_course", "user_id", "course_id"),)


class Message(TimestampMixin, Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(12))            # user | assistant
    content: Mapped[str] = mapped_column(Text)
    mode: Mapped[str | None] = mapped_column(String(12))     # direct | scaffolded
    scope: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    citations: Mapped[list[Any] | None] = mapped_column(JSON)  # cited sources with chunk_id and location
    outcome: Mapped[str | None] = mapped_column(String(16))  # answered | refused | fallback | error
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    model: Mapped[str | None] = mapped_column(String(120))
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ---------- notes (M3) ----------

class Note(TimestampMixin, Base):
    """Generated study notes (M3, FR-8). Content is stored so notes can be reopened and exported with
    their citations intact, even after the material changes."""
    __tablename__ = "notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    selection: Mapped[dict[str, Any]] = mapped_column(JSON)     # {"week": 5} | {"topic": ...} | {"material_id": 7}
    scope_label: Mapped[str] = mapped_column(String(200))       # "Week 5", "Topic: Hashing", "File: L5.pptx"
    title: Mapped[str] = mapped_column(String(200))
    sections: Mapped[list[Any]] = mapped_column(JSON)           # [{"heading", "body", "citations": [n...]}]
    sources: Mapped[list[Any]] = mapped_column(JSON)            # citation records, numbered 1..k
    outcome: Mapped[str] = mapped_column(String(16))            # generated | refused | fallback
    notice: Mapped[str | None] = mapped_column(String(500))
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str | None] = mapped_column(String(160))
    config_hash: Mapped[str] = mapped_column(String(64))        # which arm produced it (provenance)
    prompt_version: Mapped[str] = mapped_column(String(32))
    __table_args__ = (CheckConstraint("outcome IN ('generated','refused','fallback')", name="ck_notes_outcome"),
                      Index("ix_notes_user_course", "user_id", "course_id"))


# ---------- practice (M4) ----------

ITEM_STATES = ("draft", "validated", "released", "rejected", "retired")
ITEM_KINDS = ("mcq", "tf", "short")


class PracticeItem(TimestampMixin, Base):
    """A generated practice item and its lifecycle (SDS 8.4, Figure 14):
        DRAFT -> VALIDATED (teacher approves) -> RELEASED (its topic has been delivered)
        DRAFT -> REJECTED (teacher, with a reason);  any -> RETIRED (source file changed or withdrawn)
    Students only ever see RELEASED items (FR-17). `file_hash` pins the version of the material the item
    was generated from, so a replaced file retires its items."""
    __tablename__ = "practice_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id", ondelete="SET NULL"))
    chunk_id: Mapped[str] = mapped_column(String(120))
    file_hash: Mapped[str | None] = mapped_column(String(64))
    topic: Mapped[str | None] = mapped_column(String(200))
    week: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(8))                 # mcq | tf | short
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list[Any] | None] = mapped_column(JSON)      # mcq: four strings, shown as A-D
    answer: Mapped[str] = mapped_column(Text)                    # mcq: A-D; tf: true|false; short: model answer
    explanation: Mapped[str | None] = mapped_column(Text)
    source: Mapped[dict[str, Any]] = mapped_column(JSON)         # citation record of the chunk (FR-17)
    status: Mapped[str] = mapped_column(String(12), default="draft")
    reject_reason: Mapped[str | None] = mapped_column(String(500))
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    validated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_reason: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(160))
    config_hash: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    __table_args__ = (
        CheckConstraint("status IN ('draft','validated','released','rejected','retired')", name="ck_items_status"),
        CheckConstraint("kind IN ('mcq','tf','short')", name="ck_items_kind"),
        CheckConstraint("status NOT IN ('validated','released') OR validated_by IS NOT NULL",
                        name="ck_items_validated_by"),
        Index("ix_practice_items_course_status_topic", "course_id", "status", "topic"),
    )


class PracticeSchedule(Base):
    """One student's Leitner state for one released item (SDS 4.5): box 1-5, next due date. Keyed by the
    per-course pseudonym, never the user ID (C-8)."""
    __tablename__ = "practice_schedule"
    item_id: Mapped[int] = mapped_column(ForeignKey("practice_items.id", ondelete="CASCADE"), primary_key=True)
    student_ref: Mapped[str] = mapped_column(String(16), primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    box: Mapped[int] = mapped_column(Integer, default=1)
    next_due: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    n_attempts: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (CheckConstraint("box BETWEEN 1 AND 5", name="ck_schedule_box"),
                      Index("ix_practice_schedule_ref_due", "student_ref", "next_due"))


class PracticeAttempt(Base):
    """Every attempt, under the pseudonym. The source of truth for mastery and for the cohort signal."""
    __tablename__ = "practice_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("practice_items.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    student_ref: Mapped[str] = mapped_column(String(16))
    topic: Mapped[str | None] = mapped_column(String(200))
    week: Mapped[int | None] = mapped_column(Integer)
    answer: Mapped[str] = mapped_column(Text)
    correct: Mapped[bool] = mapped_column(Boolean)
    self_marked: Mapped[bool] = mapped_column(Boolean, default=False)   # short answers: the student marks
    box_before: Mapped[int] = mapped_column(Integer)
    box_after: Mapped[int] = mapped_column(Integer)
    next_due: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_practice_attempts_course_ref_topic", "course_id", "student_ref", "topic"),)


class Mastery(Base):
    """Recency-weighted accuracy per student and topic (SDS 4.5). A cache recomputed from practice_attempts
    on every attempt; visible to the student only (C-8)."""
    __tablename__ = "mastery"
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)
    student_ref: Mapped[str] = mapped_column(String(16), primary_key=True)
    topic: Mapped[str] = mapped_column(String(200), primary_key=True)
    estimate: Mapped[float] = mapped_column(Float)
    n_attempts: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    __table_args__ = (CheckConstraint("estimate >= 0 AND estimate <= 1", name="ck_mastery_estimate"),)


# ---------- style (M5) ----------

class StyleProfile(TimestampMixin, Base):
    """A teacher's explicit style rules (FR-11). Rules are plain sentences the teacher can read and edit;
    nothing is used in generation until the teacher activates the profile."""
    __tablename__ = "style_profiles"
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(12), default="draft")     # draft | active | off
    rules: Mapped[list[Any]] = mapped_column(JSON, default=list)         # [{"id", "text", "origin", "enabled"}]
    calibration: Mapped[list[Any]] = mapped_column(JSON, default=list)   # [{"prompt_id", "prompt", "answer"}]
    measures: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # deterministic features behind the rules
    model: Mapped[str | None] = mapped_column(String(160))
    config_hash: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    __table_args__ = (CheckConstraint("status IN ('draft','active','off')", name="ck_style_status"),)


class StyleExemplar(TimestampMixin, Base):
    """One example of how the teacher explains: a calibration answer, or a passage mined from their own
    material. Only approved exemplars are indexed in the teacher's style collection (SDS Table 4)."""
    __tablename__ = "style_exemplars"
    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    origin: Mapped[str] = mapped_column(String(12))                     # calibration | material
    prompt: Mapped[str | None] = mapped_column(Text)                    # the calibration prompt it answers
    text: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(String(300))          # "file, location" for mined passages
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | approved | rejected
    __table_args__ = (CheckConstraint("status IN ('pending','approved','rejected')", name="ck_exemplar_status"),
                      CheckConstraint("origin IN ('calibration','material')", name="ck_exemplar_origin"))


class QueryLog(Base):
    """Pseudonymous usage log feeding the cohort signal (FR-19). No user_id column by design (C-8)."""
    __tablename__ = "query_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    student_ref: Mapped[str] = mapped_column(String(16))
    question: Mapped[str] = mapped_column(Text)
    week: Mapped[int | None] = mapped_column(Integer)
    topic: Mapped[str | None] = mapped_column(String(200))
    outcome: Mapped[str] = mapped_column(String(16))
    top_score: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_query_log_course_week_topic", "course_id", "week", "topic"),)


# ---------- evaluation (M9) and audit ----------

class ExperimentRun(Base):
    __tablename__ = "experiment_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    experiment: Mapped[str] = mapped_column(String(32))
    arm: Mapped[str] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64))
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    query_set: Mapped[str] = mapped_column(String(64))
    query_set_version: Mapped[str] = mapped_column(String(16))
    split: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(160))
    prompt_version: Mapped[str] = mapped_column(String(32))
    index_version: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    results: Mapped[list[Any]] = mapped_column(JSON, default=list)
    __table_args__ = (Index("ix_experiment_runs_exp_arm", "experiment", "arm"),
                      CheckConstraint("length(config_hash) = 64", name="ck_experiment_runs_hash"))


class AuditLog(Base):
    """Staff actions (SDS Table 9: repudiation): uploads, provisioning, configuration and role changes."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(48))
    target: Mapped[str | None] = mapped_column(String(120))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
