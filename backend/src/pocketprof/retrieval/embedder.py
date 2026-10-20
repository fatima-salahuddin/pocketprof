"""Embedder interface (SDS 3.2: the model layer is reached only through interfaces).

Dense: a sentence-embedding model through fastembed (ONNX, CPU, runs locally as C-5 requires).
Sparse: BM25 term weights, so exact course terms ("AVL", "heapify", "Quiz 3") still match when the
dense model blurs them. Both models load lazily, once per process.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

import numpy as np

from ..config import get_settings

log = logging.getLogger("pocketprof.retrieval.embedder")
# Model files are fetched once into data/models; Windows without developer mode cannot symlink there.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


@dataclass
class SparseVec:
    indices: list[int]
    values: list[float]


class Embedder:
    def __init__(self, dense_model: str, sparse_model: str | None):
        self.dense_model = dense_model
        self.sparse_model = sparse_model
        self._dense = None
        self._sparse = None
        self._lock = threading.Lock()

    def _cache_dir(self) -> str:
        s = get_settings()
        d = s.model_cache_dir or (s.data_dir / "models")
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def _load_dense(self):
        if self._dense is None:
            with self._lock:
                if self._dense is None:
                    from fastembed import TextEmbedding
                    self._dense = TextEmbedding(model_name=self.dense_model, cache_dir=self._cache_dir())
                    log.info("dense model loaded", extra={"model": self.dense_model})
        return self._dense

    def _load_sparse(self):
        if self._sparse is None and self.sparse_model:
            with self._lock:
                if self._sparse is None:
                    from fastembed import SparseTextEmbedding
                    self._sparse = SparseTextEmbedding(model_name=self.sparse_model, cache_dir=self._cache_dir())
                    log.info("sparse model loaded", extra={"model": self.sparse_model})
        return self._sparse

    @property
    def dim(self) -> int:
        return len(self.embed_query("dimension probe"))

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        vecs = np.asarray(list(self._load_dense().passage_embed(texts, batch_size=32)), dtype=np.float32)
        return vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)

    def embed_query(self, text: str) -> np.ndarray:
        v = np.asarray(next(iter(self._load_dense().query_embed([text]))), dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)

    def sparse_passages(self, texts: list[str]) -> list[SparseVec]:
        model = self._load_sparse()
        if model is None:
            return []
        return [SparseVec(e.indices.tolist(), e.values.tolist()) for e in model.passage_embed(texts)]

    def sparse_query(self, text: str) -> SparseVec | None:
        model = self._load_sparse()
        if model is None:
            return None
        e = next(iter(model.query_embed([text])))
        return SparseVec(e.indices.tolist(), e.values.tolist())

    def warm_up(self) -> None:
        self.embed_query("warm up")
        if self.sparse_model:
            self.sparse_query("warm up")


_embedders: dict[tuple[str, str | None], Embedder] = {}
_registry_lock = threading.Lock()


def get_embedder(dense_model: str | None = None, sparse: bool = True) -> Embedder:
    s = get_settings()
    key = (dense_model or s.embed_model, s.sparse_model if sparse else None)
    with _registry_lock:
        if key not in _embedders:
            _embedders[key] = Embedder(*key)
        return _embedders[key]
