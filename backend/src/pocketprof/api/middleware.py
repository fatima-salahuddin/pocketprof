"""Request plumbing: request IDs, access logs, Prometheus metrics (SDS 11), abuse limit (SDS Table 9)."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import Request
from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ..core.logging import request_id_var

log = logging.getLogger("pocketprof.http")

REQUESTS = Counter("pocketprof_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram("pocketprof_http_request_seconds", "HTTP request latency", ["method", "route"],
                    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 40))
CHAT_OUTCOMES = Counter("pocketprof_chat_outcomes_total", "Chat outcomes", ["outcome"])
RETRIEVAL_SECONDS = Histogram("pocketprof_retrieval_seconds", "Retrieval time",
                              buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2))
INGESTION_OUTCOMES = Counter("pocketprof_ingestion_documents_total", "Ingestion outcomes per document", ["outcome"])


class RequestContext(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        token = request_id_var.set(rid)
        t0 = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = rid
            return response
        finally:
            elapsed = time.perf_counter() - t0
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            if path.startswith("/api/"):
                REQUESTS.labels(request.method, path, str(status)).inc()
                LATENCY.labels(request.method, path).observe(elapsed)
                log.info("request", extra={"method": request.method, "route": path, "status": status,
                                           "ms": int(elapsed * 1000)})
            request_id_var.reset(token)


class RateLimiter:
    """Sliding-window limit per caller for the expensive routes (chat). In-memory: one API process in
    development; behind several workers each worker enforces its own share."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> float | None:
        """Returns seconds to wait if the caller is over the limit, else None."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return 60 - (now - q[0])
            q.append(now)
            return None


def too_many(wait_s: float) -> JSONResponse:
    return JSONResponse({"type": "/problems/rate-limited", "title": "Too many requests", "status": 429,
                         "detail": f"Please wait {int(wait_s) + 1} seconds before asking again."},
                        status_code=429, headers={"retry-after": str(int(wait_s) + 1)},
                        media_type="application/problem+json")
