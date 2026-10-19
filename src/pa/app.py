"""FastAPI application — routers, lifespan, health/readiness."""

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from pa.api.routes.approvals import router as approvals_router
from pa.api.routes.assistant import router as assistant_router
from pa.api.routes.auth import router as auth_router
from pa.api.routes.email import router as email_router
from pa.api.routes.evals import router as evals_router
from pa.api.routes.integrations import router as integrations_router
from pa.api.routes.jobs import router as jobs_router
from pa.api.routes.memory import router as memory_router
from pa.api.routes.observability import router as observability_router
from pa.api.routes.settings import router as settings_router
from pa.db.database import get_session_factory, init_db
from pa.observability import set_correlation_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        from pa.graph.checkpointer import ensure_schema

        ensure_schema()
    except Exception:
        pass
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="PA",
        version="0.1.0",
        description=(
            "Personal AI assistant: submit tasks that run as background "
            "LangGraph workflows, approve sensitive actions, and manage "
            "user-scoped memory.\n\n"
            "Auth: sign up or log in via `/api/v1/auth/*`. Authenticated calls "
            "use the `pa_session` cookie or an `Authorization: Bearer` token. "
            "Every response echoes an `X-Correlation-Id` header for log "
            "correlation.\n\n"
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
                "description": "Browse and manage the user's Composio app connections.",
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

    from pa.config import get_settings

    from pa.integrations.gmail_composio import IntegrationNotReady

    @app.exception_handler(IntegrationNotReady)
    async def _integration_not_ready(_request: Request, exc: IntegrationNotReady):
        """Tell the caller to connect the app instead of failing opaquely."""
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    origins = [o.strip() for o in get_settings().APP_CORS_ORIGINS.split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "Accept", "X-Correlation-Id"],
            allow_credentials=True,
            expose_headers=["X-Correlation-Id"],
            max_age=600,
        )

    # Auth first: everything else depends on get_current_user.
    app.include_router(auth_router, prefix="/api/v1", tags=["auth"])
    app.include_router(email_router, prefix="/api/v1")
    app.include_router(observability_router, prefix="/api/v1")
    app.include_router(evals_router, prefix="/api/v1")
    app.include_router(assistant_router, prefix="/api/v1", tags=["assistant"])
    app.include_router(jobs_router, prefix="/api/v1", tags=["jobs"])
    app.include_router(approvals_router, prefix="/api/v1", tags=["approvals"])
    app.include_router(memory_router, prefix="/api/v1", tags=["memory"])
    app.include_router(integrations_router, prefix="/api/v1", tags=["integrations"])
    app.include_router(settings_router, prefix="/api/v1", tags=["settings"])

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

    @app.get("/api/v1/diagnostics", tags=["ops"], summary="What this deployment has")
    def diagnostics():
        """Report the features this running build actually provides.

        A deployment can be running an image that predates a feature, or a
        database that has not been migrated. This makes that visible in one
        call instead of showing up as a missing behaviour.
        """
        from sqlalchemy import inspect, text

        report: dict[str, object] = {}

        # Database: which revision, and does it have the columns the code needs?
        try:
            from pa.db.database import get_engine

            engine = get_engine()
            with engine.connect() as conn:
                version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            report["alembic_version"] = version

            inspector = inspect(engine)
            jobs_columns = (
                {c["name"] for c in inspector.get_columns("jobs")}
                if inspector.has_table("jobs")
                else set()
            )
            report["jobs_columns"] = sorted(jobs_columns)
            missing = [
                c for c in ("artifacts", "conversation_id", "focus") if c not in jobs_columns
            ]
            report["schema_behind_code"] = missing
        except Exception as exc:
            report["database_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"

        # Skills: the domain knowledge the assistant loads.
        try:
            from pa.services.skills import load_skills, skills_dir

            loaded = load_skills()
            report["skills"] = [s.name for s in loaded]
            report["skills_dir"] = str(skills_dir()) if skills_dir() else None
        except Exception as exc:
            report["skills_error"] = f"{type(exc).__name__}"

        # Features this build ships.
        report["features"] = {
            "cards": True,
            "focus": True,
            "skills": True,
            "app_guard": True,
        }
        return report

    import os

    if os.getenv("PA_ENABLE_UI", "0") == "1":
        try:
            from pa.frontend import mount_frontend

            app = mount_frontend(app)
        except Exception:
            pass

    return app


app = create_app()
