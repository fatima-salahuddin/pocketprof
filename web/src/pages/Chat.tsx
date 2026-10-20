import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Markdown } from "../components/Markdown";
import { ErrorNote, Spinner, TopBar, useLoad } from "../components/ui";
import { ApiError, del, get, streamChat } from "../lib/api";
import { relativeTime } from "../lib/format";
import type { ChatMessage, Citation, Course, Scope, Source, Thread } from "../lib/types";

const SUGGESTIONS = [
  "What did we cover last week?",
  "Explain the hardest idea from this week's slides",
  "When is the next assignment due?",
];

interface ThreadDetail extends Thread {
  messages: { id: number; role: "user" | "assistant"; content: string; outcome: ChatMessage["outcome"];
              citations: Citation[] | null; scope: Scope | null; mode: ChatMessage["mode"] }[];
}

export default function Chat() {
  const courseId = Number(useParams().courseId);
  const [params, setParams] = useSearchParams();
  const threadId = params.get("thread") ? Number(params.get("thread")) : null;
  const { data: course, error: courseError } = useLoad(() => get<Course>(`/courses/${courseId}`), [courseId]);
  const threads = useLoad(() => get<Thread[]>(`/threads?course_id=${courseId}`), [courseId]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<"direct" | "scaffolded">("direct");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [activeSources, setActiveSources] = useState<{ sources: Source[]; focus: number | null } | null>(null);
  const [showThreads, setShowThreads] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const createdHere = useRef<number | null>(null);

  // Load a past thread with its citations intact (FR-16).
  useEffect(() => {
    if (threadId != null && threadId === createdHere.current) {
      createdHere.current = null; // this page just created the thread; its messages are already shown
      return;
    }
    abortRef.current?.abort();
    setError(null);
    setActiveSources(null);
    if (threadId == null) {
      setMessages([]);
      return;
    }
    get<ThreadDetail>(`/threads/${threadId}`)
      .then((t) => setMessages(t.messages.map((m) => ({
        id: m.id, role: m.role, content: m.content, outcome: m.outcome ?? undefined, mode: m.mode ?? undefined,
        citations: m.citations ?? [], scope: m.scope, showPassages: false,
        notice: m.outcome === "fallback" ? m.content : null,
        sources: (m.citations ?? []).map((c) => ({ n: c.n, cited: true, kind: null, post_title: c.post_title,
          topic: null, file: c.file, location: c.location, week: c.week, date: null, url: c.url, snippet: "",
          score: 0, ocr: false })),
      }))))
      .catch(setError);
  }, [threadId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [messages]);

  useEffect(() => () => abortRef.current?.abort(), []);

  async function send(text: string) {
    const question = text.trim();
    if (!question || busy) return;
    setInput("");
    setError(null);
    setBusy(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setMessages((m) => [...m, { role: "user", content: question, mode },
                        { role: "assistant", content: "", pending: true, mode }]);
    const update = (fn: (m: ChatMessage) => ChatMessage) =>
      setMessages((all) => all.map((m, i) => (i === all.length - 1 ? fn(m) : m)));
    let newThread: number | null = null;
    try {
      await streamChat(courseId, { message: question, thread_id: threadId, mode }, ctrl.signal, {
        onEvent(type, data) {
          if (type === "meta") {
            newThread = data.thread_id;
            update((m) => ({ ...m, scope: data.scope, scopeNotes: data.scope_notes, sources: data.sources }));
          } else if (type === "delta") {
            update((m) => ({ ...m, content: m.content + data.text }));
          } else if (type === "done") {
            update((m) => ({ ...m, id: data.message_id, pending: false, content: data.answer, outcome: data.outcome,
                             citations: data.citations, sources: data.sources, notice: data.notice,
                             showPassages: data.show_passages }));
          } else if (type === "error") {
            throw new ApiError({ title: "Error", detail: data.detail, status: 500 });
          }
        },
      });
      if (threadId == null && newThread != null) {
        createdHere.current = newThread;
        setParams({ thread: String(newThread) }, { replace: true });
      }
      threads.reload();
    } catch (err) {
      if ((err as Error).name === "AbortError") return;
      setError(err);
      setMessages((all) => all.filter((m) => !m.pending));
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send(input);
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  async function removeThread(id: number) {
    await del(`/threads/${id}`);
    if (id === threadId) setParams({});
    threads.reload();
  }

  if (courseError) return <><TopBar /><main className="page"><ErrorNote error={courseError} /></main></>;
  if (!course) return <><TopBar /><main className="page"><Spinner /></main></>;
  const notReady = course.n_chunks === 0;

  return (
    <>
      <TopBar />
      <div className={`chat-shell ${activeSources ? "with-sources" : ""}`}>
        <aside className={`threads ${showThreads ? "open" : ""}`} aria-label="Conversations">
          <Link to={`/courses/${courseId}`} className="btn primary block" onClick={() => setShowThreads(false)}>
            New conversation
          </Link>
          <p className="threads-label">Past conversations</p>
          <ul>
            {threads.data?.map((t) => (
              <li key={t.id} className={t.id === threadId ? "active" : ""}>
                <Link to={`/courses/${courseId}?thread=${t.id}`} onClick={() => setShowThreads(false)}>
                  <span className="t-title">{t.title}</span>
                  <span className="t-time">{relativeTime(t.updated_at ?? t.created_at)}</span>
                </Link>
                <button className="icon-btn" aria-label={`Delete conversation: ${t.title}`}
                        onClick={() => removeThread(t.id)}>×</button>
              </li>
            ))}
            {threads.data?.length === 0 && <li className="muted">No conversations yet.</li>}
          </ul>
        </aside>

        <section className="conversation" aria-label="Conversation">
          <header className="conv-head">
            <button className="btn ghost small threads-toggle" onClick={() => setShowThreads((s) => !s)}>
              Conversations
            </button>
            <div>
              <h1 className="conv-title">{course.code} · {course.title}</h1>
              <p className="sub">Answers come only from this class's material on Google Classroom.</p>
            </div>
            <div className="mode-toggle" role="radiogroup" aria-label="How should PocketProf answer?">
              <button role="radio" aria-checked={mode === "direct"} className={mode === "direct" ? "on" : ""}
                      onClick={() => setMode("direct")}>Direct answer</button>
              <button role="radio" aria-checked={mode === "scaffolded"} className={mode === "scaffolded" ? "on" : ""}
                      onClick={() => setMode("scaffolded")} title="Hints first, so you work it out yourself">
                Step by step
              </button>
            </div>
          </header>

          <div className="messages" aria-live="polite">
            {messages.length === 0 && (
              <div className="welcome">
                <h2>What would you like to understand?</h2>
                <p>
                  Ask about a concept, an assignment, or a week ("what did we cover last week?"). Every answer
                  cites the slide, page or post it came from.
                </p>
                {notReady ? (
                  <p className="note">This class's material is still being read. Try again in a minute.</p>
                ) : (
                  <div className="suggestions">
                    {SUGGESTIONS.map((s) => (
                      <button key={s} className="chip" onClick={() => send(s)}>{s}</button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="msg user"><div className="bubble">{m.content}</div></div>
              ) : (
                <AssistantMessage key={i} m={m}
                  onCite={(n) => setActiveSources({ sources: m.sources ?? [], focus: n })}
                  onShowSources={() => setActiveSources({ sources: m.sources ?? [], focus: null })} />
              ),
            )}
            <ErrorNote error={error} />
            <div ref={endRef} />
          </div>

          <form className="composer" onSubmit={onSubmit}>
            <textarea ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={onKey}
                      rows={1} maxLength={2000} placeholder={`Ask about ${course.code}...`} aria-label="Your question"
                      disabled={notReady} />
            {busy ? (
              <button type="button" className="btn" onClick={() => abortRef.current?.abort()}>Stop</button>
            ) : (
              <button className="btn primary" disabled={!input.trim() || notReady}>Ask</button>
            )}
          </form>
        </section>

        {activeSources && (
          <SourcesPanel sources={activeSources.sources} focus={activeSources.focus} onClose={() => setActiveSources(null)} />
        )}
      </div>
    </>
  );
}

function AssistantMessage({ m, onCite, onShowSources }: { m: ChatMessage; onCite: (n: number) => void;
                                                           onShowSources: () => void }) {
  const citable = useMemo(() => new Set((m.sources ?? []).map((s) => s.n)), [m.sources]);
  const cited = (m.sources ?? []).filter((s) => s.cited);
  return (
    <article className={`msg bot outcome-${m.outcome ?? "pending"}`}>
      {m.scope && (
        <div className="scope-chip" title={(m.scope.notes ?? []).join(" ")}>
          Searching only <strong>{m.scope.label}</strong>
          {m.scope.notes?.length ? <span className="scope-note"> · {m.scope.notes[0]}</span> : null}
        </div>
      )}
      {m.pending && !m.content && <div className="typing" aria-label="PocketProf is answering"><i /><i /><i /></div>}
      {m.outcome === "refused" && <p className="refusal-label">Not in the class material</p>}
      {m.content && <Markdown text={m.content} onCite={onCite} citable={citable} />}
      {m.notice && m.outcome === "fallback" && <p className="notice">{m.notice}</p>}
      {m.showPassages && m.sources?.length ? (
        <details className="passages">
          <summary>Show the closest passages ({m.sources.length})</summary>
          {m.sources.slice(0, 3).map((s) => (
            <blockquote key={s.n}>
              <p>{s.snippet}</p>
              <footer>{s.file}{s.location ? `, ${s.location}` : ""}{s.week ? ` · Week ${s.week}` : ""}</footer>
            </blockquote>
          ))}
        </details>
      ) : null}
      {cited.length > 0 && (
        <footer className="cited">
          <span className="cited-label">Sources</span>
          {cited.map((s) => (
            <button key={s.n} className="cited-item" onClick={() => onCite(s.n)}>
              <span className="cite static">S{s.n}</span>
              {s.file}{s.location ? `, ${s.location}` : ""}
            </button>
          ))}
          {(m.sources?.length ?? 0) > cited.length && (
            <button className="linklike small" onClick={onShowSources}>all retrieved passages</button>
          )}
        </footer>
      )}
    </article>
  );
}

function SourcesPanel({ sources, focus, onClose }: { sources: Source[]; focus: number | null; onClose: () => void }) {
  const refs = useRef<Record<number, HTMLLIElement | null>>({});
  useEffect(() => {
    if (focus != null) refs.current[focus]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [focus]);
  return (
    <aside className="sources" aria-label="Sources">
      <header>
        <h2>Sources</h2>
        <button className="icon-btn" onClick={onClose} aria-label="Close sources">×</button>
      </header>
      <ol>
        {sources.map((s) => (
          <li key={s.n} ref={(el) => (refs.current[s.n] = el)} className={`${s.cited ? "cited" : ""} ${s.n === focus ? "focus" : ""}`}>
            <div className="src-head">
              <span className="cite static">S{s.n}</span>
              <span className="src-file">{s.file}</span>
            </div>
            <p className="src-where">
              {[s.location, s.week ? `Week ${s.week}` : null, s.post_title].filter(Boolean).join(" · ")}
            </p>
            {s.snippet && <p className="src-snippet">{s.snippet}</p>}
            {s.ocr && <p className="src-flag">Read with OCR; check the original.</p>}
            {s.url && <a href={s.url} target="_blank" rel="noreferrer">Open on Classroom</a>}
          </li>
        ))}
      </ol>
    </aside>
  );
}
