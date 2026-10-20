"""Metrics (SRS Tables 23-24): how conventional classifier metrics apply to a grounded assistant.

The answer-or-decline decision is the classifier: the positive class is "declined", the truth is
"unanswerable". Retrieval is scored with context precision and recall at k against phrase-matched gold.
Confidence intervals are percentile bootstraps over queries (paired arms see the same queries).
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass


@dataclass
class QueryResult:
    id: str
    type: str
    answerable: bool
    declined: bool
    outcome: str
    cited: int
    retrieved: int
    gold_retrieved: int
    has_gold_labels: bool
    cited_gold: int
    scope_expected: dict | None
    scope_resolved: dict | None
    scope_weeks: list
    top_score: float
    retrieval_ms: int
    total_ms: int
    answer: str = ""


def _safe(n: float, d: float) -> float | None:
    return round(n / d, 4) if d else None


def _ci(values: list[float], iters: int = 2000, seed: int = 7) -> list[float] | None:
    if len(values) < 5:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(iters))
    return [round(means[int(0.025 * iters)], 4), round(means[int(0.975 * iters) - 1], 4)]


def _pct(values: list[int], p: float) -> int | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def scope_correct(r: QueryResult) -> bool | None:
    if not r.scope_expected:
        return None
    resolved = r.scope_resolved or {}
    for key in ("week", "date", "lecture"):
        if key in r.scope_expected and r.scope_expected[key] != resolved.get(key):
            return False
    if "week" in r.scope_expected:
        # every retrieved chunk must come from the expected week (scoped correctness, E1)
        return all(w == r.scope_expected["week"] for w in r.scope_weeks) if r.scope_weeks else False
    return True


def compute(results: list[QueryResult], generated: bool) -> dict:
    n = len(results)
    ans = [r for r in results if r.answerable]
    unans = [r for r in results if not r.answerable]
    tp = sum(1 for r in unans if r.declined)              # correctly declined
    fp = sum(1 for r in ans if r.declined)                # declined an answerable query
    fn = sum(1 for r in unans if not r.declined)          # answered an unanswerable query
    correct = [1.0 if (r.declined != r.answerable) else 0.0 for r in results]
    labelled = [r for r in ans if r.has_gold_labels]
    recall_vals = [1.0 if r.gold_retrieved else 0.0 for r in labelled]
    precision_vals = [r.gold_retrieved / r.retrieved for r in labelled if r.retrieved]
    answered = [r for r in results if not r.declined and r.outcome == "answered"]
    scoped = [s for s in (scope_correct(r) for r in results) if s is not None]
    rp, rr = _safe(tp, tp + fp), _safe(tp, tp + fn)
    m = {
        "n": n, "n_answerable": len(ans), "n_unanswerable": len(unans), "generated": generated,
        "decision_accuracy": _safe(sum(correct), n), "decision_accuracy_ci95": _ci(correct),
        "refusal_precision": rp, "refusal_recall": rr,
        "refusal_f1": round(2 * rp * rr / (rp + rr), 4) if rp and rr else None,
        "unsupported_answer_rate": _safe(fn, len(unans)),
        "false_refusal_rate": _safe(fp, len(ans)),
        "context_recall_at_k": _safe(sum(recall_vals), len(recall_vals)),
        "context_recall_at_k_ci95": _ci(recall_vals),
        "context_precision_at_k": round(statistics.fmean(precision_vals), 4) if precision_vals else None,
        "scoped_correctness": _safe(sum(scoped), len(scoped)), "n_scoped": len(scoped),
        "retrieval_ms_p50": _pct([r.retrieval_ms for r in results], 0.5),
        "retrieval_ms_p95": _pct([r.retrieval_ms for r in results], 0.95),
    }
    if generated:
        with_labels = [r for r in answered if r.has_gold_labels and r.cited]
        m.update({
            "citation_presence": _safe(sum(1 for r in answered if r.cited), len(answered)),
            "citation_support": _safe(sum(1 for r in with_labels if r.cited_gold), len(with_labels)),
            "end_to_end_ms_p50": _pct([r.total_ms for r in results], 0.5),
            "end_to_end_ms_p95": _pct([r.total_ms for r in results], 0.95),
            "outcomes": {o: sum(1 for r in results if r.outcome == o) for o in {r.outcome for r in results}},
            "faithfulness": None,  # RAGAS faithfulness needs a judge model; added with E3 in FYP-II
        })
    return m
