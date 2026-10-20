"""ExperimentRunner (M9, FR-25, SDS 4.7): run a query-set split against a configuration arm.

Each query goes through exactly the production path (QueryScoper, Retriever and, when `generate` is on,
the ChatResponder) inside a transaction that is rolled back, so evaluation leaves no threads or usage
rows behind. Every metric is stored with the arm's config_hash, the model, the prompt version and the
index version (provenance).
"""
from __future__ import annotations

import logging
import threading
import time

from sqlalchemy import select

from ..arms import load_arm
from ..core.security import now_utc
from ..db import session_scope
from ..db.models import Course, ExperimentRun, User
from ..generation.llm import describe
from ..generation.prompts import PROMPT_VERSION
from ..generation.responder import ChatResponder
from ..ingestion import calendar
from ..retrieval import scoper
from ..retrieval.retriever import retrieve
from . import metrics, queryset

log = logging.getLogger("pocketprof.evaluation")


def _evaluate_query(db, course: Course, user: User, arm, q: queryset.Query, generate: bool) -> metrics.QueryResult:
    t0 = time.perf_counter()
    cal = calendar.load(db, course)
    scope = scoper.resolve(q.question, cal)
    result = retrieve(course.id, q.question, arm, scope)
    t_ret = time.perf_counter()
    hits = result.hits
    gold_flags = [q.is_gold(h.text) for h in hits]
    outcome, answer, cited = ("refused" if (result.abstain or not hits) else "retrieved"), "", []
    if generate:
        sp = db.begin_nested()
        try:
            done = None
            for ev in ChatResponder(db, course, user, arm=arm).respond(q.question):
                if ev.type == "done":
                    done = ev.data
            if done:
                outcome, answer = done["outcome"], done["answer"]
                cited = [c["n"] for c in done["citations"]]
        finally:
            sp.rollback()
    declined = outcome in ("refused", "smalltalk") if generate else bool(result.abstain or not hits)
    return metrics.QueryResult(
        id=q.id, type=q.type, answerable=q.answerable, declined=declined, outcome=outcome, cited=len(cited),
        retrieved=len(hits), gold_retrieved=sum(gold_flags), has_gold_labels=bool(q.gold),
        cited_gold=sum(1 for n in cited if 0 < n <= len(gold_flags) and gold_flags[n - 1]),
        scope_expected=q.scope, scope_resolved=scope.as_dict() if scope.active else None,
        scope_weeks=[h.payload.get("week") for h in hits], top_score=round(result.top_score, 4),
        retrieval_ms=int((t_ret - t0) * 1000), total_ms=int((time.perf_counter() - t0) * 1000), answer=answer[:1500])


def run(run_id: int) -> None:
    with session_scope() as db:
        er = db.get(ExperimentRun, run_id)
        course = db.get(Course, er.course_id)
        arm = load_arm(er.arm)
        qs = queryset.load(er.query_set)
        # Generation runs as the course's teacher: no query-log rows, same retrieval scope as a student.
        user = db.get(User, course.teacher_id)
        generate = er.model != "retrieval-only"
        results = []
        try:
            for q in qs.split(er.split):
                results.append(_evaluate_query(db, course, user, arm, q, generate))
            er.metrics = metrics.compute(results, generated=generate)
            er.results = [r.__dict__ for r in results]
            er.status = "done"
        except Exception as e:
            log.exception("evaluation run failed", extra={"run_id": run_id})
            er.status, er.metrics = "failed", {"error": str(e)[:500]}
        er.finished_at = now_utc()
    log.info("evaluation run finished", extra={"run_id": run_id})


def create_run(course_id: int, query_set: str, split: str, arm_name: str, experiment: str, *, generate: bool) -> int:
    arm = load_arm(arm_name)
    qs = queryset.load(query_set)
    with session_scope() as db:
        course = db.get(Course, course_id)
        if course.index_version != arm.index_version:
            raise ValueError(f"course {course_id} is indexed under another chunking/embedding configuration; "
                             f"set its arm to '{arm_name}' and let it re-index first")
        er = ExperimentRun(experiment=experiment, arm=arm.arm, config_hash=arm.config_hash, course_id=course_id,
                           query_set=qs.name, query_set_version=qs.version, split=split,
                           model=describe(arm.generation) if generate else "retrieval-only",
                           prompt_version=PROMPT_VERSION, index_version=arm.index_version)
        db.add(er)
        db.flush()
        return er.id


def start_run(course_id: int, query_set: str, split: str, arm_name: str, experiment: str, *, generate: bool) -> int:
    run_id = create_run(course_id, query_set, split, arm_name, experiment, generate=generate)
    threading.Thread(target=run, args=(run_id,), name=f"eval-{run_id}", daemon=True).start()
    return run_id


def latest(experiment: str | None = None) -> list[ExperimentRun]:
    with session_scope() as db:
        q = select(ExperimentRun).order_by(ExperimentRun.id.desc()).limit(20)
        if experiment:
            q = q.where(ExperimentRun.experiment == experiment)
        return list(db.scalars(q).all())
