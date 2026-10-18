"use client";

import { useEffect, useState } from "react";
import { ApiError, emailApi, type Draft, type EmailMessage } from "@/lib/api";
import { Badge, Card, EmptyState, ErrorBox, Notice, Skeleton } from "./ui";

function err(e: unknown): string {
  return e instanceof ApiError ? `Request failed (${e.status}): ${e.message}` : "Something went wrong.";
}

export function DraftsPanel({
  seedMessage,
  onSent,
}: {
  seedMessage: EmailMessage | null;
  onSent: () => void;
}) {
  const [drafts, setDrafts] = useState<Draft[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<Draft | null>(null);
  const [body, setBody] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      setDrafts(await emailApi.drafts());
    } catch (e) {
      setError(err(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Generate a reply when requested from the inbox.
  useEffect(() => {
    if (!seedMessage) return;
    let cancelled = false;
    setBusy(true);
    setNotice("Generating a draft…");
    (async () => {
      try {
        const d = await emailApi.generateDraft(seedMessage.id);
        if (cancelled) return;
        setEditing(d);
        setBody(d.body);
        setNotice("Draft ready — review and edit before sending.");
        await load();
      } catch (e) {
        if (!cancelled) setError(err(e));
      } finally {
        if (!cancelled) {
          setBusy(false);
          if (!cancelled) setNotice("");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedMessage]);

  async function save() {
    if (!editing) return;
    setBusy(true);
    setError("");
    try {
      const updated = await emailApi.updateDraft(editing.id, { body });
      setEditing(updated);
      setNotice("Draft saved.");
      await load();
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  }

  async function requestSend(d: Draft) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const res = await emailApi.requestSend(d.id);
      setNotice(`Approval requested. Review it in the Approvals tab to send.`);
      void res;
      await load();
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(d: Draft) {
    setBusy(true);
    try {
      await emailApi.deleteDraft(d.id);
      if (editing?.id === d.id) setEditing(null);
      await load();
      onSent();
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="Drafts"
      hint="Every reply is editable. Nothing leaves your outbox without your approval."
      actions={
        <button type="button" className="btn btn-sm" onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
      }
    >
      {notice ? <Notice tone="info">{notice}</Notice> : null}
      {error ? <ErrorBox message={error} onRetry={() => void load()} /> : null}

      {editing ? (
        <div className="card" style={{ marginTop: 20, borderColor: "var(--brand)" }}>
          <div className="card-head">
            <h2>Editing draft to {editing.to_addr}</h2>
            <p>{editing.subject || "(no subject)"}</p>
          </div>
          <div className="field">
            <label htmlFor="draft-editor">Reply body</label>
            <textarea id="draft-editor" rows={12} value={body} onChange={(e) => setBody(e.target.value)} />
          </div>
          <div className="row" style={{ marginTop: 16 }}>
            <button type="button" className="btn" onClick={() => void save()} disabled={busy}>
              Save changes
            </button>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={() => {
                setEditing(null);
                setBody("");
              }}
            >
              Done
            </button>
          </div>
        </div>
      ) : null}

      {loading && !drafts ? <Skeleton rows={3} /> : null}
      {drafts && drafts.length === 0 ? (
        <EmptyState title="No drafts" hint="Open Inbox and choose “Draft reply” on any message." />
      ) : null}

      <div className="list">
        {drafts?.map((d) => (
          <article key={d.id} className="msg-card">
            <div className="msg-head">
              <Badge value={d.status} dot />
              <span className="msg-subject">To {d.to_addr}</span>
              <span className="msg-from">{d.subject || "(no subject)"}</span>
            </div>
            <p className="msg-summary small">
              {d.body.length > 500 ? `${d.body.slice(0, 500)}…` : d.body}
            </p>

            {d.status === "draft" ? (
              <div className="row row-top">
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => {
                    setEditing(d);
                    setBody(d.body);
                  }}
                >
                  Edit
                </button>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  disabled={busy}
                  onClick={() => void requestSend(d)}
                >
                  Request approval
                </button>
                <button type="button" className="btn btn-ghost btn-sm" disabled={busy} onClick={() => void remove(d)}>
                  Delete
                </button>
              </div>
            ) : d.sent_at ? (
              <p className="tiny" style={{ color: "var(--ok)" }}>
                Sent {new Date(d.sent_at).toLocaleString()}
                {d.sent_message_id ? ` · ${d.sent_message_id}` : ""}
              </p>
            ) : null}
          </article>
        ))}
      </div>
    </Card>
  );
}