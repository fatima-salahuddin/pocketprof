<h1>PocketProf</h1>

<p><b>An AI course assistant that only answers from your teacher's own material, and says so when it can't.</b></p>

<p>
  <img src="https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/Qdrant-DC244C?style=flat-square" alt="Qdrant">
  <img src="https://img.shields.io/badge/SQLAlchemy-D71F00?style=flat-square&logo=sqlalchemy&logoColor=white" alt="SQLAlchemy">
  <img src="https://img.shields.io/badge/React-20232A?style=flat-square&logo=react&logoColor=61DAFB" alt="React">
  <img src="https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white" alt="TypeScript">
  <img src="https://img.shields.io/badge/Docker_Compose-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker Compose">
  <img src="https://img.shields.io/badge/tests-59-0D9488?style=flat-square" alt="59 tests">
  <img src="https://img.shields.io/badge/FAST--NUCES-FYP_2026-2563EB?style=flat-square" alt="FAST-NUCES FYP 2026">
</p>

<table>
  <tr>
    <td align="center"><b>97.5%</b><br><sub>answer-or-decline<br>accuracy</sub></td>
    <td align="center"><b>0%</b><br><sub>unsupported answers<br>(target 5% or less)</sub></td>
    <td align="center"><b>1.0</b><br><sub>citation presence<br>and support</sub></td>
    <td align="center"><b>0.5 s</b><br><sub>to re-check an unchanged<br>class (vs 23 s first read)</sub></td>
    <td align="center"><b>1.3 s</b><br><sub>median answer<br>latency</sub></td>
  </tr>
</table>

<sub>Baseline on the sample class, 40-query set <code>ds-v1</code>, 21 Oct 2026. Details under <a href="#tests-and-evaluation">Tests and evaluation</a>.</sub>

A teacher-specific, dynamically updated AI course assistant. Students ask questions about a class and get
answers drawn **only** from that class's own material on Google Classroom (slides, notes, assignment briefs,
announcements), with a citation to the exact slide, page or post. When the material does not cover a
question, PocketProf says so instead of answering from general knowledge.

FAST-NUCES Karachi, Final Year Project, Fall 2026. This repository is release 1.0 (phase 1) of the system
specified in the SRS v1.0 and SDS v1.0.

## What is in phase 1

| Module (SDS 3.3) | Status | Where |
|---|---|---|
| **M1** Provisioning and ingestion | Done | `backend/src/pocketprof/ingestion/` |
| **M2** Grounded assistant (retrieval, scope, citations, threads) | Done | `retrieval/`, `generation/` |
| **M7** Interaction design (direct / step-by-step mode) | Done (prompt modes; E6 not yet run) | `generation/prompts.py` |
| **M8** Access control | Done | `access/` |
| **M9** Evaluation harness | Done (harness, metrics, `ds-v1` query set) | `evaluation/`, `eval/` |
| M3 Notes and export, M4 Practice, M5 Style, M6 Analytics/CQI | Phase 2 | — |

## How it works

```
Google Classroom ─┐                                   ┌─ React web app (student, teacher, admin)
teacher uploads ──┤   M1 ProvisioningJob              │
                  ├─> ledger diff ─> parse ─> chunk ─> embed (dense + BM25) ─> Qdrant: one collection per course
calendar.csv ─────┘   (SHA-256 + source version)                                      │
                                                                                     ▼
student question ─> M8 AccessGuard ─> QueryScoper (week/date) ─> hybrid retrieval ─> abstain? ─> refusal
                                                                       │                 (no model call)
                                                                       └─> PromptBuilder ─> LLMClient (stream)
                                                                            ─> citation check ─> answer | refusal | sources only
```

* **Incremental by construction.** Every document's source version (Drive `md5Checksum`/`modifiedTime`,
  Classroom `updateTime`) and SHA-256 are kept in a materials ledger. An unchanged class costs a few list
  calls and nothing else (measured: 8 documents, 0.5 s versus 23 s for the first read), so the scheduler
  checks every linked class every 30 s and new posts appear within about a minute.
* **Isolation is structural.** Each course has its own Qdrant collection; a search names exactly one, and
  every returned payload's `course_id` is checked again.
* **Grounded, and checked in code.** The prompt requires `[Sn]` citations and the exact sentinel
  `NOT_IN_MATERIAL` when the sources do not answer. The responder holds back the stream until the opening
  cannot be the sentinel, treats an answer with no valid citation as unsupported (FR-5), and fails closed:
  if the model fails, the student sees the retrieved passages, never an unsupported answer.
* **Two abstention gates.** Retrieval declines when the best dense cosine is below τ (no model call);
  the model's sentinel catches on-topic questions the course never taught. See the evaluation below.
* **Configuration over code.** Chunking, retrieval, embedding, prompt and mode settings live in versioned
  arms (`arms/*.yaml`); each arm's SHA-256 is recorded with every sync and every evaluation run.

## Running it (development)

Requirements: Python 3.11+, Node 20+. No database or vector server is needed in development: it runs on
SQLite and Qdrant's embedded mode under `./data`.

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"        # Linux/macOS: .venv/bin/pip
cp ../.env.example ../.env                    # then fill in the Google client and a generation arm
.venv/Scripts/python -m pocketprof.seed       # admin, teacher and student accounts + an indexed sample class
.venv/Scripts/python -m uvicorn pocketprof.main:app --port 8000
```

```bash
cd web
npm install
npm run dev                                   # http://localhost:5173 (proxies /api to :8000)
```

Seed accounts are `admin@`, `teacher@` and `student@pocketprof.local`, with the password from
`SEED_PASSWORD` in `.env`. The student is enrolled in the sample class (join code `sample-ds`).

**Google Classroom.** In Google Cloud Console, add the redirect URI
`http://localhost:8000/api/v1/google/callback` to the OAuth client (the prototype used
`/api/google/callback`). A teacher connects on *My classes* and links a class by its class code; students
join with the same code.

**Generation arm.** `GEN_PROVIDER=anthropic` uses Claude through the official SDK (default model
`claude-opus-5-5`); `GEN_PROVIDER=openai` with `GEN_BASE_URL` uses any OpenAI-compatible endpoint
(OpenRouter, Gemini, local Ollama). With no model configured the assistant still retrieves and shows the
matching passages.

## Tests and evaluation

```bash
cd backend
.venv/Scripts/python -m pytest -q            # 59 tests: units, API, authorisation matrix, isolation
.venv/Scripts/ruff check src tests
.venv/Scripts/python -m pocketprof.evaluation run --course 1 --split all             # full pipeline
.venv/Scripts/python -m pocketprof.evaluation run --course 1 --split all --retrieval-only
```

Baseline on the sample class (`ds-v1`, 40 queries, arm `shipped-v1`, OpenRouter
`minimax/minimax-m3.1-flash:free`, 21 Oct 2026):

| Metric | Retrieval only | Full pipeline |
|---|---|---|
| Decision accuracy (answer vs decline) | 87.5% | **97.5%** |
| Unsupported-answer rate (SRS target ≤ 5%) | 40% | **0%** |
| False-refusal rate (SRS target ≤ 10%) | 3.3% | 3.3% |
| Context recall@k | 1.0 | 1.0 |
| Scoped correctness (week/date questions) | 1.0 | 1.0 |
| Citation presence / support | – | 1.0 / 1.0 |
| Latency p50 / p95 | 22 / 27 ms | 1.3 / 3.1 s |

The single false refusal (q14, "What is linear probing?") scores 0.598 against τ = 0.60. τ is calibrated
in E3 on the dev split; q14 is in the test split and is deliberately not used for tuning (DQ-6).
`ds-v1` exercises the harness; the pilot query set D3 (150 queries, two annotators) replaces it.

## Deployment (Compose)

```bash
cp .env.example .env               # set JWT_SECRET, PSEUDONYM_KEY, POSTGRES_PASSWORD, Google and model settings
docker compose up -d --build       # web on :80, API docs on /api/docs
docker compose --profile private up -d   # + Ollama: real student data never leaves the machine (C-5)
docker compose --profile pilot up -d     # + Caddy TLS (NFR-09) and nightly pg_dump (NFR-23)
```

Services: `migrate` (one-shot), `api` (4 workers), `scheduler`, `web` (nginx + React build), `postgres`,
`qdrant`, and the optional `ollama`, `proxy` and `backup`. Across processes, one sync per course is
enforced with a PostgreSQL advisory lock. *Not yet verified on this machine: Docker is not installed on the
development laptop.*

## Repository layout

```
arms/                      configuration arms (YAML; hashed)
eval/querysets/            versioned query sets
backend/src/pocketprof/
  access/                  M8: JWT + rotating refresh tokens, Argon2id, AccessGuard, audit log
  ingestion/               M1: Classroom connector, uploads, parsers, chunker, calendar, ledger, ProvisioningJob
  retrieval/               M2: embedder, per-course VectorIndex, QueryScoper, Retriever
  generation/              M2/M7: LLMClient (Claude, OpenAI-compatible), PromptBuilder, citations, ChatResponder
  evaluation/              M9: query sets, metrics, ExperimentRunner, CLI
  api/                     /api/v1 routes, RFC 7807 errors, request IDs, metrics, rate limit
  db/                      SQLAlchemy models and Alembic migrations
web/src/                   React + TypeScript app
deploy/                    Dockerfiles, nginx, Caddy
```

## Deviations from SRS/SDS v1.0 (for the next revision)

1. **Source of material.** Google Classroom is the primary source (supervisor decision, 15 Oct 2026), with
   teacher uploads (FR-24) as the second source. SDS 4.2's folder repository becomes "source documents"
   from connectors. Week and date tags come from post dates and the term start, with an optional
   calendar CSV, instead of a mandatory `calendar.csv`.
2. **One React app** for students, teachers and administrators instead of React + Streamlit (SDS 3.2).
3. **Change detection** uses the source's own version marker first, then SHA-256 of the content
   (SDS Table 2 names SHA-256 only), so an unchanged Drive file is never downloaded.
4. **Hybrid retrieval** is the scope filter plus dense **and BM25 sparse** ranking fused by reciprocal rank;
   abstention still uses the dense cosine.
5. **Gold evidence** in query sets is phrase-matched instead of chunk-ID-matched, so one label set serves
   every chunking arm in E2.
6. **PDF parsing** uses PyMuPDF directly; the PyMuPDF4LLM wrapper's current PyPI release (2.0) ships no
   code and was not used.
7. **Embedding model** in development is bge-small-en-v1.5 (384-d); bge-base (768-d) remains the target arm.
8. **Teacher insights** list recent questions without identity, and suppress topic aggregates below the
   minimum cohort (C-8); the full cohort signal and CQI report are M6 (phase 2).
