import { useState } from "react";
import { useParams } from "react-router-dom";
import { SourcesPanel, asSources } from "../components/Sources";
import { CourseNav, Empty, ErrorNote, Spinner, TopBar, useLoad } from "../components/ui";
import { download, get, post } from "../lib/api";
import { plural } from "../lib/format";
import type { AttemptResult, Course, MasteryRow, PracticeCard, PracticeView } from "../lib/types";

const LETTERS = ["A", "B", "C", "D"];

/** M4 (FR-10, FR-18; SRS UC-07): teacher-approved questions on a spaced schedule, with mastery per topic. */
export default function Practice() {
  const courseId = Number(useParams().courseId);
  const { data: course, error: courseError } = useLoad(() => get<Course>(`/courses/${courseId}`), [courseId]);
  const view = useLoad(() => get<PracticeView>(`/courses/${courseId}/practice/due`), [courseId]);
  const [index, setIndex] = useState(0);
  const [useOptional, setUseOptional] = useState(false);
  const [mastery, setMastery] = useState<MasteryRow[] | null>(null);
  const [source, setSource] = useState<AttemptResult["source"] | null>(null);
  const [error, setError] = useState<unknown>(null);

  if (courseError) return <><TopBar /><main className="page"><ErrorNote error={courseError} /></main></>;
  if (!course) return <><TopBar /><main className="page"><Spinner /></main></>;

  const v = view.data;
  const cards = v ? (useOptional ? v.optional : v.session) : [];
  const card = cards[index];
  const rows = mastery ?? v?.mastery ?? [];

  function onAnswered(r: AttemptResult) {
    setMastery((prev) => {
      const base = prev ?? v?.mastery ?? [];
      const others = base.filter((m) => m.topic !== r.mastery.topic);
      return [...others, r.mastery].sort((a, b) => a.topic.localeCompare(b.topic));
    });
  }

  function restart() {
    setIndex(0);
    setUseOptional(false);
    setMastery(null);
    view.reload();
  }

  async function paper() {
    setError(null);
    try {
      await download("/exports", { kind: "paper", ref: courseId }, "practice-paper.pdf");
    } catch (err) {
      setError(err);
    }
  }

  return (
    <>
      <TopBar />
      <div className={`chat-shell no-threads ${source ? "with-sources" : ""}`}>
        <section className="conversation" aria-label="Practice">
          <header className="conv-head">
            <div>
              <h1 className="conv-title">{course.code} · {course.title}</h1>
              <p className="sub">Questions your teacher approved, from topics already taught. Ones you miss come back sooner.</p>
            </div>
            <div className="head-tools">
              <CourseNav courseId={courseId} active="practice" />
              {v && v.counts.released > 0 && <button className="btn small" onClick={paper}>Practice paper (PDF)</button>}
            </div>
          </header>

          <div className="messages practice-body">
            <ErrorNote error={error ?? view.error} />
            {!v ? <Spinner /> : (
              <div className="practice-grid">
                <div>
                  {v.counts.released === 0 ? (
                    <Empty title="No practice questions yet">
                      Your teacher hasn't approved any questions for this class. Notes and the assistant work meanwhile.
                    </Empty>
                  ) : card ? (
                    <>
                      <p className="fine">
                        Question {index + 1} of {cards.length}
                        {useOptional ? " · extra practice" : v.counts.due > 0 ? ` · ${plural(v.counts.due, "question")} due` : ""}
                      </p>
                      <QuestionCard key={`${useOptional}-${card.id}`} card={card} onAnswered={onAnswered}
                                    onSource={setSource} onNext={() => setIndex((i) => i + 1)} />
                    </>
                  ) : (
                    <div className="card done-card">
                      <h2>{useOptional || cards.length ? "Session done" : "Nothing is due right now"}</h2>
                      <p className="sub">
                        {cards.length ? "Questions you got right come back in a few days; the ones you missed, tomorrow."
                          : "Everything you've practised is scheduled for later. Spaced practice works best when you wait."}
                      </p>
                      <div className="row">
                        {!useOptional && v.optional.length > 0 && (
                          <button className="btn primary" onClick={() => { setUseOptional(true); setIndex(0); }}>
                            Practise anyway ({v.optional.length})
                          </button>
                        )}
                        <button className="btn" onClick={restart}>Check again</button>
                      </div>
                    </div>
                  )}
                </div>
                <aside className="card mastery-card" aria-label="Your mastery by topic">
                  <h2>Your mastery</h2>
                  <p className="fine">Recent answers count most. Only you can see this.</p>
                  {rows.length === 0 ? <p className="muted">Answer a question to start.</p> : (
                    <ul className="mastery">
                      {rows.map((m) => (
                        <li key={m.topic}>
                          <div className="m-head"><span>{m.topic}</span>
                            <span className="m-val">{m.estimate == null ? "not started" : `${Math.round(m.estimate * 100)}%`}</span></div>
                          <div className="bar" role="img" aria-label={`${m.topic}: ${m.estimate == null ? "not started" : `${Math.round(m.estimate * 100)} percent`}`}>
                            <i style={{ width: `${Math.round((m.estimate ?? 0) * 100)}%` }} />
                          </div>
                          <span className="fine">{plural(m.attempts, "attempt")}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  <p className="fine">Spacing: {v.intervals_days.join(", ")} days.</p>
                </aside>
              </div>
            )}
          </div>
        </section>
        {source && <SourcesPanel sources={asSources([{ ...source, n: 1 }])} focus={1} onClose={() => setSource(null)} />}
      </div>
    </>
  );
}

function QuestionCard({ card, onAnswered, onSource, onNext }: {
  card: PracticeCard; onAnswered: (r: AttemptResult) => void; onSource: (s: AttemptResult["source"]) => void;
  onNext: () => void;
}) {
  const [choice, setChoice] = useState<string>("");
  const [text, setText] = useState("");
  const [model, setModel] = useState<{ answer: string; explanation: string | null } | null>(null);
  const [result, setResult] = useState<AttemptResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function submit(answer: string, selfCorrect?: boolean) {
    setBusy(true);
    setError(null);
    try {
      const r = await post<AttemptResult>(`/practice/${card.id}/attempts`, { answer, self_correct: selfCorrect });
      setResult(r);
      onAnswered(r);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function reveal() {
    setBusy(true);
    setError(null);
    try {
      setModel(await post<{ answer: string; explanation: string | null }>(`/practice/${card.id}/reveal`));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const correctLetter = result?.correct_answer;
  return (
    <article className={`card question-card ${result ? (result.correct ? "is-right" : "is-wrong") : ""}`}>
      <p className="q-meta">
        <span className="pill">{card.kind === "mcq" ? "Multiple choice" : card.kind === "tf" ? "True or false" : "Short answer"}</span>
        <span>{card.topic}{card.week ? ` · Week ${card.week}` : ""}</span>
        {card.reason === "due" && <span className="pill pill-new">Due</span>}
      </p>
      <h2 className="q-text">{card.question}</h2>

      {card.kind === "mcq" && card.options && (
        <div className="options" role="radiogroup" aria-label="Options">
          {card.options.map((o, i) => {
            const L = LETTERS[i];
            const state = result ? (L === correctLetter ? "right" : L === choice ? "wrong" : "") : choice === L ? "picked" : "";
            return (
              <button key={L} type="button" role="radio" aria-checked={choice === L} disabled={!!result}
                      className={`option ${state}`} onClick={() => setChoice(L)}>
                <span className="opt-letter">{L}</span>{o}
              </button>
            );
          })}
        </div>
      )}
      {card.kind === "tf" && (
        <div className="options tf" role="radiogroup" aria-label="True or false">
          {["true", "false"].map((v) => {
            const state = result ? (v === correctLetter ? "right" : v === choice ? "wrong" : "") : choice === v ? "picked" : "";
            return (
              <button key={v} type="button" role="radio" aria-checked={choice === v} disabled={!!result}
                      className={`option ${state}`} onClick={() => setChoice(v)}>{v === "true" ? "True" : "False"}</button>
            );
          })}
        </div>
      )}
      {card.kind === "short" && (
        <label className="short-answer">Your answer
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} disabled={!!model}
                    placeholder="Write it in your own words first, then compare." />
        </label>
      )}

      <ErrorNote error={error} />

      {!result && card.kind !== "short" && (
        <div className="row-end"><button className="btn primary" disabled={!choice || busy} onClick={() => submit(choice)}>Check</button></div>
      )}
      {!result && card.kind === "short" && !model && (
        <div className="row-end"><button className="btn primary" disabled={!text.trim() || busy} onClick={reveal}>Compare with the answer</button></div>
      )}
      {!result && card.kind === "short" && model && (
        <div className="feedback">
          <p className="fb-label">Model answer</p>
          <p>{model.answer}</p>
          {model.explanation && <p className="sub">{model.explanation}</p>}
          <p className="fine">Did your answer say the same thing?</p>
          <div className="row">
            <button className="btn primary" disabled={busy} onClick={() => submit(text, true)}>Yes, I got it</button>
            <button className="btn" disabled={busy} onClick={() => submit(text, false)}>No, I missed it</button>
          </div>
        </div>
      )}

      {result && (
        <div className="feedback" role="status">
          <p className={`fb-verdict ${result.correct ? "right" : "wrong"}`}>{result.correct ? "Correct" : "Not quite"}</p>
          {!result.correct && card.kind !== "short" && (
            <p>The answer is <strong>{card.kind === "mcq" && result.options
              ? `${result.correct_answer}. ${result.options[LETTERS.indexOf(result.correct_answer)]}`
              : result.correct_answer === "true" ? "True" : "False"}</strong>.</p>
          )}
          {result.explanation && <p>{result.explanation}</p>}
          <p className="spacing">
            You'll see this again in <strong>{plural(result.interval_days, "day")}</strong> (box {result.box} of {result.boxes}).
            {" "}{result.mastery.topic}: {Math.round((result.mastery.estimate ?? 0) * 100)}% mastery.
          </p>
          <div className="row">
            <button className="cited-item" onClick={() => onSource(result.source)}>
              <span className="cite static">S1</span>{result.source.file}{result.source.location ? `, ${result.source.location}` : ""}
            </button>
          </div>
          <div className="row-end"><button className="btn primary" onClick={onNext}>Next question</button></div>
        </div>
      )}
    </article>
  );
}
