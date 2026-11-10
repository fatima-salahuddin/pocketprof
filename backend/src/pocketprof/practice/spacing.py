"""SpacingPolicy and the mastery estimate (M4, FR-18; SDS 4.5, Table 2 "Learner model").

Both are short deterministic rules a teacher can be shown, not trained learner models:

    Leitner: each student holds a box b in 1..5 per released item, with intervals 1, 2, 4, 8, 16 days.
             correct -> b = min(b + 1, 5);  wrong -> b = 1;  next_due = attempt time + interval(b)
    mastery: m = sum(w_k * c_k) / sum(w_k) over the last N attempts on a topic, w_k = decay^(k-1),
             k = 1 the most recent attempt (defaults N = 10, decay = 0.8)

The intervals, window and decay come from the arm (configuration over code).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from ..arms import Practice


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes for timezone-aware columns; everything here is UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class SpacingPolicy:
    def __init__(self, cfg: Practice):
        if not cfg.intervals_days:
            raise ValueError("the practice arm needs at least one Leitner interval")
        self.intervals = list(cfg.intervals_days)

    @property
    def boxes(self) -> int:
        return len(self.intervals)

    def next_box(self, box: int, correct: bool) -> int:
        return min(box + 1, self.boxes) if correct else 1

    def interval(self, box: int) -> timedelta:
        return timedelta(days=self.intervals[min(max(box, 1), self.boxes) - 1])

    def next_due(self, now: datetime, box: int) -> datetime:
        return now + self.interval(box)


def mastery_estimate(outcomes_newest_first: Sequence[bool], window: int = 10, decay: float = 0.8) -> float | None:
    """Recency-weighted share of correct answers; None when there are no attempts."""
    recent = list(outcomes_newest_first)[:window]
    if not recent:
        return None
    weights = [decay ** k for k in range(len(recent))]
    return round(sum(w for w, c in zip(weights, recent, strict=True) if c) / sum(weights), 4)
