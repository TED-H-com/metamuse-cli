"""Ready export artifact snapshots, private restore and honest corruption failures."""

import json

import pytest

from metamuse import app
from metamuse import models as m
from metamuse.paths import WorkspacePaths
from metamuse.store import CONTENT_CURSOR_KEY, Store


@pytest.fixture
def export_request(ctx, project):
    saved = app.save_draft(
        ctx,
        {
            "project_id": project,
            "text": "合成匯出😀\r\ne\u0301",
            "platform": "generic",
            "request_key": "draft",
        },
    )
    assert saved.status == "ok"
    return {
        "project_id": project,
        "selection": [{"kind": "draft_revision", "id": saved.data["draft_id"], "revision": 1}],
        "request_key": "export",
        "format": "jsonl",
    }


def ready_export(ctx, request):
    result = app.export_objects(ctx, request)
    assert result.status == "ok"
    return result.data


def assert_private_tree(root):
    for path in (root, *root.rglob("*")):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600), path


def test_all_ready_exports_restored_and_old_receipts_replay(ctx, export_request, tmp_path):
    requests = [
        {**export_request, "format": fmt, "request_key": fmt}
        for fmt in ("text", "markdown", "jsonl", "csv", "bundle")
    ]
    original = [ready_export(ctx, request) for request in requests]
    # Uncommitted/orphan artifacts are not silently folded into a ready snapshot.
    (ctx.paths.root / "exports" / "orphan").mkdir()
    backup = ctx.store.backup(tmp_path / "backup")
    manifest = json.loads((backup / "manifest.json").read_bytes())
    assert {entry["id"] for entry in manifest["exports"]} == {data["id"] for data in original}
    assert not (backup / "exports" / "orphan").exists()
    paths = WorkspacePaths.resolve(tmp_path / "restored")
    Store.restore(backup, paths)
    restored = app.AppContext.open(paths.root)
    for request, expected in zip(requests, original, strict=True):
        assert ready_export(restored, request) == expected
        for path in (ctx.paths.root / expected["artifact"]).iterdir():
            assert (paths.root / expected["artifact"] / path.name).read_bytes() == path.read_bytes()
        restored.store.verify_export(restored.store.get(m.Export, expected["id"]))
    key = ctx.store.get_workspace_key(CONTENT_CURSOR_KEY).key_material.hex().encode()
    assert all(
        key not in path.read_bytes() for path in (backup / "exports").rglob("*") if path.is_file()
    )
    assert_private_tree(backup)
    assert_private_tree(paths.root)


def damage(directory, kind):
    member = directory / "records.jsonl"
    if kind == "same_size_corruption":
        raw = member.read_bytes()
        member.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    elif kind == "missing_member":
        member.unlink()
    elif kind == "unexpected_file":
        (directory / "extra.txt").write_bytes(b"synthetic unexpected member")
    elif kind == "unexpected_directory":
        (directory / "extra").mkdir()
    elif kind == "manifest_changed":
        (directory / "manifest.json").write_bytes(b"{}\n")
    elif kind == "symlink_member":
        member.rename(directory / "moved")
        member.symlink_to(directory / "moved")
    elif kind == "missing_directory":
        for path in directory.iterdir():
            path.unlink()
        directory.rmdir()
    else:
        raise AssertionError(kind)


DAMAGE = [
    "same_size_corruption",
    "missing_member",
    "unexpected_file",
    "unexpected_directory",
    "manifest_changed",
    "symlink_member",
    "missing_directory",
]


@pytest.mark.parametrize("kind", DAMAGE)
def test_backup_rejects_incomplete_artifact_and_cleans_partial_destination(
    ctx, export_request, tmp_path, kind
):
    data = ready_export(ctx, export_request)
    damage(ctx.paths.root / data["artifact"], kind)
    destination = tmp_path / "failed-backup"
    with pytest.raises(m.CoreError) as error:
        ctx.store.backup(destination)
    assert error.value.detail.code == m.ErrorCode.RESOURCE_UNAVAILABLE
    assert not destination.exists()
    assert ctx.store.get(m.Export, data["id"]).state == "ready"
    assert app.export_objects(ctx, export_request).status == "error"


@pytest.mark.parametrize("kind", DAMAGE)
def test_restore_rejects_corrupt_or_unexpected_members_before_copy(
    ctx, export_request, tmp_path, kind
):
    data = ready_export(ctx, export_request)
    backup = ctx.store.backup(tmp_path / "backup")
    damage(backup / data["artifact"], kind)
    paths = WorkspacePaths.resolve(tmp_path / "invalid-restore")
    with pytest.raises(m.CoreError):
        Store.restore(backup, paths)
    assert not paths.root.exists()


def test_restore_rejects_unlisted_export_directory(ctx, export_request, tmp_path):
    ready_export(ctx, export_request)
    backup = ctx.store.backup(tmp_path / "backup")
    (backup / "exports" / "unlisted").mkdir()
    with pytest.raises(m.CoreError) as error:
        Store.restore(backup, WorkspacePaths.resolve(tmp_path / "invalid"))
    assert error.value.detail.code == m.ErrorCode.STORAGE


def test_restore_requires_export_inventory_to_match_database(ctx, export_request, tmp_path):
    ready_export(ctx, export_request)
    backup = ctx.store.backup(tmp_path / "backup")
    path = backup / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["exports"] = []
    path.write_bytes(m.canonical_json(manifest))
    with pytest.raises(m.CoreError) as error:
        Store.restore(backup, WorkspacePaths.resolve(tmp_path / "invalid"))
    assert error.value.detail.code == m.ErrorCode.STORAGE
