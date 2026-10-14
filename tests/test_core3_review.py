"""F1–F9 adversarial regressions; all data and migrations are synthetic."""

import json
import os
import sqlite3
from contextlib import closing

import pytest
from core3_fixtures import records as records

from metamuse import app
from metamuse import models as m
from metamuse import paths as path_module
from metamuse import store as core
from metamuse.paths import WorkspacePaths


@pytest.mark.parametrize(
    "probe", ["rollback", "wal_overlay", "readonly", "unknown", "shm", "journal"]
)
def test_f1_backup_is_standalone_and_verifies_the_copied_database(ctx, records, tmp_path, probe):
    backup = ctx.store.backup(tmp_path / "backup").path
    if probe == "rollback":
        with closing(sqlite3.connect(backup / "state.sqlite3")) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    elif probe == "readonly":
        before = {p.relative_to(backup): p.read_bytes() for p in backup.rglob("*") if p.is_file()}
        try:
            for path in (backup, *backup.rglob("*")):
                path.chmod(0o500 if path.is_dir() else 0o400)
            core.Store.restore(backup, WorkspacePaths.resolve(tmp_path / "restored"))
            assert {
                p.relative_to(backup): p.read_bytes() for p in backup.rglob("*") if p.is_file()
            } == before
        finally:
            for path in (backup, *backup.rglob("*")):
                path.chmod(0o700 if path.is_dir() else 0o600)
    elif probe == "wal_overlay":
        (backup / records["asset"].object_path).write_bytes(b"corrupt")
        manifest = json.loads((backup / "manifest.json").read_bytes())
        manifest["objects"] = []
        (backup / "manifest.json").write_bytes(m.canonical_json(manifest))
        with closing(sqlite3.connect(backup / "state.sqlite3", isolation_level=None)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA wal_autocheckpoint=0")
            # Remove child rows as well, so the WAL overlay is FK-consistent.
            conn.execute("DELETE FROM item_assets")
            conn.execute("DELETE FROM assets")
            with pytest.raises(m.CoreError):
                core.Store.restore(backup, WorkspacePaths.resolve(tmp_path / "rejected"))
        assert not (tmp_path / "rejected").exists()
    else:
        name = {
            "unknown": "unexpected",
            "shm": "state.sqlite3-shm",
            "journal": "state.sqlite3-journal",
        }[probe]
        (backup / name).write_bytes(b"synthetic")
        with pytest.raises(m.CoreError):
            core.Store.restore(backup, WorkspacePaths.resolve(tmp_path / "rejected"))


def synthetic_v2(tmp_path, monkeypatch, script="CREATE TABLE upgrade_probe(id INT);\n"):
    migration = tmp_path / "synthetic_002.sql"
    migration.write_text(script)
    monkeypatch.setattr(core, "MIGRATIONS", {**core.MIGRATIONS, 2: migration})
    monkeypatch.setattr(core, "SCHEMA_VERSION", 2)
    return migration


@pytest.mark.parametrize("changed", [False, True])
def test_f4_unavailable_exports_warn_without_blocking_backup_open_or_migration(
    ctx, records, tmp_path, monkeypatch, changed
):
    member = ctx.paths.root / records["export"]["artifact"] / "records.jsonl"
    if changed:
        member.write_bytes(b"changed")
    else:
        member.unlink()
    assert app.AppContext.open(ctx.paths.root)
    with pytest.warns(UserWarning, match="unavailable"):
        backup = ctx.store.backup(tmp_path / "partial").path
    manifest = json.loads((backup / "manifest.json").read_bytes())
    assert manifest["status"] == "partial"
    assert manifest["exports"][0]["state"] == "unavailable"
    synthetic_v2(tmp_path, monkeypatch)
    with pytest.warns(UserWarning, match="unavailable"):
        upgraded = core.Store(ctx.paths)
    assert upgraded.schema_version == 2
    with pytest.warns(UserWarning, match="unavailable"):
        restored = core.Store.restore(backup, WorkspacePaths.resolve(tmp_path / "restored")).store
    assert restored.get(m.Export, records["export"]["id"]).state == "unavailable"
    assert (
        app.export_objects(app.AppContext.open(restored.paths.root), records["request"]).status
        == "error"
    )


@pytest.mark.parametrize(
    "kind", ["restore_descendant", "restore_ancestor", "backup_descendant", "backup_equal"]
)
def test_f5_backup_and_restore_paths_must_not_overlap(ctx, tmp_path, kind):
    backup = ctx.store.backup(tmp_path / "backup").path
    before = {p.relative_to(backup): p.read_bytes() for p in backup.rglob("*") if p.is_file()}
    with pytest.raises(m.CoreError):
        if kind == "restore_descendant":
            core.Store.restore(backup, WorkspacePaths.resolve(backup / "exports"))
        elif kind == "restore_ancestor":
            core.Store.restore(backup, WorkspacePaths.resolve(tmp_path))
        elif kind == "backup_descendant":
            ctx.store.backup(ctx.paths.root / "exports" / "sub")
        else:
            ctx.store.backup(ctx.paths.root)
    assert {
        p.relative_to(backup): p.read_bytes() for p in backup.rglob("*") if p.is_file()
    } == before


def test_f6_initialize_failure_is_blocked_before_any_database_creation(ctx, tmp_path, monkeypatch):
    backup = ctx.store.backup(tmp_path / "backup").path
    paths = WorkspacePaths.resolve(tmp_path / "failed")
    original = path_module.private_file

    def failure(path):
        if path == paths.runtime / "maintenance.lock":
            raise OSError("synthetic initialize failure")
        return original(path)

    monkeypatch.setattr(path_module, "private_file", failure)
    with pytest.raises(m.CoreError):
        core.Store.restore(backup, paths)
    assert (paths.runtime / "restore.incomplete").is_file()
    assert not paths.database.exists()
    with pytest.raises(m.CoreError, match="incomplete"):
        core.Store(paths)


def test_f6_marker_unlink_is_fsynced_and_preupgrade_worker_state_is_cleared(
    ctx, tmp_path, monkeypatch
):
    with ctx.store.transaction() as conn:
        conn.execute("UPDATE worker_state SET owner_nonce='99999:synthetic',heartbeat='2000-01-01'")
    backup = ctx.store.backup(tmp_path / "backup").path
    synthetic_v2(tmp_path, monkeypatch)
    paths = WorkspacePaths.resolve(tmp_path / "restored")
    observed = []
    original = core._fsync_dir

    def observe(path):
        if path == paths.runtime:
            observed.append((paths.runtime / "restore.incomplete").exists())
        original(path)

    monkeypatch.setattr(core, "_fsync_dir", observe)
    core.Store.restore(backup, paths)
    assert observed[-1] is False
    pre = next((paths.root / "backups").glob("pre_upgrade_*"))
    with closing(sqlite3.connect(pre / "state.sqlite3")) as conn:
        assert conn.execute("SELECT owner_nonce,heartbeat FROM worker_state").fetchone() == (
            None,
            None,
        )


@pytest.mark.parametrize(
    "probe",
    [
        "broken_exports",
        "object_symlink",
        "db_symlink",
        "manifest_symlink",
        "extra_object",
        "hardlink_member",
        "hardlink_manifest",
        "hardlink_object",
        "hardlink_db",
    ],
)
def test_f7_verified_files_reject_symlinks_hardlinks_and_extra_objects(
    ctx, records, tmp_path, probe
):
    backup = ctx.store.backup(tmp_path / "backup").path
    if probe == "broken_exports":
        empty = app.AppContext.open(tmp_path / "empty")
        backup = empty.store.backup(tmp_path / "empty-backup").path
        (backup / "exports").symlink_to(tmp_path / "missing")
    elif probe == "extra_object":
        (backup / "objects" / "unexpected").write_bytes(b"synthetic")
    else:
        paths = {
            "object_symlink": backup / records["asset"].object_path,
            "db_symlink": backup / "state.sqlite3",
            "manifest_symlink": backup / "manifest.json",
            "hardlink_member": backup / records["export"]["artifact"] / "records.jsonl",
            "hardlink_manifest": backup / records["export"]["artifact"] / "manifest.json",
            "hardlink_object": backup / records["asset"].object_path,
            "hardlink_db": backup / "state.sqlite3",
        }
        path = paths[probe]
        outside = tmp_path / "outside"
        if "symlink" in probe:
            path.rename(outside)
            path.symlink_to(outside)
        else:
            os.link(path, outside)
    with pytest.raises(m.CoreError):
        core.Store.restore(backup, WorkspacePaths.resolve(tmp_path / "rejected"))
    assert not (tmp_path / "rejected").exists()


def test_f7_live_asset_and_export_hardlinks_are_not_verified(ctx, records, tmp_path):
    obj = ctx.paths.root / records["asset"].object_path
    member = ctx.paths.root / records["export"]["artifact"] / "records.jsonl"
    os.link(obj, tmp_path / "object-link")
    os.link(member, tmp_path / "member-link")
    with pytest.raises(m.CoreError):
        ctx.store.verify_asset(records["asset"])
    with pytest.raises(m.CoreError):
        ctx.store.verify_export(ctx.store.get(m.Export, records["export"]["id"]))


@pytest.mark.parametrize("kind", ["database", "object", "export"])
def test_f1_copied_database_and_members_are_reverified(ctx, records, tmp_path, monkeypatch, kind):
    backup = ctx.store.backup(tmp_path / "backup").path
    paths = WorkspacePaths.resolve(tmp_path / "target")
    original = core.shutil.copyfile
    victim = {
        "database": paths.database,
        "object": paths.root / records["asset"].object_path,
        "export": paths.root / records["export"]["artifact"] / "records.jsonl",
    }[kind]

    def corrupt_copy(source, target):
        result = original(source, target)
        if target == victim:
            if kind == "database":
                with closing(sqlite3.connect(target)) as conn:
                    conn.execute("UPDATE projects SET name='altered copy'")
                    conn.commit()
            else:
                raw = target.read_bytes()
                target.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        return result

    monkeypatch.setattr(core.shutil, "copyfile", corrupt_copy)
    with pytest.raises(m.CoreError, match="incomplete"):
        core.Store.restore(backup, paths)
    with pytest.raises(m.CoreError, match="incomplete"):
        core.Store(paths)
    # The original archive remains independently restorable.
    core.Store.restore(backup, WorkspacePaths.resolve(tmp_path / "other-target"))


@pytest.mark.parametrize("broken", [False, True])
@pytest.mark.parametrize("method", ["open", "connection", "migrate", "put_object"])
def test_f6_all_entry_points_respect_even_broken_restore_markers(ctx, tmp_path, broken, method):
    marker = ctx.paths.runtime / "restore.incomplete"
    if broken:
        marker.symlink_to(tmp_path / "missing")
    else:
        marker.write_bytes(b"synthetic incomplete restore")
    with pytest.raises(m.CoreError, match="incomplete"):
        if method == "open":
            core.Store(ctx.paths)
        elif method == "connection":
            with ctx.store.connection():
                pytest.fail("Opened blocked workspace")
        elif method == "migrate":
            ctx.store.migrate(core.MIGRATIONS)
        else:
            ctx.store.put_object(None, mime="text/plain")
    assert not list((ctx.paths.root / "staging").iterdir())


def test_f6_failed_marker_unlink_fsync_reinstates_the_blocker(ctx, tmp_path, monkeypatch):
    backup = ctx.store.backup(tmp_path / "backup").path
    paths = WorkspacePaths.resolve(tmp_path / "target")
    original = core._fsync_dir

    def fail_after_unlink(path):
        if path == paths.runtime and not (paths.runtime / "restore.incomplete").exists():
            raise OSError("synthetic unlink fsync failure")
        original(path)

    monkeypatch.setattr(core, "_fsync_dir", fail_after_unlink)
    with pytest.raises(m.CoreError, match="incomplete"):
        core.Store.restore(backup, paths)
    with pytest.raises(m.CoreError, match="incomplete"):
        core.Store(paths)


def test_f7_backup_manifest_hardlink_is_rejected(ctx, tmp_path):
    backup = ctx.store.backup(tmp_path / "backup").path
    os.link(backup / "manifest.json", tmp_path / "manifest-alias")
    with pytest.raises(m.CoreError):
        core.Store.restore(backup, WorkspacePaths.resolve(tmp_path / "rejected"))


def test_f1_f4_old_backup_verification_is_independent_of_current_models(
    ctx, records, tmp_path, monkeypatch
):
    backup = ctx.store.backup(tmp_path / "backup").path

    class FutureAsset(m.Asset):
        future_column: str

    class FutureExport(m.Export):
        future_column: str

    monkeypatch.setattr(m, "Asset", FutureAsset)
    monkeypatch.setattr(m, "Export", FutureExport)
    monkeypatch.setattr(
        core, "MODEL_TABLES", {**core.MODEL_TABLES, FutureAsset: "assets", FutureExport: "exports"}
    )
    synthetic_v2(
        tmp_path,
        monkeypatch,
        "ALTER TABLE assets ADD COLUMN future_column TEXT NOT NULL DEFAULT 'v2';\n"
        "ALTER TABLE exports ADD COLUMN future_column TEXT NOT NULL DEFAULT 'v2';\n",
    )
    restored = core.Store.restore(backup, WorkspacePaths.resolve(tmp_path / "upgraded")).store
    assert restored.get(FutureAsset, records["asset"].id).future_column == "v2"
    assert restored.get(FutureExport, records["export"]["id"]).future_column == "v2"
    assert len(list((restored.paths.root / "backups").glob("pre_upgrade_*"))) == 1
