"""Auth, email, draft, trace, and eval request/response schemas."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# --- Auth ---


class SignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=512)
    display_name: str = Field(default="", max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=512)


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    permissions: dict[str, bool] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    """Token is returned once at signup/login and set as an httpOnly cookie."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserResponse


class PermissionUpdate(BaseModel):
    granted: bool


# --- Integrations ---


class AppResponse(BaseModel):
    """A connectable app in the Composio catalog."""

    slug: str
    name: str
    description: str = ""
    logo: str = ""
    state: str = "none"  # active | pending | none | unavailable


class ConnectionResponse(BaseModel):
    provider: str
    name: str
    connected: bool
    state: str


# --- Email ---


class EmailMessageResponse(BaseModel):
    id: str
    provider_message_id: str
    from_addr: str
    subject: str
    snippet: str
    received_at: Optional[datetime] = None
    summary: str = ""
    priority: str = "normal"
    needs_reply: bool = False
    action_items: list[str] = Field(default_factory=list)
    deadlines: list[str] = Field(default_factory=list)
    triaged: bool = False


class TriageRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


class TriageResponse(BaseModel):
    fetched: int
    triaged: int
    message_ids: list[str] = Field(default_factory=list)


# --- Drafts ---


class DraftCreateRequest(BaseModel):
    message_id: Optional[str] = None
    to_addr: str = Field(min_length=3, max_length=320)
    subject: str = Field(default="", max_length=998)
    body: str = Field(min_length=1, max_length=100000)
    in_reply_to: str = Field(default="", max_length=255)


class DraftUpdateRequest(BaseModel):
    subject: Optional[str] = Field(default=None, max_length=998)
    body: Optional[str] = Field(default=None, max_length=100000)
    to_addr: Optional[str] = Field(default=None, max_length=320)


class DraftResponse(BaseModel):
    id: str
    message_id: Optional[str] = None
    in_reply_to: str = ""
    to_addr: str
    subject: str
    body: str
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    sent_message_id: Optional[str] = None


class DraftGenerateRequest(BaseModel):
    """Ask the model to draft a reply to an existing message."""

    message_id: str
    instructions: str = Field(default="", max_length=4000)


class DraftComposeRequest(BaseModel):
    """Draft a reply body from free-form context.

    Used by the assistant chat, where the thing being replied to may be a
    record in a connected app (a HubSpot engagement) rather than a message
    PA has cached.
    """

    context: str = Field(min_length=1, max_length=8000)
    sender: str = Field(default="", max_length=320)
    subject: str = Field(default="", max_length=998)
    instructions: str = Field(default="", max_length=4000)


class DraftComposeResponse(BaseModel):
    body: str


# --- Observability ---


class RunTraceResponse(BaseModel):
    id: str
    job_id: str
    thread_id: str = ""
    node: str
    status: str
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    error: str = ""
    created_at: Optional[datetime] = None


class ToolCallResponse(BaseModel):
    id: str
    job_id: str
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: str
    latency_ms: int
    output_summary: str = ""
    error: str = ""
    created_at: Optional[datetime] = None


class ObservabilitySummary(BaseModel):
    job_id: str
    traces: list[RunTraceResponse] = Field(default_factory=list)
    tool_calls: list[ToolCallResponse] = Field(default_factory=list)
    total_latency_ms: int = 0
    total_cost_usd: float = 0.0
    failures: int = 0


# --- Evals ---


class EvalCaseResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class EvalSuiteResult(BaseModel):
    suite: str
    total: int
    passed: int
    failed: int
    score: float
    results: list[EvalCaseResult] = Field(default_factory=list)


class ToolInfo(BaseModel):
    """One Composio tool available to the agent for a connected app."""

    slug: str
    name: str
    description: str = ""


class ToolkitToolsResponse(BaseModel):
    provider: str
    connected: bool
    state: str
    tool_count: int
    tools: list[ToolInfo]


class ScheduleUpdate(BaseModel):
    briefing_enabled: bool | None = None
    briefing_hour: int | None = Field(default=None, ge=0, le=23)
    briefing_minute: int | None = Field(default=None, ge=0, le=59)
    followup_enabled: bool | None = None
    followup_hour: int | None = Field(default=None, ge=0, le=23)
    followup_minute: int | None = Field(default=None, ge=0, le=59)
    timezone: str | None = Field(default=None, max_length=64)


class ScheduleResponse(BaseModel):
    briefing_enabled: bool
    briefing_hour: int
    briefing_minute: int
    followup_enabled: bool
    followup_hour: int
    followup_minute: int
    timezone: str

    @classmethod
    def from_row(cls, row) -> "ScheduleResponse":
        return cls(
            briefing_enabled=row.briefing_enabled,
            briefing_hour=row.briefing_hour,
            briefing_minute=row.briefing_minute,
            followup_enabled=row.followup_enabled,
            followup_hour=row.followup_hour,
            followup_minute=row.followup_minute,
            timezone=row.timezone,
        )
