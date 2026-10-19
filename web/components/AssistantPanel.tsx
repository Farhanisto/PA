"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  appApi,
  assistantApi,
  jobApi,
  type Connection,
  type Job,
} from "@/lib/api";
import { Badge, Card, EmptyState, ErrorBox, Notice } from "./ui";

function err(e: unknown): string {
  return e instanceof ApiError ? `Request failed (${e.status}): ${e.message}` : "Something went wrong.";
}

interface Turn {
  id: string;
  prompt: string;
  jobId: string;
  status: string;
  result: string | null;
  errorCode: string | null;
}

const TERMINAL = new Set(["completed", "failed", "waiting_for_approval"]);
const POLL_MS = 1200;

const SUGGESTIONS = [
  "Summarise what needs my attention today",
  "Draft a reply to the most urgent message",
  "Give me a daily briefing",
];

export function AssistantPanel({ seed, onSeedConsumed }: { seed?: string; onSeedConsumed?: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [apps, setApps] = useState<Connection[] | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  // Show which apps the assistant can actually reach.
  const loadApps = useCallback(async () => {
    try {
      setApps(await appApi.connections());
    } catch {
      setApps([]);
    }
  }, []);

  useEffect(() => {
    void loadApps();
  }, [loadApps]);

  const update = useCallback((jobId: string, patch: Partial<Turn>) => {
    setTurns((prev) => prev.map((t) => (t.jobId === jobId ? { ...t, ...patch } : t)));
  }, []);

  // Poll the most recent non-terminal run until it settles.
  const pending = turns.filter((t) => !TERMINAL.has(t.status));
  const watching = pending.length > 0;

  useEffect(() => {
    if (!watching) return;
    let cancelled = false;
    const id = setInterval(() => {
      for (const t of pending) {
        jobApi
          .get(t.jobId)
          .then((job: Job) => {
            if (cancelled) return;
            update(job.job_id, {
              status: job.status,
              result: job.result,
              errorCode: job.error_code,
            });
          })
          .catch((e) => {
            if (!cancelled) setError(err(e));
          });
      }
    }, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [watching, pending, update]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [turns]);

  // A tool picked in the Workspace prefills the composer.
  useEffect(() => {
    if (seed) {
      setDraft(seed);
      onSeedConsumed?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seed]);

  async function send(text: string) {
    const prompt = text.trim();
    if (!prompt || busy) return;
    setBusy(true);
    setError("");
    setDraft("");
    try {
      const accepted = await assistantApi.submit(prompt);
      setTurns((prev) => [
        ...prev,
        {
          id: `${accepted.job_id}-${prev.length}`,
          prompt,
          jobId: accepted.job_id,
          status: accepted.status,
          result: null,
          errorCode: null,
        },
      ]);
    } catch (e) {
      setError(err(e));
      setDraft(prompt);
    } finally {
      setBusy(false);
    }
  }

  async function briefing() {
    setBusy(true);
    setError("");
    try {
      const accepted = await assistantApi.briefing();
      setTurns((prev) => [
        ...prev,
        {
          id: `${accepted.job_id}-${prev.length}`,
          prompt: "Daily briefing",
          jobId: accepted.job_id,
          status: accepted.status,
          result: null,
          errorCode: null,
        },
      ]);
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Assistant"
      hint="Ask for something. Work runs in the background; anything sensitive waits for your approval."
      actions={
        <>
          <button type="button" className="btn btn-sm" onClick={() => void briefing()} disabled={busy}>
            Daily briefing
          </button>
          <button type="button" className="btn btn-sm" onClick={() => setTurns([])} disabled={!turns.length}>
            Clear
          </button>
        </>
      }
    >
      {error ? <ErrorBox message={error} /> : null}

      <div className="app-scope">
        {apps === null ? (
          <span className="tiny muted">Checking connected apps…</span>
        ) : apps.length === 0 ? (
          <span className="tiny muted">
            No apps connected — the assistant can&apos;t do anything yet. Connect one in Apps.
          </span>
        ) : (
          <>
            <span className="tiny muted">PA can work in:</span>
            {apps.map((a) => (
              <span key={a.provider} className="scope-chip">
                {a.name}
                {a.state === "active" ? null : " (not connected)"}
              </span>
            ))}
          </>
        )}
      </div>

      <div className="chat" ref={logRef} aria-live="polite" aria-label="Conversation">
        {turns.length === 0 ? (
          <EmptyState
            title="Nothing asked yet"
            hint="Try one of the suggestions below, or type your own request."
          />
        ) : null}

        {turns.map((t) => (
          <div key={t.id} className="chat-turn">
            <div className="chat-msg chat-user">
              <div className="chat-role">You</div>
              <div className="chat-bubble">{t.prompt}</div>
            </div>
            <div className="chat-msg chat-agent">
              <div className="chat-role">
                PA
                {TERMINAL.has(t.status) ? (
                  <Badge value={t.status} dot />
                ) : (
                  <span className="spinner" aria-label="Working" />
                )}
              </div>
              <div className="chat-bubble">
                {t.status === "completed" && t.result ? (
                  <pre className="chat-pre">{t.result}</pre>
                ) : t.status === "failed" ? (
                  <span className="chat-error">{t.errorCode || "The run failed."}</span>
                ) : t.status === "waiting_for_approval" ? (
                  <span>
                    This needs your approval before it can continue — open the Approvals section.
                  </span>
                ) : (
                  <span className="muted">
                    {t.status === "queued" ? "Queued…" : "Working on it…"}
                  </span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {turns.length === 0 ? (
        <div className="chips">
          {SUGGESTIONS.map((s) => (
            <button key={s} type="button" className="chip" onClick={() => void send(s)} disabled={busy}>
              {s}
            </button>
          ))}
        </div>
      ) : null}

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          void send(draft);
        }}
      >
        <label htmlFor="composer-input" className="sr-only">
          Message the assistant
        </label>
        <textarea
          id="composer-input"
          rows={2}
          placeholder="Ask the assistant to do something…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(draft);
            }
          }}
        />
        <button type="submit" className="btn btn-primary" disabled={busy || !draft.trim()}>
          {busy ? "Sending…" : "Send"}
        </button>
      </form>

      {watching ? (
        <Notice tone="info">
          A run is still going. You can keep sending messages; results appear above as they finish.
        </Notice>
      ) : null}
    </Card>
  );
}