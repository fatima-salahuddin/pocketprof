"""Development seed: an admin, a teacher, a student and the offline sample course, indexed.

    python -m pocketprof.seed            # idempotent; prints the accounts it created
    python -m pocketprof.seed --reset    # drop the sample course and rebuild it

Account passwords come from SEED_PASSWORD (default for development only: see .env.example).
"""
from __future__ import annotations

import argparse
import os

from sqlalchemy import select

from . import sample_course
from .access import accounts
from .config import get_settings
from .core.logging import setup
from .db import session_scope
from .db.migrate import upgrade
from .db.models import Course, Enrollment, Material, User
from .ingestion import uploads
from .ingestion.provisioning import ProvisioningJob
from .retrieval.vector_index import collection_name, get_index

SAMPLE_JOIN_CODE = "sample-ds"
ACCOUNTS = [("admin@pocketprof.local", "Admin", "admin"),
            ("teacher@pocketprof.local", "Dr. Sana Ahmed", "teacher"),
            ("student@pocketprof.local", "Ali Raza", "student")]


def _user(db, email: str, name: str, role: str, password: str) -> tuple[User, bool]:
    u = db.scalar(select(User).where(User.email == email))
    if u is not None:
        return u, False
    return accounts.create_user(db, email=email, name=name, role=role, password=password), True


def seed(reset: bool = False) -> int:
    password = os.getenv("SEED_PASSWORD", "pocketprof-dev")
    upgrade()
    with session_scope() as db:
        users = {}
        for email, name, role in ACCOUNTS:
            u, created = _user(db, email, name, role, password)
            users[role] = u
            if created:
                print(f"created {role:<8} {email}")
        course = db.scalar(select(Course).where(Course.join_code == SAMPLE_JOIN_CODE))
        if course is not None and reset:
            get_index().drop(course.collection_name)
            db.delete(course)
            db.flush()
            course = None
        if course is None:
            course = Course(code="CS-2001", title="Data Structures (sample class)", section="BCS-3A",
                            teacher_id=users["teacher"].id, collection_name="pending", join_code=SAMPLE_JOIN_CODE,
                            term_start=sample_course.term_start_for(), active_arm=get_settings().active_arm)
            db.add(course)
            db.flush()
            course.collection_name = collection_name(course.id)
            for doc in sample_course.documents():
                row = uploads.store(db, course, doc["name"], doc["data"], topic=doc["topic"], week=doc["week"])
                row.published_at = sample_course.published_at(course.term_start, doc["week"], doc["day"])
            print(f"created sample course {course.code} (join code {SAMPLE_JOIN_CODE})")
        if db.get(Enrollment, (users["student"].id, course.id)) is None:
            db.add(Enrollment(user_id=users["student"].id, course_id=course.id))
        course_id = course.id
        pending = db.scalar(select(Material).where(Material.course_id == course_id,
                                                   Material.version.like("pending:%"))) is not None
    if pending or reset:
        run_id = ProvisioningJob(course_id, "admin").run()
        print(f"indexed sample course (run {run_id})")
    return course_id


def main() -> None:
    setup(json_logs=False)
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    cid = seed(reset=args.reset)
    with session_scope() as db:
        c = db.get(Course, cid)
        print(f"course {cid}: status={c.status} chunks={c.n_chunks} ({c.status_msg})")


if __name__ == "__main__":
    main()
