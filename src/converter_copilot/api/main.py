"""FastAPI app factory for local Converter Engineering Copilot demos."""

import os
import sqlite3

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from converter_copilot.api.routes.cases import build_case_router
from converter_copilot.api.routes.tasks import build_task_router
from converter_copilot.api.runner import TaskRunner
from converter_copilot.storage.sqlite_store import SQLiteStore


def create_app(runner: TaskRunner | None = None) -> FastAPI:
    """Build an injectable FastAPI app; default provider construction is lazy."""
    if runner is None:
        runner = TaskRunner(SQLiteStore(os.getenv("CONVERTER_DB_PATH", ".data/converter_copilot.db")))
    app = FastAPI(title="Converter Engineering Copilot", version="0.1.0")
    origin = os.getenv("WEB_ORIGIN", "http://localhost:5173")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )
    app.include_router(build_task_router(runner))
    app.include_router(build_case_router(runner))

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "converter-engineering-copilot"}

    @app.get("/api/ready")
    def readiness():
        try:
            runner.store.list_tasks(limit=1)
        except sqlite3.Error:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "components": {"database": "unavailable"}},
            )
        return {"status": "ready", "components": {"database": "ready"}}

    return app


app = create_app()
