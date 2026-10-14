"""Route package aggregator."""

from pa.api.routes.approvals import router as approvals_router
from pa.api.routes.assistant import router as assistant_router
from pa.api.routes.integrations import router as integrations_router
from pa.api.routes.jobs import router as jobs_router
from pa.api.routes.memory import router as memory_router

__all__ = [
    "approvals_router",
    "assistant_router",
    "integrations_router",
    "jobs_router",
    "memory_router",
]
