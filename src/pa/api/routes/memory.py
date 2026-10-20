"""User-scoped memory CRUD."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from pa.api.dependencies import get_current_user, get_db
from pa.memory import service as memory_service
from pa.schemas.assistant import MemoryCreate, MemoryResponse

router = APIRouter()


@router.get(
    "/memory",
    response_model=list[MemoryResponse],
    summary="List my memories",
    description="Lists the current user's stored preferences/facts, optionally filtered by category.",
)
def list_memory(
    category: str | None = Query(default=None),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """List stored memories scoped to the caller."""
    mems = memory_service.list_memories(db, user_id=user_id, category=category)
    return [MemoryResponse(id=m.id, content=m.content, category=m.category) for m in mems]


@router.post(
    "/memory",
    status_code=status.HTTP_201_CREATED,
    response_model=MemoryResponse,
    summary="Save a memory",
    description="Saves a user-approved fact/preference. Credentials and secrets are refused.",
)
def create_memory(
    body: MemoryCreate, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)
):
    """Save a new user-approved memory."""
    try:
        mem = memory_service.save_memory(
            db, user_id=user_id, content=body.content, category=body.category
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return MemoryResponse(id=mem.id, content=mem.content, category=mem.category)


@router.delete(
    "/memory/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a memory",
    description="Deletes an owned memory record. Returns 404 if missing, 403 if owned by another user.",
)
def delete_memory(
    memory_id: str, db: Session = Depends(get_db), user_id: str = Depends(get_current_user)
):
    """Delete an owned memory."""
    try:
        memory_service.delete_memory(db, user_id=user_id, memory_id=memory_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="memory not found")
    except PermissionError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not authorized")
