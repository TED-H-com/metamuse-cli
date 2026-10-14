"""AT-024/025/033/042: verified immutable Muse refs and canonical schema-4 upgrades."""

import json
import sqlite3

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from metamuse import app
from metamuse import models as m
from metamuse import store as core
from metamuse.paths import WorkspacePaths
from metamuse.schemas import schema_for


def observation(ctx, project, *, backend="fixture", identity="obs_first", text="First reply"):
    discussion = ctx.store.insert(
        m.Discussion(
            id="dsc_" + identity,
            project_id=project,
            provider="fixture",
            account="fixture",
            auth_ref="fixture",
            backend=backend,
            topic="Synthetic evidence",
        )
    )
    event = {"payload": {"role": "assistant", "status": "running", "content": text}}
    record = ctx.store.insert(
        m.MuseObservation(
            id=identity,
            discussion_id=discussion.id,
            identity_key="reply",
            raw_event=event,
            observation_digest=m.payload_digest(event),
            observed_at=ctx.clock(),
            backend=backend,
        )
    )
    return record, {"kind": "muse_observation", "id": record.id, "revision": 1}


def save(ctx, project, refs, key="draft"):
    return app.save_draft(
        ctx,
        {
            "project_id": project,
            "text": "Host text",
            "platform": "generic",
            "facts_refs": refs,
            "request_key": key,
        },
    )


@pytest.mark.parametrize("backend", ["fixture", "live"])
def test_verified_backend_and_immutable_text_follow_transitive_drafts(ctx, project, backend):
    first, ref = observation(ctx, project, backend=backend)
    draft = save(ctx, project, [ref])
    assert draft.status == "ok"
    assert draft.data["mock"] is (backend == "fixture")
    assert draft.data["backend"] == backend and draft.data["research_complete"] is False
    later_event = {
        "payload": {"role": "assistant", "status": "completed", "content": "Later enriched reply"}
    }
    ctx.store.insert(
        m.MuseObservation(
            id="obs_later",
            discussion_id=first.discussion_id,
            identity_key=first.identity_key,
            raw_event=later_event,
            observation_digest=m.payload_digest(later_event),
            observed_at=ctx.clock(),
            backend=backend,
        )
    )
    child = {"kind": "draft_revision", "id": draft.data["draft_id"], "revision": 1}
    parent = save(ctx, project, [child], "parent")
    preview = app.preview_draft(ctx, {"draft_id": parent.data["draft_id"], "revision": 1})
    assert preview.data["muse_evidence"] == draft.data["muse_evidence"]
    exported = app.export_objects(
        ctx,
        {
            "project_id": project,
            "selection": [{"kind": "draft_revision", "id": parent.data["draft_id"], "revision": 1}],
            "format": "jsonl",
            "request_key": "export",
        },
    )
    assert exported.status == "ok"
    records = [
        json.loads(line)
        for line in (ctx.paths.root / exported.data["artifact"] / "records.jsonl")
        .read_bytes()
        .splitlines()
    ]
    assert len(records) == 3
    assert all(record["backend"] == backend for record in records)
    assert all(record["research_complete"] is False for record in records)
    cited = next(record for record in records if record["ref"]["kind"] == "muse_observation")
    assert cited["text"] == "First reply" and cited["source"]["raw_status"] == "running"
    assert cited["muse_evidence"][0]["observation_digest"] == first.observation_digest


def test_missing_foreign_and_forged_observation_refs_are_rejected(ctx, project):
    _, ref = observation(ctx, project)
    other = app.create_project(
        ctx, {"name": "Other synthetic project", "request_key": "other"}
    ).data["id"]
    for owner, refs, key in (
        (other, [ref], "foreign"),
        (project, [{**ref, "id": "obs_missing"}], "missing"),
        (project, [{**ref, "backend": "live", "mock": False}], "forged"),
        (project, [{**ref, "revision": 2}], "revision"),
    ):
        result = save(ctx, owner, refs, key)
        assert result.status == "error"
        assert result.error.code in (m.ErrorCode.REFERENCE_INVALID, m.ErrorCode.INPUT_INVALID)
    packet = app.prepare_packet(
        ctx, {"project_id": project, "selection": [ref], "request_key": "packet"}
    )
    assert packet.error.code == m.ErrorCode.REFERENCE_INVALID
    with pytest.raises(ValidationError):
        m.EvidenceRef.model_validate({**ref, "revision": 2})
    schema = Draft202012Validator(schema_for(m.EvidenceRef))
    assert schema.is_valid(ref) and not schema.is_valid({**ref, "revision": 2})
    assert not schema.is_valid({**ref, "mock": False})
    # Citing model text never changes grants or queues further work.
    assert save(ctx, project, [ref], "valid").status == "ok"
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM permits").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.parametrize("bad_ref", ["missing", "foreign", "revision"])
def test_migration004_guards_direct_sql_draft_links(ctx, project, bad_ref):
    _, ref = observation(ctx, project)
    other = app.create_project(ctx, {"name": "Other", "request_key": "other"}).data["id"]
    owner = other if bad_ref == "foreign" else project
    if bad_ref == "missing":
        ref["id"] = "obs_missing"
    if bad_ref == "revision":
        ref["revision"] = 2
    ctx.store.insert(m.Draft(id="drf_sql", project_id=owner))
    with ctx.store.connection() as conn:
        with pytest.raises(
            sqlite3.IntegrityError, match="draft Muse evidence must exist in project at revision 1"
        ):
            conn.execute(
                "INSERT INTO draft_revisions"
                "(draft_id,revision,text,platform,variant,facts_refs,assets,preferences,digest) "
                "VALUES(?,1,?,?,?,?,'[]','{}',?)",
                (
                    "drf_sql",
                    "Host text",
                    "generic",
                    "{}",
                    m.canonical_json([ref]).decode(),
                    m.payload_digest("host"),
                ),
            )
        assert conn.execute("SELECT count(*) FROM draft_revisions").fetchone()[0] == 0
        with pytest.raises(
            sqlite3.IntegrityError, match="Muse observations cannot be re-shared in packets"
        ):
            conn.execute(
                "INSERT INTO packets"
                "(id,project_id,selection,rendered_text,redactions,coverage_status,digest,"
                "created_at,stale) VALUES(?,?,?,?,'[]','bounded',?,?,0)",
                (
                    "pkt_sql",
                    project,
                    m.canonical_json([ref]).decode(),
                    "Model data",
                    m.payload_digest("model"),
                    ctx.clock(),
                ),
            )


def versioned(root, version):
    return core.Store(
        WorkspacePaths.resolve(root),
        schema_version=version,
        migrations={key: value for key, value in core.MIGRATIONS.items() if key <= version},
    )


def signature(store):
    with store.connection() as conn:
        core._verify_schema(conn, store.schema_version, store._migrations)
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return core._schema_signature(conn)


def snapshot(root):
    return {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_v3_upgrade_and_archive_restore_match_fresh_v4_and_replay(tmp_path):
    old = versioned(tmp_path / "v3", 3)
    ctx = app.AppContext(old.paths, old)
    project_request = {"name": "Synthetic v3 archive", "request_key": "project"}
    project = app.create_project(ctx, project_request)
    draft_request = {
        "project_id": project.data["id"],
        "text": "Original v3 host text",
        "platform": "generic",
        "request_key": "draft",
    }
    draft = app.save_draft(ctx, draft_request)
    export_request = {
        "project_id": project.data["id"],
        "selection": [{"kind": "draft_revision", "id": draft.data["draft_id"], "revision": 1}],
        "format": "bundle",
        "request_key": "export",
    }
    exported = app.export_objects(ctx, export_request)
    assert exported.status == "ok"
    old_signature = signature(old)
    archive = old.backup(tmp_path / "archive-v3")
    assert archive.status == "complete"
    archive_before, source_before = snapshot(archive.path), snapshot(old.paths.root)
    restored = core.Store.restore(archive.path, WorkspacePaths.resolve(tmp_path / "restored"))
    assert restored.status == "complete" and restored.store.schema_version == core.SCHEMA_VERSION
    fresh = core.Store(WorkspacePaths.resolve(tmp_path / "fresh"))
    assert signature(restored.store) == signature(fresh)
    assert snapshot(archive.path) == archive_before and snapshot(old.paths.root) == source_before
    upgraded = core.Store(old.paths)
    assert upgraded.schema_version == core.SCHEMA_VERSION and signature(upgraded) == signature(
        fresh
    )
    old_guards = {(name, table) for kind, name, table, _ in old_signature if kind == "trigger"}
    assert old_guards <= {
        (name, table) for kind, name, table, _ in signature(upgraded) if kind == "trigger"
    }
    for store in (upgraded, restored.store):
        current = app.AppContext.open(store.paths.root)
        assert app.create_project(current, project_request).data == project.data
        assert app.save_draft(current, draft_request).data == draft.data
        assert app.export_objects(current, export_request).data == exported.data
        assert store.pre_upgrade_backups()[0]["status"] == "complete"
        before = next((store.paths.root / "backups").glob("pre_upgrade_*"))
        with core._archive_connection(before / "state.sqlite3") as conn:
            core._verify_schema(conn, 3, core.MIGRATIONS)
        record, ref = observation(current, project.data["id"])
        assert save(current, project.data["id"], [ref], "linked-v4").status == "ok"
        with store.connection() as conn:
            row = dict(
                conn.execute(
                    "SELECT rowid,* FROM muse_observations WHERE id=?", (record.id,)
                ).fetchone()
            )
            fields = list(row)
            incoming = {**row, "id": "obs_replacement", "identity_key": "replacement"}
            with pytest.raises(sqlite3.IntegrityError, match="observation identity retained"):
                conn.execute(
                    f"INSERT OR REPLACE INTO muse_observations({','.join(fields)}) "
                    f"VALUES({','.join('?' for _ in fields)})",
                    tuple(incoming[field] for field in fields),
                )
            with pytest.raises(sqlite3.IntegrityError, match="observation identity is immutable"):
                conn.execute(
                    "UPDATE OR REPLACE muse_observations SET rowid=rowid+100 WHERE id=?",
                    (record.id,),
                )


@pytest.mark.parametrize("guard", ["draft_muse_evidence_guard", "packet_muse_evidence_guard"])
def test_v4_schema_guard_loss_is_detected_before_upgrade(ctx, tmp_path, guard):
    with ctx.store.connection() as conn:
        conn.execute(f"DROP TRIGGER {guard}")
    with pytest.raises(m.CoreError, match="canonical"):
        core.Store(ctx.paths)
    assert not list((ctx.paths.root / "backups").glob("pre_upgrade_*"))
