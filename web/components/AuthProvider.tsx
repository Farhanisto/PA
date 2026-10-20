"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  ApiError,
  authApi,
  setSessionToken,
  type ApiUser,
} from "@/lib/api";

interface AuthState {
  user: ApiUser | null;
  loading: boolean;
  error: string;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, displayName?: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  setPermission: (key: string, granted: boolean) => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<ApiUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refreshUser = useCallback(async () => {
    try {
      const me = await authApi.me();
      setUser(me);
      setError("");
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) {
        setError(e instanceof Error ? e.message : "Could not reach the API.");
      }
      setUser(null);
    }
  }, []);

  useEffect(() => {
    // The session cookie is httpOnly, so try it on first paint.
    void refreshUser().finally(() => setLoading(false));
  }, [refreshUser]);

  const login = useCallback(async (email: string, password: string) => {
    setError("");
    try {
      const res = await authApi.login(email, password);
      setSessionToken(res.access_token);
      setUser(res.user);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Login failed.";
      setError(msg);
      throw e;
    }
  }, []);

  const signup = useCallback(async (email: string, password: string, displayName = "") => {
    setError("");
    try {
      const res = await authApi.signup(email, password, displayName);
      setSessionToken(res.access_token);
      setUser(res.user);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Sign up failed.";
      setError(msg);
      throw e;
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      setSessionToken(null);
      setUser(null);
    }
  }, []);

  const setPermission = useCallback(async (key: string, granted: boolean) => {
    const perms = await authApi.setPermission(key, granted);
    setUser((u) => (u ? { ...u, permissions: perms } : u));
  }, []);

  const value = useMemo(
    () => ({ user, loading, error, login, signup, logout, refreshUser, setPermission }),
    [user, loading, error, login, signup, logout, refreshUser, setPermission]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}