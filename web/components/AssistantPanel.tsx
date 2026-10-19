"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  appApi,
  assistantApi,
  emailApi,
  jobApi,
  type Artifact,
  type ArtifactAction,
  type AssistantFocus,
  type Connection,
  type Job,
} from "@/lib/api";
import { ArtifactCards } from "./ArtifactCards";
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

/** An inline reply composer, anchored to the turn that opened it. */
interface ReplyDraft {
  key: string;
  jobId: string;
  recipient: string;
  subject: string;
  body: string;
  notice: string;
  busy: boolean;
}

const TERMINAL = new Set(["completed", "failed", "waiting_for_approval"]);
const POLL_MS = 1200;

const SUGGESTIONS = [
  "Summarise what needs my attention today",
  "Draft a reply to the most urgent message",
  "Give me a daily briefing",
];

/** "5 tickets", "3 contacts" — the noun for whatever the cards are. */
function recordNoun(cards: Artifact[]): string {
  const type = cards[0]?.type?.toLowerCase() ?? "";
  const plural = type.endsWith("s") ? type : `${type}s`;
  return cards.length === 1 ? type || "record" : plural || "records";
}

/** The record a card refers to, in the shape the assistant expects. */
function focusOf(card: Artifact): AssistantFocus {
  const recordId =
    card.fields.find((f) => /id$/i.test(f.label) && f.label !== "Engagement ID")?.value ??
    card.fields.find((f) => f.label.endsWith("ID"))?.value ??
    "";
  return {
    id: card.id,
    app: card.app,
    kind: card.kind,
    type: card.type,
    title: card.title,
    record_id: recordId,
    fields: card.fields,
    meta: card.meta,
  };
}

export function AssistantPanel({ seed, onSeedConsumed }: { seed?: string; onSeedConsumed?: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [apps, setApps] = useState<Connection[] | null>(null);
  const [artifacts, setArtifacts] = useState<Record<string, Artifact[]>>({});
  const [reply, setReply] = useState<ReplyDraft | null>(null);
  // The record the user clicked: the next message is about it, and the
  // assistant acts on that record rather than guessing which one.
  const [focus, setFocus] = useState<AssistantFocus | null>(null);
  // Groups the turns of one exchange so a short answer to a clarifying
  // question ("tickets") is understood in the context of the question.
  const conversationId = useRef<string>("");
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

  // Load the structured records a finished run surfaced, so they can be
  // rendered as cards rather than prose.
  const requested = useRef<Set<string>>(new Set());
  useEffect(() => {
    const wanted = turns.filter(
      (t) => t.status === "completed" && !(t.jobId in artifacts) && !requested.current.has(t.jobId)
    );
    if (wanted.length === 0) return;
    for (const t of wanted) requested.current.add(t.jobId);
    (async () => {
      for (const t of wanted) {
        try {
          const cards = await jobApi.artifacts(t.jobId);
          setArtifacts((prev) => ({ ...prev, [t.jobId]: cards }));
        } catch {
          setArtifacts((prev) => ({ ...prev, [t.jobId]: [] }));
        }
      }
    })();
  }, [turns, artifacts]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [turns, artifacts]);

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
      const accepted = await assistantApi.submit(prompt, conversationId.current, focus);
      if (accepted.conversation_id) conversationId.current = accepted.conversation_id;
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

  /**
   * Open the inline reply composer and have PA draft the body.
   *
   * Drafting goes straight to the compose endpoint rather than through the
   * agent loop: the assistant must not be able to turn "draft a reply" into
   * a tool call that sends something. Nothing is sent until the user asks.
   */
  async function openReply(jobId: string, context: string, recipient = "", subject = "") {
    const key = `${jobId}-${Date.now()}`;
    setReply({
      key,
      jobId,
      recipient,
      subject,
      body: "",
      notice: "Generating a reply…",
      busy: true,
    });
    try {
      const res = await emailApi.composeDraft({
        context,
        sender: recipient,
        subject,
        instructions: "Reply appropriately.",
      });
      setReply((prev) =>
        prev && prev.key === key
          ? {
              ...prev,
              body: res.body,
              busy: false,
              notice: res.body
                ? "Reply ready — edit the recipient and body, then create the draft."
                : "Could not draft a reply. Write your own below.",
            }
          : prev
      );
    } catch (e) {
      setReply((prev) =>
        prev && prev.key === key ? { ...prev, busy: false, notice: err(e) } : prev
      );
    }
  }

  /** A card action: reply inline, or ask the assistant to act on it. */
  function onCardAction(action: ArtifactAction, card: Artifact, jobId: string) {
    // Every action works on this exact record, so it is focused first.
    setFocus(focusOf(card));
    if (action.kind === "reply") {
      const context = [card.title, card.subtitle, card.body].filter(Boolean).join("\n");
      void openReply(jobId, context, action.recipient ?? "", action.subject ?? "");
      return;
    }
    if (action.kind === "ask" && action.prompt) {
      setDraft(action.prompt);
      return;
    }
    setDraft("");
  }

  /** Clicking a card focuses it, so the next message is about that record. */
  function onCardSelect(card: Artifact) {
    setFocus((prev) => (prev?.id === card.id ? null : focusOf(card)));
  }

  async function createDraft() {
    if (!reply) return;
    if (!reply.recipient.trim() || !reply.body.trim()) {
      setReply((prev) =>
        prev ? { ...prev, notice: "Please fill in at least the recipient and reply body." } : prev
      );
      return;
    }
    setReply((prev) => (prev ? { ...prev, busy: true, notice: "" } : prev));
    try {
      await emailApi.createDraft({
        to_addr: reply.recipient,
        subject: reply.subject || "(no subject)",
        body: reply.body,
      });
      setReply((prev) =>
        prev
          ? { ...prev, busy: false, notice: "Draft created — review it in the Drafts section before sending." }
          : prev
      );
    } catch (e) {
      setReply((prev) => (prev ? { ...prev, busy: false, notice: err(e) } : prev));
    }
  }

  function closeReply() {
    setReply(null);
  }

  return (
    <Card
      title="Assistant"
      hint="Ask for anything. Work runs in the background; anything sensitive waits for your approval."
      actions={
        <>
          <button type="button" className="btn btn-sm" onClick={() => void briefing()} disabled={busy}>
            Daily briefing
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={() => {
              setTurns([]);
              setArtifacts({});
              // Clearing starts a new conversation: the next message is not
              // an answer to what was just discarded.
              conversationId.current = "";
            }}
            disabled={!turns.length}
          >
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

        {turns.map((t) => {
          const completed = t.status === "completed" && t.result;
          const cards = artifacts[t.jobId] ?? [];
          const isReply = reply?.jobId === t.jobId;
          return (
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
                  {completed ? (
                    cards.length > 0 ? (
                      // The records are the answer; the prose is a footnote.
                      <details className="result-summary">
                        <summary>PA&apos;s summary</summary>
                        <pre className="chat-pre">{t.result}</pre>
                      </details>
                    ) : (
                      <div className="result-card">
                        <pre className="chat-pre">{t.result}</pre>
                      </div>
                    )
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

              {cards.length > 0 ? (
                <div className="artifact-block">
                  <div className="artifact-block-head">
                    <span className="artifact-count">
                      {cards.length} {recordNoun(cards)}
                    </span>
                    <span className="tiny muted">Act on any of them</span>
                  </div>
                  <ArtifactCards
                    artifacts={cards}
                    onAction={(action, card) => onCardAction(action, card, t.jobId)}
                    onSelect={onCardSelect}
                    selectedId={focus?.id ?? null}
                    disabled={busy}
                  />
                  {completed ? (
                    <div className="result-actions artifact-actions-row">
                      <button
                        type="button"
                        className="btn btn-sm"
                        disabled={isReply && Boolean(reply?.busy)}
                        onClick={() => void openReply(t.jobId, t.result ?? "")}
                      >
                        Draft reply
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => setDraft("Can you expand on that?")}
                      >
                        Follow up
                      </button>
                    </div>
                  ) : null}
                </div>
              ) : null}

              {isReply && reply ? (
                <div className="reply-composer">
                  <div className="field">
                    <label htmlFor={`reply-to-${reply.key}`}>To</label>
                    <input
                      id={`reply-to-${reply.key}`}
                      value={reply.recipient}
                      onChange={(e) => setReply((p) => (p ? { ...p, recipient: e.target.value } : p))}
                      placeholder="recipient@example.com"
                      disabled={reply.busy}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor={`reply-subject-${reply.key}`}>Subject</label>
                    <input
                      id={`reply-subject-${reply.key}`}
                      value={reply.subject}
                      onChange={(e) => setReply((p) => (p ? { ...p, subject: e.target.value } : p))}
                      placeholder="(no subject)"
                      disabled={reply.busy}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor={`reply-body-${reply.key}`}>Reply body</label>
                    <textarea
                      id={`reply-body-${reply.key}`}
                      rows={6}
                      value={reply.body}
                      onChange={(e) => setReply((p) => (p ? { ...p, body: e.target.value } : p))}
                      placeholder="Type your reply…"
                      disabled={reply.busy}
                    />
                  </div>
                  {reply.notice ? <Notice tone="info">{reply.notice}</Notice> : null}
                  <div className="row row-top" style={{ marginTop: 8 }}>
                    <button
                      type="button"
                      className="btn btn-primary"
                      disabled={reply.busy || !reply.recipient.trim() || !reply.body.trim()}
                      onClick={() => void createDraft()}
                    >
                      {reply.busy ? "Working…" : "Create draft"}
                    </button>
                    <button type="button" className="btn btn-ghost" onClick={closeReply}>
                      Close
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          );
        })}
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

      {focus ? (
        <div className="focus-chip" role="status">
          <span className="focus-chip-type">{focus.type || focus.kind || "Record"}</span>
          <span className="focus-chip-title">{focus.title}</span>
          {focus.record_id ? (
            <span className="focus-chip-id mono">ID {focus.record_id}</span>
          ) : null}
          <span className="focus-chip-hint tiny muted">
            Your next message will be about this record
          </span>
          <button
            type="button"
            className="focus-chip-clear"
            onClick={() => setFocus(null)}
            aria-label="Stop working on this record"
            title="Stop working on this record"
          >
            ✕
          </button>
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
          placeholder={
            focus
              ? `Ask about ${focus.title || "this record"}…`
              : "Ask the assistant to do something…"
          }
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
