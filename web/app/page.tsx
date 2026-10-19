"use client";

import { useState } from "react";
import { AuthProvider, useAuth } from "@/components/AuthProvider";
import { AppsPanel } from "@/components/AppsPanel";
import { ApprovalBell } from "@/components/ApprovalBell";
import { ApprovalsPanel } from "@/components/ApprovalsPanel";
import { AppWorkspace } from "@/components/AppWorkspace";
import { AssistantPanel } from "@/components/AssistantPanel";
import { DashboardPanel } from "@/components/DashboardPanel";
import { DraftsPanel } from "@/components/DraftsPanel";
import { EvalsPanel } from "@/components/EvalsPanel";
import { HealthPanel } from "@/components/IntegrationsPanel";
import { InboxPanel } from "@/components/InboxPanel";
import { LoginForm } from "@/components/LoginForm";
import { ObservabilityPanel } from "@/components/ObservabilityPanel";
import { SettingsPanel } from "@/components/SettingsPanel";
import { Spinner } from "@/components/ui";
import type { EmailMessage } from "@/lib/api";

type SectionId =
  | "dashboard"
  | "workspace"
  | "assistant"
  | "apps"
  | "settings"
  | "inbox"
  | "drafts"
  | "approvals"
  | "traces"
  | "evals"
  | "health";

type NavItem = { id: SectionId; label: string; icon: string; hint?: string };
type NavGroup = { group: string; primary?: boolean; items: NavItem[] };

const NAV: NavGroup[] = [
  {
    group: "PA",
    primary: true,
    items: [
      { id: "dashboard", label: "Dashboard", icon: "◎", hint: "What needs you today" },
      { id: "assistant", label: "Assistant", icon: "✦", hint: "Talk to the agent" },
      { id: "workspace", label: "Workspace", icon: "◫", hint: "One interface for every app" },
      { id: "apps", label: "Apps", icon: "⊞", hint: "Connect or disconnect apps" },
      { id: "settings", label: "Settings", icon: "⚙", hint: "Permissions and system" },
    ],
  },
  {
    group: "Email",
    items: [
      { id: "inbox", label: "Inbox", icon: "✉" },
      { id: "drafts", label: "Drafts", icon: "✍" },
    ],
  },
  {
    group: "Operations",
    items: [
      { id: "traces", label: "Traces", icon: "⌁" },
      { id: "evals", label: "Evals", icon: "⚗" },
      { id: "health", label: "Health", icon: "●" },
    ],
  },
];

/** Sections a signed-out visitor may open without an account. */
const PUBLIC_SECTIONS = new Set<SectionId>(["dashboard", "apps", "health"]);

function Brand() {
  return (
    <div className="brand">
      <div className="brand-mark" aria-hidden="true">
        PA
      </div>
      <div className="brand-text">
        <strong>PA</strong>
        <span>Assistant for your apps</span>
      </div>
    </div>
  );
}

function Sidebar({
  section,
  onNavigate,
  email,
  onSignIn,
  onSignOut,
}: {
  section: SectionId;
  onNavigate: (id: SectionId) => void;
  email: string | null;
  onSignIn: () => void;
  onSignOut: () => void;
}) {
  return (
    <aside className="sidebar">
      <Brand />

      <nav className="nav" aria-label="Sections">
        {NAV.map((g) => (
          <div key={g.group} className={g.primary ? "nav-primary" : undefined}>
            <div className="nav-label">{g.group}</div>
            {g.items.map((s) => (
              <button
                key={s.id}
                type="button"
                title={s.hint}
                aria-current={section === s.id ? "page" : undefined}
                onClick={() => onNavigate(s.id)}
              >
                <span className="nav-icon" aria-hidden="true">
                  {s.icon}
                </span>
                {s.label}
              </button>
            ))}
            {g.primary && email ? (
              <ApprovalBell onNavigate={() => onNavigate("approvals")} />
            ) : null}
          </div>
        ))}
      </nav>

      <div className="sidebar-foot">
        {email ? (
          <>
            <div className="sidebar-user">
              Signed in as
              <strong>{email}</strong>
            </div>
            <button type="button" className="btn btn-sm" style={{ width: "100%" }} onClick={onSignOut}>
              Sign out
            </button>
          </>
        ) : (
          <>
            <div className="sidebar-user">
              Browsing as
              <strong>guest</strong>
            </div>
            <button type="button" className="btn btn-primary btn-sm" style={{ width: "100%" }} onClick={onSignIn}>
              Sign in
            </button>
          </>
        )}
      </div>
    </aside>
  );
}

function Hero({ onSignIn }: { onSignIn: () => void }) {
  return (
    <header className="hero">
      <h1>One assistant for every app you use.</h1>
      <p>
        PA connects to the tools you already rely on, handles the routine work in the background,
        and asks before it ever acts on your behalf.
      </p>
      <div className="row" style={{ marginTop: 24 }}>
        <button type="button" className="btn btn-primary" onClick={onSignIn}>
          Get started
        </button>
      </div>
    </header>
  );
}

function Gate({ section, onSignIn }: { section: SectionId; onSignIn: () => void }) {
  const label = NAV.flatMap((g) => g.items).find((i) => i.id === section)?.label ?? "this section";
  return (
    <div className="card">
      <div className="card-head">
        <h2>Sign in to open {label}</h2>
        <p>Browsing is free. You only need an account to act on your apps.</p>
      </div>
      <button type="button" className="btn btn-primary" onClick={onSignIn}>
        Sign in or create an account
      </button>
    </div>
  );
}

function Shell() {
  const { user, loading, logout } = useAuth();
  const [section, setSection] = useState<SectionId>("dashboard");
  const [gateOpen, setGateOpen] = useState(false);
  const [approvalPing, setApprovalPing] = useState(0);
  const [seedMessage, setSeedMessage] = useState<EmailMessage | null>(null);
  const [assistantSeed, setAssistantSeed] = useState<string>("");

  if (loading) {
    return (
      <div className="shell">
        <main className="main">
          <Spinner label="Loading…" />
        </main>
      </div>
    );
  }

  const signedIn = Boolean(user);

  function navigate(id: SectionId) {
    // Landing on a gated section while signed out: ask them to sign in first.
    if (!signedIn && !PUBLIC_SECTIONS.has(id)) {
      setGateOpen(true);
      return;
    }
    setSection(id);
  }

  function draftReplyFor(m: EmailMessage) {
    setSeedMessage(m);
    setSection("drafts");
  }

  function body() {
    if (!signedIn) {
      if (section === "health") return <HealthPanel />;
      return (
        <>
          {section === "dashboard" ? <Hero onSignIn={() => setGateOpen(true)} /> : null}
          <AppsPanel onNeedAuth={() => setGateOpen(true)} />
        </>
      );
    }

    switch (section) {
      case "dashboard":
        return <DashboardPanel />;
      case "assistant":
        return <AssistantPanel seed={assistantSeed} onSeedConsumed={() => setAssistantSeed("")} />;
      case "workspace":
        return (
          <AppWorkspace
            onAskAssistant={(prompt) => {
              setAssistantSeed(prompt);
              setSection("assistant");
            }}
            onGoToApps={() => setSection("apps")}
          />
        );
      case "apps":
        return <AppsPanel onNeedAuth={() => setGateOpen(true)} />;
      case "settings":
        return <SettingsPanel />;
      case "inbox":
        return <InboxPanel onDraftFor={draftReplyFor} />;
      case "drafts":
        return (
          <DraftsPanel
            seedMessage={seedMessage}
            onSent={() => {
              setSeedMessage(null);
              setApprovalPing((n) => n + 1);
            }}
          />
        );
      case "approvals":
        return (
          <ApprovalsPanel
            refreshToken={approvalPing}
            onDecided={() => setApprovalPing((n) => n + 1)}
          />
        );
      case "traces":
        return <ObservabilityPanel />;
      case "evals":
        return <EvalsPanel />;
      case "health":
        return <HealthPanel />;
      default:
        return null;
    }
  }

  return (
    <div className="shell">
      <Sidebar
        section={section}
        onNavigate={navigate}
        email={user?.email ?? null}
        onSignIn={() => setGateOpen(true)}
        onSignOut={() => void logout()}
      />

      <main className="main">
        <div className="main-inner">
          {signedIn || PUBLIC_SECTIONS.has(section) ? (
            body()
          ) : (
            <Gate section={section} onSignIn={() => setGateOpen(true)} />
          )}
        </div>
      </main>

      {gateOpen ? (
        <div className="scrim" role="dialog" aria-modal="true" aria-label="Sign in">
          <div className="card">
            <LoginForm onCancel={() => setGateOpen(false)} />
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default function Page() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}