"""Eval API routes (requirement §8)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from pa.api.dependencies import get_current_user, get_db
from pa.schemas.auth import EvalSuiteResult
from pa.services import evals as harness

router = APIRouter(prefix="/evals", tags=["evals"])


def _as_suite(data: dict) -> EvalSuiteResult:
    return EvalSuiteResult(
        suite=data["suite"],
        total=data["total"],
        passed=data["passed"],
        failed=data["failed"],
        score=data["score"],
        results=[
            {"name": r["name"], "passed": r["passed"], "detail": r.get("detail", "")}
            for r in data["results"]
        ],
    )


@router.get(
    "/suites",
    response_model=list[str],
    summary="List eval suites",
)
def list_suites():
    """Available eval suites: classification, summary, draft, authorization."""
    return ["classification", "summary", "draft", "authorization"]


@router.get(
    "/{suite}",
    response_model=EvalSuiteResult,
    summary="Run one eval suite",
    description="Deterministic suites run without a model. Authorization is never model-backed.",
)
def run_suite(
    suite: str,
    use_model: bool = Query(default=False),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Execute a single suite and return per-case results."""
    invoke = None
    if use_model:
        try:
            from pa.services.email_service import _model_invoker

            invoke = _model_invoker()
        except Exception:
            invoke = None
        if invoke is None:
            raise HTTPException(status_code=503, detail="no model configured")

    if suite == "classification":
        return _as_suite(harness.run_classification(use_model=use_model, invoke=invoke).to_dict())
    if suite == "summary":
        return _as_suite(harness.run_summary(use_model=use_model, invoke=invoke).to_dict())
    if suite == "draft":
        return _as_suite(harness.run_draft(use_model=use_model, invoke=invoke).to_dict())
    if suite == "authorization":
        other = f"{user_id}-other-eval"
        return _as_suite(harness.run_authorization(db, user_a=user_id, user_b=other).to_dict())
    raise HTTPException(status_code=404, detail=f"unknown suite: {suite}")


@router.post(
    "/run",
    summary="Run all eval suites",
    description="Runs every suite and returns an aggregate score. Useful as a CI gate.",
)
def run_everything(
    use_model: bool = Query(default=False),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    invoke = None
    if use_model:
        try:
            from pa.services.email_service import _model_invoker

            invoke = _model_invoker()
        except Exception:
            invoke = None
    other = f"{user_id}-other-eval"
    return harness.run_all(db, user_a=user_id, user_b=other, use_model=use_model, invoke=invoke)
