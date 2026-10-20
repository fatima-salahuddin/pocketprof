"""QueryScoper (FR-7, SDS 4.4 "scope resolution").

Turns the time references in a question into a metadata filter that is applied before semantic ranking:

    "week 5", "wk 5"                 -> week = 5
    "last week", "this week"         -> week relative to today
    "today", "yesterday"             -> that date
    "on Tuesday", "Tuesday's class"  -> the most recent Tuesday up to today
    "22 Sep", "Sep 22", "22/09"      -> that date (if it is today or in the past)
    "lecture 7"                      -> lecture_no = 7 (when the calendar numbers lectures)

A date in the future is not a scope: "when is the mid on 2 November" asks about an event, and the post
announcing it was made earlier. Only past dates name the teaching that happened on them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from qdrant_client import models

from ..ingestion.calendar import CourseCalendar

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                      "nov", "dec"], start=1)}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|" \
       r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"

RE_WEEK = re.compile(r"\b(?:week|wk)\s*[-#]?\s*(\d{1,2})\b", re.I)
RE_REL_WEEK = re.compile(r"\b(last|previous|this|current)\s+week\b", re.I)
RE_LECTURE = re.compile(r"\b(?:lecture|lec)\s*[-#]?\s*(\d{1,3})\b", re.I)
RE_TODAY = re.compile(r"\b(today|yesterday)\b", re.I)
RE_WEEKDAY = re.compile(r"\b(?:on\s+|last\s+)?(" + "|".join(WEEKDAYS) + r")(?:'s)?\b", re.I)
RE_DAY_MON = re.compile(rf"\b{_DAY}\s*(?:of\s+)?{_MON}\b", re.I)
RE_MON_DAY = re.compile(rf"\b{_MON}\s+{_DAY}\b", re.I)
RE_PAST_TEACHING = re.compile(r"\b(covered?|taught|teach|discuss(ed)?|did (we|you|sir|ma'?am|the teacher)|"
                              r"was|were|went over|explain(ed)?|learn(ed|t)?|lecture|class|slides?|notes)\b", re.I)
RE_NUMERIC = re.compile(r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b")


@dataclass
class Scope:
    kind: str = "none"                # none | date | week | lecture
    label: str = ""                   # shown to the student: "Week 5", "Tue 22 Sep"
    day: date | None = None
    week: int | None = None
    lecture: int | None = None
    matched: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.kind != "none"

    def to_filter(self) -> models.Filter | None:
        if self.kind == "date" and self.day:
            return models.Filter(must=[models.FieldCondition(key="date",
                                                             match=models.MatchValue(value=self.day.isoformat()))])
        if self.kind in ("week", "date") and self.week is not None:
            return models.Filter(must=[models.FieldCondition(key="week", match=models.MatchValue(value=self.week))])
        if self.kind == "lecture" and self.lecture is not None:
            return models.Filter(must=[models.FieldCondition(key="lecture_no",
                                                             match=models.MatchValue(value=self.lecture))])
        return None

    def widen_to_week(self) -> Scope | None:
        """A date filter that finds nothing widens to that date's week (material is often posted a day
        before or after the class)."""
        if self.kind == "date" and self.week is not None:
            return Scope("week", f"Week {self.week}", None, self.week, None, self.matched,
                         self.notes + [f"nothing was posted on {self.label}, so the whole week was searched"])
        return None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": self.label, "date": self.day.isoformat() if self.day else None,
                "week": self.week, "lecture": self.lecture, "notes": self.notes}


def _valid(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _resolve_year(m: int, d: int, today: date) -> date | None:
    """A day and month without a year means the most recent such date up to today, else this year."""
    this = _valid(today.year, m, d)
    if this and this <= today:
        return this
    last = _valid(today.year - 1, m, d)
    return this if (this and (this - today).days <= 180) else last


def _fmt(d: date) -> str:
    return d.strftime("%a %d %b").replace(" 0", " ")


def resolve(question: str, cal: CourseCalendar, today: date | None = None) -> Scope:
    today = today or date.today()
    q = question

    m = RE_WEEK.search(q)
    if m:
        w = int(m.group(1))
        if 1 <= w <= 30:
            return Scope("week", f"Week {w}", week=w, matched=m.group(0))

    m = RE_REL_WEEK.search(q)
    if m:
        current = cal.week_of(today)
        if current is None:
            return Scope(notes=["the course has no term start date, so 'last week' could not be resolved"])
        w = current - 1 if m.group(1).lower() in ("last", "previous") else current
        if w >= 1:
            return Scope("week", f"Week {w}", week=w, matched=m.group(0))

    m = RE_LECTURE.search(q)
    if m and any(r.lecture_no for r in cal.days.values()):
        n = int(m.group(1))
        return Scope("lecture", f"Lecture {n}", lecture=n, matched=m.group(0))

    day: date | None = None
    matched = ""
    m = RE_TODAY.search(q)
    if m:
        day = today if m.group(1).lower() == "today" else today - timedelta(days=1)
        matched = m.group(0)
    if day is None:
        for rx, order in ((RE_DAY_MON, "dm"), (RE_MON_DAY, "md")):
            m = rx.search(q)
            if m:
                a, b = m.group(1), m.group(2)
                d_s, mon_s = (a, b) if order == "dm" else (b, a)
                day = _resolve_year(MONTHS[mon_s[:3].lower()], int(d_s), today)
                matched = m.group(0)
                break
    if day is None:
        m = RE_NUMERIC.search(q)
        # dd/mm[/yyyy]; skip things that look like scores or fractions ("3/10 marks")
        if m and not re.search(r"\b(marks?|points?|out of)\b", q[m.end():m.end() + 12], re.I):
            d_i, mo_i = int(m.group(1)), int(m.group(2))
            if 1 <= mo_i <= 12 and 1 <= d_i <= 31:
                if m.group(3):
                    y = int(m.group(3))
                    day = _valid(y + 2000 if y < 100 else y, mo_i, d_i)
                else:
                    day = _resolve_year(mo_i, d_i, today)
                matched = m.group(0)
    if day is None:
        m = RE_WEEKDAY.search(q)
        # "Is the quiz on Monday?" names an event; "what did we cover on Monday?" names a class.
        if m and RE_PAST_TEACHING.search(q):
            target = WEEKDAYS.index(m.group(1).lower())
            back = (today.weekday() - target) % 7
            day = today - timedelta(days=back)
            matched = m.group(0)

    if day is None:
        return Scope()
    if day > today:
        return Scope(notes=[f"'{matched}' is in the future, so it was read as an event date, not a class date"])
    return Scope("date", _fmt(day), day=day, week=cal.week_of(day), matched=matched)
