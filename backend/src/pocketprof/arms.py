"""Configuration arms (SDS 2.2 "configuration over code", 4.7, Appendix B).

An arm is a YAML file under `arms/` that fixes every tunable of the pipeline. Its config_hash is the
SHA-256 of the canonical JSON form, so two runs with the same hash used exactly the same settings.
An experiment is a new arm, never a code branch.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from .config import REPO_ROOT

ARMS_DIR = REPO_ROOT / "arms"


class Chunking(BaseModel):
    size: int = 384          # target tokens per chunk
    overlap: float = 0.15    # share of a prose chunk carried into the next
    min: int = 128           # slides are merged until a chunk reaches this size
    splitter: Literal["structure_aware", "fixed"] = "structure_aware"


class Retrieval(BaseModel):
    mode: Literal["hybrid", "semantic"] = "hybrid"   # hybrid = scope filter + dense + sparse (RRF)
    top_k: int = 5
    tau: float = 0.60        # abstain below this best dense cosine (calibrated per embedding model, E3)
    sparse: bool = True      # add BM25 sparse vectors to the dense ranking


class Embedding(BaseModel):
    model: str = "BAAI/bge-small-en-v1.5"
    dim: int = 384


class Generation(BaseModel):
    provider: str = "auto"
    model: str = ""
    temperature: float = 0.1
    seed: int = 7
    max_tokens: int = 700


class Prompt(BaseModel):
    variant: str = "cite-v3"
    verify_pass: bool = False


class Response(BaseModel):
    default_mode: Literal["direct", "scaffolded"] = "direct"


class Analytics(BaseModel):
    min_cohort: int = 5
    clo_student_threshold: float = 0.5
    clo_cohort_target: float = 0.6


class RunConfig(BaseModel):
    arm: str
    description: str = ""
    chunking: Chunking = Field(default_factory=Chunking)
    retrieval: Retrieval = Field(default_factory=Retrieval)
    embedding: Embedding = Field(default_factory=Embedding)
    generation: Generation = Field(default_factory=Generation)
    prompt: Prompt = Field(default_factory=Prompt)
    response: Response = Field(default_factory=Response)
    analytics: Analytics = Field(default_factory=Analytics)

    @property
    def config_hash(self) -> str:
        body = self.model_dump(exclude={"description"})
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def index_version(self) -> str:
        """Settings that change what is stored in the vector index. A course indexed under a different
        index_version must be re-indexed before this arm can serve it."""
        body = {"chunking": self.chunking.model_dump(), "embedding": self.embedding.model_dump(),
                "sparse": self.retrieval.sparse}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]


@lru_cache
def load_arm(name: str) -> RunConfig:
    path = ARMS_DIR / f"{name}.yaml"
    if not path.exists():
        raise KeyError(f"configuration arm '{name}' not found in {ARMS_DIR}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("arm", name)
    return RunConfig.model_validate(data)


def list_arms() -> list[RunConfig]:
    return [load_arm(p.stem) for p in sorted(ARMS_DIR.glob("*.yaml"))]
