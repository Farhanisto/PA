"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, authApi, healthApi, obsApi, settingsApi, type Schedule } from "@/lib/api";
import { PermissionsPanel } from "./PermissionsPanel";
import { useAuth } from "./AuthProvider";
import { Badge, Card, ErrorBox, Notice, Skeleton, usd } from "./ui";

const TIMEZONES = [
  "UTC",
  "Africa/Nairobi",
  "Africa/Lagos",
  "Africa/Johannesburg",
  "Europe/London",
  "Europe/Berlin",
  "Europe/Paris",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
];

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

function SchedulePanel() {
  const [schedule, setSchedule] = useState<Schedule | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSchedule(await settingsApi.schedule());
    } catch (e) {
      setError(err(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(patch: Partial<Schedule>) {
    if (!schedule) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const next = await settingsApi.setSchedule(patch);
      setSchedule(next);
      setNotice("Schedule saved.");
    } catch (e) {
      setError(err(e));
    } finally {
      setSaving(false);
    }
  }

  const tz = schedule?.timezone || "UTC";

  return (
    <Card
      title="Update schedule"
      hint="Choose when the assistant should proactively brief you. Times are local to you."
      actions={
        <button type="button" className="btn btn-sm" onClick={() => void load()} disabled={loading}>
          Reload
        </button>
      }
    >
      {loading ? <Skeleton rows={2} /> : null}
      {notice ? <Notice tone="ok">{notice}</Notice> : null}
      {error ? <ErrorBox message={error} /> : null}

      {schedule ? (
        <>
          <div className="schedule-row">
            <div className="schedule-text">
              <strong>Daily inbox briefing</strong>
              <span className="small muted">
                A summary of what needs your attention.
              </span>
            </div>
            <input
              type="time"
              value={`${String(schedule.briefing_hour).padStart(2, "0")}:${String(schedule.briefing_minute).padStart(2, "0")}`}
              onChange={(e) => {
                const [h, m] = e.target.value.split(":").map(Number);
                void save({ briefing_hour: h, briefing_minute: m });
              }}
              disabled={saving}
              aria-label="Daily briefing time"
            />
            <button
              type="button"
              role="switch"
              aria-checked={schedule.briefing_enabled}
              className={`btn btn-sm ${schedule.briefing_enabled ? "btn-primary" : ""}`}
              disabled={saving}
              onClick={() => void save({ briefing_enabled: !schedule.briefing_enabled })}
            >
              {schedule.briefing_enabled ? "On" : "Off"}
            </button>
          </div>

          <div className="schedule-row">
            <div className="schedule-text">
              <strong>Follow-up reminders</strong>
              <span className="small muted">Threads waiting on a reply from you.</span>
            </div>
            <input
              type="time"
              value={`${String(schedule.followup_hour).padStart(2, "0")}:${String(schedule.followup_minute).padStart(2, "0")}`}
              onChange={(e) => {
                const [h, m] = e.target.value.split(":").map(Number);
                void save({ followup_hour: h, followup_minute: m });
              }}
              disabled={saving}
              aria-label="Follow-up reminder time"
            />
            <button
              type="button"
              role="switch"
              aria-checked={schedule.followup_enabled}
              className={`btn btn-sm ${schedule.followup_enabled ? "btn-primary" : ""}`}
              disabled={saving}
              onClick={() => void save({ followup_enabled: !schedule.followup_enabled })}
            >
              {schedule.followup_enabled ? "On" : "Off"}
            </button>
          </div>

          <div className="field" style={{ maxWidth: 320, marginTop: 16 }}>
            <label htmlFor="tz-select">Timezone</label>
            <select
              id="tz-select"
              value={tz}
              disabled={saving}
              onChange={(e) => void save({ timezone: e.target.value })}
            >
              {TIMEZONES.map((z) => (
                <option key={z} value={z}>
                  {z}
                </option>
              ))}
            </select>
            <span className="help">Times above are interpreted in this timezone.</span>
          </div>
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
      <SchedulePanel />
      <PermissionsPanel />
      <AccountPanel />
      <SystemPanel />
    </div>
  );
}