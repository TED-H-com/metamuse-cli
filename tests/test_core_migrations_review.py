"""F3/F8: rebuild migration safety and removal of pre-release legacy shims."""

import sqlite3
import traceback
from contextlib import closing

import pytest

from metamuse import models as m
from metamuse import store as core


def synthetic_v2(tmp_path, monkeypatch, script="CREATE TABLE upgrade_probe(id INT);\n"):
    migration = tmp_path / "synthetic_002.sql"
    migration.write_text(script)
    monkeypatch.setattr(core, "MIGRATIONS", {**core.MIGRATIONS, 2: migration})
    monkeypatch.setattr(core, "SCHEMA_VERSION", 2)
    return migration


@pytest.mark.parametrize("bad_fk", [False, True])
def test_f3_rebuild_with_child_rows_uses_fk_check_and_restores_enforcement(
    ctx, tmp_path, monkeypatch, bad_fk
):
    with ctx.store.transaction() as conn:
        conn.execute("CREATE TABLE rebuild_parent(id INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE rebuild_child(id INTEGER PRIMARY KEY,"
            "parent INTEGER REFERENCES rebuild_parent(id))"
        )
        conn.execute("INSERT INTO rebuild_parent VALUES(1)")
        conn.execute("INSERT INTO rebuild_child VALUES(1,1)")
    script = (
        "CREATE TABLE parent_new(id INTEGER PRIMARY KEY);\n"
        "INSERT INTO parent_new SELECT * FROM rebuild_parent;\n"
        "DROP TABLE rebuild_parent;\n"
        "ALTER TABLE parent_new RENAME TO rebuild_parent;\n"
    )
    if bad_fk:
        script += "INSERT INTO rebuild_child VALUES(2,99);\n"
    synthetic_v2(tmp_path, monkeypatch, script)
    trace = []
    original = core.Store._open

    def traced(store):
        conn = original(store)
        conn.set_trace_callback(trace.append)
        return conn

    monkeypatch.setattr(core.Store, "_open", traced)
    if bad_fk:
        with pytest.raises(m.CoreError):
            core.Store(ctx.paths)
        with closing(sqlite3.connect(ctx.paths.database)) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM rebuild_child").fetchone()[0] == 1
    else:
        upgraded = core.Store(ctx.paths)
        with upgraded.connection() as conn:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
            assert conn.execute("SELECT parent FROM rebuild_child").fetchone()[0] == 1
    assert trace.index("PRAGMA foreign_keys=OFF") < trace.index("BEGIN IMMEDIATE")
    assert "PRAGMA foreign_keys=ON" in trace


@pytest.mark.parametrize("missing_table", [False, True])
def test_f8_no_legacy_shim_or_secret_exception_chain(ctx, missing_table):
    secret = "SYNTHETIC_RAW_RECEIPT_SECRET"
    key = ctx.store.get_workspace_key(core.CONTENT_CURSOR_KEY)
    with ctx.store.transaction() as conn:
        if missing_table:
            conn.execute("DROP TABLE workspace_keys")
        conn.execute(
            "INSERT INTO request_receipts VALUES('content.cursor_key','v1',?,?)",
            (m.payload_digest({"version": 1}), '{"key":"' + secret),
        )
    if missing_table:
        with pytest.raises(m.CoreError) as error:
            core.Store(ctx.paths)
        assert "fresh workspace" in error.value.detail.message
        assert error.value.__cause__ is None
        assert secret not in "".join(traceback.format_exception(error.value))
        with closing(sqlite3.connect(ctx.paths.database)) as conn:
            assert not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='workspace_keys'"
            ).fetchone()
        assert not list((ctx.paths.root / "backups").glob("pre_upgrade_*"))
    else:
        assert core.Store(ctx.paths).get_workspace_key(core.CONTENT_CURSOR_KEY) == key
