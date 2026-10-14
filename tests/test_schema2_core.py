"""Schema-2 integration: rowid guards and canonical retained-table rebuilds."""

import io
import re
import sqlite3
from contextlib import closing

import pytest
from test_store import _v1_transcript_workspace

from metamuse import models as m
from metamuse import store as core

GUARDS = {
    "transcript_segments": ("transcript_revision_no_replace", "append-only revision"),
    "source_assets": ("source_assets_no_replace", "immutable source_asset relation"),
}


@pytest.fixture(params=["fresh", "upgraded"])
def scoped_store(ctx, project, tmp_path, request):
    if request.param == "upgraded":
        paths, asset = _v1_transcript_workspace(tmp_path / "legacy", link_item=True)
        store, project_id, source_id = core.Store(paths), "prj_v1", "src_v1"
    else:
        store, project_id, source_id = ctx.store, project, "src_schema2"
        store.insert(
            m.Source(
                id=source_id,
                project_id=project_id,
                platform="generic",
                collection_method="file_import",
                fetched_at=store.clock(),
            )
        )
        asset = store.put_object(io.BytesIO(b"schema2-media"), mime="text/plain")
        store.insert(
            m.TranscriptSegment(
                id="seg_schema2",
                project_id=project_id,
                source_id=source_id,
                revision=1,
                start_ms=0,
                end_ms=500,
                text="字幕",
                language="zh-Hant",
                method="provided",
            )
        )
    store.insert(
        m.SourceAsset(
            source_id=source_id, project_id=project_id, asset_id=asset.id, relation="media"
        )
    )
    return store


@pytest.mark.parametrize("table", GUARDS)
@pytest.mark.parametrize("verb", ["insert_or_replace", "replace", "upsert"])
def test_schema2_rowid_no_replace_has_specific_guard(scoped_store, table, verb):
    """A fresh composite key targeting an occupied rowid must fail before replacement."""
    store = scoped_store
    trigger, message = GUARDS[table]
    with store.connection() as conn:
        before = [tuple(row) for row in conn.execute(f"SELECT rowid,* FROM {table}")]
        victim = dict(conn.execute(f"SELECT rowid,* FROM {table}").fetchone())
        # The victim has no child FK rows. Different PKs prove the rowid check itself bites.
        changed = (
            {**victim, "id": "seg_fresh"}
            if table == "transcript_segments"
            else {**victim, "relation": "fresh"}
        )
        columns = list(changed)
        insert = f"INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})"
        sql = f"REPLACE {insert}" if verb == "replace" else f"INSERT OR REPLACE {insert}"
        if verb == "upsert":
            sql = f"INSERT {insert} ON CONFLICT DO UPDATE SET project_id=excluded.project_id"
        assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (trigger,)).fetchone()
        with pytest.raises(
            sqlite3.IntegrityError,
            match=re.escape(
                message
                + (
                    "; cannot replace existing transcript segment"
                    if table == "transcript_segments"
                    else "; cannot replace"
                )
            ),
        ):
            conn.execute(sql, list(changed.values()))
        assert [tuple(row) for row in conn.execute(f"SELECT rowid,* FROM {table}")] == before


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("transcript_segments", "rowid", 100),
        ("transcript_segments", "id", "seg_changed"),
        ("transcript_segments", "revision", 2),
        ("transcript_segments", "project_id", "prj_missing"),
        ("transcript_segments", "asset_id", "ast_missing"),
        ("transcript_segments", "item_id", "itm_missing"),
        ("transcript_segments", "source_id", "src_missing"),
        ("source_assets", "rowid", 100),
        ("source_assets", "source_id", "src_missing"),
        ("source_assets", "asset_id", "ast_missing"),
        ("source_assets", "relation", "changed"),
        ("source_assets", "project_id", "prj_missing"),
    ],
)
def test_schema2_identity_and_bindings_are_frozen(scoped_store, table, column, value):
    with scoped_store.connection() as conn:
        before = [tuple(row) for row in conn.execute(f"SELECT rowid,* FROM {table}")]
        with pytest.raises(sqlite3.IntegrityError, match="^" + re.escape(GUARDS[table][1]) + "$"):
            conn.execute(f"UPDATE {table} SET {column}=?", (value,))
        assert [tuple(row) for row in conn.execute(f"SELECT rowid,* FROM {table}")] == before


@pytest.mark.parametrize("table", GUARDS)
def test_schema2_update_replace_cannot_replace_unreferenced_victim(scoped_store, table):
    with scoped_store.connection() as conn:
        victim = dict(conn.execute(f"SELECT rowid,* FROM {table}").fetchone())
        helper = {key: value for key, value in victim.items() if key != "rowid"}
        if table == "transcript_segments":
            helper["id"] = "seg_helper"
        else:
            helper["relation"] = "helper"
        conn.execute(
            f"INSERT INTO {table} ({','.join(helper)}) VALUES ({','.join('?' for _ in helper)})",
            list(helper.values()),
        )
        helper_rowid = conn.execute(f"SELECT max(rowid) FROM {table}").fetchone()[0]
        before = [tuple(row) for row in conn.execute(f"SELECT rowid,* FROM {table}")]
        # Full-row UPDATE guards reject both rowid and primary-key collisions.
        collisions = {"rowid": victim["rowid"]}
        collisions["id" if table == "transcript_segments" else "relation"] = victim[
            "id" if table == "transcript_segments" else "relation"
        ]
        for column, value in collisions.items():
            with pytest.raises(
                sqlite3.IntegrityError, match="^" + re.escape(GUARDS[table][1]) + "$"
            ):
                conn.execute(
                    f"UPDATE OR REPLACE {table} SET {column}=? WHERE rowid=?", (value, helper_rowid)
                )
            assert [tuple(row) for row in conn.execute(f"SELECT rowid,* FROM {table}")] == before
        # Nothing references these two victim rows, so FKs cannot mask missing guards.
        conn.execute(f"DELETE FROM {table} WHERE rowid=?", (victim["rowid"],))


def test_schema2_fresh_and_upgraded_have_identical_canonical_objects(scoped_store):
    with scoped_store.connection() as conn:
        core._verify_schema(conn, 2, core.MIGRATIONS)
        scripts = tuple(core.MIGRATIONS[v].read_text() for v in (1, 2))
        assert core._schema_signature(conn) == core._expected_schema(scripts)
    prior = core._expected_schema((core.MIGRATIONS[1].read_text(),))
    current = core._expected_schema(scripts)
    retained_tables = {row[1] for row in prior if row[0] == "table"} & {
        row[1] for row in current if row[0] == "table"
    }
    assert {row[1] for row in prior if row[0] == "trigger" and row[2] in retained_tables} <= {
        row[1] for row in current if row[0] == "trigger"
    }
    assert {
        "transcript_revision_no_replace",
        "transcript_revision_immutable",
        "source_assets_no_replace",
        "source_assets_immutable",
    } <= {row[1] for row in current if row[0] == "trigger"}


@pytest.mark.parametrize(
    "guard",
    [
        "transcript_revision_no_replace",
        "transcript_revision_immutable",
        "source_assets_no_replace",
        "source_assets_immutable",
    ],
)
def test_schema2_retained_guards_cannot_be_dropped_in_later_migration(ctx, tmp_path, guard):
    migration = tmp_path / "003.sql"
    migration.write_text(f"DROP TRIGGER {guard};\n")
    with pytest.raises(m.CoreError, match="guard"):
        core.Store(ctx.paths, schema_version=3, migrations={**core.MIGRATIONS, 3: migration})
    with closing(sqlite3.connect(ctx.paths.database)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (guard,)).fetchone()
        core._verify_schema(conn, 2, core.MIGRATIONS)
