"use client";

import { useState } from "react";
import { useAuth } from "./AuthProvider";
import { Field, Spinner } from "./ui";

export function LoginForm({ onCancel }: { onCancel?: () => void }) {
  const { login, signup, error } = useAuth();
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      if (mode === "login") await login(email, password);
      else await signup(email, password, name);
    } catch {
      // surfaced from auth context
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      <div className="card-head">
        <h2>{mode === "login" ? "Welcome back" : "Create your account"}</h2>
        <p>
          {mode === "login"
            ? "Sign in to your assistant."
            : "One account per person. Your data stays isolated."}
        </p>
      </div>

      <div className="stack-md">
        {mode === "signup" ? (
          <Field label="Name" help="Optional.">
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={128} autoComplete="name" />
          </Field>
        ) : null}

        <Field label="Email">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="email"
            autoFocus
          />
        </Field>

        <Field
          label="Password"
          help={mode === "signup" ? "At least 10 characters, with upper case, lower case, and a digit." : undefined}
        >
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete={mode === "login" ? "current-password" : "new-password"}
          />
        </Field>

        {error ? (
          <div className="alert alert-error" role="alert">
            {error}
          </div>
        ) : null}

        <button type="submit" className="btn btn-primary" disabled={busy || !email || !password}>
          {busy ? <Spinner label="Please wait…" /> : mode === "login" ? "Sign in" : "Create account"}
        </button>

        <div className="row row-top" style={{ justifyContent: "space-between" }}>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setMode(mode === "login" ? "signup" : "login")}>
            {mode === "login" ? "Need an account?" : "Already registered?"}
          </button>
          {onCancel ? (
            <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>
              Cancel
            </button>
          ) : null}
        </div>
      </div>
    </form>
  );
}
