"""OFFLINE: additive removal migration and the narrowly granted core invariants."""

import sqlite3

import pytest
from pydantic import ValidationError

from metamuse import models as m
from metamuse import store as core
from metamuse.paths import WorkspacePaths


def segment(project, **values):
    return m.TranscriptSegment(
        id="cue_removed",
        project_id=project,
        source_id="src_removal",
        revision=1,
        start_ms=0,
        end_ms=1000,
        text="Original cue",
        method="provided",
        **values,
    )


def anchor(store):
    project = store.insert(m.Project(id="prj_removal", name="Synthetic", created_at=store.clock()))
    store.insert(
        m.Source(
            id="src_removal",
            project_id=project.id,
            platform="generic",
            collection_method="fixture",
            fetched_at=store.clock(),
        )
    )
    return project.id


@pytest.mark.parametrize("revision,text", [(1, ""), (2, "not empty")])
def test_removal_model_requires_empty_text_and_later_revision(revision, text):
    with pytest.raises(ValidationError, match="transcript removal"):
        m.TranscriptSegment(
            id="cue_removed",
            project_id="prj_removal",
            source_id="src_removal",
            revision=revision,
            start_ms=0,
            end_ms=1000,
            text=text,
            method="provided",
            removed=True,
        )


@pytest.mark.parametrize("kind", ["revision_one", "text", "no_prior", "double", "out_of_order"])
def test_removal_store_requires_prior_text_bearing_row(ctx, kind):
    project = anchor(ctx.store)
    base = segment(project)
    if kind != "no_prior":
        ctx.store.insert(base)
    removed = base.model_copy(update={"revision": 2, "text": "", "removed": True})
    if kind == "double":
        ctx.store.insert(removed)
        removed = removed.model_copy(update={"revision": 3})
    elif kind == "revision_one":
        removed = removed.model_copy(update={"revision": 1})
    elif kind == "text":
        removed = removed.model_copy(update={"text": "not empty"})
    elif kind == "out_of_order":
        ctx.store.insert(base.model_copy(update={"revision": 3}))
    with pytest.raises(m.CoreError) as held:
        ctx.store.insert(removed)
    assert held.value.detail.code == m.ErrorCode.REFERENCE_INVALID
    assert (
        held.value.detail.message == "Transcript removal requires an earlier text-bearing revision"
    )


def test_removal_previous_schema_upgrade_backup_and_guards(tmp_path):
    previous = core.SCHEMA_VERSION - 1
    old = core.Store(
        WorkspacePaths.resolve(tmp_path / "old"),
        schema_version=previous,
        migrations={n: p for n, p in core.MIGRATIONS.items() if n <= previous},
    )
    project = anchor(old)
    with old.transaction() as conn:
        conn.execute(
            "INSERT INTO transcript_segments (id,project_id,source_id,revision,cue_index,"
            "start_ms,end_ms,text,language,method,review,overlap) "
            "VALUES(?,?,?,1,0,0,1000,'Original cue','en','provided','unreviewed',0)",
            ("cue_removed", project, "src_removal"),
        )
    archive = old.backup(tmp_path / "backup")
    assert archive.status == "complete"
    restored = core.Store.restore(archive.path, WorkspacePaths.resolve(tmp_path / "restored"))
    assert restored.status == "complete" and restored.store.schema_version == core.SCHEMA_VERSION
    upgraded = core.Store(old.paths)
    assert upgraded.schema_version == core.SCHEMA_VERSION
    for store in (upgraded, restored.store):
        row = store.get(m.TranscriptSegment, "cue_removed")
        assert row.removed is False and row.text == "Original cue"
        with store.connection() as conn:
            core._verify_schema(conn, core.SCHEMA_VERSION, core.MIGRATIONS)
        with store.transaction() as conn:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute("UPDATE transcript_segments SET text='' WHERE id='cue_removed'")
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute(
                    "INSERT OR REPLACE INTO transcript_segments SELECT * FROM transcript_segments "
                    "WHERE id='cue_removed'"
                )


def test_sqlite_add_column_checks_existing_rows():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE cue(text TEXT NOT NULL)")
        conn.execute("INSERT INTO cue VALUES('text-bearing')")
        conn.commit()
        conn.execute("BEGIN")
        with pytest.raises(sqlite3.OperationalError, match="CHECK constraint failed"):
            conn.execute(
                "ALTER TABLE cue ADD COLUMN removed INTEGER NOT NULL DEFAULT 1 "
                "CHECK(removed IN (0,1) AND (removed=0 OR text=''))"
            )
        conn.rollback()
        conn.execute(
            "ALTER TABLE cue ADD COLUMN removed INTEGER NOT NULL DEFAULT 0 "
            "CHECK(removed IN (0,1) AND (removed=0 OR text=''))"
        )
        assert conn.execute("SELECT removed FROM cue").fetchone()[0] == 0
    finally:
        conn.close()
