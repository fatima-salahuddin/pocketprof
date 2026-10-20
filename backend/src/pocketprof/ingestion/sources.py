"""Source documents: what a connector hands to provisioning, independent of where it came from."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime


class SkipDocument(Exception):
    """Raised by a fetch() for a document deliberately not read (type, size)."""


@dataclass
class SourceDocument:
    source: str                       # google | upload
    source_ref: str                   # stable key within the course, e.g. drive:<file_id>, post:<post_id>
    kind: str                         # Material | Assignment | Announcement | Upload
    title: str
    mime: str                         # MIME type of the original document
    version: str                      # changes whenever the source document changes
    fetch: Callable[[], tuple[bytes, str]] | None  # -> (bytes, MIME type of those bytes)
    post_title: str | None = None
    topic: str | None = None
    url: str | None = None
    post_url: str | None = None
    published_at: datetime | None = None
    note: str | None = None           # shown in the run report, e.g. "title only; video not transcribed"
    skip_reason: str | None = None    # set when the document is listed but never read
