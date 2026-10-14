"""Seeded demo data (local only, no third-party integrations).

Creates a demo user with preferences, tasks, and notes for the daily
briefing workflow. Idempotent: skips content that already exists.

Usage:
    python -m pa.seed
    python -m pa.seed --user demo-user
"""

from __future__ import annotations

import argparse
import logging

from pa.db import repositories
from pa.db.database import get_session_factory, init_db
from pa.observability import log_event

logger = logging.getLogger("pa.seed")

DEMO_TASKS = [
    "Review MVP test report and fix failing tests",
    "Prepare concise morning briefing",
    "Approve pending deployment action",
]

DEMO_NOTES = [
    "Standup at 09:00 Africa/Nairobi",
    "Focus block 10:00-12:00, no meetings",
]

DEMO_PREFS = [
    "prefers concise briefings with top 3 tasks first",
    "timezone Africa/Nairobi",
]


def seed_user(user_id: str = "demo-user") -> dict:
    init_db()
    db = get_session_factory()()
    try:
        existing = {m.content for m in repositories.list_memories(db, user_id=user_id)}
        created: list[str] = []

        def _add(content: str, category: str) -> None:
            if content in existing:
                return
            try:
                repositories.create_memory(db, user_id=user_id, content=content, category=category)
                repositories.create_audit_event(
                    db,
                    event_type="memory.created",
                    user_id=user_id,
                    details={"category": category},
                )
                created.append(f"{category}:{content[:40]}")
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "seed skipped",
                    user_id=user_id,
                    extra={"error": str(exc)[:150]},
                )

        for t in DEMO_TASKS:
            _add(t, "task")
        for n in DEMO_NOTES:
            _add(n, "note")
        for p in DEMO_PREFS:
            _add(p, "preference")
        log_event(
            logger, logging.INFO, "seed complete", user_id=user_id, extra={"created": len(created)}
        )
        return {"user_id": user_id, "created": created}
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Seed PA demo data")
    p.add_argument("--user", default="demo-user")
    args = p.parse_args(argv)
    out = seed_user(user_id=args.user)
    print(f"seeded {args.user}: {len(out['created'])} new memories")
    for c in out["created"]:
        print(f"  + {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
