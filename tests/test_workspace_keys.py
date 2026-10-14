"""Internal workspace key lifecycle and backward-compatible stateless cursors."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from metamuse import app
from metamuse import models as m
from metamuse.paths import WorkspacePaths
from metamuse.store import CONTENT_CURSOR_KEY, Store


@pytest.fixture
def search_page(ctx, project):
    source = ctx.store.insert(
        m.Source(
            id="src_keys",
            project_id=project,
            platform="generic",
            collection_method="file_import",
            fetched_at=ctx.clock(),
        )
    )
    for number in range(3):
        item = ctx.store.insert(
            m.Item(id=f"itm_key_{number}", project_id=project, source_id=source.id, kind="post")
        )
        ctx.store.insert(
            m.ItemRevision(
                item_id=item.id,
                revision=1,
                text="合成資料",
                text_hash=m.payload_digest("合成資料"),
                fetched_at=ctx.clock(),
            )
        )
    return app.search_evidence(ctx, {"project_id": project, "query": "合成", "limit": 1})


def test_initialization_provisions_key_and_search_is_read_only(ctx, project, monkeypatch):
    original = ctx.store.connection

    @contextmanager
    def read_only():
        with original() as conn:
            conn.execute("PRAGMA query_only=ON")
            yield conn

    def no_mutation(*args, **kwargs):
        pytest.fail("Search attempted key provisioning or another write")

    key = ctx.store.get_workspace_key(CONTENT_CURSOR_KEY)
    assert key.version == 1 and key.status == "active" and len(key.key_material) == 32
    assert key.key_material.hex() not in repr(key)
    monkeypatch.setattr(ctx.store, "connection", read_only)
    monkeypatch.setattr(ctx.store, "transaction", no_mutation)
    monkeypatch.setattr(ctx.store, "get_or_create_workspace_key", no_mutation)
    result = app.search_evidence(ctx, {"project_id": project, "query": "合成"})
    assert result.status == "ok"
    assert key.key_material.hex() not in m.canonical_json(result).decode()
    with original() as conn:
        assert conn.execute("SELECT count(*) FROM workspace_keys").fetchone()[0] == 1
        assert (
            conn.execute(
                "SELECT count(*) FROM request_receipts WHERE operation='content.cursor_key'"
            ).fetchone()[0]
            == 0
        )
        assert key.key_material.hex() not in str(
            conn.execute("SELECT detail FROM events").fetchall()
        )


def test_get_or_create_serializes_competing_provisioning(ctx):
    with ThreadPoolExecutor(max_workers=4) as pool:
        keys = list(
            pool.map(lambda _: ctx.store.get_or_create_workspace_key("fixture.mac"), range(8))
        )
    assert all(key == keys[0] for key in keys)
    assert len(ctx.store.workspace_verification_keys("fixture.mac")) == 1
    assert Store(ctx.paths).get_workspace_key("fixture.mac") == keys[0]


def test_rotation_retains_old_cursors_and_versions_through_restore(
    ctx, project, search_page, tmp_path
):
    first = ctx.store.get_workspace_key(CONTENT_CURSOR_KEY)
    second = ctx.store.rotate_workspace_key(CONTENT_CURSOR_KEY)
    assert second.version == 2 and second.key_material != first.key_material
    assert ctx.store.get_workspace_key(CONTENT_CURSOR_KEY, 1).status == "retired"
    backup = ctx.store.backup(tmp_path / "keys-backup")
    paths = WorkspacePaths.resolve(tmp_path / "keys-restored")
    restored = Store.restore(backup, paths)
    assert restored.get_workspace_key(CONTENT_CURSOR_KEY) == second
    assert [k.version for k in restored.workspace_verification_keys(CONTENT_CURSOR_KEY)] == [2, 1]
    result = app.search_evidence(
        app.AppContext.open(paths.root),
        {"project_id": project, "query": "合成", "cursor": search_page.next_cursor, "limit": 1},
    )
    assert result.status == "ok" and result.data["results"][0]["ref"]["id"] == "itm_key_1"
    assert first.key_material.hex() not in (backup / "manifest.json").read_text()


@pytest.mark.parametrize("old_table", [True, False])
def test_legacy_receipt_imported_once_and_cursors_still_verify(
    ctx, project, search_page, old_table
):
    key = ctx.store.get_workspace_key(CONTENT_CURSOR_KEY)
    if not old_table:
        ctx.store.rotate_workspace_key(CONTENT_CURSOR_KEY)
    with ctx.store.transaction() as conn:
        if old_table:
            conn.execute("DROP TABLE workspace_keys")
        ctx.store.mutate_once(
            conn,
            "content.cursor_key",
            "v1",
            {"version": 1},
            lambda: {"key": key.key_material.hex()},
        )
    reopened = app.AppContext.open(ctx.paths.root)
    assert reopened.store.get_workspace_key(CONTENT_CURSOR_KEY, 1).key_material == key.key_material
    with reopened.store.connection() as conn:
        assert not conn.execute(
            "SELECT 1 FROM request_receipts WHERE operation='content.cursor_key'"
        ).fetchone()
    result = app.search_evidence(
        reopened, {"project_id": project, "query": "合成", "cursor": search_page.next_cursor}
    )
    assert result.status == "ok" and len(result.data["results"]) == 2
    assert len(Store(ctx.paths).workspace_verification_keys(CONTENT_CURSOR_KEY)) == (
        1 if old_table else 2
    )
    if old_table:
        assert list((ctx.paths.root / "backups").glob("pre_upgrade_*"))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE workspace_keys SET key_material=zeroblob(32)",
        "UPDATE workspace_keys SET version=99",
        "UPDATE workspace_keys SET status='active' WHERE version=1",
        "DELETE FROM workspace_keys WHERE version=1",
        "INSERT INTO workspace_keys VALUES('content.cursor',3,zeroblob(32),'synthetic','active')",
    ],
)
def test_key_identity_and_retention_guards(ctx, statement):
    ctx.store.rotate_workspace_key(CONTENT_CURSOR_KEY)
    with pytest.raises(m.CoreError):
        with ctx.store.transaction() as conn:
            conn.execute(statement)
    assert len(ctx.store.workspace_verification_keys(CONTENT_CURSOR_KEY)) == 2


def test_corrupt_legacy_key_fails_without_leaking_material(ctx):
    with ctx.store.transaction() as conn:
        conn.execute(
            "INSERT INTO request_receipts VALUES('content.cursor_key','v1',?,?)",
            (m.payload_digest({"version": 1}), '{"key":"SYNTHETIC_INVALID_SECRET"}'),
        )
    with pytest.raises(m.CoreError) as error:
        Store(ctx.paths)
    assert error.value.detail.code == m.ErrorCode.STORAGE
    assert "SYNTHETIC_INVALID_SECRET" not in str(error.value)
