"""VectorIndex (SDS 4.3, Table 4, Table 7): one Qdrant collection per course (C-4).

Isolation is structural: a search names exactly one collection, so a missing filter cannot leak another
course's material, and dropping a course is one call. Payloads also carry course_id, which the retriever
checks again on every returned point (defence in depth).

Development uses Qdrant's embedded local mode (a folder under data/), production a Qdrant server;
the code path is the same client API.
"""
from __future__ import annotations

import atexit
import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient, models

from ..config import get_settings
from .embedder import SparseVec

log = logging.getLogger("pocketprof.retrieval.index")

DENSE = "dense"
SPARSE = "bm25"
PAYLOAD_INDEXES = {"week": models.PayloadSchemaType.INTEGER, "lecture_no": models.PayloadSchemaType.INTEGER,
                   "date": models.PayloadSchemaType.KEYWORD, "file": models.PayloadSchemaType.KEYWORD,
                   "material_id": models.PayloadSchemaType.INTEGER, "kind": models.PayloadSchemaType.KEYWORD}


def collection_name(course_id: int) -> str:
    return f"course_{course_id}"


def point_id(chunk_id: str) -> str:
    """Deterministic point ID: the same chunk always maps to the same point (idempotent upserts)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pocketprof:{chunk_id}"))


@dataclass
class IndexedChunk:
    chunk_id: str
    dense: list[float]
    sparse: SparseVec | None
    payload: dict[str, Any]


@dataclass
class ScoredPoint:
    chunk_id: str
    score: float
    payload: dict[str, Any]


class VectorIndex:
    def __init__(self, client: QdrantClient, local: bool):
        self.client = client
        self.local = local
        # Embedded mode is single-writer; serialise writes from the API, scheduler and ingestion threads.
        self._write_lock = threading.RLock()

    # ----- collections -----

    def exists(self, name: str) -> bool:
        return self.client.collection_exists(name)

    def ensure(self, name: str, dim: int, sparse: bool) -> None:
        with self._write_lock:
            if self.client.collection_exists(name):
                info = self.client.get_collection(name)
                vectors = info.config.params.vectors
                current = vectors[DENSE].size if isinstance(vectors, dict) else None
                has_sparse = bool(info.config.params.sparse_vectors)
                if current == dim and has_sparse == sparse:
                    return
                log.info("recreating collection for a new index version", extra={"collection": name})
                self.client.delete_collection(name)
            self.client.create_collection(
                name,
                vectors_config={DENSE: models.VectorParams(
                    size=dim, distance=models.Distance.COSINE,
                    hnsw_config=models.HnswConfigDiff(m=16, ef_construct=100))},
                sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)}
                if sparse else None,
            )
            if not self.local:  # embedded mode has no payload indexes (it filters in memory)
                for field, schema in PAYLOAD_INDEXES.items():
                    self.client.create_payload_index(name, field, field_schema=schema)

    def drop(self, name: str) -> None:
        with self._write_lock:
            if self.client.collection_exists(name):
                self.client.delete_collection(name)

    def count(self, name: str) -> int:
        if not self.client.collection_exists(name):
            return 0
        return self.client.count(name, exact=True).count

    # ----- writes -----

    def upsert(self, name: str, chunks: list[IndexedChunk], batch: int = 128) -> None:
        with self._write_lock:
            for i in range(0, len(chunks), batch):
                points = []
                for c in chunks[i:i + batch]:
                    vector: dict[str, Any] = {DENSE: c.dense}
                    if c.sparse is not None and c.sparse.indices:
                        vector[SPARSE] = models.SparseVector(indices=c.sparse.indices, values=c.sparse.values)
                    points.append(models.PointStruct(id=point_id(c.chunk_id), vector=vector,
                                                     payload={**c.payload, "chunk_id": c.chunk_id}))
                self.client.upsert(name, points=points, wait=True)

    def delete_material(self, name: str, material_id: int) -> None:
        with self._write_lock:
            if not self.client.collection_exists(name):
                return
            self.client.delete(name, points_selector=models.FilterSelector(filter=models.Filter(must=[
                models.FieldCondition(key="material_id", match=models.MatchValue(value=material_id))])), wait=True)

    def set_material_payload(self, name: str, material_id: int, payload: dict[str, Any]) -> None:
        with self._write_lock:
            if not self.client.collection_exists(name):
                return
            self.client.set_payload(name, payload=payload, points=models.Filter(must=[
                models.FieldCondition(key="material_id", match=models.MatchValue(value=material_id))]), wait=True)

    # ----- reads -----

    def dense_search(self, name: str, vector: list[float], flt: models.Filter | None, limit: int) -> list[ScoredPoint]:
        res = self.client.query_points(name, query=vector, using=DENSE, query_filter=flt, limit=limit,
                                       with_payload=True)
        return [ScoredPoint(p.payload["chunk_id"], float(p.score), p.payload) for p in res.points]

    def sparse_search(self, name: str, vec: SparseVec, flt: models.Filter | None, limit: int) -> list[ScoredPoint]:
        if not vec.indices:
            return []
        res = self.client.query_points(name, query=models.SparseVector(indices=vec.indices, values=vec.values),
                                       using=SPARSE, query_filter=flt, limit=limit, with_payload=True)
        return [ScoredPoint(p.payload["chunk_id"], float(p.score), p.payload) for p in res.points]

    def scroll_payloads(self, name: str, flt: models.Filter | None = None, limit: int = 10000) -> list[dict]:
        if not self.client.collection_exists(name):
            return []
        out, offset = [], None
        while True:
            points, offset = self.client.scroll(name, scroll_filter=flt, limit=256, offset=offset,
                                                with_payload=True, with_vectors=False)
            out.extend(p.payload for p in points)
            if offset is None or len(out) >= limit:
                return out


_index: VectorIndex | None = None
_index_lock = threading.Lock()


def get_index() -> VectorIndex:
    global _index
    with _index_lock:
        if _index is None:
            s = get_settings()
            if s.qdrant_url:
                _index = VectorIndex(QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key or None, timeout=30),
                                     local=False)
            else:
                path = s.data_dir / "qdrant"
                path.mkdir(parents=True, exist_ok=True)
                _index = VectorIndex(QdrantClient(path=str(path)), local=True)
            atexit.register(_close)
        return _index


def _close() -> None:
    """Close before interpreter shutdown: embedded mode writes its state and releases the folder lock."""
    global _index
    if _index is not None:
        try:
            _index.client.close()
        finally:
            _index = None


def reset_index_for_tests(index: VectorIndex | None) -> None:
    global _index
    _index = index
