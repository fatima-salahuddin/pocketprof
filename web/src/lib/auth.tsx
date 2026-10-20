import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { onSessionChange, refreshSession } from "./api";
import type { User } from "./types";

interface AuthState {
  user: User | null;
  ready: boolean;
}

const Ctx = createContext<AuthState>({ user: null, ready: false });

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ user: null, ready: false });

  useEffect(() => {
    const off = onSessionChange((user) => setState({ user, ready: true }));
    // A returning visitor still holds the refresh cookie: restore the session silently.
    refreshSession().then((user) => setState({ user, ready: true }));
    return () => {
      off();
    };
  }, []);

  return <Ctx.Provider value={state}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);

export function homeFor(user: User): string {
  return user.role === "admin" ? "/admin" : user.role === "teacher" ? "/teacher" : "/courses";
}
