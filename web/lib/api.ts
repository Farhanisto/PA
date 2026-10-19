/* Typed client for the PA FastAPI backend.
 *
 * Auth: opaque session token. The server sets an httpOnly `pa_session`
 * cookie on signup/login; we also keep the token in memory so the SPA can
 * send `Authorization: Bearer`. Never persisted to localStorage.
 */

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export type Priority = "high" | "normal" | "low";

// --- session (module-scoped, intentionally not persisted) ---
let sessionToken: string | null = null;
export function setSessionToken(token: string | null): void {
  sessionToken = token;
}
export function getSessionToken(): string | null {
  return sessionToken;
}

export interface ApiUser {
  id: string;
  email: string;
  display_name: string;
  permissions: Record<string, boolean>;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: ApiUser;
}

export interface EmailMessage {
  id: string;
  provider_message_id: string;
  from_addr: string;
  subject: string;
  snippet: string;
  received_at: string | null;
  summary: string;
  priority: Priority;
  needs_reply: boolean;
  action_items: string[];
  deadlines: string[];
  triaged: boolean;
}

export interface Draft {
  id: string;
  message_id: string | null;
  in_reply_to: string;
  to_addr: string;
  subject: string;
  body: string;
  status: "draft" | "approved" | "sent" | "rejected" | "failed";
  created_at: string | null;
  updated_at: string | null;
  sent_at: string | null;
  sent_message_id: string | null;
}

export interface Approval {
  approval_id: string;
  job_id: string;
  thread_id: string;
  action: string;
  payload: Record<string, unknown>;
  status: string;
}

export interface App {
  slug: string;
  name: string;
  description: string;
  logo: string;
  state: "active" | "pending" | "none" | "unavailable";
}

export interface Connection {
  provider: string;
  name: string;
  connected: boolean;
  state: string;
}

export interface RunTrace {
  id: string;
  job_id: string;
  thread_id: string;
  node: string;
  status: string;
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  error: string;
  created_at: string | null;
}

export interface ToolCall {
  id: string;
  job_id: string;
  tool_name: string;
  args: Record<string, unknown>;
  status: string;
  latency_ms: number;
  output_summary: string;
  error: string;
  created_at: string | null;
}

export interface ObservabilitySummary {
  job_id: string;
  traces: RunTrace[];
  tool_calls: ToolCall[];
  total_latency_ms: number;
  total_cost_usd: number;
  failures: number;
}

export interface EvalCaseResult {
  name: string;
  passed: boolean;
  detail: string;
}

export interface EvalSuiteResult {
  suite: string;
  model_backed?: boolean;
  total: number;
  passed: number;
  failed: number;
  score: number;
  results: EvalCaseResult[];
}

export interface EvalAllResult {
  total: number;
  passed: number;
  failed: number;
  score: number;
  suites: EvalSuiteResult[];
}

export interface Job {
  job_id: string;
  thread_id: string;
  status: string;
  request: string;
  result: string | null;
  error_code: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ArtifactField {
  label: string;
  value: string;
}

/** A small coloured tag on a card, e.g. Priority: HIGH. */
export interface ArtifactBadge {
  label: string;
  value: string;
  tone: "neutral" | "info" | "ok" | "warn" | "danger";
}

/** An action the user can take on a card. */
export interface ArtifactAction {
  id: string;
  label: string;
  /** "reply" opens a draft composer; "ask" asks the assistant to act. */
  kind: "reply" | "ask";
  recipient?: string;
  subject?: string;
  prompt?: string;
}

/** A structured app record (HubSpot engagement, contact, deal, ...). */
export interface Artifact {
  id: string;
  app: string;
  kind: string;
  /** Display type, e.g. "Email", "Call", "Contact". */
  type: string;
  title: string;
  subtitle: string;
  body: string;
  fields: ArtifactField[];
  badges: ArtifactBadge[];
  meta: string;
  /** Section heading, e.g. "Needs your reply". */
  group: string;
  /** The id the app knows this record by. */
  record_id: string;
  actions: ArtifactAction[];
}

export interface ToolkitTool {
  slug: string;
  name: string;
  description: string;
}

export interface ToolkitTools {
  provider: string;
  connected: boolean;
  state: string;
  tool_count: number;
  tools: ToolkitTool[];
}

export interface AppInsights {
  app: string;
  connected: boolean;
  state: string;
  available: boolean;
  headline: string;
  stats: { label: string; value: string }[];
  items: { title: string; detail: string; meta: string }[];
}

export interface AssistantJobAccepted {
  job_id: string;
  thread_id: string;
  conversation_id: string;
  status: string;
}

/** The record the user is acting on. */
export interface AssistantFocus {
  id: string;
  app: string;
  kind: string;
  type: string;
  title: string;
  record_id: string;
  fields: { label: string; value: string }[];
  meta: string;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

interface ReqOpts {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
}

async function req<T>(path: string, opts: ReqOpts = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (sessionToken) headers["Authorization"] = `Bearer ${sessionToken}`;

  const res = await fetch(`${API_URL}${path}`, {
    method: opts.method ?? "GET",
    headers,
    credentials: "include",
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") detail = data.detail;
      else if (Array.isArray(data?.detail) && data.detail[0]?.msg)
        detail = String(data.detail[0].msg);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// --- auth ---
export const authApi = {
  signup: (email: string, password: string, display_name = "") =>
    req<AuthResponse>("/api/v1/auth/signup", {
      method: "POST",
      body: { email, password, display_name },
    }),
  login: (email: string, password: string) =>
    req<AuthResponse>("/api/v1/auth/login", { method: "POST", body: { email, password } }),
  logout: () => req<void>("/api/v1/auth/logout", { method: "POST" }),
  me: (signal?: AbortSignal) => req<ApiUser>("/api/v1/auth/me", { signal }),
  permissions: (signal?: AbortSignal) =>
    req<Record<string, boolean>>("/api/v1/auth/permissions", { signal }),
  revokeAllSessions: () => req<void>("/api/v1/auth/sessions/revoke-all", { method: "POST" }),
  setPermission: (key: string, granted: boolean) =>
    req<Record<string, boolean>>(`/api/v1/auth/permissions/${encodeURIComponent(key)}`, {
      method: "PUT",
      body: { granted },
    }),
};

// --- email ---
export const emailApi = {
  sync: (limit = 20) =>
    req<{ fetched: number; triaged: number; message_ids: string[] }>(
      `/api/v1/email/sync?limit=${limit}`,
      { method: "POST" }
    ),
  messages: (opts: { priority?: string; needsReply?: boolean; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (opts.priority) q.set("priority", opts.priority);
    if (opts.needsReply !== undefined) q.set("needs_reply", String(opts.needsReply));
    q.set("limit", String(opts.limit ?? 50));
    return req<EmailMessage[]>(`/api/v1/email/messages?${q.toString()}`);
  },
  message: (id: string) => req<EmailMessage>(`/api/v1/email/messages/${encodeURIComponent(id)}`),
  followups: () => req<EmailMessage[]>("/api/v1/email/followups"),
  // drafts
  generateDraft: (message_id: string, instructions = "") =>
    req<Draft>("/api/v1/email/drafts/generate", {
      method: "POST",
      body: { message_id, instructions },
    }),
  /** Draft a reply body from any context (e.g. a HubSpot engagement). */
  composeDraft: (body: { context: string; sender?: string; subject?: string; instructions?: string }) =>
    req<{ body: string }>("/api/v1/email/drafts/compose", {
      method: "POST",
      body,
    }),
  createDraft: (body: {
    to_addr: string;
    subject?: string;
    body: string;
    message_id?: string | null;
  }) => req<Draft>("/api/v1/email/drafts", { method: "POST", body }),
  drafts: (status?: string) =>
    req<Draft[]>(`/api/v1/email/drafts${status ? `?status=${status}` : ""}`),
  draft: (id: string) => req<Draft>(`/api/v1/email/drafts/${encodeURIComponent(id)}`),
  updateDraft: (
    id: string,
    body: { subject?: string; body?: string; to_addr?: string }
  ) =>
    req<Draft>(`/api/v1/email/drafts/${encodeURIComponent(id)}`, { method: "PATCH", body }),
  deleteDraft: (id: string) =>
    req<void>(`/api/v1/email/drafts/${encodeURIComponent(id)}`, { method: "DELETE" }),
  requestSend: (id: string) =>
    req<{ approval_id: string; status: string; draft_id: string }>(
      `/api/v1/email/drafts/${encodeURIComponent(id)}/send-request`,
      { method: "POST" }
    ),
};

// --- approvals / jobs / integrations / observability / evals ---
export const approvalApi = {
  list: (status = "pending") => req<Approval[]>(`/api/v1/approvals?status=${status}`),
  approve: (id: string) =>
    req<Approval>(`/api/v1/approvals/${encodeURIComponent(id)}/approve`, { method: "POST" }),
  reject: (id: string) =>
    req<Approval>(`/api/v1/approvals/${encodeURIComponent(id)}/reject`, { method: "POST" }),
};

export const assistantApi = {
  /**
   * Submit a prompt. Returns 202 immediately; poll jobApi.get for the result.
   * `conversationId` groups the turns of one exchange, so a short answer to
   * a clarifying question is understood in context. `focus` is the record
   * the user clicked, so the assistant acts on that record.
   */
  submit: (request: string, conversationId = "", focus?: AssistantFocus | null) =>
    req<AssistantJobAccepted>("/api/v1/assistant/tasks", {
      method: "POST",
      body: {
        request,
        ...(conversationId ? { conversation_id: conversationId } : {}),
        ...(focus ? { focus } : {}),
      },
    }),
  briefing: (scheduled_for = "") =>
    req<AssistantJobAccepted>("/api/v1/assistant/briefing", {
      method: "POST",
      body: { scheduled_for },
    }),
};

/** A page of cards, with enough context to fetch the next one. */
export interface ArtifactPage {
  items: Artifact[];
  total: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

export const jobApi = {
  submit: (request: string, conversationId = "", focus?: AssistantFocus | null) =>
    req<AssistantJobAccepted>("/api/v1/assistant/tasks", {
      method: "POST",
      body: {
        request,
        ...(conversationId ? { conversation_id: conversationId } : {}),
        ...(focus ? { focus } : {}),
      },
    }),
  list: (limit = 20) => req<Job[]>(`/api/v1/jobs?limit=${limit}`),
  get: (id: string) => req<Job>(`/api/v1/jobs/${encodeURIComponent(id)}`),
  /**
   * Structured app records a run surfaced, rendered as cards in the chat.
   *
   * The endpoint returns a page envelope when `limit` is given and a bare
   * list otherwise, so this tolerates both — the API and the web are
   * deployed separately, and neither order should break the cards.
   */
  artifacts: async (id: string, offset = 0, limit = 12): Promise<ArtifactPage> => {
    const res = await req<ArtifactPage | Artifact[]>(
      `/api/v1/jobs/${encodeURIComponent(id)}/artifacts?offset=${offset}&limit=${limit}`
    );
    if (Array.isArray(res)) {
      return {
        items: res,
        total: res.length,
        offset,
        limit: res.length,
        has_more: false,
      };
    }
    return res;
  },
};

export const appApi = {
  /** Public: browsable catalog of connectable apps. */
  catalog: () => req<App[]>("/api/v1/apps"),
  /** Public: whether the catalog is reachable, and why not if it isn't. */
  catalogStatus: () =>
    req<{ configured: boolean; reachable: boolean; app_count: number; reason: string }>(
      "/api/v1/apps/status"
    ),
  /** Auth: apps this user has connected. */
  connections: () => req<Connection[]>("/api/v1/integrations"),
  /** Begin Composio OAuth; returns the URL the user must authorise. */
  connect: (slug: string, orgId = "") =>
    req<{ provider: string; redirect_url: string }>(
      `/api/v1/integrations/${encodeURIComponent(slug)}/connect${
        orgId ? `?org_id=${encodeURIComponent(orgId)}` : ""
      }`,
      { method: "POST" }
    ),
  insights: (slug: string) =>
    req<AppInsights>(`/api/v1/integrations/${encodeURIComponent(slug)}/insights`),
  tools: (slug: string, limit = 200) =>
    req<ToolkitTools>(`/api/v1/integrations/${encodeURIComponent(slug)}/tools?limit=${limit}`),
  status: (slug: string, orgId = "") =>
    req<{ provider: string; state: string }>(
      `/api/v1/integrations/${encodeURIComponent(slug)}/status${
        orgId ? `?org_id=${encodeURIComponent(orgId)}` : ""
      }`
    ),
  disconnect: (slug: string, orgId = "") =>
    req<{ provider: string; revoked: boolean }>(
      `/api/v1/integrations/${encodeURIComponent(slug)}${
        orgId ? `?org_id=${encodeURIComponent(orgId)}` : ""
      }`,
      { method: "DELETE" }
    ),
};

export const obsApi = {
  job: (jobId: string) => req<ObservabilitySummary>(`/api/v1/observability/jobs/${encodeURIComponent(jobId)}`),
  toolCalls: (limit = 50) => req<ToolCall[]>(`/api/v1/observability/tool-calls?limit=${limit}`),
  costs: () =>
    req<{ runs: number; prompt_tokens: number; completion_tokens: number; total_tokens: number; total_cost_usd: number }>(
      "/api/v1/observability/costs"
    ),
};

export const evalApi = {
  suites: () => req<string[]>("/api/v1/evals/suites"),
  run: (suite: string, useModel = false) =>
    req<EvalSuiteResult>(`/api/v1/evals/${suite}?use_model=${useModel}`),
  runAll: (useModel = false) =>
    req<EvalAllResult>(`/api/v1/evals/run?use_model=${useModel}`, { method: "POST" }),
};

export interface Schedule {
  briefing_enabled: boolean;
  briefing_hour: number;
  briefing_minute: number;
  followup_enabled: boolean;
  followup_hour: number;
  followup_minute: number;
  timezone: string;
}

export const settingsApi = {
  schedule: () => req<Schedule>("/api/v1/settings/schedule"),
  setSchedule: (patch: Partial<Schedule>) =>
    req<Schedule>("/api/v1/settings/schedule", { method: "PUT", body: patch }),
};

export const healthApi = {
  health: () => req<{ status: string }>("/health"),
  ready: () =>
    req<{ status: string; checks: Record<string, string> }>("/ready"),
};

export function isTerminal(status: string): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}