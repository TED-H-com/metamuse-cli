import io
import json
import sqlite3
from pathlib import Path

import pytest

from metamuse import app
from metamuse import models as m
from metamuse.paths import WorkspacePaths
from metamuse.store import Store


def source(ctx, project, sid="src_one"):
    return ctx.store.insert(
        m.Source(
            id=sid,
            project_id=project,
            platform="generic",
            collection_method="file_import",
            fetched_at=ctx.clock(),
        )
    )


def item(ctx, project, source_id, iid="itm_one", parent=None):
    return ctx.store.insert(
        m.Item(id=iid, project_id=project, source_id=source_id, kind="comment", parent_id=parent)
    )


def revision(ctx, iid, rev=1):
    return ctx.store.insert(
        m.ItemRevision(
            item_id=iid,
            revision=rev,
            text="繁中😀\n",
            text_hash=m.payload_digest("繁中😀\n"),
            fetched_at=ctx.clock(),
        )
    )


def test_connection_pragmas_and_permissions(ctx):
    with ctx.store.connection() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "packets",
        "permits",
        "discussions",
        "exchanges",
        "messages",
        "drafts",
        "draft_revisions",
        "exports",
        "events",
        "worker_state",
    } <= tables
    for path in (ctx.paths.root, *ctx.paths.root.rglob("*")):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600), path


def test_cross_project_source_parent_and_cycles(ctx, project):
    other = app.create_project(ctx, {"name": "Other", "request_key": "other"}).data["id"]
    s1, s2 = source(ctx, project), source(ctx, other, "src_other")
    with pytest.raises(m.CoreError):
        item(ctx, other, s1.id)
    a = item(ctx, project, s1.id, "itm_a")
    b = item(ctx, project, s1.id, "itm_b", a.id)
    item(ctx, other, s2.id, "itm_c")
    with pytest.raises(m.CoreError):
        item(ctx, project, s1.id, "itm_d", "itm_c")
    with pytest.raises(m.CoreError):
        item(ctx, project, s1.id, "itm_self", "itm_self")
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute("UPDATE items SET parent_id=? WHERE id=?", (b.id, a.id))
    assert ctx.store.get(m.Item, a.id).parent_id is None


def test_revision_history_and_packet_scope(ctx, project):
    s = source(ctx, project)
    i = item(ctx, project, s.id)
    revision(ctx, i.id)
    ref = m.EvidenceRef(kind="item_revision", id=i.id, revision=1)
    packet = m.Packet(
        id="pkt_one",
        project_id=project,
        selection=[ref],
        rendered_text="fixture",
        digest=m.payload_digest("fixture"),
        created_at=ctx.clock(),
    )
    ctx.store.insert(packet)
    revision(ctx, i.id, 2)
    assert ctx.store.get(m.Packet, packet.id).stale
    assert ctx.store.get(m.ItemRevision, i.id, revision=1).text == "繁中😀\n"
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute("UPDATE item_revisions SET text='changed' WHERE item_id=?", (i.id,))
    other = app.create_project(ctx, {"name": "Other", "request_key": "other"}).data["id"]
    with pytest.raises(m.CoreError):
        ctx.store.insert(packet.model_copy(update={"id": "pkt_other", "project_id": other}))
    with pytest.raises(m.CoreError):
        ctx.store.insert(
            packet.model_copy(
                update={"id": "pkt_missing", "selection": [ref.model_copy(update={"revision": 99})]}
            )
        )


def test_objects_dedup_actual_bytes_and_missing(ctx, project):
    s = source(ctx, project)
    i1, i2 = item(ctx, project, s.id), item(ctx, project, s.id, "itm_two")
    revision(ctx, i1.id)
    revision(ctx, i2.id)
    a = ctx.store.put_object(io.BytesIO(b"synthetic media"), mime="application/octet-stream")
    b = ctx.store.put_object(io.BytesIO(b"synthetic media"), mime="application/octet-stream")
    assert a.id == b.id and a.actual_bytes == 15
    ctx.store.insert(m.ItemAsset(item_id=i1.id, revision=1, asset_id=a.id, relation="attached"))
    ctx.store.insert(m.ItemAsset(item_id=i2.id, revision=1, asset_id=a.id, relation="attached"))
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM item_assets").fetchone()[0] == 2
    with pytest.raises(m.CoreError) as error:
        ctx.store.put_object(io.BytesIO(b"too many bytes"), mime="text/plain", max_bytes=2)
    assert error.value.detail.code == m.ErrorCode.LIMIT_REACHED
    assert not list((ctx.paths.root / "staging").iterdir())
    ctx.paths.object_path(a.sha256).unlink()
    with pytest.raises(m.CoreError) as error:
        ctx.store.get(m.Asset, a.id)
    assert error.value.detail.code == m.ErrorCode.RESOURCE_UNAVAILABLE


def test_transcript_sql_constraint(ctx):
    asset = ctx.store.put_object(io.BytesIO(b"fixture"), mime="text/plain")
    with pytest.raises(ValueError):
        m.TranscriptSegment(
            id="seg_one",
            asset_id=asset.id,
            revision=1,
            start_ms=2,
            end_ms=1,
            text="fixture",
            method="provided",
        )
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute(
                "INSERT INTO transcript_segments VALUES(?,?,1,2,1,'fixture','und',"
                "'provided','unreviewed')",
                ("seg_bad", asset.id),
            )


def test_backup_restore_and_hash_integrity(ctx, project, tmp_path):
    a = ctx.store.put_object(io.BytesIO(b"backup fixture"), mime="text/plain")
    backup = ctx.store.backup(tmp_path / "backup")
    restored = Store.restore(backup, WorkspacePaths.resolve(tmp_path / "restored"))
    assert restored.get(m.Project, project).name == "Synthetic project"
    assert restored.get(m.Asset, a.id).sha256 == a.sha256
    with restored.connection() as conn:
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    (backup / a.object_path).write_bytes(b"damaged")
    with pytest.raises(m.CoreError):
        Store.restore(backup, WorkspacePaths.resolve(tmp_path / "invalid"))
    assert not (tmp_path / "invalid").exists()


def test_migration_rollback_backup_and_newer_refusal(ctx, project, tmp_path):
    migration = tmp_path / "002_failure.sql"
    migration.write_text("CREATE TABLE intermediate(id TEXT);\nINSERT INTO missing VALUES(1);\n")
    with pytest.raises(m.CoreError) as error:
        Store(ctx.paths, schema_version=2, migrations={2: migration})
    assert error.value.detail.code == m.ErrorCode.STORAGE
    with ctx.store.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='intermediate'").fetchone()
    assert ctx.store.get(m.Project, project).id == project
    backup = next((ctx.paths.root / "backups").glob("pre_upgrade_*"))
    assert json.loads((backup / "manifest.json").read_text())["schema_version"] == 1
    restored = Store.restore(backup, WorkspacePaths.resolve(tmp_path / "pre_restored"))
    assert restored.get(m.Project, project).id == project
    migration.write_text("CREATE TABLE added(id TEXT);\n")
    newer = Store(ctx.paths, schema_version=2, migrations={2: migration})
    with newer.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    with pytest.raises(m.CoreError) as error:
        Store(ctx.paths)
    assert error.value.detail.code == m.ErrorCode.PROTOCOL_MISMATCH
    with pytest.raises(m.CoreError):
        ctx.store.insert(m.Project(id="prj_stale", name="x", created_at=ctx.clock()))
    with sqlite3.connect(ctx.paths.database) as conn:
        assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] == 1


def test_exchange_project_and_open_constraint(ctx, project):
    s = source(ctx, project)
    i = item(ctx, project, s.id)
    revision(ctx, i.id)
    packet = ctx.store.insert(
        m.Packet(
            id="pkt_one",
            project_id=project,
            selection=[m.EvidenceRef(kind="item_revision", id=i.id, revision=1)],
            rendered_text="fixture",
            digest=m.payload_digest("p"),
            created_at=ctx.clock(),
        )
    )
    permit = ctx.store.insert(
        m.Permit(
            id="pmt_one",
            project_id=project,
            kind="share",
            scope={},
            issuer="fixture",
            issued_at=ctx.clock(),
            expires_at=ctx.clock(),
            max_uses=1,
        )
    )
    discussion = ctx.store.insert(
        m.Discussion(
            id="dsc_one",
            project_id=project,
            provider="fixture",
            auth_ref="fixture",
            topic="fixture",
        )
    )
    exchange = m.Exchange(
        id="exc_one",
        project_id=project,
        discussion_id=discussion.id,
        request_key="one",
        question="fixture",
        packet_id=packet.id,
        digest=m.payload_digest("e"),
        permit_id=permit.id,
    )
    ctx.store.insert(exchange)
    with pytest.raises(m.CoreError):
        ctx.store.insert(exchange.model_copy(update={"id": "exc_two", "request_key": "two"}))
    other = app.create_project(ctx, {"name": "Other", "request_key": "other"}).data["id"]
    other_discussion = ctx.store.insert(
        m.Discussion(
            id="dsc_other",
            project_id=other,
            provider="fixture",
            auth_ref="fixture",
            topic="fixture",
        )
    )
    with pytest.raises(m.CoreError):
        ctx.store.insert(
            exchange.model_copy(
                update={
                    "id": "exc_other",
                    "request_key": "other",
                    "discussion_id": other_discussion.id,
                    "project_id": other,
                }
            )
        )


def test_repository_workspace_rejected():
    with pytest.raises(m.CoreError):
        WorkspacePaths.resolve(Path(__file__).resolve().parents[1] / "runtime-data")


def test_draft_revision_append_only_and_source_only_packet(ctx, project):
    draft = ctx.store.insert(m.Draft(id="drf_one", project_id=project))
    rev = ctx.store.insert(
        m.DraftRevision(
            draft_id=draft.id,
            revision=1,
            text="fixture",
            platform="generic",
            digest=m.payload_digest("fixture"),
        )
    )
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute("UPDATE draft_revisions SET text='changed' WHERE draft_id=?", (draft.id,))
    with pytest.raises(m.CoreError):
        ctx.store.insert(
            m.Packet(
                id="pkt_draft",
                project_id=project,
                selection=[
                    m.EvidenceRef(kind="draft_revision", id=draft.id, revision=rev.revision)
                ],
                rendered_text="fixture",
                digest=m.payload_digest("fixture"),
                created_at=ctx.clock(),
            )
        )


def test_upgrade_refuses_live_executor(ctx, tmp_path):
    from metamuse.jobs import Executor

    migration = tmp_path / "002.sql"
    migration.write_text("CREATE TABLE added(id TEXT);\n")
    with Executor(ctx.store):
        with pytest.raises(m.CoreError) as error:
            Store(ctx.paths, schema_version=2, migrations={2: migration})
        assert error.value.detail.code == m.ErrorCode.JOB_BUSY
    with ctx.store.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_backup_private_files_and_tampered_manifest(ctx, tmp_path):
    ctx.store.put_object(io.BytesIO(b"fixture"), mime="text/plain")
    backup = ctx.store.backup(tmp_path / "backup")
    for path in (backup, *backup.rglob("*")):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    manifest = backup / "manifest.json"
    manifest.write_text('{"schema_version":1,"objects":[]}')
    with pytest.raises(m.CoreError) as error:
        Store.restore(backup, WorkspacePaths.resolve(tmp_path / "restore"))
    assert error.value.detail.code == m.ErrorCode.STORAGE


def test_default_platformdirs_workspace(monkeypatch, tmp_path):
    from metamuse import paths

    monkeypatch.setattr(paths, "user_data_path", lambda *args, **kwargs: tmp_path / "user-data")
    context = app.AppContext.open()
    assert context.paths.root == tmp_path / "user-data/default"
    assert context.paths.database.is_file()


def test_corrupt_json_is_storage_error(ctx, project):
    src = source(ctx, project)
    with ctx.store.transaction() as conn:
        conn.execute("UPDATE sources SET use_basis='invalid json' WHERE id=?", (src.id,))
    with pytest.raises(m.CoreError) as error:
        ctx.store.get(m.Source, src.id)
    assert error.value.detail.code == m.ErrorCode.STORAGE
