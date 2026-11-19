import { useEffect, useRef, useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { Brand, ErrorNote } from "../components/ui";
import { get, login, signup } from "../lib/api";
import { homeFor, useAuth } from "../lib/auth";

/** Seed accounts offered by the API in development and demo mode only (never in a pilot). */
interface DemoAccount { role: "student" | "teacher" | "admin"; name: string; email: string; password: string }
const ROLE_LABEL = { student: "Student", teacher: "Teacher", admin: "Admin" };

export default function Login() {
  const { user } = useAuth();
  const nav = useNavigate();
  const from = (useLocation().state as { from?: string } | null)?.from;
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [demo, setDemo] = useState<DemoAccount[]>([]);
  const submitRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    get<DemoAccount[]>("/auth/demo-accounts").then(setDemo).catch(() => setDemo([]));
  }, []);

  function fill(a: DemoAccount) {
    setMode("signin");
    setEmail(a.email);
    setPassword(a.password);
    setError(null);
    submitRef.current?.focus();
  }

  if (user) return <Navigate to={from ?? homeFor(user)} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const u = mode === "signin" ? await login(email, password) : await signup(name, email, password);
      nav(from ?? homeFor(u), { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-shell">
      <section className="auth-intro">
        <Brand />
        <h1>Your course's own assistant.</h1>
        <p className="lede">
          PocketProf answers from your teacher's Google Classroom material only: the slides, notes, assignment
          briefs and announcements of your class. Every answer shows where it came from, and when the material
          doesn't cover something, it says so.
        </p>
        <ul className="auth-points">
          <li><strong>Cited.</strong> Each answer links to the exact slide, page or post.</li>
          <li><strong>Up to date.</strong> New posts on Classroom are picked up within a minute.</li>
          <li><strong>Honest.</strong> No answers from outside your class material.</li>
        </ul>
      </section>
      <section className="auth-card card">
        <h2>{mode === "signin" ? "Sign in" : "Create a student account"}</h2>
        <form onSubmit={submit} className="form">
          {mode === "signup" && (
            <label>
              Full name
              <input value={name} onChange={(e) => setName(e.target.value)} required minLength={2} autoComplete="name" />
            </label>
          )}
          <label>
            University email
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
                   minLength={mode === "signup" ? 8 : 1}
                   autoComplete={mode === "signin" ? "current-password" : "new-password"} />
          </label>
          <ErrorNote error={error} />
          <button ref={submitRef} className="btn primary block" disabled={busy}>
            {busy ? "Please wait..." : mode === "signin" ? "Sign in" : "Create account"}
          </button>
        </form>
        <p className="switch">
          {mode === "signin" ? (
            <>New student? <button className="linklike" onClick={() => setMode("signup")}>Create an account</button></>
          ) : (
            <>Have an account? <button className="linklike" onClick={() => setMode("signin")}>Sign in</button></>
          )}
        </p>
        <p className="fine">Teachers and administrators receive their accounts from the university administrator.</p>
        {demo.length > 0 && (
          <div className="demo-logins">
            <p className="demo-label">Demo accounts <span className="fine">(for testing and demos)</span></p>
            <div className="demo-buttons">
              {demo.map((a) => (
                <button key={a.role} type="button" className={`demo-btn ${email === a.email ? "on" : ""}`}
                        onClick={() => fill(a)} aria-label={`Fill in the ${ROLE_LABEL[a.role]} demo account`}>
                  <strong>{ROLE_LABEL[a.role]}</strong>
                  <span>{a.name}</span>
                </button>
              ))}
            </div>
            <p className="fine">Fills in the email and password; then press Sign in.</p>
          </div>
        )}
      </section>
    </div>
  );
}
