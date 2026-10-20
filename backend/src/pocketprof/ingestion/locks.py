"""One provisioning run per course at a time, across threads and processes.

In development there is one process, and a thread lock is enough. In Compose, the API's workers and the
scheduler are separate processes, so on PostgreSQL the lock is also a session advisory lock: held on a
dedicated connection for the length of the run and released by the server if the process dies, so a
crashed run can never leave a course locked.
"""
from __future__ import annotations

import threading

from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from ..db.session import engine

_LOCK_CLASS = 7301  # namespace for PocketProf's advisory locks (first key of the two-key form)
_thread_locks: dict[int, threading.Lock] = {}
_guard = threading.Lock()


def _thread_lock(course_id: int) -> threading.Lock:
    with _guard:
        return _thread_locks.setdefault(course_id, threading.Lock())


def _postgres() -> bool:
    return engine().dialect.name == "postgresql"


class CourseLock:
    def __init__(self, course_id: int):
        self.course_id = course_id
        self._conn: Connection | None = None
        self._held = False

    def acquire(self) -> bool:
        tl = _thread_lock(self.course_id)
        if not tl.acquire(blocking=False):
            return False
        if _postgres():
            conn = engine().connect()
            got = conn.execute(select(func.pg_try_advisory_lock(_LOCK_CLASS, self.course_id))).scalar()
            if not got:
                conn.close()
                tl.release()
                return False
            self._conn = conn
        self._held = True
        return True

    def release(self) -> None:
        if not self._held:
            return
        try:
            if self._conn is not None:
                self._conn.execute(select(func.pg_advisory_unlock(_LOCK_CLASS, self.course_id)))
                self._conn.close()
        finally:
            self._conn, self._held = None, False
            _thread_lock(self.course_id).release()


def is_running(course_id: int) -> bool:
    """True if any thread or process is provisioning the course right now."""
    if _thread_lock(course_id).locked():
        return True
    if not _postgres():
        return False
    with engine().connect() as conn:
        got = conn.execute(select(func.pg_try_advisory_lock(_LOCK_CLASS, course_id))).scalar()
        if got:
            conn.execute(select(func.pg_advisory_unlock(_LOCK_CLASS, course_id)))
        return not got
