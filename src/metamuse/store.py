"""SQLite, migrations and content objects. Connections never escape a context manager."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import tempfile
import threading
import warnings
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, BinaryIO, Literal, TypeVar

from pydantic import ValidationError

from metamuse import models as m
from metamuse.config import load_config
from metamuse.paths import WorkspacePaths, private_dir, private_file

SCHEMA_VERSION = 3
DATA = Path(__file__).parent / "data"
MIGRATIONS = {
    1: DATA / "migrations" / "001_initial.sql",
    2: DATA / "migrations" / "002_collect_media.sql",
    3: DATA / "migrations" / "003_muse_lifecycle.sql",
}
CONTENT_CURSOR_KEY = "content.cursor"


@dataclass(frozen=True)
class WorkspaceKey:
    """Internal signing material; never serialize into public records or events."""

    name: str
    version: int
    key_material: bytes = field(repr=False)
    created_at: str
    status: str


TABLE_MODELS = {
    "projects": m.Project,
    "jobs": m.Job,
    "job_steps": m.JobStep,
    "sources": m.Source,
    "items": m.Item,
    "item_revisions": m.ItemRevision,
    "assets": m.Asset,
    "item_assets": m.ItemAsset,
    "source_assets": m.SourceAsset,
    "transcript_segments": m.TranscriptSegment,
    "packets": m.Packet,
    "permits": m.Permit,
    "discussions": m.Discussion,
    "exchanges": m.Exchange,
    "messages": m.Message,
    "muse_observations": m.MuseObservation,
    "drafts": m.Draft,
    "draft_revisions": m.DraftRevision,
    "exports": m.Export,
    "events": m.Event,
}
MODEL_TABLES = {value: key for key, value in TABLE_MODELS.items()}
JSON_FIELDS = {
    "payload",
    "requested_scope",
    "observed_scope",
    "missing_fields",
    "limits",
    "result",
    "error",
    "target",
    "checkpoint",
    "use_basis",
    "evidence",
    "selection",
    "redactions",
    "scope",
    "baseline",
    "variant",
    "facts_refs",
    "assets",
    "preferences",
    "manifest",
    "detail",
    "open_payload",
    "open_delivery_proof",
    "outbound_payload",
    "delivery_proof",
    "observation_memory",
    "raw_event",
}
BOOL_FIELDS = {
    "cancel_requested",
    "complete",
    "stale",
    "revoked",
    "overlap",
    "bytes_handed_off",
    "open_bytes_handed_off",
}
T = TypeVar("T", bound=m.Model)
_ACTIVE_WRITES = threading.local()


class TransactionWriter:
    """Bound to the caller's transaction; no commit, rollback or nested transaction API."""

    def __init__(self, store: Store, conn: sqlite3.Connection):
        self._store, self._conn = store, conn

    def insert(self, record: T) -> T:
        return self._store.insert(record, conn=self._conn)

    def get(self, model: type[T], record_id: str, *, revision: int | None = None) -> T:
        return self._store.get(model, record_id, revision=revision, conn=self._conn)

    def event(
        self,
        kind: str,
        *,
        job: m.Job | None = None,
        object_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> m.Event:
        return self._store.event(self._conn, kind, job=job, object_id=object_id, detail=detail)

    def append_draft_revision(
        self, record: m.DraftRevision, *, expected_revision: int | None
    ) -> m.DraftRevision:
        return self._store.append_draft_revision(
            record, expected_revision=expected_revision, conn=self._conn
        )

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        # Feature-owned static SQL only. Transaction control belongs to Store.
        if sql.lstrip().split(None, 1)[0].upper() not in (
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "WITH",
        ):
            raise m.CoreError(m.ErrorCode.STORAGE, "Batch SQL must not control transactions")
        return self._conn.execute(sql, parameters)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def file_lock(path: Path, *, exclusive: bool = True, blocking: bool = True) -> Iterator[Any]:
    private_file(path)
    with path.open("r+b") as handle:
        flags = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(handle, flags)
        except BlockingIOError:
            raise m.CoreError(m.ErrorCode.JOB_BUSY, "Workspace lock is held") from None
        try:
            yield handle
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _statements(script: str) -> Iterator[str]:
    """Execute complete statements individually; executescript commits implicitly."""
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip():
        raise ValueError("Incomplete migration statement")


class Store:
    def __init__(
        self,
        paths: WorkspacePaths,
        *,
        clock: Callable[[], str] = utc_now,
        migrations: dict[int, Path] | None = None,
        schema_version: int | None = None,
        _restore_in_progress: bool = False,
    ):
        if _restore_marker_present(paths) and not _restore_in_progress:
            raise m.CoreError(
                m.ErrorCode.STORAGE,
                "Workspace restore is incomplete",
                next_action="Restore the verified backup into a fresh workspace",
            )
        self.paths = WorkspacePaths.resolve(paths.root)
        self.clock = clock
        self._restore_in_progress = _restore_in_progress
        self.open_warnings: list[str] = []
        self.schema_version = SCHEMA_VERSION if schema_version is None else schema_version
        self._migrations = migrations or MIGRATIONS
        self.paths.initialize()
        self.migrate(self._migrations)
        self._provision_workspace_keys()

    def _open_database(self) -> sqlite3.Connection:
        # Check live files before SQLite can follow a link or mutate its sidecars.
        for path in (
            self.paths.database,
            Path(str(self.paths.database) + "-wal"),
            Path(str(self.paths.database) + "-shm"),
        ):
            if path.is_symlink() or path.exists():
                _regular_file(path)
        # Check version before applying writable pragmas; older builds must not write newer DBs.
        if self.paths.database.exists():
            with closing(
                sqlite3.connect(f"{self.paths.database.as_uri()}?mode=ro", uri=True)
            ) as probe:
                _configure(probe, writable=False)
                version = probe.execute("PRAGMA user_version").fetchone()[0]
            if version > self.schema_version:
                raise m.CoreError(
                    m.ErrorCode.PROTOCOL_MISMATCH, "Database schema is newer than this build"
                )
        private_file(self.paths.database)
        conn = sqlite3.connect(self.paths.database, timeout=5, isolation_level=None)
        try:
            _configure(conn)
            conn.create_function("metamuse_now", 0, lambda: self.clock())
        except BaseException:
            conn.close()
            raise
        return conn

    def _open(self) -> sqlite3.Connection:
        try:
            return self._open_database()
        except sqlite3.Error:
            raise m.CoreError(m.ErrorCode.STORAGE, "Cannot open workspace database") from None

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self._check_restore_marker()
        with file_lock(self.paths.runtime / "maintenance.lock", exclusive=False):
            self._check_restore_marker()
            conn = self._open()
            try:
                if conn.execute("PRAGMA user_version").fetchone()[0] != self.schema_version:
                    raise m.CoreError(m.ErrorCode.PROTOCOL_MISMATCH, "Database requires migration")
                yield conn
            except sqlite3.Error:
                raise m.CoreError(m.ErrorCode.STORAGE, "SQLite operation failed") from None
            finally:
                conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        active = getattr(_ACTIVE_WRITES, "roots", set())
        if self.paths.root in active:
            raise m.CoreError(m.ErrorCode.STORAGE, "Nested write transaction; use the batch writer")
        _ACTIVE_WRITES.roots = active | {self.paths.root}
        try:
            with self.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                    conn.commit()
                except sqlite3.IntegrityError:
                    conn.rollback()
                    raise m.CoreError(
                        m.ErrorCode.REFERENCE_INVALID, "Database reference or constraint rejected"
                    ) from None
                except BaseException:
                    conn.rollback()
                    raise
        finally:
            _ACTIVE_WRITES.roots = active

    def _check_restore_marker(self) -> None:
        if _restore_marker_present(self.paths) and not self._restore_in_progress:
            raise m.CoreError(m.ErrorCode.STORAGE, "Workspace restore is incomplete")

    def migrate(self, migrations: dict[int, Path]) -> None:
        self._check_restore_marker()
        with file_lock(self.paths.runtime / "maintenance.lock"):
            self._check_restore_marker()
            had_database = self.paths.database.exists() and self.paths.database.stat().st_size > 0
            conn = self._open()
            try:
                old_version = conn.execute("PRAGMA user_version").fetchone()[0]
                _verify_schema(conn, old_version, migrations)
                if old_version == self.schema_version:
                    return
                with file_lock(self.paths.worker_lock, blocking=False):
                    if had_database:
                        result = self._backup(
                            conn,
                            self.paths.root / "backups" / m.new_id("pre_upgrade"),
                            _pre_upgrade=True,
                        )
                        self._record_upgrade_backup(result)
                        self.open_warnings.extend(result.warnings)
                        _warn_backup(result, stacklevel=4)
                    conn.execute("PRAGMA foreign_keys=OFF")
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        for version in range(old_version + 1, self.schema_version + 1):
                            if version not in migrations or not migrations[version].is_file():
                                raise m.CoreError(m.ErrorCode.STORAGE, "Missing migration")
                            for statement in _statements(migrations[version].read_text()):
                                conn.execute(statement)
                            conn.execute(f"PRAGMA user_version={version}")
                        if conn.execute("PRAGMA foreign_key_check").fetchone():
                            raise m.CoreError(m.ErrorCode.STORAGE, "Migration FK check failed")
                        _verify_schema(conn, self.schema_version, migrations)
                        conn.commit()
                    except BaseException:
                        conn.rollback()
                        raise
                    finally:
                        conn.execute("PRAGMA foreign_keys=ON")
            except (sqlite3.Error, ValueError):
                raise m.CoreError(
                    m.ErrorCode.STORAGE, "Migration failed; original data retained"
                ) from None
            finally:
                conn.close()

    @staticmethod
    def _key_name(name: str) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", name, flags=re.ASCII) is None:
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid workspace key name")

    def get_workspace_key(
        self, name: str, version: int | None = None, *, conn: sqlite3.Connection | None = None
    ) -> WorkspaceKey:
        """Read an active key or an exact older version without creating anything."""
        self._key_name(name)
        if conn is None:
            with self.connection() as read:
                return self.get_workspace_key(name, version, conn=read)
        row = conn.execute(
            "SELECT * FROM workspace_keys WHERE name=? AND "
            + ("status='active'" if version is None else "version=?"),
            (name,) if version is None else (name, version),
        ).fetchone()
        if row is None:
            raise m.CoreError(m.ErrorCode.STORAGE, "Workspace signing key is unavailable")
        return WorkspaceKey(**dict(row))

    def workspace_verification_keys(self, name: str) -> tuple[WorkspaceKey, ...]:
        """Retained active/retired keys; never reads legacy receipts."""
        self._key_name(name)
        with self.connection() as conn:
            return tuple(
                WorkspaceKey(**dict(row))
                for row in conn.execute(
                    "SELECT * FROM workspace_keys WHERE name=? ORDER BY version DESC", (name,)
                )
            )

    def get_or_create_workspace_key(
        self, name: str, *, conn: sqlite3.Connection | None = None
    ) -> WorkspaceKey:
        """Provision at initialization/mutation boundaries, never in a read-only tool."""
        self._key_name(name)
        if conn is None:
            with self.transaction() as tx:
                return self.get_or_create_workspace_key(name, conn=tx)
        active = conn.execute(
            "SELECT version FROM workspace_keys WHERE name=? AND status='active'", (name,)
        ).fetchone()
        if active:
            return self.get_workspace_key(name, active[0], conn=conn)
        version = conn.execute(
            "SELECT coalesce(max(version),0)+1 FROM workspace_keys WHERE name=?", (name,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO workspace_keys VALUES(?,?,?,?, 'active')",
            (name, version, secrets.token_bytes(32), self.clock()),
        )
        return self.get_workspace_key(name, version, conn=conn)

    def rotate_workspace_key(self, name: str) -> WorkspaceKey:
        """Create the next active version atomically, retaining all verification keys."""
        self._key_name(name)
        with self.transaction() as conn:
            conn.execute(
                "UPDATE workspace_keys SET status='retired' WHERE name=? AND status='active'",
                (name,),
            )
            return self.get_or_create_workspace_key(name, conn=conn)

    def _provision_workspace_keys(self) -> None:
        with self.transaction() as conn:
            self.get_or_create_workspace_key(CONTENT_CURSOR_KEY, conn=conn)

    def insert(self, record: T, *, conn: sqlite3.Connection | None = None) -> T:
        if conn is None:
            with self.transaction() as tx:
                return self.insert(record, conn=tx)
        table = MODEL_TABLES[type(record)]
        data = record.model_dump(mode="json")
        project = data.get("project_id")
        if isinstance(record, (m.Packet, m.Export)):
            if isinstance(record, m.Packet):
                try:
                    m.utf8_text(
                        record.rendered_text,
                        load_config(self.paths.root).limits.packet_max_utf8_bytes,
                    )
                except ValueError:
                    raise m.CoreError(
                        m.ErrorCode.LIMIT_REACHED, "Packet UTF-8 byte limit exceeded"
                    ) from None
            self.validate_evidence(
                project, record.selection, conn=conn, source_only=isinstance(record, m.Packet)
            )
        if isinstance(record, m.MuseObservation):
            if m.payload_digest(record.raw_event) != record.observation_digest:
                raise m.CoreError(
                    m.ErrorCode.REFERENCE_INVALID, "Observation digest differs from raw event"
                )
        if isinstance(record, m.DraftRevision):
            draft = self.get(m.Draft, record.draft_id, conn=conn)
            self.validate_evidence(draft.project_id, record.facts_refs, conn=conn)
            for asset_id in record.assets:
                self.require_project_asset(draft.project_id, asset_id, conn=conn)
        if isinstance(record, m.Asset) and record.state == "ready":
            self.verify_asset(record)
        if isinstance(record, m.TranscriptSegment) and record.asset_id is not None:
            # Derived text is owned by project_id. The asset must already be
            # visible there; shared bytes are not themselves the owner.
            self.require_project_asset(record.project_id, record.asset_id, conn=conn)
        columns = list(data)
        values = [
            m.canonical_json(data[c]).decode()
            if c in JSON_FIELDS and data[c] is not None
            else int(data[c])
            if c in BOOL_FIELDS and data[c] is not None
            else data[c]
            for c in columns
        ]
        conn.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            values,
        )
        if isinstance(record, m.ItemRevision) and record.revision > 1:
            self._mark_derived_stale(conn, "item_revision", record.item_id)
        if isinstance(record, m.TranscriptSegment) and record.revision > 1:
            self._mark_derived_stale(conn, "transcript_segment", record.id)
        return record

    @staticmethod
    def _mark_derived_stale(conn: sqlite3.Connection, kind: str, ref_id: str) -> None:
        """Keep packet selection and rendered text. Only the stale flag moves."""

        conn.execute(
            "UPDATE packets SET stale=1 WHERE EXISTS (SELECT 1 FROM json_each(selection) "
            "WHERE json_extract(value,'$.kind')=? AND json_extract(value,'$.id')=?)",
            (kind, ref_id),
        )
        conn.execute(
            "UPDATE drafts SET stale=1 WHERE id IN (SELECT draft_id FROM draft_revisions, "
            "json_each(facts_refs) WHERE json_extract(value,'$.kind')=? "
            "AND json_extract(value,'$.id')=?)",
            (kind, ref_id),
        )

    def append_draft_revision(
        self,
        record: m.DraftRevision,
        *,
        expected_revision: int | None,
        conn: sqlite3.Connection | None = None,
    ) -> m.DraftRevision:
        "Compare-and-swap and append on the caller's transaction; the trigger advances head."
        if conn is None:
            with self.transaction() as tx:
                return self.append_draft_revision(
                    record, expected_revision=expected_revision, conn=tx
                )
        if not conn.in_transaction:
            raise m.CoreError(m.ErrorCode.STORAGE, "Draft append requires a write transaction")
        draft = self.get(m.Draft, record.draft_id, conn=conn)
        exists = conn.execute(
            "SELECT 1 FROM draft_revisions WHERE draft_id=?", (draft.id,)
        ).fetchone()
        if (exists and expected_revision != draft.current_revision) or (
            not exists and expected_revision is not None
        ):
            raise m.CoreError(m.ErrorCode.DATA_STALE, "Draft expected_revision does not match")
        return self.insert(record, conn=conn)

    @staticmethod
    def decode(model: type[T], row: sqlite3.Row) -> T:
        try:
            data = dict(row)
            for key, value in data.items():
                if key in JSON_FIELDS and value is not None:
                    data[key] = json.loads(value)
                elif key in BOOL_FIELDS and value is not None:
                    data[key] = bool(value)
                elif key in ("attempt_error_code", "open_error_code") and value is not None:
                    data[key] = m.ErrorCode(value)
            return model.model_validate(data, context={"stored": True})
        except (ValidationError, ValueError, TypeError):
            raise m.CoreError(m.ErrorCode.STORAGE, "Stored record violates its contract") from None

    def get(
        self,
        model: type[T],
        record_id: str,
        *,
        revision: int | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> T:
        if conn is None:
            with self.connection() as tx:
                return self.get(model, record_id, revision=revision, conn=tx)
        table = MODEL_TABLES[model]
        key = (
            "item_id"
            if model is m.ItemRevision
            else "draft_id"
            if model is m.DraftRevision
            else "id"
        )
        query = f"SELECT * FROM {table} WHERE {key}=?"
        args: list[Any] = [record_id]
        if revision is not None:
            query += " AND revision=?"
            args.append(revision)
        elif model in (m.ItemRevision, m.DraftRevision, m.TranscriptSegment):
            query += " ORDER BY revision DESC"
        row = conn.execute(query, args).fetchone()
        if row is None:
            raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Referenced record does not exist")
        result = self.decode(model, row)
        if isinstance(result, m.Asset) and result.state == "ready":
            self.verify_asset(result)
        return result

    def require_project_asset(
        self, project_id: str, asset_id: str, *, conn: sqlite3.Connection
    ) -> None:
        row = conn.execute(
            "SELECT 1 FROM item_assets ia JOIN items i ON i.id=ia.item_id "
            "WHERE i.project_id=? AND ia.asset_id=? "
            "UNION ALL SELECT 1 FROM source_assets WHERE project_id=? AND asset_id=? LIMIT 1",
            (project_id, asset_id, project_id, asset_id),
        ).fetchone()
        if not row:
            raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Asset is outside project selection")
        self.get(m.Asset, asset_id, conn=conn)

    def validate_evidence(
        self,
        project_id: str,
        refs: list[m.EvidenceRef],
        *,
        conn: sqlite3.Connection,
        source_only: bool = False,
    ) -> None:
        for ref in refs:
            if ref.kind == "item_revision":
                self.get(m.ItemRevision, ref.id, revision=ref.revision, conn=conn)
                owner = self.get(m.Item, ref.id, conn=conn).project_id
            elif ref.kind == "draft_revision" and not source_only:
                self.get(m.DraftRevision, ref.id, revision=ref.revision, conn=conn)
                owner = self.get(m.Draft, ref.id, conn=conn).project_id
            elif ref.kind == "transcript_segment":
                segment = self.get(m.TranscriptSegment, ref.id, revision=ref.revision, conn=conn)
                owner = segment.project_id
                if segment.asset_id is not None:
                    self.require_project_asset(owner, segment.asset_id, conn=conn)
            else:
                raise m.CoreError(
                    m.ErrorCode.REFERENCE_INVALID, "Packet requires original evidence"
                )
            if owner != project_id:
                raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Evidence is outside project")

    def mutate_once(
        self,
        conn: sqlite3.Connection,
        operation: str,
        request_key: str,
        payload: Any,
        mutation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        digest = m.payload_digest(payload)
        row = conn.execute(
            "SELECT * FROM request_receipts WHERE operation=? AND request_key=?",
            (operation, request_key),
        ).fetchone()
        if row:
            if row["payload_digest"] != digest:
                raise m.CoreError(
                    m.ErrorCode.IDEMPOTENCY_CONFLICT, "Request key has a different payload"
                )
            try:
                return json.loads(row["response"])
            except (ValueError, TypeError):
                raise m.CoreError(m.ErrorCode.STORAGE, "Stored receipt is invalid") from None
        result = mutation()
        conn.execute(
            "INSERT INTO request_receipts VALUES(?,?,?,?)",
            (operation, request_key, digest, m.canonical_json(result).decode()),
        )
        return result

    def event(
        self,
        conn: sqlite3.Connection,
        kind: str,
        *,
        job: m.Job | None = None,
        object_id: str | None = None,
        project_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if job is not None and project_id is not None and job.project_id != project_id:
            raise m.CoreError(m.ErrorCode.REFERENCE_INVALID, "Event project does not match its job")
        self.insert(
            m.Event(
                id=m.new_id("evt"),
                object_id=object_id,
                job_id=job.id if job else None,
                project_id=job.project_id if job else project_id,
                kind=kind,
                detail=detail or {},
                created_at=self.clock(),
            ),
            conn=conn,
        )

    def verify_asset(self, asset: m.Asset) -> Path:
        return _verify_object(self.paths.root, asset.model_dump(mode="json"))

    def put_object(
        self,
        stream: BinaryIO,
        *,
        mime: str,
        max_bytes: int = 536870912,
        duration_ms: int | None = None,
    ) -> m.Asset:
        self._check_restore_marker()
        digest = hashlib.sha256()
        size = 0
        fd, name = tempfile.mkstemp(dir=self.paths.root / "staging", prefix="object-")
        staging = Path(name)
        try:
            with os.fdopen(fd, "wb") as output:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Object exceeds byte limit")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            sha = digest.hexdigest()
            target = self.paths.object_path(sha)
            private_dir(target.parent)
            # Only measured/hash-verified staged bytes may repair a corrupt occupant.
            if not target.exists() or _hash_file(target) != sha:
                os.replace(staging, target)
                target.chmod(0o600)
                _fsync_dir(target.parent)
            asset = m.Asset(
                id=m.new_id("ast"),
                sha256=sha,
                mime=mime,
                actual_bytes=size,
                duration_ms=duration_ms,
                object_path=str(target.relative_to(self.paths.root)),
                state="ready",
            )
            with self.transaction() as conn:
                row = conn.execute("SELECT * FROM assets WHERE sha256=?", (sha,)).fetchone()
                if row:
                    result = self.decode(m.Asset, row)
                    if result.mime != mime:
                        raise m.CoreError(
                            m.ErrorCode.INPUT_INVALID,
                            "Object MIME disagrees with its existing classification",
                        )
                    self.verify_asset(result)
                    return result
                return self.insert(asset, conn=conn)
        finally:
            staging.unlink(missing_ok=True)

    def _backup(
        self, source: sqlite3.Connection, destination: Path, *, _pre_upgrade: bool = False
    ) -> BackupResult:
        destination = WorkspacePaths.resolve(destination).root
        if _pre_upgrade:
            if destination.parent != self.paths.root / "backups" or not destination.name.startswith(
                "pre_upgrade_"
            ):
                raise m.CoreError(
                    m.ErrorCode.INPUT_INVALID, "Invalid pre-upgrade backup destination"
                )
        else:
            _reject_overlap(
                self.paths.root, destination, "Backup destination must not overlap workspace"
            )
        if destination.is_symlink() or destination.exists():
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Backup destination must be fresh")
        try:
            destination.mkdir(mode=0o700, parents=True)
        except FileExistsError:
            raise m.CoreError(
                m.ErrorCode.INPUT_INVALID, "Backup destination must be fresh"
            ) from None
        try:
            return self._write_backup(source, destination)
        except BaseException:
            shutil.rmtree(destination)
            raise

    def _write_backup(self, source: sqlite3.Connection, destination: Path) -> BackupResult:
        db = destination / "state.sqlite3"
        private_file(db)
        with closing(sqlite3.connect(db)) as target:
            _configure(target, writable=False)
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
        with _archive_connection(db) as snapshot:
            inventory = _inventory(snapshot)
        for obj in inventory.objects:
            path = _verify_object(self.paths.root, obj)
            copied = destination / obj["object_path"]
            private_dir(copied.parent)
            _copy_private_file(path, copied)
            _verify_object(destination, obj)
        entries = []
        for export in inventory.exports:
            state = export["state"]
            if state == "ready":
                try:
                    _copy_export(export, self.paths.root, destination)
                except m.CoreError as exc:
                    if exc.detail.code != m.ErrorCode.RESOURCE_UNAVAILABLE:
                        raise
                    state = "unavailable"
                    partial = destination / export["artifact"]
                    if partial.is_dir() and not partial.is_symlink():
                        shutil.rmtree(partial)
            entries.append(_export_manifest_entry(export, state=state))
        unavailable = sum(entry["state"] == "unavailable" for entry in entries)
        messages = (
            [f"{unavailable} export artifacts unavailable; backup is partial"]
            if unavailable
            else []
        )
        manifest = {
            "schema_version": inventory.version,
            "database": {"sha256": _hash_file(db), "actual_bytes": db.stat().st_size},
            "objects": [
                {"sha256": obj["sha256"], "actual_bytes": obj["actual_bytes"]}
                for obj in inventory.objects
            ],
            "exports": entries,
            "status": "partial" if unavailable else "ok",
            "warnings": messages,
        }
        file = destination / "manifest.json"
        private_file(file)
        file.write_bytes(m.canonical_json(manifest))
        _fsync_file(db)
        _fsync_file(file)
        for directory in sorted(
            (p for p in destination.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            _fsync_dir(directory)
        _fsync_dir(destination)
        _fsync_dir(destination.parent)
        # Validate the archive we actually copied, including every directory member.
        _verified_backup(destination, max_version=self.schema_version, migrations=self._migrations)
        return BackupResult(
            destination,
            "partial" if unavailable else "complete",
            tuple(entry["id"] for entry in entries if entry["state"] == "unavailable"),
            tuple(messages),
        )

    def backup(self, destination: Path) -> BackupResult:
        """Structured artifact availability; Python warnings are only a secondary signal."""
        with self.connection() as conn:
            result = self._backup(conn, destination)
        _warn_backup(result, stacklevel=3)
        return result

    def pre_upgrade_backups(self) -> list[dict[str, Any]]:
        path = self.paths.runtime / "pre_upgrade_backups.json"
        if not path.exists():
            return []
        try:
            _regular_file(path)
            return json.loads(path.read_bytes())
        except (OSError, ValueError, TypeError):
            raise m.CoreError(m.ErrorCode.STORAGE, "Upgrade backup status is unavailable") from None

    def _record_upgrade_backup(self, result: BackupResult) -> None:
        records = self.pre_upgrade_backups()
        records.append(
            {
                "path": str(result.path.relative_to(self.paths.root)),
                "status": result.status,
                "unavailable_export_ids": list(result.unavailable_export_ids),
                "warnings": list(result.warnings),
            }
        )
        target = self.paths.runtime / "pre_upgrade_backups.json"
        staged = self.paths.runtime / "pre_upgrade_backups.new"
        private_file(staged)
        staged.write_bytes(m.canonical_json(records[-100:]))
        _fsync_file(staged)
        os.replace(staged, target)
        _fsync_dir(self.paths.runtime)

    def verify_export(self, export: m.Export) -> Path:
        return _verify_export(export.model_dump(mode="json"), self.paths.root)

    @classmethod
    def restore(cls, backup: Path, paths: WorkspacePaths) -> RestoreResult:
        try:
            raw = Path(backup).expanduser()
            if raw.is_symlink():
                raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Backup root must not be a symlink")
            return cls._restore(raw.resolve(), WorkspacePaths.resolve(paths.root))
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            raise m.CoreError(m.ErrorCode.STORAGE, "Restore verification failed") from None

    @classmethod
    def _restore(cls, backup: Path, paths: WorkspacePaths) -> RestoreResult:
        _reject_overlap(backup, paths.root, "Restore destination must not overlap backup")
        if paths.root.exists() and any(paths.root.iterdir()):
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Restore requires a fresh workspace")
        verified = _verified_backup(backup, max_version=SCHEMA_VERSION)
        inventory, states = verified.inventory, verified.export_states
        marker = paths.runtime / "restore.incomplete"
        try:
            # Create and fsync the blocker before any general initialization can fail.
            _write_restore_marker(paths)
            paths.initialize()
            _copy_private_file(backup / "state.sqlite3", paths.database)
            if _hash_file(paths.database) != verified.database_digest:
                raise m.CoreError(
                    m.ErrorCode.STORAGE, "Copied database differs from verified backup"
                )
            for obj in inventory.objects:
                target = paths.root / obj["object_path"]
                private_dir(target.parent)
                _copy_private_file(backup / obj["object_path"], target)
            for export in inventory.exports:
                if states[export["id"]] == "ready":
                    _copy_export(export, backup, paths.root)
            # Verify the actual copy at its old schema, using raw SQL, before migration.
            with _archive_connection(paths.database) as conn:
                _verify_schema(conn, inventory.version, MIGRATIONS)
                if _inventory(conn) != inventory:
                    raise m.CoreError(m.ErrorCode.STORAGE, "Copied database inventory mismatch")
            _verify_artifacts(paths.root, inventory, states)
            # Clear stale ownership and persist known artifact loss before pre-upgrade backup.
            if inventory.version:
                with closing(sqlite3.connect(paths.database, isolation_level=None)) as conn:
                    _configure(conn, writable=False)
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        conn.execute("UPDATE worker_state SET owner_nonce=NULL,heartbeat=NULL")
                        for export in inventory.exports:
                            if states[export["id"]] == "unavailable":
                                conn.execute(
                                    "UPDATE exports SET state='unavailable' WHERE id=?",
                                    (export["id"],),
                                )
                        conn.commit()
                    except BaseException:
                        conn.rollback()
                        raise
            store = cls(paths, _restore_in_progress=True)
            with store.connection() as conn:
                actual = _inventory(conn)
                if actual.objects != inventory.objects or actual.exports != tuple(
                    {**export, "state": states[export["id"]]} for export in inventory.exports
                ):
                    raise m.CoreError(m.ErrorCode.STORAGE, "Restored database inventory mismatch")
                _check_integrity(conn)
            with file_lock(paths.runtime / "maintenance.lock"):
                if not _restore_marker_present(paths):
                    raise m.CoreError(m.ErrorCode.STORAGE, "Restore blocker disappeared")
                _verify_artifacts(paths.root, actual, states)
                marker.unlink()
                try:
                    _fsync_dir(paths.runtime)
                except BaseException:
                    _write_restore_marker(paths)
                    raise
                store._restore_in_progress = False
            unavailable = tuple(key for key, state in states.items() if state == "unavailable")
            result = RestoreResult(
                paths.root,
                "partial" if unavailable else "complete",
                unavailable,
                (f"{len(unavailable)} export artifacts unavailable; restore is partial",)
                if unavailable
                else (),
                store,
            )
            _warn_backup(result, stacklevel=4)
            return result
        except BaseException as exc:
            # A failed unlink fsync must also leave the workspace blocked.
            if not _restore_marker_present(paths):
                try:
                    _write_restore_marker(paths)
                except OSError:
                    pass
            if isinstance(exc, Exception):
                raise m.CoreError(
                    m.ErrorCode.STORAGE,
                    "Restore failed; target workspace is incomplete",
                    next_action="Restore the verified backup into a fresh workspace",
                ) from None
            raise


@dataclass(frozen=True)
class BackupResult:
    path: Path
    status: Literal["complete", "partial"]
    unavailable_export_ids: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class RestoreResult(BackupResult):
    store: Store


def _warn_backup(result: BackupResult, *, stacklevel: int) -> None:
    for message in result.warnings:
        warnings.warn(message, BackupWarning, stacklevel=stacklevel)


def _fsync_file(path: Path) -> None:
    _regular_file(path)
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


class BackupWarning(UserWarning):
    """A completed archive explicitly records unavailable export artifacts."""


@dataclass(frozen=True)
class _Inventory:
    version: int
    objects: tuple[dict[str, Any], ...]
    exports: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class _VerifiedBackup:
    inventory: _Inventory
    database_digest: str
    export_states: dict[str, str]


def _schema_signature(conn: sqlite3.Connection) -> frozenset[tuple[str, str, str, str]]:
    return frozenset(
        (row[0], row[1], row[2], re.sub(r"\s+", " ", row[3].strip()) if row[3] else "")
        for row in conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT GLOB 'sqlite_*' AND type IN ('table','index','trigger','view')"
        )
    )


@lru_cache(maxsize=16)
def _expected_schema(scripts: tuple[str, ...]) -> frozenset[tuple[str, str, str, str]]:
    with closing(sqlite3.connect(":memory:", isolation_level=None)) as conn:
        _configure(conn, writable=False)
        conn.create_function("metamuse_now", 0, utc_now)
        conn.execute("PRAGMA foreign_keys=OFF")
        retained: set[tuple[str, str]] = set()
        for script in scripts:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in _statements(script):
                    conn.execute(statement)
                signature = _schema_signature(conn)
                tables = {name for kind, name, _, _ in signature if kind == "table"}
                guards = {(name, table) for kind, name, table, _ in signature if kind == "trigger"}
                if {(name, table) for name, table in retained if table in tables} - guards:
                    raise m.CoreError(m.ErrorCode.STORAGE, "Migration drops required schema guards")
                retained = guards
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        return _schema_signature(conn)


def _verify_schema(conn: sqlite3.Connection, version: int, migrations: dict[int, Path]) -> None:
    try:
        expected = _expected_schema(tuple(migrations[n].read_text() for n in range(1, version + 1)))
    except (KeyError, OSError, sqlite3.Error, ValueError):
        raise m.CoreError(
            m.ErrorCode.STORAGE, "Canonical migration schema is unavailable"
        ) from None
    if _schema_signature(conn) != expected:
        raise m.CoreError(
            m.ErrorCode.PROTOCOL_MISMATCH,
            "Database schema differs from canonical migrations; create a fresh workspace",
        ) from None


def _inventory(conn: sqlite3.Connection) -> _Inventory:
    """Read stable archive columns at this DB's version, never current Pydantic models."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if not version:
        return _Inventory(0, (), ())
    objects = tuple(
        dict(row)
        for row in conn.execute(
            "SELECT id,sha256,actual_bytes,object_path,state FROM assets "
            "WHERE state='ready' ORDER BY id"
        )
    )
    exports = []
    try:
        for row in conn.execute(
            "SELECT id,project_id,selection,format,manifest,artifact,state FROM exports "
            "WHERE state IN ('ready','unavailable') ORDER BY id"
        ):
            export = dict(row)
            for field_name in ("selection", "manifest"):
                export[field_name] = json.loads(export[field_name])
            exports.append(export)
    except (ValueError, TypeError):
        raise m.CoreError(m.ErrorCode.STORAGE, "Invalid archived export metadata") from None
    return _Inventory(version, objects, tuple(exports))


@contextmanager
def _archive_connection(database: Path) -> Iterator[sqlite3.Connection]:
    # immutable prevents SQLite from creating journals/sidecars during archive inspection.
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro&immutable=1", uri=True)) as conn:
        _configure(conn, writable=False)
        yield conn


def _check_integrity(conn: sqlite3.Connection) -> None:
    if (
        conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
        or conn.execute("PRAGMA foreign_key_check").fetchone()
    ):
        raise m.CoreError(m.ErrorCode.STORAGE, "Backup integrity check failed")


def _verified_backup(
    root: Path, *, max_version: int, migrations: dict[int, Path] | None = None
) -> _VerifiedBackup:
    if {p.name for p in root.iterdir()} - {"manifest.json", "state.sqlite3", "objects", "exports"}:
        raise m.CoreError(m.ErrorCode.STORAGE, "Backup contains sidecars or unexpected entries")
    _regular_file(root / "manifest.json")
    database = root / "state.sqlite3"
    _regular_file(database)
    manifest = json.loads((root / "manifest.json").read_bytes())
    version = manifest["schema_version"]
    if type(version) is not int or version < 0:
        raise m.CoreError(m.ErrorCode.STORAGE, "Invalid backup schema version")
    if version > max_version:
        raise m.CoreError(m.ErrorCode.PROTOCOL_MISMATCH, "Backup schema is newer than this build")
    digest = _hash_file(database)
    if manifest.get("database") != {
        "sha256": digest,
        "actual_bytes": database.stat().st_size,
    }:
        raise m.CoreError(m.ErrorCode.STORAGE, "Backup database digest mismatch")
    with _archive_connection(database) as conn:
        _verify_schema(conn, version, MIGRATIONS if migrations is None else migrations)
        _check_integrity(conn)
        inventory = _inventory(conn)
    if inventory.version != version:
        raise m.CoreError(m.ErrorCode.PROTOCOL_MISMATCH, "Backup schema mismatch")
    if manifest["objects"] != [
        {"sha256": obj["sha256"], "actual_bytes": obj["actual_bytes"]} for obj in inventory.objects
    ]:
        raise m.CoreError(m.ErrorCode.STORAGE, "Backup object manifest mismatch")
    entries = manifest.get("exports", [])
    if len(entries) != len(inventory.exports):
        raise m.CoreError(m.ErrorCode.STORAGE, "Backup export manifest mismatch")
    states = {}
    for export, entry in zip(inventory.exports, entries, strict=True):
        state = entry.get("state", "ready")
        if state not in ("ready", "unavailable") or (
            export["state"] == "unavailable" and state != "unavailable"
        ):
            raise m.CoreError(m.ErrorCode.STORAGE, "Invalid archived export state")
        expected = _export_manifest_entry(export, state=state)
        if {**entry, "state": state} != expected:
            raise m.CoreError(m.ErrorCode.STORAGE, "Backup export manifest mismatch")
        states[export["id"]] = state
    partial = any(state == "unavailable" for state in states.values())
    if manifest.get("status", "partial" if partial else "ok") != ("partial" if partial else "ok"):
        raise m.CoreError(m.ErrorCode.STORAGE, "Backup status mismatch")
    _verify_artifacts(root, inventory, states)
    if _hash_file(database) != digest:
        raise m.CoreError(m.ErrorCode.STORAGE, "Backup changed during verification")
    return _VerifiedBackup(inventory, digest, states)


def _verify_artifacts(root: Path, inventory: _Inventory, states: dict[str, str]) -> None:
    expected_objects = {Path(obj["object_path"]) for obj in inventory.objects}
    for obj in inventory.objects:
        _verify_object(root, obj)
    object_root = root / "objects"
    if object_root.is_symlink():
        raise m.CoreError(m.ErrorCode.STORAGE, "Invalid backup object directory")
    if object_root.exists():
        if not object_root.is_dir():
            raise m.CoreError(m.ErrorCode.STORAGE, "Invalid backup object directory")
        expected_dirs = {Path("objects"), Path("objects/sha256")}
        for path in expected_objects:
            expected_dirs.update(path.parents)
        for path in object_root.rglob("*"):
            relative = path.relative_to(root)
            if path.is_symlink() or path.resolve() != path:
                raise m.CoreError(m.ErrorCode.STORAGE, "Linked backup object entry")
            if path.is_dir():
                if relative not in expected_dirs:
                    raise m.CoreError(m.ErrorCode.STORAGE, "Unlisted backup object directory")
            elif relative not in expected_objects:
                raise m.CoreError(m.ErrorCode.STORAGE, "Unlisted backup object file")
    ready_exports = [export for export in inventory.exports if states[export["id"]] == "ready"]
    export_root = root / "exports"
    if export_root.is_symlink() or (
        export_root.exists()
        and (
            not export_root.is_dir()
            or {p.name for p in export_root.iterdir()} != {e["id"] for e in ready_exports}
        )
    ):
        raise m.CoreError(m.ErrorCode.STORAGE, "Backup contains unexpected export artifacts")
    for export in ready_exports:
        _verify_export(export, root)


def _reject_overlap(first: Path, second: Path, message: str) -> None:
    if first == second or first in second.parents or second in first.parents:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, message)


def _restore_marker_present(paths: WorkspacePaths) -> bool:
    marker = paths.runtime / "restore.incomplete"
    return marker.is_symlink() or marker.exists()


def _write_restore_marker(paths: WorkspacePaths) -> None:
    private_dir(paths.runtime)
    marker = paths.runtime / "restore.incomplete"
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(b"Restore in progress; do not use this workspace.\n")
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_dir(paths.runtime)


def _regular_file(path: Path) -> None:
    if path.is_symlink() or path.resolve() != path:
        raise m.CoreError(m.ErrorCode.RESOURCE_UNAVAILABLE, "Linked archive file is unavailable")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise m.CoreError(
            m.ErrorCode.RESOURCE_UNAVAILABLE, "Archive file is not a private regular file"
        )


def _copy_private_file(source: Path, target: Path) -> None:
    _regular_file(source)
    shutil.copyfile(source, target)
    target.chmod(0o600)
    _regular_file(target)
    with target.open("rb") as stream:
        os.fsync(stream.fileno())


def _verify_object(root: Path, obj: dict[str, Any]) -> Path:
    sha = obj["sha256"]
    relative = Path("objects/sha256") / sha[:2] / sha
    if re.fullmatch(r"[0-9a-f]{64}", sha, flags=re.ASCII) is None or obj["object_path"] != str(
        relative
    ):
        raise m.CoreError(m.ErrorCode.STORAGE, "Invalid object path")
    path = root / relative
    try:
        _regular_file(path)
        if path.stat().st_size != obj["actual_bytes"] or _hash_file(path) != sha:
            raise m.CoreError(m.ErrorCode.STORAGE, "Object size/hash mismatch")
    except OSError:
        raise m.CoreError(m.ErrorCode.RESOURCE_UNAVAILABLE, "Ready object is missing") from None
    return path


def _export_manifest_entry(export: dict[str, Any], *, state: str) -> dict[str, Any]:
    return {
        "id": export["id"],
        "artifact": export["artifact"],
        "state": state,
        "manifest_sha256": hashlib.sha256(m.canonical_json(export["manifest"]) + b"\n").hexdigest(),
    }


def _verify_export(export: dict[str, Any], root: Path) -> Path:
    if (
        export["state"] != "ready"
        or re.fullmatch(r"[A-Za-z0-9_-]{3,128}", export["id"], flags=re.ASCII) is None
        or export["artifact"] != f"exports/{export['id']}"
    ):
        raise m.CoreError(m.ErrorCode.STORAGE, "Invalid stored export artifact")
    directory = root / export["artifact"]
    unavailable = m.CoreError(
        m.ErrorCode.RESOURCE_UNAVAILABLE,
        "Export artifact is unavailable or changed",
        next_action="Restore a verified backup containing the artifact, or use a new request key",
    )
    try:
        if directory.is_symlink() or directory.resolve() != directory or not directory.is_dir():
            raise unavailable
        manifest = export["manifest"]
        if (
            manifest["export_id"] != export["id"]
            or manifest["format"] != export["format"]
            or manifest["selection"] != export["selection"]
            or not isinstance(manifest["files"], list)
        ):
            raise ValueError
        manifest_path = directory / "manifest.json"
        expected_manifest = m.canonical_json(manifest) + b"\n"
        _regular_file(manifest_path)
        if (
            manifest_path.stat().st_size != len(expected_manifest)
            or manifest_path.read_bytes() != expected_manifest
        ):
            raise unavailable
        names = {"manifest.json"}
        for entry in manifest["files"]:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"name", "sha256", "bytes"}
                or not isinstance(entry["name"], str)
                or re.fullmatch(
                    r"(?:records\.(?:csv|jsonl)|object-\d{4,}\.(?:txt|md))",
                    entry["name"],
                    flags=re.ASCII,
                )
                is None
                or entry["name"] in names
                or not isinstance(entry["sha256"], str)
                or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"], flags=re.ASCII) is None
                or type(entry["bytes"]) is not int
                or entry["bytes"] < 0
            ):
                raise ValueError
            path = directory / entry["name"]
            _regular_file(path)
            if path.stat().st_size != entry["bytes"] or _hash_file(path) != entry["sha256"]:
                raise unavailable
            names.add(entry["name"])
        if {path.name for path in directory.iterdir()} != names:
            raise unavailable
        return directory
    except OSError:
        raise unavailable from None
    except (ValueError, KeyError, TypeError):
        raise m.CoreError(m.ErrorCode.STORAGE, "Invalid stored export manifest") from None


def _copy_export(export: dict[str, Any], source_root: Path, target_root: Path) -> None:
    source = _verify_export(export, source_root)
    target = private_dir(target_root / export["artifact"])
    for name in ["manifest.json", *(entry["name"] for entry in export["manifest"]["files"])]:
        _copy_private_file(source / name, target / name)
    _verify_export(export, target_root)


def _hash_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _configure(conn: sqlite3.Connection, *, writable: bool = True) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA recursive_triggers=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    if writable:
        conn.execute("PRAGMA journal_mode=WAL")
    else:
        conn.execute("PRAGMA journal_mode")  # inspect only; never modify a newer schema
    conn.execute("PRAGMA synchronous=FULL")
