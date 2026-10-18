"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, appApi, type App } from "@/lib/api";
import { Badge, Card, EmptyState, ErrorBox, Notice, Skeleton } from "./ui";

interface CatalogStatus {
  configured: boolean;
  reachable: boolean;
  app_count: number;
  reason: string;
}
import { useAuth } from "./AuthProvider";

function err(e: unknown): string {
  return e instanceof ApiError ? `Request failed (${e.status}): ${e.message}` : "Something went wrong.";
}

export function AppsPanel({ onNeedAuth }: { onNeedAuth: () => void }) {
  const { user } = useAuth();
  const [apps, setApps] = useState<App[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [pending, setPending] = useState<{ app: App; url: string } | null>(null);

  const [status, setStatus] = useState<CatalogStatus | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [list, st] = await Promise.all([appApi.catalog(), appApi.catalogStatus()]);
      setApps(list);
      setStatus(st);
    } catch (e) {
      setError(err(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function connect(app: App) {
    if (!user) {
      onNeedAuth();
      return;
    }
    setBusy(app.slug);
    setError("");
    try {
      const res = await appApi.connect(app.slug);
      setPending({ app, url: res.redirect_url });
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(null);
    }
  }

  async function confirm(app: App) {
    setBusy(app.slug);
    setError("");
    try {
      const res = await appApi.status(app.slug);
      if (res.state === "active") {
        setPending(null);
        await load();
      } else {
        setError(`Still “${res.state}”. Finish approving access in your browser, then try again.`);
      }
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(null);
    }
  }

  async function disconnect(app: App) {
    setBusy(app.slug);
    setError("");
    try {
      await appApi.disconnect(app.slug);
      await load();
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(null);
    }
  }

  const connected = apps?.filter((a) => a.state === "active").length ?? 0;

  return (
    <Card
      title="Connected apps"
      hint="PA only touches apps you connect and approve. Disconnect any time."
      actions={
        <>
          {apps ? (
            <span className="tiny muted">
              {connected} of {apps.length} connected
            </span>
          ) : null}
          <button type="button" className="btn btn-sm" onClick={() => void load()} disabled={loading}>
            Refresh
          </button>
        </>
      }
    >
      {!user ? (
        <Notice tone="info">
          You are browsing as a guest. Sign in to connect an app.
        </Notice>
      ) : null}

      {error ? <ErrorBox message={error} onRetry={() => void load()} /> : null}

      {pending ? (
        <Notice tone="info">
          <strong>{pending.app.name} — one more step.</strong>
          <div style={{ marginTop: 6 }}>
            Approve access in the authorisation page, then confirm below.
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <a className="btn btn-primary btn-sm" href={pending.url} target="_blank" rel="noreferrer noopener">
              Open authorisation page
            </a>
            <button
              type="button"
              className="btn btn-sm"
              disabled={busy === pending.app.slug}
              onClick={() => void confirm(pending.app)}
            >
              I approved it
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => setPending(null)}>
              Cancel
            </button>
          </div>
        </Notice>
      ) : null}

      {loading && !apps ? <Skeleton rows={4} /> : null}
      {apps && apps.length === 0 ? (
        status && status.configured && !status.reachable ? (
          <div className="alert alert-error" role="alert">
            <strong>Composio rejected your API key.</strong>
            <div style={{ marginTop: 4 }}>{status.reason}</div>
            <div style={{ marginTop: 4 }}>
              Re-copy the key from the Composio dashboard into <code>COMPOSIO_API_KEY</code> in
              <code>.env</code>, then restart the API container.
            </div>
          </div>
        ) : status && !status.configured ? (
          <div className="alert alert-info" role="status">
            <strong>No apps yet.</strong>
            <div style={{ marginTop: 4 }}>
              Set <code>COMPOSIO_API_KEY</code> in <code>.env</code> to load the catalog of apps
              you can connect.
            </div>
          </div>
        ) : (
          <EmptyState title="No apps available" hint={status?.reason || "Check back shortly."} />
        )
      ) : null}

      {apps && apps.length > 0 ? (
        <div className="grid grid-apps">
          {apps.map((a) => (
            <article key={a.slug} className="app-card">
              <div className="app-card-head">
                <div className="app-logo" aria-hidden="true">
                  {a.logo || "🔗"}
                </div>
                <div className="app-name" title={a.name}>
                  {a.name}
                </div>
                <Badge value={a.state} dot />
              </div>
              <p className="app-desc">{a.description}</p>
              <div className="row row-top">
                {a.state === "active" ? (
                  <button
                    type="button"
                    className="btn btn-sm"
                    disabled={busy === a.slug}
                    onClick={() => void disconnect(a)}
                  >
                    Disconnect
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    disabled={busy === a.slug}
                    onClick={() => void connect(a)}
                  >
                    {busy === a.slug ? "Working…" : "Connect"}
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </Card>
  );
}