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
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, TypeVar

from pydantic import ValidationError

from metamuse import models as m
from metamuse.config import load_config
from metamuse.paths import WorkspacePaths, private_dir, private_file

SCHEMA_VERSION = 1
DATA = Path(__file__).parent / "data"
MIGRATIONS = {1: DATA / "migrations" / "001_initial.sql"}
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
    "transcript_segments": m.TranscriptSegment,
    "packets": m.Packet,
    "permits": m.Permit,
    "discussions": m.Discussion,
    "exchanges": m.Exchange,
    "messages": m.Message,
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
}
BOOL_FIELDS = {"cancel_requested", "complete", "stale", "revoked"}
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
        except BlockingIOError as exc:
            raise m.CoreError(m.ErrorCode.JOB_BUSY, "Workspace lock is held") from exc
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
        if (paths.runtime / "restore.incomplete").exists() and not _restore_in_progress:
            raise m.CoreError(
                m.ErrorCode.STORAGE,
                "Workspace restore is incomplete",
                next_action="Restore the verified backup into a fresh workspace",
            )
        self.paths = paths
        self.clock = clock
        self._restore_in_progress = _restore_in_progress
        self.schema_version = SCHEMA_VERSION if schema_version is None else schema_version
        self.paths.initialize()
        self.migrate(migrations or MIGRATIONS)
        self._provision_workspace_keys()

    def _open_database(self) -> sqlite3.Connection:
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
        except sqlite3.Error as exc:
            raise m.CoreError(m.ErrorCode.STORAGE, "Cannot open workspace database") from exc

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        if (self.paths.runtime / "restore.incomplete").exists() and not self._restore_in_progress:
            raise m.CoreError(m.ErrorCode.STORAGE, "Workspace restore is incomplete")
        with file_lock(self.paths.runtime / "maintenance.lock", exclusive=False):
            conn = self._open()
            try:
                if conn.execute("PRAGMA user_version").fetchone()[0] != self.schema_version:
                    raise m.CoreError(m.ErrorCode.PROTOCOL_MISMATCH, "Database requires migration")
                yield conn
            except sqlite3.Error as exc:
                raise m.CoreError(m.ErrorCode.STORAGE, "SQLite operation failed") from exc
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
                except sqlite3.IntegrityError as exc:
                    conn.rollback()
                    raise m.CoreError(
                        m.ErrorCode.REFERENCE_INVALID, "Database reference or constraint rejected"
                    ) from exc
                except BaseException:
                    conn.rollback()
                    raise
        finally:
            _ACTIVE_WRITES.roots = active

    def migrate(self, migrations: dict[int, Path]) -> None:
        with file_lock(self.paths.runtime / "maintenance.lock"):
            had_database = self.paths.database.exists() and self.paths.database.stat().st_size > 0
            conn = self._open()
            try:
                old_version = conn.execute("PRAGMA user_version").fetchone()[0]
                # Pre-release schema-1 workspaces can predate the key-table amendment.
                key_amendment = (
                    old_version >= 1
                    and not conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='workspace_keys'"
                    ).fetchone()
                )
                if old_version == self.schema_version and not key_amendment:
                    return
                with file_lock(self.paths.worker_lock, blocking=False):
                    if had_database:
                        self._backup(conn, self.paths.root / "backups" / m.new_id("pre_upgrade"))
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        if key_amendment:
                            # Read the authoritative 001 statements, never a second DDL copy.
                            for statement in _statements(MIGRATIONS[1].read_text()):
                                if re.match(
                                    r"CREATE (?:TABLE workspace_keys|UNIQUE INDEX workspace_key_|"
                                    r"TRIGGER workspace_key_)",
                                    statement.lstrip(),
                                ):
                                    conn.execute(statement)
                        for version in range(old_version + 1, self.schema_version + 1):
                            if version not in migrations:
                                raise m.CoreError(m.ErrorCode.STORAGE, "Missing migration")
                            for statement in _statements(migrations[version].read_text()):
                                conn.execute(statement)
                            conn.execute(f"PRAGMA user_version={version}")
                        if conn.execute("PRAGMA foreign_key_check").fetchone():
                            raise m.CoreError(m.ErrorCode.STORAGE, "Migration FK check failed")
                        conn.commit()
                    except BaseException:
                        conn.rollback()
                        raise
            except (sqlite3.Error, ValueError) as exc:
                raise m.CoreError(
                    m.ErrorCode.STORAGE, "Migration failed; original data retained"
                ) from exc
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
        """Retained active/retired keys; read-only, including legacy cursor material."""
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
            legacy = conn.execute(
                "SELECT response FROM request_receipts "
                "WHERE operation='content.cursor_key' AND request_key='v1'"
            ).fetchone()
            if legacy:
                try:
                    material = bytes.fromhex(json.loads(legacy[0])["key"])
                    if len(material) != 32:
                        raise ValueError
                except (ValueError, KeyError, TypeError) as exc:
                    raise m.CoreError(
                        m.ErrorCode.STORAGE, "Legacy cursor signing key is unavailable"
                    ) from exc
                rows = conn.execute(
                    "SELECT * FROM workspace_keys WHERE name=?", (CONTENT_CURSOR_KEY,)
                ).fetchall()
                if not any(row["key_material"] == material for row in rows):
                    conn.execute(
                        "INSERT INTO workspace_keys VALUES(?,?,?,?,?)",
                        (
                            CONTENT_CURSOR_KEY,
                            max((row["version"] for row in rows), default=0) + 1,
                            material,
                            self.clock(),
                            "retired" if rows else "active",
                        ),
                    )
                conn.execute(
                    "DELETE FROM request_receipts "
                    "WHERE operation='content.cursor_key' AND request_key='v1'"
                )
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
                except ValueError as exc:
                    raise m.CoreError(
                        m.ErrorCode.LIMIT_REACHED, "Packet UTF-8 byte limit exceeded"
                    ) from exc
            self.validate_evidence(
                project, record.selection, conn=conn, source_only=isinstance(record, m.Packet)
            )
        if isinstance(record, m.DraftRevision):
            draft = self.get(m.Draft, record.draft_id, conn=conn)
            self.validate_evidence(draft.project_id, record.facts_refs, conn=conn)
            for asset_id in record.assets:
                self.require_project_asset(draft.project_id, asset_id, conn=conn)
        if isinstance(record, m.Asset) and record.state == "ready":
            self.verify_asset(record)
        columns = list(data)
        values = [
            m.canonical_json(data[c]).decode()
            if c in JSON_FIELDS and data[c] is not None
            else int(data[c])
            if c in BOOL_FIELDS
            else data[c]
            for c in columns
        ]
        conn.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            values,
        )
        if isinstance(record, m.ItemRevision) and record.revision > 1:
            # Retain fixed packet selection; new observations mark dependent views stale.
            conn.execute(
                "UPDATE packets SET stale=1 WHERE EXISTS (SELECT 1 FROM json_each(selection) "
                "WHERE json_extract(value,'$.kind')='item_revision' "
                "AND json_extract(value,'$.id')=?)",
                (record.item_id,),
            )
            conn.execute(
                "UPDATE drafts SET stale=1 WHERE id IN (SELECT draft_id FROM draft_revisions, "
                "json_each(facts_refs) WHERE json_extract(value,'$.kind')='item_revision' "
                "AND json_extract(value,'$.id')=?)",
                (record.item_id,),
            )
        return record

    def append_draft_revision(
        self,
        record: m.DraftRevision,
        *,
        expected_revision: int | None,
        conn: sqlite3.Connection | None = None,
    ) -> m.DraftRevision:
        """Compare-and-swap and append on the caller's transaction; the trigger advances head."""
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
                elif key in BOOL_FIELDS:
                    data[key] = bool(value)
            return model.model_validate(data, context={"stored": True})
        except (ValidationError, ValueError, TypeError) as exc:
            raise m.CoreError(m.ErrorCode.STORAGE, "Stored record violates its contract") from exc

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
            "WHERE i.project_id=? AND ia.asset_id=?",
            (project_id, asset_id),
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
                self.require_project_asset(project_id, segment.asset_id, conn=conn)
                owner = project_id
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
            return json.loads(row["response"])
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
        path = self.paths.object_path(asset.sha256)
        if asset.object_path != str(path.relative_to(self.paths.root)) or not path.is_file():
            raise m.CoreError(m.ErrorCode.RESOURCE_UNAVAILABLE, "Ready object is missing")
        if path.stat().st_size != asset.actual_bytes:
            raise m.CoreError(m.ErrorCode.STORAGE, "Object size mismatch")
        if _hash_file(path) != asset.sha256:
            raise m.CoreError(m.ErrorCode.STORAGE, "Object hash mismatch")
        return path

    def put_object(
        self,
        stream: BinaryIO,
        *,
        mime: str,
        max_bytes: int = 536870912,
        duration_ms: int | None = None,
    ) -> m.Asset:
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

    def _backup(self, source: sqlite3.Connection, destination: Path) -> Path:
        destination = WorkspacePaths.resolve(destination).root
        if destination.exists():
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Backup destination must be fresh")
        try:
            destination.mkdir(mode=0o700, parents=True)
        except FileExistsError as exc:
            raise m.CoreError(
                m.ErrorCode.INPUT_INVALID, "Backup destination must be fresh"
            ) from exc
        try:
            return self._write_backup(source, destination)
        except BaseException:
            shutil.rmtree(destination)
            raise

    def _write_backup(self, source: sqlite3.Connection, destination: Path) -> Path:
        db = destination / "state.sqlite3"
        private_file(db)
        with closing(sqlite3.connect(db)) as target:
            _configure(target)
            source.backup(target)
        # Object manifest is selected from the backup, not a later live snapshot.
        with closing(sqlite3.connect(db)) as snapshot:
            _configure(snapshot)
            version = snapshot.execute("PRAGMA user_version").fetchone()[0]
            objects = []
            exports = []
            if version:
                for row in snapshot.execute("SELECT * FROM assets WHERE state='ready'"):
                    asset = self.decode(m.Asset, row)
                    path = self.verify_asset(asset)
                    relative = path.relative_to(self.paths.root)
                    copied = destination / relative
                    private_dir(copied.parent)
                    shutil.copyfile(path, copied)
                    copied.chmod(0o600)
                    objects.append({"sha256": asset.sha256, "actual_bytes": asset.actual_bytes})
                for row in snapshot.execute(
                    "SELECT * FROM exports WHERE state='ready' ORDER BY id"
                ):
                    export = self.decode(m.Export, row)
                    _copy_export(export, self.paths.root, destination)
                    exports.append(_export_manifest_entry(export))
        manifest = {"schema_version": version, "objects": objects, "exports": exports}
        file = destination / "manifest.json"
        file.write_bytes(m.canonical_json(manifest))
        file.chmod(0o600)
        return destination

    def backup(self, destination: Path) -> Path:
        with self.connection() as conn:
            return self._backup(conn, destination)

    def verify_export(self, export: m.Export) -> Path:
        """Verify ready artifact membership, exact manifest bytes and each size/hash."""
        return _verify_export(export, self.paths.root)

    @classmethod
    def restore(cls, backup: Path, paths: WorkspacePaths) -> Store:
        try:
            return cls._restore(backup.resolve(), paths)
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise m.CoreError(m.ErrorCode.STORAGE, "Restore verification failed") from exc

    @classmethod
    def _restore(cls, backup: Path, paths: WorkspacePaths) -> Store:
        if paths.root.exists() and any(paths.root.iterdir()):
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Restore requires a fresh workspace")
        manifest = json.loads((backup / "manifest.json").read_bytes())
        backup_version = manifest["schema_version"]
        if type(backup_version) is not int or backup_version < 0:
            raise m.CoreError(m.ErrorCode.STORAGE, "Invalid backup schema version")
        if backup_version > SCHEMA_VERSION:
            raise m.CoreError(
                m.ErrorCode.PROTOCOL_MISMATCH, "Backup schema is newer than this build"
            )
        # Verify the backup before writing the new workspace.
        with closing(
            sqlite3.connect(f"{(backup / 'state.sqlite3').as_uri()}?mode=ro", uri=True)
        ) as conn:
            _configure(conn, writable=False)
            if conn.execute("PRAGMA user_version").fetchone()[0] != backup_version:
                raise m.CoreError(m.ErrorCode.PROTOCOL_MISMATCH, "Backup schema mismatch")
            if (
                conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or conn.execute("PRAGMA foreign_key_check").fetchone()
            ):
                raise m.CoreError(m.ErrorCode.STORAGE, "Backup integrity check failed")
            rows = (
                conn.execute(
                    "SELECT sha256,actual_bytes,object_path FROM assets WHERE state='ready'"
                ).fetchall()
                if backup_version
                else []
            )
            exports = (
                [
                    cls.decode(m.Export, row)
                    for row in conn.execute("SELECT * FROM exports WHERE state='ready' ORDER BY id")
                ]
                if backup_version
                else []
            )
        expected = {row["sha256"]: row["actual_bytes"] for row in rows}
        if expected != {obj["sha256"]: obj["actual_bytes"] for obj in manifest["objects"]}:
            raise m.CoreError(m.ErrorCode.STORAGE, "Backup manifest mismatch")
        for row in rows:
            sha = row["sha256"]
            paths.object_path(sha)  # validates opaque hash before constructing a backup path
            relative = Path("objects") / "sha256" / sha[:2] / sha
            if row["object_path"] != str(relative):
                raise m.CoreError(m.ErrorCode.STORAGE, "Backup object path mismatch")
            source = backup / relative
            if source.stat().st_size != row["actual_bytes"] or _hash_file(source) != sha:
                raise m.CoreError(m.ErrorCode.STORAGE, "Restore object hash mismatch")
        if manifest.get("exports", []) != [_export_manifest_entry(export) for export in exports]:
            raise m.CoreError(m.ErrorCode.STORAGE, "Backup export manifest mismatch")
        export_root = backup / "exports"
        if export_root.exists() and (
            export_root.is_symlink()
            or not export_root.is_dir()
            or {path.name for path in export_root.iterdir()} != {export.id for export in exports}
        ):
            raise m.CoreError(m.ErrorCode.STORAGE, "Backup contains unexpected export artifacts")
        for export in exports:
            _verify_export(export, backup)
        paths.initialize()
        # Remains present across errors/crashes; a partially restored target is never opened.
        marker = paths.runtime / "restore.incomplete"
        private_file(marker)
        with marker.open("wb") as stream:
            stream.write(b"Restore in progress; do not use this workspace.\n")
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_dir(paths.runtime)
        try:
            shutil.copyfile(backup / "state.sqlite3", paths.database)
            paths.database.chmod(0o600)
            for row in rows:
                relative = Path(row["object_path"])
                private_dir((paths.root / relative).parent)
                shutil.copyfile(backup / relative, paths.root / relative)
                (paths.root / relative).chmod(0o600)
            for export in exports:
                _copy_export(export, backup, paths.root)
            # Normal guarded open: maintenance + worker locks, pre-upgrade backup, DDL rollback.
            store = cls(paths, _restore_in_progress=True)
            with store.transaction() as conn:
                if (
                    conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                    or conn.execute("PRAGMA foreign_key_check").fetchone()
                ):
                    raise m.CoreError(
                        m.ErrorCode.STORAGE, "Restored database integrity check failed"
                    )
                conn.execute("UPDATE worker_state SET owner_nonce=NULL,heartbeat=NULL")
            _fsync_dir(paths.runtime)
            marker.unlink()
            store._restore_in_progress = False
            return store
        except Exception as exc:
            raise m.CoreError(
                m.ErrorCode.STORAGE,
                "Restore failed; target workspace is incomplete",
                next_action="Restore the verified backup into a fresh workspace",
            ) from exc


def _export_manifest_entry(export: m.Export) -> dict[str, Any]:
    return {
        "id": export.id,
        "artifact": export.artifact,
        "manifest_sha256": hashlib.sha256(m.canonical_json(export.manifest) + b"\n").hexdigest(),
    }


def _verify_export(export: m.Export, root: Path) -> Path:
    if (
        export.state != "ready"
        or re.fullmatch(r"[A-Za-z0-9_-]{3,128}", export.id, flags=re.ASCII) is None
        or export.artifact != f"exports/{export.id}"
    ):
        raise m.CoreError(m.ErrorCode.STORAGE, "Invalid stored export artifact")
    directory = root / export.artifact
    unavailable = m.CoreError(
        m.ErrorCode.RESOURCE_UNAVAILABLE,
        "Export artifact is unavailable or changed",
        next_action="Restore a verified backup containing the artifact, or use a new request key",
    )
    try:
        if directory.resolve() != directory or not directory.is_dir():
            raise unavailable
        manifest = export.manifest
        if (
            manifest["export_id"] != export.id
            or manifest["format"] != export.format
            or manifest["selection"] != [ref.model_dump(mode="json") for ref in export.selection]
            or not isinstance(manifest["files"], list)
        ):
            raise ValueError
        manifest_path = directory / "manifest.json"
        expected_manifest = m.canonical_json(manifest) + b"\n"
        if (
            manifest_path.is_symlink()
            or not manifest_path.is_file()
            or manifest_path.stat().st_size != len(expected_manifest)
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
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size != entry["bytes"]
                or _hash_file(path) != entry["sha256"]
            ):
                raise unavailable
            names.add(entry["name"])
        if {path.name for path in directory.iterdir()} != names:
            raise unavailable
        return directory
    except OSError as exc:
        raise unavailable from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise m.CoreError(m.ErrorCode.STORAGE, "Invalid stored export manifest") from exc


def _copy_export(export: m.Export, source_root: Path, target_root: Path) -> None:
    source = _verify_export(export, source_root)
    target = private_dir(target_root / export.artifact)
    for name in ["manifest.json", *(entry["name"] for entry in export.manifest["files"])]:
        shutil.copyfile(source / name, target / name)
        (target / name).chmod(0o600)
    # Detect changes between source verification and copying before declaring success.
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
    conn.execute("PRAGMA busy_timeout=5000")
    if writable:
        conn.execute("PRAGMA journal_mode=WAL")
    else:
        conn.execute("PRAGMA journal_mode")  # inspect only; never modify a newer schema
    conn.execute("PRAGMA synchronous=FULL")
