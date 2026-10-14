"""Round-3 adversarial reproductions, using only synthetic local data."""

import json
import os
import sqlite3
import warnings
from contextlib import closing

import pytest
from core3_fixtures import records as records

from metamuse import app, cli
from metamuse import models as m
from metamuse import store as core
from metamuse.paths import WorkspacePaths


def archive_path(result):
    return getattr(result, "path", result)


def refresh_digest(backup):
    file = backup / "manifest.json"
    manifest = json.loads(file.read_bytes())
    database = backup / "state.sqlite3"
    manifest["database"] = {
        "sha256": core._hash_file(database),
        "actual_bytes": database.stat().st_size,
    }
    file.write_bytes(m.canonical_json(manifest))


def test_r2_f1_rowid_and_update_replace_cannot_reset_unreferenced_step(ctx, records):
    with ctx.store.connection() as conn:
        row = dict(conn.execute("SELECT rowid,* FROM job_steps").fetchone())
    altered = {**row, "id": "stp_fresh", "sequence": 99, "state": "queued", "attempts": 0}
    columns = list(altered)
    with ctx.store.connection() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="step replacement is forbidden"):
            conn.execute(
                f"REPLACE INTO job_steps ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                list(altered.values()),
            )


def test_r2_f1_binding_columns_cannot_change(ctx, records):
    with ctx.store.connection() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="step identity is immutable"):
            conn.execute("UPDATE job_steps SET target='{\"forged\":true}' WHERE id='stp_review'")


@pytest.mark.parametrize("kind", ["trojan", "changed", "missing_database"])
def test_r2_f2_archive_schema_is_verified_before_target_write(ctx, records, tmp_path, kind):
    backup = archive_path(ctx.store.backup(tmp_path / "backup"))
    if kind == "missing_database":
        file = backup / "manifest.json"
        manifest = json.loads(file.read_bytes())
        del manifest["database"]
        file.write_bytes(m.canonical_json(manifest))
    else:
        with closing(sqlite3.connect(backup / "state.sqlite3")) as conn:
            if kind == "trojan":
                conn.execute(
                    "CREATE TRIGGER trojan AFTER UPDATE ON worker_state "
                    "BEGIN UPDATE projects SET name='forged'; END"
                )
            else:
                conn.execute("DROP TRIGGER source_method_immutable")
                conn.execute(
                    "CREATE TRIGGER source_method_immutable BEFORE UPDATE ON sources "
                    "WHEN 0 BEGIN SELECT RAISE(ABORT,'noop'); END"
                )
            conn.commit()
        refresh_digest(backup)
    target = WorkspacePaths.resolve(tmp_path / "target")
    with pytest.raises(m.CoreError):
        core.Store.restore(backup, target)
    assert not target.root.exists()


def test_r2_f2_migration_cannot_drop_a_guard_and_commit(ctx, tmp_path):
    migration = tmp_path / "002.sql"
    migration.write_text("DROP TRIGGER source_method_immutable;\n")
    with pytest.raises(m.CoreError, match="schema|guard"):
        core.Store(ctx.paths, migrations={**core.MIGRATIONS, 2: migration}, schema_version=2)
    with closing(sqlite3.connect(ctx.paths.database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='source_method_immutable'"
        ).fetchone()


@pytest.mark.parametrize("kind", ["backup_leaf", "workspace_leaf", "restore_leaf", "live_hardlink"])
def test_r2_f3_leaf_links_and_linked_live_database_are_rejected(ctx, tmp_path, kind):
    link = tmp_path / "link"
    if kind == "backup_leaf":
        link.symlink_to(tmp_path / "new-backup")
        with pytest.raises(m.CoreError):
            ctx.store.backup(link)
    elif kind == "workspace_leaf":
        link.symlink_to(tmp_path / "new-workspace")
        with pytest.raises(m.CoreError):
            app.AppContext.open(link)
    elif kind == "restore_leaf":
        backup = archive_path(ctx.store.backup(tmp_path / "backup"))
        link.symlink_to(backup)
        with pytest.raises(m.CoreError):
            core.Store.restore(link, WorkspacePaths.resolve(tmp_path / "target"))
    else:
        os.link(ctx.paths.database, link)
        with pytest.raises(m.CoreError):
            core.Store(ctx.paths)


def test_r2_f4_partial_results_survive_warning_suppression_and_same_version_restore(
    ctx, records, tmp_path
):
    (ctx.paths.root / records["export"]["artifact"] / "records.jsonl").unlink()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        first = ctx.store.backup(tmp_path / "first")
        second = ctx.store.backup(tmp_path / "second")
    assert first.status == second.status == "partial"
    assert first.unavailable_export_ids == (records["export"]["id"],)
    assert first.warnings and second.warnings
    with pytest.warns(core.BackupWarning):
        restored = core.Store.restore(first.path, WorkspacePaths.resolve(tmp_path / "restored"))
    assert restored.status == "partial"
    assert restored.unavailable_export_ids == first.unavailable_export_ids


def test_r2_f4_upgrade_status_reaches_cli_and_inspect(ctx, records, tmp_path, monkeypatch, capsys):
    (ctx.paths.root / records["export"]["artifact"] / "records.jsonl").unlink()
    migration = tmp_path / "002.sql"
    migration.write_text("CREATE TABLE upgrade_probe(id TEXT);\n")
    monkeypatch.setattr(core, "MIGRATIONS", {**core.MIGRATIONS, 2: migration})
    monkeypatch.setattr(core, "SCHEMA_VERSION", 2)
    with pytest.warns(core.BackupWarning):
        assert cli.main(["--workspace", str(ctx.paths.root), "--json", "doctor"]) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["warnings"]
    assert envelope["data"]["pre_upgrade_backups"][-1]["status"] == "partial"
    reopened = app.AppContext.open(ctx.paths.root)
    assert (
        app.inspect_capabilities(reopened, {}).data["pre_upgrade_backups"][-1]["status"]
        == "partial"
    )


def test_r2_f5_backup_outputs_are_fsynced(ctx, tmp_path, monkeypatch):
    observed = []
    original = os.fsync

    def track(fd):
        observed.append(os.readlink(f"/proc/self/fd/{fd}"))
        original(fd)

    monkeypatch.setattr(core.os, "fsync", track)
    backup = archive_path(ctx.store.backup(tmp_path / "backup"))
    assert str(backup / "state.sqlite3") in observed
    assert str(backup / "manifest.json") in observed
    assert str(backup) in observed


@pytest.mark.parametrize("kind", ["workspace", "backup", "restore"])
def test_r2_f3_symlinked_parent_directories_remain_supported(ctx, tmp_path, kind):
    real = tmp_path / "real_parent"
    real.mkdir()
    alias = tmp_path / "alias_parent"
    alias.symlink_to(real)
    if kind == "workspace":
        assert app.AppContext.open(alias / "workspace").paths.root == real / "workspace"
    elif kind == "backup":
        assert ctx.store.backup(alias / "backup").path == real / "backup"
    else:
        backup = ctx.store.backup(alias / "backup").path
        restored = core.Store.restore(alias / "backup", WorkspacePaths.resolve(alias / "target"))
        assert restored.path == real / "target" and backup.is_dir()


@pytest.mark.parametrize("kind", ["db_symlink", "wal_symlink", "shm_hardlink"])
def test_r2_f3_live_sqlite_files_are_private_regular_files(ctx, tmp_path, kind):
    path = (
        ctx.paths.database
        if kind == "db_symlink"
        else type(ctx.paths.database)(
            str(ctx.paths.database) + ("-wal" if kind == "wal_symlink" else "-shm")
        )
    )
    outside = tmp_path / "outside"
    if path.exists():
        path.rename(outside)
    else:
        outside.write_bytes(b"synthetic")
    if "symlink" in kind:
        path.symlink_to(outside)
    else:
        os.link(outside, path)
    with pytest.raises(m.CoreError):
        core.Store(ctx.paths)


def test_r2_f3_private_file_never_follows_a_leaf_link(tmp_path):
    from metamuse.paths import private_file

    outside = tmp_path / "outside"
    outside.write_bytes(b"synthetic")
    outside.chmod(0o400)
    link = tmp_path / "link"
    link.symlink_to(outside)
    with pytest.raises(OSError):
        private_file(link)
    assert outside.stat().st_mode & 0o777 == 0o400


@pytest.mark.parametrize("method", ["connection", "migrate"])
def test_r2_f5_marker_is_rechecked_after_maintenance_lock(ctx, monkeypatch, method):
    from contextlib import contextmanager

    original = core.file_lock

    @contextmanager
    def race(path, **kwargs):
        with original(path, **kwargs) as handle:
            (ctx.paths.runtime / "restore.incomplete").write_bytes(b"synthetic")
            yield handle

    monkeypatch.setattr(core, "file_lock", race)
    with pytest.raises(m.CoreError, match="incomplete"):
        if method == "connection":
            with ctx.store.connection():
                pytest.fail("Opened incomplete workspace")
        else:
            ctx.store.migrate(core.MIGRATIONS)


def test_r2_f5_marker_unlink_and_fsync_hold_maintenance_lock(ctx, tmp_path, monkeypatch):
    from contextlib import contextmanager

    backup = ctx.store.backup(tmp_path / "backup").path
    paths = WorkspacePaths.resolve(tmp_path / "restored")
    held = 0
    original_lock = core.file_lock
    original_sync = core._fsync_dir

    @contextmanager
    def tracked(path, **kwargs):
        nonlocal held
        with original_lock(path, **kwargs) as handle:
            if path == paths.runtime / "maintenance.lock":
                held += 1
            try:
                yield handle
            finally:
                if path == paths.runtime / "maintenance.lock":
                    held -= 1

    def synced(path):
        if path == paths.runtime and not (path / "restore.incomplete").exists():
            assert held
        original_sync(path)

    monkeypatch.setattr(core, "file_lock", tracked)
    monkeypatch.setattr(core, "_fsync_dir", synced)
    core.Store.restore(backup, paths)


def test_r2_f4_secondary_warning_points_to_public_caller(ctx, records, tmp_path):
    (ctx.paths.root / records["export"]["artifact"] / "records.jsonl").unlink()
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        ctx.store.backup(tmp_path / "partial")
    assert len(seen) == 1 and seen[0].filename == __file__


@pytest.mark.parametrize(
    "mutation",
    [
        "CREATE INDEX unexpected_index ON projects(name)",
        "CREATE VIEW unexpected_view AS SELECT * FROM projects",
        "ALTER TABLE projects ADD COLUMN unexpected_column TEXT",
        "DROP INDEX source_identity",
    ],
)
def test_r2_f2_all_schema_object_types_and_normal_open_are_checked(ctx, tmp_path, mutation):
    backup = ctx.store.backup(tmp_path / "backup").path
    for database in (ctx.paths.database, backup / "state.sqlite3"):
        with closing(sqlite3.connect(database)) as conn:
            conn.execute(mutation)
            conn.commit()
    refresh_digest(backup)
    with pytest.raises(m.CoreError, match="canonical"):
        core.Store(ctx.paths)
    target = WorkspacePaths.resolve(tmp_path / "target")
    with pytest.raises(m.CoreError, match="canonical"):
        core.Store.restore(backup, target)
    assert not target.root.exists()


def test_r2_f2_runtime_migration_schema_drift_rolls_back_before_commit(ctx, tmp_path, monkeypatch):
    migration = tmp_path / "002.sql"
    migration.write_text("CREATE TABLE upgrade_probe(id TEXT);\n")
    original = core._statements

    def changed(script):
        yield from original(script)
        if "upgrade_probe" in script:
            yield "DROP TRIGGER source_method_immutable;"

    # Build the trusted expected schema first, then inject an execution-only bug.
    scripts = (core.MIGRATIONS[1].read_text(), migration.read_text())
    core._expected_schema(scripts)
    monkeypatch.setattr(core, "_statements", changed)
    with pytest.raises(m.CoreError, match="canonical"):
        core.Store(ctx.paths, migrations={**core.MIGRATIONS, 2: migration}, schema_version=2)
    with closing(sqlite3.connect(ctx.paths.database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='source_method_immutable'"
        ).fetchone()
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='upgrade_probe'").fetchone()
