"""Read-only retrieval from human-reviewed, provenance-linked memory."""

from fastapi import APIRouter, HTTPException, Query

from converter_copilot.api.models import MemoryMatchesResponse
from converter_copilot.api.runner import TaskRunner
from converter_copilot.domain.memory import search_memory


def build_memory_router(runner: TaskRunner) -> APIRouter:
    """Build bounded memory search routes for the local SQLite store."""
    router = APIRouter(prefix="/api/memory", tags=["reviewed-memory"])

    @router.get("", response_model=MemoryMatchesResponse)
    def query_memory(
        q: str | None = Query(default=None, max_length=120),
        equipment_type: str | None = Query(default=None, max_length=120),
        limit: int = Query(default=10, ge=1, le=50),
    ):
        try:
            return MemoryMatchesResponse(
                items=search_memory(runner.store, query=q, equipment_type=equipment_type, limit=limit),
                synthetic_demo=False,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return router
