import subprocess
import sys

import pytest

from metamuse import app, jobs
from metamuse import models as m


def registry(handler=None, side_effect="none", uncertain="needs_reconcile"):
    operations = {}
    jobs.register_operation(
        "fixture",
        handler or (lambda ctx: m.JobResult(coverage_status="scope_satisfied")),
        side_effect,
        registry=operations,
        uncertain_state=uncertain,
    )
    return operations


def submit(ctx, project, operations, key="one", payload=None, **kwargs):
    return jobs.submit_job(
        ctx.store,
        "fixture",
        key,
        payload or {"fixture": "繁中😀"},
        project_id=project,
        registry=operations,
        **kwargs,
    )


def test_accepted_without_worker_and_idempotency(ctx, project):
    operations = registry()
    first = submit(ctx, project, operations)
    assert first.status == "accepted"
    assert first.data["state"] == "queued"
    assert first.data["worker_available"] is False
    same = submit(ctx, project, operations)
    assert first.data["job_id"] == same.data["job_id"]
    conflict = submit(ctx, project, operations, payload={"different": True})
    assert conflict.error.code == m.ErrorCode.IDEMPOTENCY_CONFLICT
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
        assert (
            conn.execute("SELECT count(*) FROM events WHERE kind='job.accepted'").fetchone()[0] == 1
        )
    with jobs.Executor(ctx.store, registry=operations) as executor:
        assert jobs.worker_available(ctx.store)
        executor.run_once()
    assert not jobs.worker_available(ctx.store)
    result = submit(ctx, project, operations)
    assert result.data["job_id"] == first.data["job_id"]
    assert result.data["state"] == "succeeded"


def test_success_failure_restart_no_repeat(ctx, project):
    calls = []

    def handler(step):
        calls.append(step.payload["fail"])
        if step.payload["fail"]:
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Synthetic invalid input")
        step.commit_checkpoint({"done": 1})
        return m.JobResult(
            coverage_status="bounded",
            requested_scope={"items": 2},
            observed_scope={"items": 1},
            stop_reason="limit",
            missing_fields=["time"],
        )

    operations = registry(handler)
    success = submit(ctx, project, operations, "success", {"fail": False}).data["job_id"]
    failure = submit(ctx, project, operations, "failure", {"fail": True}).data["job_id"]
    with jobs.Executor(ctx.store, registry=operations) as executor:
        assert executor.run_once() and executor.run_once()
    with jobs.Executor(ctx.store, registry=operations) as executor:
        assert not executor.run_once()
    assert calls == [False, True]
    assert ctx.store.get(m.Job, success).coverage_status == "bounded"
    assert ctx.store.get(m.Job, failure).state == "failed"
    assert ctx.store.get(m.Job, success).missing_fields == ["time"]


def test_multistep_resume_preserves_success(ctx, project):
    calls = []
    should_fail = True

    def handler(step):
        calls.append(step.step.sequence)
        if step.step.sequence == 1 and should_fail:
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "fixture")
        return m.JobResult(coverage_status="scope_satisfied")

    operations = registry(handler)
    job_id = submit(ctx, project, operations, steps=[jobs.StepSpec(), jobs.StepSpec()]).data[
        "job_id"
    ]
    with jobs.Executor(ctx.store, registry=operations) as executor:
        executor.run_once()
        executor.run_once()
    should_fail = False
    response = app.control_job(
        ctx, {"job_id": job_id, "operation": "resume", "request_key": "resume"}
    )
    assert response.status == "ok"
    with jobs.Executor(ctx.store, registry=operations) as executor:
        executor.run_once()
    assert calls == [0, 1, 1]
    assert ctx.store.get(m.Job, job_id).state == "succeeded"


PROCESS = r"""
import os, socket, sys
from metamuse.app import AppContext
from metamuse.jobs import Executor, register_operation
from metamuse.models import CoreError, JobResult

def forbidden(*args, **kwargs):
    raise AssertionError("Test process tried to connect")
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
ctx = AppContext.open(sys.argv[1])

def handler(step):
    if sys.argv[2] == "crash_external":
        step.before_external_action(step.target, verify=lambda conn, target: None)
    else:
        step.commit_checkpoint({"batch": 7})
    os._exit(23)

operations = {}
register_operation("fixture", handler,
                   "external_submit" if sys.argv[2] == "crash_external" else "none",
                   registry=operations, uncertain_state="send_uncertain")
try:
    with Executor(ctx.store, registry=operations) as executor:
        if sys.argv[2].startswith("crash"):
            executor.run_once()
        else:
            with ctx.store.transaction() as conn:
                conn.execute("UPDATE worker_state SET heartbeat='2000-01-01T00:00:00+00:00'")
            print("owned", executor.epoch, flush=True)
            sys.stdin.readline()
except CoreError as exc:
    print(exc.detail.code.value, flush=True)
    sys.exit(5)
"""


def test_two_real_processes_and_stale_heartbeat(ctx, project):
    holder = subprocess.Popen(
        [sys.executable, "-c", PROCESS, str(ctx.paths.root), "hold"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().startswith("owned")
        contender = subprocess.run(
            [sys.executable, "-c", PROCESS, str(ctx.paths.root), "contend"],
            input="\n",
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert contender.returncode == 5
        assert contender.stdout.strip() == "E_JOB_BUSY"
        with ctx.store.connection() as conn:
            state = conn.execute("SELECT * FROM worker_state").fetchone()
            assert state["worker_epoch"] == 1
            assert state["heartbeat"].startswith("2000")
        accepted = submit(ctx, project, registry())
        assert accepted.data["worker_available"] is True
        with pytest.raises(m.CoreError) as error:
            with jobs.Executor(ctx.store):
                pass
        assert error.value.detail.code == m.ErrorCode.JOB_BUSY
    finally:
        holder.communicate("\n", timeout=15)
        if holder.poll() is None:
            holder.kill()
            holder.wait()
    assert holder.returncode == 0
    with jobs.Executor(ctx.store) as executor:
        assert executor.epoch == 2


@pytest.mark.parametrize("mode", ["crash_local", "crash_external"])
def test_real_process_crash_recovery(ctx, project, mode):
    side_effect = "external_submit" if mode == "crash_external" else "none"
    calls = []

    def handler(step):
        calls.append(step.checkpoint)
        return m.JobResult(coverage_status="scope_satisfied")

    operations = registry(handler, side_effect, "send_uncertain")
    job_id = submit(ctx, project, operations).data["job_id"]
    crashed = subprocess.run(
        [sys.executable, "-c", PROCESS, str(ctx.paths.root), mode],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert crashed.returncode == 23, crashed.stderr
    with jobs.Executor(ctx.store, registry=operations) as executor:
        if side_effect == "external_submit":
            assert not executor.run_once()
        else:
            assert executor.run_once()
    job = ctx.store.get(m.Job, job_id)
    if side_effect == "external_submit":
        assert calls == [] and job.state == "awaiting_user"
        state = app.get_job(ctx, {"job_id": job_id}).data["steps"][0]["state"]
        assert state == "send_uncertain"
        resumed = app.control_job(
            ctx, {"job_id": job_id, "operation": "resume", "request_key": "resume"}
        )
        assert resumed.error.code == m.ErrorCode.EXTERNAL_STATE_UNKNOWN
    else:
        assert calls == [{"batch": 7}]
        assert job.state == "succeeded"


@pytest.mark.parametrize(
    "code,retries",
    [
        (m.ErrorCode.NETWORK_TIMEOUT, 2),
        (m.ErrorCode.RATE_LIMITED, 2),
        (m.ErrorCode.ACCESS_DENIED, 0),
        (m.ErrorCode.AUTH_REQUIRED, 0),
        (m.ErrorCode.CHECKPOINT_REQUIRED, 0),
    ],
)
def test_safe_read_retry_policy(ctx, project, code, retries):
    calls = []
    delays = []

    def handler(step):
        calls.append(1)
        # A second writer works while handler executes; no long transaction is held.
        with ctx.store.transaction():
            pass
        raise m.CoreError(code, "Synthetic read failure", retry_after=0.2)

    operations = registry(handler, "safe_read")
    job_id = submit(ctx, project, operations).data["job_id"]
    with jobs.Executor(ctx.store, registry=operations, sleep=delays.append) as executor:
        for _ in range(4):
            executor.run_once()
    assert len(calls) == retries + 1
    assert delays == [0.2] * retries
    assert ctx.store.get(m.Job, job_id).state in ("failed", "awaiting_user")


def test_epoch_target_guard_and_atomic_checkpoint(ctx, project):
    operations = registry()
    job_id = submit(ctx, project, operations, steps=[jobs.StepSpec({"object": "fixture"})]).data[
        "job_id"
    ]
    with jobs.Executor(ctx.store, registry=operations) as executor:
        job, step = executor._claim()
        context = jobs.StepContext(executor, job, step)
        with pytest.raises(m.CoreError):
            context.before_external_action({"object": "other"}, verify=lambda conn, target: None)

        def bad_batch(conn):
            conn.execute("UPDATE projects SET name='should rollback' WHERE id=?", (project,))
            raise RuntimeError("fixture")

        with pytest.raises(RuntimeError):
            context.commit_checkpoint({"batch": 1}, write_batch=bad_batch)
        assert ctx.store.get(m.Project, project).name == "Synthetic project"
        assert ctx.store.get(m.JobStep, step.id).checkpoint == {}
        with ctx.store.transaction() as conn:
            conn.execute("UPDATE worker_state SET worker_epoch=worker_epoch+1")
        with pytest.raises(m.CoreError) as error:
            context.commit_checkpoint({"stale": True})
        assert error.value.detail.code == m.ErrorCode.JOB_BUSY
    assert ctx.store.get(m.Job, job_id).state == "running"


@pytest.mark.parametrize("side_effect", ["none", "external_submit"])
def test_cancel_persisted_at_boundary_remote_unknown(ctx, project, side_effect):
    calls = []

    def handler(step):
        if side_effect == "external_submit":
            step.before_external_action(step.target, verify=lambda conn, target: None)
            calls.append("sent once")
        result = app.control_job(
            ctx, {"job_id": step.job.id, "operation": "cancel", "request_key": "cancel"}
        )
        assert result.data["cancel_requested"]
        assert ctx.store.get(m.Job, step.job.id).cancel_requested
        step.guard()
        pytest.fail("Cancellation boundary was ignored")

    operations = registry(handler, side_effect)
    job_id = submit(ctx, project, operations).data["job_id"]
    with jobs.Executor(ctx.store, registry=operations) as executor:
        executor.run_once()
    with jobs.Executor(ctx.store, registry=operations) as executor:
        assert not executor.run_once()
    job = ctx.store.get(m.Job, job_id)
    assert job.state == "cancelled" and job.remote_state == "unknown"
    if side_effect == "external_submit":
        assert calls == ["sent once"]
        assert (
            app.control_job(
                ctx, {"job_id": job_id, "operation": "resume", "request_key": "resume"}
            ).error.code
            == m.ErrorCode.EXTERNAL_STATE_UNKNOWN
        )


def test_project_and_control_request_keys(ctx, project):
    assert (
        app.create_project(ctx, {"name": "Synthetic project", "request_key": "project-1"}).data[
            "id"
        ]
        == project
    )
    assert (
        app.create_project(ctx, {"name": "Different", "request_key": "project-1"}).error.code
        == m.ErrorCode.IDEMPOTENCY_CONFLICT
    )
    job_id = submit(ctx, project, registry()).data["job_id"]
    request = {"job_id": job_id, "operation": "cancel", "request_key": "control"}
    first = app.control_job(ctx, request)
    assert first.data == app.control_job(ctx, request).data
    assert (
        app.control_job(ctx, {**request, "operation": "resume"}).error.code
        == m.ErrorCode.IDEMPOTENCY_CONFLICT
    )


def test_job_list_snapshot_cursor_and_limits(ctx, project):
    operations = registry()
    ids = [submit(ctx, project, operations, str(i)).data["job_id"] for i in range(4)]
    first = app.list_jobs(ctx, {"project_id": project, "limit": 2})
    submit(ctx, project, operations, "new")
    second = app.list_jobs(ctx, {"project_id": project, "limit": 2, "cursor": first.next_cursor})
    assert [j["id"] for j in first.data["jobs"] + second.data["jobs"]] == ids
    assert second.next_cursor is None
    assert (
        app.list_jobs(ctx, {"project_id": project, "limit": 201}).error.code
        == m.ErrorCode.INPUT_INVALID
    )
    assert (
        app.list_jobs(ctx, {"project_id": project, "cursor": "invalid"}).error.code
        == m.ErrorCode.INPUT_INVALID
    )
    other = app.create_project(ctx, {"name": "Other", "request_key": "other"}).data["id"]
    assert (
        app.list_jobs(ctx, {"project_id": other, "cursor": first.next_cursor}).error.code
        == m.ErrorCode.INPUT_INVALID
    )
    assert len(app.list_jobs(ctx, {"project_id": other}).data["jobs"]) == 0


def test_unregistered_and_changed_side_effect(ctx, project):
    assert jobs.submit_job(ctx.store, "missing", "key", {}).error.code == m.ErrorCode.UNSUPPORTED
    operations = registry()
    job_id = submit(ctx, project, operations).data["job_id"]
    with jobs.Executor(ctx.store, registry=registry(side_effect="external_submit")) as executor:
        executor.run_once()
    assert ctx.store.get(m.Job, job_id).error["code"] == "E_PROTOCOL_MISMATCH"


@pytest.mark.parametrize("uncertain", ["open_uncertain", "send_uncertain", "needs_reconcile"])
def test_persisted_uncertainty_survives_missing_registry(ctx, project, uncertain):
    def handler(step):
        step.before_external_action(step.target, verify=lambda conn, target: None)
        raise KeyboardInterrupt()

    operations = registry(handler, "external_submit", uncertain)
    job_id = submit(ctx, project, operations).data["job_id"]
    with jobs.Executor(ctx.store, registry=operations) as executor:
        with pytest.raises(KeyboardInterrupt):
            executor.run_once()
    with jobs.Executor(ctx.store, registry={}) as executor:
        assert not executor.run_once()
    step = app.get_job(ctx, {"job_id": job_id}).data["steps"][0]
    assert step["state"] == uncertain
    assert ctx.store.get(m.Job, job_id).state == "awaiting_user"


def test_external_permission_rechecked_before_intent(ctx, project):
    calls = []

    def handler(step):
        def denied(conn, target):
            raise m.CoreError(m.ErrorCode.SCOPE_DENIED, "Synthetic permission denied")

        step.before_external_action(step.target, verify=denied)
        calls.append("external action")
        return m.JobResult()

    operations = registry(handler, "external_submit")
    job_id = submit(ctx, project, operations).data["job_id"]
    with jobs.Executor(ctx.store, registry=operations) as executor:
        executor.run_once()
    assert calls == []
    assert ctx.store.get(m.Job, job_id).state == "awaiting_user"
    assert app.get_job(ctx, {"job_id": job_id}).data["steps"][0]["state"] == "failed"


def test_job_wall_clock_limit_spans_steps(ctx, project):
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 1, 1, tzinfo=UTC)
    ctx.store.clock = lambda: now.isoformat()
    calls = []

    def handler(step):
        nonlocal now
        calls.append(step.step.sequence)
        now += timedelta(seconds=2)
        return m.JobResult()

    operations = registry(handler)
    job_id = submit(
        ctx,
        project,
        operations,
        steps=[jobs.StepSpec(), jobs.StepSpec()],
        limits={"wall_clock_seconds": 3},
    ).data["job_id"]
    with jobs.Executor(ctx.store, registry=operations) as executor:
        executor.run_once()
        executor.run_once()
    assert calls == [0, 1]
    assert ctx.store.get(m.Job, job_id).error["code"] == "E_LIMIT_REACHED"


def test_payload_digest_binds_target_and_scope(ctx, project):
    operations = registry()
    submit(ctx, project, operations, steps=[jobs.StepSpec({"id": "fixture"})])
    different = submit(ctx, project, operations, steps=[jobs.StepSpec({"id": "other"})])
    assert different.error.code == m.ErrorCode.IDEMPOTENCY_CONFLICT
    different = submit(
        ctx,
        project,
        operations,
        steps=[jobs.StepSpec({"id": "fixture"})],
        requested_scope={"items": 5},
    )
    assert different.error.code == m.ErrorCode.IDEMPOTENCY_CONFLICT
