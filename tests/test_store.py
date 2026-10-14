import io
import json
import sqlite3
from pathlib import Path

import pytest

from metamuse import app
from metamuse import models as m
from metamuse.paths import WorkspacePaths
from metamuse.store import MIGRATIONS, SCHEMA_VERSION, Store


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


def test_r16_null_item_text_is_sql_null_and_distinct_from_empty(ctx, project):
    src = source(ctx, project, "src_null_text")
    item(ctx, project, src.id, "itm_null_text")
    item(ctx, project, src.id, "itm_empty_text")
    ctx.store.insert(
        m.ItemRevision(
            item_id="itm_null_text",
            revision=1,
            text=None,
            text_hash=m.payload_digest({"text": None}),
            fetched_at=ctx.clock(),
            missing_fields=["text"],
        )
    )
    ctx.store.insert(
        m.ItemRevision(
            item_id="itm_empty_text",
            revision=1,
            text="",
            text_hash=m.payload_digest({"text": ""}),
            fetched_at=ctx.clock(),
            missing_fields=[],
        )
    )
    with ctx.store.connection() as conn:
        absent = conn.execute(
            "SELECT text, missing_fields, text_hash FROM item_revisions WHERE item_id=?",
            ("itm_null_text",),
        ).fetchone()
        blank = conn.execute(
            "SELECT text, missing_fields, text_hash FROM item_revisions WHERE item_id=?",
            ("itm_empty_text",),
        ).fetchone()
        declared = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='item_revisions'"
        ).fetchone()[0]
    assert absent["text"] is None
    assert json.loads(absent["missing_fields"]) == ["text"]
    assert blank["text"] == ""
    assert json.loads(blank["missing_fields"]) == []
    assert absent["text_hash"] != blank["text_hash"]
    assert ctx.store.get(m.ItemRevision, "itm_null_text").text is None
    assert ctx.store.get(m.ItemRevision, "itm_empty_text").text == ""
    assert "text TEXT," in declared or "text TEXT " in declared
    assert "text TEXT NOT NULL" not in declared


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
        "source_assets",
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


def test_transcript_sql_constraint(ctx, project):
    asset = ctx.store.put_object(io.BytesIO(b"fixture"), mime="text/plain")
    with pytest.raises(ValueError):
        m.TranscriptSegment(
            id="seg_one",
            project_id=project,
            asset_id=asset.id,
            revision=1,
            start_ms=2,
            end_ms=1,
            text="fixture",
            method="provided",
        )
    with pytest.raises(ValueError):
        m.TranscriptSegment(
            id="seg_loose",
            project_id=project,
            revision=1,
            start_ms=0,
            end_ms=1,
            text="fixture",
            method="provided",
        )
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute(
                "INSERT INTO transcript_segments (id,project_id,asset_id,revision,"
                "cue_index,start_ms,end_ms,text,language,method,review,overlap) "
                "VALUES (?,?,?,1,0,2,1,'fixture','und','provided','unreviewed',0)",
                ("seg_bad", project, asset.id),
            )


def test_transcript_project_scope_and_source_asset(ctx, project):
    other = app.create_project(ctx, {"name": "Other", "request_key": "other"}).data["id"]
    src = source(ctx, project, "src_media")
    other_src = source(ctx, other, "src_other_media")
    asset = ctx.store.put_object(io.BytesIO(b"shared-bytes"), mime="application/octet-stream")
    ctx.store.insert(
        m.SourceAsset(source_id=src.id, project_id=project, asset_id=asset.id, relation="video")
    )
    ctx.store.insert(
        m.SourceAsset(source_id=other_src.id, project_id=other, asset_id=asset.id, relation="video")
    )
    segment = ctx.store.insert(
        m.TranscriptSegment(
            id="seg_scoped",
            project_id=project,
            source_id=src.id,
            asset_id=asset.id,
            revision=1,
            cue_index=0,
            start_ms=0,
            end_ms=1000,
            text="僅此專案",
            language="zh-Hant",
            method="provided",
            overlap=True,
        )
    )
    assert ctx.store.get(m.TranscriptSegment, segment.id).overlap is True
    ctx.store.insert(
        m.Packet(
            id="pkt_seg",
            project_id=project,
            selection=[m.EvidenceRef(kind="transcript_segment", id=segment.id, revision=1)],
            rendered_text="僅此專案",
            digest=m.payload_digest("seg"),
            created_at=ctx.clock(),
        )
    )
    with pytest.raises(m.CoreError) as error:
        ctx.store.insert(
            m.Packet(
                id="pkt_leak",
                project_id=other,
                selection=[m.EvidenceRef(kind="transcript_segment", id=segment.id, revision=1)],
                rendered_text="僅此專案",
                digest=m.payload_digest("leak"),
                created_at=ctx.clock(),
            )
        )
    assert error.value.detail.code == m.ErrorCode.REFERENCE_INVALID
    owned_elsewhere = ctx.store.insert(
        m.TranscriptSegment(
            id="seg_other_owner",
            project_id=other,
            source_id=other_src.id,
            asset_id=asset.id,
            revision=1,
            start_ms=0,
            end_ms=500,
            text="另一專案的字幕",
            method="provided",
        )
    )
    assert owned_elsewhere.project_id == other
    assert ctx.store.get(m.TranscriptSegment, segment.id).project_id == project
    with pytest.raises(m.CoreError) as swapped:
        ctx.store.insert(
            m.Packet(
                id="pkt_swap",
                project_id=project,
                selection=[
                    m.EvidenceRef(kind="transcript_segment", id=owned_elsewhere.id, revision=1)
                ],
                rendered_text="另一專案的字幕",
                digest=m.payload_digest("swap"),
                created_at=ctx.clock(),
            )
        )
    assert swapped.value.detail.code == m.ErrorCode.REFERENCE_INVALID
    loose = ctx.store.insert(
        m.TranscriptSegment(
            id="seg_source_only",
            project_id=project,
            source_id=src.id,
            revision=1,
            cue_index=1,
            start_ms=1000,
            end_ms=1500,
            text="沒有 asset",
            method="provided",
        )
    )
    assert ctx.store.get(m.TranscriptSegment, loose.id).asset_id is None
    item_row = item(ctx, project, src.id, "itm_caption")
    revision(ctx, item_row.id)
    asset_less = ctx.store.insert(
        m.TranscriptSegment(
            id="seg_item_only",
            project_id=project,
            item_id=item_row.id,
            revision=1,
            start_ms=0,
            end_ms=400,
            text="貼在項目上",
            method="provided",
        )
    )
    assert asset_less.asset_id is None and asset_less.project_id == project
    unlinked = ctx.store.put_object(io.BytesIO(b"unlinked-bytes"), mime="application/octet-stream")
    with pytest.raises(m.CoreError) as missing_link:
        ctx.store.insert(
            m.TranscriptSegment(
                id="seg_unlinked",
                project_id=project,
                asset_id=unlinked.id,
                revision=1,
                start_ms=0,
                end_ms=100,
                text="沒有來源關係",
                method="provided",
            )
        )
    assert missing_link.value.detail.code == m.ErrorCode.REFERENCE_INVALID
    ctx.store.insert(m.Draft(id="drf_seg", project_id=project))
    ctx.store.insert(
        m.DraftRevision(
            draft_id="drf_seg",
            revision=1,
            text="引用字幕",
            platform="generic",
            facts_refs=[m.EvidenceRef(kind="transcript_segment", id=segment.id, revision=1)],
            digest=m.payload_digest("引用字幕"),
        )
    )
    ctx.store.insert(
        m.TranscriptSegment(
            id=segment.id,
            project_id=project,
            source_id=src.id,
            asset_id=asset.id,
            revision=2,
            start_ms=0,
            end_ms=1000,
            text="修正後",
            method="provided",
        )
    )
    pinned = ctx.store.get(m.Packet, "pkt_seg")
    assert pinned.stale is True
    assert pinned.selection == [m.EvidenceRef(kind="transcript_segment", id=segment.id, revision=1)]
    assert pinned.rendered_text == "僅此專案"
    assert ctx.store.get(m.Draft, "drf_seg").stale is True
    assert ctx.store.get(m.TranscriptSegment, segment.id, revision=1).text == "僅此專案"
    assert ctx.store.get(m.TranscriptSegment, segment.id).text == "修正後"
    with pytest.raises(m.CoreError) as immutable:
        with ctx.store.transaction() as conn:
            conn.execute(
                "UPDATE transcript_segments SET text='changed' WHERE id=? AND revision=1",
                (segment.id,),
            )
    assert immutable.value.detail.code == m.ErrorCode.REFERENCE_INVALID
    with pytest.raises(m.CoreError) as method:
        with ctx.store.transaction() as conn:
            conn.execute(
                "UPDATE sources SET upstream_collection_method='manual' WHERE id=?",
                (src.id,),
            )
    assert method.value.detail.code == m.ErrorCode.REFERENCE_INVALID
    with pytest.raises(m.CoreError) as payload:
        with ctx.store.transaction() as conn:
            conn.execute("UPDATE packets SET rendered_text='changed' WHERE id='pkt_seg'")
    assert payload.value.detail.code == m.ErrorCode.REFERENCE_INVALID
    assert ctx.store.get(m.Source, src.id).upstream_collection_method == "unknown"


def _v1_transcript_workspace(tmp_path, *, link_item: bool):
    paths = WorkspacePaths.resolve(tmp_path / "v1")
    legacy = Store(paths, schema_version=1, migrations={1: MIGRATIONS[1]})
    now = legacy.clock()
    legacy.insert(m.Project(id="prj_v1", name="Version one", created_at=now))
    legacy.insert(
        m.Source(
            id="src_v1",
            project_id="prj_v1",
            platform="generic",
            collection_method="file_import",
            fetched_at=now,
        )
    )
    legacy.insert(
        m.Item(
            id="itm_v1",
            project_id="prj_v1",
            source_id="src_v1",
            kind="post",
            identity_confidence="weak",
        )
    )
    legacy.insert(
        m.ItemRevision(
            item_id="itm_v1",
            revision=1,
            text="舊稿",
            text_hash=m.payload_digest("舊稿"),
            fetched_at=now,
        )
    )
    asset = legacy.put_object(io.BytesIO(b"v1-bytes"), mime="text/plain")
    if link_item:
        legacy.insert(
            m.ItemAsset(item_id="itm_v1", revision=1, asset_id=asset.id, relation="attached")
        )
    with legacy.transaction() as conn:
        conn.execute(
            "INSERT INTO transcript_segments (id,asset_id,revision,start_ms,end_ms,text,"
            "language,method,review) VALUES (?,?,1,0,500,?,?,?,?)",
            ("seg_v1", asset.id, "舊字幕", "zh-Hant", "provided", "unreviewed"),
        )
    return paths, asset


def test_v1_workspace_upgrades_with_backup(tmp_path):
    paths, asset = _v1_transcript_workspace(tmp_path, link_item=True)
    upgraded = Store(paths)
    with upgraded.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='source_assets'").fetchone()
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    segment = upgraded.get(m.TranscriptSegment, "seg_v1")
    assert segment.project_id == "prj_v1"
    assert segment.asset_id == asset.id
    assert segment.text == "舊字幕" and segment.language == "zh-Hant"
    assert segment.cue_index == 0 and segment.overlap is False
    assert upgraded.get(m.ItemRevision, "itm_v1").text == "舊稿"
    with upgraded.connection() as conn:
        triggers = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
    assert {
        "source_method_immutable",
        "packet_payload_immutable",
        "item_revision_immutable",
        "transcript_revision_immutable",
        "parent_cycle_insert",
        "parent_cycle_update",
    } <= triggers
    backup = next((paths.root / "backups").glob("pre_upgrade_*"))
    assert json.loads((backup / "manifest.json").read_text())["schema_version"] == 1
    with sqlite3.connect(backup / "state.sqlite3") as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT text FROM transcript_segments").fetchone()[0] == "舊字幕"


def test_v1_unscoped_transcript_aborts_upgrade(tmp_path):
    paths, _asset = _v1_transcript_workspace(tmp_path, link_item=False)
    with pytest.raises(m.CoreError) as error:
        Store(paths)
    assert error.value.detail.code == m.ErrorCode.STORAGE
    with sqlite3.connect(paths.database) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT text FROM transcript_segments").fetchone()[0] == "舊字幕"
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='source_assets'").fetchone()
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='source_method_immutable'"
        ).fetchone()
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' "
            "AND name='transcript_revision_immutable'"
        ).fetchone()
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    assert next((paths.root / "backups").glob("pre_upgrade_*")).is_dir()
    assert (
        Store(paths, schema_version=1, migrations={1: MIGRATIONS[1]}).get(m.Project, "prj_v1").name
        == "Version one"
    )


def test_v1_two_project_shared_asset_aborts_upgrade(tmp_path):
    paths, asset = _v1_transcript_workspace(tmp_path, link_item=True)
    legacy = Store(paths, schema_version=1, migrations={1: MIGRATIONS[1]})
    now = legacy.clock()
    legacy.insert(m.Project(id="prj_v2", name="Second project", created_at=now))
    legacy.insert(
        m.Source(
            id="src_v2",
            project_id="prj_v2",
            platform="generic",
            collection_method="file_import",
            fetched_at=now,
        )
    )
    legacy.insert(
        m.Item(
            id="itm_v2",
            project_id="prj_v2",
            source_id="src_v2",
            kind="post",
            identity_confidence="weak",
        )
    )
    legacy.insert(
        m.ItemRevision(
            item_id="itm_v2",
            revision=1,
            text="另一專案",
            text_hash=m.payload_digest("另一專案"),
            fetched_at=now,
        )
    )
    legacy.insert(m.ItemAsset(item_id="itm_v2", revision=1, asset_id=asset.id, relation="attached"))
    with pytest.raises(m.CoreError) as error:
        Store(paths)
    assert error.value.detail.code == m.ErrorCode.STORAGE
    with sqlite3.connect(paths.database) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM item_assets").fetchone()[0] == 2
        assert conn.execute("SELECT text FROM transcript_segments").fetchone()[0] == "舊字幕"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='packet_payload_immutable'"
        ).fetchone()


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
    current = ctx.store.schema_version
    migration = tmp_path / "next_failure.sql"
    migration.write_text("CREATE TABLE intermediate(id TEXT);\nINSERT INTO missing VALUES(1);\n")
    with pytest.raises(m.CoreError) as error:
        Store(ctx.paths, schema_version=current + 1, migrations={current + 1: migration})
    assert error.value.detail.code == m.ErrorCode.STORAGE
    with ctx.store.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == current
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='intermediate'").fetchone()
    assert ctx.store.get(m.Project, project).id == project
    backup = next((ctx.paths.root / "backups").glob("pre_upgrade_*"))
    assert json.loads((backup / "manifest.json").read_text())["schema_version"] == current
    restored = Store.restore(backup, WorkspacePaths.resolve(tmp_path / "pre_restored"))
    assert restored.get(m.Project, project).id == project
    migration.write_text("CREATE TABLE added(id TEXT);\n")
    newer = Store(ctx.paths, schema_version=current + 1, migrations={current + 1: migration})
    with newer.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == current + 1
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

    current = ctx.store.schema_version
    migration = tmp_path / "next.sql"
    migration.write_text("CREATE TABLE added(id TEXT);\n")
    with Executor(ctx.store):
        with pytest.raises(m.CoreError) as error:
            Store(ctx.paths, schema_version=current + 1, migrations={current + 1: migration})
        assert error.value.detail.code == m.ErrorCode.JOB_BUSY
    with ctx.store.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == current


def test_backup_private_files_and_tampered_manifest(ctx, tmp_path):
    ctx.store.put_object(io.BytesIO(b"fixture"), mime="text/plain")
    backup = ctx.store.backup(tmp_path / "backup")
    for path in (backup, *backup.rglob("*")):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    manifest = backup / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "objects": []}))
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
