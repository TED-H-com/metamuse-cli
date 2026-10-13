"""Local collection: files and text become project items, media, and subtitles.

Accepted file bytes are pinned by size and SHA-256. A later run that sees different
bytes records E_DATA_STALE for that input and still commits the other inputs.
Remote URL collection stays unsupported until B12/B13.

The same reliable id twice in one batch is applied in order. Identical text writes
nothing new; different text appends a revision. A disagreeing kind, a self-parent,
or a cycle rejects that row and leaves the earlier row in place.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import shutil
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from metamuse import jobs, safety
from metamuse import media as media_mod
from metamuse import models as m
from metamuse.app import Config
from metamuse.jobs import StepContext, StepSpec, submit_job

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


def capability_status(ctx: Any) -> dict[str, dict[str, str]]:
    """Local import is fixture-tested. ffprobe is optional and does not change this."""

    del ctx
    return {
        "metamuse_collect": {
            "verification": "fixture_passed",
            "environment": "ready",
            "permission": "granted_scope",
        }
    }


def register_operations(registry: dict[jobs.Operation] | None = None) -> None:
    target = jobs.OPERATIONS if registry is None else registry
    if OPERATION in target:
        return
    jobs.register_operation(
        OPERATION,
        run_import,
        "none",
        request_model=m.CollectRequest,
        registry=target,
    )


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
    text: str
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
    upstream_dirty: bool = False
    complete: bool = False


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
        stored = "" if record.text is None else record.text
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
            if item.upstream == "unknown" and record.upstream != "unknown":
                item.upstream = record.upstream
                item.upstream_dirty = True
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

    def flush(self, conn: Any, store: Any) -> None:
        now = store.clock()
        for item in self.pending:
            if not item.source_new:
                continue
            store.insert(
                m.Source(
                    id=item.source_id,
                    project_id=self.project_id,
                    platform=item.platform,
                    native_scope="" if item.native_id else None,
                    native_id=item.native_id,
                    original_url=item.original_url,
                    collection_method=item.collection_method,
                    upstream_collection_method=item.upstream,
                    fetched_at=now,
                    use_basis={"input_sha256": item.evidence.get("input_sha256")},
                ),
                conn=conn,
            )
        fresh = [item for item in self.pending if item.item_new]
        ready = set(self.by_id) - {item.item_id for item in fresh}
        for item in _parents_first(fresh, ready):
            store.insert(
                m.Item(
                    id=item.item_id,
                    project_id=self.project_id,
                    source_id=item.source_id,
                    kind=item.kind,  # type: ignore[arg-type]
                    native_id=item.native_id,
                    parent_id=item.parent_id,
                    unresolved_parent=item.unresolved_parent,
                    identity_confidence=item.confidence,  # type: ignore[arg-type]
                ),
                conn=conn,
            )
        for item in self.pending:
            if item.item_new:
                continue
            if item.parent_dirty:
                conn.execute(
                    "UPDATE items SET parent_id=?, unresolved_parent=? WHERE id=?",
                    (item.parent_id, item.unresolved_parent, item.item_id),
                )
            if item.upstream_dirty:
                conn.execute(
                    "UPDATE sources SET upstream_collection_method=? WHERE id=?",
                    (item.upstream, item.source_id),
                )
        for item in self.pending:
            if not item.revision_new:
                continue
            store.insert(
                m.ItemRevision(
                    item_id=item.item_id,
                    revision=item.revision,
                    text=item.text,
                    text_hash=item.text_hash or m.payload_digest({"text": None}),
                    raw_time=item.raw_time,
                    parsed_time=item.parsed_time,
                    source_time=item.parsed_time,
                    fetched_at=now,
                    complete=item.complete,
                    missing_fields=list(item.missing_fields),
                    evidence=item.evidence,
                ),
                conn=conn,
            )
        for item in self.pending:
            item.source_new = False
            item.item_new = False
            item.revision_new = False
            item.parent_dirty = False
            item.upstream_dirty = False
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


def _optional_str(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Invalid(name, m.ErrorCode.INPUT_INVALID, f"{name} must be a string")
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


def _record_from(obj: Any, *, row: int, line: int | None) -> Record:
    if not isinstance(obj, dict):
        raise _Invalid("record", m.ErrorCode.INPUT_INVALID, "Record must be a JSON object")
    unknown = set(obj) - RECORD_FIELDS
    if unknown:
        name = sorted(unknown)[0]
        raise _Invalid(name, m.ErrorCode.INPUT_INVALID, f"Unknown record field {name}")
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
    text = _optional_str(obj.get("text"), "text") if "text" in obj else None
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


def parse_jsonl(data: bytes) -> list[Record | dict[str, Any]]:
    body = _strip_bom(data)
    rows: list[Record | dict[str, Any]] = []
    lines = body.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    for number, raw in enumerate(lines, start=1):
        if raw.endswith(b"\r"):
            raw = raw[:-1]
        if raw.strip() == b"":
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            rows.append(
                _problem(
                    number,
                    number,
                    _Invalid("record", m.ErrorCode.INPUT_INVALID, "Line is not valid UTF-8"),
                )
            )
            continue
        try:
            parsed = _loads(text)
            rows.append(_record_from(parsed, row=number, line=number))
        except _Invalid as exc:
            rows.append(_problem(number, number, exc))
        except ValueError:
            rows.append(
                _problem(
                    number,
                    number,
                    _Invalid("record", m.ErrorCode.INPUT_INVALID, "Line is not valid JSON"),
                )
            )
    return rows


def parse_json(data: bytes) -> list[Record | dict[str, Any]]:
    try:
        parsed = _loads(_decode(data))
    except ValueError as exc:
        raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "Input is not valid JSON") from exc
    if isinstance(parsed, dict):
        values = [parsed]
    elif isinstance(parsed, list):
        values = parsed
    else:
        raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "JSON input must be an object or array")
    rows: list[Record | dict[str, Any]] = []
    for index, value in enumerate(values, start=1):
        try:
            rows.append(_record_from(value, row=index, line=None))
        except _Invalid as exc:
            rows.append(_problem(index, None, exc))
    return rows


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


def parse_csv(data: bytes) -> list[Record | dict[str, Any]]:
    text = _decode(data)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = list(reader.fieldnames or [])
    if not header:
        return []
    if len(header) != len(set(header)):
        raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "CSV header repeats a column")
    if "platform" not in header or "kind" not in header:
        raise _FileInvalid(m.ErrorCode.INPUT_INVALID, "CSV header must include platform and kind")
    unknown = set(header) - CSV_FIELDS
    if unknown:
        raise _FileInvalid(
            m.ErrorCode.INPUT_INVALID, f"CSV header has unknown column {sorted(unknown)[0]}"
        )
    rows: list[Record | dict[str, Any]] = []
    for index, raw in enumerate(reader, start=1):
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
            rows.append(_record_from(obj, row=index, line=None))
        except _Invalid as exc:
            rows.append(_problem(index, None, exc))
    return rows


def parse_text(data: bytes) -> list[Record]:
    text = _decode(data)
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


def _sweep(store: Any, current: str, *, remove_current: bool) -> None:
    staging = store.paths.root / "staging"
    if not staging.is_dir() or staging.is_symlink():
        return
    root = staging.resolve()
    with store.connection() as conn:
        known = {row["id"]: row["state"] for row in conn.execute("SELECT id, state FROM jobs")}
    for child in staging.iterdir():
        if not child.name.startswith("job_") or child.is_symlink() or not child.is_dir():
            continue
        try:
            child.resolve().relative_to(root)
        except ValueError:
            continue
        state = known.get(child.name)
        drop = (remove_current and child.name == current) or state is None or state in jobs.TERMINAL
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
    method: str = ""
    sha: str = ""
    writes: list[Callable[[Any], None]] = field(default_factory=list)

    def checkpoint(self, input_index: int, next_record: int) -> None:
        payload = {
            "input_index": input_index,
            "next_record": next_record,
            "halt": self.halt,
            "rows": self.rows,
            "inputs": self.inputs,
            "missing_fields": sorted(self.missing),
            "accepted": self.accepted,
        }

        def write(conn: Any) -> None:
            self.book.flush(conn, self.step.store)
            for extra in self.writes:
                extra(conn)
            self.writes.clear()
            self.step.store.event(
                conn,
                "collect.batch",
                job=self.step.job,
                object_id=self.step.job.id,
                detail={"input": input_index, "next_record": next_record},
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
        bytes_used=sum(int(item.byte_size or 0) for item in request.inputs if item.kind != "url"),
    )
    resources = set(request.resources)
    start_input = int(saved.get("input_index", 0))
    start_record = int(saved.get("next_record", 0))
    staging = step.store.paths.root / "staging" / step.job.id
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
        if item.format == "jsonl":
            parsed = parse_jsonl(data)
        elif item.format == "csv":
            parsed = parse_csv(data)
        elif item.format == "text":
            parsed = list(parse_text(data))
        else:
            parsed = parse_json(data)
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
        parsed = list(parse_text(raw))
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
    while cursor < len(parsed):
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
    with safety.open_input_file(
        str(candidate), max_bytes=runner.media_max, roots=runner.roots
    ) as src:
        with dest.open("wb") as out:
            while chunk := src.read(1024 * 1024):
                size += len(chunk)
                if runner.bytes_used + size > runner.request.limits.max_bytes:
                    raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Job byte limit reached")
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    runner.bytes_used += size
    info = media_mod.analyze_file(
        dest, max_bytes=runner.media_max, read_timeout_seconds=runner.read_timeout
    )
    media_mod.place_object(runner.step.store, dest, info)
    return {"mime": info.mime}, info


def _queue_item_asset(
    runner: _Run, row: dict[str, Any], info: media_mod.MediaInfo, relation: str
) -> None:
    item_id = row["item_id"]
    revision = row["revision"]

    def write(conn: Any) -> None:
        asset = media_mod.ensure_asset(runner.step.store, conn, info)
        existing = conn.execute(
            "SELECT 1 FROM item_assets WHERE item_id=? AND revision=? AND asset_id=? "
            "AND relation=?",
            (item_id, revision, asset.id, relation),
        ).fetchone()
        if existing is None:
            runner.step.store.insert(
                m.ItemAsset(
                    item_id=item_id, revision=revision, asset_id=asset.id, relation=relation
                ),
                conn=conn,
            )
        row["asset_id"] = asset.id

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
    source_id = _subtitle_source(runner, item.sha256 or "", language)
    if resume == 0 and _cues_exist(runner, source_id, anchor):
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
    cursor = resume
    batch = 0
    while cursor < len(parsed.cues):
        if runner.accepted >= runner.request.limits.max_items:
            runner.halt = "limit_reached"
            break
        cue = parsed.cues[cursor]
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


def _subtitle_source(runner: _Run, sha: str, language: str) -> str:
    with runner.step.store.connection() as conn:
        row = conn.execute(
            "SELECT id FROM sources WHERE project_id=? "
            "AND json_extract(use_basis, '$.role')='subtitles' "
            "AND json_extract(use_basis, '$.file_sha256')=?",
            (runner.request.project_id, sha),
        ).fetchone()
    if row is not None:
        return str(row["id"])
    source_id = m.new_id("src")

    def write(conn: Any) -> None:
        existing = conn.execute(
            "SELECT id FROM sources WHERE project_id=? "
            "AND json_extract(use_basis, '$.role')='subtitles' "
            "AND json_extract(use_basis, '$.file_sha256')=?",
            (runner.request.project_id, sha),
        ).fetchone()
        if existing is not None:
            return
        runner.step.store.insert(
            m.Source(
                id=source_id,
                project_id=runner.request.project_id,
                platform="generic",
                collection_method="file_import",
                upstream_collection_method="unknown",
                fetched_at=runner.step.store.clock(),
                use_basis={"file_sha256": sha, "language": language, "role": "subtitles"},
            ),
            conn=conn,
        )

    runner.writes.append(write)
    return source_id


def _cues_exist(runner: _Run, source_id: str, anchor: tuple[str | None, str | None]) -> bool:
    with runner.step.store.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM transcript_segments WHERE project_id=? AND source_id=? "
            "AND item_id IS ? AND asset_id IS ? LIMIT 1",
            (runner.request.project_id, source_id, anchor[0], anchor[1]),
        ).fetchone()
    return row is not None


def _queue_cue(
    runner: _Run,
    source_id: str,
    anchor: tuple[str | None, str | None],
    cue: media_mod.Cue,
    segment_id: str,
    language: str,
) -> None:
    def write(conn: Any) -> None:
        runner.step.store.insert(
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
            ),
            conn=conn,
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
            source_id = m.new_id("src")
            _queue_direct_media(runner, source_id, info, publish=True)
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
            )
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
    runner.rows.append(row)
    if asset_info is not None:
        _remember_asset(runner, row, asset_info)
    runner.accepted += 1
    runner.checkpoint(index + 1, 0)


def _remember_asset(runner: _Run, row: dict[str, Any], info: media_mod.MediaInfo) -> None:
    def write(conn: Any) -> None:
        asset = conn.execute("SELECT id FROM assets WHERE sha256=?", (info.sha256,)).fetchone()
        if asset is not None:
            row["asset_id"] = asset["id"]

    runner.writes.append(write)


def _queue_direct_media(
    runner: _Run, source_id: str, info: media_mod.MediaInfo, *, publish: bool
) -> None:
    project_id = runner.request.project_id
    attach_item = runner.request.attach_item_id

    def write(conn: Any) -> None:
        runner.step.store.insert(
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
            ),
            conn=conn,
        )
        if not publish:
            return
        asset = media_mod.ensure_asset(runner.step.store, conn, info)
        runner.step.store.insert(
            m.SourceAsset(
                source_id=source_id,
                project_id=project_id,
                asset_id=asset.id,
                relation="media",
            ),
            conn=conn,
        )
        if attach_item is not None:
            revision = runner.step.store.get(m.ItemRevision, attach_item, conn=conn)
            runner.step.store.insert(
                m.ItemAsset(
                    item_id=attach_item,
                    revision=revision.revision,
                    asset_id=asset.id,
                    relation="media",
                ),
                conn=conn,
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
    register_operations()
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
