"""Connector contract for external apps (Gmail, WhatsApp, Zoom).

Design rules (match the project's security posture):
- Credentials live in environment variables only — never in the database,
  never in logs, never in API responses or tool results.
- Every connector works in two modes: `live` when its credentials are
  configured, `mock` (seeded demo data, no side effects) otherwise.
- `status()` exposes provider/mode only — no secrets, no tokens.
"""

from typing import Any, Protocol


class Connector(Protocol):
    provider: str

    def is_configured(self) -> bool:
        """True when live credentials are present."""
        ...

    def mode(self) -> str:
        """'live' or 'mock'."""
        ...

    def status(self) -> dict[str, Any]:
        """Public status payload — must never contain secrets."""
        ...


def public_status(provider: str, configured: bool, description: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "configured": configured,
        "mode": "live" if configured else "mock",
        "description": description,
    }
