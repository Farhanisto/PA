"""Zoom connector: list meetings, create meetings (approval-gated).

Live mode uses Zoom Server-to-Server OAuth (account credentials grant) via
httpx, with the access token cached in memory. Mock mode serves seeded
meetings and returns mock join URLs (no side effects).
"""

import time
from typing import Any

import httpx

from pa.config import get_settings
from pa.integrations.base import public_status

PROVIDER = "zoom"

MOCK_MEETINGS = [
    {
        "id": "mock-1",
        "topic": "Weekly standup",
        "start_time": "2026-10-15T09:00:00",
        "join_url": "https://zoom.us/j/mock-1",
    },
    {
        "id": "mock-2",
        "topic": "Design review",
        "start_time": "2026-10-16T14:00:00",
        "join_url": "https://zoom.us/j/mock-2",
    },
]

# In-memory record of mock-created meetings. Tests may clear it.
MOCK_CREATED: list[dict[str, Any]] = []

_token_cache: dict[str, Any] = {}


def _creds() -> tuple[str, str, str]:
    s = get_settings()
    return s.ZOOM_ACCOUNT_ID, s.ZOOM_CLIENT_ID, s.ZOOM_CLIENT_SECRET


class ZoomConnector:
    provider = PROVIDER

    def is_configured(self) -> bool:
        account_id, client_id, client_secret = _creds()
        return bool(account_id and client_id and client_secret)

    def mode(self) -> str:
        return "live" if self.is_configured() else "mock"

    def status(self) -> dict[str, Any]:
        return public_status(
            PROVIDER, self.is_configured(), "List meetings and schedule new ones (approval-gated)."
        )

    def list_meetings(self) -> list[dict[str, Any]]:
        if not self.is_configured():
            return [dict(m) for m in MOCK_MEETINGS]
        token = _access_token()
        resp = httpx.get(
            "https://api.zoom.us/v2/users/me/meetings",
            headers={"Authorization": f"Bearer {token}"},
            params={"type": "upcoming", "page_size": 25},
            timeout=30.0,
        )
        resp.raise_for_status()
        out = []
        for m in resp.json().get("meetings", []):
            out.append(
                {
                    "id": str(m.get("id", "")),
                    "topic": str(m.get("topic", ""))[:200],
                    "start_time": str(m.get("start_time", "")),
                    "join_url": str(m.get("join_url", "")),
                }
            )
        return out

    def create_meeting(
        self, *, topic: str, start_time: str = "", duration_minutes: int = 30
    ) -> dict[str, Any]:
        topic = (topic or "").strip()
        if not topic:
            raise ValueError("meeting topic required")
        if len(topic) > 200:
            raise ValueError("topic too long")
        duration_minutes = int(duration_minutes)
        if duration_minutes < 1 or duration_minutes > 480:
            raise ValueError("duration must be 1-480 minutes")
        if not self.is_configured():
            created = {
                "id": f"mock-{len(MOCK_CREATED) + 1}",
                "topic": topic,
                "start_time": start_time,
                "join_url": f"https://zoom.us/j/mock-{len(MOCK_CREATED) + 1}",
            }
            MOCK_CREATED.append(created)
            return dict(created, mode="mock")
        token = _access_token()
        payload: dict[str, Any] = {"topic": topic, "type": 2, "duration": duration_minutes}
        if start_time:
            payload["start_time"] = start_time
        resp = httpx.post(
            "https://api.zoom.us/v2/users/me/meetings",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "id": str(data.get("id", "")),
            "topic": topic,
            "start_time": str(data.get("start_time", start_time)),
            "join_url": str(data.get("join_url", "")),
            "mode": "live",
        }


def _access_token() -> str:
    if _token_cache.get("expires_at", 0) > time.time() + 30:
        return str(_token_cache["token"])
    account_id, client_id, client_secret = _creds()
    resp = httpx.post(
        "https://zoom.us/oauth/token",
        params={"grant_type": "account_credentials", "account_id": account_id},
        auth=(client_id, client_secret),
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + int(data.get("expires_in", 3600))
    return str(_token_cache["token"])
