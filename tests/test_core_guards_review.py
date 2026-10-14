"""F2/F9: conflict-resolution guards and authoritative export receipts."""

import pytest
from core3_fixtures import records as records

from metamuse import app
from metamuse import models as m

GUARDED = {
    "sources": {"collection_method": "manual"},
    "item_revisions": {"text": "rewritten"},
    "packets": {"rendered_text": "rewritten"},
    "permits": {"used": 0, "revoked": 0},
    "exchanges": {"outcome": None},
    "drafts": {"current_revision": 1, "project_id": "prj_other"},
    "draft_revisions": {"text": "rewritten"},
    "job_steps": {"state": "queued", "attempts": 0},
    "workspace_keys": {"key_material": bytes(32), "status": "active"},
    "exports": {"artifact": "exports/tampered", "format": "csv"},
}
PRIMARY = {
    "item_revisions": ["item_id", "revision"],
    "draft_revisions": ["draft_id", "revision"],
    "workspace_keys": ["name", "version"],
}


@pytest.mark.parametrize("table", GUARDED)
@pytest.mark.parametrize("verb", ["INSERT OR REPLACE", "REPLACE", "UPSERT"])
def test_f2_guarded_table_rejects_replacement_and_upsert(ctx, project, records, table, verb):
    app.create_project(ctx, {"name": "Other", "request_key": "other"})
    with ctx.store.transaction() as conn:
        conn.execute("INSERT INTO projects VALUES('prj_other','Other',1,?)", (ctx.clock(),))
    with ctx.store.connection() as conn:
        before = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
    row = {**before[0], **GUARDED[table]}
    columns = list(row)
    params = [row[name] for name in columns]
    sql = (
        f"{verb if verb != 'UPSERT' else 'INSERT'} INTO {table} ({','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in columns)})"
    )
    if verb == "UPSERT":
        sql += f" ON CONFLICT ({','.join(PRIMARY.get(table, ['id']))}) DO UPDATE SET " + ",".join(
            f"{name}=excluded.{name}" for name in GUARDED[table]
        )
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute(sql, params)
    with ctx.store.connection() as conn:
        assert [
            dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
        ] == before


@pytest.mark.parametrize(
    "column,value",
    [("artifact", "exports/tampered"), ("format", "csv"), ("selection", "[]"), ("manifest", "{}")],
)
def test_f9_ready_export_payload_is_immutable(ctx, records, column, value):
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute(
                f"UPDATE exports SET {column}=? WHERE id=?", (value, records["export"]["id"])
            )


def test_f9_receipt_replay_checks_the_authoritative_export_row(ctx, records):
    with ctx.store.transaction() as conn:
        # Simulate corrupt persisted metadata independent of the SQL write guards.
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='exports'"
        ).fetchall():
            conn.execute(f"DROP TRIGGER {row['name']}")
        conn.execute("UPDATE exports SET format='csv' WHERE id=?", (records["export"]["id"],))
    assert app.export_objects(ctx, records["request"]).status == "error"


@pytest.mark.parametrize("table", ["sources", "job_steps", "exchanges", "workspace_keys"])
@pytest.mark.parametrize("verb", ["INSERT OR REPLACE", "REPLACE"])
def test_f2_secondary_unique_identity_cannot_delete_a_guarded_row(ctx, records, table, verb):
    with ctx.store.transaction() as conn:
        if table == "sources":
            conn.execute("UPDATE sources SET native_scope='fixture',native_id='opaque'")
        before = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
    row = dict(before[-1] if table == "workspace_keys" else before[0])
    if table == "workspace_keys":
        row["version"] += 1
        row["status"] = "active"
    else:
        row["id"] = "new_opaque_id"
    columns = list(row)
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute(
                f"{verb} INTO {table} ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                [row[column] for column in columns],
            )
    with ctx.store.connection() as conn:
        assert [
            dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
        ] == before


def test_f2_recursive_delete_triggers_fire_on_replace(ctx):
    with ctx.store.transaction() as conn:
        assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
        conn.execute("CREATE TABLE retained_probe(id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO retained_probe VALUES(1)")
        conn.execute(
            "CREATE TRIGGER retained_probe_delete BEFORE DELETE ON retained_probe "
            "BEGIN SELECT RAISE(ABORT,'retained probe'); END"
        )
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute("REPLACE INTO retained_probe VALUES(1)")
