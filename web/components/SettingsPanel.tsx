"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, authApi, healthApi, obsApi } from "@/lib/api";
import { PermissionsPanel } from "./PermissionsPanel";
import { useAuth } from "./AuthProvider";
import { Badge, Card, ErrorBox, Notice, Skeleton, usd } from "./ui";

function err(e: unknown): string {
  return e instanceof ApiError ? `Request failed (${e.status}): ${e.message}` : "Something went wrong.";
}

function SystemPanel() {
  const [health, setHealth] = useState<{ status: string } | null>(null);
  const [ready, setReady] = useState<{ status: string; checks: Record<string, string> } | null>(null);
  const [costs, setCosts] = useState<Awaited<ReturnType<typeof obsApi.costs>> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [h, r, c] = await Promise.all([healthApi.health(), healthApi.ready(), obsApi.costs()]);
      setHealth(h);
      setReady(r);
      setCosts(c);
    } catch (e) {
      setError(err(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Card
      title="System"
      hint="Runtime health, dependency checks, and usage for this account."
      actions={
        <button type="button" className="btn btn-sm" onClick={() => void load()} disabled={loading}>
          Check now
        </button>
      }
    >
      {error ? <ErrorBox message={error} onRetry={() => void load()} /> : null}
      {loading ? <Skeleton rows={2} /> : null}

      {health && ready ? (
        <>
          <div className="row row-top" style={{ marginBottom: 16 }}>
            <Badge value={health.status} dot />
            {Object.entries(ready.checks ?? {}).map(([name, state]) => (
              <span key={name} className="row row-top tiny muted">
                {name}: <Badge value={state} dot />
              </span>
            ))}
          </div>
          {costs ? (
            <div className="stats">
              <div className="stat">
                <span className="stat-value">{costs.runs}</span>
                <span className="stat-label">Model runs</span>
              </div>
              <div className="stat">
                <span className="stat-value">{costs.total_tokens}</span>
                <span className="stat-label">Tokens</span>
              </div>
              <div className="stat">
                <span className="stat-value">{usd(costs.total_cost_usd)}</span>
                <span className="stat-label">Est. cost</span>
              </div>
            </div>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}

function AccountPanel() {
  const { user, logout, refreshUser } = useAuth();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  if (!user) return null;

  async function revokeAll() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await authApi.revokeAllSessions();
      setNotice("Every session was revoked. Other devices are signed out.");
      await refreshUser();
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Account" hint="Who you are signed in as, and how to end sessions.">
      {notice ? <Notice tone="ok">{notice}</Notice> : null}
      {error ? <ErrorBox message={error} /> : null}

      <dl className="kv">
        <div>
          <dt>Email</dt>
          <dd>{user.email}</dd>
        </div>
        <div>
          <dt>Name</dt>
          <dd>{user.display_name || "—"}</dd>
        </div>
        <div>
          <dt>User ID</dt>
          <dd className="mono tiny">{user.id}</dd>
        </div>
      </dl>

      <div className="row row-top" style={{ marginTop: 16 }}>
        <button type="button" className="btn" onClick={() => void revokeAll()} disabled={busy}>
          {busy ? "Revoking…" : "Sign out everywhere"}
        </button>
        <button type="button" className="btn btn-ghost" onClick={() => void logout()}>
          Sign out
        </button>
      </div>
    </Card>
  );
}

export function SettingsPanel() {
  return (
    <div className="stack-md">
      <PermissionsPanel />
      <AccountPanel />
      <SystemPanel />
    </div>
  );
}