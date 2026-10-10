import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from converter_copilot.api import runner as runner_module
from converter_copilot.api.runner import TaskRunner
from converter_copilot.config import WorkerSettings
from converter_copilot.domain.schemas import ConverterReport, TaskCreate, TaskMode
from converter_copilot.storage.sqlite_store import SQLiteStore


def _request():
    return TaskCreate(mode=TaskMode.FAULT_ANALYSIS, inputs={"equipment_type": "PV inverter", "symptom": "DC bus overvoltage"})


def _report(task_id: str):
    return ConverterReport(
        task_id=task_id,
        mode="fault_analysis",
        result={"hypotheses": [], "checks": []},
        claims=[],
        evidence=[],
        quality_warnings=[],
        completion_status="completed",
    )


class FixedGraph:
    async def ainvoke(self, state):
        return {"report": _report(state["task_id"])}


def _runner(store, monkeypatch, graph=None, **settings):
    graph = graph or FixedGraph()
    monkeypatch.setattr(runner_module, "build_workflow", lambda mode, deps: graph)
    deps = SimpleNamespace(case_store=None)
    return TaskRunner(
        store,
        deps_provider=lambda: deps,
        settings=WorkerSettings(
            concurrency=settings.get("concurrency", 1),
            poll_seconds=settings.get("poll_seconds", 0.01),
            lease_seconds=settings.get("lease_seconds", 1),
            timeout_seconds=settings.get("timeout_seconds", 2),
            max_attempts=settings.get("max_attempts", 3),
        ),
    )


@pytest.mark.asyncio
async def test_queued_task_runs_after_runner_restart(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "restart.db")
    queued, _ = store.create_task(_request())
    runner = _runner(store, monkeypatch)

    assert await runner.run_once() is True
    assert store.get_task(queued.task_id).status == "completed"
    assert store.get_report(queued.task_id) == _report(queued.task_id)
    assert await runner.run_once() is False


def test_expired_running_lease_is_requeued_once(tmp_path):
    store = SQLiteStore(tmp_path / "expired.db")
    task, _ = store.create_task(_request())
    claimed = store.claim_next_task("worker-old", lease_seconds=30)
    assert claimed.task_id == task.task_id
    with store._connect() as connection:
        connection.execute(
            "UPDATE tasks SET lease_expires_at = ? WHERE task_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), task.task_id),
        )

    assert store.requeue_expired_tasks() == [task.task_id]
    assert store.requeue_expired_tasks() == []
    assert store.get_task(task.task_id).status == "queued"
    assert [event.stage for event in store.events_after(task.task_id)].count("recovered") == 1


def test_only_current_lease_owner_can_renew(tmp_path):
    store = SQLiteStore(tmp_path / "renew.db")
    task, _ = store.create_task(_request())
    store.claim_next_task("worker-current", lease_seconds=30)

    assert store.renew_task_lease(task.task_id, "worker-current", lease_seconds=45) is True
    assert store.renew_task_lease(task.task_id, "worker-stale", lease_seconds=45) is False
    assert store.get_task(task.task_id).attempt == 1


def test_worker_settings_reject_non_positive_limits():
    with pytest.raises(ValueError, match="must be positive"):
        WorkerSettings(concurrency=0)
    with pytest.raises(ValueError, match="poll interval"):
        WorkerSettings(poll_seconds=0)


def test_expired_task_at_attempt_limit_becomes_failed(tmp_path):
    store = SQLiteStore(tmp_path / "exhausted.db")
    task, _ = store.create_task(_request())
    store.claim_next_task("worker-old", lease_seconds=30, max_attempts=1)
    with store._connect() as connection:
        connection.execute(
            "UPDATE tasks SET lease_expires_at = ? WHERE task_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), task.task_id),
        )

    assert store.requeue_expired_tasks() == [task.task_id]
    record = store.get_task(task.task_id)
    assert record.status == "failed"
    assert record.error_code == "attempt_limit"


def test_stale_worker_cannot_overwrite_new_lease_result(tmp_path):
    store = SQLiteStore(tmp_path / "fencing.db")
    task, _ = store.create_task(_request())
    store.claim_next_task("worker-old", lease_seconds=1)
    with store._connect() as connection:
        connection.execute(
            "UPDATE tasks SET lease_expires_at = ? WHERE task_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), task.task_id),
        )
    store.requeue_expired_tasks()
    store.claim_next_task("worker-new", lease_seconds=30)

    assert store.finish_task(task.task_id, _report(task.task_id), worker_id="worker-old") is False
    assert store.get_report(task.task_id) is None
    assert store.finish_task(task.task_id, _report(task.task_id), worker_id="worker-new") is True
    assert store.get_task(task.task_id).status == "completed"


@pytest.mark.asyncio
async def test_worker_never_exceeds_configured_concurrency(tmp_path, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingGraph:
        active = 0
        maximum = 0

        async def ainvoke(self, state):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            started.set()
            await release.wait()
            self.active -= 1
            return {"report": _report(state["task_id"])}

    store = SQLiteStore(tmp_path / "concurrency.db")
    graph = BlockingGraph()
    runner = _runner(store, monkeypatch, graph, concurrency=1, poll_seconds=0.01)
    store.create_task(_request())
    store.create_task(_request())
    await runner.start()
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.sleep(0.05)
    assert graph.maximum == 1
    release.set()
    await asyncio.sleep(0.1)
    await runner.stop()


@pytest.mark.asyncio
async def test_failed_attempt_persists_safe_error_category(tmp_path, monkeypatch):
    class FailingGraph:
        async def ainvoke(self, state):
            raise RuntimeError("provider key=secret-value should never be persisted")

    store = SQLiteStore(tmp_path / "safe-error.db")
    task, _ = store.create_task(_request())
    runner = _runner(store, monkeypatch, FailingGraph())

    assert await runner.run_once() is True
    record = store.get_task(task.task_id)
    assert record.status == "failed"
    assert record.error_code == "workflow_error"
    assert "secret-value" not in " ".join(event.message for event in store.events_after(task.task_id))
    assert store.get_report(task.task_id) is None
