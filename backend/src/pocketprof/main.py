"""FastAPI application factory.  Run:  uvicorn pocketprof.main:app"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from . import __version__
from .api import routes_admin, routes_auth, routes_chat, routes_courses, routes_google, routes_insights
from .api.middleware import RequestContext
from .arms import load_arm
from .config import get_settings
from .core import errors
from .core.logging import setup
from .core.netfix import prefer_ipv4
from .db import session_scope
from .db.migrate import upgrade
from .db.models import Course
from .db.session import engine
from .ingestion.locks import is_running
from .retrieval.embedder import get_embedder
from .scheduler import build as build_scheduler

log = logging.getLogger("pocketprof")


def _recover_interrupted_runs() -> None:
    """A run interrupted by a restart leaves its course 'syncing'. Reset it only if no process holds the
    course's lock: in Compose another worker or the scheduler may genuinely be syncing it."""
    with session_scope() as db:
        for c in db.query(Course).filter(Course.status == "syncing"):
            if not is_running(c.id):
                c.status, c.status_msg = "error", "Sync was interrupted by a restart; it will run again shortly."


def _warm_up() -> None:
    try:
        arm = load_arm(get_settings().active_arm)
        get_embedder(arm.embedding.model, sparse=arm.retrieval.sparse).warm_up()
    except Exception:
        log.exception("model warm-up failed; models will load on first use")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if s.migrate_on_start:  # Compose runs migrations once in a separate step instead (MIGRATE_ON_START=false)
        upgrade()
    _recover_interrupted_runs()
    threading.Thread(target=_warm_up, name="warm-up", daemon=True).start()
    sched = build_scheduler() if s.run_scheduler else None
    if sched is not None:
        sched.start()
    log.info("PocketProf API started", extra={"version": __version__, "env": s.env,
                                              "database": s.sqlalchemy_url.split(":")[0],
                                              "qdrant": "server" if s.qdrant_url else "embedded"})
    yield
    if sched is not None:
        sched.shutdown(wait=False)


def create_app() -> FastAPI:
    s = get_settings()
    setup(json_logs=not s.is_dev)
    if s.force_ipv4:
        prefer_ipv4()
    app = FastAPI(title="PocketProf API", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.add_middleware(RequestContext)
    app.add_middleware(CORSMiddleware, allow_origins=[s.public_url.rstrip("/")], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"], expose_headers=["x-request-id"])
    errors.install(app)
    for r in (routes_auth, routes_courses, routes_google, routes_chat, routes_insights, routes_admin):
        app.include_router(r.router, prefix="/api/v1")

    @app.get("/health", include_in_schema=False)
    def health():
        with engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "version": __version__}

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
