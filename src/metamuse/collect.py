"""Local collection: files and text become project items, media, and subtitles.

Accepted file bytes are pinned by size and SHA-256. A later run that sees different
bytes records E_DATA_STALE for that input and still commits the other inputs.
URL inputs that request only media metadata, subtitles, or a media file are
handled by collect.remote_media. Any other URL shape, including posts and
comments, stays unsupported until B13.

The same reliable id twice in one batch is applied in order. Identical text writes
nothing new; different text appends a revision. A disagreeing kind, a self-parent,
or a cycle rejects that row and leaves the earlier row in place.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import os
import shutil
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from metamuse import jobs, remote_media, safety
from metamuse import media as media_mod
from metamuse import models as m
from metamuse.app import Config
from metamuse.jobs import StepContext, StepSpec, submit_job
from metamuse.store import TransactionWriter

OPERATION = "import.local"
BATCH_SIZE = 50
IMPLEMENTED_TOOLS: frozenset[str] = frozenset({"metamuse_collect"})
RECORD_FIELDS = {
    "platform",
    "kind",
    "native_id",
    "native_scope",
    "parent_native_id",
    "text",
    "source_url",
    "upstream_collection_method",
    "created_at_raw",
    "created_at",
    "author_display",
    "media",
}
CSV_FIELDS = RECORD_FIELDS | {"media_ref", "media_kind"}
APPLICABLE = {
    "text": {"post", "comments"},
    "json": {"post", "comments", "media_metadata", "media_file"},
    "jsonl": {"post", "comments", "media_metadata", "media_file"},
    "csv": {"post", "comments", "media_metadata", "media_file"},
    "vtt": {"subtitles"},
    "srt": {"subtitles"},
    "media": {"media_metadata", "media_file"},
}
FIELD_LIMITS = {
    "platform": 128,
    "kind": 32,
    "native_id": 512,
    "native_scope": 512,
    "parent_native_id": 512,
    "text": 1_000_000,
    "source_url": 4096,
    "upstream_collection_method": 128,
    "created_at_raw": 256,
    "created_at": 256,
    "author_display": 512,
}
KINDS = {"post", "comment", "reply"}
MEDIA_KINDS = {"image", "video", "audio", "file"}
REMOTE_NEXT = (
    "Remote URL collection is B12/B13 and stays LIVE-gated. "
    "Import a local file or text input instead."
)
MAX_NESTING = 32
MAX_STORED_ERRORS = 50
LOOKAHEAD = 64
_OWNER_NAME = ".collect-owner"
log = logging.getLogger(__name__)


def capability_status(ctx: Any) -> dict[str, dict[str, str]]:
    """Local import stays ready. ffprobe does not change that row.

    ``metamuse_collect.remote_media`` reports the yt-dlp extra. Its permission
    is ``granted_scope`` only when the workspace is live and
    ``remote_media.approvals`` is non-empty. Each collection still needs its
    own ``collect.remote`` permit at submit. This row is not that permit.
    """

    from metamuse import remote_media
    from metamuse.config import load_config

    remote_environment = "ready" if remote_media.dependency_available() else "missing_dependency"
    permission = "not_configured"
    if ctx is not None:
        workspace = load_config(ctx.store.paths.root)
        if workspace.app.network_mode != "offline" and workspace.remote_media.approvals:
            permission = "granted_scope"
    from metamuse import facebook

    return {
        "metamuse_collect": {
            "verification": "fixture_passed",
            "environment": "ready",
            "permission": "granted_scope",
        },
        "metamuse_collect.remote_media": {
            "verification": "fixture_passed",
            "environment": remote_environment,
            "permission": permission,
        },
        **facebook.capability_status(ctx),
    }


def _remote_media_request(request: m.CollectRequest) -> bool:
    """URL inputs whose resources are only remote media. Other URL shapes stay unsupported."""

    return (
        bool(request.inputs)
        and all(item.kind == "url" for item in request.inputs)
        and set(request.resources) <= {"media_metadata", "subtitles", "media_file"}
    )


# B13 adds its fixed route here and in _remote_route; the entrypoint has one callback.
_REMOTE_APPROVAL_ROUTES = {remote_media.OPERATION: remote_media.preview_remote_media}


def _remote_route(request: m.CollectRequest) -> str | None:
    return remote_media.OPERATION if _remote_media_request(request) else None


def _remote_approval_preview(ctx: Any, request: m.CollectRequest) -> m.ApprovalPreview:
    callback = _REMOTE_APPROVAL_ROUTES.get(_remote_route(request))
    if callback is None:
        raise m.CoreError(m.ErrorCode.UNSUPPORTED, "This collection route has no remote approval")
    return callback(ctx, request)


def register_operations(registry: dict[jobs.Operation] | None = None) -> None:
    safety.register_approval_preview(
        "collect.remote", _remote_approval_preview, request_model=m.CollectRequest
    )
    remote_media.register_operations(registry)
    from metamuse import facebook

    target = jobs.OPERATIONS if registry is None else registry
    if OPERATION not in target:
        jobs.register_operation(
            OPERATION,
            run_import,
            "none",
            request_model=m.CollectRequest,
            registry=target,
        )
    facebook.register_operations(target)


def _note_batch(step: StepContext) -> None:
    """Called after each durable batch. Tests replace this to simulate a crash."""

    del step


class _Invalid(Exception):
    def __init__(self, field_name: str, code: m.ErrorCode, message: str):
        self.field = field_name
        self.code = code
        self.message = message


class _FileInvalid(Exception):
    def __init__(self, code: m.ErrorCode, message: str, *, parser: bool = True):
        self.code = code
        self.message = message
        self.parser = parser


@dataclass
class Record:
    row: int
    line: int | None
    platform: str
    kind: str
    native_id: str | None
    scope: str
    parent_native_id: str | None
    parent_set: bool
    text: str | None
    source_url: str | None
    upstream: str
    created_at_raw: str | None
    parsed_time: str | None
    author_display: str | None
    media: list[dict[str, str]] | None


@dataclass
class _Revision:
    revision: int
    text_hash: str
    text: str | None
    missing_fields: list[str]
    raw_time: str | None
    parsed_time: str | None
    evidence: dict[str, Any]
    complete: bool


@dataclass
class _Item:
    item_id: str
    source_id: str
    platform: str
    scope: str
    native_id: str | None
    kind: str
    parent_id: str | None
    unresolved_parent: str | None
    confidence: str
    revision: int
    text_hash: str | None
    text: str | None
    missing_fields: list[str]
    raw_time: str | None
    parsed_time: str | None
    evidence: dict[str, Any]
    upstream: str
    collection_method: str
    original_url: str | None
    source_new: bool = False
    item_new: bool = False
    revision_new: bool = False
    parent_dirty: bool = False
    complete: bool = False
    revisions: list[_Revision] = field(default_factory=list)


def _freeze(item: _Item) -> _Revision:
    """Copy one unflushed revision before a later row in the same batch replaces it."""

    return _Revision(
        revision=item.revision,
        text_hash=item.text_hash or m.payload_digest({"text": None}),
        text=item.text,
        missing_fields=list(item.missing_fields),
        raw_time=item.raw_time,
        parsed_time=item.parsed_time,
        evidence=dict(item.evidence),
        complete=item.complete,
    )


@dataclass
class _Book:
    project_id: str
    by_key: dict[tuple[str, str, str], _Item] = field(default_factory=dict)
    by_id: dict[str, _Item] = field(default_factory=dict)
    pending: list[_Item] = field(default_factory=list)

    def load(self, conn: Any) -> None:
        rows = conn.execute(
            "SELECT i.id, i.source_id, i.kind, i.native_id, i.parent_id, i.unresolved_parent, "
            "i.identity_confidence, s.platform, s.native_scope, s.collection_method, "
            "s.upstream_collection_method, s.original_url, r.revision, r.text_hash "
            "FROM items i JOIN sources s ON s.id=i.source_id "
            "LEFT JOIN item_revisions r ON r.item_id=i.id AND r.revision="
            "(SELECT MAX(revision) FROM item_revisions WHERE item_id=i.id) "
            "WHERE i.project_id=?",
            (self.project_id,),
        )
        for row in rows:
            native = row["native_id"]
            reliable = row["identity_confidence"] == "reliable" and native
            scope = row["native_scope"] if row["native_scope"] is not None else ""
            item = _Item(
                item_id=row["id"],
                source_id=row["source_id"],
                platform=row["platform"],
                scope=scope,
                native_id=native if reliable else None,
                kind=row["kind"],
                parent_id=row["parent_id"],
                unresolved_parent=row["unresolved_parent"],
                confidence=row["identity_confidence"],
                revision=row["revision"] or 0,
                text_hash=row["text_hash"],
                text="",
                missing_fields=[],
                raw_time=None,
                parsed_time=None,
                evidence={},
                upstream=row["upstream_collection_method"],
                collection_method=row["collection_method"],
                original_url=row["original_url"],
            )
            self.by_id[item.item_id] = item
            if reliable:
                self.by_key[(item.platform, scope, native)] = item

    def parents(self) -> dict[str, str | None]:
        return {item.item_id: item.parent_id for item in self.by_id.values()}

    def apply(
        self, record: Record, *, method: str, sha: str, evidence_media: list[dict[str, Any]] | None
    ) -> tuple[_Item, str]:
        key = None
        if record.native_id:
            key = (record.platform, record.scope, record.native_id)
        existing = self.by_key.get(key) if key else None
        if existing is not None and existing.kind != record.kind:
            raise _Invalid(
                "kind",
                m.ErrorCode.REFERENCE_INVALID,
                "Reliable id was already imported with a different kind",
            )
        digest = m.payload_digest({"text": record.text})
        stored = record.text
        missing = ["text"] if record.text is None else []
        parent_id, unresolved = self._parent_of(existing, record)
        item_id = existing.item_id if existing else m.new_id("itm")
        mapping = self.parents()
        mapping[item_id] = parent_id
        if parent_id is not None and _cycle(item_id, parent_id, mapping):
            raise _Invalid(
                "parent_native_id",
                m.ErrorCode.REFERENCE_INVALID,
                "Parent link would create a cycle",
            )
        children = self._waiting_children(record, item_id, mapping)
        evidence: dict[str, Any] = {
            "collection_method": method,
            "upstream_collection_method": record.upstream,
            "input_sha256": sha,
            "row": record.row,
        }
        if record.author_display is not None:
            evidence["author_display"] = record.author_display
        if record.source_url is not None:
            evidence["source_url"] = record.source_url
        if evidence_media:
            evidence["media"] = evidence_media
        if existing is None:
            item = _Item(
                item_id=item_id,
                source_id=m.new_id("src"),
                platform=record.platform,
                scope=record.scope,
                native_id=record.native_id,
                kind=record.kind,
                parent_id=parent_id,
                unresolved_parent=unresolved,
                confidence="reliable" if record.native_id else "weak",
                revision=1,
                text_hash=digest,
                text=stored,
                missing_fields=missing,
                raw_time=record.created_at_raw,
                parsed_time=record.parsed_time,
                evidence=evidence,
                upstream=record.upstream,
                collection_method=method,
                original_url=record.source_url,
                source_new=True,
                item_new=True,
                revision_new=True,
                complete=not missing,
            )
            self.by_id[item.item_id] = item
            if key is not None:
                self.by_key[key] = item
            self.pending.append(item)
            status = "committed"
        else:
            item = existing
            wrote = False
            if item.text_hash != digest:
                if item.revision_new:
                    item.revisions.append(_freeze(item))
                item.revision += 1
                item.revision_new = True
                item.text_hash = digest
                item.text = stored
                item.missing_fields = list(missing)
                item.raw_time = record.created_at_raw
                item.parsed_time = record.parsed_time
                item.evidence = evidence
                item.complete = not missing
                wrote = True
            if record.parent_set and (
                item.parent_id != parent_id or item.unresolved_parent != unresolved
            ):
                item.parent_id = parent_id
                item.unresolved_parent = unresolved
                item.parent_dirty = True
                wrote = True
            if item not in self.pending and wrote:
                self.pending.append(item)
            status = "committed" if wrote else "unchanged"
        for child in children:
            child.parent_id = item.item_id
            child.unresolved_parent = None
            child.parent_dirty = True
            if child not in self.pending:
                self.pending.append(child)
        return item, status

    def _parent_of(self, existing: _Item | None, record: Record) -> tuple[str | None, str | None]:
        if not record.parent_set:
            if existing is None:
                return None, None
            return existing.parent_id, existing.unresolved_parent
        if record.parent_native_id is None:
            return None, None
        if record.native_id and record.parent_native_id == record.native_id:
            raise _Invalid(
                "parent_native_id",
                m.ErrorCode.REFERENCE_INVALID,
                "Item cannot be its own parent",
            )
        parent = self.by_key.get((record.platform, record.scope, record.parent_native_id))
        if parent is None:
            return None, record.parent_native_id
        return parent.item_id, None

    def _waiting_children(
        self, record: Record, item_id: str, mapping: dict[str, str | None]
    ) -> list[_Item]:
        if not record.native_id:
            return []
        linked: list[_Item] = []
        for child in self.by_id.values():
            if child.item_id == item_id or not child.unresolved_parent:
                continue
            if (
                child.unresolved_parent == record.native_id
                and child.platform == record.platform
                and child.scope == record.scope
            ):
                trial = dict(mapping)
                trial[child.item_id] = item_id
                if _cycle(child.item_id, item_id, trial):
                    raise _Invalid(
                        "parent_native_id",
                        m.ErrorCode.REFERENCE_INVALID,
                        "Parent link would create a cycle",
                    )
                mapping[child.item_id] = item_id
                linked.append(child)
        return linked

    def preview(self, record: Record) -> _Invalid | None:
        """Identity checks for a row that must not change the book."""

        key = (record.platform, record.scope, record.native_id) if record.native_id else None
        existing = self.by_key.get(key) if key else None
        if existing is not None and existing.kind != record.kind:
            return _Invalid(
                "kind",
                m.ErrorCode.REFERENCE_INVALID,
                "Reliable id was already imported with a different kind",
            )
        try:
            parent_id, _unresolved = self._parent_of(existing, record)
            item_id = existing.item_id if existing else "peek"
            mapping = self.parents()
            mapping[item_id] = parent_id
            if parent_id is not None and _cycle(item_id, parent_id, mapping):
                return _Invalid(
                    "parent_native_id",
                    m.ErrorCode.REFERENCE_INVALID,
                    "Parent link would create a cycle",
                )
            self._waiting_children(record, item_id, dict(mapping))
        except _Invalid as exc:
            return exc
        return None

    def flush(self, writer: TransactionWriter, store: Any) -> None:
        now = store.clock()
        for item in self.pending:
            if not item.source_new:
                continue
            writer.insert(
                m.Source(
                    id=item.source_id,
                    project_id=self.project_id,
                    platform=item.platform,
                    native_scope=item.scope if item.native_id else (item.scope or None),
                    native_id=item.native_id,
                    original_url=item.original_url,
                    collection_method=item.collection_method,
                    upstream_collection_method=item.upstream,
                    fetched_at=now,
                    use_basis={"input_sha256": item.evidence.get("input_sha256")},
                )
            )
        fresh = [item for item in self.pending if item.item_new]
        ready = set(self.by_id) - {item.item_id for item in fresh}
        for item in _parents_first(fresh, ready):
            writer.insert(
                m.Item(
                    id=item.item_id,
                    project_id=self.project_id,
                    source_id=item.source_id,
                    kind=item.kind,  # type: ignore[arg-type]
                    native_id=item.native_id,
                    parent_id=item.parent_id,
                    unresolved_parent=item.unresolved_parent,
                    identity_confidence=item.confidence,  # type: ignore[arg-type]
                )
            )
        for item in self.pending:
            if item.item_new or not item.parent_dirty:
                continue
            writer.execute(
                "UPDATE items SET parent_id=?, unresolved_parent=? WHERE id=?",
                (item.parent_id, item.unresolved_parent, item.item_id),
            )
        for item in self.pending:
            if not item.revision_new:
                continue
            for rev in (*item.revisions, _freeze(item)):
                writer.insert(
                    m.ItemRevision(
                        item_id=item.item_id,
                        revision=rev.revision,
                        text=rev.text,
                        text_hash=rev.text_hash,
                        raw_time=rev.raw_time,
                        parsed_time=rev.parsed_time,
                        source_time=rev.parsed_time,
                        fetched_at=now,
                        complete=rev.complete,
                        missing_fields=list(rev.missing_fields),
                        evidence=dict(rev.evidence),
                    )
                )
        for item in self.pending:
            item.source_new = False
            item.item_new = False
            item.revision_new = False
            item.parent_dirty = False
            item.revisions.clear()
        self.pending.clear()


def _cycle(child: str, parent: str | None, mapping: dict[str, str | None]) -> bool:
    seen: set[str] = set()
    cursor = parent
    while cursor is not None:
        if cursor == child or cursor in seen:
            return True
        seen.add(cursor)
        cursor = mapping.get(cursor)
    return False


def _parents_first(items: list[_Item], ready: set[str]) -> list[_Item]:
    remaining = list(items)
    ordered: list[_Item] = []
    while remaining:
        progressed = False
        later: list[_Item] = []
        for item in remaining:
            if item.parent_id is None or item.parent_id in ready:
                ordered.append(item)
                ready.add(item.item_id)
                progressed = True
            else:
                later.append(item)
        if not progressed:
            raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Parent link would create a cycle")
        remaining = later
    return ordered


def _reject_constant(value: str) -> None:
    raise ValueError("Nonfinite JSON number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite JSON number")
    return number


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _loads(text: str) -> Any:
    return json.loads(
        text,
        parse_constant=_reject_constant,
        parse_float=_finite_float,
        object_pairs_hook=_unique_object,
    )


def _bound_text(value: str, text_max_bytes: int) -> str:
    try:
        return m.utf8_text(value, text_max_bytes)
    except ValueError as exc:
        raise _Invalid(
            "text", m.ErrorCode.LIMIT_REACHED, "text exceeds the UTF-8 byte limit"
        ) from exc


def _optional_str(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Invalid(name, m.ErrorCode.INPUT_INVALID, f"{name} must be a string")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise _Invalid(name, m.ErrorCode.INPUT_INVALID, f"{name} is not valid Unicode text")
    limit = FIELD_LIMITS[name]
    if len(value) > limit:
        raise _Invalid(name, m.ErrorCode.LIMIT_REACHED, f"{name} exceeds {limit} characters")
    return value


def _parse_time(value: str) -> str:
    token = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(token)
    except ValueError as exc:
        raise _Invalid(
            "created_at", m.ErrorCode.INPUT_INVALID, "created_at is not timezone-aware ISO-8601"
        ) from exc
    if parsed.tzinfo is None:
        raise _Invalid(
            "created_at", m.ErrorCode.INPUT_INVALID, "created_at is not timezone-aware ISO-8601"
        )
    return parsed.astimezone(UTC).isoformat()


def _media_list(value: Any) -> list[dict[str, str]] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise _Invalid("media", m.ErrorCode.INPUT_INVALID, "media must be a list")
    if len(value) > 100:
        raise _Invalid("media", m.ErrorCode.LIMIT_REACHED, "media exceeds 100 entries")
    parsed: list[dict[str, str]] = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) - {"ref", "kind"}:
            raise _Invalid(
                "media", m.ErrorCode.INPUT_INVALID, "media entry must contain ref and kind"
            )
        ref = entry.get("ref")
        kind = entry.get("kind")
        if not isinstance(ref, str) or not isinstance(kind, str):
            raise _Invalid("media", m.ErrorCode.INPUT_INVALID, "media ref and kind must be strings")
        if len(ref) > 1024:
            raise _Invalid("media", m.ErrorCode.LIMIT_REACHED, "media ref exceeds 1024 characters")
        if kind not in MEDIA_KINDS:
            raise _Invalid("media", m.ErrorCode.INPUT_INVALID, "media kind is not supported")
        parsed.append({"ref": ref, "kind": kind})
    return parsed


def _record_from(
    obj: Any, *, row: int, line: int | None, text_max_bytes: int | None = None
) -> Record:
    if not isinstance(obj, dict):
        raise _Invalid("record", m.ErrorCode.INPUT_INVALID, "Record must be a JSON object")
    unknown = set(obj) - RECORD_FIELDS
    if unknown:
        raise _Invalid("record", m.ErrorCode.INPUT_INVALID, "Record contains an unknown field")
    platform = _optional_str(obj.get("platform"), "platform")
    kind = _optional_str(obj.get("kind"), "kind")
    if not platform:
        raise _Invalid("platform", m.ErrorCode.INPUT_INVALID, "platform is required")
    if kind not in KINDS:
        raise _Invalid("kind", m.ErrorCode.INPUT_INVALID, "kind must be post, comment, or reply")
    native = _optional_str(obj.get("native_id"), "native_id")
    if native == "":
        native = None
    scope = _optional_str(obj.get("native_scope"), "native_scope") or ""
    parent_set = "parent_native_id" in obj
    parent = _optional_str(obj.get("parent_native_id"), "parent_native_id") if parent_set else None
    if parent == "":
        parent = None
    limit = m.Limits().text_max_bytes if text_max_bytes is None else text_max_bytes
    text = _optional_str(obj.get("text"), "text") if "text" in obj else None
    if text is not None:
        text = _bound_text(text, limit)
    created = _optional_str(obj.get("created_at"), "created_at")
    parsed = _parse_time(created) if created else None
    raw = _optional_str(obj.get("created_at_raw"), "created_at_raw")
    if raw is None:
        raw = created
    upstream = _optional_str(obj.get("upstream_collection_method"), "upstream_collection_method")
    return Record(
        row=row,
        line=line,
        platform=platform,
        kind=kind,
        native_id=native,
        scope=scope,
        parent_native_id=parent,
        parent_set=parent_set,
        text=text,
        source_url=_optional_str(obj.get("source_url"), "source_url"),
        upstream=upstream or "unknown",
        created_at_raw=raw,
        parsed_time=parsed,
        author_display=_optional_str(obj.get("author_display"), "author_display"),
        media=_media_list(obj.get("media")) if "media" in obj else None,
    )


def _problem(row: int, line: int | None, exc: _Invalid) -> dict[str, Any]:
    return {
        "row": row,
        "line": line,
        "field": exc.field,
        "code": exc.code.value,
        "message": exc.message,
        "status": "rejected",
        "item_id": None,
        "revision": None,
        "segment_id": None,
        "asset_id": None,
        "source_id": None,
    }


def _strip_bom(data: bytes) -> bytes:
    return data[3:] if data.startswith(b"\xef\xbb\xbf") else data


def _decode(data: bytes) -> str:
    try:
        return _strip_bom(data).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "Input is not valid UTF-8") from exc


def _budgets(max_records: int | None, max_errors: int | None) -> tuple[int, int]:
    records = m.Limits().max_items if max_records is None else max_records
    errors = MAX_STORED_ERRORS if max_errors is None else max_errors
    if records < 0 or errors < 0:
        raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "Record budget is negative")
    return records, errors


class _ParseBudget:
    """Keep a bounded number of rows and errors. The rest is a count, not a copy."""

    def __init__(self, max_records: int, max_errors: int) -> None:
        self.max_records = max_records
        self.max_errors = max_errors
        self.seen = 0
        self.kept_errors = 0
        self.omitted = 0
        self.stopped = False
        self.rows: list[Record | dict[str, Any]] = []

    def add(self, item: Record | dict[str, Any], *, error: bool) -> bool:
        if self.seen >= self.max_records:
            self.stopped = True
            return False
        self.seen += 1
        if not error:
            self.rows.append(item)
            return True
        if self.kept_errors < self.max_errors:
            self.rows.append(item)
            self.kept_errors += 1
        else:
            self.omitted += 1
        return True

    def finish(self) -> list[Record | dict[str, Any]]:
        if self.omitted:
            self.rows.append(
                _problem(
                    None,
                    None,
                    _Invalid(
                        "record",
                        m.ErrorCode.LIMIT_REACHED,
                        f"{self.omitted} additional record errors omitted",
                    ),
                )
            )
        if self.stopped:
            self.rows.append(
                _problem(
                    None,
                    None,
                    _Invalid(
                        "record",
                        m.ErrorCode.LIMIT_REACHED,
                        "Record budget reached; remainder not inspected",
                    ),
                )
            )
        return self.rows


def _too_deep(text: str, limit: int = MAX_NESTING) -> bool:
    depth = 0
    in_string = False
    escape = False
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > limit:
                return True
        elif char in "]}":
            depth = max(0, depth - 1)
    return False


def _json_decoder() -> json.JSONDecoder:
    return json.JSONDecoder(
        parse_constant=_reject_constant,
        parse_float=_finite_float,
        object_pairs_hook=_unique_object,
    )


def _skip_ws(text: str, index: int) -> int:
    size = len(text)
    while index < size and text[index] in " \t\r\n":
        index += 1
    return index


def _skip_json_value(text: str, index: int) -> int:
    index = _skip_ws(text, index)
    size = len(text)
    if index >= size:
        return index
    char = text[index]
    if char == '"':
        index += 1
        escape = False
        while index < size:
            current = text[index]
            index += 1
            if escape:
                escape = False
            elif current == "\\":
                escape = True
            elif current == '"':
                break
        return index
    if char in "[{":
        depth = 0
        in_string = False
        escape = False
        while index < size:
            current = text[index]
            if in_string:
                if escape:
                    escape = False
                elif current == "\\":
                    escape = True
                elif current == '"':
                    in_string = False
            elif current == '"':
                in_string = True
            elif current in "[{":
                depth += 1
            elif current in "]}":
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1
        return size
    while index < size and text[index] not in ",]} \t\r\n":
        index += 1
    return index


def _value_too_deep(text: str, index: int, limit: int = MAX_NESTING) -> bool:
    index = _skip_ws(text, index)
    depth = 0
    in_string = False
    escape = False
    size = len(text)
    while index < size:
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            if depth == 0:
                return False
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > limit:
                return True
        elif char in "]}":
            depth -= 1
            if depth <= 0:
                return False
        elif depth == 0 and char == ",":
            return False
        index += 1
    return False


def _iter_lines(body: bytes):
    start = 0
    size = len(body)
    number = 0
    while start <= size:
        end = body.find(b"\n", start)
        if end == -1:
            end = size
        raw = body[start:end]
        number += 1
        nxt = end + 1
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        if raw.strip():
            yield number, raw
        if end == size:
            break
        start = nxt


def _iter_json_values(text: str):
    decoder = _json_decoder()
    index = _skip_ws(text, 0)
    if index >= len(text):
        raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "Input is not valid JSON")
    if text[index] == "{":
        if _value_too_deep(text, index):
            yield _Invalid("record", m.ErrorCode.LIMIT_REACHED, "Record nesting exceeds 32")
            return
        try:
            value, end = decoder.raw_decode(text, index)
        except (ValueError, RecursionError, MemoryError) as exc:
            raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "Input is not valid JSON") from exc
        if _skip_ws(text, end) != len(text):
            raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "Input is not valid JSON")
        yield value
        return
    if text[index] != "[":
        raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "JSON input must be an object or array")
    index += 1
    while True:
        index = _skip_ws(text, index)
        if index >= len(text):
            raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "Input is not valid JSON")
        if text[index] == "]":
            return
        if _value_too_deep(text, index):
            yield _Invalid("record", m.ErrorCode.LIMIT_REACHED, "Record nesting exceeds 32")
            index = _skip_json_value(text, index)
        else:
            try:
                value, end = decoder.raw_decode(text, index)
            except RecursionError:
                yield _Invalid("record", m.ErrorCode.LIMIT_REACHED, "Record nesting exceeds 32")
                return
            except MemoryError:
                yield _Invalid(
                    "record",
                    m.ErrorCode.LIMIT_REACHED,
                    "Record budget reached; remainder not inspected",
                )
                return
            except ValueError as exc:
                raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "Input is not valid JSON") from exc
            yield value
            index = end
        index = _skip_ws(text, index)
        if index < len(text) and text[index] == ",":
            index += 1
            if _skip_ws(text, index) >= len(text) or text[_skip_ws(text, index)] == "]":
                raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "Input is not valid JSON")
            continue
        if index < len(text) and text[index] == "]":
            return
        raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "Input is not valid JSON")


def _accept_record(
    budget: _ParseBudget,
    value: Any,
    *,
    row: int,
    line: int | None,
    text_max_bytes: int | None,
) -> None:
    if isinstance(value, _Invalid):
        budget.add(_problem(row, line, value), error=True)
        return
    try:
        budget.add(
            _record_from(value, row=row, line=line, text_max_bytes=text_max_bytes), error=False
        )
    except _Invalid as exc:
        budget.add(_problem(row, line, exc), error=True)


def parse_jsonl(
    data: bytes,
    *,
    text_max_bytes: int | None = None,
    max_records: int | None = None,
    max_errors: int | None = None,
    progress: Callable[[], None] | None = None,
) -> list[Record | dict[str, Any]]:
    records, errors = _budgets(max_records, max_errors)
    budget = _ParseBudget(records, errors)
    try:
        for number, raw in _iter_lines(_strip_bom(data)):
            if progress is not None and budget.seen and budget.seen % 64 == 0:
                progress()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                if not budget.add(
                    _problem(
                        number,
                        number,
                        _Invalid("record", m.ErrorCode.INPUT_INVALID, "Line is not valid UTF-8"),
                    ),
                    error=True,
                ):
                    break
                continue
            if _too_deep(text):
                if not budget.add(
                    _problem(
                        number,
                        number,
                        _Invalid("record", m.ErrorCode.LIMIT_REACHED, "Record nesting exceeds 32"),
                    ),
                    error=True,
                ):
                    break
                continue
            try:
                parsed = _loads(text)
            except (ValueError, RecursionError, MemoryError):
                if not budget.add(
                    _problem(
                        number,
                        number,
                        _Invalid("record", m.ErrorCode.INPUT_INVALID, "Line is not valid JSON"),
                    ),
                    error=True,
                ):
                    break
                continue
            before = budget.seen
            _accept_record(budget, parsed, row=number, line=number, text_max_bytes=text_max_bytes)
            if budget.stopped and budget.seen == before:
                break
    except MemoryError:
        budget.stopped = True
    return budget.finish()


def parse_json(
    data: bytes,
    *,
    text_max_bytes: int | None = None,
    max_records: int | None = None,
    max_errors: int | None = None,
    progress: Callable[[], None] | None = None,
) -> list[Record | dict[str, Any]]:
    records, errors = _budgets(max_records, max_errors)
    budget = _ParseBudget(records, errors)
    text = _decode(data)
    try:
        for value in _iter_json_values(text):
            if progress is not None and budget.seen and budget.seen % 64 == 0:
                progress()
            row = budget.seen + 1
            before = budget.seen
            _accept_record(budget, value, row=row, line=None, text_max_bytes=text_max_bytes)
            if budget.stopped and budget.seen == before:
                break
    except MemoryError:
        budget.stopped = True
    return budget.finish()


def _csv_media(row: dict[str, str | None]) -> list[dict[str, str]] | None:
    raw = row.get("media")
    parsed = None
    if raw:
        try:
            parsed = _media_list(_loads(raw))
        except _Invalid:
            raise
        except ValueError as exc:
            raise _Invalid(
                "media", m.ErrorCode.INPUT_INVALID, "media column is not valid JSON"
            ) from exc
    if parsed:
        return parsed
    ref = row.get("media_ref")
    if not ref:
        return None
    kind = row.get("media_kind")
    return _media_list([{"ref": ref, "kind": kind}])


def parse_csv(
    data: bytes,
    *,
    text_max_bytes: int | None = None,
    max_records: int | None = None,
    max_errors: int | None = None,
    progress: Callable[[], None] | None = None,
) -> list[Record | dict[str, Any]]:
    records, errors = _budgets(max_records, max_errors)
    budget = _ParseBudget(records, errors)
    text = _decode(data)
    previous = csv.field_size_limit()
    csv.field_size_limit(1_000_000)
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        header = list(reader.fieldnames or [])
        if not header:
            return []
        if len(header) != len(set(header)):
            raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "CSV header repeats a column")
        if "platform" not in header or "kind" not in header:
            raise _FileInvalid(
                m.ErrorCode.INPUT_INVALID, "CSV header must include platform and kind"
            )
        unknown = set(header) - CSV_FIELDS
        if unknown:
            raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "CSV header has an unknown column")
        index = 0
        pending = iter(reader)
        while True:
            if progress is not None and budget.seen and budget.seen % 64 == 0:
                progress()
            try:
                raw = next(pending)
            except StopIteration:
                break
            except csv.Error:
                budget.add(
                    _problem(
                        index + 1,
                        None,
                        _Invalid("record", m.ErrorCode.INPUT_INVALID, "CSV record is malformed"),
                    ),
                    error=True,
                )
                break
            except MemoryError:
                budget.stopped = True
                break
            index += 1
            if raw is None or all(value in (None, "") for value in raw.values()):
                continue
            obj: dict[str, Any] = {}
            for key, value in raw.items():
                if key in ("media", "media_ref", "media_kind"):
                    continue
                obj[key] = None if value in (None, "") else value
            try:
                media = _csv_media(
                    {
                        key: (None if raw.get(key) in ("", None) else raw.get(key))
                        for key in ("media", "media_ref", "media_kind")
                    }
                )
                if media is not None:
                    obj["media"] = media
                added = budget.add(
                    _record_from(obj, row=index, line=None, text_max_bytes=text_max_bytes),
                    error=False,
                )
            except _Invalid as exc:
                added = budget.add(_problem(index, None, exc), error=True)
            if not added:
                break
    finally:
        csv.field_size_limit(previous)
    return budget.finish()


def parse_text(data: bytes, *, text_max_bytes: int | None = None) -> list[Record]:
    limit = m.Limits().text_max_bytes if text_max_bytes is None else text_max_bytes
    text = _bound_text(_decode(data), limit)
    return [
        Record(
            row=1,
            line=1,
            platform="generic",
            kind="post",
            native_id=None,
            scope="",
            parent_native_id=None,
            parent_set=False,
            text=text,
            source_url=None,
            upstream="unknown",
            created_at_raw=None,
            parsed_time=None,
            author_display=None,
            media=None,
        )
    ]


def _selected(kind: str, resources: set[str]) -> bool:
    if kind == "post":
        return "post" in resources
    return kind in ("comment", "reply") and "comments" in resources


def _blank_row(input_index: int, row: int | None, line: int | None, status: str) -> dict[str, Any]:
    return {
        "input": input_index,
        "row": row,
        "line": line,
        "field": None,
        "code": None,
        "message": None,
        "status": status,
        "item_id": None,
        "revision": None,
        "segment_id": None,
        "asset_id": None,
        "source_id": None,
    }


def _live_roots(store: Any) -> list[str]:
    path = store.paths.root / "metamuse.toml"
    config = Config.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    return list(config.app.import_roots)


def _real_roots(values: list[str]) -> list[str]:
    ready: list[str] = []
    for value in values:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        ready.append(str(candidate.resolve()))
    return ready


def _marker_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads((path / _OWNER_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _claim_staging(store: Any, job_id: str) -> Path:
    directory = store.paths.root / "staging" / job_id
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / _OWNER_NAME
    payload = {"job_id": job_id, "operation": OPERATION}
    current = _marker_payload(directory)
    if current != payload:
        marker.write_text(json.dumps(payload), encoding="utf-8")
        marker.chmod(0o600)
    return directory


def _sweep(store: Any, current: str, *, remove_current: bool) -> None:
    """Delete only staging this operation marked for this job."""

    staging = store.paths.root / "staging"
    if not staging.is_dir() or staging.is_symlink():
        return
    root = staging.resolve()
    with store.connection() as conn:
        known = {
            row["id"]: (row["operation"], row["state"])
            for row in conn.execute("SELECT id, operation, state FROM jobs")
        }
    for child in staging.iterdir():
        if child.is_symlink() or not child.is_dir():
            continue
        try:
            child.resolve().relative_to(root)
        except ValueError:
            log.debug("staging left in place; directory escapes the staging root: %s", child.name)
            continue
        payload = _marker_payload(child)
        if payload is None:
            log.debug("staging left in place; no collect ownership marker: %s", child.name)
            continue
        if payload.get("job_id") != child.name or payload.get("operation") != OPERATION:
            log.debug("staging left in place; ownership marker does not match: %s", child.name)
            continue
        job = known.get(child.name)
        if job is None:
            log.debug("staging left in place; job is not in the database: %s", child.name)
            continue
        operation, state = job
        if operation != OPERATION:
            log.debug("staging left in place; job operation is not import.local: %s", child.name)
            continue
        drop = (remove_current and child.name == current) or state in jobs.TERMINAL
        if drop:
            shutil.rmtree(child)


@dataclass
class _Run:
    step: StepContext
    request: m.CollectRequest
    roots: list[str]
    media_max: int
    read_timeout: int
    book: _Book
    rows: list[dict[str, Any]]
    inputs: list[dict[str, Any]]
    missing: set[str]
    accepted: int
    halt: str | None
    bytes_used: int
    text_max: int = 20_971_520
    method: str = ""
    sha: str = ""
    writes: list[Callable[[TransactionWriter], None]] = field(default_factory=list)
    asset_ids: dict[str, str] = field(default_factory=dict)

    def checkpoint(self, input_index: int, next_record: int) -> None:
        payload = {
            "input_index": input_index,
            "next_record": next_record,
            "halt": self.halt,
            "rows": self.rows,
            "inputs": self.inputs,
            "missing_fields": sorted(self.missing),
            "accepted": self.accepted,
            "bytes_used": self.bytes_used,
        }

        def write(writer: TransactionWriter) -> None:
            self.book.flush(writer, self.step.store)
            for extra in self.writes:
                extra(writer)
            self.writes.clear()
            writer.insert(
                m.Event(
                    id=m.new_id("evt"),
                    object_id=self.step.job.id,
                    job_id=self.step.job.id,
                    project_id=self.step.job.project_id,
                    kind="collect.batch",
                    detail={"input": input_index, "next_record": next_record},
                    created_at=self.step.store.clock(),
                )
            )

        self.step.commit_checkpoint(payload, write_batch=write)
        _note_batch(self.step)

    def take(self, record: Record | dict[str, Any], resources: set[str]) -> dict[str, Any] | None:
        if isinstance(record, dict):
            return dict(record)
        if not _selected(record.kind, resources):
            return _blank_row(0, record.row, record.line, "skipped")
        if self.accepted >= self.request.limits.max_items:
            self.halt = "limit_reached"
            return None
        try:
            item, status = self.book.apply(
                record, method=self.method, sha=self.sha, evidence_media=None
            )
        except _Invalid as exc:
            return _problem(record.row, record.line, exc)
        self.accepted += 1
        for name in item.missing_fields:
            self.missing.add(name)
        row = _blank_row(0, record.row, record.line, status)
        row["item_id"] = item.item_id
        row["revision"] = item.revision
        row["source_id"] = item.source_id
        return row


def run_import(step: StepContext) -> m.JobResult:
    _sweep(step.store, step.job.id, remove_current=True)
    try:
        return _execute(step)
    finally:
        _sweep(step.store, step.job.id, remove_current=True)


def _execute(step: StepContext) -> m.JobResult:
    request = m.CollectRequest.model_validate(step.payload)
    saved = dict(step.checkpoint or {})
    if saved.get("halt"):
        return _result(step, request, saved)
    accepted = _real_roots([str(value) for value in step.target.get("import_roots", [])])
    live = _real_roots(_live_roots(step.store))
    roots = [root for root in accepted if root in set(live)]
    with step.store.connection() as conn:
        book = _Book(request.project_id)
        book.load(conn)
    runner = _Run(
        step=step,
        request=request,
        roots=roots,
        media_max=int(step.target.get("media_max_bytes", step.job.limits.get("max_bytes", 1))),
        read_timeout=int(step.target.get("read_timeout_seconds", 30)),
        book=book,
        rows=list(saved.get("rows", [])),
        inputs=list(saved.get("inputs", [])),
        missing=set(saved.get("missing_fields", [])),
        accepted=int(saved.get("accepted", 0)),
        halt=saved.get("halt"),
        bytes_used=(
            int(saved["bytes_used"])
            if "bytes_used" in saved
            else sum(int(item.byte_size or 0) for item in request.inputs if item.kind != "url")
        ),
        text_max=int(step.target.get("text_max_bytes", m.Limits().text_max_bytes)),
    )
    resources = set(request.resources)
    start_input = int(saved.get("input_index", 0))
    start_record = int(saved.get("next_record", 0))
    staging = _claim_staging(step.store, step.job.id)
    for index, item in enumerate(request.inputs):
        if runner.halt:
            break
        if index < start_input:
            continue
        resume = start_record if index == start_input else 0
        if item.kind == "text":
            _import_text(runner, index, item, resources, resume)
        elif item.format in ("vtt", "srt"):
            _import_subtitles(runner, index, item, staging, resume)
        elif item.format == "media":
            _import_media_file(runner, index, item, staging)
        else:
            _import_records(runner, index, item, resources, staging, resume)
        start_record = 0
        if runner.halt:
            break
    payload = {
        "input_index": len(request.inputs),
        "next_record": 0,
        "halt": runner.halt,
        "rows": runner.rows,
        "inputs": runner.inputs,
        "missing_fields": sorted(runner.missing),
        "accepted": runner.accepted,
        "bytes_used": runner.bytes_used,
    }
    return _result(step, request, payload)


def _result(step: StepContext, request: m.CollectRequest, saved: dict[str, Any]) -> m.JobResult:
    rows = list(saved.get("rows", []))
    inputs = [
        {key: value for key, value in item.items() if key != "parser"}
        for item in saved.get("inputs", [])
    ]
    rejected = any(row.get("status") == "rejected" or row.get("code") for row in rows) or bool(
        inputs
    )
    accepted = sum(row.get("status") in ("committed", "unchanged") for row in rows)
    skipped = any(row.get("status") == "skipped" for row in rows)
    if saved.get("halt") == "limit_reached":
        stop, coverage = "limit_reached", ("partial" if rejected else "bounded")
    elif any(item.get("code") == m.ErrorCode.DATA_STALE.value for item in inputs):
        stop, coverage = "data_stale", "partial"
    elif any(item.get("parser") for item in saved.get("inputs", [])):
        stop, coverage = "parser_failed", "partial"
    elif rejected:
        stop, coverage = "row_errors", "partial"
    elif accepted == 0 and not skipped:
        stop, coverage = "empty_input", "bounded"
    else:
        stop, coverage = "input_exhausted", "scope_satisfied"
    observed = {
        "inputs": len(request.inputs),
        "committed": sum(row.get("status") == "committed" for row in rows),
        "unchanged": sum(row.get("status") == "unchanged" for row in rows),
        "rejected": sum(row.get("status") == "rejected" for row in rows),
        "skipped": sum(row.get("status") == "skipped" for row in rows),
        "cues": sum(row.get("segment_id") is not None for row in rows),
    }
    return m.JobResult(
        coverage_status=coverage,  # type: ignore[arg-type]
        requested_scope=step.job.requested_scope,
        observed_scope=observed,
        stop_reason=stop,
        missing_fields=list(saved.get("missing_fields", [])),
        data={"rows": rows, "inputs": inputs},
    )


def _stage_input(runner: _Run, index: int, item: m.LocalFileInput, staging: Path) -> Path | None:
    dest = staging / f"input-{index}"
    try:
        if not runner.roots:
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Input path is outside the configured import roots",
                next_action="Restore the import root that was accepted for this job",
            )
        digest = hashlib.sha256()
        size = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        with safety.open_input_file(
            item.path, max_bytes=item.byte_size or 0, roots=runner.roots
        ) as src:
            with dest.open("wb") as out:
                while chunk := src.read(1024 * 1024):
                    size += len(chunk)
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
        if digest.hexdigest() != item.sha256 or size != item.byte_size:
            dest.unlink(missing_ok=True)
            raise m.CoreError(
                m.ErrorCode.DATA_STALE, "Input bytes no longer match the accepted file"
            )
        return dest
    except m.CoreError as exc:
        code = exc.detail.code
        if code in (
            m.ErrorCode.LIMIT_REACHED,
            m.ErrorCode.INPUT_INVALID,
            m.ErrorCode.ACCESS_DENIED,
        ):
            code = m.ErrorCode.DATA_STALE
            message = "Input bytes no longer match the accepted file"
        else:
            message = exc.detail.message
        runner.inputs.append(
            {"input": index, "code": code.value, "message": message, "field": None, "parser": False}
        )
        runner.checkpoint(index + 1, 0)
        return None


def _import_records(
    runner: _Run,
    index: int,
    item: m.LocalFileInput,
    resources: set[str],
    staging: Path,
    resume: int,
) -> None:
    staged = _stage_input(runner, index, item, staging)
    if staged is None:
        return
    try:
        data = staged.read_bytes()
        budget = runner.request.limits.max_items + LOOKAHEAD
        progress = runner.step.guard
        if item.format == "jsonl":
            parsed = parse_jsonl(
                data, text_max_bytes=runner.text_max, max_records=budget, progress=progress
            )
        elif item.format == "csv":
            parsed = parse_csv(
                data, text_max_bytes=runner.text_max, max_records=budget, progress=progress
            )
        elif item.format == "text":
            parsed = list(parse_text(data, text_max_bytes=runner.text_max))
        else:
            parsed = parse_json(
                data, text_max_bytes=runner.text_max, max_records=budget, progress=progress
            )
    except _FileInvalid as exc:
        runner.inputs.append(
            {
                "input": index,
                "code": exc.code.value,
                "message": exc.message,
                "field": None,
                "parser": True,
            }
        )
        runner.checkpoint(index + 1, 0)
        return
    runner.method = "file_import"
    runner.sha = item.sha256 or ""
    _consume_records(runner, index, parsed, resources, resume, Path(item.path))


def _import_text(
    runner: _Run, index: int, item: m.TextInput, resources: set[str], resume: int
) -> None:
    raw = item.text.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != item.sha256 or len(raw) != item.byte_size:
        runner.inputs.append(
            {
                "input": index,
                "code": m.ErrorCode.DATA_STALE.value,
                "message": "Input bytes no longer match the accepted text",
                "field": None,
                "parser": False,
            }
        )
        runner.checkpoint(index + 1, 0)
        return
    try:
        parsed = list(parse_text(raw, text_max_bytes=runner.text_max))
    except _FileInvalid as exc:
        runner.inputs.append(
            {
                "input": index,
                "code": exc.code.value,
                "message": exc.message,
                "field": None,
                "parser": True,
            }
        )
        runner.checkpoint(index + 1, 0)
        return
    runner.method = "text_input"
    runner.sha = digest
    _consume_records(runner, index, parsed, resources, resume, None)


def _acceptable(book: _Book, record: Record | dict[str, Any], resources: set[str]) -> bool:
    return (
        isinstance(record, Record)
        and _selected(record.kind, resources)
        and book.preview(record) is None
    )


def _diagnose(book: _Book, record: Record | dict[str, Any], resources: set[str]) -> dict[str, Any]:
    if isinstance(record, dict):
        return dict(record)
    if not _selected(record.kind, resources):
        return _blank_row(0, record.row, record.line, "skipped")
    reason = book.preview(record)
    if reason is None:
        return _blank_row(0, record.row, record.line, "skipped")
    return _problem(record.row, record.line, reason)


def _consume_records(
    runner: _Run,
    index: int,
    parsed: list[Record | dict[str, Any]],
    resources: set[str],
    resume: int,
    import_path: Path | None,
) -> None:
    cursor = resume
    batch: list[dict[str, Any]] = []
    limit = runner.request.limits.max_items
    while cursor < len(parsed):
        if runner.accepted >= limit:
            rest = parsed[cursor:]
            if any(_acceptable(runner.book, record, resources) for record in rest):
                runner.halt = "limit_reached"
                break
            for record in rest:
                taken = _diagnose(runner.book, record, resources)
                taken["input"] = index
                batch.append(taken)
            cursor = len(parsed)
            break
        if runner.accepted and runner.accepted % 64 == 0:
            runner.step.guard()
        taken = runner.take(parsed[cursor], resources)
        if taken is None:
            break
        cursor += 1
        taken["input"] = index
        if isinstance(parsed[cursor - 1], Record) and taken["status"] in ("committed", "unchanged"):
            _attach_media(runner, parsed[cursor - 1], taken, import_path, index)
        batch.append(taken)
        if len(batch) >= BATCH_SIZE:
            runner.rows.extend(batch)
            runner.checkpoint(index, cursor)
            batch = []
    if batch:
        runner.rows.extend(batch)
    if runner.halt:
        runner.checkpoint(index, cursor)
    else:
        runner.checkpoint(index + 1, 0)


def _attach_media(
    runner: _Run, record: Record, row: dict[str, Any], import_path: Path | None, index: int
) -> None:
    if not record.media or import_path is None:
        return
    requested = set(runner.request.resources)
    if "media_metadata" not in requested and "media_file" not in requested:
        notes = [{"ref": spec["ref"], "kind": spec["kind"]} for spec in record.media]
        for item in runner.book.pending:
            if item.item_id == row["item_id"] and item.revision_new:
                item.evidence = {**item.evidence, "media": notes}
        return
    notes: list[dict[str, Any]] = []
    failed = False
    code = None
    message = None
    for media_index, spec in enumerate(record.media):
        try:
            info, asset_later = _read_media_ref(
                runner, import_path, spec, index, record.row, media_index
            )
            notes.append({"ref": spec["ref"], "kind": spec["kind"], "mime": info.get("mime")})
            if asset_later is not None:
                _queue_item_asset(runner, row, asset_later, spec["kind"])
        except m.CoreError as exc:
            failed = True
            code = exc.detail.code.value
            message = exc.detail.message
    if notes:
        for item in runner.book.pending:
            if item.item_id == row["item_id"] and item.revision_new:
                item.evidence = {**item.evidence, "media": notes}
    if failed:
        row["field"] = "media"
        row["code"] = code
        row["message"] = message
        for item in runner.book.by_id.values():
            if item.item_id == row["item_id"] and "media" not in item.missing_fields:
                item.missing_fields.append("media")
                item.complete = False
                if item.revision_new:
                    item.evidence = dict(item.evidence)
        runner.missing.add("media")


def _read_media_ref(
    runner: _Run,
    import_path: Path,
    spec: dict[str, str],
    index: int,
    row: int,
    media_index: int,
) -> tuple[dict[str, Any], media_mod.MediaInfo | None]:
    ref = spec["ref"]
    candidate = Path(ref)
    if not candidate.is_absolute():
        candidate = import_path.parent / candidate
    publish = "media_file" in runner.request.resources
    if not publish:
        with safety.open_input_file(
            str(candidate), max_bytes=runner.media_max, roots=runner.roots
        ) as handle:
            header = handle.read(64)
        runner.bytes_used += len(header)
        if runner.bytes_used > runner.request.limits.max_bytes:
            raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Job byte limit reached")
        mime = media_mod.sniff_mime(header)
        if mime in (None, "text/html"):
            raise m.CoreError(
                m.ErrorCode.UNSUPPORTED if mime == "text/html" else m.ErrorCode.INPUT_INVALID,
                "Referenced media is not a recognized file",
            )
        return {"mime": mime}, None
    dest = (
        runner.step.store.paths.root
        / "staging"
        / runner.step.job.id
        / f"media-{index}-{row}-{media_index}"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    added = False
    try:
        with safety.open_input_file(
            str(candidate), max_bytes=runner.media_max, roots=runner.roots
        ) as src:
            with dest.open("wb") as out:
                while chunk := src.read(1024 * 1024):
                    size += len(chunk)
                    if runner.bytes_used + size > runner.request.limits.max_bytes:
                        runner.bytes_used += size
                        added = True
                        raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Job byte limit reached")
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
        runner.bytes_used += size
        added = True
    except m.CoreError:
        if not added:
            runner.bytes_used += size
        raise
    info = media_mod.analyze_file(
        dest, max_bytes=runner.media_max, read_timeout_seconds=runner.read_timeout
    )
    media_mod.place_object(runner.step.store, dest, info)
    media_mod.require_compatible_mime(runner.step.store, info)
    return {"mime": info.mime}, info


def _allocate_asset_id(runner: _Run, sha: str) -> str:
    known = runner.asset_ids.get(sha)
    if known:
        return known
    with runner.step.store.connection() as conn:
        found = conn.execute("SELECT id FROM assets WHERE sha256=?", (sha,)).fetchone()
    asset_id = str(found["id"]) if found is not None else m.new_id("ast")
    runner.asset_ids[sha] = asset_id
    return asset_id


def _queue_item_asset(
    runner: _Run, row: dict[str, Any], info: media_mod.MediaInfo, relation: str
) -> None:
    asset_id = _allocate_asset_id(runner, info.sha256)
    row["asset_id"] = asset_id
    item_id = row["item_id"]
    revision = row["revision"]

    def write(writer: TransactionWriter) -> None:
        asset = media_mod.ensure_asset(runner.step.store, writer, info, asset_id=asset_id)
        existing = writer.execute(
            "SELECT 1 FROM item_assets WHERE item_id=? AND revision=? AND asset_id=? "
            "AND relation=?",
            (item_id, revision, asset.id, relation),
        ).fetchone()
        if existing is None:
            writer.insert(
                m.ItemAsset(
                    item_id=item_id, revision=revision, asset_id=asset.id, relation=relation
                )
            )

    runner.writes.append(write)


def _import_subtitles(
    runner: _Run, index: int, item: m.LocalFileInput, staging: Path, resume: int
) -> None:
    staged = _stage_input(runner, index, item, staging)
    if staged is None:
        return
    try:
        parsed = media_mod.parse_subtitles(_decode(staged.read_bytes()), item.format)
    except _FileInvalid as exc:
        runner.inputs.append(
            {
                "input": index,
                "code": exc.code.value,
                "message": exc.message,
                "field": None,
                "parser": True,
            }
        )
        runner.checkpoint(index + 1, 0)
        return
    except m.CoreError as exc:
        runner.inputs.append(
            {
                "input": index,
                "code": exc.detail.code.value,
                "message": exc.detail.message,
                "field": None,
                "parser": True,
            }
        )
        runner.checkpoint(index + 1, 0)
        return
    language = runner.request.language or parsed.language or "und"
    if len(language) > 35:
        language = "und"
    try:
        anchor = _subtitle_anchor(runner)
    except m.CoreError as exc:
        runner.inputs.append(
            {
                "input": index,
                "code": exc.detail.code.value,
                "message": exc.detail.message,
                "field": "attach",
                "parser": False,
            }
        )
        runner.checkpoint(index + 1, 0)
        return
    sha = item.sha256 or ""
    found = _find_subtitle_source(runner, sha, language, anchor)
    if found is not None and found[1] and resume == 0:
        source_id = found[0]
        for cue in parsed.cues:
            row = _blank_row(index, cue.row, None, "unchanged")
            row["source_id"] = source_id
            runner.rows.append(row)
        for error in parsed.errors:
            runner.rows.append(
                {
                    **_blank_row(index, error.row, None, "rejected"),
                    "field": error.field,
                    "code": error.code,
                    "message": error.message,
                }
            )
        runner.checkpoint(index + 1, 0)
        return
    if found is None:
        source_id = m.new_id("src")
        _queue_subtitle_source(runner, source_id, sha, language, anchor)
        have: set[int] = set()
    else:
        source_id = found[0]
        have = _stored_cue_indexes(runner, source_id)
    cursor = resume
    batch = 0
    stored_all = True
    while cursor < len(parsed.cues):
        if runner.accepted >= runner.request.limits.max_items:
            runner.halt = "limit_reached"
            break
        if cursor and cursor % 64 == 0:
            runner.step.guard()
        cue = parsed.cues[cursor]
        if cue.cue_index in have:
            row = _blank_row(index, cue.row, None, "unchanged")
            row["source_id"] = source_id
            runner.rows.append(row)
            cursor += 1
            continue
        try:
            if len(cue.text) > FIELD_LIMITS["text"]:
                raise _Invalid(
                    "text",
                    m.ErrorCode.LIMIT_REACHED,
                    f"text exceeds {FIELD_LIMITS['text']} characters",
                )
            _bound_text(cue.text, runner.text_max)
        except _Invalid as exc:
            stored_all = False
            runner.rows.append(
                {
                    **_blank_row(index, cue.row, None, "rejected"),
                    "field": exc.field,
                    "code": exc.code.value,
                    "message": exc.message,
                }
            )
            cursor += 1
            continue
        segment_id = m.new_id("tsg")
        _queue_cue(runner, source_id, anchor, cue, segment_id, language)
        row = _blank_row(index, cue.row, None, "committed")
        row["segment_id"] = segment_id
        row["source_id"] = source_id
        row["item_id"] = anchor[0]
        row["revision"] = 1
        if cue.overlap:
            row["message"] = "Cue overlaps another cue"
        runner.rows.append(row)
        runner.accepted += 1
        cursor += 1
        batch += 1
        if batch >= BATCH_SIZE:
            runner.checkpoint(index, cursor)
            batch = 0
    if not runner.halt:
        for error in parsed.errors:
            runner.rows.append(
                {
                    **_blank_row(index, error.row, None, "rejected"),
                    "field": error.field,
                    "code": error.code,
                    "message": error.message,
                }
            )
        if stored_all:
            _queue_subtitle_complete(runner, source_id, sha, language, anchor, len(parsed.cues))
        runner.checkpoint(index + 1, 0)
    else:
        runner.checkpoint(index, cursor)


def _subtitle_anchor(runner: _Run) -> tuple[str | None, str | None]:
    item_id = runner.request.attach_item_id
    asset_id = runner.request.attach_asset_id
    with runner.step.store.connection() as conn:
        if item_id is not None:
            item = runner.step.store.get(m.Item, item_id, conn=conn)
            if item.project_id != runner.request.project_id:
                raise m.CoreError(
                    m.ErrorCode.REFERENCE_INVALID, "Attached item is outside the project"
                )
        if asset_id is not None:
            runner.step.store.require_project_asset(runner.request.project_id, asset_id, conn=conn)
    return item_id, asset_id


def _subtitle_basis(
    sha: str,
    language: str,
    anchor: tuple[str | None, str | None],
    *,
    complete: bool,
    cue_count: int,
) -> dict[str, Any]:
    return {
        "file_sha256": sha,
        "language": language,
        "role": "subtitles",
        "item_id": anchor[0],
        "asset_id": anchor[1],
        "complete": 1 if complete else 0,
        "cue_count": cue_count,
    }


def _find_subtitle_source(
    runner: _Run, sha: str, language: str, anchor: tuple[str | None, str | None]
) -> tuple[str, bool] | None:
    with runner.step.store.connection() as conn:
        row = conn.execute(
            "SELECT id, json_extract(use_basis, '$.complete') AS complete FROM sources "
            "WHERE project_id=? AND json_extract(use_basis, '$.role')='subtitles' "
            "AND json_extract(use_basis, '$.file_sha256')=? "
            "AND json_extract(use_basis, '$.language')=? "
            "AND json_extract(use_basis, '$.item_id') IS ? "
            "AND json_extract(use_basis, '$.asset_id') IS ?",
            (runner.request.project_id, sha, language, anchor[0], anchor[1]),
        ).fetchone()
    if row is None:
        return None
    return str(row["id"]), bool(row["complete"])


def _queue_subtitle_source(
    runner: _Run,
    source_id: str,
    sha: str,
    language: str,
    anchor: tuple[str | None, str | None],
) -> None:
    basis = _subtitle_basis(sha, language, anchor, complete=False, cue_count=0)

    def write(writer: TransactionWriter) -> None:
        writer.insert(
            m.Source(
                id=source_id,
                project_id=runner.request.project_id,
                platform="generic",
                collection_method="file_import",
                upstream_collection_method="unknown",
                fetched_at=runner.step.store.clock(),
                use_basis=basis,
            )
        )

    runner.writes.append(write)


def _queue_subtitle_complete(
    runner: _Run,
    source_id: str,
    sha: str,
    language: str,
    anchor: tuple[str | None, str | None],
    cue_count: int,
) -> None:
    basis = _subtitle_basis(sha, language, anchor, complete=True, cue_count=cue_count)
    encoded = m.canonical_json(basis).decode()

    def write(writer: TransactionWriter) -> None:
        writer.execute("UPDATE sources SET use_basis=? WHERE id=?", (encoded, source_id))

    runner.writes.append(write)


def _stored_cue_indexes(runner: _Run, source_id: str) -> set[int]:
    with runner.step.store.connection() as conn:
        rows = conn.execute(
            "SELECT cue_index FROM transcript_segments "
            "WHERE source_id=? AND project_id=? AND revision=1",
            (source_id, runner.request.project_id),
        ).fetchall()
    return {int(row["cue_index"]) for row in rows}


def _queue_cue(
    runner: _Run,
    source_id: str,
    anchor: tuple[str | None, str | None],
    cue: media_mod.Cue,
    segment_id: str,
    language: str,
) -> None:
    def write(writer: TransactionWriter) -> None:
        writer.insert(
            m.TranscriptSegment(
                id=segment_id,
                project_id=runner.request.project_id,
                asset_id=anchor[1],
                item_id=anchor[0],
                source_id=source_id,
                revision=1,
                cue_index=cue.cue_index,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                text=cue.text,
                language=language,
                method="provided",
                review="unreviewed",
                overlap=cue.overlap,
            )
        )

    runner.writes.append(write)


def _import_media_file(runner: _Run, index: int, item: m.LocalFileInput, staging: Path) -> None:
    if runner.accepted >= runner.request.limits.max_items:
        runner.halt = "limit_reached"
        runner.checkpoint(index, 0)
        return
    staged = _stage_input(runner, index, item, staging)
    if staged is None:
        return
    publish = "media_file" in runner.request.resources
    try:
        if runner.request.attach_item_id or runner.request.attach_asset_id:
            _subtitle_anchor(runner)
        if publish:
            info = media_mod.analyze_file(
                staged, max_bytes=runner.media_max, read_timeout_seconds=runner.read_timeout
            )
            media_mod.place_object(runner.step.store, staged, info)
            media_mod.require_compatible_mime(runner.step.store, info)
            source_id = m.new_id("src")
            asset_id = _allocate_asset_id(runner, info.sha256)
            _queue_direct_media(runner, source_id, info, publish=True, asset_id=asset_id)
            asset_info: media_mod.MediaInfo | None = info
        else:
            header = staged.read_bytes()[:64]
            mime = media_mod.sniff_mime(header)
            if not staged.stat().st_size:
                raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Media file is empty")
            if mime == "text/html":
                raise m.CoreError(m.ErrorCode.UNSUPPORTED, "Media file is an HTML page")
            if mime is None:
                raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Media type is not recognized")
            source_id = m.new_id("src")
            asset_info = None
            _queue_direct_media(
                runner,
                source_id,
                media_mod.MediaInfo(item.sha256 or "", mime, item.byte_size or 0, None, "metadata"),
                publish=False,
                asset_id=None,
            )
            asset_id = None
    except m.CoreError as exc:
        runner.inputs.append(
            {
                "input": index,
                "code": exc.detail.code.value,
                "message": exc.detail.message,
                "field": None,
                "parser": exc.detail.code in (m.ErrorCode.INPUT_INVALID, m.ErrorCode.UNSUPPORTED),
            }
        )
        runner.checkpoint(index + 1, 0)
        return
    row = _blank_row(index, 1, None, "committed")
    row["source_id"] = source_id
    row["item_id"] = runner.request.attach_item_id
    if asset_info is not None:
        row["asset_id"] = asset_id
    runner.rows.append(row)
    runner.accepted += 1
    runner.checkpoint(index + 1, 0)


def _queue_direct_media(
    runner: _Run,
    source_id: str,
    info: media_mod.MediaInfo,
    *,
    publish: bool,
    asset_id: str | None,
) -> None:
    project_id = runner.request.project_id
    attach_item = runner.request.attach_item_id

    def write(writer: TransactionWriter) -> None:
        writer.insert(
            m.Source(
                id=source_id,
                project_id=project_id,
                platform="generic",
                collection_method="file_import",
                upstream_collection_method="unknown",
                fetched_at=runner.step.store.clock(),
                use_basis={
                    "file_sha256": info.sha256,
                    "mime": info.mime,
                    "byte_size": info.byte_size,
                    "probe": info.probe,
                    "duration_ms": info.duration_ms,
                    "role": "media",
                },
            )
        )
        if not publish or asset_id is None:
            return
        asset = media_mod.ensure_asset(runner.step.store, writer, info, asset_id=asset_id)
        writer.insert(
            m.SourceAsset(
                source_id=source_id,
                project_id=project_id,
                asset_id=asset.id,
                relation="media",
            )
        )
        if attach_item is None:
            return
        revision = writer.get(m.ItemRevision, attach_item)
        existing = writer.execute(
            "SELECT 1 FROM item_assets WHERE item_id=? AND revision=? AND asset_id=? "
            "AND relation=?",
            (attach_item, revision.revision, asset.id, "media"),
        ).fetchone()
        if existing is None:
            writer.insert(
                m.ItemAsset(
                    item_id=attach_item,
                    revision=revision.revision,
                    asset_id=asset.id,
                    relation="media",
                )
            )

    runner.writes.append(write)


def _measure(path: str, *, max_bytes: int, roots: list[str]) -> tuple[str, str, int]:
    digest = hashlib.sha256()
    size = 0
    with safety.open_input_file(path, max_bytes=max_bytes, roots=roots) as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
        resolved = str(handle.path)
    return resolved, digest.hexdigest(), size


def _check_request(ctx: Any, request: m.CollectRequest) -> None:
    if any(item.kind == "url" for item in request.inputs):
        raise m.CoreError(
            m.ErrorCode.UNSUPPORTED,
            "Remote collection is not implemented in this build",
            next_action=REMOTE_NEXT,
        )
    ctx.store.get(m.Project, request.project_id)
    for name in m.CollectLimits.model_fields:
        if getattr(request.limits, name) > getattr(ctx.limits, name):
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Request limit exceeds the workspace limit",
                next_action="Lower the request limit or raise it in the workspace configuration",
            )
    if len(request.inputs) > request.limits.max_urls:
        raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Input count exceeds max_urls")
    requested = set(request.resources)
    formats = [item.format for item in request.inputs if item.kind == "local_file"]
    if any(item.kind == "text" for item in request.inputs):
        formats.append("text")
    for fmt in formats:
        if not APPLICABLE[fmt] & requested:
            raise m.CoreError(
                m.ErrorCode.INPUT_INVALID,
                "Input format does not provide the requested resources",
            )
    subtitle = any(fmt in ("vtt", "srt") for fmt in formats)
    media = any(fmt == "media" for fmt in formats)
    if request.language is not None and not subtitle:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "language applies to a subtitle input")
    if request.attach_asset_id is not None and not subtitle:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "asset attachment applies to a subtitle input")
    if request.attach_item_id is not None and not (subtitle or media):
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID, "item attachment applies to a subtitle or media input"
        )


def submit_collect(ctx: Any, request: m.CollectRequest) -> m.Envelope:
    from metamuse import facebook

    register_operations()
    if _remote_media_request(request):
        from metamuse import remote_media

        return remote_media.submit_remote_media(ctx, request)
    if facebook.is_facebook_post_request(request):
        return facebook.submit_facebook_collect(ctx, request)
    _check_request(ctx, request)
    roots = list(ctx.policy.app.import_roots)
    if any(item.kind == "local_file" for item in request.inputs) and not roots:
        raise m.CoreError(
            m.ErrorCode.SCOPE_REQUIRED,
            "No import root is configured",
            next_action=(
                "Set app.import_roots in the workspace metamuse.toml "
                "to the directories local imports may read"
            ),
        )
    payload = request.model_dump(mode="json")
    remaining = request.limits.max_bytes
    for index, item in enumerate(request.inputs):
        if item.kind == "text":
            raw = item.text.encode("utf-8")
            size = len(raw)
            if size > ctx.limits.text_max_bytes or size > remaining:
                raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Input exceeds the byte limit")
            payload["inputs"][index]["sha256"] = hashlib.sha256(raw).hexdigest()
            payload["inputs"][index]["byte_size"] = size
        else:
            cap = (
                ctx.limits.media_max_bytes if item.format == "media" else ctx.limits.text_max_bytes
            )
            resolved, sha, size = _measure(item.path, max_bytes=min(cap, remaining), roots=roots)
            if size > remaining:
                raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Input exceeds the job byte limit")
            payload["inputs"][index]["path"] = resolved
            payload["inputs"][index]["sha256"] = sha
            payload["inputs"][index]["byte_size"] = size
        remaining -= size
    return submit_job(
        ctx.store,
        OPERATION,
        request.request_key,
        payload,
        project_id=request.project_id,
        steps=[
            StepSpec(
                target={
                    "import_roots": roots,
                    "text_max_bytes": ctx.limits.text_max_bytes,
                    "media_max_bytes": ctx.limits.media_max_bytes,
                    "read_timeout_seconds": ctx.limits.read_timeout_seconds,
                }
            )
        ],
        requested_scope={"resources": list(request.resources), "input_count": len(request.inputs)},
        limits=request.limits.model_dump(mode="json"),
    )
