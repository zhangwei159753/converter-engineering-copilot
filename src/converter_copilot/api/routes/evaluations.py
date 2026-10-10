"""Local evaluation review bundle and human label endpoints."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from converter_copilot.api.models import EvaluationReviewRequest
from converter_copilot.api.runner import TaskRunner
from converter_copilot.evaluation.review import (
    EvaluationReviewBundle,
    EvaluationReviewService,
    EvidenceLabel,
)


def build_evaluation_router(runner: TaskRunner) -> APIRouter:
    """Build review endpoints backed by the task runner's SQLite store."""
    router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])
    service = EvaluationReviewService(runner.store)

    @router.get("")
    def summary():
        return service.summary()

    @router.post("/review-bundles")
    def import_bundle(payload: dict[str, object]):
        try:
            bundle = EvaluationReviewBundle.model_validate(payload)
            return service.import_review_bundle(bundle)
        except ValidationError as error:
            raise HTTPException(status_code=422, detail="Review bundle failed validation.") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/examples")
    def list_examples(
        mode: str | None = None,
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        try:
            examples, total = service.list_examples(mode=mode, status=status, limit=limit, offset=offset)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"items": examples, "total": total, "limit": limit, "offset": offset}

    @router.put("/examples/{review_id}/review")
    def save_review(review_id: str, payload: dict[str, object]):
        try:
            request = EvaluationReviewRequest.model_validate(payload)
            labels = [EvidenceLabel.model_validate(label) for label in request.evidence_labels]
            return service.save_review(
                review_id,
                reviewer_id=request.reviewer_id,
                evidence_labels=labels,
                expected_retrieval_ids=request.expected_retrieval_ids,
                note=request.note,
            )
        except ValidationError as error:
            raise HTTPException(status_code=422, detail="Review payload failed validation.") from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Evaluation example not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/examples/{review_id}/history")
    def history(review_id: str):
        try:
            return service.history(review_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Evaluation example not found.") from error

    return router
