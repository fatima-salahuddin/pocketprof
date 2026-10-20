"""Command line for the evaluation harness.

    python -m pocketprof.evaluation run --course 1 --arm shipped-v1 --split dev [--retrieval-only]
    python -m pocketprof.evaluation compare 4 5          # two runs side by side
    python -m pocketprof.evaluation list
"""
from __future__ import annotations

import argparse
import sys

from ..core.logging import setup
from ..db import session_scope
from ..db.models import ExperimentRun
from . import runner

KEYS = ["n", "decision_accuracy", "refusal_precision", "refusal_recall", "refusal_f1", "unsupported_answer_rate",
        "false_refusal_rate", "context_recall_at_k", "context_precision_at_k", "scoped_correctness",
        "citation_presence", "citation_support", "retrieval_ms_p50", "retrieval_ms_p95", "end_to_end_ms_p50",
        "end_to_end_ms_p95"]


def _print_metrics(m: dict) -> None:
    for k in KEYS:
        if k in m:
            print(f"  {k:<26} {m[k]}")
    if m.get("outcomes"):
        print(f"  {'outcomes':<26} {m['outcomes']}")


def main(argv: list[str] | None = None) -> int:
    setup(json_logs=False)
    ap = argparse.ArgumentParser(prog="pocketprof.evaluation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--course", type=int, required=True)
    r.add_argument("--arm", default="shipped-v1")
    r.add_argument("--query-set", default="ds-v1")
    r.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    r.add_argument("--experiment", default="regression")
    r.add_argument("--retrieval-only", action="store_true")
    c = sub.add_parser("compare")
    c.add_argument("a", type=int)
    c.add_argument("b", type=int)
    sub.add_parser("list")
    args = ap.parse_args(argv)

    if args.cmd == "run":
        run_id = runner.create_run(args.course, args.query_set, args.split, args.arm, args.experiment,
                                   generate=not args.retrieval_only)
        runner.run(run_id)
        with session_scope() as db:
            er = db.get(ExperimentRun, run_id)
            print(f"run {run_id}: {er.status}  arm={er.arm} hash={er.config_hash[:12]} model={er.model}")
            _print_metrics(er.metrics)
            misses = [x for x in er.results if x["declined"] == x["answerable"]]
            if misses:
                print("\n  wrong decisions:")
                for x in misses:
                    print(f"    {x['id']} ({x['type']}) answerable={x['answerable']} outcome={x['outcome']} "
                          f"top={x['top_score']}")
        return 0
    if args.cmd == "compare":
        with session_scope() as db:
            a, b = db.get(ExperimentRun, args.a), db.get(ExperimentRun, args.b)
            print(f"{'metric':<26} {a.arm:>16} {b.arm:>16}")
            for k in KEYS:
                print(f"{k:<26} {str(a.metrics.get(k)):>16} {str(b.metrics.get(k)):>16}")
        return 0
    for er in runner.latest():
        print(f"{er.id:>4} {er.experiment:<12} {er.arm:<16} {er.split:<5} {er.status:<8} "
              f"acc={er.metrics.get('decision_accuracy')} recall@k={er.metrics.get('context_recall_at_k')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
