"""Project-isolated evidence and bounded, local content workflows (B07/B11)."""

from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from metamuse import models as m
from metamuse.paths import private_dir
from metamuse.safety import redact_text, redaction_sensitive_value
from metamuse.store import CONTENT_CURSOR_KEY, TransactionWriter

if TYPE_CHECKING:
    from metamuse.app import AppContext
    from metamuse.jobs import Operation

IMPLEMENTED_TOOLS = frozenset(
    {
        "metamuse_search",
        "metamuse_packet_prepare",
        "metamuse_draft_save",
        "metamuse_draft_preview",
        "metamuse_export",
    }
)
MAX_RESOLUTION_NODES = 2000
MAX_RESOLUTION_EDGES = 10000
MAX_RESOLUTION_DEPTH = 32


def capability_status(ctx: AppContext) -> dict[str, dict[str, str]]:
    return {
        name: {
            "verification": "fixture_passed",
            "environment": "ready",
            "permission": "granted_scope",
        }
        for name in IMPLEMENTED_TOOLS
    }


def register_operations(registry: dict[str, Operation] | None = None) -> None:
    """Synchronous local functions; no submitted jobs or external actions."""


def _now(conn: sqlite3.Connection) -> str:
    return conn.execute("SELECT metamuse_now()").fetchone()[0]


def _event(
    ctx: AppContext,
    conn: sqlite3.Connection,
    kind: str,
    object_id: str,
    project_id: str,
    detail: dict[str, Any] | None = None,
) -> None:
    TransactionWriter(ctx.store, conn).insert(
        m.Event(
            id=m.new_id("evt"),
            object_id=object_id,
            project_id=project_id,
            kind=kind,
            detail=detail or {},
            created_at=_now(conn),
        )
    )


_MISSING_TEXT = "[missing text]"


def _transcript_scope_sql(alias: str = "ts") -> str:
    """A transcript of shared bytes is visible only to the project that owns it."""
    return f"{alias}.project_id=?"


def _flat_text(value: str | None) -> str:
    """Render absent item text without collapsing it into an empty string."""
    return _MISSING_TEXT if value is None else value


def _identity(ref: m.EvidenceRef) -> tuple[str, str, int]:
    return ref.kind, ref.id, ref.revision


def _muse_attribution(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Copy only verified observation provenance, including through draft dependencies."""
    evidence = {
        (entry["ref"]["id"], entry["ref"]["revision"]): entry
        for record in records
        for entry in record.get("muse_evidence", [])
    }
    if not evidence:
        return {}
    entries = [evidence[key] for key in sorted(evidence)]
    backends = {entry["backend"] for entry in entries}
    return {
        "muse_evidence": entries,
        "backend": next(iter(backends)) if len(backends) == 1 else "mixed",
        "mock": any(entry["mock"] for entry in entries),
        "research_complete": False,
    }


@dataclass
class _Resolver:
    """One bounded memo per operation; flattened records never expand a diamond DAG."""

    ctx: AppContext
    project_id: str
    conn: sqlite3.Connection
    tolerant: bool = False
    cache: dict[tuple[str, str, int], dict[str, Any]] = field(default_factory=dict)
    heights: dict[tuple[str, str, int], int] = field(default_factory=dict)
    asset_cache: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)
    edges: int = 0
    bytes: int = 0

    def asset(self, asset_id: str, ref: m.EvidenceRef) -> dict[str, Any] | None:
        self.edges += 1
        if self.edges > MAX_RESOLUTION_EDGES:
            raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Reference edge budget exceeded")
        if asset_id in self.asset_cache:
            return self.asset_cache[asset_id]
        try:
            self.ctx.store.require_project_asset(self.project_id, asset_id, conn=self.conn)
            asset = self.ctx.store.get(m.Asset, asset_id, conn=self.conn)
            if asset.state != "ready":
                raise m.CoreError(m.ErrorCode.RESOURCE_UNAVAILABLE, "Asset is unavailable")
            result = {
                "id": asset.id,
                "sha256": asset.sha256,
                "mime": asset.mime,
                "actual_bytes": asset.actual_bytes,
            }
            self.asset_cache[asset_id] = result
            return result
        except m.CoreError as exc:
            if not self.tolerant or exc.detail.code not in (
                m.ErrorCode.REFERENCE_INVALID,
                m.ErrorCode.RESOURCE_UNAVAILABLE,
                m.ErrorCode.STORAGE,
            ):
                raise
            self.issues.append(
                {
                    "kind": "asset",
                    "asset_id": asset_id,
                    "ref": ref.model_dump(mode="json"),
                    "code": exc.detail.code.value,
                }
            )
            self.asset_cache[asset_id] = None
            return None

    def resolve(
        self,
        ref: m.EvidenceRef,
        *,
        source_only: bool = False,
        seen: frozenset[tuple[str, str, int]] = frozenset(),
    ) -> dict[str, Any] | None:
        self.edges += 1
        if self.edges > MAX_RESOLUTION_EDGES:
            raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Reference edge budget exceeded")
        identity = _identity(ref)
        if identity in seen or len(seen) >= MAX_RESOLUTION_DEPTH:
            raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Cyclic or excessive reference depth")
        if source_only and ref.kind in ("draft_revision", "muse_observation"):
            raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Packet requires original evidence")
        if identity in self.cache:
            if len(seen) + self.heights[identity] > MAX_RESOLUTION_DEPTH:
                raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Excessive reference depth")
            return dict(self.cache[identity])
        if len(self.cache) + len(seen) >= MAX_RESOLUTION_NODES:
            raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Reference node budget exceeded")
        try:
            record = self._load(ref, seen)
        except m.CoreError as exc:
            if not self.tolerant or exc.detail.code != m.ErrorCode.REFERENCE_INVALID:
                raise
            self.issues.append(
                {
                    "kind": "reference",
                    "ref": ref.model_dump(mode="json"),
                    "code": exc.detail.code.value,
                }
            )
            return None
        height = 1 + max(
            (
                self.heights.get((child["kind"], child["id"], child["revision"]), 1)
                for child in record.get("facts_refs", [])
            ),
            default=0,
        )
        if len(seen) + height > MAX_RESOLUTION_DEPTH:
            raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Excessive reference depth")
        self.bytes += len(m.canonical_json(record))
        if self.bytes > self.ctx.limits.text_max_bytes:
            raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Resolved evidence byte budget exceeded")
        self.cache[identity] = record
        self.heights[identity] = height
        return dict(record)

    def _load(self, ref: m.EvidenceRef, seen: frozenset) -> dict[str, Any]:
        store, conn = self.ctx.store, self.conn
        record: dict[str, Any] = {"ref": ref.model_dump(mode="json")}
        if ref.kind == "transcript_segment":
            visible = conn.execute(
                "SELECT 1 FROM transcript_segments ts WHERE ts.id=? AND ts.revision=? AND "
                + _transcript_scope_sql(),
                (ref.id, ref.revision, self.project_id),
            ).fetchone()
            if visible is None:
                raise m.CoreError(
                    m.ErrorCode.REFERENCE_INVALID, "Transcript is missing or ambiguous"
                )
            revision = store.get(m.TranscriptSegment, ref.id, revision=ref.revision, conn=conn)
            if revision.asset_id is not None:
                self.asset(revision.asset_id, ref)
            latest = conn.execute(
                "SELECT max(revision) FROM transcript_segments WHERE id=?", (ref.id,)
            ).fetchone()[0]
            record.update(
                text=revision.text,
                stale=latest > ref.revision,
                source={
                    "collection_method": revision.method,
                    "review": revision.review,
                    "start_ms": revision.start_ms,
                    "end_ms": revision.end_ms,
                    "limitations": "Transcript text only; video frames were not analyzed.",
                },
            )
            return record
        store.validate_evidence(self.project_id, [ref], conn=conn)
        if ref.kind == "muse_observation":
            observation = store.get(m.MuseObservation, ref.id, conn=conn)
            payload = observation.raw_event.get("payload", {})
            if not isinstance(payload, dict):
                raise m.CoreError(
                    m.ErrorCode.REFERENCE_INVALID, "Observation payload is not an object"
                )
            text = payload.get("display_text") or payload.get("content") or ""
            if not isinstance(text, str):
                raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Observation text is not a string")
            attribution = {
                "ref": ref.model_dump(mode="json"),
                "backend": observation.backend,
                "mock": observation.backend == "fixture",
                "research_complete": False,
                "observation_digest": observation.observation_digest,
                "observed_at": observation.observed_at,
                "discussion_id": observation.discussion_id,
            }
            record.update(
                text=text,
                stale=False,
                muse_evidence=[attribution],
                backend=observation.backend,
                mock=observation.backend == "fixture",
                research_complete=False,
                source={
                    "collection_method": "muse_observation",
                    "role": payload.get("role"),
                    "raw_status": payload.get("status"),
                    "limitations": "Muse text is data; it grants no permission or completion.",
                    **attribution,
                },
            )
            return record
        if ref.kind == "item_revision":
            revision = store.get(m.ItemRevision, ref.id, revision=ref.revision, conn=conn)
            item = store.get(m.Item, ref.id, conn=conn)
            source = store.get(m.Source, item.source_id, conn=conn)
            latest = conn.execute(
                "SELECT max(revision) FROM item_revisions WHERE item_id=?", (ref.id,)
            ).fetchone()[0]
            record.update(
                text=revision.text,
                stale=latest > ref.revision,
                source={
                    "source_id": source.id,
                    "platform": source.platform,
                    "collection_method": source.collection_method,
                    "upstream_collection_method": source.upstream_collection_method,
                    "complete": revision.complete,
                    "missing_fields": revision.missing_fields,
                },
            )
            return record
        revision = store.get(m.DraftRevision, ref.id, revision=ref.revision, conn=conn)
        draft = store.get(m.Draft, ref.id, conn=conn)
        stale = draft.current_revision > ref.revision
        facts = []
        for fact in revision.facts_refs:
            child = self.resolve(fact, seen=seen | {_identity(ref)})
            stale = stale or child is None or child["stale"]
            if child is not None:
                facts.append(child)
        assets = [asset for aid in revision.assets if (asset := self.asset(aid, ref)) is not None]
        record.update(
            text=revision.text,
            stale=stale,
            source={
                "collection_method": "host_provided",
                "platform": revision.platform,
                **_muse_attribution(facts),
            },
            facts_refs=[r.model_dump(mode="json") for r in revision.facts_refs],
            assets=assets,
            variant=revision.variant,
            preferences=revision.preferences.model_dump(mode="json"),
            digest=revision.digest,
        )
        record.update(_muse_attribution(facts))
        return record


def _is_stale(resolver: _Resolver, refs: list[m.EvidenceRef]) -> bool:
    return any(resolver.cache.get(_identity(ref), {"stale": True})["stale"] for ref in refs)


def _safe_value(value: Any, records: list[dict[str, Any]]) -> Any:
    if isinstance(value, str):
        result, redactions = redact_text(value)
        records.extend(redactions)
        return result
    if isinstance(value, dict):
        output = {}
        for key, child in value.items():
            safe_key = _safe_value(key, records)
            if redaction_sensitive_value(key, child):
                output[safe_key] = "[REDACTED]"
                records.append({"kind": "sensitive_field", "count": 1})
            else:
                output[safe_key] = _safe_value(child, records)
        return output
    if isinstance(value, list):
        return [_safe_value(v, records) for v in value]
    return value


def _summarize_redactions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record["kind"]] = counts.get(record["kind"], 0) + record["count"]
    return [{"kind": k, "count": v} for k, v in sorted(counts.items())]


def _internal_dir(ctx: AppContext, *parts: str) -> Path:
    path = ctx.paths.root.joinpath(*parts)
    if path.resolve() != path:
        raise m.CoreError(m.ErrorCode.STORAGE, "Internal directory must not be a symlink")
    return private_dir(path)


def _write(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _encode_cursor(payload: dict[str, Any], key: bytes) -> str:
    raw = m.canonical_json(payload)
    return (
        base64.urlsafe_b64encode(raw + hmac.digest(key, raw, "sha256")).decode("ascii").rstrip("=")
    )


def _decode_cursor(cursor: str, keys: tuple[bytes, ...], binding: dict[str, Any]) -> dict[str, Any]:
    try:
        if len(cursor) > 4096 or re.fullmatch(r"[A-Za-z0-9_-]+", cursor, flags=re.ASCII) is None:
            raise ValueError
        raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        payload, signature = raw[:-32], raw[-32:]
        key = next(
            (
                key
                for key in keys
                if hmac.compare_digest(signature, hmac.digest(key, payload, "sha256"))
            ),
            None,
        )
        if key is None:
            raise ValueError
        value = json.loads(payload)
        if not isinstance(value, dict) or _encode_cursor(value, key) != cursor:
            raise ValueError
        if set(value) != {
            *binding,
            "item_watermark",
            "transcript_watermark",
            "count",
            "snapshot_id",
            "last",
        }:
            raise ValueError
        if any(value[k] != v for k, v in binding.items()):
            raise ValueError
        for name in ("item_watermark", "transcript_watermark", "count"):
            if type(value[name]) is not int or value[name] < 0:
                raise ValueError
        if re.fullmatch(r"[0-9a-f]{64}", value["snapshot_id"], flags=re.ASCII) is None:
            raise ValueError
        last = value["last"]
        if (
            not isinstance(last, list)
            or len(last) != 3
            or type(last[0]) is not int
            or last[0] not in (0, 1)
        ):
            raise ValueError
        m.EvidenceRef(
            kind="item_revision" if last[0] == 0 else "transcript_segment",
            id=last[1],
            revision=last[2],
        )
        return value
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID, "Tampered, foreign or invalid search cursor"
        ) from exc


def _search_rows(
    conn: sqlite3.Connection, project: str, query: str, wm: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT r.item_id AS id,r.revision,r.rowid,r.text_hash FROM item_revisions r "
        "JOIN items i ON i.id=r.item_id WHERE i.project_id=? AND r.rowid<=? AND instr(r.text,?)>0 "
        "AND r.revision=(SELECT max(r2.revision) FROM item_revisions r2 "
        "WHERE r2.item_id=r.item_id AND r2.rowid<=?) ORDER BY r.item_id,r.revision",
        (project, wm["item_watermark"], query, wm["item_watermark"]),
    ).fetchall()
    result = [
        {
            "kind": "item_revision",
            "id": r["id"],
            "revision": r["revision"],
            "rank": 0,
            "rowid": r["rowid"],
            "content_digest": r["text_hash"],
        }
        for r in rows
    ]
    rows = conn.execute(
        "SELECT ts.* ,ts.rowid FROM transcript_segments ts WHERE "
        + _transcript_scope_sql()
        + " AND ts.rowid<=? AND instr(ts.text,?)>0 AND ts.revision=(SELECT max(t2.revision) "
        "FROM transcript_segments t2 WHERE t2.id=ts.id AND t2.rowid<=?) ORDER BY ts.id,ts.revision",
        (project, wm["transcript_watermark"], query, wm["transcript_watermark"]),
    ).fetchall()
    result.extend(
        {
            "kind": "transcript_segment",
            "id": r["id"],
            "revision": r["revision"],
            "rank": 1,
            "rowid": r["rowid"],
            "content_digest": m.payload_digest(dict(r)),
        }
        for r in rows
    )
    return result


def search_evidence(ctx: AppContext, request: m.SearchRequest) -> m.Envelope:
    key = ctx.store.get_workspace_key(CONTENT_CURSOR_KEY).key_material
    binding = {
        "version": 1,
        "project_id": request.project_id,
        "query_digest": m.payload_digest(request.query),
    }
    cursor = (
        _decode_cursor(
            request.cursor,
            tuple(
                k.key_material for k in ctx.store.workspace_verification_keys(CONTENT_CURSOR_KEY)
            ),
            binding,
        )
        if request.cursor is not None
        else None
    )
    with ctx.store.connection() as conn:
        conn.execute("BEGIN")
        ctx.store.get(m.Project, request.project_id, conn=conn)
        watermark = cursor or {
            "item_watermark": conn.execute(
                "SELECT coalesce(max(rowid),0) FROM item_revisions"
            ).fetchone()[0],
            "transcript_watermark": conn.execute(
                "SELECT coalesce(max(rowid),0) FROM transcript_segments"
            ).fetchone()[0],
        }
        rows = _search_rows(conn, request.project_id, request.query, watermark)
        snapshot_id = m.payload_digest(rows)
        if cursor and (cursor["count"] != len(rows) or cursor["snapshot_id"] != snapshot_id):
            raise m.CoreError(
                m.ErrorCode.REFERENCE_INVALID, "Search snapshot membership changed; restart search"
            )
        last = tuple(cursor["last"]) if cursor else (-1, "", 0)
        if cursor and not any((r["rank"], r["id"], r["revision"]) == last for r in rows):
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid search sort key")
        page = [r for r in rows if (r["rank"], r["id"], r["revision"]) > last][: request.limit + 1]
        resolver = _Resolver(ctx, request.project_id, conn)
        results = []
        for value in page[: request.limit]:
            ref = m.EvidenceRef(kind=value["kind"], id=value["id"], revision=value["revision"])
            record = resolver.resolve(ref, source_only=True)
            text = record.pop("text")
            if text is None:
                record.update(snippet="", snippet_start_code_points=0)
            else:
                match = text.find(request.query)
                lead = min(80, max(0, (240 - len(request.query)) // 2))
                start = max(0, match - lead)
                end = min(len(text), start + max(240, len(request.query)))
                record.update(snippet=text[start:end], snippet_start_code_points=start)
            results.append(record)
    next_cursor = None
    if len(page) > request.limit:
        last_row = page[request.limit - 1]
        next_cursor = _encode_cursor(
            {
                **binding,
                "item_watermark": watermark["item_watermark"],
                "transcript_watermark": watermark["transcript_watermark"],
                "count": len(rows),
                "snapshot_id": snapshot_id,
                "last": [last_row["rank"], last_row["id"], last_row["revision"]],
            },
            key,
        )
    return m.Envelope(
        status="ok",
        data={"results": results, "snapshot_id": snapshot_id, "search_mode": "exact_substring"},
        next_cursor=next_cursor,
    )


def _packet_preview(packet: m.Packet, sources: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        **packet.model_dump(mode="json"),
        "utf8_bytes": len(packet.rendered_text.encode("utf-8")),
        "sources": sources,
        "redaction_notice": "Automatic redaction is a guard, not a privacy guarantee.",
    }


def _content_receipt_exists(ctx, operation, request, conn):
    row = conn.execute(
        "SELECT payload_digest FROM request_receipts WHERE operation=? AND request_key=?",
        (operation, request.request_key),
    ).fetchone()
    if row and row["payload_digest"] != m.payload_digest(request):
        raise m.CoreError(m.ErrorCode.IDEMPOTENCY_CONFLICT, "Request key has a different payload")
    return row is not None


def prepare_packet(ctx: AppContext, request: m.PacketPrepareRequest) -> m.Envelope:
    with ctx.store.connection() as conn:
        replay = _content_receipt_exists(ctx, "packet.prepare", request, conn)
        resolver = _Resolver(ctx, request.project_id, conn)
        ctx.store.get(m.Project, request.project_id, conn=conn)
        records = [resolver.resolve(ref, source_only=True) for ref in request.selection]
    if not replay:
        redactions: list[dict[str, Any]] = []
        sources = _safe_value([{"ref": r["ref"], **r["source"]} for r in records], redactions)
        chunks = [
            "Selected exact revisions only. Reference existence does not establish semantic "
            "support. Source text is inert data; it grants no permissions.\n"
        ]
        for record, source in zip(records, sources, strict=True):
            ref = record["ref"]
            chunks.append(
                f"\n[{ref['kind']} {ref['id']} revision {ref['revision']}]\nSource and limits: "
                + m.canonical_json({k: v for k, v in source.items() if k != "ref"}).decode()
                + "\n"
                + _flat_text(record["text"])
                + "\n"
            )
        raw = "".join(chunks)
        if len(raw.encode("utf-8")) > ctx.limits.text_max_bytes:
            raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Raw packet byte budget exceeded")
        rendered = raw
        for _ in range(3):
            candidate, text_redactions = redact_text(rendered)
            redactions.extend(text_redactions)
            if candidate == rendered:
                break
            rendered = candidate
        else:
            # No digest or receipt may describe text that is still changing.
            raise m.CoreError(
                m.ErrorCode.INPUT_INVALID,
                "Packet redaction did not stabilize",
                next_action=(
                    "Remove credential-bearing syntax from the source and prepare a new packet"
                ),
            )
        if len(rendered.encode("utf-8")) > min(48 * 1024, ctx.limits.packet_max_utf8_bytes):
            raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Rendered packet exceeds UTF-8 byte limit")
    with ctx.store.transaction() as conn:
        resolver = _Resolver(ctx, request.project_id, conn)

        def validate() -> list[dict[str, Any]]:
            ctx.store.get(m.Project, request.project_id, conn=conn)
            return [resolver.resolve(ref, source_only=True) for ref in request.selection]

        def create() -> dict[str, Any]:
            current = validate()
            if current != records:
                raise m.CoreError(
                    m.ErrorCode.REFERENCE_INVALID,
                    "Evidence changed during rendering; prepare a new packet",
                )
            packet = m.Packet(
                id=m.new_id("pkt"),
                project_id=request.project_id,
                selection=request.selection,
                rendered_text=rendered,
                redactions=_summarize_redactions(redactions),
                coverage_status="bounded",
                digest=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                created_at=_now(conn),
                stale=_is_stale(resolver, request.selection),
            )
            TransactionWriter(ctx.store, conn).insert(packet)
            _event(ctx, conn, "packet.prepared", packet.id, request.project_id)
            return _packet_preview(packet, sources)

        data = ctx.store.mutate_once(conn, "packet.prepare", request.request_key, request, create)
        validate()  # Replays still check scope/existence, after receipt conflict detection.
        packet = ctx.store.get(m.Packet, data["id"], conn=conn)
        stale = _is_stale(resolver, request.selection)
        conn.execute("UPDATE packets SET stale=? WHERE id=?", (int(stale), packet.id))
        data = {**data, "stale": stale}
    return m.Envelope(status="ok", data=data)


def preview_packet(ctx: AppContext, packet_id: str) -> m.Envelope:
    """Read the fixed packet text without creating a packet or granting permission."""
    with ctx.store.connection() as conn:
        packet = ctx.store.get(m.Packet, packet_id, conn=conn)
        if redact_text(packet.rendered_text)[0] != packet.rendered_text:
            raise m.CoreError(
                m.ErrorCode.REFERENCE_INVALID,
                "Packet is stale; prepare a new packet",
                next_action="Prepare a new packet before reviewing or sending it",
            )
        resolver = _Resolver(ctx, packet.project_id, conn)
        records = [resolver.resolve(ref, source_only=True) for ref in packet.selection]
        sources = _safe_value([{"ref": r["ref"], **r["source"]} for r in records], [])
        data = _packet_preview(packet, sources)
        data["stale"] = _is_stale(resolver, packet.selection)
    return m.Envelope(status="ok", data=data)


def save_draft(ctx: AppContext, request: m.DraftSaveRequest) -> m.Envelope:
    with ctx.store.transaction() as conn:
        resolver = _Resolver(ctx, request.project_id, conn)

        def validate() -> None:
            ctx.store.get(m.Project, request.project_id, conn=conn)
            for ref in request.facts_refs:
                resolver.resolve(ref)
            for asset_id in request.assets:
                resolver.asset(
                    asset_id,
                    m.EvidenceRef(
                        kind="draft_revision", id=request.draft_id or "drf_new", revision=1
                    ),
                )

        def save() -> dict[str, Any]:
            if (request.draft_id is None) != (request.expected_revision is None):
                raise m.CoreError(
                    m.ErrorCode.INPUT_INVALID,
                    "Draft updates require expected_revision; creation omits it",
                )
            validate()
            writer = TransactionWriter(ctx.store, conn)
            if request.draft_id is None:
                draft = writer.insert(m.Draft(id=m.new_id("drf"), project_id=request.project_id))
                number = 1
            else:
                draft = writer.get(m.Draft, request.draft_id)
                if draft.project_id != request.project_id:
                    raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Draft is outside project")
                number = draft.current_revision + 1
            payload = {
                "text": request.text,
                "platform": request.platform,
                "variant": request.variant,
                "facts_refs": [r.model_dump(mode="json") for r in request.facts_refs],
                "assets": request.assets,
                "preferences": request.preferences.model_dump(mode="json"),
            }
            revision = m.DraftRevision(
                draft_id=draft.id, revision=number, **payload, digest=m.payload_digest(payload)
            )
            writer.append_draft_revision(revision, expected_revision=request.expected_revision)
            writer.execute(
                "UPDATE drafts SET stale=? WHERE id=?",
                (int(_is_stale(resolver, request.facts_refs)), draft.id),
            )
            _event(ctx, conn, "draft.saved", draft.id, request.project_id, {"revision": number})
            return revision.model_dump(mode="json")

        data = ctx.store.mutate_once(conn, "draft.save", request.request_key, request, save)
        validate()
        owner = ctx.store.get(m.Draft, data["draft_id"], conn=conn)
        if owner.project_id != request.project_id:
            raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Draft is outside project")
        ctx.store.get(m.DraftRevision, data["draft_id"], revision=data["revision"], conn=conn)
        stale = _is_stale(resolver, request.facts_refs)
        if owner.current_revision == data["revision"]:
            conn.execute("UPDATE drafts SET stale=? WHERE id=?", (int(stale), owner.id))
        data = {**data, "stale": stale, **_muse_attribution(list(resolver.cache.values()))}
    return m.Envelope(status="ok", data=data)


_DATE = re.compile(r"(?<!\d)\d{4}[-/]\d{1,2}[-/]\d{1,2}(?!\d)|\d{4}年\d{1,2}月\d{1,2}日")
_NUMBER = re.compile(r"(?<![0-9A-Za-z_])[-+]?\d+(?:[.,]\d+)*(?:%|％)?")


def _conflict_candidates(text: str, facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for kind, pattern in (("date", _DATE), ("number", _NUMBER)):
        target = _DATE.sub(" ", text) if kind == "number" else text
        draft_values = set(pattern.findall(target))
        for fact in facts:
            fact_text = fact.get("text")
            if not isinstance(fact_text, str):
                continue
            target = _DATE.sub(" ", fact_text) if kind == "number" else fact_text
            source_values = set(pattern.findall(target))
            differences = sorted(draft_values - source_values)
            if source_values and differences:
                candidates.append(
                    {
                        "kind": kind,
                        "ref": fact["ref"],
                        "draft_values": differences,
                        "source_values": sorted(source_values),
                        "assessment": "Lexical difference candidate only; requires host review.",
                    }
                )
    return candidates


def preview_draft(ctx: AppContext, request: m.DraftPreviewRequest) -> m.Envelope:
    with ctx.store.connection() as conn:
        conn.execute("BEGIN")
        draft = ctx.store.get(m.Draft, request.draft_id, conn=conn)
        revision = ctx.store.get(
            m.DraftRevision, request.draft_id, revision=request.revision, conn=conn
        )
        resolver = _Resolver(ctx, draft.project_id, conn, tolerant=True)
        for ref in revision.facts_refs:
            resolver.resolve(ref)
        root = m.EvidenceRef(kind="draft_revision", id=draft.id, revision=revision.revision)
        for asset_id in revision.assets:
            resolver.asset(asset_id, root)
        facts = list(resolver.cache.values())
        stale = _is_stale(resolver, revision.facts_refs)
    text = revision.text
    format_issues = [
        message
        for condition, message in (
            ("\x00" in text, "Text contains NUL code points"),
            (not text, "Text is empty"),
        )
        if condition
    ]
    return m.Envelope(
        status="ok",
        data={
            "draft_id": draft.id,
            "revision": revision.revision,
            "digest": revision.digest,
            "text": text,
            "platform": revision.platform,
            "stale": stale,
            "counts": {
                "code_points": len(text),
                "utf16_units": len(text.encode("utf-16-le")) // 2,
                "utf8_bytes": len(text.encode("utf-8")),
            },
            "counting_algorithm": "Python len for Unicode code points; "
            "UTF-16LE bytes / 2 without BOM; "
            "UTF-8 encoded bytes. Grapheme clusters are not counted.",
            "format": {"declared": revision.preferences.format, "issues": format_issues},
            "reference_issues": resolver.issues,
            "reference_notice": "Reference exists does not mean semantically supported.",
            "conflict_candidates": _conflict_candidates(text, facts),
            "platform_limit": {"value": None, "source": None, "verified_at": None},
            **_muse_attribution(facts),
        },
        warnings=[
            "Platform length limit is unknown: no recorded source and verification date.",
            "Date/number checks identify lexical candidates only; host review is required.",
        ],
    )


def _plain_text_bytes(record: dict[str, Any]) -> bytes:
    """A text file has no source column, so absence is written beside the marker."""
    text = record.get("text")
    if text is None:
        missing = record.get("source", {}).get("missing_fields")
        if not isinstance(missing, list):
            missing = ["text"]
        body = _MISSING_TEXT + "\nmissing_fields: " + json.dumps(missing, ensure_ascii=False) + "\n"
        return body.encode("utf-8")
    attribution = _muse_attribution([record])
    prefix = (
        "Muse evidence: " + m.canonical_json(attribution).decode() + "\n\n" if attribution else ""
    )
    return (prefix + text).encode("utf-8")


def _csv_bytes(records: list[dict[str, Any]]) -> bytes:
    def cell(value: str) -> str:
        candidate = value.lstrip()
        while candidate.startswith(('"', "'")):
            candidate = candidate[1:].lstrip()
        return (
            "'" + value
            if value.startswith(("\t", "\r", "\n"))
            or candidate.startswith(("=", "+", "-", "@", "|"))
            else value
        )

    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["kind", "id", "revision", "text", "source"])
    for record in records:
        ref = record["ref"]
        writer.writerow(
            [
                cell(str(v))
                for v in (
                    ref["kind"],
                    ref["id"],
                    ref["revision"],
                    _flat_text(record["text"]),
                    m.canonical_json(record["source"]).decode(),
                )
            ]
        )
    return stream.getvalue().encode("utf-8")


def _export_files(
    format_name: str, records: list[dict[str, Any]], *, redacted: bool = False
) -> dict[str, bytes]:
    if format_name == "jsonl":
        return {"records.jsonl": b"".join(m.canonical_json(r) + b"\n" for r in records)}
    if format_name == "csv":
        return {"records.csv": _csv_bytes(records)}
    if format_name == "text":
        return {f"object-{i:04d}.txt": _plain_text_bytes(r) for i, r in enumerate(records, 1)}
    if format_name == "markdown":
        notice = (
            "" if redacted else "Notice: Private unredacted export; not a public share bundle.\n\n"
        )
        return {
            f"object-{i:04d}.md": (
                notice
                + "Evidence: "
                + m.canonical_json(r["ref"]).decode()
                + "\n\nSource: "
                + m.canonical_json(r["source"]).decode()
                + "\n\n"
                + _flat_text(r["text"])
            ).encode("utf-8")
            for i, r in enumerate(records, 1)
        }
    return {
        **_export_files("jsonl", records, redacted=True),
        **_export_files("markdown", records, redacted=True),
    }


def export_objects(ctx: AppContext, request: m.ExportRequest) -> m.Envelope:
    with ctx.store.connection() as read_conn:
        replay = _content_receipt_exists(ctx, "export.objects", request, read_conn)
        reader = _Resolver(ctx, request.project_id, read_conn)
        ctx.store.get(m.Project, request.project_id, conn=read_conn)
        selected_records = [reader.resolve(ref) for ref in request.selection]
        identities = {_identity(ref) for ref in request.selection}
        prepared_records = selected_records + [
            value for identity, value in reader.cache.items() if identity not in identities
        ]
    if not replay:
        redactions: list[dict[str, Any]] = []
        selected = (
            _safe_value(prepared_records, redactions)
            if request.format == "bundle"
            else prepared_records
        )
        prepared_files = _export_files(request.format, selected)
        if sum(len(value) for value in prepared_files.values()) > ctx.limits.text_max_bytes:
            raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Export byte budget exceeded")
    staging = final = None
    committed = False
    try:
        with ctx.store.transaction() as conn:
            resolver = _Resolver(ctx, request.project_id, conn)

            def validate() -> list[dict[str, Any]]:
                ctx.store.get(m.Project, request.project_id, conn=conn)
                selected = [resolver.resolve(ref) for ref in request.selection]
                identities = {_identity(ref) for ref in request.selection}
                return selected + [
                    value
                    for identity, value in resolver.cache.items()
                    if identity not in identities
                ]

            def create() -> dict[str, Any]:
                records = validate()
                if records != prepared_records:
                    raise m.CoreError(
                        m.ErrorCode.REFERENCE_INVALID,
                        "Evidence changed during rendering; prepare a new export",
                    )
                export_id = m.new_id("exp")
                export = m.Export(
                    id=export_id,
                    project_id=request.project_id,
                    selection=request.selection,
                    format=request.format,
                )
                TransactionWriter(ctx.store, conn).insert(export)
                files = prepared_files
                manifest = {
                    "export_id": export_id,
                    "format": request.format,
                    "selection": [r.model_dump(mode="json") for r in request.selection],
                    "redactions": _summarize_redactions(redactions)
                    if request.format == "bundle"
                    else None,
                    "text_policy": "redacted" if request.format == "bundle" else "original_private",
                    "asset_policy": "metadata_only; opaque media bytes are not included",
                    **_muse_attribution(records),
                    "files": [
                        {"name": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
                        for name, raw in sorted(files.items())
                    ],
                }
                if request.format == "bundle":
                    manifest["redaction_notice"] = (
                        "Automatic redaction is a guard, not a guarantee."
                    )
                else:
                    manifest["redaction_notice"] = (
                        "Private original text; automatic redaction is not applied."
                    )
                stage = _internal_dir(ctx, "staging") / export_id
                stage.mkdir(mode=0o700)
                nonlocal staging, final
                staging = stage
                exports = _internal_dir(ctx, "exports")
                destination = exports / export_id
                for name, raw in files.items():
                    _write(stage / name, raw)
                _write(stage / "manifest.json", m.canonical_json(manifest) + b"\n")
                _sync_dir(stage)
                if destination.exists() or destination.is_symlink():
                    raise m.CoreError(m.ErrorCode.STORAGE, "Export destination already exists")
                stage.rename(destination)
                final = destination
                _sync_dir(exports)
                _sync_dir(ctx.paths.root / "staging")
                artifact = f"exports/{export_id}"
                conn.execute(
                    "UPDATE exports SET manifest=?,artifact=?,state='ready' WHERE id=?",
                    (m.canonical_json(manifest).decode(), artifact, export_id),
                )
                _event(ctx, conn, "export.ready", export_id, request.project_id)
                return {
                    **export.model_dump(mode="json"),
                    "manifest": manifest,
                    "artifact": artifact,
                    "state": "ready",
                }

            data = ctx.store.mutate_once(
                conn, "export.objects", request.request_key, request, create
            )
            validate()
            stored = ctx.store.get(m.Export, data["id"], conn=conn)
            if stored.state != "ready" or stored.project_id != request.project_id:
                raise m.CoreError(m.ErrorCode.RESOURCE_UNAVAILABLE, "Stored export is not ready")
            if stored.model_dump(mode="json") != data:
                raise m.CoreError(
                    m.ErrorCode.STORAGE, "Export receipt differs from stored artifact"
                )
            ctx.store.verify_export(stored, conn=conn)
        committed = True
    finally:
        if not committed:
            for directory in (staging, final):
                if directory is not None and directory.is_dir() and not directory.is_symlink():
                    shutil.rmtree(directory)
    ctx.store.verify_export(stored)
    return m.Envelope(
        status="ok",
        data=data,
        warnings=[
            "Local export only; nothing was published or uploaded.",
            "Bundle redaction is a guard, not a guarantee; assets are metadata only."
            if request.format == "bundle"
            else "Original text is preserved in this private export.",
        ],
    )
