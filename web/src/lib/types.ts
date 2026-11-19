export type Role = "student" | "teacher" | "admin";

export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
}

export interface Course {
  id: number;
  code: string;
  title: string;
  section: string | null;
  teacher_name: string;
  join_code: string | null;
  source: "google" | "upload";
  gc_link: string | null;
  status: "new" | "syncing" | "ready" | "error";
  status_msg: string | null;
  last_synced_at: string | null;
  n_chunks: number;
  n_materials: number;
  term_start: string | null;
  active_arm: string | null;
}

export interface Material {
  id: number;
  source: "google" | "upload";
  kind: string;
  post_title: string | null;
  topic: string | null;
  title: string;
  url: string | null;
  post_url: string | null;
  mime: string;
  published_at: string | null;
  week: number | null;
  status: "indexed" | "skipped" | "failed" | "withdrawn";
  note: string | null;
  n_chunks: number;
  ocr_pages: number;
}

export interface Run {
  id: number;
  trigger: string;
  arm: string;
  config_hash: string;
  status: "running" | "done" | "failed";
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  counts: Record<string, number>;
  error: string | null;
  items?: { outcome: string; title: string; ref: string; reason?: string; chunks?: number }[];
}

export interface Source {
  n: number;
  cited: boolean;
  kind: string | null;
  post_title: string | null;
  topic: string | null;
  file: string | null;
  location: string | null;
  week: number | null;
  date: string | null;
  url: string | null;
  snippet: string;
  score: number;
  ocr: boolean;
}

export interface Scope {
  kind: string;
  label: string;
  date: string | null;
  week: number | null;
  notes: string[];
}

export interface Citation {
  n: number;
  chunk_id: string;
  file: string | null;
  location: string | null;
  week: number | null;
  post_title: string | null;
  url: string | null;
}

export type Outcome = "answered" | "refused" | "fallback" | "smalltalk" | "error";

export interface ChatMessage {
  id?: number;
  role: "user" | "assistant";
  content: string;
  outcome?: Outcome;
  citations?: Citation[];
  sources?: Source[];
  scope?: Scope | null;
  scopeNotes?: string[];
  notice?: string | null;
  showPassages?: boolean;
  pending?: boolean;
  mode?: "direct" | "scaffolded";
}

export interface Thread {
  id: number;
  course_id: number;
  title: string;
  created_at: string;
  updated_at: string | null;
}

export interface Problem {
  title: string;
  detail: string;
  status: number;
  errors?: { field?: string; row?: number; message: string }[];
}

// ----- notes (M3) -----

export interface NoteSource {
  n: number;
  chunk_id: string | null;
  kind: string | null;
  file: string | null;
  location: string | null;
  week: number | null;
  date: string | null;
  post_title: string | null;
  topic: string | null;
  url: string | null;
  snippet: string;
}

export interface NoteSection {
  heading: string;
  body: string;
  citations: number[];
}

export interface NoteSummary {
  id: number;
  course_id: number;
  title: string;
  scope_label: string;
  outcome: "generated" | "refused" | "fallback";
  created_at: string;
}

export interface Note extends NoteSummary {
  selection: Record<string, string | number>;
  sections: NoteSection[];
  sources: NoteSource[];
  notice: string | null;
  truncated: boolean;
  model: string | null;
}

export interface NotesOptions {
  weeks: number[];
  lectures: number[];
  topics: string[];
  files: { id: number; title: string; week: number | null; topic: string | null; kind: string }[];
}

// ----- practice (M4) -----

export type ItemKind = "mcq" | "tf" | "short";
export type ItemStatus = "draft" | "validated" | "released" | "rejected" | "retired";

/** What a student sees before answering: no answer, no explanation, no source text. */
export interface PracticeCard {
  id: number;
  kind: ItemKind;
  question: string;
  options: string[] | null;
  topic: string;
  week: number | null;
  box: number | null;
  next_due: string | null;
  reason: "due" | "new" | "optional";
}

export interface MasteryRow {
  topic: string;
  estimate: number | null;
  attempts: number;
}

export interface PracticeView {
  session: PracticeCard[];
  optional: PracticeCard[];
  mastery: MasteryRow[];
  counts: { due: number; new: number; released: number };
  intervals_days: number[];
}

export interface AttemptResult {
  correct: boolean;
  self_marked: boolean;
  correct_answer: string;
  options: string[] | null;
  explanation: string | null;
  source: NoteSource;
  box: number;
  boxes: number;
  next_due: string;
  interval_days: number;
  mastery: MasteryRow;
}

/** The teacher's view of an item. */
export interface PracticeItem {
  id: number;
  course_id: number;
  kind: ItemKind;
  question: string;
  options: string[] | null;
  answer: string;
  explanation: string | null;
  topic: string | null;
  week: number | null;
  source: NoteSource;
  status: ItemStatus;
  reject_reason: string | null;
  edited: boolean;
  retired_reason: string | null;
  created_at: string;
  validated_at: string | null;
  released_at: string | null;
  model: string | null;
  stats: { suppressed: boolean; min_cohort?: number; students?: number; attempts?: number; accuracy?: number | null } | null;
}
