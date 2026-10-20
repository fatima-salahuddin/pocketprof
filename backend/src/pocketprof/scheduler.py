"""Scheduler (SDS Table 8): keeps Google-linked courses in sync with Classroom.

Every SYNC_SECONDS each linked course gets a provisioning run. The materials ledger makes an unchanged
class cheap (a few list calls, no downloads, no embedding), so new posts appear within about a minute
without the teacher pressing anything.

In development the scheduler runs inside the API process (RUN_SCHEDULER=true). In Compose it is its own
service:  python -m pocketprof.scheduler
"""
from __future__ import annotations

import logging
import signal
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from .config import get_settings
from .db import session_scope
from .db.models import Course
from .ingestion.provisioning import ProvisioningJob, is_running

log = logging.getLogger("pocketprof.scheduler")


def sync_linked_courses() -> None:
    with session_scope() as db:
        ids = db.scalars(select(Course.id).where(Course.gc_course_id.is_not(None),
                                                 Course.status.in_(("ready", "error", "new")))).all()
    for cid in ids:
        if not is_running(cid):
            try:
                ProvisioningJob(cid, "scheduler").run()
            except Exception:
                log.exception("scheduled sync failed", extra={"course_id": cid})


def build() -> BackgroundScheduler | None:
    s = get_settings()
    if s.sync_seconds <= 0:
        return None
    sched = BackgroundScheduler(daemon=True, job_defaults={"coalesce": True, "max_instances": 1})
    sched.add_job(sync_linked_courses, "interval", seconds=s.sync_seconds, id="classroom-sync")
    return sched


def main() -> None:
    from .core.logging import setup
    from .core.netfix import prefer_ipv4

    s = get_settings()
    setup(json_logs=not s.is_dev)
    if s.force_ipv4:
        prefer_ipv4()
    sched = build()
    if sched is None:
        log.info("automatic sync is off (SYNC_SECONDS=0)")
        return
    sched.start()
    log.info("scheduler started", extra={"sync_seconds": s.sync_seconds})
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    signal.signal(signal.SIGINT, lambda *_: done.set())
    done.wait()
    sched.shutdown(wait=False)


if __name__ == "__main__":
    main()
