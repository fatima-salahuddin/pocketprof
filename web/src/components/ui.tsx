import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { ApiError, logout } from "../lib/api";
import { useAuth } from "../lib/auth";

export function Brand() {
  return (
    <Link to="/" className="brand" aria-label="PocketProf home">
      <span className="brand-mark" aria-hidden="true">P</span>
      <span className="brand-name">PocketProf</span>
    </Link>
  );
}

export function TopBar({ children }: { children?: ReactNode }) {
  const { user } = useAuth();
  const nav = useNavigate();
  return (
    <header className="topbar">
      <Brand />
      <nav className="topnav" aria-label="Main">
        {user?.role === "student" && <NavLink to="/courses">My classes</NavLink>}
        {user?.role === "teacher" && <NavLink to="/teacher">My classes</NavLink>}
        {user?.role === "admin" && <NavLink to="/admin">Administration</NavLink>}
        {children}
      </nav>
      {user && (
        <div className="who">
          <span className="who-name">{user.name}</span>
          <span className="role-tag">{user.role}</span>
          <button className="btn ghost small" onClick={async () => { await logout(); nav("/login"); }}>
            Sign out
          </button>
        </div>
      )}
    </header>
  );
}

export function Page({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return (
    <>
      <TopBar />
      <main className={wide ? "page wide" : "page"}>{children}</main>
    </>
  );
}

export function StatusPill({ status, label }: { status: string; label?: string }) {
  const text = label ?? { ready: "Ready", syncing: "Reading", new: "Queued", error: "Needs attention",
    indexed: "Indexed", skipped: "Skipped", failed: "Failed", withdrawn: "Withdrawn", running: "Running",
    done: "Done" }[status] ?? status;
  return <span className={`pill pill-${status}`}>{status === "syncing" || status === "running" ? <i className="spin" aria-hidden="true" /> : null}{text}</span>;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const p = error instanceof ApiError ? error.problem : null;
  return (
    <div className="note error" role="alert">
      <strong>{p?.title && p.title !== "Error" ? p.title : "Something went wrong"}.</strong>{" "}
      {p?.detail ?? String(error)}
      {p?.errors?.length ? (
        <ul>
          {p.errors.slice(0, 8).map((e, i) => (
            <li key={i}>{e.row ? `Row ${e.row}: ` : e.field ? `${e.field}: ` : ""}{e.message}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <div className="loading" role="status"><i className="spin" aria-hidden="true" /> {label}...</div>;
}

/** Fetch on mount and when deps change; `reload` refetches. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    load().then((d) => alive && (setData(d), setError(null))).catch((e) => alive && setError(e));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, reload: () => setTick((t) => t + 1), setData };
}
