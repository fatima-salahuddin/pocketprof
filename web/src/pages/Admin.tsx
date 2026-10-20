import { useEffect, useRef, useState, type FormEvent } from "react";
import { ErrorNote, Page, Spinner, StatusPill, useLoad } from "../components/ui";
import { get, patch, post, put } from "../lib/api";
import { relativeTime } from "../lib/format";
import type { Course, Role, User } from "../lib/types";

type Tab = "courses" | "users" | "evaluation" | "audit";

interface AdminUser extends User { is_active: boolean; created_at: string; last_login_at: string | null }
interface Arm { arm: string; description: string; config_hash: string; index_version: string; config: Record<string, any> }
interface EvalRun { id: number; experiment: string; arm: string; config_hash: string; course_id: number; split: string;
                    model: string; status: string; started_at: string; metrics: Record<string, any> }

export default function Admin() {
  const [tab, setTab] = useState<Tab>("courses");
  return (
    <Page wide>
      <div className="page-head">
        <div>
          <h1>Administration</h1>
          <p className="sub">Accounts, course provisioning, configuration arms and evaluation runs.</p>
        </div>
      </div>
      <nav className="tabs" role="tablist">
        {([["courses", "Courses"], ["users", "Users"], ["evaluation", "Evaluation"], ["audit", "Audit log"]] as [Tab, string][])
          .map(([t, label]) => (
            <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
              {label}
            </button>
          ))}
      </nav>
      {tab === "courses" && <CoursesTab />}
      {tab === "users" && <UsersTab />}
      {tab === "evaluation" && <EvalTab />}
      {tab === "audit" && <AuditTab />}
    </Page>
  );
}

function CoursesTab() {
  const courses = useLoad(() => get<Course[]>("/admin/courses"));
  const arms = useLoad(() => get<Arm[]>("/admin/arms"));
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (!courses.data?.some((c) => c.status === "syncing")) return;
    const t = setTimeout(courses.reload, 2500);
    return () => clearTimeout(t);
  }, [courses.data]);
  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      courses.reload();
    } catch (e) {
      setError(e);
    }
  }
  if (!courses.data || !arms.data) return <Spinner />;
  return (
    <section>
      <ErrorNote error={error} />
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Course</th><th>Teacher</th><th>Source</th><th>Status</th><th>Passages</th><th>Arm</th><th /></tr></thead>
          <tbody>
            {courses.data.map((c) => (
              <tr key={c.id}>
                <td><strong>{c.code}</strong><div className="muted small">{c.title}</div></td>
                <td>{c.teacher_name}</td>
                <td>{c.source === "google" ? "Classroom" : "Uploads"}</td>
                <td><StatusPill status={c.status} /><div className="muted small">{relativeTime(c.last_synced_at)}</div></td>
                <td>{c.n_chunks}</td>
                <td>
                  <select value={c.active_arm ?? ""} aria-label={`Configuration arm for ${c.code}`}
                          onChange={(e) => act(() => put(`/admin/courses/${c.id}/config`, { arm: e.target.value }))}>
                    {arms.data!.map((a) => <option key={a.arm} value={a.arm}>{a.arm}</option>)}
                  </select>
                </td>
                <td className="nowrap">
                  <button className="btn small" onClick={() => act(() => post(`/admin/courses/${c.id}/provision`))}>Provision</button>{" "}
                  <button className="btn ghost small" title="Re-read and re-embed everything"
                          onClick={() => act(() => post(`/admin/courses/${c.id}/provision?full=true`))}>Full rebuild</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>Configuration arms</h2>
      <p className="sub">An arm fixes every tunable setting; its hash is recorded with every sync and evaluation run.</p>
      <div className="arm-grid">
        {arms.data.map((a) => (
          <div key={a.arm} className="card arm">
            <h3>{a.arm}</h3>
            <p className="sub">{a.description}</p>
            <p className="mono small">hash {a.config_hash.slice(0, 16)} · index {a.index_version}</p>
            <dl>
              <dt>Chunking</dt><dd>{a.config.chunking.splitter}, {a.config.chunking.size} tokens, {a.config.chunking.overlap * 100}% overlap</dd>
              <dt>Retrieval</dt><dd>{a.config.retrieval.mode}, top {a.config.retrieval.top_k}, τ {a.config.retrieval.tau}</dd>
              <dt>Embedding</dt><dd>{a.config.embedding.model}</dd>
              <dt>Prompt</dt><dd>{a.config.prompt.variant}, default {a.config.response.default_mode}</dd>
            </dl>
          </div>
        ))}
      </div>
    </section>
  );
}

function UsersTab() {
  const users = useLoad(() => get<AdminUser[]>("/admin/users"));
  const [form, setForm] = useState({ name: "", email: "", role: "teacher" as Role, password: "" });
  const [error, setError] = useState<unknown>(null);
  const [rosterMsg, setRosterMsg] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function create(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await post("/admin/users", form);
      setForm({ name: "", email: "", role: "teacher", password: "" });
      users.reload();
    } catch (err) {
      setError(err);
    }
  }
  async function roster(e: FormEvent) {
    e.preventDefault();
    const f = fileRef.current?.files?.[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    setError(null);
    try {
      const r = await post<{ created: number; existing: number; enrolled: number }>("/admin/roster", fd);
      setRosterMsg(`${r.created} created, ${r.existing} already existed, ${r.enrolled} enrolled.`);
      users.reload();
    } catch (err) {
      setError(err);
    }
  }
  async function toggle(u: AdminUser) {
    try {
      await patch(`/admin/users/${u.id}`, { is_active: !u.is_active });
      users.reload();
    } catch (err) {
      setError(err);
    }
  }

  return (
    <section>
      <div className="two-col">
        <form className="card form" onSubmit={create}>
          <h2>Add an account</h2>
          <label>Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required minLength={2} /></label>
          <label>Email<input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required /></label>
          <label>Role
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
              <option value="teacher">Teacher</option><option value="student">Student</option><option value="admin">Administrator</option>
            </select>
          </label>
          <label>Initial password<input type="password" value={form.password} minLength={8} required
                 onChange={(e) => setForm({ ...form, password: e.target.value })} autoComplete="new-password" /></label>
          <button className="btn primary">Create account</button>
        </form>
        <form className="card form" onSubmit={roster}>
          <h2>Import a roster</h2>
          <p className="sub">CSV with <span className="mono">email, name, role, password, course_code</span>. Every row is
            checked first; nothing is imported if any row is invalid.</p>
          <input ref={fileRef} type="file" accept=".csv" />
          <button className="btn">Import</button>
          {rosterMsg && <p className="note success">{rosterMsg}</p>}
        </form>
      </div>
      <ErrorNote error={error} />
      {!users.data ? <Spinner /> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Last sign-in</th><th>Status</th><th /></tr></thead>
            <tbody>{users.data.map((u) => (
              <tr key={u.id} className={u.is_active ? "" : "dim"}>
                <td>{u.name}</td><td>{u.email}</td><td><span className="role-tag">{u.role}</span></td>
                <td>{relativeTime(u.last_login_at)}</td><td>{u.is_active ? "Active" : "Deactivated"}</td>
                <td><button className="btn ghost small" onClick={() => toggle(u)}>{u.is_active ? "Deactivate" : "Reactivate"}</button></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}

const METRICS: [string, string, boolean][] = [
  ["decision_accuracy", "Decision accuracy", true], ["unsupported_answer_rate", "Unsupported answers", true],
  ["false_refusal_rate", "False refusals", true], ["context_recall_at_k", "Context recall@k", true],
  ["scoped_correctness", "Scoped correctness", true], ["citation_presence", "Citation presence", true],
  ["end_to_end_ms_p50", "Latency p50 (ms)", false],
];

function EvalTab() {
  const runs = useLoad(() => get<EvalRun[]>("/admin/eval/runs"));
  const courses = useLoad(() => get<Course[]>("/admin/courses"));
  const arms = useLoad(() => get<Arm[]>("/admin/arms"));
  const [form, setForm] = useState({ course_id: 0, arm: "shipped-v1", split: "dev", generate: true });
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (courses.data?.length && !form.course_id) setForm((f) => ({ ...f, course_id: courses.data![0].id }));
  }, [courses.data]);
  useEffect(() => {
    if (!runs.data?.some((r) => r.status === "running")) return;
    const t = setTimeout(runs.reload, 3000);
    return () => clearTimeout(t);
  }, [runs.data]);
  async function start(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await post("/admin/eval/runs", { ...form, query_set: "ds-v1", experiment: "regression" });
      runs.reload();
    } catch (err) {
      setError(err);
    }
  }
  const fmt = (v: any, pct: boolean) => v == null ? "–" : pct ? `${(v * 100).toFixed(1)}%` : String(v);
  return (
    <section>
      <form className="card form inline-form" onSubmit={start}>
        <h2>Run the query set</h2>
        <p className="sub">Runs the versioned query set through the production pipeline and stores every metric with
          the arm's hash, the model, the prompt and the index version.</p>
        <div className="grid4">
          <label>Course<select value={form.course_id} onChange={(e) => setForm({ ...form, course_id: Number(e.target.value) })}>
            {courses.data?.map((c) => <option key={c.id} value={c.id}>{c.code}</option>)}</select></label>
          <label>Arm<select value={form.arm} onChange={(e) => setForm({ ...form, arm: e.target.value })}>
            {arms.data?.map((a) => <option key={a.arm}>{a.arm}</option>)}</select></label>
          <label>Split<select value={form.split} onChange={(e) => setForm({ ...form, split: e.target.value })}>
            <option value="dev">dev</option><option value="test">test</option><option value="all">all</option></select></label>
          <label className="check"><input type="checkbox" checked={form.generate}
            onChange={(e) => setForm({ ...form, generate: e.target.checked })} /> Include generation</label>
        </div>
        <ErrorNote error={error} />
        <button className="btn primary">Start run</button>
      </form>
      {!runs.data ? <Spinner /> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Run</th><th>Arm</th><th>Split</th><th>Model</th>{METRICS.map(([, l]) => <th key={l}>{l}</th>)}<th>Status</th></tr></thead>
            <tbody>{runs.data.map((r) => (
              <tr key={r.id}>
                <td>#{r.id}<div className="muted small">{relativeTime(r.started_at)}</div></td>
                <td>{r.arm}<div className="mono small">{r.config_hash.slice(0, 10)}</div></td>
                <td>{r.split}</td><td className="small">{r.model}</td>
                {METRICS.map(([k, , pct]) => <td key={k}>{fmt(r.metrics?.[k], pct)}</td>)}
                <td><StatusPill status={r.status} /></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}

interface Audit { id: number; actor: string | null; action: string; target: string | null; detail: any; ts: string }

function AuditTab() {
  const rows = useLoad(() => get<Audit[]>("/admin/audit"));
  if (!rows.data) return <Spinner />;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead>
        <tbody>{rows.data.map((a) => (
          <tr key={a.id}><td>{relativeTime(a.ts)}</td><td>{a.actor ?? "system"}</td><td className="mono small">{a.action}</td>
            <td className="mono small">{a.target}</td><td className="small">{a.detail ? JSON.stringify(a.detail) : ""}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}
