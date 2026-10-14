"""F2/F9: conflict-resolution guards and authoritative export receipts."""

import os
import sqlite3

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
    "muse_trust_accounts": {"viewer_hash": "b" * 64},
    "muse_trust_vms": {"observed_at": "rewritten"},
    "muse_trust_observations": {"observed_at": "rewritten"},
}
PRIMARY = {
    "item_revisions": ["item_id", "revision"],
    "draft_revisions": ["draft_id", "revision"],
    "workspace_keys": ["name", "version"],
    "muse_trust_accounts": ["provider", "account"],
    "muse_trust_vms": ["provider", "account", "vm_id"],
    "muse_trust_observations": ["viewer_hash", "vm_id"],
}


MESSAGES = {
    "sources": "source replacement is forbidden",
    "item_revisions": "revision replacement is forbidden",
    "packets": "packet replacement is forbidden",
    "permits": "permit replacement is forbidden",
    "exchanges": "exchange replacement is forbidden",
    "drafts": "draft replacement is forbidden",
    "draft_revisions": "revision replacement is forbidden",
    "job_steps": "step replacement is forbidden",
    "workspace_keys": "workspace key replacement is forbidden",
    "exports": "export replacement is forbidden",
    "muse_trust_accounts": "muse trust account replacement",
    "muse_trust_vms": "muse trust VM replacement",
    "muse_trust_observations": "muse trust observation replacement",
}


def unreferenced_row(ctx, records, table):
    if table.startswith("muse_trust_"):
        ctx.store.muse_freeze(
            provider="synthetic",
            account="unreferenced",
            vm_id=None if table == "muse_trust_accounts" else "vm1",
            viewer_id="review-owner",
            session_binding_id="review-session",
            confidential=True,
        )
        with ctx.store.connection() as conn:
            row = dict(
                conn.execute(
                    f"SELECT * FROM {table}"
                    if table == "muse_trust_observations"
                    else f"SELECT * FROM {table} WHERE account='unreferenced'"
                ).fetchone()
            )
            if os.environ.get("METAMUSE_TEST_DROP_GUARDS") == "1":
                for trigger in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)
                ).fetchall():
                    conn.execute(f"DROP TRIGGER {trigger[0]}")
        return row
    ctx.store.insert(m.Project(id="prj_other", name="Other", created_at=ctx.clock()))
    # Clone a row under a new identity and add only its parents, never child references.
    with ctx.store.connection() as conn:
        row = dict(conn.execute(f"SELECT * FROM {table} ORDER BY rowid LIMIT 1").fetchone())
        if table in ("item_revisions", "draft_revisions"):
            parent_table = "items" if table == "item_revisions" else "drafts"
            field = "item_id" if table == "item_revisions" else "draft_id"
            parent = dict(conn.execute(f"SELECT * FROM {parent_table} LIMIT 1").fetchone())
            parent["id"] = "unreferenced_parent"
            columns = list(parent)
            conn.execute(
                f"INSERT INTO {parent_table} ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                list(parent.values()),
            )
            row[field] = parent["id"]
            row["revision"] = 1
        elif table == "workspace_keys":
            row.update(name="unreferenced.key", version=1, status="active")
        else:
            row["id"] = "unreferenced_row"
            if table == "job_steps":
                row["sequence"] = 99
            if table == "exchanges":
                row["request_key"] = "unreferenced"
            if table == "sources":
                row.update(native_scope="unreferenced", native_id="opaque")
        columns = list(row)
        conn.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            list(row.values()),
        )
        if os.environ.get("METAMUSE_TEST_DROP_GUARDS") == "1":
            # Explicit mutation experiment on test scratch DBs only, never production code.
            for trigger in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (table,)
            ).fetchall():
                conn.execute(f"DROP TRIGGER {trigger[0]}")
    return row


@pytest.mark.parametrize("table", GUARDED)
@pytest.mark.parametrize("verb", ["INSERT OR REPLACE", "REPLACE", "UPSERT"])
def test_f2_guarded_table_rejects_replacement_and_upsert(ctx, project, records, table, verb):
    row = unreferenced_row(ctx, records, table)
    original = dict(row)
    row.update(GUARDED[table])
    if table == "drafts":
        row["current_revision"] = 1  # identity conflict still invokes the guard
    columns = list(row)
    command = verb if verb != "UPSERT" else "INSERT"
    sql = f"{command} INTO {table} ({','.join(columns)}) "
    sql += f"VALUES ({','.join('?' for _ in columns)})"
    if verb == "UPSERT":
        sql += f" ON CONFLICT ({','.join(PRIMARY.get(table, ['id']))}) DO UPDATE SET " + ",".join(
            f"{name}=excluded.{name}" for name in GUARDED[table]
        )
    with ctx.store.connection() as conn:
        before = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        with pytest.raises(sqlite3.IntegrityError, match=MESSAGES[table]):
            conn.execute(sql, list(row.values()))
        assert [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")] == before
    assert original


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
    row = unreferenced_row(ctx, records, table)
    if table == "workspace_keys":
        row["version"] += 1
    else:
        row["id"] = "fresh_id"
    with ctx.store.connection() as conn:
        before = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        columns = list(row)
        with pytest.raises(sqlite3.IntegrityError, match=MESSAGES[table]):
            conn.execute(
                f"{verb} INTO {table} ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                list(row.values()),
            )
        assert [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")] == before


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


def test_f2_obsolete_development_guard_set_is_not_silently_accepted(ctx):
    from metamuse.store import Store

    with ctx.store.transaction() as conn:
        conn.execute("DROP TRIGGER source_no_replace")
    with pytest.raises(m.CoreError, match="fresh workspace"):
        Store(ctx.paths)


@pytest.mark.parametrize("table", GUARDED)
@pytest.mark.parametrize("verb", ["REPLACE", "UPSERT", "UPDATE OR REPLACE"])
def test_r2_f1_rowid_conflicts_and_identity_updates_are_guarded(ctx, records, table, verb):
    row = unreferenced_row(ctx, records, table)
    with ctx.store.connection() as conn:
        # The victim has no child references; assert this trigger, not a generic constraint.
        victim = conn.execute(f"SELECT rowid FROM {table} ORDER BY rowid DESC LIMIT 1").fetchone()[
            0
        ]
        if verb == "UPDATE OR REPLACE":
            message = "immutable" if table.startswith("muse_trust_") else "identity is immutable"
            with pytest.raises(sqlite3.IntegrityError, match=message):
                conn.execute(
                    f"UPDATE OR REPLACE {table} SET rowid=rowid+1000 WHERE rowid=?", (victim,)
                )
        else:
            incoming = {**row, "rowid": victim}
            if table in PRIMARY:
                if table.startswith("muse_trust_"):
                    incoming["account"] = "fresh-account"
                elif table == "workspace_keys":
                    incoming["name"] = "fresh.key"
                else:
                    incoming["revision"] += 1
            else:
                incoming["id"] = "fresh_incoming"
                if table == "sources":
                    incoming["native_id"] = "fresh"
                if table == "job_steps":
                    incoming["sequence"] += 1
                if table == "exchanges":
                    incoming["request_key"] = "fresh"
            columns = list(incoming)
            command = "REPLACE" if verb == "REPLACE" else "INSERT"
            sql = f"{command} INTO {table} ({','.join(columns)}) "
            sql += f"VALUES ({','.join('?' for _ in columns)})"
            if verb == "UPSERT":
                sql += " ON CONFLICT DO UPDATE SET rowid=excluded.rowid"
            with pytest.raises(sqlite3.IntegrityError, match=MESSAGES[table]):
                conn.execute(sql, list(incoming.values()))


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("sources", "native_id", "forged"),
        ("drafts", "project_id", "prj_other"),
        ("job_steps", "job_id", "job_forged"),
        ("job_steps", "sequence", 101),
        ("job_steps", "target", '{"forged":true}'),
        ("job_steps", "side_effect_class", "safe_read"),
        ("exchanges", "question", "forged"),
        ("exchanges", "digest", "forged"),
        ("exchanges", "packet_id", "pkt_forged"),
        ("exchanges", "permit_id", "pmt_forged"),
        ("exchanges", "discussion_id", "dsc_forged"),
        ("exchanges", "request_key", "forged"),
    ],
)
def test_r2_f1_immutable_binding_columns_cannot_change(ctx, records, table, column, value):
    row = unreferenced_row(ctx, records, table)
    with ctx.store.connection() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
            conn.execute(f"UPDATE {table} SET {column}=? WHERE id=?", (value, row["id"]))


def test_r2_f1_auto_assigned_rowid_before_insert_is_minus_one():
    with sqlite3.connect(":memory:") as conn:
        conn.executescript(
            "CREATE TABLE probe(id TEXT); CREATE TABLE observed(value INT); "
            "CREATE TRIGGER capture BEFORE INSERT ON probe "
            "BEGIN INSERT INTO observed VALUES(NEW.rowid); END;"
        )
        conn.execute("INSERT INTO probe VALUES('auto')")
        conn.execute("INSERT INTO probe(rowid,id) VALUES(42,'explicit')")
        assert conn.execute("SELECT value FROM observed").fetchall() == [(-1,), (42,)]


@pytest.mark.parametrize(
    "table", ["muse_trust_accounts", "muse_trust_vms", "muse_trust_observations"]
)
def test_r2_f8_trust_rows_cannot_be_deleted(ctx, records, table):
    unreferenced_row(ctx, records, table)
    with ctx.store.connection() as conn:
        before = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
        with pytest.raises(sqlite3.IntegrityError, match="retention"):
            conn.execute(f"DELETE FROM {table}")
        assert [dict(row) for row in conn.execute(f"SELECT * FROM {table}")] == before
