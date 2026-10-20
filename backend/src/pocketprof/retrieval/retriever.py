"""Retriever (M2, FR-4, FR-6, FR-7; SDS 4.4).

    scope filter (QueryScoper)  ->  dense kNN + BM25 sparse, fused by reciprocal rank  ->  top-k
    abstain when the filtered set is empty or the best dense cosine is below tau

Dense cosine scores are kept on every hit even in hybrid mode: RRF scores are rank-based and say nothing
about whether the course covers the question, so abstention always looks at the cosine.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from qdrant_client import models

from ..arms import RunConfig
from .embedder import get_embedder
from .scoper import Scope
from .vector_index import ScoredPoint, collection_name, get_index

log = logging.getLogger("pocketprof.retrieval")
RRF_K = 60


@dataclass
class Hit:
    chunk_id: str
    payload: dict
    dense_score: float     # cosine similarity to the question (0 if found only by BM25)
    fused_score: float

    @property
    def text(self) -> str:
        return self.payload.get("text", "")


@dataclass
class RetrievalResult:
    hits: list[Hit]
    scope: Scope
    top_score: float
    abstain: str | None = None        # why nothing usable was found: no_material | out_of_scope | low_score
    timings_ms: dict[str, int] = field(default_factory=dict)


def _with_course(flt: models.Filter | None, course_id: int) -> models.Filter:
    must = [models.FieldCondition(key="course_id", match=models.MatchValue(value=course_id))]
    if flt is not None and flt.must:
        must.extend(flt.must)
    return models.Filter(must=must)


def rrf(rankings: list[list[ScoredPoint]], k: int = RRF_K) -> dict[str, float]:
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, p in enumerate(ranking):
            fused[p.chunk_id] = fused.get(p.chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return fused


def retrieve(course_id: int, query: str, arm: RunConfig, scope: Scope) -> RetrievalResult:
    t0 = time.perf_counter()
    index = get_index()
    name = collection_name(course_id)
    if not index.exists(name):
        return RetrievalResult([], scope, 0.0, "no_material")
    embedder = get_embedder(arm.embedding.model, sparse=arm.retrieval.sparse)
    use_scope = arm.retrieval.mode == "hybrid" and scope.active
    qvec = embedder.embed_query(query).tolist()
    qsparse = embedder.sparse_query(query) if arm.retrieval.sparse else None
    t_embed = time.perf_counter()

    def search(sc: Scope | None):
        flt = _with_course(sc.to_filter() if sc else None, course_id)
        limit = max(arm.retrieval.top_k * 4, 20)
        dense = index.dense_search(name, qvec, flt, limit)
        sparse = index.sparse_search(name, qsparse, flt, limit) if qsparse else []
        return dense, sparse

    dense, sparse = search(scope if use_scope else None)
    if use_scope and not dense:
        wider = scope.widen_to_week()
        if wider is not None:
            scope = wider
            dense, sparse = search(scope)
    t_search = time.perf_counter()
    timings = {"embed": int((t_embed - t0) * 1000), "search": int((t_search - t_embed) * 1000)}

    if not dense:
        return RetrievalResult([], scope, 0.0, "out_of_scope" if use_scope else "no_material", timings)

    cos = {p.chunk_id: p.score for p in dense}
    payloads = {p.chunk_id: p.payload for p in dense}
    payloads.update({p.chunk_id: p.payload for p in sparse if p.chunk_id not in payloads})
    fused = rrf([dense, sparse]) if sparse else {p.chunk_id: p.score for p in dense}
    order = sorted(fused, key=lambda cid: fused[cid], reverse=True)
    hits = []
    for cid in order:
        pl = payloads[cid]
        if pl.get("course_id") != course_id:  # defence in depth: never trust the store alone
            log.error("cross-course payload returned", extra={"course_id": course_id, "chunk": cid})
            continue
        hits.append(Hit(cid, pl, cos.get(cid, 0.0), fused[cid]))
        if len(hits) >= arm.retrieval.top_k:
            break
    top = max(cos.values())
    abstain = None if top >= arm.retrieval.tau else "low_score"
    # A scoped question ("what did we cover in week 5?") is answerable by construction: everything in the
    # filtered set is relevant, even if no single chunk is semantically close to the wording.
    if use_scope and abstain == "low_score":
        abstain = None
    return RetrievalResult(hits, scope, top, abstain, timings)
