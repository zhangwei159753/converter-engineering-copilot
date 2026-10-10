"""FastAPI app factory for local Converter Engineering Copilot demos."""

import os
import sqlite3
from contextlib import asynccontextmanager

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
        runner = TaskRunner(
            SQLiteStore(os.getenv("CONVERTER_DB_PATH", ".data/converter_copilot.db"), initialize=False)
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.startup_error = None
        app.state.startup_component = None
        app.state.worker_started = False
        try:
            runner.store.initialize()
        except Exception:
            app.state.startup_error = "initialization_failed"
            app.state.startup_component = "database"
        else:
            try:
                await runner.start()
                app.state.worker_started = True
            except Exception:
                app.state.startup_error = "initialization_failed"
                app.state.startup_component = "worker"
        try:
            yield
        finally:
            if app.state.worker_started:
                await runner.stop()

    app = FastAPI(title="Converter Engineering Copilot", version="0.1.0", lifespan=lifespan)
    app.state.startup_error = None
    app.state.startup_component = None
    app.state.worker_started = False
    origin = os.getenv("WEB_ORIGIN", "http://localhost:5173")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID", "Idempotency-Key"],
    )
    app.include_router(build_task_router(runner))
    app.include_router(build_case_router(runner))

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "converter-engineering-copilot"}

    @app.get("/api/ready")
    def readiness():
        if app.state.startup_error:
            database_state = "unavailable" if app.state.startup_component == "database" else "ready"
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "components": {"database": database_state, "worker": "stopped"},
                },
            )
        try:
            runner.store.list_tasks(limit=1)
        except sqlite3.Error:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "components": {"database": "unavailable", "worker": "stopped"}},
            )
        if not app.state.worker_started or not runner.is_running:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "components": {"database": "ready", "worker": "stopped"}},
            )
        return {"status": "ready", "components": {"database": "ready", "worker": "ready"}}

    return app


app = create_app()
