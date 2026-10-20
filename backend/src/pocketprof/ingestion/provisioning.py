"""ProvisioningJob (M1, FR-1 to FR-4, FR-23, SDS 4.2 and Figure 16).

Brings a course's vector collection in line with its sources and writes a run report. The materials
ledger decides everything:

    for each source document:
        unchanged version            -> nothing (no download, no parse, no embed)
        changed version, same SHA-256 -> record the new version only
        new or changed content       -> parse (content-addressed cache), chunk, tag, embed,
                                        delete the document's old points, upsert the new ones
    ledger rows whose document is gone -> delete their points, mark withdrawn

A course indexed under another arm's index_version (different chunking or embedding) is re-indexed in
full on the next run, so a collection never mixes two configurations.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..arms import RunConfig, load_arm
from ..core.security import now_utc
from ..db import session_scope
from ..db.models import Course, IngestionRun, Material, ParsedCache
from ..retrieval.embedder import get_embedder
from ..retrieval.vector_index import IndexedChunk, collection_name, get_index
from . import calendar, google_classroom, parsers, uploads
from .chunker import chunk_segments
from .locks import CourseLock, is_running  # noqa: F401  (is_running is re-exported for the API)
from .sources import SkipDocument, SourceDocument

log = logging.getLogger("pocketprof.ingestion.provisioning")

PARSE_TIMEOUT_S = 180
_parse_pool = cf.ThreadPoolExecutor(max_workers=2, thread_name_prefix="parse")


@dataclass
class RunReport:
    counts: dict[str, int] = field(default_factory=lambda: {k: 0 for k in
                                   ("parsed", "unchanged", "skipped", "failed", "withdrawn", "ocr")})
    items: list[dict] = field(default_factory=list)

    def add(self, outcome: str, doc_title: str, ref: str, reason: str | None = None, **extra):
        self.counts[outcome] = self.counts.get(outcome, 0) + 1
        item = {"outcome": outcome, "title": doc_title, "ref": ref}
        if reason:
            item["reason"] = reason
        item.update(extra)
        self.items.append(item)


def _embedding_text(row: Material, location: str, text: str) -> str:
    """Context header + chunk text. The header tells the embedder (and BM25) which post, file and place a
    chunk belongs to, which is what students name in questions ("the quiz 3 announcement")."""
    parts = [row.kind, row.post_title or "", row.topic or "", f"{row.title}, {location}"]
    return " | ".join(p for p in parts if p) + "\n" + text


def _parse(file_hash: str, data: bytes, mime: str, db: Session) -> parsers.ParseResult:
    key = parsers.PARSER_VERSION
    cached = db.get(ParsedCache, (file_hash, key))
    if cached is not None:
        return parsers.ParseResult.from_json(cached.segments, cached.ocr_pages)
    fut = _parse_pool.submit(parsers.parse, data, mime)
    try:
        result = fut.result(timeout=PARSE_TIMEOUT_S)
    except cf.TimeoutError:
        raise RuntimeError(f"parsing took longer than {PARSE_TIMEOUT_S} s") from None
    db.merge(ParsedCache(file_hash=file_hash, parser=key, segments=result.to_json(), ocr_pages=result.ocr_pages))
    return result


class ProvisioningJob:
    def __init__(self, course_id: int, trigger: str, *, force: bool = False):
        self.course_id, self.trigger, self.force = course_id, trigger, force

    # ----- entry points -----

    def run(self) -> int | None:
        """Run synchronously. Returns the run ID, or None if a run for this course is already going."""
        lock = CourseLock(self.course_id)
        if not lock.acquire():
            return None
        try:
            return self._run()
        finally:
            lock.release()

    def start(self) -> bool:
        """Run in a background thread. False if a run for this course is already going. The lock is taken
        here, before the thread starts, so two quick calls can never both start a run."""
        lock = CourseLock(self.course_id)
        if not lock.acquire():
            return False

        def target():
            try:
                self._run()
            finally:
                lock.release()

        threading.Thread(target=target, name=f"provision-{self.course_id}", daemon=True).start()
        return True

    # ----- the run -----

    def _run(self) -> int:
        t0 = time.perf_counter()
        with session_scope() as db:
            course = db.get(Course, self.course_id)
            arm = load_arm(course.active_arm)
            run = IngestionRun(course_id=course.id, trigger=self.trigger, arm=arm.arm, config_hash=arm.config_hash,
                               counts=RunReport().counts, items=[])
            db.add(run)
            course.status, course.status_msg = "syncing", "Reading course material..."
            db.flush()
            run_id = run.id
        report = RunReport()
        try:
            with session_scope() as db:
                course = db.get(Course, self.course_id)
                self._provision(db, course, load_arm(course.active_arm), report)
            status, error = "done", None
        except Exception as e:
            log.exception("provisioning failed", extra={"course_id": self.course_id})
            status, error = "failed", str(e)[:1000]
        ms = int((time.perf_counter() - t0) * 1000)
        with session_scope() as db:
            run = db.get(IngestionRun, run_id)
            course = db.get(Course, self.course_id)
            run.status, run.error, run.finished_at, run.duration_ms = status, error, now_utc(), ms
            run.counts, run.items = report.counts, report.items
            if status == "done":
                c = report.counts
                course.status = "ready"
                course.status_msg = (f"{c['parsed']} read, {c['unchanged']} unchanged, {c['skipped']} skipped"
                                     + (f", {c['failed']} failed" if c["failed"] else "")
                                     + (f", {c['withdrawn']} withdrawn" if c["withdrawn"] else ""))
                course.last_synced_at = now_utc()
            else:
                course.status, course.status_msg = "error", (error or "Sync failed")[:500]
        log.info("provisioning run finished", extra={"course_id": self.course_id, "run_id": run_id,
                                                     "status": status, "ms": ms, **report.counts})
        return run_id

    def _provision(self, db: Session, course: Course, arm: RunConfig, report: RunReport) -> None:
        index = get_index()
        name = collection_name(course.id)
        embedder = get_embedder(arm.embedding.model, sparse=arm.retrieval.sparse)
        rebuild = self.force or course.index_version != arm.index_version or not index.exists(name)
        if rebuild:
            index.ensure(name, embedder.dim, arm.retrieval.sparse)
            course.index_version = None  # set again only after a complete run

        docs: list[SourceDocument] = []
        if course.gc_course_id:
            course.status_msg = "Reading posts and files from Google Classroom..."
            db.commit()
            docs += google_classroom.collect(db, course.teacher_id, course.gc_course_id)
        docs += uploads.documents(db, course)

        ledger = {m.source_ref: m for m in db.scalars(select(Material).where(Material.course_id == course.id))}
        # Fill in post dates before computing weeks, so the term start can default to the first post.
        cal = calendar.load(db, course)
        if cal.term_start is None:
            dates = [d.published_at for d in docs if d.published_at]
            if dates:
                cal.term_start = calendar.monday(min(dates).date())
        seen: set[str] = set()

        for i, doc in enumerate(docs, start=1):
            seen.add(doc.source_ref)
            row = ledger.get(doc.source_ref)
            if row is None:
                row = Material(course_id=course.id, source=doc.source, source_ref=doc.source_ref, kind=doc.kind,
                               title=doc.title, mime=doc.mime, status="skipped")
                db.add(row)
                db.flush()
                ledger[doc.source_ref] = row
            before = (row.week, row.lecture_no, row.post_title, row.topic)
            self._sync_metadata(row, doc, cal)
            if row.n_chunks and row.status == "indexed" and before != (row.week, row.lecture_no, row.post_title,
                                                                        row.topic):
                # A calendar change or a renamed post: re-tag the existing points without re-embedding.
                index.set_material_payload(name, row.id, {"week": row.week, "lecture_no": row.lecture_no,
                                                          "post_title": row.post_title, "topic": row.topic})
            if i % 5 == 0:
                course.status_msg = f"Reading material {i} of {len(docs)}..."
                db.commit()
            try:
                self._one(db, course, arm, row, doc, report, rebuild, embedder, index, name)
            except Exception as e:  # one bad document must not fail the course
                log.warning("document failed", extra={"course_id": course.id, "material_id": row.id,
                                                      "error": type(e).__name__})
                row.status, row.note = "failed", f"{type(e).__name__}: {e}"[:500]
                report.add("failed", doc.title, doc.source_ref, row.note)
            db.commit()

        for ref, row in ledger.items():
            if ref in seen or row.status == "withdrawn":
                continue
            if row.source == "upload":
                continue  # uploads leave only when the teacher withdraws them
            index.delete_material(name, row.id)
            row.status, row.note, row.n_chunks, row.version = "withdrawn", "no longer on Google Classroom", 0, None
            report.add("withdrawn", row.title, ref, "removed from the source")
        for row in ledger.values():  # uploads withdrawn through the API
            if row.source == "upload" and row.status == "withdrawn" and row.n_chunks:
                index.delete_material(name, row.id)
                row.n_chunks = 0
                report.add("withdrawn", row.title, row.source_ref, "withdrawn by the teacher")

        course.n_chunks = index.count(name)
        course.index_version = arm.index_version

    def _sync_metadata(self, row: Material, doc: SourceDocument, cal: calendar.CourseCalendar) -> None:
        row.kind, row.title, row.mime = doc.kind, doc.title, doc.mime or row.mime
        row.post_title, row.topic, row.url, row.post_url = doc.post_title, doc.topic, doc.url, doc.post_url
        if doc.published_at:
            row.published_at = doc.published_at
        if not (row.source == "upload" and row.week):  # an upload's week may be set by the teacher
            row.week = cal.week_of(row.published_at)
        row.lecture_no = cal.lecture_of(row.published_at)

    def _one(self, db, course, arm, row: Material, doc: SourceDocument, report: RunReport, rebuild: bool,
             embedder, index, name) -> None:
        if doc.skip_reason or doc.fetch is None:
            if row.n_chunks:
                index.delete_material(name, row.id)
            row.status, row.note, row.n_chunks, row.version = "skipped", doc.skip_reason or doc.note, 0, doc.version
            report.add("skipped", doc.title, doc.source_ref, row.note)
            return
        if not rebuild and row.version == doc.version and row.status in ("indexed", "skipped"):
            report.add("unchanged", doc.title, doc.source_ref)
            return
        try:
            data, mime = doc.fetch()
        except SkipDocument as e:
            row.status, row.note, row.version = "skipped", str(e), doc.version
            report.add("skipped", doc.title, doc.source_ref, str(e))
            return
        digest = hashlib.sha256(data).hexdigest()
        if not rebuild and row.file_hash == digest and row.status == "indexed":
            row.version = doc.version  # touched but identical (e.g. re-saved): nothing to re-embed
            report.add("unchanged", doc.title, doc.source_ref, "same content")
            return

        parsed = _parse(digest, data, mime, db)
        chunks = chunk_segments(parsed.segments, arm.chunking)
        index.delete_material(name, row.id)  # drop the old version's points
        if not chunks:
            reason = "no extractable text" + (f" ({parsed.needs_ocr_pages} image-only pages; OCR not installed)"
                                              if parsed.needs_ocr_pages else " (empty or scanned?)")
            row.status, row.note, row.n_chunks = "skipped", reason, 0
            row.version, row.file_hash = doc.version, digest
            report.add("skipped", doc.title, doc.source_ref, reason)
            return

        texts = [_embedding_text(row, c.location, c.text) for c in chunks]
        dense = embedder.embed_passages(texts)
        sparse = embedder.sparse_passages(texts) if arm.retrieval.sparse else []
        day = row.published_at.date().isoformat() if row.published_at else None
        points = []
        for j, c in enumerate(chunks):
            chunk_id = f"c{course.id}:w{row.week or 0}:m{row.id}:{c.seq}"
            points.append(IndexedChunk(chunk_id, dense[j].tolist(), sparse[j] if sparse else None, {
                "course_id": course.id, "teacher_id": course.teacher_id, "material_id": row.id,
                "kind": row.kind, "post_title": row.post_title, "topic": row.topic, "file": row.title,
                "url": row.url, "post_url": row.post_url, "location": c.location, "page": c.page,
                "slide": c.slide, "heading": c.heading, "week": row.week, "lecture_no": row.lecture_no,
                "date": day, "file_hash": digest, "text": c.text, "ocr_flag": c.ocr,
            }))
        index.upsert(name, points)
        row.status, row.n_chunks, row.version, row.file_hash = "indexed", len(chunks), doc.version, digest
        row.ocr_pages = parsed.ocr_pages
        notes = [n for n in (doc.note,
                             f"{parsed.ocr_pages} pages read with OCR" if parsed.ocr_pages else None,
                             f"{parsed.needs_ocr_pages} image-only pages not read (OCR not installed)"
                             if parsed.needs_ocr_pages else None) if n]
        row.note = "; ".join(notes) or None
        report.add("ocr" if parsed.ocr_pages else "parsed", doc.title, doc.source_ref, row.note,
                   chunks=len(chunks))
        if parsed.ocr_pages:
            report.counts["parsed"] += 1
