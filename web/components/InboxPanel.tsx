"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, emailApi, type EmailMessage, type Priority } from "@/lib/api";
import { Badge, Card, EmptyState, ErrorBox, Skeleton } from "./ui";

function err(e: unknown): string {
  return e instanceof ApiError ? `Request failed (${e.status}): ${e.message}` : "Something went wrong.";
}

const PRIORITIES: Array<Priority | "all"> = ["all", "high", "normal", "low"];

export function InboxPanel({ onDraftFor }: { onDraftFor: (m: EmailMessage) => void }) {
  const [msgs, setMsgs] = useState<EmailMessage[] | null>(null);
  const [filter, setFilter] = useState<Priority | "all">("all");
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setMsgs(await emailApi.messages({ priority: filter === "all" ? undefined : filter }));
    } catch (e) {
      setError(err(e));
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    void load();
  }, [load]);

  async function sync() {
    setSyncing(true);
    setError("");
    setNotice("");
    try {
      const res = await emailApi.sync(25);
      setNotice(`Fetched ${res.fetched} · triaged ${res.triaged}.`);
      await load();
    } catch (e) {
      setError(err(e));
    } finally {
      setSyncing(false);
    }
  }

  const counts = msgs
    ? PRIORITIES.slice(1).reduce<Record<string, number>>((acc, p) => {
        acc[p] = msgs.filter((m) => m.priority === p).length;
        return acc;
      }, {})
    : null;

  return (
    <Card
      title="Inbox"
      hint="Messages are summarised, prioritised, and scanned for deadlines and replies."
      actions={
        <>
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value as Priority | "all")}
            aria-label="Filter by priority"
            style={{ width: "auto", minWidth: 130 }}
          >
            {PRIORITIES.map((p) => (
              <option key={p} value={p}>
                {p === "all" ? "All priorities" : p}
              </option>
            ))}
          </select>
          <button type="button" className="btn btn-sm" onClick={() => void load()} disabled={loading}>
            Refresh
          </button>
          <button type="button" className="btn btn-primary btn-sm" onClick={() => void sync()} disabled={syncing}>
            {syncing ? "Syncing…" : "Sync & triage"}
          </button>
        </>
      }
    >
      {counts ? (
        <p className="small muted" aria-live="polite">
          {notice ? `${notice} · ` : ""}
          {counts.high} high · {counts.normal} normal · {counts.low} low
        </p>
      ) : null}
      {error ? <ErrorBox message={error} onRetry={() => void load()} /> : null}

      {loading && !msgs ? <Skeleton rows={5} /> : null}
      {msgs && msgs.length === 0 ? (
        <EmptyState title="Nothing here yet" hint="Run a sync to pull and triage your mail." />
      ) : null}

      <div className="list">
        {msgs?.map((m) => (
          <article key={m.id} className={`msg-card priority-${m.priority}`}>
            <div className="msg-head">
              <Badge value={m.priority} dot />
              {m.needs_reply ? <Badge value="needs reply" /> : null}
              <span className="msg-subject">{m.subject || "(no subject)"}</span>
              <span className="msg-from">{m.from_addr}</span>
            </div>
            {m.summary ? <p className="msg-summary small">{m.summary}</p> : null}
            {m.deadlines.length > 0 ? (
              <p className="small">
                <strong>Deadline:</strong> {m.deadlines.join(", ")}
              </p>
            ) : null}
            <div className="row row-top">
              <button
                type="button"
                className="btn btn-sm"
                aria-expanded={expanded === m.id}
                onClick={() => setExpanded(expanded === m.id ? null : m.id)}
              >
                {expanded === m.id ? "Hide details" : "Details"}
              </button>
              <button type="button" className="btn btn-primary btn-sm" onClick={() => onDraftFor(m)}>
                Draft reply
              </button>
            </div>
            {expanded === m.id ? (
              <div className="stack-sm" style={{ marginTop: 4 }}>
                {m.action_items.length > 0 ? (
                  <div className="small">
                    <strong>Action items</strong>
                    <ul style={{ margin: "6px 0 0", paddingLeft: 20 }}>
                      {m.action_items.map((a, i) => (
                        <li key={i}>{a}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {m.snippet ? <p className="small muted">{m.snippet}</p> : null}
              </div>
            ) : null}
          </article>
        ))}
      </div>
    </Card>
  );
}