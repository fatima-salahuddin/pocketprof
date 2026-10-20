"""Teacher uploads (FR-24): the second material source, alongside Google Classroom.

Uploaded bytes are stored content-addressed under data/uploads/course_<id>/<sha256><ext>. The materials
ledger row (source='upload', source_ref='upload:<filename>') is the manifest: uploading a file with the
same name replaces it, and withdrawing it removes its chunks from the index.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import bad_request
from ..core.security import now_utc
from ..db.models import Course, Material
from . import parsers
from .sources import SourceDocument


def _dir(course_id: int) -> Path:
    d = get_settings().data_dir / "uploads" / f"course_{course_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(course_id: int, file_hash: str, filename: str) -> Path:
    ext = Path(filename).suffix.lower()
    return _dir(course_id) / f"{file_hash}{ext}"


def store(db: Session, course: Course, filename: str, data: bytes, *, topic: str | None = None,
          week: int | None = None) -> Material:
    limit = get_settings().max_upload_mb
    if len(data) > limit * 1024 * 1024:
        raise bad_request(f"{filename} is larger than {limit} MB.")
    if not data:
        raise bad_request(f"{filename} is empty.")
    try:
        mime = parsers.sniff_mime(filename, data)
    except parsers.UnsupportedType as e:
        raise bad_request(f"{filename}: {e}") from None
    digest = hashlib.sha256(data).hexdigest()
    _path(course.id, digest, filename).write_bytes(data)
    ref = f"upload:{filename}"
    row = db.scalar(select(Material).where(Material.course_id == course.id, Material.source_ref == ref))
    if row is None:
        row = Material(course_id=course.id, source="upload", source_ref=ref, kind="Upload", title=filename,
                       mime=mime, status="skipped", note="waiting to be indexed")
        db.add(row)
    row.mime, row.topic = mime, topic or row.topic
    row.post_title = topic or row.post_title or "Uploaded material"
    row.week = week if week is not None else row.week
    row.published_at = row.published_at or now_utc()
    # `version` holds the hash of the stored bytes; provisioning compares it with file_hash.
    row.note = "waiting to be indexed" if row.file_hash != digest else row.note
    row.url = None
    row.version = f"pending:{digest}"
    db.flush()
    return row


def withdraw(row: Material) -> None:
    row.status, row.note, row.version = "withdrawn", "withdrawn by the teacher", "withdrawn"


def documents(db: Session, course: Course) -> list[SourceDocument]:
    rows = db.scalars(select(Material).where(Material.course_id == course.id, Material.source == "upload",
                                             Material.status != "withdrawn")).all()
    docs = []
    for r in rows:
        digest = (r.version or "").removeprefix("pending:") if (r.version or "").startswith("pending:") else r.file_hash
        if not digest:
            continue
        path = _path(course.id, digest, r.title)

        def fetch(path=path, mime=r.mime):
            return path.read_bytes(), mime

        docs.append(SourceDocument(source="upload", source_ref=r.source_ref, kind="Upload", title=r.title,
                                   mime=r.mime, version=f"sha256:{digest}", fetch=fetch if path.exists() else None,
                                   post_title=r.post_title, topic=r.topic, published_at=r.published_at,
                                   skip_reason=None if path.exists() else "stored file is missing; upload it again"))
    return docs
