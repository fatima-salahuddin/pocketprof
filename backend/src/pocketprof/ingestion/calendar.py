"""Course calendar: maps dates to teaching weeks (MetadataTagger input, SDS 4.2; QueryScoper, 4.4).

Two sources, in priority order:
1. an uploaded calendar.csv (date, week, lecture_no, topics), when the teacher provides one;
2. the course's term start: week 1 is the week (Monday to Sunday) containing term_start.
If neither is set, term_start defaults to the Monday of the earliest material's post date.

A teacher's own label overrides the date (see declared_week), whenever the material was posted:
- a Classroom topic named "Week 5 - Normalisation" files its posts under week 5 (the week may appear anywhere
  in a topic name: a topic is an organising label);
- otherwise a material's, assignment's or upload's post title or file name that STARTS with it ("WEEK 3",
  "Week3_notes.pdf"). Announcements are never read this way, and a title only counts when it starts with the
  week: "Mid 2 syllabus: week 5 and week 6" mentions weeks without belonging to them.
"Lecture 9 - Heaps" sets the lecture number by the same rules.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from dateutil import parser as dateparser
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..core.errors import bad_request
from ..db.models import CalendarDay, Course, Material

MAX_WEEK = 30
MAX_LECTURE = 200
# "Week 5", "week-05", "Wk 5:", "WEEK#5", "Week3_notes", "Week 5 & 6" (the first week wins).
# Not "weekly" or "5 weeks". (?!\d) rather than \b, so "Week3_notes" (an underscore is a word character) counts.
_WEEK = r"(?:week|wk)[\s_]*[-#:.]?[\s_]*0*(\d{1,2})(?!\d)"
_LECTURE = r"(?:lecture|lec)[\s_]*[-#:.]?[\s_]*0*(\d{1,3})(?!\d)"
_LABEL_WEEK, _START_WEEK = re.compile(r"\b" + _WEEK, re.I), re.compile(r"^\W*" + _WEEK, re.I)
_LABEL_LECTURE, _START_LECTURE = re.compile(r"\b" + _LECTURE, re.I), re.compile(r"^\W*" + _LECTURE, re.I)
TITLED_KINDS = ("Material", "Assignment", "Upload")   # announcements mention weeks; they do not belong to them


def _number(pattern: re.Pattern, label: str | None, limit: int) -> int | None:
    m = pattern.search(label or "")
    n = int(m.group(1)) if m else 0
    return n if 1 <= n <= limit else None


def week_from_label(label: str | None, *, at_start: bool = False) -> int | None:
    """The teaching week a label declares: anywhere in it, or only at its start (titles, file names)."""
    return _number(_START_WEEK if at_start else _LABEL_WEEK, label, MAX_WEEK)


def lecture_from_label(label: str | None, *, at_start: bool = False) -> int | None:
    return _number(_START_LECTURE if at_start else _LABEL_LECTURE, label, MAX_LECTURE)


def declared_week(kind: str, topic: str | None, post_title: str | None, title: str | None) -> int | None:
    """The week the teacher's own labels give a material, or None (then the post date decides)."""
    if (w := week_from_label(topic)) is not None:
        return w
    if kind in TITLED_KINDS:
        for label in (post_title, title):
            if (w := week_from_label(label, at_start=True)) is not None:
                return w
    return None


def declared_lecture(kind: str, topic: str | None, post_title: str | None, title: str | None) -> int | None:
    if (n := lecture_from_label(topic)) is not None:
        return n
    if kind in TITLED_KINDS:
        for label in (post_title, title):
            if (n := lecture_from_label(label, at_start=True)) is not None:
                return n
    return None


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


@dataclass
class CourseCalendar:
    term_start: date | None
    days: dict[date, CalendarDay]

    def week_of(self, d: date | datetime | None) -> int | None:
        if d is None:
            return None
        if isinstance(d, datetime):
            d = d.date()
        if d in self.days:
            return self.days[d].week
        if self.term_start is None:
            return None
        delta = (monday(d) - monday(self.term_start)).days // 7
        return delta + 1 if delta >= 0 else None

    def lecture_of(self, d: date | datetime | None) -> int | None:
        if d is None:
            return None
        if isinstance(d, datetime):
            d = d.date()
        row = self.days.get(d)
        return row.lecture_no if row else None

    def week_range(self, week: int) -> tuple[date, date] | None:
        dated = sorted(d for d, row in self.days.items() if row.week == week)
        if dated:
            return dated[0], dated[-1]
        if self.term_start is None:
            return None
        start = monday(self.term_start) + timedelta(weeks=week - 1)
        return start, start + timedelta(days=6)


def load(db: Session, course: Course) -> CourseCalendar:
    rows = db.scalars(select(CalendarDay).where(CalendarDay.course_id == course.id)).all()
    term_start = course.term_start
    if term_start is None and rows:
        term_start = min(r.day for r in rows)
    if term_start is None:
        first = db.scalar(select(func.min(Material.published_at)).where(Material.course_id == course.id))
        if first is not None:
            term_start = monday(first.date())
    return CourseCalendar(term_start, {r.day: r for r in rows})


def import_csv(db: Session, course: Course, text: str) -> int:
    """Replace the course calendar from CSV with columns date, week[, lecture_no][, topics].
    Raises a 422 problem listing every bad row; nothing is written unless all rows are valid."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = {f.strip().lower() for f in (reader.fieldnames or [])}
    if not {"date", "week"} <= fields:
        raise bad_request("The calendar needs at least the columns 'date' and 'week'.")
    rows, errors = [], []
    for i, raw in enumerate(reader, start=2):
        r = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        try:
            d = dateparser.parse(r["date"], dayfirst=True).date()
            week = int(r["week"])
            lec = int(r["lecture_no"]) if r.get("lecture_no") else None
            if week < 1 or week > 30:
                raise ValueError("week must be between 1 and 30")
            rows.append(CalendarDay(course_id=course.id, day=d, week=week, lecture_no=lec,
                                    topics=r.get("topics") or None))
        except (ValueError, KeyError, OverflowError) as e:
            errors.append({"row": i, "message": str(e) or "unreadable date"})
    if errors:
        raise bad_request("Some calendar rows are invalid.", errors)
    if len({r.day for r in rows}) != len(rows):
        raise bad_request("Each date may appear only once in the calendar.")
    db.execute(delete(CalendarDay).where(CalendarDay.course_id == course.id))
    db.add_all(rows)
    if rows and course.term_start is None:
        course.term_start = min(r.day for r in rows)
    return len(rows)
