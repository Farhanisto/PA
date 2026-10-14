"""FastAPI application — routers, lifespan, health/readiness."""

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from pa.api.routes.approvals import router as approvals_router
from pa.api.routes.assistant import router as assistant_router
from pa.api.routes.integrations import router as integrations_router
from pa.api.routes.jobs import router as jobs_router
from pa.api.routes.memory import router as memory_router
from pa.db.database import get_session_factory, init_db
from pa.observability import set_correlation_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="PA",
        version="0.1.0",
        description=(
            "Personal AI assistant: submit tasks that run as background "
            "LangGraph workflows, approve sensitive actions, and manage "
            "user-scoped memory.\n\n"
            "Auth (MVP): pass your user id as the `X-User-Id` header "
            "(defaults to `demo-user`). Every response echoes an "
            "`X-Correlation-Id` header for log correlation.\n\n"
            "Interactive docs: Swagger UI at `/docs`, ReDoc at `/redoc`, "
            "raw OpenAPI JSON at `/openapi.json`."
        ),
        openapi_tags=[
            {
                "name": "assistant",
                "description": "Submit assistant tasks for background execution.",
            },
            {"name": "jobs", "description": "Poll durable job status and results."},
            {
                "name": "approvals",
                "description": "Approve or reject paused, approval-gated actions.",
            },
            {"name": "memory", "description": "User-scoped persistent memory CRUD."},
            {
                "name": "integrations",
                "description": "Connected apps (Gmail, WhatsApp, Zoom): status and read-only views.",
            },
            {"name": "ops", "description": "Liveness and readiness probes."},
        ],
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        cid = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        set_correlation_id(cid)
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = cid
        return response

    app.include_router(assistant_router, prefix="/api/v1", tags=["assistant"])
    app.include_router(jobs_router, prefix="/api/v1", tags=["jobs"])
    app.include_router(approvals_router, prefix="/api/v1", tags=["approvals"])
    app.include_router(memory_router, prefix="/api/v1", tags=["memory"])
    app.include_router(integrations_router, prefix="/api/v1", tags=["integrations"])

    @app.get("/health", tags=["ops"], summary="Liveness probe")
    def health():
        """Liveness: process is running (no dependency checks)."""
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"], summary="Readiness probe")
    def ready():
        """Readiness: app + DB (+ Redis best-effort) can serve traffic."""
        checks: dict[str, str] = {}
        # DB check (authoritative store).
        try:
            factory = get_session_factory()
            db = factory()
            try:
                from sqlalchemy import text

                db.execute(text("SELECT 1"))
                checks["database"] = "ok"
            finally:
                db.close()
        except Exception as exc:
            checks["database"] = f"error: {type(exc).__name__}"
            return {"status": "not_ready", "checks": checks}
        # Redis check (queue broker, best-effort — degraded, not down).
        try:
            import os

            import redis

            url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            client = redis.from_url(url, socket_connect_timeout=2)
            client.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"degraded: {type(exc).__name__}"
        status = "ready" if checks.get("database") == "ok" else "not_ready"
        return {"status": status, "checks": checks}

    import os

    if os.getenv("PA_ENABLE_UI", "0") == "1":
        try:
            from pa.frontend import mount_frontend

            app = mount_frontend(app)
        except Exception:
            pass

    return app


app = create_app()
