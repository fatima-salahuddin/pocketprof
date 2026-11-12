import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Markdown } from "../components/Markdown";
import { SourcesPanel, asSources } from "../components/Sources";
import { CourseNav, ErrorNote, Spinner, TopBar, useLoad } from "../components/ui";
import { del, download, get, post } from "../lib/api";
import { relativeTime } from "../lib/format";
import type { Course, Note, NoteSummary, NotesOptions } from "../lib/types";

type Pick = "week" | "topic" | "file" | "own";

/** M3 (FR-8, FR-9): structured notes for a week, topic or file, from that class's material only. */
export default function Notes() {
  const courseId = Number(useParams().courseId);
  const [params, setParams] = useSearchParams();
  const noteId = params.get("note") ? Number(params.get("note")) : null;
  const { data: course, error: courseError } = useLoad(() => get<Course>(`/courses/${courseId}`), [courseId]);
  const opts = useLoad(() => get<NotesOptions>(`/courses/${courseId}/notes/options`), [courseId]);
  const saved = useLoad(() => get<NoteSummary[]>(`/notes?course_id=${courseId}`), [courseId]);
  const [note, setNote] = useState<Note | null>(null);
  const [pick, setPick] = useState<Pick>("week");
  const [choice, setChoice] = useState<string>("");
  const [ownTopic, setOwnTopic] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [focus, setFocus] = useState<number | null | undefined>(undefined);
  const [showList, setShowList] = useState(false);

  useEffect(() => {
    setFocus(undefined);
    if (noteId == null) {
      setNote(null);
      return;
    }
    if (note?.id === noteId) return;
    get<Note>(`/notes/${noteId}`).then(setNote).catch(setError);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [noteId]);

  const selection = useMemo(() => {
    if (pick === "own") return ownTopic.trim().length >= 2 ? { topic: ownTopic.trim() } : null;
    if (!choice) return null;
    if (pick === "week") return { week: Number(choice) };
    if (pick === "topic") return { topic: choice };
    return { material_id: Number(choice) };
  }, [pick, choice, ownTopic]);

  const label = useMemo(() => {
    if (!selection) return "";
    if ("week" in selection) return `week ${selection.week}`;
    if ("material_id" in selection) return opts.data?.files.find((f) => f.id === selection.material_id)?.title ?? "this file";
    return `"${selection.topic}"`;
  }, [selection, opts.data]);

  async function generate(e?: FormEvent) {
    e?.preventDefault();
    if (!selection || busy) return;
    setBusy(`Reading the material for ${label}...`);
    setError(null);
    try {
      const n = await post<Note>(`/courses/${courseId}/notes`, selection);
      setNote(n);
      setParams({ note: String(n.id) });
      saved.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  async function remove(id: number) {
    await del(`/notes/${id}`);
    if (id === noteId) setParams({});
    saved.reload();
  }

  async function pdf() {
    if (!note) return;
    setError(null);
    try {
      await download("/exports", { kind: "notes", ref: note.id }, "notes.pdf");
    } catch (err) {
      setError(err);
    }
  }

  if (courseError) return <><TopBar /><main className="page"><ErrorNote error={courseError} /></main></>;
  if (!course) return <><TopBar /><main className="page"><Spinner /></main></>;
  const sources = note ? asSources(note.sources) : [];
  const citable = new Set(sources.map((s) => s.n));

  return (
    <>
      <TopBar />
      <div className={`chat-shell ${focus !== undefined && note?.outcome === "generated" ? "with-sources" : ""}`}>
        <aside className={`threads ${showList ? "open" : ""}`} aria-label="Saved notes">
          <Link to={`/courses/${courseId}/notes`} className="btn primary block" onClick={() => setShowList(false)}>
            New notes
          </Link>
          <p className="threads-label">Your notes</p>
          <ul>
            {saved.data?.map((n) => (
              <li key={n.id} className={n.id === noteId ? "active" : ""}>
                <Link to={`/courses/${courseId}/notes?note=${n.id}`} onClick={() => setShowList(false)}>
                  <span className="t-title">{n.title}</span>
                  <span className="t-time">{n.scope_label} · {relativeTime(n.created_at)}</span>
                </Link>
                <button className="icon-btn" aria-label={`Delete notes: ${n.title}`} onClick={() => remove(n.id)}>×</button>
              </li>
            ))}
            {saved.data?.length === 0 && <li className="muted">No notes yet.</li>}
          </ul>
        </aside>

        <section className="conversation" aria-label="Notes">
          <header className="conv-head">
            <button className="btn ghost small threads-toggle" onClick={() => setShowList((s) => !s)}>Your notes</button>
            <div>
              <h1 className="conv-title">{course.code} · {course.title}</h1>
              <p className="sub">Study notes written only from this class's material, with a source for every point.</p>
            </div>
            <CourseNav courseId={courseId} active="notes" />
          </header>

          <div className="messages notes-body">
            {!note && (
              <form className="card notes-picker" onSubmit={generate}>
                <h2>Make notes for...</h2>
                <div className="seg" role="tablist" aria-label="What to make notes for">
                  {([["week", "A week"], ["topic", "A topic"], ["file", "A file"], ["own", "Your own topic"]] as [Pick, string][])
                    .map(([k, l]) => (
                      <button key={k} type="button" role="tab" aria-selected={pick === k} className={pick === k ? "on" : ""}
                              onClick={() => { setPick(k); setChoice(""); }}>{l}</button>
                    ))}
                </div>
                {!opts.data ? <Spinner /> : (
                  <div className="pick-list" role="radiogroup" aria-label="Choose one">
                    {pick === "week" && (opts.data.weeks.length ? opts.data.weeks.map((w) => (
                      <button key={w} type="button" role="radio" aria-checked={choice === String(w)}
                              className={`chip ${choice === String(w) ? "on" : ""}`} onClick={() => setChoice(String(w))}>
                        Week {w}
                      </button>
                    )) : <p className="muted">No week has material yet.</p>)}
                    {pick === "topic" && (opts.data.topics.length ? opts.data.topics.map((t) => (
                      <button key={t} type="button" role="radio" aria-checked={choice === t}
                              className={`chip ${choice === t ? "on" : ""}`} onClick={() => setChoice(t)}>{t}</button>
                    )) : <p className="muted">The material has no topic tags yet. Try "Your own topic".</p>)}
                    {pick === "file" && (
                      <ul className="file-pick">
                        {opts.data.files.map((f) => (
                          <li key={f.id}>
                            <label className="check">
                              <input type="radio" name="file" checked={choice === String(f.id)}
                                     onChange={() => setChoice(String(f.id))} />
                              <span>{f.title}<span className="muted small">{f.week ? ` · Week ${f.week}` : ""}{f.topic ? ` · ${f.topic}` : ""}</span></span>
                            </label>
                          </li>
                        ))}
                      </ul>
                    )}
                    {pick === "own" && (
                      <label className="grow">Topic, in your own words
                        <input value={ownTopic} onChange={(e) => setOwnTopic(e.target.value)} maxLength={200}
                               placeholder="e.g. collision handling in hash tables" />
                      </label>
                    )}
                  </div>
                )}
                <ErrorNote error={error} />
                <div className="row-end">
                  {busy ? <Spinner label={busy.replace(/\.\.\.$/, "")} /> : (
                    <button className="btn primary" disabled={!selection}>Generate notes{label ? ` for ${label}` : ""}</button>
                  )}
                </div>
                <p className="fine">Notes take 5 to 30 seconds. PocketProf only uses what your teacher posted; if a
                  selection has nothing, it says so.</p>
              </form>
            )}

            {note && (
              <article className={`notes-doc outcome-${note.outcome}`}>
                <div className="notes-head">
                  <div>
                    <p className="scope-chip">{note.scope_label}</p>
                    <h2 className="notes-title">{note.title}</h2>
                    <p className="fine">AI-generated from your teacher's material · {relativeTime(note.created_at)}</p>
                  </div>
                  <div className="actions">
                    <button className="btn" onClick={pdf}>Download PDF</button>
                    <Link className="btn ghost" to={`/courses/${courseId}/notes`}>New notes</Link>
                  </div>
                </div>
                <ErrorNote error={error} />
                {note.outcome === "refused" && (
                  <div className="msg bot outcome-refused">
                    <p className="refusal-label">Not in the class material</p>
                    <p>{note.notice}</p>
                  </div>
                )}
                {note.outcome === "fallback" && (
                  <>
                    <p className="notice">{note.notice}</p>
                    <ul className="material-list">
                      {note.sources.map((s) => (
                        <li key={s.n}><strong>{s.file}</strong>{s.location ? ` · ${s.location}` : ""}{s.week ? ` · Week ${s.week}` : ""}
                          {s.url && <> · <a href={s.url} target="_blank" rel="noreferrer">open</a></>}</li>
                      ))}
                    </ul>
                  </>
                )}
                {note.outcome === "generated" && note.sections.map((s, i) => (
                  <section key={i} className="note-section">
                    {s.heading && <h3>{s.heading}</h3>}
                    <Markdown text={s.body} citable={citable} onCite={(n) => setFocus(n)} />
                  </section>
                ))}
                {note.truncated && (
                  <p className="fine">This selection holds more material than one set of notes covers. Every file is
                    represented; pick a single file for more detail.</p>
                )}
                {note.outcome === "generated" && (
                  <footer className="cited">
                    <span className="cited-label">Sources</span>
                    {sources.map((s) => (
                      <button key={s.n} className="cited-item" onClick={() => setFocus(s.n)}>
                        <span className="cite static">S{s.n}</span>{s.file}{s.location ? `, ${s.location}` : ""}
                      </button>
                    ))}
                  </footer>
                )}
              </article>
            )}
          </div>
        </section>

        {focus !== undefined && note?.outcome === "generated" && (
          <SourcesPanel sources={sources} focus={focus} onClose={() => setFocus(undefined)} />
        )}
      </div>
    </>
  );
}
