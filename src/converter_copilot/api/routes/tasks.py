"""Task lifecycle, reports, retry, and reconnectable SSE endpoints."""

import asyncio
import json

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from converter_copilot.api.models import TaskAccepted
from converter_copilot.api.runner import RetryLimitError, TaskRunner, WorkflowUnavailableError
from converter_copilot.domain.schemas import TaskCreate


def build_task_router(runner: TaskRunner) -> APIRouter:
    """Build task routes bound to the application runner."""
    router = APIRouter(prefix="/api/tasks", tags=["tasks"])

    @router.post("", response_model=TaskAccepted, status_code=202)
    async def create_task(request: TaskCreate):
        try:
            task = await runner.create(request)
        except WorkflowUnavailableError as error:
            raise HTTPException(status_code=501, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return TaskAccepted(task=task)

    @router.get("")
    def list_tasks(limit: int = Query(default=50, ge=1, le=200)):
        return runner.store.list_tasks(limit=limit)

    @router.get("/{task_id}")
    def get_task(task_id: str):
        task = runner.store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        return task

    @router.post("/{task_id}/retry", response_model=TaskAccepted, status_code=202)
    async def retry_task(task_id: str):
        try:
            task = await runner.retry(task_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Task not found.") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except RetryLimitError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except WorkflowUnavailableError as error:
            raise HTTPException(status_code=501, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return TaskAccepted(task=task)

    @router.get("/{task_id}/report")
    def get_report(task_id: str):
        task = runner.store.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        report = runner.store.get_report(task_id)
        if report is None:
            raise HTTPException(status_code=409, detail="Report is not ready.")
        return report

    @router.get("/{task_id}/events")
    async def stream_events(
        task_id: str,
        after_id: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        if runner.store.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        cursor = max(after_id, int(last_event_id)) if last_event_id and last_event_id.isdigit() else after_id

        async def events():
            nonlocal cursor
            while True:
                batch = runner.store.events_after(task_id, after_id=cursor)
                for event in batch:
                    cursor = event.event_id
                    payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                    yield f"id: {event.event_id}\nevent: progress\ndata: {payload}\n\n"
                task = runner.store.get_task(task_id)
                if task is None or task.status in {"completed", "partial", "failed"}:
                    break
                await asyncio.sleep(0.25)

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    return router
