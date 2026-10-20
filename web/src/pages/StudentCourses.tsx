import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorNote, Page, Spinner, StatusPill, useLoad } from "../components/ui";
import { get, post } from "../lib/api";
import { relativeTime } from "../lib/format";
import type { Course } from "../lib/types";

export default function StudentCourses() {
  const { data: courses, error, reload } = useLoad(() => get<Course[]>("/courses"));
  const [code, setCode] = useState("");
  const [joinError, setJoinError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function join(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setJoinError(null);
    try {
      await post<Course>("/courses/join", { join_code: code });
      setCode("");
      reload();
    } catch (err) {
      setJoinError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Page>
      <div className="page-head">
        <div>
          <h1>My classes</h1>
          <p className="sub">Ask about any class you've joined. Answers come only from that class's material.</p>
        </div>
      </div>

      <form className="card join" onSubmit={join}>
        <label htmlFor="join-code"><strong>Join a class</strong> with the class code your teacher shared
          (the same code as on Google Classroom).</label>
        <div className="row">
          <input id="join-code" value={code} onChange={(e) => setCode(e.target.value)} placeholder="e.g. ittixrzv"
                 required minLength={4} autoComplete="off" spellCheck={false} />
          <button className="btn primary" disabled={busy}>{busy ? "Joining..." : "Join"}</button>
        </div>
        <ErrorNote error={joinError} />
      </form>

      <ErrorNote error={error} />
      {!courses && !error && <Spinner />}
      {courses && courses.length === 0 && (
        <Empty title="You haven't joined a class yet">Enter a class code above to get started.</Empty>
      )}
      <div className="course-grid">
        {courses?.map((c) => (
          <Link key={c.id} to={`/courses/${c.id}`} className="course-card card">
            <div className="course-card-top">
              <span className="course-code">{c.code}</span>
              <StatusPill status={c.status} />
            </div>
            <h3>{c.title}</h3>
            <p className="sub">{c.teacher_name}{c.section ? ` · ${c.section}` : ""}</p>
            <p className="meta">{c.n_materials} materials · updated {relativeTime(c.last_synced_at)}</p>
          </Link>
        ))}
      </div>
    </Page>
  );
}
