"""Read-only synthetic work-order API and local analysis review workflow."""

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import ValidationError

from converter_copilot.api.models import WorkOrderReviewRequest
from converter_copilot.api.runner import TaskRunner, WorkflowUnavailableError
from converter_copilot.domain.schemas import FaultAnalysisInput, TaskCreate, TaskMode
from converter_copilot.domain.work_orders import get_work_order, list_work_orders
from converter_copilot.storage.sqlite_store import IdempotencyConflictError


def build_work_order_router(runner: TaskRunner) -> APIRouter:
    """Build work-order routes bound to the existing durable task runner."""
    router = APIRouter(prefix="/api/work-orders", tags=["work-orders"])

    @router.get("")
    def list_orders(
        q: str | None = Query(default=None, max_length=120),
        status: str | None = Query(default=None, pattern="^(open|investigating|resolved)$"),
    ):
        items = list_work_orders(query=q, status=status)
        return {
            "items": [
                {
                    **item.model_dump(mode="json"),
                    "analysis_count": len(runner.store.list_work_order_task_ids(item.work_order_id)),
                }
                for item in items
            ],
            "total": len(items),
            "synthetic_demo": True,
        }

    @router.get("/{work_order_id}")
    def get_order(work_order_id: str):
        work_order = get_work_order(work_order_id)
        if work_order is None:
            raise HTTPException(status_code=404, detail="Work order not found.")
        analyses = []
        for task_id in runner.store.list_work_order_task_ids(work_order_id):
            task = runner.store.get_task(task_id)
            if task is None:
                continue
            report = runner.store.get_report(task_id)
            history = runner.store.list_work_order_reviews(work_order_id, task_id)
            analyses.append({
                "task": task,
                "report": report,
                "review_history": history,
                "latest_review": history[-1] if history else None,
            })
        return {"work_order": work_order, "analyses": analyses, "synthetic_demo": True}

    @router.post("/{work_order_id}/analyses", status_code=202)
    async def create_analysis(
        work_order_id: str,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        work_order = get_work_order(work_order_id)
        if work_order is None:
            raise HTTPException(status_code=404, detail="Work order not found.")
        conditions = {key: value for key, value in work_order.operating_conditions.items() if value is not None}
        request = TaskCreate(
            mode=TaskMode.FAULT_ANALYSIS,
            inputs=FaultAnalysisInput(
                equipment_type=work_order.equipment_model,
                symptom=work_order.symptom,
                operating_conditions=conditions,
                source_context={
                    "work_order_id": work_order.work_order_id,
                    "serial_number": work_order.serial_number,
                    "occurred_at": work_order.occurred_at,
                    "firmware_version": work_order.firmware_version,
                    "attachment_names": work_order.attachment_names,
                    "missing_fields": work_order.missing_fields,
                    "severity": work_order.severity,
                    "synthetic_demo": True,
                },
                fault_code=work_order.alarm_code,
            ),
        )
        try:
            task, reused = await runner.create(
                request, idempotency_key=idempotency_key, work_order_id=work_order_id
            )
        except IdempotencyConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except WorkflowUnavailableError as error:
            raise HTTPException(status_code=501, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return {"task": task, "reused": reused, "work_order_id": work_order_id}

    @router.put("/{work_order_id}/analyses/{task_id}/review")
    def review_analysis(work_order_id: str, task_id: str, payload: dict[str, object]):
        if get_work_order(work_order_id) is None:
            raise HTTPException(status_code=404, detail="Work order not found.")
        try:
            request = WorkOrderReviewRequest.model_validate(payload)
            return runner.store.save_work_order_review(
                work_order_id,
                task_id,
                reviewer=request.reviewer,
                verdict=request.verdict,
                note=request.note,
            )
        except ValidationError as error:
            raise HTTPException(status_code=422, detail="Review payload failed validation.") from error
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Analysis task is not linked to this work order.") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return router
