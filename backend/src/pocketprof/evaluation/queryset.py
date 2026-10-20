"""Versioned query sets (SRS D3; DQ-5: versioned in the repository, a change creates a new version).

    name: ds-v1
    version: "1.0"
    queries:
      - id: q001
        split: dev                      # dev | test (DQ-6: test is never used for tuning)
        type: concept                   # concept | logistics | scoped | procedural | comparison | unanswerable
        question: "How do I delete a node with two children?"
        answerable: true
        gold: ["in-order successor", "two children"]   # phrases that identify a supporting chunk
        scope: {week: 5}                # expected resolved scope, for scoped questions
        reference: "Replace the node with its in-order successor, then delete the successor."

Gold evidence is matched by phrase, not by chunk ID: chunk IDs change whenever chunking changes (E2),
phrases do not, so the same labels serve every arm.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ..config import REPO_ROOT

QUERYSET_DIR = REPO_ROOT / "eval" / "querysets"


class Query(BaseModel):
    id: str
    split: str = "dev"
    type: str = "concept"
    question: str
    answerable: bool = True
    gold: list[str] = Field(default_factory=list)
    scope: dict | None = None
    reference: str | None = None

    def is_gold(self, text: str) -> bool:
        low = " ".join(text.lower().split())
        return any(" ".join(g.lower().split()) in low for g in self.gold)


class QuerySet(BaseModel):
    name: str
    version: str
    description: str = ""
    queries: list[Query]

    def split(self, which: str) -> list[Query]:
        return self.queries if which == "all" else [q for q in self.queries if q.split == which]


def load(name: str) -> QuerySet:
    path = QUERYSET_DIR / f"{name}.yaml"
    if not path.exists():
        raise KeyError(f"query set '{name}' not found in {QUERYSET_DIR}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    qs = QuerySet.model_validate(data)
    ids = [q.id for q in qs.queries]
    if len(ids) != len(set(ids)):
        raise ValueError(f"query set '{name}' has duplicate query IDs")
    return qs


def available() -> list[str]:
    return sorted(p.stem for p in Path(QUERYSET_DIR).glob("*.yaml"))
