"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  appApi,
  jobApi,
  obsApi,
  type Connection,
  type Job,
  type ToolCall,
} from "@/lib/api";
import { Badge, Card, EmptyState, ErrorBox, Skeleton, usd } from "./ui";

function err(e: unknown): string {
  return e instanceof ApiError ? `Request failed (${e.status}): ${e.message}` : "Something went wrong.";
}

/** One row of the unified activity feed. */
type FeedItem =
  | { kind: "tool"; at: number; id: string; source: string; title: string; detail: string; status: string }
  | { kind: "job"; at: number; id: string; source: string; title: string; detail: string; status: string };

function when(iso: string | null | undefined): number {
  if (!iso) return 0;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? 0 : t;
}

function relTime(ts: number): string {
  if (!ts) return "";
  const secs = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (secs < 60) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

/** "gmail_fetch_inbox" -> "Gmail", so the feed reads as devices, not internals. */
function deviceOf(name: string): string {
  const root = name.split("_")[0] ?? name;
  return root.charAt(0).toUpperCase() + root.slice(1);
}

function summarizeArgs(args: Record<string, unknown>): string {
  const keys = Object.keys(args ?? {});
  if (!keys.length) return "";
  return keys
    .slice(0, 3)
    .map((k) => {
      const v = args[k];
      if (v === null || v === undefined) return k;
      const s = typeof v === "object" ? JSON.stringify(v) : String(v);
      return `${k}=${s.length > 40 ? `${s.slice(0, 40)}…` : s}`;
    })
    .join(" · ");
}

export function DashboardPanel() {
  const [connections, setConnections] = useState<Connection[] | null>(null);
  const [calls, setCalls] = useState<ToolCall[] | null>(null);
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [costs, setCosts] = useState<Awaited<ReturnType<typeof obsApi.costs>> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [c, t, j, k] = await Promise.all([
        appApi.connections(),
        obsApi.toolCalls(30),
        jobApi.list(15),
        obsApi.costs(),
      ]);
      setConnections(c);
      setCalls(t);
      setJobs(j);
      setCosts(k);
    } catch (e) {
      setError(err(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), 30_000);
    return () => clearInterval(id);
  }, [load]);

  const feed = useMemo<FeedItem[]>(() => {
    const items: FeedItem[] = [];
    for (const c of calls ?? []) {
      items.push({
        kind: "tool",
        at: when(c.created_at),
        id: c.id,
        source: deviceOf(c.tool_name),
        title: c.tool_name.replace(/_/g, " "),
        detail: summarizeArgs(c.args) || c.output_summary,
        status: c.status,
      });
    }
    for (const j of jobs ?? []) {
      items.push({
        kind: "job",
        at: when(j.created_at),
        id: j.job_id,
        source: "Assistant",
        title: j.request || "Assistant run",
        detail: j.result ?? j.error_code ?? "",
        status: j.status,
      });
    }
    return items.sort((a, b) => b.at - a.at).slice(0, 40);
  }, [calls, jobs]);

  const active = (connections ?? []).filter((c) => c.state === "active");
  const failures = feed.filter((f) => f.status === "error" || f.status === "failed").length;
  const devicesUsed = new Set(feed.map((f) => f.source)).size;

  return (
    <div className="stack-md">
      <Card
        title="Connected devices"
        hint="Every app PA can act on, and its current link state."
        actions={
          <button type="button" className="btn btn-sm" onClick={() => void load()} disabled={loading}>
            Refresh
          </button>
        }
      >
        {error ? <ErrorBox message={error} onRetry={() => void load()} /> : null}
        {loading && !connections ? <Skeleton rows={2} /> : null}
        {connections && connections.length === 0 ? (
          <EmptyState
            title="No apps connected"
            hint="Connect an app in the Apps section to give the assistant something to work with."
          />
        ) : null}
        {active.length > 0 ? (
          <div className="grid grid-apps">
            {active.map((c) => (
              <div key={c.provider} className="app-card app-card-static">
                <div className="app-logo" aria-hidden="true">
                  🔗
                </div>
                <div className="app-name">{c.name}</div>
                <Badge value={c.state} dot />
              </div>
            ))}
          </div>
        ) : null}
      </Card>

      <Card title="Activity" hint="Everything the assistant and your connected apps have done, newest first.">
        <div className="stats">
          <div className="stat">
            <span className="stat-value">{active.length}</span>
            <span className="stat-label">Apps connected</span>
          </div>
          <div className="stat">
            <span className="stat-value">{devicesUsed}</span>
            <span className="stat-label">Devices used</span>
          </div>
          <div className="stat">
            <span className="stat-value">{feed.length}</span>
            <span className="stat-label">Recent actions</span>
          </div>
          <div className="stat">
            <span className="stat-value">{failures}</span>
            <span className="stat-label">Failures</span>
          </div>
          {costs ? (
            <div className="stat">
              <span className="stat-value">{usd(costs.total_cost_usd)}</span>
              <span className="stat-label">{costs.total_tokens} tokens</span>
            </div>
          ) : null}
        </div>

        {loading && !calls ? <Skeleton rows={4} /> : null}
        {!loading && feed.length === 0 ? (
          <EmptyState
            title="No activity yet"
            hint="Ask the assistant something, or sync an inbox, and it will show up here."
          />
        ) : null}

        {feed.length > 0 ? (
          <ol className="feed">
            {feed.map((f) => (
              <li key={`${f.kind}-${f.id}`} className="feed-row">
                <span className="feed-dot" data-status={f.status} aria-hidden="true" />
                <div className="feed-body">
                  <div className="feed-head">
                    <span className="feed-source">{f.source}</span>
                    <Badge value={f.status} dot />
                    <span className="tiny muted">{relTime(f.at)}</span>
                  </div>
                  <div className="feed-title">{f.title}</div>
                  {f.detail ? <div className="feed-detail small muted">{f.detail}</div> : null}
                </div>
              </li>
            ))}
          </ol>
        ) : null}
      </Card>
    </div>
  );
}