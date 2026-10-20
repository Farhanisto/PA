"use client";

import { useState } from "react";
import type { Artifact, ArtifactAction } from "@/lib/api";

/* Icons are inline SVG (stroke-based, 24px grid) so they inherit colour and
 * scale with the type scale — no icon font, no image requests. */

const ICON_PATHS: Record<string, string> = {
  email:
    "M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z|m3 7 9 6 9-6",
  call: "M22 16.92v3a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.9.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z",
  meeting:
    "M8 2v4M16 2v4M3 10h18|M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
  task: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z|m9 12 2 2 4-4",
  note: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z|M14 2v6h6",
  contact: "M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2|M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  deal: "M8 2h8a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z|M16 8h4a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2h-4",
  company:
    "M6 2h8a2 2 0 0 1 2 2v18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z|M8 6h1M12 6h1M8 10h1M12 10h1M8 14h1M12 14h1M10 22v-4h4v4",
  ticket:
    "M12.6 2.6A2 2 0 0 0 11.2 2H4a2 2 0 0 0-2 2v7.2a2 2 0 0 0 .6 1.4l8.7 8.7a2.4 2.4 0 0 0 3.4 0l6.6-6.6a2.4 2.4 0 0 0 0-3.4z|M7.5 7.5h.01",
  message:
    "M21 11.5a8.4 8.4 0 0 1-9 8.4 8.9 8.9 0 0 1-4-.9L3 21l1.9-5a8.4 8.4 0 0 1-.9-3.9 8.4 8.4 0 0 1 8.5-8.4 8.4 8.4 0 0 1 8.5 7.8z",
  generic: "M3 5h18v14H3z|M3 10h18M9 19V10",
};

/** Map a record type/kind to an icon name. */
function iconFor(artifact: Artifact): string {
  const t = artifact.type.toLowerCase();
  const k = artifact.kind.toLowerCase();
  if (t.includes("email") || t.includes("mail")) return "email";
  if (t.includes("call")) return "call";
  if (t.includes("meeting") || t.includes("event")) return "meeting";
  if (t.includes("task")) return "task";
  if (t.includes("note")) return "note";
  if (k.includes("contact") || k.includes("lead") || k.includes("person")) return "contact";
  if (k.includes("deal") || k.includes("opportunit")) return "deal";
  if (k.includes("company") || k.includes("account") || k.includes("organi")) return "company";
  if (k.includes("ticket") || k.includes("issue")) return "ticket";
  if (k.includes("message") || k.includes("mail")) return "message";
  return "generic";
}

function Icon({ name }: { name: string }) {
  const d = ICON_PATHS[name] ?? ICON_PATHS.generic;
  return (
    <svg
      className="artifact-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {d.split("|").map((p, i) => (
        <path key={i} d={p} />
      ))}
    </svg>
  );
}

const APP_LABELS: Record<string, string> = {
  hubspot: "HubSpot",
  gmail: "Gmail",
  outlook: "Outlook",
  slack: "Slack",
  notion: "Notion",
  salesforce: "Salesforce",
};

function appLabel(app: string): string {
  if (!app) return "App";
  return APP_LABELS[app] ?? app.charAt(0).toUpperCase() + app.slice(1);
}

const BODY_PREVIEW_CHARS = 260;

function ArtifactCard({
  artifact,
  onAction,
  onSelect,
  selected,
  disabled,
}: {
  artifact: Artifact;
  onAction: (action: ArtifactAction, card: Artifact) => void;
  onSelect?: (card: Artifact) => void;
  selected?: boolean;
  disabled?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const long = artifact.body.length > BODY_PREVIEW_CHARS;
  const body = expanded || !long ? artifact.body : `${artifact.body.slice(0, BODY_PREVIEW_CHARS)}…`;
  const badges = artifact.badges ?? [];

  return (
    <article
      className="artifact"
      data-kind={artifact.kind}
      aria-selected={selected || undefined}
      onClick={onSelect ? () => onSelect(artifact) : undefined}
      onKeyDown={
        onSelect
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect(artifact);
              }
            }
          : undefined
      }
      role={onSelect ? "button" : undefined}
      tabIndex={onSelect ? 0 : undefined}
    >
      <header className="artifact-head">
        <span className="artifact-type" title={artifact.type}>
          <Icon name={iconFor(artifact)} />
          <span>{artifact.type || artifact.kind}</span>
        </span>
        {badges.map((b, i) => (
          <span key={`${b.label}-${i}`} className={`artifact-badge badge-${b.tone}`} title={b.label}>
            {b.value}
          </span>
        ))}
        <span className="artifact-app">{appLabel(artifact.app)}</span>
        {artifact.meta ? <span className="artifact-meta">{artifact.meta}</span> : null}
      </header>

      <div className="artifact-main">
        <h3 className="artifact-title">{artifact.title}</h3>
        {artifact.subtitle ? <p className="artifact-subtitle">{artifact.subtitle}</p> : null}
        {body ? <p className="artifact-body">{body}</p> : null}
        {long ? (
          <button
            type="button"
            className="artifact-more"
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((v) => !v);
            }}
            aria-expanded={expanded}
          >
            {expanded ? "Show less" : "Show more"}
          </button>
        ) : null}
      </div>

      {artifact.fields.length > 0 ? (
        <dl className="artifact-fields">
          {artifact.fields.map((f, i) => (
            <div key={`${f.label}-${i}`} className="artifact-field">
              <dt>{f.label}</dt>
              <dd>{f.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {artifact.actions.length > 0 ? (
        <footer className="artifact-actions">
          {artifact.actions.map((a) => (
            <button
              key={a.id}
              type="button"
              className={`btn btn-sm ${a.kind === "reply" ? "btn-primary" : "btn-ghost"}`}
              disabled={disabled}
              onClick={(e) => {
                e.stopPropagation();
                onAction(a, artifact);
              }}
            >
              {a.label}
            </button>
          ))}
        </footer>
      ) : null}
    </article>
  );
}

/**
 * The structured records a run surfaced, rendered as cards the user can act
 * on directly. This is the assistant's main engagement surface for app data.
 *
 * Cards that carry a group are shown under a heading, so a briefing reads
 * as sections rather than one long list.
 */
export function ArtifactCards({
  artifacts,
  onAction,
  onSelect,
  selectedId,
  disabled,
}: {
  artifacts: Artifact[];
  onAction: (action: ArtifactAction, card: Artifact) => void;
  onSelect?: (card: Artifact) => void;
  selectedId?: string | null;
  disabled?: boolean;
}) {
  if (artifacts.length === 0) return null;

  const groups: { name: string; items: Artifact[] }[] = [];
  for (const card of artifacts) {
    const name = card.group ?? "";
    const last = groups[groups.length - 1];
    if (last && last.name === name) last.items.push(card);
    else groups.push({ name, items: [card] });
  }

  return (
    <div className="artifact-groups">
      {groups.map((group, i) => (
        <div key={`${group.name}-${i}`} className="artifact-group">
          {group.name ? <h4 className="artifact-group-title">{group.name}</h4> : null}
          <div className="artifact-grid">
            {group.items.map((a) => (
              <ArtifactCard
                key={a.id}
                artifact={a}
                onAction={onAction}
                onSelect={onSelect}
                selected={selectedId === a.id}
                disabled={disabled}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
