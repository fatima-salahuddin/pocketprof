import type { Problem, User } from "./types";

/** API client. The access token lives in memory only; the refresh token is an httpOnly cookie the
 * browser sends to /api/v1/auth, so script on the page can never read it. A 401 triggers one silent
 * refresh and a retry. */

const BASE = "/api/v1";
let accessToken: string | null = null;
let refreshing: Promise<User | null> | null = null;
const listeners = new Set<(u: User | null) => void>();

export class ApiError extends Error {
  problem: Problem;
  constructor(problem: Problem) {
    super(problem.detail || problem.title);
    this.problem = problem;
  }
}

export function onSessionChange(fn: (u: User | null) => void) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function setSession(token: string | null, user: User | null) {
  accessToken = token;
  listeners.forEach((fn) => fn(user));
}

async function toProblem(res: Response): Promise<Problem> {
  try {
    const body = await res.json();
    return { title: body.title ?? "Error", detail: body.detail ?? res.statusText, status: res.status, errors: body.errors };
  } catch {
    return { title: "Error", detail: res.statusText || "The server could not be reached.", status: res.status };
  }
}

export async function refreshSession(): Promise<User | null> {
  if (!refreshing) {
    refreshing = (async () => {
      const res = await fetch(`${BASE}/auth/refresh`, { method: "POST", credentials: "same-origin" });
      if (!res.ok) {
        setSession(null, null);
        return null;
      }
      const body = await res.json();
      setSession(body.access_token, body.user);
      return body.user as User;
    })().finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

async function raw(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError({ title: "Offline", detail: "PocketProf's server could not be reached.", status: 0 });
  }
  if (res.status === 401 && retry && !path.startsWith("/auth/")) {
    const user = await refreshSession();
    if (user) return raw(path, init, false);
  }
  return res;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await raw(path, init);
  if (!res.ok) throw new ApiError(await toProblem(res));
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const get = <T,>(path: string) => api<T>(path);
export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body) });
export const put = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: "PUT", body: body instanceof FormData ? body : JSON.stringify(body) });
export const patch = <T,>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = (path: string) => api<void>(path, { method: "DELETE" });

// ----- auth -----

export async function login(email: string, password: string): Promise<User> {
  const body = await api<{ access_token: string; user: User }>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setSession(body.access_token, body.user);
  return body.user;
}

export async function signup(name: string, email: string, password: string): Promise<User> {
  const body = await api<{ access_token: string; user: User }>("/auth/signup", {
    method: "POST",
    body: JSON.stringify({ name, email, password }),
  });
  setSession(body.access_token, body.user);
  return body.user;
}

export async function logout(): Promise<void> {
  try {
    await fetch(`${BASE}/auth/logout`, { method: "POST", credentials: "same-origin" });
  } finally {
    setSession(null, null);
  }
}

// ----- chat stream (Server-Sent Events over a POST) -----

export interface StreamHandlers {
  onEvent: (type: string, data: any) => void;
}

export async function streamChat(courseId: number, body: object, signal: AbortSignal, h: StreamHandlers) {
  const res = await raw(`/courses/${courseId}/chat`, { method: "POST", body: JSON.stringify(body), signal });
  if (!res.ok || !res.body) throw new ApiError(await toProblem(res));
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let type = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) type = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) h.onEvent(type, JSON.parse(data));
    }
  }
}
