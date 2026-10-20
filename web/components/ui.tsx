"use client";

import React from "react";

/* Shared presentational primitives. All styling comes from globals.css. */

export function Card({
  title,
  hint,
  actions,
  children,
}: {
  title: string;
  hint?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="card" aria-label={title}>
      <header className="card-head">
        <div className="row row-top" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <h2>{title}</h2>
            {hint ? <p>{hint}</p> : null}
          </div>
          {actions ? <div className="row row-top">{actions}</div> : null}
        </div>
      </header>
      {children}
    </section>
  );
}

const TONE: Record<string, string> = {
  queued: "badge-neutral",
  running: "badge-info",
  pending: "badge-warn",
  waiting_for_approval: "badge-warn",
  completed: "badge-ok",
  sent: "badge-ok",
  active: "badge-ok",
  pass: "badge-ok",
  ok: "badge-ok",
  connected: "badge-ok",
  approved: "badge-ok",
  draft: "badge-neutral",
  normal: "badge-info",
  high: "badge-danger",
  live: "badge-ok",
  rejected: "badge-danger",
  failed: "badge-danger",
  error: "badge-danger",
  fail: "badge-danger",
  unavailable: "badge-neutral",
  none: "badge-neutral",
};

export function Badge({ value, dot }: { value: string; dot?: boolean }) {
  const tone = TONE[value.toLowerCase()] ?? "badge-neutral";
  const label = value.replace(/_/g, " ");
  return (
    <span className={`badge ${tone}`}>
      {dot ? <span className="badge-dot" aria-hidden="true" /> : null}
      {label}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row row-top tiny muted" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      {label ? <span>{label}</span> : null}
    </span>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="stack-sm" aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton" style={{ width: `${100 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="alert alert-error" role="alert">
      <div style={{ flex: 1 }}>
        <strong>Something went wrong.</strong>
        <div style={{ marginTop: 4 }}>{message}</div>
      </div>
      {onRetry ? (
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export function Notice({ children, tone = "info" }: { children: React.ReactNode; tone?: "info" | "ok" }) {
  return (
    <div className={`alert alert-${tone}`} role="status" aria-live="polite">
      <div style={{ flex: 1 }}>{children}</div>
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="empty">
      <strong>{title}</strong>
      {hint ? <span>{hint}</span> : null}
    </div>
  );
}

export function Field({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  const id = React.useId();
  const child =
    React.isValidElement(children)
      ? React.cloneElement(children as React.ReactElement<{ id?: string }>, { id })
      : children;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {child}
      {help ? <span className="help">{help}</span> : null}
    </div>
  );
}

export function Table({
  head,
  children,
}: {
  head: string[];
  children: React.ReactNode;
}) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function usd(n: number): string {
  if (n === 0) return "$0";
  return n < 0.01 ? "<$0.01" : `$${n.toFixed(4)}`;
}