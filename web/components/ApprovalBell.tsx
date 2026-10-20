"use client";

import { useEffect, useState } from "react";
import { approvalApi } from "@/lib/api";

/**
 * Pending-approval counter for the sidebar. Polls cheaply so a run that
 * pauses for approval surfaces without the user hunting for it.
 */
export function ApprovalBell({ onNavigate }: { onNavigate: () => void }) {
  const [pending, setPending] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      approvalApi
        .list("pending")
        .then((rows) => {
          if (!cancelled) setPending(rows.length);
        })
        .catch(() => {
          /* the badge is decorative; ignore transient failures */
        });
    };
    tick();
    const id = setInterval(tick, 20_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const count = pending ?? 0;

  return (
    <button type="button" onClick={onNavigate} className={count ? "nav-alert" : undefined}>
      <span className="nav-icon" aria-hidden="true">
        ✓
      </span>
      Approvals
      {count > 0 ? (
        <span className="nav-count" aria-label={`${count} awaiting approval`}>
          {count}
        </span>
      ) : null}
    </button>
  );
}