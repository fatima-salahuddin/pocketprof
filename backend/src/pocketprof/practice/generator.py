"""PracticeGenerator (M4, FR-10, FR-17; SDS Table 1).

    selection (week | topic | file) -> the slice's chunks (shared with notes, M3)
        -> keep only DELIVERED material (FR-10): posted on or before today
        -> drop announcements (logistics, not content) -> word budget -> practice prompt -> model (JSON)
        -> validate every item -> DRAFT items, each pinned to the chunk and file version it came from

Nothing here reaches a student: every item starts as DRAFT and waits in the teacher's validation queue
(FR-17). A malformed item is discarded, never repaired by guessing.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from ..arms import RunConfig, load_arm
from ..core.errors import Problem, bad_request
from ..core.security import now_utc
from ..db.models import ITEM_KINDS, Course, PracticeItem, User
from ..generation import prompts
from ..generation.llm import GenerationRefused, get_client
from ..generation.notes import apply_budget, collect_chunks, resolve_selection, source_record

log = logging.getLogger("pocketprof.practice.generator")

LETTERS = "ABCD"
_OPTION_PREFIX = re.compile(r"^\s*(?:\(?[A-Da-d][).:]\s+)")


def model_unavailable(detail: str) -> Problem:
    return Problem(503, "Model unavailable", detail, "/problems/model-unavailable")


# ---------- delivered material (FR-10) ----------

def week_start(course: Course, week: int | None) -> date | None:
    if week is None or course.term_start is None:
        return None
    return course.term_start + timedelta(weeks=week - 1)


def is_delivered(course: Course, *, day: str | date | None, week: int | None, today: date | None = None) -> bool:
    """Material counts as delivered once the day it was posted (or, without a date, the start of its week)
    has passed. Undated, unweeked uploads were posted by the teacher already, so they count as delivered."""
    today = today or now_utc().date()
    if day:
        d = date.fromisoformat(day) if isinstance(day, str) else day
        return d <= today
    start = week_start(course, week)
    return start is None or start <= today


# ---------- parsing and validation (pure) ----------

def _json_array(text: str) -> list[Any]:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    i, j = t.find("["), t.rfind("]")
    if i >= 0 and j > i:
        try:
            data = json.loads(t[i:j + 1])
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            pass
    out = []  # fall back to one object per line
    for line in t.splitlines():
        line = line.strip().rstrip(",")
        if line.startswith("{") and line.endswith("}"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _source_number(value: Any, n_sources: int) -> int | None:
    m = re.search(r"\d+", str(value or ""))
    if not m:
        return None
    n = int(m.group(0))
    return n if 1 <= n <= n_sources else None


def clean_item(obj: Any, n_sources: int, kinds: list[str]) -> dict | None:
    """One model item -> a validated dict, or None. The same rules apply to a teacher's edits."""
    if not isinstance(obj, dict):
        return None
    kind = str(obj.get("kind", "")).strip().lower()
    kind = {"multiple choice": "mcq", "true/false": "tf", "true_false": "tf", "short answer": "short"}.get(kind, kind)
    if kind not in ITEM_KINDS or kind not in kinds:
        return None
    question = " ".join(str(obj.get("question", "")).split())
    if not 8 <= len(question) <= 800:
        return None
    src = _source_number(obj.get("source"), n_sources) if n_sources else 0
    if n_sources and src is None:
        return None  # FR-17: every item records the chunk it came from
    explanation = " ".join(str(obj.get("explanation") or "").split())[:800] or None
    answer = obj.get("answer")
    options = None
    if kind == "mcq":
        raw = obj.get("options")
        if not isinstance(raw, list) or len(raw) != 4:
            return None
        options = [_OPTION_PREFIX.sub("", " ".join(str(o).split())) for o in raw]
        if any(not o or len(o) > 300 for o in options) or len({o.lower() for o in options}) != 4:
            return None
        a = str(answer if answer is not None else "").strip()
        if len(a) == 1 and a.upper() in LETTERS:
            answer = a.upper()
        elif a.isdigit() and int(a) in range(4):
            answer = LETTERS[int(a)]
        else:
            stripped = _OPTION_PREFIX.sub("", a).lower()
            match = [i for i, o in enumerate(options) if o.lower() == stripped]
            if not match:
                letter = re.match(r"^\(?([A-Da-d])[).:]", a)
                if not letter:
                    return None
                answer = letter.group(1).upper()
            else:
                answer = LETTERS[match[0]]
    elif kind == "tf":
        a = str(answer).strip().lower()
        if a not in ("true", "false"):
            return None
        answer = a
    else:
        answer = " ".join(str(answer or "").split())
        if not 2 <= len(answer) <= 1500:
            return None
    return {"kind": kind, "source": src, "question": question, "options": options, "answer": answer,
            "explanation": explanation}


def parse_items(text: str, n_sources: int, kinds: list[str], limit: int) -> tuple[list[dict], int]:
    """Returns (valid items, number discarded). Duplicate questions are discarded."""
    raw = _json_array(text)
    items, seen = [], set()
    for obj in raw:
        item = clean_item(obj, n_sources, kinds)
        if item is None or item["question"].lower() in seen:
            continue
        seen.add(item["question"].lower())
        items.append(item)
    return items[:limit], len(raw) - len(items[:limit])


# ---------- the generator ----------

class PracticeGenerator:
    def __init__(self, db: Session, course: Course, user: User, *, arm: RunConfig | None = None):
        self.db, self.course, self.user = db, course, user
        self.arm = arm or load_arm(course.active_arm)

    def _style(self, query: str) -> str:
        from ..style.service import style_block
        return style_block(self.db, self.course, self.arm, query)

    def generate(self, selection: dict[str, Any], n: int, kinds: list[str],
                 stop: threading.Event | None = None) -> dict:
        cfg = self.arm.practice
        n = max(1, min(n, cfg.max_generate))
        kinds = [k for k in ITEM_KINDS if k in kinds] or list(ITEM_KINDS)
        selection, label = resolve_selection(self.db, self.course, selection)
        payloads = collect_chunks(self.course, self.arm, selection)
        if not payloads:
            raise bad_request(f"There is no class material for {label}.")
        today = now_utc().date()
        delivered = [p for p in payloads if is_delivered(self.course, day=p.get("date"), week=p.get("week"), today=today)]
        if not delivered:
            raise bad_request(f"{label} has not been taught yet. Practice questions can only come from topics "
                              "that have already been delivered (FR-10).")
        content = [p for p in delivered if p.get("kind") != "Announcement"]
        if not content:
            raise bad_request(f"The material for {label} is only announcements; there is no teaching content to "
                              "practise.")
        content, _ = apply_budget(content, cfg.word_budget)

        client = get_client(self.arm.generation)
        if client is None:
            raise model_unavailable("Generating practice questions needs a language model, and none is configured.")
        ctx = prompts.CourseContext(self.course.code, self.course.title, self.course.teacher.name)
        system, messages = prompts.practice_prompt(ctx, label, content, n, kinds, style_block=self._style(label))
        try:
            text = "".join(client.stream(system, messages, max_tokens=max(cfg.max_tokens, self.arm.generation.max_tokens),
                                         stop=stop or threading.Event()))
        except GenerationRefused:
            raise model_unavailable("The language model declined this request. Try a different selection.") from None
        except Exception as e:
            log.warning("practice generation failed", extra={"course_id": self.course.id, "error": type(e).__name__})
            raise model_unavailable("The language model could not be reached. Try again in a minute.") from None

        items, discarded = parse_items(text, len(content), kinds, n)
        created = []
        for it in items:
            p = content[it["source"] - 1]
            row = PracticeItem(
                course_id=self.course.id, material_id=p.get("material_id"), chunk_id=p.get("chunk_id") or "",
                file_hash=p.get("file_hash"), topic=p.get("topic"), week=p.get("week"), kind=it["kind"],
                question=it["question"], options=it["options"], answer=it["answer"], explanation=it["explanation"],
                source=source_record(1, p), status="draft", created_by=self.user.id, model=client.name,
                config_hash=self.arm.config_hash, prompt_version=prompts.PRACTICE_PROMPT_VERSION)
            self.db.add(row)
            created.append(row)
        self.db.flush()
        log.info("practice generated", extra={"course_id": self.course.id, "n_created": len(created),
                                              "n_discarded": discarded})
        return {"label": label, "items": created, "requested": n, "discarded": discarded}
