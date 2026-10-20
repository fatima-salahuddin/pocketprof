import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Empty, ErrorNote, Page, Spinner, StatusPill, useLoad } from "../components/ui";
import { ApiError, del, get, post } from "../lib/api";
import { relativeTime } from "../lib/format";
import type { Course } from "../lib/types";

interface GoogleStatus { configured: boolean; connected: boolean; email: string | null; roster_scope: boolean }
interface GClass { id: string; name: string; section: string | null; code: string | null; link: string | null }

export default function TeacherHome() {
  const [params, setParams] = useSearchParams();
  const courses = useLoad(() => get<Course[]>("/courses"));
  const google = useLoad(() => get<GoogleStatus>("/google/status"));
  const [classes, setClasses] = useState<GClass[] | null>(null);
  const [classesError, setClassesError] = useState<unknown>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [showUpload, setShowUpload] = useState(false);
  const banner = params.get("google") === "connected" ? "Google Classroom connected." : null;
  const bannerError = params.get("google_error");

  const linked = new Set(courses.data?.map((c) => c.join_code?.toLowerCase()));

  useEffect(() => {
    if (google.data?.connected) {
      get<GClass[]>("/google/classes").then(setClasses).catch(setClassesError);
    }
  }, [google.data?.connected]);

  // Poll while any course is being read.
  useEffect(() => {
    if (!courses.data?.some((c) => c.status === "syncing" || c.status === "new")) return;
    const t = setTimeout(courses.reload, 2500);
    return () => clearTimeout(t);
  }, [courses.data]);

  async function connect() {
    setBusy("connect");
    try {
      const { url } = await post<{ url: string }>("/google/connect?return_to=/teacher");
      window.location.href = url;
    } catch (e) {
      setError(e);
      setBusy(null);
    }
  }

  async function link(classCode: string) {
    setBusy(classCode);
    setError(null);
    try {
      await post<Course>("/courses/link-google", { class_code: classCode });
      setCode("");
      courses.reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  async function disconnect() {
    await del("/google/connection");
    setClasses(null);
    google.reload();
  }

  return (
    <Page>
      {banner && <div className="note success" role="status">{banner}
        <button className="icon-btn" onClick={() => setParams({})} aria-label="Dismiss">×</button></div>}
      {bannerError && <div className="note error" role="alert">Google sign-in failed: {bannerError}</div>}

      <div className="page-head">
        <div>
          <h1>My classes</h1>
          <p className="sub">Link a Google Classroom class and PocketProf reads its posts and files, then keeps up as
            you post more.</p>
        </div>
        <button className="btn" onClick={() => setShowUpload((s) => !s)}>Course without Classroom</button>
      </div>

      {showUpload && <UploadCourseForm onCreated={() => { setShowUpload(false); courses.reload(); }} />}

      <section className="card google-card">
        <div className="google-head">
          <div>
            <h2>Google Classroom</h2>
            {!google.data ? <Spinner /> : !google.data.configured ? (
              <p className="sub">Google sign-in is not configured on this server yet. Ask the administrator.</p>
            ) : google.data.connected ? (
              <p className="sub">Connected as <strong>{google.data.email}</strong>. PocketProf has read-only access.</p>
            ) : (
              <p className="sub">Connect the Google account you teach with. PocketProf asks for read-only access.</p>
            )}
          </div>
          {google.data?.configured && (google.data.connected ? (
            <button className="btn ghost small" onClick={disconnect}>Disconnect</button>
          ) : (
            <button className="btn primary" onClick={connect} disabled={busy === "connect"}>Connect Google Classroom</button>
          ))}
        </div>

        {google.data?.connected && (
          <>
            <form className="row link-row" onSubmit={(e: FormEvent) => { e.preventDefault(); link(code); }}>
              <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="Class code, e.g. ittixrzv"
                     aria-label="Classroom class code" required minLength={4} spellCheck={false} />
              <button className="btn primary" disabled={busy === code}>Link class</button>
            </form>
            <ErrorNote error={classesError} />
            {classes && classes.length > 0 && (
              <ul className="gclasses">
                {classes.map((g) => (
                  <li key={g.id}>
                    <div>
                      <strong>{g.name}</strong>{g.section ? <span className="sub"> · {g.section}</span> : null}
                      <span className="mono"> {g.code}</span>
                    </div>
                    {g.code && linked.has(g.code.toLowerCase()) ? (
                      <span className="muted small">Linked</span>
                    ) : (
                      <button className="btn small" onClick={() => g.code && link(g.code)} disabled={!g.code || busy === g.code}>
                        Link
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        <ErrorNote error={error} />
      </section>

      <ErrorNote error={courses.error} />
      {!courses.data && !courses.error && <Spinner />}
      {courses.data?.length === 0 && <Empty title="No classes linked yet">Link a Classroom class above.</Empty>}
      <div className="course-grid">
        {courses.data?.map((c) => (
          <Link key={c.id} to={`/teacher/courses/${c.id}`} className="course-card card">
            <div className="course-card-top">
              <span className="course-code">{c.code}</span>
              <StatusPill status={c.status} />
            </div>
            <h3>{c.title}</h3>
            <p className="sub">{c.source === "google" ? "Google Classroom" : "Uploaded material"} · join code{" "}
              <span className="mono">{c.join_code}</span></p>
            <p className="meta">{c.n_materials} materials · {c.status === "syncing" ? c.status_msg : `updated ${relativeTime(c.last_synced_at)}`}</p>
          </Link>
        ))}
      </div>
    </Page>
  );
}

function UploadCourseForm({ onCreated }: { onCreated: () => void }) {
  const [code, setCode] = useState("");
  const [title, setTitle] = useState("");
  const [termStart, setTermStart] = useState("");
  const [error, setError] = useState<unknown>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await post("/courses", { code, title, term_start: termStart || null });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err : err);
    }
  }
  return (
    <form className="card form inline-form" onSubmit={submit}>
      <h2>Course without Google Classroom</h2>
      <p className="sub">Upload slides, notes and briefs yourself. Students join with the code PocketProf generates.</p>
      <div className="grid3">
        <label>Course code<input value={code} onChange={(e) => setCode(e.target.value)} placeholder="CS-2001" required /></label>
        <label>Title<input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Data Structures" required /></label>
        <label>Term starts<input type="date" value={termStart} onChange={(e) => setTermStart(e.target.value)} /></label>
      </div>
      <ErrorNote error={error} />
      <button className="btn primary">Create course</button>
    </form>
  );
}
