# PocketProf phase 2 handoff: M3, M4, M5, M7 (Rimsha)

Branch `phase2-m3-m4-m5-m7`, built on `main` (phase 1, Fatima). This document is for whoever reviews,
merges or continues this work: what was built, where it lives, what changed in shared code, how it was
checked, and what is still open. The M3 brief it started from is [M3_HANDOFF.md](M3_HANDOFF.md); connecting a
real Classroom class is in [GOOGLE_CLASSROOM_SETUP.md](GOOGLE_CLASSROOM_SETUP.md).

**Contents:** 1. Status · 2. Running and checking it · 3. The modules · 4. Data and configuration ·
5. Changes to phase-1 code · 6. Deviations from SRS/SDS v1.0 · 7. Evaluation so far · 8. Open work ·
9. Notes for teammates

---

## 1. Status

| Module | FRs | State |
|---|---|---|
| **M3** Notes and export | FR-8, FR-9 | Done. Notes by week, lecture, topic, free-text topic or file; PDF of a thread, notes or practice paper |
| **M4** Practice | FR-10, FR-17, FR-18 | Done. Generation from delivered material, teacher validation queue, Leitner spacing, mastery |
| **M5** Style adaptation | FR-11 | Done (code). Calibration, rules, exemplars, per-query selection; E4 human study not run |
| **M7** Interaction design | FR-14, UI-4, UC-02 6a | Done. Full-answer reveal, mode control by the message box, leak metric; E6 piloted (section 7) |
| Also | | Demo-account buttons on sign-in; week from Classroom topic, title or file name; chat token budget |

Checks on the branch head: **157 backend tests pass** (59 from phase 1 + 98 new), `ruff` clean, `tsc -b` and
`npm run build` clean. Checked in the browser with a real model (OpenRouter free tier) on the sample class and
on a real linked Google Classroom class (CS-2005 Database Systems, 11 posts).

## 2. Running and checking it

Setup and running are unchanged from the README and M3_HANDOFF section 2, with three notes:

- **Python 3.11 to 3.13.** 3.14 has no wheels for some dependencies (`py -3.13 -m venv .venv` on Windows).
- **New migrations 0002 to 0004** are applied automatically when the API starts.
- **The model in `.env.example` is retired.** `minimax/minimax-m3.1-flash:free` was withdrawn on 22 Oct 2026.
  Working free choice on 10 Nov 2026 (section 7 explains the testing):
  ```
  GEN_MODEL=nvidia/nemotron-3.5-super-vl:free
  GEN_FALLBACK_MODELS=nvidia/nemotron-3-ultra-550b-a55b:free,google/gemma-4-31b-it:free
  ```
  Avoid `nemotron-3.5-lightning` and `dots-3-note`: they spend the whole token budget on hidden reasoning and
  return empty text.

On the sign-in page in development, the **Admin / Teacher / Student** buttons fill in the seed accounts.

A walkthrough that touches every new feature (about 20 minutes, free model):

| As | Do | Expect |
|---|---|---|
| Student | Chat, **Step by step**: "I delete key 50 from a BST; the smallest key in its right subtree is 60. What replaces 50?" | A hint and a question, not "60" |
| Student | **Show me the full answer** | The complete answer |
| Student | **Download PDF** in a conversation | PDF with a numbered Sources list |
| Student | **Notes**, Week 5, Generate | 3 to 8 sections, every point cited |
| Student | Notes, Your own topic: "cricket" | "Nothing in the class material..." and no model call |
| Teacher | Class page, **Practice** tab, Week 3, Generate | Drafts beside their source passage; tab shows "(n to review)" |
| Teacher | Edit, approve, reject (reason required) | Items move to Live / Rejected |
| Student | **Practice**: answer wrong, then right | "1 day (box 1 of 5)", then "2 days (box 2 of 5)"; mastery changes |
| Teacher | **Teaching style**: answer 5 prompts, Learn my style, edit rules, Use my style, Preview | Proposed rules and exemplars; the exact STYLE block |

## 3. The modules

### M3 Notes and export (FR-8, FR-9)

Built as specified in M3_HANDOFF, with these choices where it left options open:

- **Selection** (`generation/notes.py`): `resolve_selection` + `collect_chunks` (shared with M4). Week, lecture,
  topic tag (any case) and file use a filtered scroll of the course's collection, in teaching order; any other
  topic text uses retrieval with `top_k` from the arm and refuses when retrieval abstains.
- **Budget:** chunks in teaching order up to `notes.word_budget` (6,000 words), always keeping the first chunk
  of every file; the note records `truncated`.
- **Grounding:** an empty selection refuses without a model call; the reply is parsed into `## ` sections;
  invalid tags are removed; **sections with no valid citation are dropped**; cited sources are renumbered 1..k
  in order of first use; no surviving section, a model failure, or no model means fail closed (the matching
  files and locations, with a notice).
- **PDF** (`exports/`): PyMuPDF `Story` (multi-page HTML flow). Everything is HTML-escaped before the small
  Markdown subset is turned into markup; one numbered Sources list per document (a thread's messages share it,
  markers rewritten to match); footer on every page. Builders are registered by kind (`thread`, `notes`,
  `paper`), so the route never changes.
- **API:** `GET /courses/{id}/notes/options`, `POST /courses/{id}/notes`, `GET /notes`, `GET|DELETE /notes/{id}`,
  `POST /exports`. Owner-only; enrolment re-checked; rate-limited like chat.
- **Web:** `pages/Notes.tsx`; `components/Sources.tsx` (the sources panel, moved out of `Chat.tsx`);
  `CourseNav` (Ask / Notes / Practice); PDF download via `api.download()`, which keeps the token private.

### M4 Practice (FR-10, FR-17, FR-18; SDS 4.5, 8.4)

- **Generation** (`practice/generator.py`, prompt `practice-v1`): MCQ, true/false and short answer from a week,
  topic or file. Only **delivered** material: posted on or before today (or, undated, its week has started).
  Announcements are excluded. The model returns JSON; every item is validated (four distinct options, answer
  A-D, a source tag that exists) and malformed items are **discarded, never repaired**. Each item stores its
  chunk ID, material ID and **file hash**. No model means 503 (there is nothing to fall back to).
- **Lifecycle** (`practice/lifecycle.py`): `DRAFT -> VALIDATED -> RELEASED`, `REJECTED` (reason required),
  `RETIRED`. Approve accepts edits through the same validator. Release (topic delivered) and retirement (source
  file withdrawn, or its hash no longer matches the ledger) are applied **on read**, so a student is never
  drilled on replaced content (UC-07 2a) and no scheduler job is needed.
- **Tracker** (`practice/tracker.py`, `practice/spacing.py`): five Leitner boxes with intervals from the arm
  (1, 2, 4, 8, 16 days); mastery is the recency-weighted share correct over the last 10 attempts
  (`w_k = 0.8^(k-1)`). Session: due items oldest first, then unseen items from the lowest-mastery topics; when
  nothing is due, optional items (UC-07 1a). Attempts, schedule and mastery are keyed by `student_ref` only.
  **Short answers are self-marked** against the model answer, so no score ever comes from the model.
- **Teacher stats:** per-item accuracy appears only once `analytics.min_cohort` distinct students attempted it.
- **API:** `POST /courses/{id}/practice/generate`, `GET /courses/{id}/validation`,
  `GET /courses/{id}/practice/items`, `PATCH /practice/{id}` (approve | reject | retire),
  `GET /courses/{id}/practice/due`, `POST /practice/{id}/attempts`, `POST /practice/{id}/reveal`; paper export
  via `POST /exports {kind: "paper", ref: course_id, topic?}` (staff get approved-but-unreleased items too).
- **Web:** `pages/Practice.tsx` (student: question card, feedback, spacing, mastery bars, source panel, paper
  PDF); `pages/TeacherPractice.tsx` (generator, review queue beside the source passage, edit/approve/reject/
  retire, cohort-safe stats). The queue count is in the class page's tab label (UI-8).

### M5 Style adaptation (FR-11; SDS Table 4, 4.4; UC-09; E4)

- **Calibration** (`style/service.py`): 12 prompts built from the teacher's own topic tags (logistics topics
  excluded); at least 5 answers of 5+ words. Answers become **approved** exemplars.
- **Mining:** explanatory passages (40 to 160 words, explanatory cues, no announcements or logistics) from the
  teacher's own courses become **pending** exemplars.
- **Rules:** 5 to 8 plain sentences about how the teacher explains, proposed by the model from the answers
  (`style-v1` prompt), or, without a model, derived from measurements (sentence length, bullets, questions,
  examples, second person, Urdu words, Big-O). Shown to the teacher with the measurements.
- **Review:** edit, add, disable rules; approve or reject exemplars; switch the profile on. **Nothing is used
  until the teacher switches it on.**
- **Use:** approved exemplars live in a per-teacher Qdrant collection `style_<teacher_id>`. Every chat, notes
  and practice request for that teacher's courses gets a STYLE block, **after the grounding rules and before
  the mode block** (SDS Appendix A order): a guard ("wording only; facts only from SOURCES"), the enabled
  rules, and the `k` nearest exemplars above `min_score`. Any failure falls back to no style.
- **E4 arms:** `style.mode` is `exemplars` (shipped), `preamble` (rules only, `e4-preamble`) or `none`
  (`e4-no-style`).
- **API:** `GET /teachers/me/style`, `GET|POST /teachers/me/calibration`, `PATCH /teachers/me/style`,
  `PATCH /teachers/me/style/exemplars/{id}`, `POST /teachers/me/style/preview`. **Web:** `pages/TeacherStyle.tsx`
  and a "Teaching style" link in the teacher's top bar.

### M7 Interaction design (FR-14, UI-4; UC-02 6a; E6)

- **Full answer one click away:** a "Show me the full answer" button under a step-by-step answer. The API takes
  `reveal: true`; the responder answers the thread's last real question in direct mode. The reveal turn is
  stored with mode `reveal` and is **not logged as a new question** for the teacher.
- **UI-4:** the mode control moved from the header to beside the message box.
- **Leak metric:** queries may carry `solution` phrases; the harness reports `solution_disclosure_rate`, and
  `leak_rate` when the arm answers in scaffolded mode. Query set `eval/querysets/modes-v1.yaml` (8 worked
  problems, version 1.1); arm `e6-scaffolded`.
- **Prompt `modes-v2`:** the scaffolded instruction is three explicit steps and forbids stating the result,
  and the scaffolded user turn ends with a one-line reminder. `MODES_VERSION` is recorded with every evaluation
  run (`prompt_version` = `cite-v3+modes-v2`); the grounding rules (`cite-v3`) are unchanged.

### Smaller additions

- **Demo accounts on the sign-in page:** `GET /auth/demo-accounts` returns the seed accounts and
  `SEED_PASSWORD` in `ENV=development` or `demo` only, never in a pilot (`DEMO_LOGINS=false` hides them).
- **Teaching week from labels** (`ingestion/calendar.py: declared_week`). Priority: a week typed on upload; a
  Classroom **topic** name containing "Week N"; a Material's, Assignment's or upload's **title or file name
  starting with** "Week N" (never announcements); then the post date. "Lecture N" sets the lecture number the
  same way. Re-tags existing points on the next sync without re-embedding. Needed because Classroom cannot
  backdate posts, so a class posted at once was all "week 1".

## 4. Data and configuration

**Migrations** (each module's own, all reversible, checked for drift against the models):
`0002_notes` (notes), `0003_practice` (practice_items, practice_schedule, practice_attempts, mastery),
`0004_style` (style_profiles, style_exemplars).

**Arms** (`arms/*.yaml`, `arms.py`), new sections with defaults:

```yaml
notes:    {word_budget: 6000, max_tokens: 1800, topic_top_k: 12, min_sections: 1}
practice: {intervals_days: [1, 2, 4, 8, 16], mastery_window: 10, mastery_decay: 0.8, max_generate: 10,
           word_budget: 3500, max_tokens: 2500, session_size: 10}
style:    {mode: exemplars, k: 3, min_score: 0.35}
```

New arms: `e6-scaffolded` (default mode scaffolded), `e4-preamble`, `e4-no-style`. **Chat `max_tokens` is 1500 in
every arm** (was 700; section 7). These change each arm's `config_hash`, so runs before and after this branch
have different hashes by design.

**Settings:** `SEED_PASSWORD` is now read through the settings, so `.env` is respected (the seed script used to
read only the process environment); `DEMO_LOGINS` (optional).

## 5. Changes to phase-1 code (please review)

| File | Change | Why |
|---|---|---|
| `generation/prompts.py` | `format_payload_sources()`; `style_block` passed through `build()`; reveal mode; `modes-v2` scaffolded text and reminder; notes and practice prompts | One SOURCES format for every mode; M5 hook; M7 |
| `generation/responder.py` | `reveal` handling; STYLE block | M7, M5 |
| `api/routes_chat.py`, `schemas.py` | `reveal` field (422 without a thread, checked before streaming) | M7 |
| `retrieval/vector_index.py` | `delete_where()` | Embedded Qdrant brings old points back after drop + re-create (section 9) |
| `ingestion/calendar.py`, `provisioning.py` | `declared_week/lecture` used in `_sync_metadata` | Weeks from labels |
| `evaluation/*` | `solution` labels, disclosure and leak metrics, `MODES_VERSION` in provenance | M7 / E6 |
| `config.py`, `seed.py`, `routes_auth.py` | `seed_password`, `demo_logins`, `/auth/demo-accounts` | Demo buttons; `.env` bug |
| `web/src/pages/Chat.tsx` | Course tabs, thread PDF, reveal button, mode control by the composer | M3, M7, UI-4 |
| `web/src/styles.css` | New styles; **fix**: stacked headers on phones left a 280 px gap (flex-basis became height) | Phone layout |
| `tests/conftest.py` | Shared fixtures: `FakeLLM`, `BrokenLLM`, `peer`, `outsider`, `other_teacher` | Phase-2 tests |

## 6. Deviations from SRS/SDS v1.0 (for the next revision)

Continuing the README's list:

9. **Short-answer practice items are self-marked** by the student against the model answer (FR-18 marks
   attempts; the SDS does not say how). Keeps C-3: no score comes from the model.
10. **Release and retirement of practice items happen on read**, not by a scheduled job (SDS 8.4 names the
    transitions, not the trigger).
11. **Style rules are stored in the database; only exemplars are in `style_<teacher_id>`** (SDS Table 4 lists
    both kinds in the collection). Rules are all applied, so they need no vector search.
12. **Response-mode prompts are versioned separately** (`MODES_VERSION`) from the grounding prompt variant.
13. **Weeks also come from the teacher's labels** (topic name, or a material's title or file name), ahead of
    the post date (SDS 4.2 tags weeks from the calendar only).
14. **Notes accept a free-text topic** (retrieval, refusing on abstention) in addition to week, lecture, topic
    tag and file (SDS Table 5 lists week, lecture or topic).

## 7. Evaluation so far

**E6 pilot, solution leakage** (`modes-v1`, 8 worked problems, sample class,
`nvidia/nemotron-3.5-super-vl:free`, 10 Nov 2026):

| Arm | Worked problems answered | Result stated | |
|---|---|---|---|
| `shipped-v1` (direct) | 6 | 5 (83%) | expected: direct answers in full |
| `e6-scaffolded`, modes-v1 prompt | 6 | 3 (**50% leak**) | the defect the AI guardrail names |
| `e6-scaffolded`, modes-v2 prompt | 7 | 0 (**0% leak**) | after the prompt change |

Small n, free-tier model, and some rate-limit fallbacks: indicative, not E6. Reading the answers also found a
false positive in the first labels (w01 matched the rule text itself), fixed in query set 1.1.

**Real-model spot checks** (M3_HANDOFF step 9): week 5 notes on the sample class, every bullet supported by its
cited slide; four generated Week 4 practice questions, all correct and grounded; on the real CS-2005 Classroom
class, "When is quiz 1?" gave the postponed date with both announcements cited, and "What did we cover in week
5?" a full cited summary.

**Model findings:** with this free reasoning model, chat at `max_tokens: 700` was cut off mid-answer (twice)
or returned nothing (once) on a week summary; at 1500 it finished in 979 tokens. Free models change often:
re-check before a demonstration.

## 8. Open work

- **E4** (style): needs a teacher's calibration, 15 held-out genuine explanations and two annotators
  (preference above chance with kappa >= 0.40). The arms and the preview are ready.
- **E6** proper: a larger worked-problem set, human usefulness ratings and confidence intervals. The leak check
  should run before every demonstration:
  `python -m pocketprof.evaluation run --course 1 --arm e6-scaffolded --query-set modes-v1 --split all`.
- **M6 (Hamna):** `practice_attempts` (course, `student_ref`, topic, week, correct) is ready for the practice
  accuracy part of FR-19's weekly signal.
- **Not verified:** the Compose/PostgreSQL deployment (only SQLite and embedded Qdrant were used); PDF fonts
  inside the Docker image; OCR for scanned PDFs (not installed, so such files are reported as skipped).
- **Small UI follow-up:** citations of Classroom announcements are labelled "Post text"; showing the post title
  would read better.

## 9. Notes for teammates

- **Embedded Qdrant: drop + re-create brings the old points back.** Deleting a collection and creating one with
  the same name in the same process returned the deleted points (reproduced in isolation). M5 avoids it with
  `delete_where()`. `VectorIndex.ensure()` still drops and re-creates when the embedding dimension changes,
  so after an embedding-arm change in development a stale index is possible; a server Qdrant may behave
  differently.
- **Google testing-mode tokens expire every 7 days:** Disconnect, then Connect Google Classroom again. The
  redirect URI must be `http://localhost:8000/api/v1/google/callback` (the prototype used
  `/api/google/callback`).
- **`web/tsconfig.tsbuildinfo` is tracked** and changes on every `tsc -b`; it was left out of these commits.
- **Log extras:** do not use `created`, `msg` or other `LogRecord` attribute names as `extra=` keys (one crashed
  a request during development).
