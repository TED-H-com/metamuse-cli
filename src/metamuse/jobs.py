"""Durable single-executor jobs. Handlers register operations without editing this module."""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from metamuse import models as m
from metamuse.paths import private_file
from metamuse.store import Store

UncertainState = Literal["open_uncertain", "send_uncertain", "needs_reconcile"]
UNCERTAIN = ("open_uncertain", "send_uncertain", "needs_reconcile")
TERMINAL = ("succeeded", "partial", "failed", "cancelled")


@dataclass(frozen=True)
class Operation:
    handler: Callable[[StepContext], m.JobResult]
    side_effect_class: m.SideEffectClass
    request_model: type[m.Model] | None = None
    uncertain_state: UncertainState = "needs_reconcile"


OPERATIONS: dict[str, Operation] = {}


def register_operation(
    name: str,
    handler: Callable[[StepContext], m.JobResult],
    side_effect_class: m.SideEffectClass,
    *,
    request_model: type[m.Model] | None = None,
    uncertain_state: UncertainState = "needs_reconcile",
    registry: dict[str, Operation] | None = None,
) -> None:
    """Call once from your module's explicit registration function, before worker startup."""
    if side_effect_class not in ("none", "safe_read", "external_submit"):
        raise ValueError("Invalid side effect class")
    if uncertain_state not in UNCERTAIN:
        raise ValueError("Invalid uncertainty state")
    registry = OPERATIONS if registry is None else registry
    if not name or name in registry:
        raise ValueError("Operation name is empty or already registered")
    registry[name] = Operation(handler, side_effect_class, request_model, uncertain_state)


@dataclass(frozen=True)
class StepSpec:
    target: dict[str, Any] = field(default_factory=dict)


def worker_available(store: Store) -> bool:
    """OS ownership plus executor metadata; heartbeat expiry never grants ownership."""
    private_file(store.paths.worker_lock)
    with store.paths.worker_lock.open("r+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            with store.connection() as conn:
                return (
                    conn.execute(
                        "SELECT owner_nonce FROM worker_state WHERE singleton=1"
                    ).fetchone()[0]
                    is not None
                )
        else:
            fcntl.flock(handle, fcntl.LOCK_UN)
            return False


def submit_job(
    store: Store,
    operation: str,
    request_key: str,
    payload: dict[str, Any],
    *,
    project_id: str | None = None,
    steps: list[StepSpec] | None = None,
    requested_scope: dict[str, Any] | None = None,
    limits: dict[str, Any] | None = None,
    registry: dict[str, Operation] | None = None,
) -> m.Envelope:
    registry = OPERATIONS if registry is None else registry
    try:
        if operation not in registry:
            raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")
        op = registry[operation]
        if op.request_model:
            payload = op.request_model.model_validate(payload).model_dump(mode="json")
        m.ProjectCreateRequest(name=operation, request_key=request_key)  # shared key constraints
        specs = steps if steps is not None else [StepSpec()]
        if not specs:
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Job needs at least one step")
        # Bind metadata too: changing a target/project/limit cannot bypass the request key.
        identity = {
            "payload": payload,
            "project_id": project_id,
            "steps": [s.target for s in specs],
            "requested_scope": requested_scope or {},
            "limits": limits or {},
        }
        digest = m.payload_digest(identity)
        with store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE operation=? AND request_key=?", (operation, request_key)
            ).fetchone()
            if row:
                if row["payload_digest"] != digest:
                    raise m.CoreError(
                        m.ErrorCode.IDEMPOTENCY_CONFLICT, "Request key has a different payload"
                    )
                job = store.decode(m.Job, row)
            else:
                if project_id is not None:
                    store.get(m.Project, project_id, conn=conn)
                now = store.clock()
                job = m.Job(
                    id=m.new_id("job"),
                    project_id=project_id,
                    operation=operation,
                    request_key=request_key,
                    payload_digest=digest,
                    payload=payload,
                    requested_scope=requested_scope or {},
                    limits=limits or {},
                    created_at=now,
                    updated_at=now,
                )
                store.insert(job, conn=conn)
                for sequence, spec in enumerate(specs):
                    store.insert(
                        m.JobStep(
                            id=m.new_id("step"),
                            job_id=job.id,
                            sequence=sequence,
                            target=spec.target,
                            side_effect_class=op.side_effect_class,
                            uncertain_state=op.uncertain_state,
                        ),
                        conn=conn,
                    )
                store.event(conn, "job.accepted", job=job)
        return m.Envelope(
            status="accepted"
            if job.state not in TERMINAL
            else "partial"
            if job.state == "partial"
            else "ok",
            data={
                "job_id": job.id,
                "state": job.state,
                "worker_available": worker_available(store),
                "job": job.model_dump(mode="json"),
            },
        )
    except m.CoreError as exc:
        return m.failure(exc)
    except (ValueError, TypeError):
        return m.failure(m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid job payload"))


class BoundaryStop(Exception):
    """A persisted cancellation stops at a handler boundary."""


class StepContext:
    def __init__(self, executor: Executor, job: m.Job, step: m.JobStep):
        self.executor = executor
        self.store = executor.store
        self.job = job
        self.step = step
        self.payload = job.payload
        self.target = step.target
        self.checkpoint = step.checkpoint
        self.started = time.monotonic()

    def _guard(self, conn: Any, target: dict[str, Any] | None = None) -> None:
        self.executor._guard(conn)
        job = self.store.get(m.Job, self.job.id, conn=conn)
        step = self.store.get(m.JobStep, self.step.id, conn=conn)
        if job.cancel_requested:
            raise BoundaryStop()
        if job.state != "running" or step.state not in ("running", "submitting"):
            raise m.CoreError(m.ErrorCode.JOB_BUSY, "Job is no longer running")
        if job.worker_epoch != self.executor.epoch or step.worker_epoch != self.executor.epoch:
            raise m.CoreError(m.ErrorCode.JOB_BUSY, "Stale job callback")
        if step.target != (self.target if target is None else target):
            raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Step target changed")
        wall_limit = self.job.limits.get("wall_clock_seconds", 900)
        elapsed = time.monotonic() - self.started
        if job.started_at is not None:
            elapsed = max(
                elapsed,
                (
                    datetime.fromisoformat(self.store.clock())
                    - datetime.fromisoformat(job.started_at)
                ).total_seconds(),
            )
        if elapsed > wall_limit:
            raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Job wall clock limit reached")

    def guard(self) -> None:
        with self.store.transaction() as conn:
            self._guard(conn)

    def before_external_action(
        self, target: dict[str, Any], *, verify: Callable[[Any, dict[str, Any]], None]
    ) -> None:
        """Verify epoch/job/target and permission in a short transaction, then commit intent.

        verify must check current permit/target with this connection and must not wait externally.
        For external_submit this is the single submitting boundary. Never call send without it.
        """
        with self.store.transaction() as conn:
            self._guard(conn, target)
            verify(conn, target)
            row = self.store.get(m.JobStep, self.step.id, conn=conn)
            if row.side_effect_class == "external_submit":
                if row.state == "submitting":
                    raise m.CoreError(
                        m.ErrorCode.EXTERNAL_STATE_UNKNOWN,
                        "External submission intent already exists",
                    )
                conn.execute("UPDATE job_steps SET state='submitting' WHERE id=?", (row.id,))
                self.store.event(conn, "step.submitting", job=self.job, object_id=row.id)

    def commit_checkpoint(
        self, checkpoint: dict[str, Any], *, write_batch: Callable[[Any], None] | None = None
    ) -> None:
        """Batch records and checkpoint commit together; callbacks are database-only."""
        encoded = m.canonical_json(checkpoint).decode()
        with self.store.transaction() as conn:
            self._guard(conn)
            if write_batch:
                write_batch(conn)
            conn.execute("UPDATE job_steps SET checkpoint=? WHERE id=?", (encoded, self.step.id))
            self.executor._heartbeat(conn)
        self.checkpoint = checkpoint


class Executor:
    def __init__(
        self,
        store: Store,
        *,
        registry: dict[str, Operation] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.store = store
        self.registry = OPERATIONS if registry is None else registry
        self.sleep = sleep
        self.epoch: int | None = None
        self.nonce: str | None = None
        self.handle: Any = None
        self._stop = threading.Event()

    def __enter__(self) -> Executor:
        if self.handle is not None:
            raise m.CoreError(m.ErrorCode.JOB_BUSY, "Executor already acquired")
        self.handle = self.store.paths.worker_lock.open("r+b")
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            self.handle = None
            raise m.CoreError(m.ErrorCode.JOB_BUSY, "Another executor owns this workspace") from exc
        self.nonce = f"{os.getpid()}:{m.new_id('owner')}"
        try:
            with self.store.transaction() as conn:
                conn.execute(
                    "UPDATE worker_state SET worker_epoch=worker_epoch+1,owner_nonce=?,heartbeat=?",
                    (self.nonce, self.store.clock()),
                )
                self.epoch = conn.execute("SELECT worker_epoch FROM worker_state").fetchone()[0]
                self._recover(conn)
        except BaseException:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
            raise
        return self

    def __exit__(self, *args: Any) -> None:
        if self.handle is not None:
            try:
                with self.store.transaction() as conn:
                    conn.execute(
                        "UPDATE worker_state SET owner_nonce=NULL,heartbeat=NULL "
                        "WHERE worker_epoch=? AND owner_nonce=?",
                        (self.epoch, self.nonce),
                    )
            finally:
                fcntl.flock(self.handle, fcntl.LOCK_UN)
                self.handle.close()
                self.handle = None

    def _guard(self, conn: Any) -> None:
        row = conn.execute("SELECT * FROM worker_state WHERE singleton=1").fetchone()
        if (
            self.handle is None
            or row["worker_epoch"] != self.epoch
            or row["owner_nonce"] != self.nonce
        ):
            raise m.CoreError(m.ErrorCode.JOB_BUSY, "Stale executor epoch")

    def _heartbeat(self, conn: Any) -> None:
        self._guard(conn)
        conn.execute("UPDATE worker_state SET heartbeat=? WHERE singleton=1", (self.store.clock(),))

    def _recover(self, conn: Any) -> None:
        for row in conn.execute(
            "SELECT * FROM job_steps WHERE state IN ('running','submitting')"
        ).fetchall():
            step = self.store.decode(m.JobStep, row)
            job = self.store.get(m.Job, step.job_id, conn=conn)
            if step.state == "submitting":
                self._uncertain(conn, job, step)
            elif job.cancel_requested:
                self._cancel(conn, job)
            else:
                conn.execute(
                    "UPDATE job_steps SET state='queued',worker_epoch=NULL WHERE id=?", (step.id,)
                )
                conn.execute(
                    "UPDATE jobs SET state='queued',worker_epoch=NULL,updated_at=? WHERE id=?",
                    (self.store.clock(), job.id),
                )
                self.store.event(
                    conn,
                    "job.recovered_checkpoint",
                    job=job,
                    object_id=step.id,
                    detail={"has_checkpoint": bool(step.checkpoint)},
                )

    def _uncertain(self, conn: Any, job: m.Job, step: m.JobStep) -> None:
        state = step.uncertain_state
        error = m.CoreError(
            m.ErrorCode.EXTERNAL_STATE_UNKNOWN,
            "External delivery is unknown",
            next_action="Reconcile the original remote target before resuming",
        )
        encoded = m.canonical_json(error.detail).decode()
        conn.execute("UPDATE job_steps SET state=?,error=? WHERE id=?", (state, encoded, step.id))
        conn.execute(
            "UPDATE jobs SET state=?,remote_state='unknown',error=?,updated_at=? WHERE id=?",
            (
                "cancelled" if job.cancel_requested else "awaiting_user",
                encoded,
                self.store.clock(),
                job.id,
            ),
        )
        self.store.event(
            conn, "step.uncertain", job=job, object_id=step.id, detail={"state": state}
        )

    def _cancel(self, conn: Any, job: m.Job) -> None:
        conn.execute(
            "UPDATE jobs SET state='cancelled',stop_reason='cancel_requested',"
            "updated_at=? WHERE id=?",
            (self.store.clock(), job.id),
        )
        conn.execute(
            "UPDATE job_steps SET state='cancelled' WHERE job_id=? "
            "AND state IN ('queued','running')",
            (job.id,),
        )
        self.store.event(conn, "job.cancelled", job=job, detail={"remote_state": job.remote_state})

    def _claim(self) -> tuple[m.Job, m.JobStep] | None:
        with self.store.transaction() as conn:
            self._heartbeat(conn)
            for row in conn.execute(
                "SELECT * FROM jobs WHERE cancel_requested=1 AND state IN ('queued','running')"
            ).fetchall():
                self._cancel(conn, self.store.decode(m.Job, row))
            row = conn.execute(
                "SELECT s.* FROM job_steps s JOIN jobs j ON j.id=s.job_id "
                "WHERE s.state='queued' AND j.state IN ('queued','running') "
                "AND j.cancel_requested=0 AND NOT EXISTS(SELECT 1 FROM job_steps p "
                "WHERE p.job_id=s.job_id AND p.sequence<s.sequence "
                "AND p.state NOT IN ('succeeded','partial')) "
                "ORDER BY j.rowid,s.sequence LIMIT 1"
            ).fetchone()
            if not row:
                return None
            step = self.store.decode(m.JobStep, row)
            job = self.store.get(m.Job, step.job_id, conn=conn)
            if job.operation not in self.registry:
                error = m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")
                self._fail(conn, job, step, error)
                return job, self.store.get(m.JobStep, step.id, conn=conn)
            if self.registry[job.operation].side_effect_class != step.side_effect_class:
                self._fail(
                    conn,
                    job,
                    step,
                    m.CoreError(
                        m.ErrorCode.PROTOCOL_MISMATCH, "Operation side effect class changed"
                    ),
                )
                return job, self.store.get(m.JobStep, step.id, conn=conn)
            conn.execute(
                "UPDATE jobs SET state='running',worker_epoch=?,updated_at=?,"
                "started_at=COALESCE(started_at,?) WHERE id=?",
                (self.epoch, self.store.clock(), self.store.clock(), job.id),
            )
            conn.execute(
                "UPDATE job_steps SET state='running',worker_epoch=?,attempts=attempts+1 "
                "WHERE id=?",
                (self.epoch, step.id),
            )
            self.store.event(conn, "step.claimed", job=job, object_id=step.id)
            return self.store.get(m.Job, job.id, conn=conn), self.store.get(
                m.JobStep, step.id, conn=conn
            )

    def _fail(self, conn: Any, job: m.Job, step: m.JobStep, error: m.CoreError) -> None:
        encoded = m.canonical_json(error.detail).decode()
        state = "awaiting_user" if error.detail.retry_class == "user_action" else "failed"
        conn.execute("UPDATE job_steps SET state='failed',error=? WHERE id=?", (encoded, step.id))
        conn.execute(
            "UPDATE jobs SET state=?,error=?,updated_at=? WHERE id=?",
            (state, encoded, self.store.clock(), job.id),
        )
        self.store.event(
            conn,
            "step.failed",
            job=job,
            object_id=step.id,
            detail={"code": error.detail.code.value},
        )

    def run_once(self) -> bool:
        claimed = self._claim()
        if claimed is None:
            return False
        job, step = claimed
        if step.state == "failed":
            return True
        ctx = StepContext(self, job, step)
        try:
            ctx.guard()
            result = self.registry[job.operation].handler(ctx)
            if not isinstance(result, m.JobResult):
                raise m.CoreError(m.ErrorCode.STORAGE, "Handler returned an invalid result")
            encoded = m.canonical_json(result).decode()
            with self.store.transaction() as conn:
                ctx._guard(conn)
                state = "partial" if result.coverage_status == "partial" else "succeeded"
                conn.execute(
                    "UPDATE job_steps SET state=?,result=?,error=NULL WHERE id=?",
                    (state, encoded, step.id),
                )
                pending = conn.execute(
                    "SELECT 1 FROM job_steps WHERE job_id=? AND state='queued'", (job.id,)
                ).fetchone()
                if not pending:
                    results = [
                        json.loads(row[0])
                        for row in conn.execute(
                            "SELECT result FROM job_steps WHERE job_id=? ORDER BY sequence",
                            (job.id,),
                        )
                    ]
                    coverage = [r["coverage_status"] for r in results]
                    # Multi-step coverage cannot become more confident than its least certain batch.
                    overall = next(
                        (
                            c
                            for c in ("partial", "unknown", "bounded", "scope_satisfied")
                            if c in coverage
                        ),
                        "unknown",
                    )
                    final = m.JobResult(
                        coverage_status=overall,
                        requested_scope=job.requested_scope,
                        observed_scope={"steps": [r["observed_scope"] for r in results]},
                        stop_reason=result.stop_reason,
                        missing_fields=sorted({f for r in results for f in r["missing_fields"]}),
                        data={"steps": results},
                    )
                    final_state = "partial" if overall == "partial" else "succeeded"
                    conn.execute(
                        "UPDATE jobs SET state=?,coverage_status=?,observed_scope=?,stop_reason=?,"
                        "missing_fields=?,result=?,error=NULL,updated_at=? WHERE id=?",
                        (
                            final_state,
                            overall,
                            m.canonical_json(final.observed_scope).decode(),
                            final.stop_reason,
                            m.canonical_json(final.missing_fields).decode(),
                            m.canonical_json(final).decode(),
                            self.store.clock(),
                            job.id,
                        ),
                    )
                self.store.event(conn, "step.completed", job=job, object_id=step.id)
        except BoundaryStop:
            with self.store.transaction() as conn:
                self._guard(conn)
                current = self.store.get(m.JobStep, step.id, conn=conn)
                current_job = self.store.get(m.Job, job.id, conn=conn)
                if current.state == "submitting":
                    self._uncertain(conn, current_job, current)
                else:
                    self._cancel(conn, current_job)
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, m.CoreError)
                else m.CoreError(m.ErrorCode.STORAGE, "Operation handler failed")
            )
            retry_delay: float | None = None
            with self.store.transaction() as conn:
                self._guard(conn)
                current = self.store.get(m.JobStep, step.id, conn=conn)
                current_job = self.store.get(m.Job, job.id, conn=conn)
                if current.state == "submitting":
                    self._uncertain(conn, current_job, current)
                elif current_job.cancel_requested:
                    self._cancel(conn, current_job)
                elif (
                    step.side_effect_class == "safe_read"
                    and error.detail.retry_class == "safe_read"
                    and current.retries < 2
                ):
                    conn.execute(
                        "UPDATE job_steps SET state='queued',retries=retries+1,error=? WHERE id=?",
                        (m.canonical_json(error.detail).decode(), step.id),
                    )
                    retry_delay = error.retry_after
                else:
                    self._fail(conn, job, step, error)
            if retry_delay is not None:
                # No transaction is held during Retry-After; still own the OS lock.
                self.sleep(retry_delay)
        return True

    def request_stop(self) -> None:
        self._stop.set()

    def run(self, *, once: bool = False) -> int:
        count = 0
        while not self._stop.is_set():
            did_work = self.run_once()
            count += int(did_work)
            if once:
                break
            if not did_work:
                self._stop.wait(0.5)
        return count
