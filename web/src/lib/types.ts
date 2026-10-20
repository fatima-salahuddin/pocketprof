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
