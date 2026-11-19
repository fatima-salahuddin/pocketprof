"""MasteryTracker (M4, FR-18; SDS 4.5; SRS UC-07): the student's practice session.

    view:    due items (oldest due date first), then unseen released items from the student's
             lowest-mastery topics; if nothing is due, optional items from delivered topics (UC-07 1a)
    attempt: mark -> move the Leitner box -> schedule the next due date -> store the attempt under the
             pseudonym -> recompute the topic's mastery from the attempts

Every row is keyed by student_ref, the per-course pseudonym (C-8). The student re-derives it on each
request; teachers never see per-student practice data, only cohort aggregates above the minimum size.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..arms import RunConfig, load_arm
from ..core.errors import bad_request, conflict
from ..core.security import now_utc, student_ref
from ..db.models import Course, Mastery, PracticeAttempt, PracticeItem, PracticeSchedule, User
from .lifecycle import refresh, retire_stale
from .spacing import SpacingPolicy, aware, mastery_estimate

GENERAL = "General"   # topic name for material without a topic tag


def topic_of(item: PracticeItem) -> str:
    return item.topic or GENERAL


def public_item(item: PracticeItem, schedule: PracticeSchedule | None = None) -> dict:
    """What a student sees before answering: never the answer, the explanation or the source text."""
    return {"id": item.id, "kind": item.kind, "question": item.question, "options": item.options,
            "topic": topic_of(item), "week": item.week,
            "box": schedule.box if schedule else None,
            "next_due": aware(schedule.next_due) if schedule else None}


def is_correct(item: PracticeItem, answer: str, self_correct: bool | None) -> tuple[bool, bool]:
    """(correct, self_marked). Short answers are marked by the student against the model answer."""
    if item.kind == "short":
        if self_correct is None:
            raise bad_request("Short-answer questions are self-marked: say whether your answer matched.")
        return bool(self_correct), True
    a = (answer or "").strip()
    if item.kind == "mcq":
        a = a[:1].upper()
        if not a or a not in "ABCD":
            raise bad_request("Answer with the letter of an option, A to D.")
        return a == item.answer, False
    a = a.lower()
    if a not in ("true", "false"):
        raise bad_request("Answer true or false.")
    return a == item.answer, False


class MasteryTracker:
    def __init__(self, db: Session, course: Course, user: User, *, arm: RunConfig | None = None):
        self.db, self.course, self.user = db, course, user
        self.arm = arm or load_arm(course.active_arm)
        self.policy = SpacingPolicy(self.arm.practice)
        self.ref = student_ref(user.id, course.id)

    # ----- mastery -----

    def mastery(self) -> list[dict]:
        rows = {m.topic: m for m in self.db.scalars(select(Mastery).where(
            Mastery.course_id == self.course.id, Mastery.student_ref == self.ref))}
        released_topics = {topic_of(i) for i in self.db.scalars(select(PracticeItem).where(
            PracticeItem.course_id == self.course.id, PracticeItem.status == "released"))}
        out = []
        for t in sorted(released_topics | set(rows), key=str.lower):
            m = rows.get(t)
            out.append({"topic": t, "estimate": m.estimate if m else None, "attempts": m.n_attempts if m else 0})
        return out

    def _recompute(self, topic: str) -> Mastery:
        cfg = self.arm.practice
        q = (select(PracticeAttempt.correct).where(
            PracticeAttempt.course_id == self.course.id, PracticeAttempt.student_ref == self.ref,
            func.coalesce(PracticeAttempt.topic, GENERAL) == topic)
            .order_by(PracticeAttempt.id.desc()).limit(cfg.mastery_window))
        outcomes = list(self.db.scalars(q))
        estimate = mastery_estimate(outcomes, cfg.mastery_window, cfg.mastery_decay) or 0.0
        row = self.db.get(Mastery, (self.course.id, self.ref, topic))
        total = self.db.scalar(select(func.count()).select_from(PracticeAttempt).where(
            PracticeAttempt.course_id == self.course.id, PracticeAttempt.student_ref == self.ref,
            func.coalesce(PracticeAttempt.topic, GENERAL) == topic)) or 0
        if row is None:
            row = Mastery(course_id=self.course.id, student_ref=self.ref, topic=topic, estimate=estimate,
                          n_attempts=total)
            self.db.add(row)
        else:
            row.estimate, row.n_attempts = estimate, total
        return row

    # ----- the session -----

    def view(self, now: datetime | None = None) -> dict:
        now = now or now_utc()
        refresh(self.db, self.course)
        released = list(self.db.scalars(select(PracticeItem).where(
            PracticeItem.course_id == self.course.id, PracticeItem.status == "released").order_by(PracticeItem.id)))
        sched = {s.item_id: s for s in self.db.scalars(select(PracticeSchedule).where(
            PracticeSchedule.course_id == self.course.id, PracticeSchedule.student_ref == self.ref))}
        mastery = self.mastery()
        level = {m["topic"]: (m["estimate"] if m["estimate"] is not None else 0.0) for m in mastery}

        due = sorted((i for i in released if i.id in sched and aware(sched[i.id].next_due) <= now),
                     key=lambda i: aware(sched[i.id].next_due))
        fresh = sorted((i for i in released if i.id not in sched), key=lambda i: (level.get(topic_of(i), 0.0), i.id))
        size = self.arm.practice.session_size
        session = [public_item(i, sched.get(i.id)) | {"reason": "due"} for i in due[:size]]
        session += [public_item(i) | {"reason": "new"} for i in fresh[:max(0, size - len(session))]]
        optional = []
        if not session:  # UC-07 1a: nothing is due, so offer items from delivered topics anyway
            later = sorted((i for i in released if i.id in sched), key=lambda i: aware(sched[i.id].next_due))
            optional = [public_item(i, sched[i.id]) | {"reason": "optional"} for i in later[:size]]
        return {"session": session, "optional": optional, "mastery": mastery,
                "counts": {"due": len(due), "new": len(fresh), "released": len(released)},
                "intervals_days": self.policy.intervals}

    def released_item(self, item: PracticeItem) -> PracticeItem:
        retire_stale(self.db, self.course, [item])
        if item.status == "retired":
            raise conflict("This question was retired because its source material changed, so it is skipped.")
        if item.status != "released":
            from ..core.errors import not_found
            raise not_found("Practice question")
        return item

    def attempt(self, item: PracticeItem, answer: str, self_correct: bool | None,
                now: datetime | None = None) -> dict:
        now = now or now_utc()
        self.released_item(item)
        correct, self_marked = is_correct(item, answer, self_correct)
        s = self.db.get(PracticeSchedule, (item.id, self.ref))
        if s is None:
            s = PracticeSchedule(item_id=item.id, student_ref=self.ref, course_id=self.course.id, box=1,
                                 next_due=now, n_attempts=0)
            self.db.add(s)
        before = s.box
        s.box = self.policy.next_box(before, correct)
        s.next_due, s.last_attempt_at, s.n_attempts = self.policy.next_due(now, s.box), now, (s.n_attempts or 0) + 1
        self.db.add(PracticeAttempt(item_id=item.id, course_id=self.course.id, student_ref=self.ref,
                                    topic=item.topic, week=item.week, answer=(answer or "")[:2000], correct=correct,
                                    self_marked=self_marked, box_before=before, box_after=s.box,
                                    next_due=s.next_due, ts=now))
        self.db.flush()
        m = self._recompute(topic_of(item))
        self.db.flush()
        return {"correct": correct, "self_marked": self_marked, "correct_answer": item.answer,
                "options": item.options, "explanation": item.explanation, "source": item.source,
                "box": s.box, "boxes": self.policy.boxes, "next_due": s.next_due,
                "interval_days": self.policy.intervals[s.box - 1],
                "mastery": {"topic": m.topic, "estimate": m.estimate, "attempts": m.n_attempts}}
