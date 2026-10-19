"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  appApi,
  approvalApi,
  emailApi,
  jobApi,
  type AppInsights,
  type Approval,
  type App,
  type Connection,
  type EmailMessage,
  type Job,
} from "@/lib/api";
import { Badge, Card, EmptyState, ErrorBox, Skeleton, usd } from "./ui";

function err(e: unknown): string {
  return e instanceof ApiError ? `Request failed (${e.status}): ${e.message}` : "Something went wrong.";
}

type Attention =
  | { kind: "email"; id: string; title: string; detail: string; badge: string; at: string | null }
  | { kind: "approval"; id: string; title: string; detail: string; badge: string; at: string | null }
  | { kind: "failure"; id: string; title: string; detail: string; badge: string; at: string | null };

function titleCase(slug: string): string {
  return slug.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function when(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const mins = Math.round((Date.now() - t) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

function describeApproval(a: Approval): string {
  const p = a.payload ?? {};
  if (a.action === "email_send_draft") {
    return `Email to ${String(p.to_addr ?? "")}${p.subject ? ` — “${String(p.subject)}”` : ""}`;
  }
  return String(p.summary ?? a.action);
}

/**
 * Dashboard: a summary of what needs attention across connected apps.
 * Deliberately not a raw activity log — only actionable, high-priority
 * items are surfaced.
 */
export function DashboardPanel({ onGoTo }: { onGoTo?: (section: string) => void }) {
  const [connections, setConnections] = useState<Connection[] | null>(null);
  const [high, setHigh] = useState<EmailMessage[] | null>(null);
  const [approvals, setApprovals] = useState<Approval[] | null>(null);
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [briefing, setBriefing] = useState<string>("");
  const [insights, setInsights] = useState<Record<string, AppInsights>>({});
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [c, h, a, j] = await Promise.all([
        appApi.connections(),
        emailApi.messages({ priority: "high", limit: 50 }),
        approvalApi.list("pending"),
        jobApi.list(10),
      ]);
      setConnections(c);
      setHigh(h);
      setApprovals(a);
      setJobs(j);

      // Per-app dashboard summaries (HubSpot pipeline, inbox, ...).
      const active = c.filter((x) => x.state === "active").map((x) => x.provider);
      if (active.length) {
        const rows = await Promise.all(
          active.map((slug) => appApi.insights(slug).catch(() => null))
        );
        const next: Record<string, AppInsights> = {};
        rows.forEach((r, i) => {
          if (r) next[active[i]] = r;
        });
        setInsights(next);
      } else {
        setInsights({});
      }
    } catch (e) {
      setError(err(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), 60_000);
    return () => clearInterval(id);
  }, [load]);

  async function generateSummary() {
    setRunning(true);
    setError("");
    setBriefing("");
    try {
      const accepted = await jobApi.submit("Give me a short summary of what needs attention today");
      let status = accepted.status;
      for (let i = 0; i < 20; i += 1) {
        await new Promise((r) => setTimeout(r, 1500));
        const job = await jobApi.get(accepted.job_id);
        status = job.status;
        if (status === "completed" || status === "failed") {
          setBriefing(job.result ?? "");
          break;
        }
      }
      if (status === "queued" || status === "running") {
        setBriefing("Still working — it will appear in your jobs shortly.");
      }
    } catch (e) {
      setError(err(e));
    } finally {
      setRunning(false);
      await load();
    }
  }

  const active = (connections ?? []).filter((c) => c.state === "active");
  const failed = (jobs ?? []).filter((j) => j.status === "failed");

  const attention: Attention[] = [
    ...(approvals ?? []).map((a) => ({
      kind: "approval" as const,
      id: a.approval_id,
      title: "Waiting for your approval",
      detail: describeApproval(a),
      badge: "approval",
      at: null,
    })),
    ...(high ?? []).map((m) => ({
      kind: "email" as const,
      id: m.id,
      title: m.subject || "(no subject)",
      detail: [m.from_addr, m.deadlines.length ? `due: ${m.deadlines.join(", ")}` : "", m.needs_reply ? "needs a reply" : ""]
        .filter(Boolean)
        .join(" · "),
      badge: m.needs_reply ? "needs reply" : "high",
      at: m.received_at,
    })),
    ...failed.map((j) => ({
      kind: "failure" as const,
      id: j.job_id,
      title: "A task failed",
      detail: `${j.request.slice(0, 80)}${j.error_code ? ` (${j.error_code})` : ""}`,
      badge: "failed",
      at: j.created_at,
    })),
  ];

  return (
    <div className="stack-md">
      <Card
        title="Summary"
        hint="A short read on what needs you today, across your connected apps."
        actions={
          <button type="button" className="btn btn-primary btn-sm" onClick={() => void generateSummary()} disabled={running}>
            {running ? "Summarising…" : "Summarise now"}
          </button>
        }
      >
        {error ? <ErrorBox message={error} onRetry={() => void load()} /> : null}
        <div className="stats">
          <div className="stat">
            <span className="stat-value">{active.length}</span>
            <span className="stat-label">Apps connected</span>
          </div>
          <div className="stat">
            <span className="stat-value">{(approvals ?? []).length}</span>
            <span className="stat-label">Awaiting approval</span>
          </div>
          <div className="stat">
            <span className="stat-value">{(high ?? []).length}</span>
            <span className="stat-label">High priority</span>
          </div>
          <div className="stat">
            <span className="stat-value">{failed.length}</span>
            <span className="stat-label">Failed</span>
          </div>
        </div>
        {briefing ? <div className="briefing">{briefing}</div> : null}
        {!briefing && !loading ? (
          <p className="small muted">
            {attention.length === 0
              ? "Nothing needs you right now."
              : `${attention.length} item${attention.length === 1 ? "" : "s"} need attention below.`}
          </p>
        ) : null}
      </Card>

      {Object.keys(insights).length > 0 ? (
        <>
          {Object.values(insights).map((ins) => (
            <Card
              key={ins.app}
              title={titleCase(ins.app)}
              hint={ins.headline}
            >
              {ins.stats.length > 0 ? (
                <div className="stats">
                  {ins.stats.map((s) => (
                    <div key={s.label} className="stat">
                      <span className="stat-value">{s.value}</span>
                      <span className="stat-label">{s.label}</span>
                    </div>
                  ))}
                </div>
              ) : null}
              {ins.items.length > 0 ? (
                <ul className="insight-items">
                  {ins.items.map((item, i) => (
                    <li key={`${item.title}-${i}`} className="insight-row">
                      <div className="insight-body">
                        <div className="insight-title">{item.title}</div>
                        {item.detail ? <div className="small muted">{item.detail}</div> : null}
                      </div>
                      {item.meta ? <span className="tiny muted">{item.meta}</span> : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </Card>
          ))}
        </>
      ) : null}

      <Card
        title="Needs your attention"
        hint="Only high-priority items are listed. Routine activity stays out of the way."
      >
        {loading && !high ? <Skeleton rows={3} /> : null}
        {attention.length === 0 && !loading ? (
          <EmptyState
            title="All clear"
            hint="No high-priority mail, pending approvals, or failures."
          />
        ) : null}

        {attention.length > 0 ? (
          <ul className="attention">
            {attention.map((item) => (
              <li key={`${item.kind}-${item.id}`} className="attention-row">
                <Badge value={item.badge} dot />
                <div className="attention-body">
                  <div className="attention-title">{item.title}</div>
                  {item.detail ? <div className="small muted">{item.detail}</div> : null}
                </div>
                {item.at ? <span className="tiny muted">{when(item.at)}</span> : null}
                {item.kind === "approval" && onGoTo ? (
                  <button type="button" className="btn btn-sm" onClick={() => onGoTo("approvals")}>
                    Review
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
      </Card>

      <Card title="Connected apps" hint="What the assistant can currently act on.">
        {loading && !connections ? <Skeleton rows={2} /> : null}
        {connections && connections.length === 0 ? (
          <EmptyState title="No apps connected" hint="Connect an app to give the assistant something to work with." />
        ) : null}
        {active.length > 0 ? (
          <div className="grid grid-apps">
            {active.map((c) => (
              <div key={c.provider} className="app-card app-card-static">
                <div className="app-logo" aria-hidden="true">🔗</div>
                <div className="app-name">{c.name}</div>
                <Badge value={c.state} dot />
              </div>
            ))}
          </div>
        ) : null}
      </Card>
    </div>
  );
}