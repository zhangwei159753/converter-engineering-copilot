import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from converter_copilot.api import runner as runner_module
from converter_copilot.api.runner import TaskRunner
from converter_copilot.config import WorkerSettings
from converter_copilot.domain.agent_audit import AgentRunRecord, HypothesisBranch
from converter_copilot.domain.schemas import ConverterReport, TaskCreate, TaskMode
from converter_copilot.storage.sqlite_store import SQLiteStore


def _request():
    return TaskCreate(
        mode=TaskMode.FAULT_ANALYSIS,
        inputs={
            "equipment_type": "PV inverter",
            "symptom": "DC bus overvoltage",
            "operating_conditions": {"load": "50%"},
        },
    )


def _report(task_id):
    return ConverterReport(
        task_id=task_id,
        mode=TaskMode.FAULT_ANALYSIS,
        result={"hypotheses": [], "checks": []},
        completion_status="completed",
    )


class LifecycleGraph:
    def __init__(self, deps, *, fail=False):
        self.deps = deps
        self.fail = fail

    async def ainvoke(self, state):
        started = AgentRunRecord(
            agent_run_id="run-coordinator",
            task_id=state["task_id"],
            role="coordinator",
            stage="intake",
            status="running",
            summary="检查输入。",
            started_at=datetime.now(timezone.utc),
        )
        self.deps.agent_event_sink(started)
        finished = started.model_copy(
            update={
                "status": "failed" if self.fail else "completed",
                "summary": "服务失败，凭据=private-secret" if self.fail else "输入校验完成。",
                "error_code": "agent_intake_failed" if self.fail else None,
                "completed_at": datetime.now(timezone.utc),
            }
        )
        self.deps.agent_event_sink(finished)
        if not self.fail:
            self.deps.agent_result_sink([
                HypothesisBranch(
                    branch_id=f"{state['task_id']}-branch-1",
                    task_id=state["task_id"],
                    hypothesis="Check the braking unit.",
                    mechanism_summary="Candidate only.",
                    ordinal=0,
                )
            ])
            return {"report": _report(state["task_id"])}
        raise RuntimeError("provider key=private-secret")


def _runner(store, monkeypatch, *, fail=False):
    shared_deps = SimpleNamespace(case_store=None, agent_event_sink=None, agent_result_sink=None)
    monkeypatch.setattr(
        runner_module,
        "build_workflow",
        lambda _mode, deps: LifecycleGraph(deps, fail=fail),
    )
    runner = TaskRunner(
        store,
        deps_provider=lambda: shared_deps,
        settings=WorkerSettings(lease_seconds=2, timeout_seconds=5),
    )
    return runner, shared_deps


def test_runner_persists_agent_lifecycle_branches_and_replayable_events(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "agent-lifecycle.db")
    task, _ = store.create_task(_request())
    runner, shared_deps = _runner(store, monkeypatch)

    assert asyncio.run(runner.run_once()) is True

    runs = store.list_agent_runs(task.task_id)
    assert len(runs) == 1
    assert runs[0].status == "completed"
    assert [item.branch_id for item in store.list_hypothesis_branches(task.task_id)] == [
        f"{task.task_id}-branch-1"
    ]
    events = store.events_after(task.task_id)
    agent_events = [event for event in events if event.stage == "agent:coordinator"]
    assert [event.status for event in agent_events] == ["running", "completed"]
    assert [event.event_id for event in events] == sorted(event.event_id for event in events)
    assert [event.event_id for event in store.events_after(task.task_id, agent_events[0].event_id)] == [
        *(event.event_id for event in events if event.event_id > agent_events[0].event_id)
    ]
    assert shared_deps.agent_event_sink is None
    assert shared_deps.agent_result_sink is None
    reopened = SQLiteStore(tmp_path / "agent-lifecycle.db")
    assert reopened.list_agent_runs(task.task_id) == runs
    assert reopened.list_hypothesis_branches(task.task_id)[0].branch_id == f"{task.task_id}-branch-1"


def test_runner_persists_sanitized_failure_without_secret_text(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "agent-failure.db")
    task, _ = store.create_task(_request())
    runner, _ = _runner(store, monkeypatch, fail=True)

    assert asyncio.run(runner.run_once()) is True

    run = store.list_agent_runs(task.task_id)[0]
    assert run.status == "failed"
    assert run.error_code == "agent_intake_failed"
    assert "private-secret" not in run.summary
    assert "private-secret" not in " ".join(event.message for event in store.events_after(task.task_id))


def test_runner_timeout_marks_running_agent_records_incomplete(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "agent-timeout.db")
    task, _ = store.create_task(_request())
    shared_deps = SimpleNamespace(case_store=None, agent_event_sink=None, agent_result_sink=None)

    class HangingGraph:
        def __init__(self, deps):
            self.deps = deps

        async def ainvoke(self, state):
            started = AgentRunRecord(
                agent_run_id="run-hanging", task_id=state["task_id"], role="coordinator",
                stage="intake", status="running", summary="检查输入。", started_at=datetime.now(timezone.utc),
            )
            self.deps.agent_event_sink(started)
            await asyncio.Event().wait()

    monkeypatch.setattr(runner_module, "build_workflow", lambda _mode, deps: HangingGraph(deps))
    runner = TaskRunner(
        store, deps_provider=lambda: shared_deps,
        settings=WorkerSettings(lease_seconds=3, timeout_seconds=1),
    )

    assert asyncio.run(runner.run_once()) is True

    assert store.get_task(task.task_id).status == "failed"
    run = store.list_agent_runs(task.task_id)[0]
    assert run.status == "incomplete"
    assert run.error_code == "task_interrupted"
    assert run.completed_at is not None


def test_runner_cancellation_stops_and_awaits_workflow_task(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "agent-cancel.db")
    task, _ = store.create_task(_request())
    shared_deps = SimpleNamespace(case_store=None, agent_event_sink=None, agent_result_sink=None)
    started_event = asyncio.Event()
    cancelled_event = asyncio.Event()

    class HangingGraph:
        def __init__(self, deps):
            self.deps = deps

        async def ainvoke(self, state):
            started = AgentRunRecord(
                agent_run_id="run-cancelled", task_id=state["task_id"], role="coordinator",
                stage="intake", status="running", summary="检查输入。", started_at=datetime.now(timezone.utc),
            )
            self.deps.agent_event_sink(started)
            started_event.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled_event.set()
                raise

    monkeypatch.setattr(runner_module, "build_workflow", lambda _mode, deps: HangingGraph(deps))
    runner = TaskRunner(
        store, deps_provider=lambda: shared_deps,
        settings=WorkerSettings(lease_seconds=30, timeout_seconds=10),
    )

    async def cancel_running_task():
        run_task = asyncio.create_task(runner.run_once())
        await started_event.wait()
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass

    asyncio.run(cancel_running_task())

    assert cancelled_event.is_set()
    assert store.list_agent_runs(task.task_id)[0].status == "incomplete"
    assert store.get_task(task.task_id).status == "running"


def test_agent_run_and_branch_updates_are_fenced_by_current_lease(tmp_path):
    store = SQLiteStore(tmp_path / "agent-fencing.db")
    task, _ = store.create_task(_request())
    store.claim_next_task("worker-old", lease_seconds=30)
    running = AgentRunRecord(
        agent_run_id="expired-run", task_id=task.task_id, role="coordinator", stage="intake",
        status="running", summary="running", started_at=datetime.now(timezone.utc),
    )
    assert store.save_agent_run_for_worker(running, worker_id="worker-old") is True
    with store._connect() as connection:
        connection.execute(
            "UPDATE tasks SET lease_expires_at = ? WHERE task_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), task.task_id),
        )
    assert store.requeue_expired_tasks() == [task.task_id]
    assert store.list_agent_runs(task.task_id)[0].status == "incomplete"
    store.claim_next_task("worker-new", lease_seconds=30)

    record = AgentRunRecord(
        agent_run_id="stale-run",
        task_id=task.task_id,
        role="coordinator",
        stage="intake",
        status="completed",
        summary="stale",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )

    assert store.save_agent_run_for_worker(record, worker_id="worker-old") is False
    assert store.replace_hypothesis_branches_for_worker(task.task_id, [], worker_id="worker-old") is False
    assert [run.status for run in store.list_agent_runs(task.task_id)] == ["incomplete"]
