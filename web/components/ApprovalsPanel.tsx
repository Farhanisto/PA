"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, approvalApi, type Approval } from "@/lib/api";
import { Badge, Card, EmptyState, ErrorBox, Notice, Skeleton } from "./ui";

function err(e: unknown): string {
  return e instanceof ApiError ? `Request failed (${e.status}): ${e.message}` : "Something went wrong.";
}

function describe(a: Approval): string {
  const p = a.payload ?? {};
  if (a.action === "email_send_draft") {
    const to = String(p.to_addr ?? "");
    const subject = String(p.subject ?? "");
    return `Send email to ${to}${subject ? ` — “${subject}”` : ""}`;
  }
  return String(p.summary ?? p.request ?? a.action);
}

export function ApprovalsPanel({
  refreshToken,
  onDecided,
}: {
  refreshToken: number;
  onDecided: () => void;
}) {
  const [items, setItems] = useState<Approval[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [acting, setActing] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setItems(await approvalApi.list("pending"));
    } catch (e) {
      setError(err(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  async function decide(id: string, action: "approve" | "reject") {
    setActing(id + action);
    setError("");
    setNotice("");
    try {
      const res = action === "approve" ? await approvalApi.approve(id) : await approvalApi.reject(id);
      setNotice(
        action === "approve"
          ? `Approved — running now (${res.status}).`
          : `Rejected — nothing was sent (${res.status}).`
      );
      await load();
      onDecided();
    } catch (e) {
      setError(err(e));
    } finally {
      setActing(null);
    }
  }

  return (
    <Card
      title="Approvals"
      hint="Nothing is sent without you. The stored proposal is what runs — it cannot be swapped here."
      actions={
        <button type="button" className="btn btn-sm" onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
      }
    >
      {notice ? <Notice tone={notice.startsWith("Approved") ? "ok" : "info"}>{notice}</Notice> : null}
      {error ? <ErrorBox message={error} onRetry={() => void load()} /> : null}

      {loading && !items ? <Skeleton rows={3} /> : null}
      {items && items.length === 0 ? (
        <EmptyState title="All clear" hint="No actions are waiting for your approval." />
      ) : null}

      <div className="list">
        {items?.map((a) => (
          <article key={a.approval_id} className="msg-card priority-high">
            <div className="msg-head">
              <Badge value={a.action} dot />
              <span className="mono tiny muted">{a.approval_id.slice(0, 8)}</span>
            </div>
            <p>{describe(a)}</p>
            <div className="row row-top">
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={acting !== null}
                onClick={() => void decide(a.approval_id, "approve")}
              >
                {acting === a.approval_id + "approve" ? "Approving…" : "Approve & send"}
              </button>
              <button
                type="button"
                className="btn btn-sm"
                disabled={acting !== null}
                onClick={() => void decide(a.approval_id, "reject")}
              >
                Reject
              </button>
            </div>
          </article>
        ))}
      </div>
    </Card>
  );
}