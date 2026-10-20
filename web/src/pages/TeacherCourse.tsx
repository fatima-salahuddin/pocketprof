import { Fragment, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Empty, ErrorNote, Page, Spinner, StatusPill, useLoad } from "../components/ui";
import { del, get, patch, post, put } from "../lib/api";
import { plural, relativeTime, shortDate } from "../lib/format";
import type { Course, Material, Run } from "../lib/types";

type Tab = "material" | "runs" | "questions" | "calendar" | "settings";

export default function TeacherCourse() {
  const id = Number(useParams().courseId);
  const course = useLoad(() => get<Course>(`/courses/${id}`), [id]);
  const [tab, setTab] = useState<Tab>("material");
  const [syncError, setSyncError] = useState<unknown>(null);
  const c = course.data;

  useEffect(() => {
    if (c?.status !== "syncing" && c?.status !== "new") return;
    const t = setTimeout(course.reload, 2000);
    return () => clearTimeout(t);
  }, [c]);

  async function sync(full = false) {
    setSyncError(null);
    try {
      await post(`/courses/${id}/sync${full ? "?full=true" : ""}`);
      course.reload();
    } catch (e) {
      setSyncError(e);
    }
  }

  if (course.error) return <Page><ErrorNote error={course.error} /></Page>;
  if (!c) return <Page><Spinner /></Page>;

  return (
    <Page>
      <p className="crumbs"><Link to="/teacher">My classes</Link> / {c.code}</p>
      <div className="page-head">
        <div>
          <h1>{c.code} · {c.title}</h1>
          <p className="sub">
            <StatusPill status={c.status} /> {c.status === "syncing" ? c.status_msg :
              `${c.status_msg ?? ""} · ${plural(c.n_chunks, "passage")} · updated ${relativeTime(c.last_synced_at)}`}
          </p>
          <p className="sub">Students join with <span className="mono">{c.join_code}</span>
            {c.gc_link && <> · <a href={c.gc_link} target="_blank" rel="noreferrer">Open on Classroom</a></>}</p>
        </div>
        <div className="actions">
          <Link className="btn" to={`/courses/${c.id}`}>Try it as a student</Link>
          <button className="btn primary" onClick={() => sync()} disabled={c.status === "syncing"}>
            {c.status === "syncing" ? "Reading..." : "Sync now"}
          </button>
        </div>
      </div>
      <ErrorNote error={syncError} />
      {c.source === "google" && (
        <p className="fine">New posts on Classroom are picked up automatically within about a minute; "Sync now"
          only makes it immediate.</p>
      )}

      <nav className="tabs" role="tablist">
        {([["material", "What PocketProf read"], ["runs", "Sync reports"], ["questions", "Student questions"],
           ["calendar", "Calendar"], ["settings", "Settings"]] as [Tab, string][]).map(([t, label]) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
            {label}
          </button>
        ))}
      </nav>

      {tab === "material" && <MaterialTab course={c} onChange={course.reload} />}
      {tab === "runs" && <RunsTab courseId={c.id} />}
      {tab === "questions" && <QuestionsTab courseId={c.id} />}
      {tab === "calendar" && <CalendarTab courseId={c.id} />}
      {tab === "settings" && <SettingsTab course={c} onSaved={course.reload} />}
    </Page>
  );
}

function MaterialTab({ course, onChange }: { course: Course; onChange: () => void }) {
  const mats = useLoad(() => get<Material[]>(`/courses/${course.id}/materials`), [course.id, course.last_synced_at, course.status]);
  const [filter, setFilter] = useState<"all" | "problems">("all");
  const fileRef = useRef<HTMLInputElement>(null);
  const [topic, setTopic] = useState("");
  const [week, setWeek] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function upload(e: FormEvent) {
    e.preventDefault();
    const files = fileRef.current?.files;
    if (!files?.length) return;
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    if (topic) fd.append("topic", topic);
    if (week) fd.append("week", week);
    setBusy(true);
    setError(null);
    try {
      await post(`/courses/${course.id}/materials`, fd);
      if (fileRef.current) fileRef.current.value = "";
      onChange();
      mats.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function withdraw(m: Material) {
    if (!confirm(`Withdraw "${m.title}"? Students will no longer get answers from it.`)) return;
    await del(`/courses/${course.id}/materials/${m.id}`);
    onChange();
    mats.reload();
  }

  const rows = (mats.data ?? []).filter((m) => filter === "all" || m.status === "failed" || m.status === "skipped");
  const counts = (mats.data ?? []).reduce<Record<string, number>>((a, m) => ({ ...a, [m.status]: (a[m.status] ?? 0) + 1 }), {});

  return (
    <section>
      <form className="card upload" onSubmit={upload}>
        <label className="grow">Add files (PDF, PPTX, DOCX, text; up to 50 MB each)
          <input ref={fileRef} type="file" multiple accept=".pdf,.pptx,.docx,.txt,.md,.csv" /></label>
        <label>Topic<input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="e.g. Heaps" /></label>
        <label>Week<input type="number" min={1} max={30} value={week} onChange={(e) => setWeek(e.target.value)} /></label>
        <button className="btn primary" disabled={busy}>{busy ? "Uploading..." : "Upload"}</button>
        <ErrorNote error={error} />
      </form>

      <div className="table-tools">
        <p className="sub">{counts.indexed ?? 0} indexed · {counts.skipped ?? 0} skipped · {counts.failed ?? 0} failed
          {counts.withdrawn ? ` · ${counts.withdrawn} withdrawn` : ""}</p>
        <div className="seg">
          <button className={filter === "all" ? "on" : ""} onClick={() => setFilter("all")}>All</button>
          <button className={filter === "problems" ? "on" : ""} onClick={() => setFilter("problems")}>Not read</button>
        </div>
      </div>
      {!mats.data ? <Spinner /> : rows.length === 0 ? <Empty title="Nothing here yet" /> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Material</th><th>Post</th><th>Week</th><th>Status</th><th>Passages</th><th /></tr></thead>
            <tbody>
              {rows.map((m) => (
                <tr key={m.id} className={m.status === "withdrawn" ? "dim" : ""}>
                  <td>
                    {m.url ? <a href={m.url} target="_blank" rel="noreferrer">{m.title}</a> : m.title}
                    <div className="muted small">{m.kind}{m.published_at ? ` · ${shortDate(m.published_at)}` : ""}</div>
                  </td>
                  <td>{m.post_url ? <a href={m.post_url} target="_blank" rel="noreferrer">{m.post_title}</a> : m.post_title}
                    {m.topic && <div className="muted small">{m.topic}</div>}</td>
                  <td>{m.week ?? "–"}</td>
                  <td><StatusPill status={m.status} />{m.note && <div className="muted small">{m.note}</div>}</td>
                  <td>{m.n_chunks || "–"}</td>
                  <td>{m.source === "upload" && m.status !== "withdrawn" &&
                    <button className="btn ghost small" onClick={() => withdraw(m)}>Withdraw</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function RunsTab({ courseId }: { courseId: number }) {
  const runs = useLoad(() => get<Run[]>(`/courses/${courseId}/runs`), [courseId]);
  const [open, setOpen] = useState<Run | null>(null);
  useEffect(() => {
    if (!runs.data?.some((r) => r.status === "running")) return;
    const t = setTimeout(runs.reload, 2000);
    return () => clearTimeout(t);
  }, [runs.data]);
  async function show(r: Run) {
    setOpen(open?.id === r.id ? null : await get<Run>(`/courses/${courseId}/runs/${r.id}`));
  }
  if (!runs.data) return <Spinner />;
  return (
    <section>
      <p className="sub">Every sync writes a report: what was read, what was unchanged and skipped, and why.</p>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>When</th><th>Trigger</th><th>Read</th><th>Unchanged</th><th>Skipped</th><th>Failed</th>
            <th>Withdrawn</th><th>Time</th><th>Status</th></tr></thead>
          <tbody>
            {runs.data.map((r) => (
              <Fragment key={r.id}>
                <tr className="clickable" onClick={() => show(r)}>
                  <td>{relativeTime(r.started_at)}</td><td>{r.trigger}</td><td>{r.counts.parsed ?? 0}</td>
                  <td>{r.counts.unchanged ?? 0}</td><td>{r.counts.skipped ?? 0}</td><td>{r.counts.failed ?? 0}</td>
                  <td>{r.counts.withdrawn ?? 0}</td>
                  <td>{r.duration_ms != null ? `${(r.duration_ms / 1000).toFixed(1)} s` : "–"}</td>
                  <td><StatusPill status={r.status} /></td>
                </tr>
                {open?.id === r.id && (
                  <tr className="detail"><td colSpan={9}>
                    <p className="muted small">Arm {r.arm} · config {r.config_hash.slice(0, 12)}</p>
                    {r.error && <p className="note error">{r.error}</p>}
                    <ul className="run-items">
                      {open.items?.filter((i) => i.outcome !== "unchanged").map((i, k) => (
                        <li key={k}><StatusPill status={i.outcome === "parsed" || i.outcome === "ocr" ? "indexed" : i.outcome} />{" "}
                          {i.title}{i.chunks ? ` (${plural(i.chunks, "passage")})` : ""}{i.reason ? ` · ${i.reason}` : ""}</li>
                      ))}
                      {open.items?.every((i) => i.outcome === "unchanged") && <li className="muted">Nothing changed.</li>}
                    </ul>
                  </td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

interface QRow { question: string; outcome: string; week: number | null; topic: string | null; ts: string }
interface Signal { min_cohort: number; total_questions: number; total_students: number;
                   rows: { week: number | null; topic: string; questions: number; students: number; refusal_rate: number }[] }

function QuestionsTab({ courseId }: { courseId: number }) {
  const qs = useLoad(() => get<QRow[]>(`/courses/${courseId}/questions`), [courseId]);
  const sig = useLoad(() => get<Signal>(`/courses/${courseId}/signal`), [courseId]);
  const label: Record<string, string> = { answered: "answered", refused: "not in material", fallback: "model unavailable",
                                          smalltalk: "greeting" };
  return (
    <section>
      <div className="card privacy-note">
        <strong>Anonymous by design.</strong> You see what was asked, never who asked it. Topic summaries appear
        only once at least {sig.data?.min_cohort ?? 5} different students have asked about a topic.
      </div>
      <h2>Topics students ask about</h2>
      {!sig.data ? <Spinner /> : sig.data.rows.length === 0 ? (
        <Empty title="Not enough students yet">
          {sig.data.total_questions} questions from {plural(sig.data.total_students, "student")} so far. Summaries need
          at least {sig.data.min_cohort} students per topic.
        </Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Week</th><th>Topic</th><th>Questions</th><th>Students</th><th>Not covered</th></tr></thead>
            <tbody>{sig.data.rows.map((r, i) => (
              <tr key={i}><td>{r.week ?? "–"}</td><td>{r.topic}</td><td>{r.questions}</td><td>{r.students}</td>
                <td>{Math.round(r.refusal_rate * 100)}%</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
      <h2>Recent questions</h2>
      <p className="sub">Questions the material didn't cover show you what to post next.</p>
      {!qs.data ? <Spinner /> : qs.data.length === 0 ? <Empty title="No questions yet" /> : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Question</th><th>Answered from material</th><th>Week</th><th>When</th></tr></thead>
            <tbody>{qs.data.map((q, i) => (
              <tr key={i}><td>{q.question}</td>
                <td><span className={`pill pill-${q.outcome === "answered" ? "indexed" : q.outcome === "refused" ? "skipped" : "new"}`}>
                  {label[q.outcome] ?? q.outcome}</span></td>
                <td>{q.week ?? "–"}</td><td>{relativeTime(q.ts)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}

interface Cal { term_start: string | null; current_week: number | null;
                days: { date: string; week: number; lecture_no: number | null; topics: string | null }[] }

function CalendarTab({ courseId }: { courseId: number }) {
  const cal = useLoad(() => get<Cal>(`/courses/${courseId}/calendar`), [courseId]);
  const fileRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);
  async function upload(e: FormEvent) {
    e.preventDefault();
    const f = fileRef.current?.files?.[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    setError(null);
    try {
      const r = await put<{ days: number }>(`/courses/${courseId}/calendar`, fd);
      setDone(`Imported ${plural(r.days, "teaching day")}.`);
      cal.reload();
    } catch (err) {
      setError(err);
    }
  }
  return (
    <section>
      <p className="sub">Week numbers let students ask "what did we cover in week 5?" or "last week".
        {cal.data?.term_start ? ` Week 1 starts ${shortDate(cal.data.term_start)}; this is week ${cal.data.current_week ?? "–"}.`
          : " Set the term start in Settings, or upload a calendar."}</p>
      <form className="card upload" onSubmit={upload}>
        <label className="grow">Calendar CSV with columns <span className="mono">date, week, lecture_no, topics</span>
          <input ref={fileRef} type="file" accept=".csv" /></label>
        <button className="btn primary">Upload calendar</button>
        <ErrorNote error={error} />
        {done && <p className="note success">{done}</p>}
      </form>
      {cal.data && cal.data.days.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Date</th><th>Week</th><th>Lecture</th><th>Topics</th></tr></thead>
            <tbody>{cal.data.days.map((d) => (
              <tr key={d.date}><td>{shortDate(d.date)}</td><td>{d.week}</td><td>{d.lecture_no ?? "–"}</td><td>{d.topics}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function SettingsTab({ course, onSaved }: { course: Course; onSaved: () => void }) {
  const nav = useNavigate();
  const [title, setTitle] = useState(course.title);
  const [termStart, setTermStart] = useState(course.term_start ?? "");
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await patch(`/courses/${course.id}`, { title, term_start: termStart || null });
      setSaved(true);
      onSaved();
    } catch (err) {
      setError(err);
    }
  }
  async function remove() {
    if (!confirm(`Delete ${course.code} from PocketProf? Students lose access; Google Classroom is not changed.`)) return;
    await del(`/courses/${course.id}`);
    nav("/teacher");
  }
  return (
    <section className="settings">
      <form className="card form" onSubmit={save}>
        <label>Title<input value={title} onChange={(e) => { setTitle(e.target.value); setSaved(false); }} /></label>
        <label>Term starts (week 1)<input type="date" value={termStart}
               onChange={(e) => { setTermStart(e.target.value); setSaved(false); }} /></label>
        <ErrorNote error={error} />
        <button className="btn primary">{saved ? "Saved" : "Save"}</button>
      </form>
      <div className="card danger">
        <h3>Remove from PocketProf</h3>
        <p className="sub">Deletes PocketProf's copy of the material and its conversations. Nothing changes on Google Classroom.</p>
        <button className="btn danger" onClick={remove}>Delete course</button>
      </div>
    </section>
  );
}
