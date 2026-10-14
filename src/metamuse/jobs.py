"Durable single-executor jobs. Handlers register operations without editing this module."

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from metamuse import models as m
from metamuse.config import load_config
from metamuse.store import Store, TransactionWriter

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
    "Call once from your module's explicit registration function, before worker startup."
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
    "Observe Linux FLOCK records without acquiring a lock (fcntl queries don't see flock)."
    with store.connection() as conn:
        owner = conn.execute("SELECT owner_nonce FROM worker_state WHERE singleton=1").fetchone()[0]
    if owner is None:
        return False
    try:
        stat = store.paths.worker_lock.stat()
        pid = int(owner.split(":", 1)[0])
        with Path("/proc/locks").open(encoding="ascii") as records:
            for line in records:
                fields = line.split()
                if len(fields) < 6 or fields[1] != "FLOCK" or fields[3] != "WRITE":
                    continue
                major, minor, inode = fields[5].split(":")
                if (int(fields[4]), int(major, 16), int(minor, 16), int(inode)) == (
                    pid,
                    os.major(stat.st_dev),
                    os.minor(stat.st_dev),
                    stat.st_ino,
                ):
                    return True
    except (OSError, ValueError):
        pass  # Visibility unavailable: never claim a worker exists without evidence.
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
    prepare_domain: Callable[[TransactionWriter, m.Job, list[m.JobStep]], None] | None = None,
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
                created_steps = []
                for sequence, spec in enumerate(specs):
                    created_steps.append(
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
                    )
                if prepare_domain:
                    prepare_domain(TransactionWriter(store, conn), job, created_steps)
                store.event(conn, "job.accepted", job=job)
        if job.state == "awaiting_user" or (
            job.error and job.error.get("retry_class") == "reconcile"
        ):
            error = (
                m.ErrorDetail.model_validate(job.error)
                if job.error
                else m.CoreError(m.ErrorCode.JOB_BUSY, "Job is awaiting user action").detail
            )
            return m.Envelope(
                status="error",
                error=error.model_copy(
                    update={
                        "next_action": f"Inspect job {job.id} with job_get; "
                        + (error.next_action or "resolve its pending action")
                    }
                ),
            )
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


class BoundaryStop(BaseException):
    """A persisted cancellation stops at a handler boundary."""


class ExternalActionToken:
    """Opaque committed boundary evidence. Only the issuing executor can mint it."""

    __slots__ = ("_issuer",)

    def __new__(cls, *args, **kwargs):
        raise TypeError("External action tokens are issuer-created")

    def __repr__(self) -> str:
        return "<ExternalActionToken>"

    def __reduce__(self):
        raise TypeError("External action tokens cannot be copied or serialized")

    def consume(self, *, action_digest: str) -> None:
        self._issuer._consume_token(self, action_digest)


class NotSubmittedProof:
    __slots__ = ()

    def __new__(cls):
        raise TypeError("Zero-handoff proof is issuer-created")

    def __repr__(self):
        return "<NotSubmittedProof>"


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
        wall_limit = min(
            self.executor.limits.wall_clock_seconds, self.job.limits.get("wall_clock_seconds", 900)
        )
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
        self, target: dict[str, Any], *, verify: Callable[[Any, dict[str, Any]], str | None]
    ) -> ExternalActionToken | None:
        """Verify epoch/job/target and permission in a short transaction, then commit intent.

        verify must check current permit/target with this connection and must not wait externally.
        For external_submit this is the single submitting boundary. Never call send without it.
        """
        with self.store.transaction() as conn:
            self._guard(conn, target)
            self.executor.require_network(operation=self.job.operation, target=target)
            row = self.store.get(m.JobStep, self.step.id, conn=conn)
            if row.state == "submitting":
                raise m.CoreError(
                    m.ErrorCode.EXTERNAL_STATE_UNKNOWN, "External submission intent already exists"
                )
            verified_digest = verify(conn, target)
            if verified_digest is not None:
                m.MuseProof(kind="ack", step_id=row.id, outbound_digest=verified_digest)
            if row.side_effect_class == "external_submit":
                if row.state == "submitting":
                    raise m.CoreError(
                        m.ErrorCode.EXTERNAL_STATE_UNKNOWN,
                        "External submission intent already exists",
                    )
                conn.execute("UPDATE job_steps SET state='submitting' WHERE id=?", (row.id,))
                self.store.event(conn, "step.submitting", job=self.job, object_id=row.id)
        if row.side_effect_class == "external_submit" and verified_digest is not None:
            m.MuseProof(kind="ack", step_id=row.id, outbound_digest=verified_digest)
            token = object.__new__(ExternalActionToken)
            token._issuer = self.executor
            self.executor._tokens[token] = (
                self,
                verified_digest,
                self.executor.epoch,
                self.executor.nonce,
                False,
            )
            return token
        return None

    def commit_checkpoint(
        self,
        checkpoint: dict[str, Any],
        *,
        write_batch: Callable[[TransactionWriter], None] | None = None,
    ) -> None:
        """Batch records and checkpoint commit together; callbacks are database-only."""
        encoded = m.canonical_json(checkpoint).decode()
        with self.store.transaction() as conn:
            self._guard(conn)
            if write_batch:
                write_batch(TransactionWriter(self.store, conn))
            self._guard(conn)
            conn.execute("UPDATE job_steps SET checkpoint=? WHERE id=?", (encoded, self.step.id))
            self.executor._heartbeat(conn)
        self.checkpoint = checkpoint

    def require_interactive(self) -> None:
        if self.executor.non_interactive:
            raise m.CoreError(m.ErrorCode.SCOPE_REQUIRED, "Human interaction is disabled")


class Executor:
    def __init__(
        self,
        store: Store,
        *,
        registry: dict[str, Operation] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        non_interactive: bool = False,
        muse_gateway: Any = None,
        secret_store: Any = None,
        muse_resolver: Any = None,
    ):
        self.store = store
        self.registry = OPERATIONS if registry is None else registry
        self.sleep = sleep
        self.non_interactive = non_interactive
        self.limits = load_config(store.paths.root).limits
        self.epoch: int | None = None
        self.nonce: str | None = None
        self.handle: Any = None
        self._stop = threading.Event()
        self.muse_gateway = muse_gateway
        self.secret_store = secret_store
        self.muse_resolver = muse_resolver
        self._tokens: dict[ExternalActionToken, tuple] = {}
        self._zero_handoff: dict[str, NotSubmittedProof] = {}
        self._proofs: dict[NotSubmittedProof, tuple[str, m.ErrorCode]] = {}

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
        self._tokens.clear()
        self._zero_handoff.clear()
        self._proofs.clear()
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

    def _consume_token(self, token: ExternalActionToken, digest: str) -> None:
        with self.store.transaction() as conn:
            entry = self._tokens.get(token)
            if entry is None:
                raise m.CoreError(m.ErrorCode.SCOPE_DENIED, "Unknown external action token")
            step, expected, epoch, nonce, consumed = entry
            if consumed or digest != expected or epoch != self.epoch or nonce != self.nonce:
                raise m.CoreError(
                    m.ErrorCode.SCOPE_DENIED,
                    "External action token does not authorize this request",
                )
            step._guard(conn, step.target)
            current = self.store.get(m.JobStep, step.step.id, conn=conn)
            if current.state != "submitting" or current.side_effect_class != "external_submit":
                raise m.CoreError(
                    m.ErrorCode.SCOPE_DENIED, "External submission boundary is absent"
                )
            self._tokens[token] = (step, expected, epoch, nonce, True)

    def record_not_submitted(self, step: StepContext, attempt: Any) -> NotSubmittedProof:
        # No boolean API: require the transport's concrete zero-handoff accounting.
        from metamuse.muse import MuseNotSubmitted, StreamAttempt

        valid = (
            isinstance(attempt, StreamAttempt)
            and attempt.state == "failed_before_send"
            and attempt.bytes_handed_off is False
        ) or isinstance(attempt, MuseNotSubmitted)
        if not valid or not any(entry[0] is step and entry[4] for entry in self._tokens.values()):
            raise m.CoreError(
                m.ErrorCode.INPUT_INVALID, "Affirmative in-process zero-handoff evidence required"
            )
        proof = object.__new__(NotSubmittedProof)
        code = (
            attempt.detail.code
            if isinstance(attempt, MuseNotSubmitted)
            else m.ErrorCode(attempt.error_code or "E_NETWORK_TIMEOUT")
        )
        self._proofs[proof] = (step.step.id, code)
        self._zero_handoff[step.step.id] = proof
        return proof

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
            if step.state == "submitting" or step.side_effect_class == "external_submit":
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
        if job.cancel_requested:
            conn.execute(
                "UPDATE job_steps SET state='cancelled' WHERE job_id=? AND state='queued'",
                (job.id,),
            )
            conn.execute("UPDATE jobs SET stop_reason='cancel_requested' WHERE id=?", (job.id,))

    def _cancel(self, conn: Any, job: m.Job) -> None:
        for row in conn.execute(
            "SELECT * FROM job_steps WHERE job_id=? AND "
            "side_effect_class='external_submit' AND state IN ('running','submitting')",
            (job.id,),
        ).fetchall():
            self._uncertain(conn, job, self.store.decode(m.JobStep, row))
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
            placeholders = ",".join("?" for _ in self.registry) or "NULL"
            row = conn.execute(
                "SELECT s.* FROM job_steps s JOIN jobs j ON j.id=s.job_id "
                "WHERE s.state='queued' AND j.state IN ('queued','running') "
                "AND j.cancel_requested=0 AND NOT EXISTS(SELECT 1 FROM job_steps p "
                "WHERE p.job_id=s.job_id AND p.sequence<s.sequence "
                "AND p.state NOT IN ('succeeded','partial')) "
                f"AND j.operation IN ({placeholders}) ORDER BY j.rowid,s.sequence LIMIT 1",
                tuple(self.registry),
            ).fetchone()
            if not row:
                return None
            step = self.store.decode(m.JobStep, row)
            job = self.store.get(m.Job, step.job_id, conn=conn)
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
            if step.side_effect_class != "none":
                try:
                    self.require_network(operation=job.operation, target=step.target)
                except m.CoreError as exc:
                    self._fail(conn, job, step, exc)
                    return job, self.store.get(m.JobStep, step.id, conn=conn)
            request_model = self.registry[job.operation].request_model
            if request_model is not None:
                try:
                    request_model.model_validate(job.payload, context={"limits": self.limits})
                except ValidationError as exc:
                    self._fail(conn, job, step, m.validation_error(exc))
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

    def _finish_step(self, conn: Any, job: m.Job, step: m.JobStep, result: m.JobResult) -> None:
        encoded = m.canonical_json(result).decode()
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
                    "SELECT result FROM job_steps WHERE job_id=? "
                    "AND result IS NOT NULL ORDER BY sequence",
                    (job.id,),
                )
            ]
            coverage = [r["coverage_status"] for r in results]
            # Multi-step coverage cannot become more confident than its least certain batch.
            overall = next(
                (c for c in ("partial", "unknown", "bounded", "scope_satisfied") if c in coverage),
                "unknown",
            )
            if job.cancel_requested:
                overall = "partial"
            final = m.JobResult(
                coverage_status=overall,
                requested_scope=job.requested_scope,
                observed_scope={"steps": [r["observed_scope"] for r in results]},
                stop_reason="cancel_requested" if job.cancel_requested else result.stop_reason,
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

    def require_network(self, *, operation: str | None = None, target: dict | None = None) -> None:
        policy = load_config(self.store.paths.root)
        if (
            operation in ("muse.open", "muse.send", "muse.history", "muse.sessions")
            and target
            and target.get("backend") == "fixture"
        ):
            if (policy.muse.enabled and policy.muse.backend == "fixture") or getattr(
                self.muse_gateway, "backend", None
            ) == "fixture":
                return
        if policy.app.network_mode == "offline":
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "External operations are disabled in offline mode",
                next_action="Enable live mode explicitly and configure the required permission",
            )

    def unavailable_operations(self) -> bool:
        with self.store.connection() as conn:
            return any(
                row[0] not in self.registry
                for row in conn.execute(
                    "SELECT operation FROM jobs WHERE state IN ('queued','running') "
                    "AND cancel_requested=0"
                )
            )

    def _retry_wait(self, job_id: str, seconds: float) -> None:
        while seconds > 0 and not self._stop.is_set():
            with self.store.transaction() as conn:
                self._heartbeat(conn)
                job = self.store.get(m.Job, job_id, conn=conn)
                if job.cancel_requested:
                    self._cancel(conn, job)
                    return
            interval = min(seconds, 0.25)
            self.sleep(interval)  # no database transaction; cap cancellation latency
            seconds -= interval

    def settle_uncertain(
        self,
        step_id: str,
        *,
        expected_target: dict[str, Any],
        verify: Callable[[Any, dict[str, Any]], None],
        result: m.JobResult | None = None,
        error: m.CoreError | None = None,
        not_submitted: NotSubmittedProof | None = None,
        write_batch: Callable[[TransactionWriter], None] | None = None,
    ) -> m.Job:
        """Settle proven delivery/refusal under this executor's epoch; never requeue/send.

        Read remote evidence before this call, outside transactions. verify is database-only
        and must establish the original target and proof. Exactly one of result/error or
        issuer-registered not_submitted proof is required.
        """
        if sum(value is not None for value in (result, error, not_submitted)) != 1 or (
            error and error.detail.retry_class in ("reconcile", "safe_read")
        ):
            raise m.CoreError(
                m.ErrorCode.INPUT_INVALID, "Settlement requires a definite result or refusal"
            )
        with self.store.transaction() as conn:
            self._guard(conn)
            step = self.store.get(m.JobStep, step_id, conn=conn)
            job = self.store.get(m.Job, step.job_id, conn=conn)
            if step.state not in UNCERTAIN or job.state not in ("awaiting_user", "cancelled"):
                raise m.CoreError(
                    m.ErrorCode.EXTERNAL_STATE_UNKNOWN, "Step is not awaiting reconciliation"
                )
            if step.target != expected_target:
                raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Settlement target changed")
            if (
                not_submitted is not None
                and self._proofs.get(not_submitted, (None, None))[0] != step_id
            ):
                raise m.CoreError(
                    m.ErrorCode.INPUT_INVALID, "Missing in-process zero-handoff proof"
                )
            verify(conn, expected_target)
            if write_batch:
                write_batch(TransactionWriter(self.store, conn))
            self._guard(conn)
            current = self.store.get(m.JobStep, step_id, conn=conn)
            current_job = self.store.get(m.Job, job.id, conn=conn)
            if (
                current.state != step.state
                or current.target != step.target
                or current_job.state != job.state
                or current_job.cancel_requested != job.cancel_requested
            ):
                raise m.CoreError(m.ErrorCode.JOB_BUSY, "Settlement state changed")
            conn.execute("UPDATE job_steps SET worker_epoch=? WHERE id=?", (self.epoch, step.id))
            if result is not None:
                self._finish_step(conn, job, step, result)
                if conn.execute(
                    "SELECT 1 FROM job_steps WHERE job_id=? AND state='queued'", (job.id,)
                ).fetchone():
                    conn.execute("UPDATE jobs SET state='queued',error=NULL WHERE id=?", (job.id,))
            else:
                self._fail(
                    conn,
                    job,
                    step,
                    error
                    or m.CoreError(
                        self._proofs[not_submitted][1]
                        if not_submitted
                        else m.ErrorCode.NETWORK_TIMEOUT,
                        "Request failed before frame handoff; use a new request key and grant",
                    ),
                )
                conn.execute("UPDATE jobs SET state='failed' WHERE id=?", (job.id,))
            conn.execute(
                "UPDATE jobs SET remote_state=?,worker_epoch=? WHERE id=?",
                (
                    "not_submitted"
                    if not_submitted is not None
                    else "confirmed"
                    if result is not None
                    else "refused",
                    self.epoch,
                    job.id,
                ),
            )
            if job.cancel_requested:
                self._cancel(conn, self.store.get(m.Job, job.id, conn=conn))
            if not_submitted is not None:
                self._proofs.pop(not_submitted)
                self._zero_handoff.pop(step_id, None)
            self.store.event(conn, "step.settled", job=job, object_id=step.id)
            return self.store.get(m.Job, job.id, conn=conn)

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
            with self.store.transaction() as conn:
                ctx._guard(conn)
                persisted = self.store.get(m.JobStep, step.id, conn=conn)
                if (
                    persisted.side_effect_class == "external_submit"
                    and persisted.state != "submitting"
                ):
                    raise m.CoreError(
                        m.ErrorCode.PROTOCOL_MISMATCH,
                        "External handler skipped the submitting boundary",
                    )
                self._finish_step(conn, job, step, result)
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
                    and current.retries
                    < min(
                        self.limits.safe_read_retries,
                        current_job.limits.get("safe_read_retries", 2),
                    )
                ):
                    conn.execute(
                        "UPDATE job_steps SET state='queued',retries=retries+1,error=? WHERE id=?",
                        (m.canonical_json(error.detail).decode(), step.id),
                    )
                    retry_delay = min(error.retry_after, 30, self.limits.read_timeout_seconds)
                else:
                    self._fail(conn, job, step, error)
            if current.state == "submitting" and current.id in self._zero_handoff:

                def zero_proof(conn, target):
                    row = self.store.get(m.JobStep, current.id, conn=conn)
                    if row.checkpoint.get("not_submitted") is not True:
                        raise m.CoreError(
                            m.ErrorCode.EXTERNAL_STATE_UNKNOWN,
                            "Zero-handoff accounting was not saved",
                        )

                self.settle_uncertain(
                    current.id,
                    expected_target=current.target,
                    verify=zero_proof,
                    not_submitted=self._zero_handoff[current.id],
                )
            if retry_delay is not None:
                # No transaction is held during Retry-After; still own the OS lock.
                self._retry_wait(job.id, retry_delay)
        for token, entry in tuple(self._tokens.items()):
            if entry[0] is ctx:
                del self._tokens[token]
        remaining = self._zero_handoff.pop(step.id, None)
        if remaining is not None:
            self._proofs.pop(remaining, None)
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
                if self.unavailable_operations():
                    import logging

                    logging.getLogger("metamuse").warning(
                        "Jobs with unavailable operations remain queued"
                    )
                    # This process cannot gain a missing implementation while idle.
                    break
                self._stop.wait(0.5)
        return count
