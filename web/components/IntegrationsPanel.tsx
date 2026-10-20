"use client";

import { useState } from "react";
import { ApiError, healthApi } from "@/lib/api";
import { Card, EmptyState, ErrorBox, Notice, Spinner } from "./ui";

function err(e: unknown): string {
  return e instanceof ApiError ? `Request failed (${e.status}): ${e.message}` : "Something went wrong.";
}

export function HealthPanel() {
  const [out, setOut] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function check() {
    setBusy(true);
    setError("");
    try {
      const [h, r] = await Promise.all([healthApi.health(), healthApi.ready()]);
      setOut(`API ${h.status} · Database ${r.checks?.database ?? "?"} · Queue ${r.checks?.redis ?? "?"}`);
    } catch (e) {
      setError(`Cannot reach the API: ${err(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="System health"
      hint="Liveness plus database and queue readiness."
      actions={
        <button type="button" className="btn btn-sm" onClick={() => void check()} disabled={busy}>
          {busy ? "Checking…" : "Check"}
        </button>
      }
    >
      {error ? <ErrorBox message={error} onRetry={() => void check()} /> : null}
      {out ? <Notice tone="ok">{out}</Notice> : null}
      {!out && !error && !busy ? (
        <EmptyState title="Not checked yet" hint="Run a health check to verify the backend." />
      ) : null}
      {busy ? <Spinner label="Probing services…" /> : null}
    </Card>
  );
}
