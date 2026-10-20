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
