"""Pydantic request/response schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AssistantFocus(BaseModel):
    """The record the user is working on.

    Clicking a card focuses it: the next message is about that record, and
    the assistant uses its id in tool arguments instead of guessing which
    record was meant.
    """

    id: str = ""
    app: str = ""
    kind: str = ""
    type: str = ""
    title: str = ""
    record_id: str = ""
    fields: list[dict[str, str]] = Field(default_factory=list)
    meta: str = ""


class AssistantTaskRequest(BaseModel):
    request: str = Field(min_length=1, max_length=10000)
    # Groups turns into one conversation so a follow-up ("tickets") is
    # understood in the context of the question it answers.
    conversation_id: str | None = Field(default=None, max_length=64)
    # The record the user is acting on, if any.
    focus: AssistantFocus | None = None


class AssistantTaskResponse(BaseModel):
    job_id: str
    thread_id: str
    conversation_id: str
    status: str


class BriefingRequest(BaseModel):
    scheduled_for: str = Field(default="", max_length=128)


class JobResponse(BaseModel):
    job_id: str
    thread_id: str
    status: str
    request: str
    result: str | None = None
    error_code: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ArtifactField(BaseModel):
    label: str = ""
    value: str = ""


class ArtifactBadge(BaseModel):
    """A small coloured tag on a card, e.g. Priority: HIGH."""

    label: str = ""
    value: str = ""
    tone: str = "neutral"


class ArtifactAction(BaseModel):
    """A thing the user can do with a card.

    ``kind`` is ``reply`` (open a draft composer) or ``ask`` (ask the
    assistant to act, which goes through the approval gate for writes).
    """

    id: str
    label: str
    kind: str
    recipient: str = ""
    subject: str = ""
    prompt: str = ""


class ArtifactResponse(BaseModel):
    """One structured app record, rendered as a card in the chat."""

    id: str
    app: str = ""
    kind: str = ""
    type: str = ""
    title: str = ""
    subtitle: str = ""
    body: str = ""
    fields: list[ArtifactField] = Field(default_factory=list)
    badges: list[ArtifactBadge] = Field(default_factory=list)
    meta: str = ""
    actions: list[ArtifactAction] = Field(default_factory=list)


class ApprovalResponse(BaseModel):
    approval_id: str
    job_id: str
    thread_id: str
    action: str
    payload: dict[str, Any]
    status: str


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    category: str = Field(default="general", max_length=64)


class MemoryResponse(BaseModel):
    id: str
    content: str
    category: str


class GmailMessage(BaseModel):
    sender: str = Field(alias="from")
    subject: str
    snippet: str
    date: str

    model_config = {"populate_by_name": True}


class ZoomMeeting(BaseModel):
    id: str
    topic: str
    start_time: str
    join_url: str
