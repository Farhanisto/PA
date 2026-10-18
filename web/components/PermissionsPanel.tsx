"use client";

import { useState } from "react";
import { useAuth } from "./AuthProvider";
import { Card, ErrorBox, Spinner } from "./ui";

const PERMS: Record<string, { label: string; hint: string; risky?: boolean }> = {
  "email.read": { label: "Read email", hint: "Fetch and triage messages so PA can summarise them." },
  "email.send": { label: "Send email", hint: "Required to send. Each send still needs your approval." },
  "email.proactive": { label: "Proactive briefings", hint: "Scheduled inbox summaries and follow-up reminders." },
  "email.auto_send": {
    label: "Send without approval",
    hint: "Bypasses the approval step. Leave off unless you fully trust the assistant.",
    risky: true,
  },
};

export function PermissionsPanel() {
  const { user, setPermission } = useAuth();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  if (!user) return null;

  async function toggle(key: string, granted: boolean) {
    setBusy(key);
    setError("");
    try {
      await setPermission(key, granted);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not update permission.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card title="Permissions" hint="You decide what PA may do. Changes apply immediately and are audited.">
      {Object.entries(PERMS).map(([key, meta]) => {
        const id = `perm-${key}`;
        const on = Boolean(user.permissions[key]);
        return (
          <div
            key={key}
            style={{
              display: "flex",
              gap: 16,
              alignItems: "flex-start",
              padding: "16px 0",
              borderBottom: "1px solid var(--border)",
            }}
          >
            <div style={{ flex: 1, minWidth: 200 }}>
              <label htmlFor={id} style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
                {meta.label}
                {meta.risky ? (
                  <span className="badge badge-danger">risky</span>
                ) : null}
              </label>
              <p className="small muted" style={{ marginTop: 4 }}>
                {meta.hint}
              </p>
            </div>
            <div className="row row-top" style={{ flexShrink: 0 }}>
              {busy === key ? <Spinner /> : null}
              <button
                id={id}
                type="button"
                role="switch"
                aria-checked={on}
                className={`btn btn-sm ${on ? "btn-primary" : ""}`}
                disabled={busy === key}
                onClick={() => void toggle(key, !on)}
              >
                {on ? "On" : "Off"}
              </button>
            </div>
          </div>
        );
      })}
      {error ? <ErrorBox message={error} /> : null}
    </Card>
  );
}
