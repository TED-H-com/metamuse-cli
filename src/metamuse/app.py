"""Shared CLI/MCP application functions. No printing and no second orchestrator."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from pydantic import Field, ValidationError

from metamuse import models as m
from metamuse.jobs import UNCERTAIN, Operation, worker_available
from metamuse.paths import WorkspacePaths
from metamuse.store import Store, utc_now


class AppPolicy(m.Model):
    network_mode: Literal["offline", "live"] = "offline"
    telemetry_enabled: bool = False
    # Directories local import may read. Empty refuses every local file.
    import_roots: list[str] = Field(default_factory=list)


class WorkerConfig(m.Model):
    max_executors: Literal[1] = 1


class EnabledConfig(m.Model):
    enabled: bool = False


class Config(m.Model):
    app: AppPolicy = Field(default_factory=AppPolicy)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    limits: m.Limits = Field(default_factory=m.Limits)
    muse: EnabledConfig = Field(default_factory=EnabledConfig)
    publishing: EnabledConfig = Field(default_factory=EnabledConfig)


DEFAULT_CONFIG = """[app]
network_mode = "offline"
telemetry_enabled = false
[worker]
max_executors = 1
[limits]
max_urls = 10
max_items = 500
max_bytes = 1073741824
wall_clock_seconds = 900
media_max_bytes = 536870912
text_max_bytes = 20971520
packet_max_utf8_bytes = 49152
question_max_utf8_bytes = 8192
read_timeout_seconds = 30
safe_read_retries = 2
muse_poll_initial_seconds = 5
muse_poll_max_seconds = 30
muse_wait_seconds = 120
max_exchanges = 3
[muse]
enabled = false
[publishing]
enabled = false
"""


@dataclass
class AppContext:
    paths: WorkspacePaths
    store: Store
    clock: Callable[[], str] = utc_now
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("metamuse"))
    limits: m.Limits = field(default_factory=m.Limits)
    policy: Config = field(default_factory=Config)

    @classmethod
    def open(
        cls, workspace: str | Path | None = None, *, clock: Callable[[], str] = utc_now
    ) -> AppContext:
        paths = WorkspacePaths.resolve(workspace)
        store = Store(paths, clock=clock)
        config_path = paths.root / "metamuse.toml"
        if not config_path.exists():
            # Exclusive create also prevents two init processes from truncating configuration.
            try:
                with config_path.open("x") as handle:
                    config_path.chmod(0o600)
                    handle.write(DEFAULT_CONFIG)
            except FileExistsError:
                pass
        config_path.chmod(0o600)
        config = Config.model_validate(tomllib.loads(config_path.read_text()))
        return cls(paths, store, clock=clock, limits=config.limits, policy=config)


def application(model: type[m.Model]) -> Callable:
    def decorate(fn: Callable) -> Callable:
        @wraps(fn)
        def call(ctx: AppContext, request: Any = None) -> m.Envelope:
            try:
                if not isinstance(request, model):
                    request = model.model_validate({} if request is None else request)
                return fn(ctx, request)
            except m.CoreError as exc:
                return m.failure(exc)
            except (ValidationError, ValueError, TypeError):
                return m.failure(m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid request"))
            except OSError:
                return m.failure(m.CoreError(m.ErrorCode.STORAGE, "Workspace I/O failed"))

        return call

    return decorate


CORE_IMPLEMENTED_TOOLS = frozenset(
    {
        "metamuse_inspect",
        "metamuse_project_create",
        "metamuse_job_get",
        "metamuse_job_list",
        "metamuse_job_control",
    }
)


def _feature_modules() -> tuple[ModuleType, ...]:
    # Fixed imports only: no entrypoints, discovery, configuration or runtime cloning.
    from metamuse import collect, content, media, muse, safety

    return (collect, media, content, safety, muse)


def register_builtin_operations(registry: dict[str, Operation] | None = None) -> None:
    """Call before submission/execution; feature hooks must be idempotent per registry."""
    for module in _feature_modules():
        register = getattr(module, "register_operations", None)
        if register is not None:
            register(registry)


@application(m.InspectRequest)
def inspect_capabilities(ctx: AppContext, request: m.InspectRequest) -> m.Envelope:
    implemented = set(CORE_IMPLEMENTED_TOOLS)
    metadata: dict[str, dict[str, str]] = {}
    for module in _feature_modules():
        declared = module.IMPLEMENTED_TOOLS
        if not declared <= m.TOOL_REQUEST_MODELS.keys():
            raise m.CoreError(m.ErrorCode.STORAGE, "Feature declares an unknown tool")
        implemented.update(declared)
        status = getattr(module, "capability_status", None)
        if status is not None:
            rows = status(ctx)  # offline checks only; never perform a health request here
            if not rows.keys() <= declared:
                raise m.CoreError(m.ErrorCode.STORAGE, "Capability status has no implementation")
            metadata.update(rows)
    capabilities = []
    for name in m.TOOL_REQUEST_MODELS:
        core = name in CORE_IMPLEMENTED_TOOLS
        state = {
            "verification": "fixture_passed" if core else "unverified",
            "environment": "ready" if core else "unavailable",
            "permission": "granted_scope" if core else "not_configured",
        }
        if name in metadata:
            if not metadata[name].keys() <= state.keys():
                raise m.CoreError(m.ErrorCode.STORAGE, "Invalid capability status fields")
            state.update(metadata[name])
        capabilities.append(
            m.Capability(
                name=name,
                implementation="implemented" if name in implemented else "absent",
                effective=False,
                **state,
            ).model_dump(mode="json")
        )
    return m.Envelope(
        status="ok",
        data={
            "capabilities": capabilities,
            "worker_available": worker_available(ctx.store),
            "network_mode": ctx.policy.app.network_mode,
        },
    )


@application(m.ProjectCreateRequest)
def create_project(ctx: AppContext, request: m.ProjectCreateRequest) -> m.Envelope:
    with ctx.store.transaction() as conn:

        def create() -> dict[str, Any]:
            project = m.Project(id=m.new_id("prj"), name=request.name, created_at=ctx.clock())
            ctx.store.insert(project, conn=conn)
            ctx.store.event(conn, "project.created", object_id=project.id)
            return project.model_dump(mode="json")

        data = ctx.store.mutate_once(conn, "project.create", request.request_key, request, create)
    return m.Envelope(status="ok", data=data)


@application(m.JobGetRequest)
def get_job(ctx: AppContext, request: m.JobGetRequest) -> m.Envelope:
    with ctx.store.connection() as conn:
        conn.execute("BEGIN")  # one short read snapshot for job and steps
        job = ctx.store.get(m.Job, request.job_id, conn=conn)
        steps = [
            ctx.store.decode(m.JobStep, row).model_dump(mode="json")
            for row in conn.execute(
                "SELECT * FROM job_steps WHERE job_id=? ORDER BY sequence", (job.id,)
            )
        ]
    return m.Envelope(status="ok", data={"job": job.model_dump(mode="json"), "steps": steps})


def _encode_cursor(project: str | None, snapshot: int, after: int) -> str:
    return base64.urlsafe_b64encode(
        m.canonical_json({"v": 1, "project": project, "snapshot": snapshot, "after": after})
    ).decode()


@application(m.JobListQuery)
def list_jobs(ctx: AppContext, request: m.JobListQuery) -> m.Envelope:
    with ctx.store.connection() as conn:
        if request.project_id is not None:
            ctx.store.get(m.Project, request.project_id, conn=conn)
        snapshot = conn.execute("SELECT COALESCE(MAX(rowid),0) FROM jobs").fetchone()[0]
        after = 0
        if request.cursor:
            try:
                cursor = json.loads(base64.b64decode(request.cursor, altchars=b"-_", validate=True))
                if (
                    set(cursor) != {"v", "project", "snapshot", "after"}
                    or cursor["v"] != 1
                    or cursor["project"] != request.project_id
                    or type(cursor["snapshot"]) is not int
                    or type(cursor["after"]) is not int
                    or not 0 <= cursor["after"] <= cursor["snapshot"] <= snapshot
                ):
                    raise ValueError("Invalid cursor")
                snapshot, after = cursor["snapshot"], cursor["after"]
            except (ValueError, KeyError, TypeError, binascii.Error) as exc:
                raise m.CoreError(
                    m.ErrorCode.INPUT_INVALID, "Invalid cursor for this project"
                ) from exc
        rows = conn.execute(
            "SELECT rowid AS ordinal,* FROM jobs WHERE (? IS NULL OR project_id=?) "
            "AND rowid>? AND rowid<=? ORDER BY rowid LIMIT ?",
            (request.project_id, request.project_id, after, snapshot, request.limit + 1),
        ).fetchall()
        result = []
        for row in rows[: request.limit]:
            values = dict(row)
            values.pop("ordinal")
            # decode accepts Mapping as well as sqlite Row.
            result.append(ctx.store.decode(m.Job, values).model_dump(mode="json"))
        cursor = (
            _encode_cursor(request.project_id, snapshot, rows[request.limit - 1]["ordinal"])
            if len(rows) > request.limit
            else None
        )
    return m.Envelope(status="ok", data={"jobs": result}, next_cursor=cursor)


@application(m.JobControlRequest)
def control_job(ctx: AppContext, request: m.JobControlRequest) -> m.Envelope:
    with ctx.store.transaction() as conn:

        def control() -> dict[str, Any]:
            job = ctx.store.get(m.Job, request.job_id, conn=conn)
            steps = conn.execute("SELECT state FROM job_steps WHERE job_id=?", (job.id,)).fetchall()
            uncertain = any(row[0] in UNCERTAIN or row[0] == "submitting" for row in steps)
            if request.operation == "cancel":
                conn.execute(
                    "UPDATE jobs SET cancel_requested=1,updated_at=? WHERE id=?",
                    (ctx.clock(), job.id),
                )
                if job.state in ("queued", "awaiting_user"):
                    conn.execute(
                        "UPDATE jobs SET state='cancelled',stop_reason='cancel_requested' "
                        "WHERE id=?",
                        (job.id,),
                    )
                    conn.execute(
                        "UPDATE job_steps SET state='cancelled' WHERE job_id=? AND state='queued'",
                        (job.id,),
                    )
            else:
                if uncertain:
                    raise m.CoreError(
                        m.ErrorCode.EXTERNAL_STATE_UNKNOWN,
                        "Reconciliation is required before resume",
                    )
                if job.state == "running":
                    raise m.CoreError(m.ErrorCode.JOB_BUSY, "Job is already running")
                if job.state in ("failed", "awaiting_user", "cancelled"):
                    conn.execute(
                        "UPDATE jobs SET state='queued',cancel_requested=0,error=NULL,"
                        "stop_reason=NULL,updated_at=? WHERE id=?",
                        (ctx.clock(), job.id),
                    )
                    conn.execute(
                        "UPDATE job_steps SET state='queued',error=NULL WHERE job_id=? "
                        "AND state IN ('failed','cancelled')",
                        (job.id,),
                    )
            updated = ctx.store.get(m.Job, job.id, conn=conn)
            ctx.store.event(conn, f"job.{request.operation}_requested", job=updated)
            return {
                "job_id": job.id,
                "state": updated.state,
                "cancel_requested": updated.cancel_requested,
                "remote_state": updated.remote_state,
            }

        data = ctx.store.mutate_once(conn, "job.control", request.request_key, request, control)
    return m.Envelope(status="ok", data=data)


def unsupported() -> m.Envelope:
    return m.failure(m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build"))


@application(m.CollectRequest)
def submit_collect(ctx: AppContext, request: m.CollectRequest) -> m.Envelope:
    from metamuse import collect

    return collect.submit_collect(ctx, request)


@application(m.SearchRequest)
def search_evidence(ctx: AppContext, request: m.SearchRequest) -> m.Envelope:
    from metamuse import content

    return content.search_evidence(ctx, request)


@application(m.PacketPrepareRequest)
def prepare_packet(ctx: AppContext, request: m.PacketPrepareRequest) -> m.Envelope:
    from metamuse import content

    return content.prepare_packet(ctx, request)


@application(m.MuseOpenRequest)
def open_muse_discussion(ctx: AppContext, request: m.MuseOpenRequest) -> m.Envelope:
    from metamuse import muse

    return muse.open_muse_discussion(ctx, request)


@application(m.MuseSendRequest)
def submit_muse_exchange(ctx: AppContext, request: m.MuseSendRequest) -> m.Envelope:
    from metamuse import muse

    return muse.submit_muse_exchange(ctx, request)


@application(m.MuseReadRequest)
def read_muse_exchange(ctx: AppContext, request: m.MuseReadRequest) -> m.Envelope:
    from metamuse import muse

    return muse.read_muse_exchange(ctx, request)


@application(m.MuseCloseExchangeRequest)
def close_muse_exchange(ctx: AppContext, request: m.MuseCloseExchangeRequest) -> m.Envelope:
    from metamuse import muse

    return muse.close_muse_exchange(ctx, request)


@application(m.DraftSaveRequest)
def save_draft(ctx: AppContext, request: m.DraftSaveRequest) -> m.Envelope:
    from metamuse import content

    return content.save_draft(ctx, request)


@application(m.DraftPreviewRequest)
def preview_draft(ctx: AppContext, request: m.DraftPreviewRequest) -> m.Envelope:
    from metamuse import content

    return content.preview_draft(ctx, request)


@application(m.ExportRequest)
def export_objects(ctx: AppContext, request: m.ExportRequest) -> m.Envelope:
    from metamuse import content

    return content.export_objects(ctx, request)


TOOL_FUNCTIONS = dict(
    zip(
        m.TOOL_REQUEST_MODELS,
        (
            inspect_capabilities,
            create_project,
            submit_collect,
            get_job,
            list_jobs,
            control_job,
            search_evidence,
            prepare_packet,
            open_muse_discussion,
            submit_muse_exchange,
            read_muse_exchange,
            close_muse_exchange,
            save_draft,
            preview_draft,
            export_objects,
        ),
        strict=True,
    )
)
