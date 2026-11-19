import { useEffect, useState, type FormEvent } from "react";
import { Empty, ErrorNote, Page, Spinner, useLoad } from "../components/ui";
import { get, patch, post } from "../lib/api";
import { relativeTime } from "../lib/format";
import type { Course } from "../lib/types";

interface Rule { id?: string; text: string; origin?: string; enabled: boolean }
interface Exemplar { id: number; origin: "calibration" | "material"; prompt: string | null; text: string;
                     source_ref: string | null; status: "pending" | "approved" | "rejected" }
interface StyleView {
  profile: { status: "draft" | "active" | "off"; rules: Rule[]; measures: Record<string, number | boolean | null>;
             model: string | null; calibrated: boolean; answers: Record<string, string>; updated_at: string } | null;
  exemplars: Exemplar[];
  counts: { approved: number; pending: number; rejected: number };
  prompts: { id: string; prompt: string }[];
  min_answers: number;
}

const STATUS_LABEL = { draft: "Not in use yet", active: "In use", off: "Switched off" };
const MEASURE_LABEL: Record<string, string> = {
  samples: "Answers analysed", avg_sentence_words: "Words per sentence", bullet_share: "Lines that are bullets",
  questions_per_sample: "Questions to the student, per answer", examples_per_sample: "Examples, per answer",
  urdu_words: "Urdu words", big_o: "Uses Big-O",
};

/** M5 (FR-11; SRS UC-09): calibrate, review and switch on the teacher's explanation style. */
export default function TeacherStyle() {
  const style = useLoad(() => get<StyleView>("/teachers/me/style"), []);
  const v = style.data;
  const [error, setError] = useState<unknown>(null);

  async function run(fn: () => Promise<StyleView>) {
    setError(null);
    try {
      style.setData(await fn());
    } catch (err) {
      setError(err);
    }
  }

  if (style.error) return <Page><ErrorNote error={style.error} /></Page>;
  if (!v) return <Page><Spinner /></Page>;
  const p = v.profile;

  return (
    <Page>
      <div className="page-head">
        <div>
          <h1>Your teaching style</h1>
          <p className="sub">PocketProf can phrase answers, notes and practice questions the way you explain things.
            It changes wording and tone only: facts still come only from your class material, with sources.</p>
        </div>
        {p && (
          <div className="actions">
            <span className={`pill pill-${p.status === "active" ? "ready" : p.status === "draft" ? "new" : "skipped"}`}>
              {STATUS_LABEL[p.status]}</span>
            {p.status === "active"
              ? <button className="btn" onClick={() => run(() => patch("/teachers/me/style", { status: "off" }))}>Switch off</button>
              : <button className="btn primary" onClick={() => run(() => patch("/teachers/me/style", { status: "active" }))}>Use my style</button>}
          </div>
        )}
      </div>
      <ErrorNote error={error} />
      <Calibration view={v} onDone={(nv) => style.setData(nv)} />
      {p && (
        <>
          <Rules view={v} onSave={(rules) => run(() => patch("/teachers/me/style", { rules }))} />
          <Examples view={v} onReview={(id, status) => run(() => patch(`/teachers/me/style/exemplars/${id}`, { status }))} />
          <Preview active={p.status === "active"} />
        </>
      )}
    </Page>
  );
}

function Calibration({ view, onDone }: { view: StyleView; onDone: (v: StyleView) => void }) {
  const done = !!view.profile?.calibrated;
  const [open, setOpen] = useState(!done);
  const [answers, setAnswers] = useState<Record<string, string>>(view.profile?.answers ?? {});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const answered = Object.values(answers).filter((a) => a.trim().split(/\s+/).length >= 5).length;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const nv = await post<StyleView>("/teachers/me/calibration", {
        answers: Object.entries(answers).map(([prompt_id, answer]) => ({ prompt_id, answer })) });
      onDone(nv);
      setOpen(false);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card style-section">
      <div className="section-head">
        <div>
          <h2>1. Calibration</h2>
          <p className="sub">Answer at least {view.min_answers} of these the way you would in class or on Classroom.
            A few sentences each is enough; it takes about 15 minutes.</p>
        </div>
        {done && <button className="btn small" onClick={() => setOpen((o) => !o)}>{open ? "Hide" : "Redo calibration"}</button>}
      </div>
      {open && (
        <form className="form" onSubmit={submit}>
          {view.prompts.map((pr, i) => (
            <label key={pr.id}>{i + 1}. {pr.prompt}
              <textarea rows={3} value={answers[pr.id] ?? ""} maxLength={4000}
                        onChange={(e) => setAnswers((a) => ({ ...a, [pr.id]: e.target.value }))} />
            </label>
          ))}
          <ErrorNote error={error} />
          <div className="row-end">
            <span className="fine">{answered} of {view.prompts.length} answered</span>
            <button className="btn primary" disabled={busy || answered < view.min_answers}>
              {busy ? "Reading your writing..." : "Learn my style"}</button>
          </div>
        </form>
      )}
      {done && !open && <p className="fine">Calibrated {relativeTime(view.profile!.updated_at)}.</p>}
    </section>
  );
}

function Rules({ view, onSave }: { view: StyleView; onSave: (rules: Rule[]) => void }) {
  const [rules, setRules] = useState<Rule[]>(view.profile?.rules ?? []);
  useEffect(() => setRules(view.profile?.rules ?? []), [view.profile?.rules]);
  const m = view.profile?.measures ?? {};
  const set = (i: number, r: Partial<Rule>) => setRules((rs) => rs.map((x, j) => (j === i ? { ...x, ...r } : x)));
  return (
    <section className="card style-section">
      <h2>2. Your style rules</h2>
      <p className="sub">{view.profile?.model ? "Proposed from your answers. " : "Measured from your answers. "}
        Edit, switch off or add rules; they are sent with every request for your classes.</p>
      <ul className="rule-list">
        {rules.map((r, i) => (
          <li key={r.id ?? `new-${i}`}>
            <input type="checkbox" checked={r.enabled} aria-label={`Use rule ${i + 1}`} onChange={(e) => set(i, { enabled: e.target.checked })} />
            <input value={r.text} maxLength={240} aria-label={`Rule ${i + 1}`} onChange={(e) => set(i, { text: e.target.value })} />
            <button className="icon-btn" aria-label={`Remove rule ${i + 1}`} onClick={() => setRules((rs) => rs.filter((_, j) => j !== i))}>×</button>
          </li>
        ))}
      </ul>
      <div className="row">
        <button className="btn ghost small" onClick={() => setRules((rs) => [...rs, { text: "", enabled: true }])} disabled={rules.length >= 12}>Add a rule</button>
        <button className="btn primary small" onClick={() => onSave(rules.filter((r) => r.text.trim()))}>Save rules</button>
      </div>
      {Object.keys(m).length > 0 && (
        <details className="measures">
          <summary>What PocketProf measured in your writing</summary>
          <dl>{Object.entries(m).filter(([k]) => k in MEASURE_LABEL).map(([k, val]) => (
            <div key={k}><dt>{MEASURE_LABEL[k]}</dt><dd>{typeof val === "boolean" ? (val ? "yes" : "no") : val ?? "–"}</dd></div>
          ))}</dl>
        </details>
      )}
    </section>
  );
}

function Examples({ view, onReview }: { view: StyleView; onReview: (id: number, status: Exemplar["status"]) => void }) {
  const [tab, setTab] = useState<Exemplar["status"]>(view.counts.pending ? "pending" : "approved");
  const rows = view.exemplars.filter((e) => e.status === tab);
  return (
    <section className="card style-section">
      <h2>3. Examples of how you explain</h2>
      <p className="sub">The two or three closest to each question are shown to the model as wording examples. Passages
        found in your own material wait for your approval.</p>
      <div className="seg" role="tablist" aria-label="Examples by state">
        {(["pending", "approved", "rejected"] as const).map((s) => (
          <button key={s} role="tab" aria-selected={tab === s} className={tab === s ? "on" : ""} onClick={() => setTab(s)}>
            {{ pending: "From your material", approved: "In use", rejected: "Not used" }[s]} ({view.counts[s]})
          </button>
        ))}
      </div>
      {rows.length === 0 ? <Empty title="Nothing here" /> : (
        <ul className="exemplar-list">
          {rows.map((e) => (
            <li key={e.id}>
              <p className="fine">{e.origin === "calibration" ? `Your answer to: ${e.prompt}` : `From ${e.source_ref}`}</p>
              <blockquote>{e.text}</blockquote>
              <div className="row">
                {e.status !== "approved" && <button className="btn small primary" onClick={() => onReview(e.id, "approved")}>Use it</button>}
                {e.status !== "rejected" && <button className="btn small ghost" onClick={() => onReview(e.id, "rejected")}>Don't use</button>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Preview({ active }: { active: boolean }) {
  const courses = useLoad(() => get<Course[]>("/courses"), []);
  const [courseId, setCourseId] = useState<string>("");
  const [question, setQuestion] = useState("Explain this week's main idea");
  const [block, setBlock] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  async function show(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const r = await post<{ style_block: string }>("/teachers/me/style/preview", { course_id: Number(courseId), question });
      setBlock(r.style_block);
    } catch (err) {
      setError(err);
    }
  }
  return (
    <section className="card style-section">
      <h2>4. Preview</h2>
      <p className="sub">Exactly what is added to the request, after the rules that keep answers to your material.</p>
      <form className="upload" onSubmit={show}>
        <label>Class
          <select value={courseId} onChange={(e) => setCourseId(e.target.value)}>
            <option value="">Choose...</option>
            {courses.data?.map((c) => <option key={c.id} value={c.id}>{c.code} · {c.title}</option>)}
          </select>
        </label>
        <label className="grow">A student question<input value={question} onChange={(e) => setQuestion(e.target.value)} /></label>
        <button className="btn" disabled={!courseId || question.trim().length < 3}>Show</button>
      </form>
      <ErrorNote error={error} />
      {block !== null && (block ? <pre className="style-preview">{block}</pre>
        : <p className="note">{active ? "No rules are switched on." : "Nothing is added while your style is not in use."}</p>)}
    </section>
  );
}
