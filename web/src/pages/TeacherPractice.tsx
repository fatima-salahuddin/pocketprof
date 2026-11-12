import { useState, type FormEvent } from "react";
import { Empty, ErrorNote, Spinner, useLoad } from "../components/ui";
import { download, get, patch, post } from "../lib/api";
import { plural, relativeTime } from "../lib/format";
import type { ItemKind, ItemStatus, NotesOptions, PracticeItem } from "../lib/types";

const LETTERS = ["A", "B", "C", "D"];
type Counts = Record<ItemStatus, number>;
const KIND_LABEL: Record<ItemKind, string> = { mcq: "Multiple choice", tf: "True or false", short: "Short answer" };

/** M4 teacher side (FR-10, FR-17; SRS UC-10): generate drafts, review each beside its source, release. */
export default function PracticeTab({ courseId, onCounts }: { courseId: number; onCounts?: (c: Counts) => void }) {
  const [view, setView] = useState<ItemStatus>("draft");
  const opts = useLoad(() => get<NotesOptions>(`/courses/${courseId}/notes/options`), [courseId]);
  const queue = useLoad(async () => {
    const q = await get<{ counts: Counts; items: PracticeItem[] }>(`/courses/${courseId}/validation`);
    onCounts?.(q.counts);
    return q;
  }, [courseId]);
  const list = useLoad(() => view === "draft" ? Promise.resolve(null)
    : get<PracticeItem[]>(`/courses/${courseId}/practice/items?status=${view}`), [courseId, view]);
  const [error, setError] = useState<unknown>(null);

  function refresh() {
    queue.reload();
    list.reload();
  }

  async function paper() {
    setError(null);
    try {
      await download("/exports", { kind: "paper", ref: courseId }, "practice-paper.pdf");
    } catch (err) {
      setError(err);
    }
  }

  const counts = queue.data?.counts;
  const items = view === "draft" ? queue.data?.items : list.data;
  const tabs: [ItemStatus, string][] = [["draft", "To review"], ["released", "Live for students"],
    ["validated", "Approved, not yet taught"], ["rejected", "Rejected"], ["retired", "Retired"]];

  return (
    <section>
      <div className="card privacy-note">
        <strong>Nothing reaches a student until you approve it.</strong> Questions come only from material you have
        already posted, each one tied to the passage it came from. If you replace that file, its questions are retired
        automatically.
      </div>
      <Generator courseId={courseId} options={opts.data} onDone={() => { setView("draft"); refresh(); }} />
      <div className="table-tools">
        <div className="seg" role="tablist" aria-label="Questions by state">
          {tabs.map(([k, label]) => (
            <button key={k} role="tab" aria-selected={view === k} className={view === k ? "on" : ""} onClick={() => setView(k)}>
              {label}{counts ? ` (${counts[k]})` : ""}
            </button>
          ))}
        </div>
        {counts && counts.released + counts.validated > 0 && (
          <button className="btn small" onClick={paper}>Practice paper with answer key (PDF)</button>
        )}
      </div>
      <ErrorNote error={error ?? queue.error ?? list.error} />
      {!items ? <Spinner /> : items.length === 0 ? (
        <Empty title={view === "draft" ? "Nothing to review" : "No questions here"}>
          {view === "draft" ? "Generate questions above; they wait here for your decision." : null}
        </Empty>
      ) : (
        <div className="review-list">
          {items.map((it) => <ReviewCard key={`${it.id}-${it.status}`} item={it} onChange={refresh} />)}
        </div>
      )}
    </section>
  );
}

function Generator({ courseId, options, onDone }: { courseId: number; options: NotesOptions | null; onDone: () => void }) {
  const [by, setBy] = useState<"week" | "topic" | "material_id">("week");
  const [value, setValue] = useState("");
  const [n, setN] = useState(5);
  const [kinds, setKinds] = useState<ItemKind[]>(["mcq", "tf", "short"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!value) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const sel = by === "topic" ? { topic: value } : { [by]: Number(value) };
      const r = await post<{ label: string; items: PracticeItem[]; discarded: number }>(
        `/courses/${courseId}/practice/generate`, { ...sel, n, kinds });
      setDone(`${plural(r.items.length, "draft question")} for ${r.label}` +
              (r.discarded ? ` (${r.discarded} malformed ${r.discarded === 1 ? "one" : "ones"} discarded)` : "") +
              ". Review them below.");
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const toggle = (k: ItemKind) => setKinds((ks) => ks.includes(k) ? ks.filter((x) => x !== k) : [...ks, k]);
  return (
    <form className="card upload generator" onSubmit={submit}>
      <label>From
        <select value={by} onChange={(e) => { setBy(e.target.value as typeof by); setValue(""); }}>
          <option value="week">a week</option>
          <option value="topic">a topic</option>
          <option value="material_id">a file</option>
        </select>
      </label>
      <label className="grow">Which
        <select value={value} onChange={(e) => setValue(e.target.value)} disabled={!options}>
          <option value="">Choose...</option>
          {by === "week" && options?.weeks.map((w) => <option key={w} value={w}>Week {w}</option>)}
          {by === "topic" && options?.topics.map((t) => <option key={t} value={t}>{t}</option>)}
          {by === "material_id" && options?.files.map((f) => (
            <option key={f.id} value={f.id}>{f.title}{f.week ? ` (week ${f.week})` : ""}</option>
          ))}
        </select>
      </label>
      <label>How many<input type="number" min={1} max={10} value={n} onChange={(e) => setN(Number(e.target.value))} /></label>
      <fieldset className="kinds">
        <legend>Kinds</legend>
        {(["mcq", "tf", "short"] as ItemKind[]).map((k) => (
          <label key={k} className="check"><input type="checkbox" checked={kinds.includes(k)} onChange={() => toggle(k)} />{KIND_LABEL[k]}</label>
        ))}
      </fieldset>
      <button className="btn primary" disabled={!value || !kinds.length || busy}>{busy ? "Writing questions..." : "Generate questions"}</button>
      <ErrorNote error={error} />
      {done && <p className="note success">{done}</p>}
    </form>
  );
}

function ReviewCard({ item, onChange }: { item: PracticeItem; onChange: () => void }) {
  const [mode, setMode] = useState<"view" | "edit" | "reject">("view");
  const [q, setQ] = useState(item.question);
  const [options, setOptions] = useState<string[]>(item.options ?? ["", "", "", ""]);
  const [answer, setAnswer] = useState(item.answer);
  const [explanation, setExplanation] = useState(item.explanation ?? "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function act(body: object) {
    setBusy(true);
    setError(null);
    try {
      await patch(`/practice/${item.id}`, body);
      onChange();
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  const editable = item.status === "draft" || item.status === "validated";
  return (
    <article className={`card review-card status-${item.status}`}>
      <div className="review-main">
        <p className="q-meta">
          <span className="pill">{KIND_LABEL[item.kind]}</span>
          <span>{item.topic ?? "General"}{item.week ? ` · Week ${item.week}` : ""}</span>
          {item.edited && <span className="pill">edited</span>}
          <span className="muted small">{relativeTime(item.created_at)}</span>
        </p>
        {mode === "edit" ? (
          <div className="form">
            <label>Question<textarea value={q} onChange={(e) => setQ(e.target.value)} rows={2} /></label>
            {item.kind === "mcq" && options.map((o, i) => (
              <label key={i}>Option {LETTERS[i]}
                <input value={o} onChange={(e) => setOptions((os) => os.map((x, j) => j === i ? e.target.value : x))} />
              </label>
            ))}
            <label>Answer
              {item.kind === "mcq" ? (
                <select value={answer} onChange={(e) => setAnswer(e.target.value)}>
                  {LETTERS.map((L) => <option key={L} value={L}>{L}</option>)}
                </select>
              ) : item.kind === "tf" ? (
                <select value={answer} onChange={(e) => setAnswer(e.target.value)}>
                  <option value="true">True</option><option value="false">False</option>
                </select>
              ) : <textarea value={answer} onChange={(e) => setAnswer(e.target.value)} rows={2} />}
            </label>
            <label>Explanation<textarea value={explanation} onChange={(e) => setExplanation(e.target.value)} rows={2} /></label>
          </div>
        ) : (
          <>
            <h3 className="q-text">{item.question}</h3>
            {item.kind === "mcq" && item.options && (
              <ol className="review-options" type="A">
                {item.options.map((o, i) => <li key={i} className={LETTERS[i] === item.answer ? "right" : ""}>
                  {o}{LETTERS[i] === item.answer && <span className="sr-only"> (correct)</span>}</li>)}
              </ol>
            )}
            {item.kind !== "mcq" && <p><span className="fb-label">Answer</span> {item.kind === "tf" ? (item.answer === "true" ? "True" : "False") : item.answer}</p>}
            {item.explanation && <p className="sub">{item.explanation}</p>}
          </>
        )}
        {item.reject_reason && <p className="note">Rejected: {item.reject_reason}</p>}
        {item.retired_reason && <p className="note">Retired: {item.retired_reason}</p>}
        {item.stats && (
          <p className="fine">{item.stats.suppressed
            ? `Accuracy appears once ${item.stats.min_cohort} different students have answered.`
            : `${plural(item.stats.students ?? 0, "student")}, ${Math.round((item.stats.accuracy ?? 0) * 100)}% correct.`}</p>
        )}
        <ErrorNote error={error} />
        {mode === "reject" && (
          <div className="row">
            <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why? (kept for the acceptance rate)"
                   aria-label="Reason for rejecting" />
            <button className="btn danger" disabled={!reason.trim() || busy} onClick={() => act({ action: "reject", reason })}>Reject</button>
            <button className="btn ghost" onClick={() => setMode("view")}>Cancel</button>
          </div>
        )}
        <div className="row">
          {editable && mode === "view" && <>
            {item.status === "draft" && <button className="btn primary" disabled={busy} onClick={() => act({ action: "approve" })}>Approve</button>}
            <button className="btn" onClick={() => setMode("edit")}>Edit</button>
            <button className="btn ghost" onClick={() => setMode("reject")}>Reject</button>
          </>}
          {mode === "edit" && <>
            <button className="btn primary" disabled={busy} onClick={() => act({
              action: "approve", question: q, answer, explanation: explanation || null,
              options: item.kind === "mcq" ? options : undefined })}>Save and approve</button>
            <button className="btn ghost" onClick={() => setMode("view")}>Cancel</button>
          </>}
          {item.status === "released" && <button className="btn ghost small" disabled={busy}
            onClick={() => confirm("Retire this question? Students will no longer see it.") && act({ action: "retire" })}>Retire</button>}
        </div>
      </div>
      <aside className="review-source" aria-label="Source passage">
        <p className="fb-label">Generated from</p>
        <p className="src-file">{item.source.file}</p>
        <p className="src-where">{[item.source.location, item.source.week ? `Week ${item.source.week}` : null].filter(Boolean).join(" · ")}</p>
        <p className="src-snippet">{item.source.snippet}</p>
      </aside>
    </article>
  );
}
