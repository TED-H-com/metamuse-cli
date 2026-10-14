"""B10 schema-3 upgrades, archive isolation and direct SQLite guard evidence."""

import json
import re
import sqlite3

import pytest

from metamuse import app
from metamuse import models as m
from metamuse import store as core
from metamuse.paths import WorkspacePaths


def versioned_store(root, version):
    return core.Store(
        WorkspacePaths.resolve(root),
        schema_version=version,
        migrations={v: core.MIGRATIONS[v] for v in range(1, version + 1)},
    )


def signature(store):
    with store.connection() as conn:
        core._verify_schema(conn, store.schema_version, store._migrations)
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return core._schema_signature(conn)


def add_legacy(store):
    ctx = app.AppContext(store.paths, store)
    project = app.create_project(ctx, {"name": "Synthetic upgrade", "request_key": "project"})
    project_id = project.data["id"]
    store.insert(
        m.Packet(
            id="pkt_legacy",
            project_id=project_id,
            selection=[],
            rendered_text="Synthetic packet",
            digest=m.payload_digest("packet"),
            created_at=ctx.clock(),
        )
    )
    store.insert(
        m.Permit(
            id="pmt_legacy",
            project_id=project_id,
            kind="share",
            scope={},
            issuer="fixture",
            issued_at=ctx.clock(),
            expires_at="2099-01-01T00:00:00+00:00",
            max_uses=1,
        )
    )
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO discussions "
            "(id,project_id,provider,auth_ref,topic,open_attempt_state,last_seq,state) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                "dsc_legacy",
                project_id,
                "fixture",
                "fixture",
                "Legacy",
                "open_uncertain",
                0,
                "opening",
            ),
        )
        conn.execute(
            "INSERT INTO exchanges "
            "(id,project_id,discussion_id,request_key,question,packet_id,"
            "digest,permit_id,baseline,state) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "exc_legacy",
                project_id,
                "dsc_legacy",
                "legacy",
                "Synthetic question",
                "pkt_legacy",
                m.payload_digest("legacy"),
                "pmt_legacy",
                "{}",
                "failed_before_send",
            ),
        )
    return project.data


def test_fresh_v3_matches_canonical_and_retains_every_prior_guard(tmp_path):
    current = core.Store(WorkspacePaths.resolve(tmp_path / "fresh"))
    assert current.schema_version == 3
    actual = signature(current)
    assert "muse_observations" in {name for kind, name, _, _ in actual if kind == "table"}
    for version in (1, 2):
        older = versioned_store(tmp_path / f"v{version}", version)
        guards = {(name, table) for kind, name, table, _ in signature(older) if kind == "trigger"}
        assert guards <= {(name, table) for kind, name, table, _ in actual if kind == "trigger"}


@pytest.mark.parametrize(
    "guard",
    [
        "discussion_no_replace",
        "discussion_identity_immutable",
        "discussion_attempt_once",
        "exchange_no_replace",
        "exchanges_identity_immutable",
        "exchange_attempt_once",
        "message_no_replace",
        "message_identity_immutable",
        "muse_observation_no_replace",
        "muse_observation_identity_immutable",
    ],
)
def test_later_migration_cannot_drop_v3_guards(ctx, tmp_path, guard):
    before = signature(ctx.store)
    migration = tmp_path / "004_drop_guard.sql"
    migration.write_text(f"DROP TRIGGER {guard};\n")
    with pytest.raises(m.CoreError, match="Migration drops required schema guards"):
        core.Store(ctx.paths, schema_version=4, migrations={**core.MIGRATIONS, 4: migration})
    with ctx.store.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    assert signature(ctx.store) == before


@pytest.mark.parametrize("versions", [(1, 2, 3), (1, 3), (2, 3)])
def test_real_upgrades_have_fresh_canonical_objects_and_unknown_legacy_delivery(tmp_path, versions):
    paths = WorkspacePaths.resolve(tmp_path / "upgrade")
    older = versioned_store(paths.root, versions[0])
    project = add_legacy(older)
    for version in versions[1:]:
        current = versioned_store(paths.root, version)
        assert current.schema_version == version
    fresh = core.Store(WorkspacePaths.resolve(tmp_path / "fresh"))
    assert signature(current) == signature(fresh)
    assert current.get(m.Project, project["id"]).name == "Synthetic upgrade"
    exchange = current.get(m.Exchange, "exc_legacy")
    discussion = current.get(m.Discussion, "dsc_legacy")
    assert exchange.state == "send_uncertain"
    assert exchange.bytes_handed_off is None and exchange.attempt_verdict is None
    assert discussion.account is None and discussion.backend is None
    assert len(current.pre_upgrade_backups()) == len(versions) - 1


def tree_snapshot(root):
    return {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_v2_archive_restores_to_v3_with_receipt_replay_and_untouched_original(tmp_path):
    original = versioned_store(tmp_path / "original-v2", 2)
    ctx = app.AppContext(original.paths, original)
    project_request = {"name": "Synthetic archive", "request_key": "project"}
    project = app.create_project(ctx, project_request)
    draft_request = {
        "project_id": project.data["id"],
        "text": "Synthetic archive draft 原文",
        "platform": "generic",
        "request_key": "draft",
    }
    draft = app.save_draft(ctx, draft_request)
    export_request = {
        "project_id": project.data["id"],
        "selection": [{"kind": "draft_revision", "id": draft.data["draft_id"], "revision": 1}],
        "format": "jsonl",
        "request_key": "export",
    }
    exported = app.export_objects(ctx, export_request)
    assert exported.status == "ok"
    archive = original.backup(tmp_path / "archive-v2")
    assert isinstance(archive, core.BackupResult) and archive.status == "complete"
    archive_before = tree_snapshot(archive.path)
    original_before = tree_snapshot(original.paths.root)
    result = core.Store.restore(archive.path, WorkspacePaths.resolve(tmp_path / "restored-v3"))
    assert isinstance(result, core.RestoreResult) and result.status == "complete"
    restored = result.store
    assert restored.schema_version == 3
    assert signature(restored) == signature(core.Store(WorkspacePaths.resolve(tmp_path / "fresh")))
    restored_ctx = app.AppContext.open(result.path)
    assert app.create_project(restored_ctx, project_request).data == project.data
    assert app.save_draft(restored_ctx, draft_request).data == draft.data
    assert app.export_objects(restored_ctx, export_request).data == exported.data
    status = restored.pre_upgrade_backups()
    assert len(status) == 1 and status[0]["status"] == "complete"
    before_upgrade = next((result.path / "backups").glob("pre_upgrade_*"))
    assert json.loads((before_upgrade / "manifest.json").read_bytes())["schema_version"] == 2
    with core._archive_connection(before_upgrade / "state.sqlite3") as conn:
        core._verify_schema(conn, 2, core.MIGRATIONS)
    assert tree_snapshot(archive.path) == archive_before
    assert tree_snapshot(original.paths.root) == original_before
    assert not (restored.paths.runtime / "restore.incomplete").exists()


TABLES = ("discussions", "exchanges", "messages", "muse_observations")
IDENTITY_MESSAGES = {
    "discussions": "discussion binding and intent immutable",
    "exchanges": "exchange identity is immutable",
    "messages": "message identity immutable",
    "muse_observations": "observation identity is immutable",
}
INSERT_MESSAGES = {
    "discussions": "discussion identity retained",
    "exchanges": "exchange replacement is forbidden",
    "messages": "message identity retained",
    "muse_observations": "observation identity retained",
}


def unreferenced_pair(ctx, project, table):
    parent = m.Discussion(
        id="dsc_parent",
        project_id=project,
        provider="fixture",
        auth_ref="fixture",
        topic="Parent",
        account="fixture",
        backend="fixture",
    )
    ctx.store.insert(parent)
    if table == "exchanges":
        ctx.store.insert(
            m.Packet(
                id="pkt_parent",
                project_id=project,
                selection=[],
                rendered_text="Fixture",
                digest=m.payload_digest("packet"),
                created_at=ctx.clock(),
            )
        )
        ctx.store.insert(
            m.Permit(
                id="pmt_parent",
                project_id=project,
                kind="share",
                scope={},
                issuer="fixture",
                issued_at=ctx.clock(),
                expires_at="2099-01-01T00:00:00+00:00",
                max_uses=1,
            )
        )
    for number in range(2):
        identity = f"row_{number}"
        if table == "discussions":
            record = parent.model_copy(update={"id": identity, "open_request_key": identity})
        elif table == "exchanges":
            record = m.Exchange(
                id=identity,
                project_id=project,
                discussion_id=parent.id,
                request_key=identity,
                question="Fixture",
                packet_id="pkt_parent",
                digest=m.payload_digest(identity),
                permit_id="pmt_parent",
                state="closed",
                outcome="answered",
            )
        elif table == "messages":
            record = m.Message(
                id=identity,
                discussion_id=parent.id,
                remote_session="fixture-session",
                remote_message_id=identity,
                seq=number,
                role="assistant",
                text="Fixture",
                observed_at=ctx.clock(),
                backend="fixture",
                identity_key=identity,
            )
        else:
            event = {"event_name": "fixture", "seq": number, "payload": {}}
            record = m.MuseObservation(
                id=identity,
                discussion_id=parent.id,
                identity_key=identity,
                raw_event=event,
                observation_digest=m.payload_digest(event),
                observed_at=ctx.clock(),
                backend="fixture",
            )
        ctx.store.insert(record)
    # The rows under attack have no inbound references: FK deletion refusal cannot mask a guard.
    queries = {
        "discussions": ("exchanges", "messages", "muse_observations"),
        "exchanges": ("messages",),
        "messages": ("muse_observations",),
        "muse_observations": (),
    }
    foreign_key = {
        "discussions": "discussion_id",
        "exchanges": "exchange_id",
        "messages": "message_id",
    }
    with ctx.store.connection() as conn:
        for child in queries[table]:
            assert (
                conn.execute(
                    f"SELECT count(*) FROM {child} WHERE {foreign_key[table]} IN ('row_0','row_1')"
                ).fetchone()[0]
                == 0
            )


def expect_guard(store, table, sql, params, message):
    with store.connection() as conn:
        before = [tuple(row) for row in conn.execute(f"SELECT rowid,* FROM {table} ORDER BY rowid")]
        with pytest.raises(sqlite3.IntegrityError, match=f"^{re.escape(message)}$"):
            conn.execute(sql, params)
        assert [
            tuple(row) for row in conn.execute(f"SELECT rowid,* FROM {table} ORDER BY rowid")
        ] == before


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("alias", ["rowid", "_rowid_", "oid"])
def test_v3_rowid_collision_insert_and_update_or_replace_use_specific_guards(
    ctx, project, table, alias
):
    unreferenced_pair(ctx, project, table)
    with ctx.store.connection() as conn:
        rows = conn.execute(
            f"SELECT rowid,* FROM {table} WHERE id IN ('row_0','row_1') ORDER BY id"
        ).fetchall()
        fields = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    values = dict(rows[0])
    values["id"] = "row_new"
    if table == "discussions":
        values["open_request_key"] = "new"
    elif table == "exchanges":
        values["request_key"] = "new"
    else:
        values["identity_key"] = "new"
        if table == "messages":
            values["remote_message_id"] = "new"
    expect_guard(
        ctx.store,
        table,
        f"INSERT OR REPLACE INTO {table} ({alias},{','.join(fields)}) "
        f"VALUES ({','.join('?' for _ in range(len(fields) + 1))})",
        (rows[0]["rowid"], *(values[field] for field in fields)),
        INSERT_MESSAGES[table],
    )
    expect_guard(
        ctx.store,
        table,
        f"UPDATE OR REPLACE {table} SET {alias}=? WHERE id='row_0'",
        (rows[1]["rowid"],),
        IDENTITY_MESSAGES[table],
    )


@pytest.mark.parametrize(
    "table,field,value",
    [
        ("discussions", "id", "row_1"),
        ("discussions", "account", "other"),
        ("discussions", "provider", "other"),
        ("discussions", "auth_ref", "other"),
        ("discussions", "backend", "live"),
        ("discussions", "open_request_key", "row_1"),
        ("discussions", "open_payload", "{}"),
        ("discussions", "topic", "other"),
        ("exchanges", "id", "row_1"),
        ("exchanges", "request_key", "row_1"),
        ("exchanges", "baseline", '{"seq":5}'),
        ("exchanges", "outbound_payload", "{}"),
        ("exchanges", "request_marker", "other"),
        ("exchanges", "outbound_digest", "a" * 64),
        ("messages", "id", "row_1"),
        ("messages", "remote_message_id", "row_1"),
        ("messages", "identity_key", "row_1"),
        ("messages", "backend", "live"),
        ("messages", "remote_session", "other"),
        ("messages", "discussion_id", "dsc_missing"),
        ("muse_observations", "id", "row_1"),
        ("muse_observations", "identity_key", "row_1"),
        ("muse_observations", "observation_digest", "a" * 64),
        ("muse_observations", "backend", "live"),
        ("muse_observations", "discussion_id", "dsc_missing"),
        ("muse_observations", "message_id", "msg_missing"),
    ],
)
def test_v3_binding_and_unique_identity_freeze_on_unreferenced_rows(
    ctx, project, table, field, value
):
    unreferenced_pair(ctx, project, table)
    expect_guard(
        ctx.store,
        table,
        f"UPDATE OR REPLACE {table} SET {field}=? WHERE id='row_0'",
        (value,),
        IDENTITY_MESSAGES[table],
    )


@pytest.mark.parametrize(
    "table,field,first,second",
    [
        ("discussions", "open_submitted_at_ms", 1, 2),
        ("discussions", "open_stream_id", 1, 2),
        ("discussions", "open_bytes_handed_off", 0, 1),
        ("discussions", "open_verdict", "undecided", "acked"),
        ("discussions", "open_error_code", "E_NETWORK_TIMEOUT", "E_PROTOCOL_MISMATCH"),
        ("exchanges", "submitted_at_ms", 1, 2),
        ("exchanges", "stream_id", 1, 2),
        ("exchanges", "bytes_handed_off", 0, 1),
        ("exchanges", "attempt_verdict", "undecided", "acked"),
        ("exchanges", "attempt_detail", "first", "second"),
        ("exchanges", "attempt_error_code", "E_NETWORK_TIMEOUT", "E_PROTOCOL_MISMATCH"),
    ],
)
def test_v3_attempt_columns_allow_first_value_and_refuse_rewrite(
    ctx, project, table, field, first, second
):
    unreferenced_pair(ctx, project, table)
    with ctx.store.connection() as conn:
        conn.execute(f"UPDATE {table} SET {field}=? WHERE id='row_0'", (first,))
    message = (
        "discussion attempt is write once"
        if table == "discussions"
        else "exchange attempt is write once"
    )
    expect_guard(
        ctx.store, table, f"UPDATE {table} SET {field}=? WHERE id='row_0'", (second,), message
    )
