"""Older backup verification, guarded forward migration and unusable failed targets."""

import io
import json
import sqlite3
from contextlib import closing

import pytest

from metamuse import app
from metamuse import models as m
from metamuse import store as core
from metamuse.paths import WorkspacePaths

BASE_VERSION = core.SCHEMA_VERSION


@pytest.fixture
def older_backup(ctx, project, tmp_path):
    asset = ctx.store.put_object(io.BytesIO(b"synthetic old-version object"), mime="text/plain")
    saved = app.save_draft(
        ctx,
        {
            "project_id": project,
            "text": "舊版合成備份",
            "platform": "generic",
            "request_key": "draft",
        },
    )
    request = {
        "project_id": project,
        "selection": [{"kind": "draft_revision", "id": saved.data["draft_id"], "revision": 1}],
        "format": "jsonl",
        "request_key": "export",
    }
    exported = app.export_objects(ctx, request)
    assert exported.status == "ok"
    return ctx.store.backup(tmp_path / "older-backup").path, asset, request, exported.data


def install_next(tmp_path, monkeypatch, *, failing=False):
    migration = tmp_path / f"synthetic_{BASE_VERSION + 1:03}.sql"
    sql = "CREATE TABLE upgrade_probe(id INTEGER PRIMARY KEY, label TEXT);\n"
    sql += "INSERT INTO upgrade_probe VALUES(1,'synthetic upgrade');\n"
    if failing:
        sql += "INSERT INTO nonexistent VALUES(1);\n"
    migration.write_text(sql)
    monkeypatch.setattr(core, "MIGRATIONS", {**core.MIGRATIONS, BASE_VERSION + 1: migration})
    monkeypatch.setattr(core, "SCHEMA_VERSION", BASE_VERSION + 1)
    return migration


def test_older_backup_restores_then_migrates_with_its_own_backup(
    ctx, project, tmp_path, monkeypatch, older_backup
):
    backup, asset, request, exported = older_backup
    install_next(tmp_path, monkeypatch)
    restored_paths = WorkspacePaths.resolve(tmp_path / "restored-next")
    restored = core.Store.restore(backup, restored_paths).store
    assert restored.schema_version == BASE_VERSION + 1
    with restored.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == BASE_VERSION + 1
        assert conn.execute("SELECT label FROM upgrade_probe").fetchone()[0] == "synthetic upgrade"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert restored.get(m.Project, project).name == "Synthetic project"
    assert restored.get(m.Asset, asset.id).sha256 == asset.sha256
    assert app.export_objects(app.AppContext.open(restored_paths.root), request).data == exported
    pre_upgrade = list((restored_paths.root / "backups").glob("pre_upgrade_*"))
    assert len(pre_upgrade) == 1
    manifest = json.loads((pre_upgrade[0] / "manifest.json").read_bytes())
    assert manifest["schema_version"] == BASE_VERSION and len(manifest["exports"]) == 1
    assert (pre_upgrade[0] / asset.object_path).read_bytes() == b"synthetic old-version object"
    assert (pre_upgrade[0] / exported["artifact"] / "manifest.json").is_file()
    with closing(sqlite3.connect(backup / "state.sqlite3")) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == BASE_VERSION
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='upgrade_probe'").fetchone()
    assert not (restored_paths.runtime / "restore.incomplete").exists()


def test_restore_migration_failure_rolls_back_and_blocks_target(
    tmp_path, monkeypatch, older_backup
):
    backup, asset, _, exported = older_backup
    migration = install_next(tmp_path, monkeypatch, failing=True)
    paths = WorkspacePaths.resolve(tmp_path / "failed-restore")
    with pytest.raises(m.CoreError) as error:
        core.Store.restore(backup, paths)
    assert error.value.detail.code == m.ErrorCode.STORAGE
    assert "incomplete" in error.value.detail.message
    assert "fresh workspace" in error.value.detail.next_action
    marker = paths.runtime / "restore.incomplete"
    assert marker.is_file() and marker.stat().st_mode & 0o777 == 0o600
    with closing(sqlite3.connect(paths.database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == BASE_VERSION
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='upgrade_probe'").fetchone()
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (paths.root / asset.object_path).is_file()
    assert (paths.root / exported["artifact"] / "manifest.json").is_file()
    pre_upgrade = list((paths.root / "backups").glob("pre_upgrade_*"))
    assert len(pre_upgrade) == 1
    assert (
        json.loads((pre_upgrade[0] / "manifest.json").read_bytes())["schema_version"]
        == BASE_VERSION
    )
    # Even a subsequently repaired migration cannot silently adopt a failed restore.
    migration.write_text("CREATE TABLE upgrade_probe(id INTEGER PRIMARY KEY);\n")
    with pytest.raises(m.CoreError, match="restore is incomplete"):
        core.Store(paths)
    with pytest.raises(m.CoreError):
        app.AppContext.open(paths.root)
    recovery = core.Store.restore(backup, WorkspacePaths.resolve(tmp_path / "fresh-recovery")).store
    assert recovery.schema_version == BASE_VERSION + 1


@pytest.mark.parametrize("corruption", ["foreign_key", "object", "export"])
def test_older_backup_verified_before_any_target_or_upgrade(
    tmp_path, monkeypatch, older_backup, corruption
):
    backup, asset, _, exported = older_backup
    install_next(tmp_path, monkeypatch)
    if corruption == "foreign_key":
        with closing(sqlite3.connect(backup / "state.sqlite3")) as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("UPDATE events SET project_id='prj_missing'")
            conn.commit()
    elif corruption == "object":
        (backup / asset.object_path).write_bytes(b"corrupt")
    else:
        member = backup / exported["artifact"] / exported["manifest"]["files"][0]["name"]
        member.write_bytes(b"corrupt")
    paths = WorkspacePaths.resolve(tmp_path / "rejected-old-backup")
    with pytest.raises(m.CoreError):
        core.Store.restore(backup, paths)
    assert not paths.root.exists()


def test_newer_backup_refused_before_touching_target(tmp_path, monkeypatch, older_backup):
    backup, _, _, _ = older_backup
    install_next(tmp_path, monkeypatch)
    upgraded = core.Store.restore(backup, WorkspacePaths.resolve(tmp_path / "next")).store
    newer_backup = upgraded.backup(tmp_path / "next-backup").path
    monkeypatch.setattr(core, "SCHEMA_VERSION", BASE_VERSION)
    paths = WorkspacePaths.resolve(tmp_path / "old-build-target")
    with pytest.raises(m.CoreError) as error:
        core.Store.restore(newer_backup, paths)
    assert error.value.detail.code == m.ErrorCode.PROTOCOL_MISMATCH
    assert not paths.root.exists()


def test_manifest_version_must_match_backup_database(tmp_path, older_backup):
    backup, _, _, _ = older_backup
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["schema_version"] = 0
    manifest_path.write_bytes(m.canonical_json(manifest))
    with pytest.raises(m.CoreError) as error:
        core.Store.restore(backup, WorkspacePaths.resolve(tmp_path / "mismatched"))
    assert error.value.detail.code == m.ErrorCode.PROTOCOL_MISMATCH


def test_restore_copy_failure_blocks_target(tmp_path, monkeypatch, older_backup):
    backup, _, _, _ = older_backup
    paths = WorkspacePaths.resolve(tmp_path / "copy-failed")
    original = core.shutil.copyfile

    def fail_database(source, target):
        if target == paths.database:
            raise OSError("synthetic copy failure")
        return original(source, target)

    monkeypatch.setattr(core.shutil, "copyfile", fail_database)
    with pytest.raises(m.CoreError, match="incomplete"):
        core.Store.restore(backup, paths)
    with pytest.raises(m.CoreError, match="restore is incomplete"):
        core.Store(paths)
