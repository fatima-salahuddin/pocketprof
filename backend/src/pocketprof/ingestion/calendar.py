"""Course calendar: maps dates to teaching weeks (MetadataTagger input, SDS 4.2; QueryScoper, 4.4).

Two sources, in priority order:
1. an uploaded calendar.csv (date, week, lecture_no, topics), when the teacher provides one;
2. the course's term start: week 1 is the week (Monday to Sunday) containing term_start.
If neither is set, term_start defaults to the Monday of the earliest material's post date.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from dateutil import parser as dateparser
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..core.errors import bad_request
from ..db.models import CalendarDay, Course, Material


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
