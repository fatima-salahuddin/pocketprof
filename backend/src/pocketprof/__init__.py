"""PocketProf: a teacher-specific, dynamically updated AI course assistant.

Package layout follows SDS 3.3 (module decomposition):

    ingestion/   M1  provisioning and ingestion (Google Classroom, uploads, ledger, parsers, chunker)
    retrieval/   M2  embedding, per-course vector index, scope resolution, hybrid retrieval
    generation/  M2  LLM clients, prompt builder, citation mapping, chat responder, threads
    access/      M8  authentication, roles, enrolment and ownership checks
    evaluation/  M9  query sets, experiment runner, metrics with provenance
    api/             FastAPI routers under /api/v1
    db/              SQLAlchemy models and sessions (PostgreSQL in production, SQLite in development)
    core/            settings-independent plumbing: errors, logging, security primitives
"""

__version__ = "1.0.0a1"
