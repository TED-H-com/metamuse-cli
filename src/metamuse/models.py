"""Public typed contracts. Regenerate schemas with python -m metamuse.schemas."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = Annotated[str, Field(min_length=3, max_length=128)]
RequestKey = Annotated[str, Field(min_length=1, max_length=128)]
Text = Annotated[str, Field(max_length=1_000_000)]
Revision = Annotated[int, Field(ge=1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def canonical_json(value: Any) -> bytes:
    """No Unicode normalization; nonfinite numbers and non-string keys are invalid."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")

    def check(node: Any) -> None:
        if isinstance(node, dict):
            if any(not isinstance(key, str) for key in node):
                raise ValueError("JSON object keys must be strings")
            for child in node.values():
                check(child)
        elif isinstance(node, (list, tuple)):
            for child in node:
                check(child)

    check(value)
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def payload_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


class ErrorCode(StrEnum):
    INPUT_INVALID = "E_INPUT_INVALID"
    REFERENCE_INVALID = "E_REFERENCE_INVALID"
    SCOPE_REQUIRED = "E_SCOPE_REQUIRED"
    SCOPE_DENIED = "E_SCOPE_DENIED"
    AUTH_REQUIRED = "E_AUTH_REQUIRED"
    DEPENDENCY_MISSING = "E_DEPENDENCY_MISSING"
    UNSUPPORTED = "E_UNSUPPORTED"
    PROTOCOL_MISMATCH = "E_PROTOCOL_MISMATCH"
    ACCESS_DENIED = "E_ACCESS_DENIED"
    CHECKPOINT_REQUIRED = "E_CHECKPOINT_REQUIRED"
    RATE_LIMITED = "E_RATE_LIMITED"
    SELECTOR_DRIFT = "E_SELECTOR_DRIFT"
    NETWORK_TIMEOUT = "E_NETWORK_TIMEOUT"
    JOB_BUSY = "E_JOB_BUSY"
    IDEMPOTENCY_CONFLICT = "E_IDEMPOTENCY_CONFLICT"
    EXTERNAL_STATE_UNKNOWN = "E_EXTERNAL_STATE_UNKNOWN"
    STORAGE = "E_STORAGE"
    LIMIT_REACHED = "E_LIMIT_REACHED"
    DATA_STALE = "E_DATA_STALE"
    RESOURCE_UNAVAILABLE = "E_RESOURCE_UNAVAILABLE"


RetryClass = Literal["never", "safe_read", "reconcile", "user_action"]
# All codes have an explicit default retry and CLI exit mapping.
ERROR_POLICY: dict[ErrorCode, tuple[RetryClass, int]] = {
    ErrorCode.INPUT_INVALID: ("never", 2),
    ErrorCode.REFERENCE_INVALID: ("never", 2),
    ErrorCode.SCOPE_REQUIRED: ("user_action", 3),
    ErrorCode.SCOPE_DENIED: ("user_action", 3),
    ErrorCode.AUTH_REQUIRED: ("user_action", 3),
    ErrorCode.DEPENDENCY_MISSING: ("user_action", 4),
    ErrorCode.UNSUPPORTED: ("never", 4),
    ErrorCode.PROTOCOL_MISMATCH: ("never", 4),
    ErrorCode.ACCESS_DENIED: ("user_action", 3),
    ErrorCode.CHECKPOINT_REQUIRED: ("user_action", 3),
    ErrorCode.RATE_LIMITED: ("safe_read", 5),
    ErrorCode.SELECTOR_DRIFT: ("never", 4),
    ErrorCode.NETWORK_TIMEOUT: ("safe_read", 5),
    ErrorCode.JOB_BUSY: ("user_action", 5),
    ErrorCode.IDEMPOTENCY_CONFLICT: ("never", 2),
    ErrorCode.EXTERNAL_STATE_UNKNOWN: ("reconcile", 6),
    ErrorCode.STORAGE: ("never", 7),
    ErrorCode.LIMIT_REACHED: ("never", 5),
    ErrorCode.DATA_STALE: ("user_action", 3),
    ErrorCode.RESOURCE_UNAVAILABLE: ("user_action", 3),
}


class ErrorDetail(Model):
    code: ErrorCode = Field(strict=False)
    message: Text
    retry_class: RetryClass
    next_action: Text | None = None


class CoreError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        next_action: str | None = None,
        retry_after: float = 0,
    ):
        super().__init__(message)
        self.detail = ErrorDetail(
            code=code, message=message, retry_class=ERROR_POLICY[code][0], next_action=next_action
        )
        self.retry_after = max(0, retry_after)


class Envelope(Model):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        json_schema_extra={
            "required": [
                "contract_version",
                "request_id",
                "status",
                "data",
                "error",
                "warnings",
                "next_cursor",
            ],
            "allOf": [
                {
                    "if": {"properties": {"status": {"const": "error"}}},
                    "then": {"properties": {"data": {"type": "null"}, "error": {"type": "object"}}},
                    "else": {"properties": {"error": {"type": "null"}}},
                }
            ],
        },
    )
    contract_version: Literal["1.1"] = "1.1"
    request_id: ID = Field(default_factory=lambda: new_id("req"))
    status: Literal["ok", "accepted", "partial", "error"]
    data: dict[str, Any] | list[Any] | None = None
    error: ErrorDetail | None = None
    warnings: list[Text] = Field(default_factory=list)
    next_cursor: str | None = None

    @model_validator(mode="after")
    def consistent(self) -> Envelope:
        if self.status == "error":
            if self.error is None or self.data is not None:
                raise ValueError("error status requires error and null data")
        elif self.error is not None:
            raise ValueError("successful status requires null error")
        canonical_json(self.model_dump(mode="json"))
        return self


def failure(error: CoreError) -> Envelope:
    return Envelope(status="error", error=error.detail)


def exit_code(envelope: Envelope) -> int:
    if envelope.error:
        return ERROR_POLICY[envelope.error.code][1]
    return 5 if envelope.status == "partial" else 0


JobState = Literal[
    "queued", "running", "succeeded", "partial", "failed", "awaiting_user", "cancelled"
]
CoverageStatus = Literal["unknown", "scope_satisfied", "bounded", "partial"]
SideEffectClass = Literal["none", "safe_read", "external_submit"]
StepState = Literal[
    "queued",
    "running",
    "submitting",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
    "open_uncertain",
    "send_uncertain",
    "needs_reconcile",
]


class Limits(Model):
    max_urls: Annotated[int, Field(ge=1, le=100)] = 10
    max_items: Annotated[int, Field(ge=1, le=10000)] = 500
    max_bytes: Annotated[int, Field(ge=1)] = 1073741824
    wall_clock_seconds: Annotated[int, Field(ge=1, le=86400)] = 900
    media_max_bytes: Annotated[int, Field(ge=1)] = 536870912
    text_max_bytes: Annotated[int, Field(ge=1)] = 20971520
    packet_max_utf8_bytes: Annotated[int, Field(ge=1)] = 49152
    question_max_utf8_bytes: Annotated[int, Field(ge=1)] = 8192
    read_timeout_seconds: Annotated[int, Field(ge=1)] = 30
    safe_read_retries: Annotated[int, Field(ge=0, le=2)] = 2
    muse_poll_initial_seconds: int = 5
    muse_poll_max_seconds: int = 30
    muse_wait_seconds: int = 120
    max_exchanges: int = 3


class CollectLimits(Model):
    max_urls: Annotated[int, Field(ge=1, le=100)]
    max_items: Annotated[int, Field(ge=1, le=10000)]
    max_bytes: Annotated[int, Field(ge=1)]
    wall_clock_seconds: Annotated[int, Field(ge=1, le=86400)]


class EvidenceRef(Model):
    kind: Literal["item_revision", "transcript_segment", "draft_revision"]
    id: ID
    revision: Revision


class Capability(Model):
    name: str
    implementation: Literal["absent", "implemented"]
    verification: Literal["unverified", "fixture_passed", "live_verified", "regressed"]
    environment: Literal["ready", "missing_dependency", "needs_auth", "unavailable"]
    permission: Literal["not_configured", "granted_scope", "denied"]
    effective: bool

    @model_validator(mode="after")
    def compute_effective(self) -> Capability:
        self.effective = (
            self.implementation == "implemented"
            and self.verification in ("fixture_passed", "live_verified")
            and self.environment == "ready"
            and self.permission == "granted_scope"
        )
        return self


class Project(Model):
    id: ID
    name: Text
    revision: Revision = 1
    created_at: str


class JobResult(Model):
    coverage_status: CoverageStatus = "unknown"
    requested_scope: dict[str, Any] = Field(default_factory=dict)
    observed_scope: dict[str, Any] = Field(default_factory=dict)
    stop_reason: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class Job(Model):
    id: ID
    project_id: ID | None
    operation: str
    request_key: RequestKey
    payload_digest: Digest
    payload: dict[str, Any]
    state: JobState = "queued"
    coverage_status: CoverageStatus = "unknown"
    requested_scope: dict[str, Any] = Field(default_factory=dict)
    observed_scope: dict[str, Any] = Field(default_factory=dict)
    stop_reason: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    limits: dict[str, Any] = Field(default_factory=dict)
    worker_epoch: int | None = None
    cancel_requested: bool = False
    remote_state: str = "unknown"
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: str
    updated_at: str
    started_at: str | None = None


class JobStep(Model):
    id: ID
    job_id: ID
    sequence: Annotated[int, Field(ge=0)]
    target: dict[str, Any]
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    state: StepState = "queued"
    attempts: int = 0
    retries: int = 0
    side_effect_class: SideEffectClass
    uncertain_state: Literal["open_uncertain", "send_uncertain", "needs_reconcile"] = (
        "needs_reconcile"
    )
    worker_epoch: int | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class Source(Model):
    id: ID
    project_id: ID
    platform: str
    native_scope: str | None = None
    native_id: str | None = None
    original_url: str | None = None
    canonical_url: str | None = None
    collection_method: str
    upstream_collection_method: str = "unknown"
    assurance: str = "unknown"
    fetched_at: str
    use_basis: dict[str, Any] = Field(default_factory=dict)


class Item(Model):
    id: ID
    project_id: ID
    source_id: ID
    kind: Literal["post", "comment", "reply"]
    native_id: str | None = None
    parent_id: ID | None = None
    unresolved_parent: str | None = None
    identity_confidence: Literal["reliable", "weak"] = "weak"


class ItemRevision(Model):
    item_id: ID
    revision: Revision
    text: Text
    text_hash: Digest
    raw_time: str | None = None
    parsed_time: str | None = None
    source_time: str | None = None
    fetched_at: str
    complete: bool = False
    missing_fields: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class Asset(Model):
    id: ID
    sha256: Digest
    mime: str
    actual_bytes: Annotated[int, Field(ge=0)]
    duration_ms: Annotated[int, Field(ge=0)] | None = None
    object_path: str
    state: Literal["ready", "unavailable"]


class ItemAsset(Model):
    item_id: ID
    revision: Revision
    asset_id: ID
    relation: str


class SourceAsset(Model):
    """Project-scoped relation between a source observation and shared bytes."""

    source_id: ID
    project_id: ID
    asset_id: ID
    relation: str


class TranscriptSegment(Model):
    id: ID
    project_id: ID
    asset_id: ID | None = None
    item_id: ID | None = None
    source_id: ID | None = None
    revision: Revision
    cue_index: Annotated[int, Field(ge=0)] = 0
    start_ms: Annotated[int, Field(ge=0)]
    end_ms: Annotated[int, Field(ge=0)]
    text: Text
    language: str = "und"
    method: Literal["provided", "auto", "asr", "manual"]
    review: str = "unreviewed"
    overlap: bool = False

    @model_validator(mode="after")
    def anchored_and_ordered(self) -> TranscriptSegment:
        if self.asset_id is None and self.item_id is None and self.source_id is None:
            raise ValueError("transcript segment needs an asset, item, or source")
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be >= start_ms")
        return self


class Packet(Model):
    id: ID
    project_id: ID
    selection: list[EvidenceRef]
    rendered_text: Text
    redactions: list[dict[str, Any]] = Field(default_factory=list)
    coverage_status: CoverageStatus = "unknown"
    digest: Digest
    created_at: str
    stale: bool = False


class Permit(Model):
    id: ID
    project_id: ID
    kind: str
    scope: dict[str, Any]
    provider: str | None = None
    account: str | None = None
    issuer: str
    issued_at: str
    expires_at: str
    max_uses: Annotated[int, Field(ge=1)]
    used: Annotated[int, Field(ge=0)] = 0
    revoked: bool = False
    exact_digest: Digest | None = None


class Discussion(Model):
    id: ID
    project_id: ID
    provider: str
    auth_ref: str
    topic: Text
    remote_session: str | None = None
    open_attempt_state: str = "prepared"
    last_seq: int = 0
    state: str = "opening"


class Exchange(Model):
    id: ID
    project_id: ID
    discussion_id: ID
    job_id: ID | None = None
    request_key: RequestKey
    question: Text
    packet_id: ID
    digest: Digest
    permit_id: ID
    baseline: dict[str, Any] = Field(default_factory=dict)
    state: str = "prepared"
    outcome: Literal["answered", "needs_more_data", "abandoned"] | None = None


class Message(Model):
    id: ID
    discussion_id: ID
    exchange_id: ID | None = None
    remote_session: str
    remote_message_id: str | None = None
    seq: int | None = None
    role: str
    raw_status: str | None = None
    text: Text
    association: str = "unassociated"
    observed_at: str


class Draft(Model):
    id: ID
    project_id: ID
    current_revision: Revision = 1
    stale: bool = False


class DraftRevision(Model):
    draft_id: ID
    revision: Revision
    text: Text
    platform: str
    variant: dict[str, Any] = Field(default_factory=dict)
    facts_refs: list[EvidenceRef] = Field(default_factory=list)
    assets: list[ID] = Field(default_factory=list)
    preferences: dict[str, Any] = Field(
        default_factory=lambda: {
            "language": "und",
            "emoji": "unspecified",
            "format": "plain_text",
            "scope": "this_draft",
        }
    )
    digest: Digest


class Export(Model):
    id: ID
    project_id: ID
    selection: list[EvidenceRef]
    format: str
    manifest: dict[str, Any] = Field(default_factory=dict)
    artifact: str | None = None
    state: str = "staging"


class Event(Model):
    id: ID
    object_id: str | None = None
    job_id: ID | None = None
    project_id: ID | None = None
    kind: str
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class InspectRequest(Model):
    pass


class ProjectCreateRequest(Model):
    name: Text
    request_key: RequestKey


class URLInput(Model):
    kind: Literal["url"]
    url: Annotated[str, Field(pattern=r"^https?://", json_schema_extra={"format": "uri"})]


class LocalFileInput(Model):
    kind: Literal["local_file"]
    path: Text
    format: Literal["text", "json", "jsonl", "csv", "vtt", "srt", "media"]
    # Server-measured at accept time. Clients may omit them; a supplied value is replaced.
    sha256: Digest | None = None
    byte_size: Annotated[int, Field(ge=0)] | None = None


class TextInput(Model):
    kind: Literal["text"]
    text: Text
    sha256: Digest | None = None
    byte_size: Annotated[int, Field(ge=0)] | None = None


class CollectRequest(Model):
    project_id: ID
    inputs: Annotated[
        list[Annotated[URLInput | LocalFileInput | TextInput, Field(discriminator="kind")]],
        Field(min_length=1, max_length=100),
    ]
    resources: Annotated[
        list[Literal["post", "comments", "media_metadata", "subtitles", "media_file"]],
        Field(min_length=1),
    ]
    limits: CollectLimits
    access_grant_id: ID | None
    request_key: RequestKey
    # Optional attachment for a subtitle or media input. Absent for comment imports.
    attach_item_id: ID | None = None
    attach_asset_id: ID | None = None
    language: Annotated[str, Field(min_length=1, max_length=35)] | None = None


class JobGetRequest(Model):
    job_id: ID


class JobListQuery(Model):
    project_id: ID | None = None
    cursor: str | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 50


class JobListRequest(JobListQuery):
    project_id: ID


class JobControlRequest(Model):
    job_id: ID
    operation: Literal["resume", "cancel"]
    request_key: RequestKey


class SearchRequest(JobListRequest):
    query: Text


class PacketPrepareRequest(Model):
    project_id: ID
    selection: Annotated[list[EvidenceRef], Field(min_length=1)]
    request_key: RequestKey


class MuseOpenRequest(Model):
    project_id: ID
    topic: Text
    access_grant_id: ID
    request_key: RequestKey


class MuseSendRequest(Model):
    discussion_id: ID
    packet_id: ID
    question: Annotated[str, Field(min_length=1, max_length=8192)]
    share_grant_id: ID
    request_key: RequestKey


class MuseReadRequest(Model):
    exchange_id: ID
    cursor: str | None = None
    refresh: bool = False
    access_grant_id: ID | None = None


class MuseCloseExchangeRequest(Model):
    exchange_id: ID
    outcome: Literal["answered", "needs_more_data", "abandoned"]
    request_key: RequestKey


class DraftSaveRequest(Model):
    project_id: ID
    draft_id: ID | None = None
    expected_revision: Revision | None = None
    text: Text
    platform: Literal["facebook", "instagram", "threads", "youtube", "tiktok", "x", "generic"]
    facts_refs: list[EvidenceRef]
    request_key: RequestKey


class DraftPreviewRequest(Model):
    draft_id: ID
    revision: Revision


class ExportRequest(PacketPrepareRequest):
    format: Literal["text", "markdown", "jsonl", "csv", "bundle"]


class AuthImportRequest(Model):
    """Human-only CLI input; never part of the MCP tool registry."""

    path: Text
    provider: Text | None = None
    account: Text | None = None


class PermitCreateRequest(Model):
    """Human-only local intent; the safety feature validates/creates the grant."""

    project_id: ID
    kind: Text
    scope: dict[str, Any] = Field(default_factory=dict)
    provider: Text | None = None
    account: Text | None = None
    issuer: Text = "local_operator"
    expires_at: Text | None = None
    max_uses: Annotated[int, Field(ge=1)] = 1
    exact_digest: Digest | None = None
    request_key: RequestKey


TOOL_REQUEST_MODELS = {
    "metamuse_inspect": InspectRequest,
    "metamuse_project_create": ProjectCreateRequest,
    "metamuse_collect": CollectRequest,
    "metamuse_job_get": JobGetRequest,
    "metamuse_job_list": JobListRequest,
    "metamuse_job_control": JobControlRequest,
    "metamuse_search": SearchRequest,
    "metamuse_packet_prepare": PacketPrepareRequest,
    "metamuse_muse_open": MuseOpenRequest,
    "metamuse_muse_send": MuseSendRequest,
    "metamuse_muse_read": MuseReadRequest,
    "metamuse_muse_close_exchange": MuseCloseExchangeRequest,
    "metamuse_draft_save": DraftSaveRequest,
    "metamuse_draft_preview": DraftPreviewRequest,
    "metamuse_export": ExportRequest,
}
