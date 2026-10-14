"""User-controlled persistent memory, separate from graph checkpoints."""

from sqlalchemy.orm import Session

from pa.db import repositories
from pa.db.models import Memory

FORBIDDEN_PATTERNS = ("api_key", "token", "password", "secret", "credential")


def _contains_secret(content: str) -> bool:
    lowered = content.lower()
    return any(p in lowered for p in FORBIDDEN_PATTERNS)


def save_memory(db: Session, *, user_id: str, content: str, category: str = "general") -> Memory:
    content = (content or "").strip()
    if not content:
        raise ValueError("memory content must not be empty")
    if len(content) > 5000:
        raise ValueError("memory content too long")
    if _contains_secret(content):
        raise ValueError("refusing to store credentials/secrets as memory")
    mem = repositories.create_memory(
        db, user_id=user_id, content=content, category=category or "general"
    )
    repositories.create_audit_event(
        db,
        event_type="memory.created",
        user_id=user_id,
        details={"memory_id": mem.id, "category": mem.category},
    )
    return mem


def list_memories(db: Session, *, user_id: str, category: str | None = None) -> list[Memory]:
    return repositories.list_memories(db, user_id=user_id, category=category)


def delete_memory(db: Session, *, user_id: str, memory_id: str) -> None:
    mem = repositories.get_memory(db, memory_id)
    if mem is None:
        raise KeyError("memory not found")
    if mem.user_id != user_id:
        raise PermissionError("not authorized for this memory")
    repositories.delete_memory(db, mem)
    repositories.create_audit_event(
        db, event_type="memory.deleted", user_id=user_id, details={"memory_id": memory_id}
    )


def context_snippets(db: Session, *, user_id: str, limit: int = 10) -> list[str]:
    mems = list_memories(db, user_id=user_id)[:limit]
    return [m.content for m in mems]
