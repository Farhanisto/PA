"""Pydantic request/response schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AssistantTaskRequest(BaseModel):
    request: str = Field(min_length=1, max_length=10000)


class AssistantTaskResponse(BaseModel):
    job_id: str
    thread_id: str
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
