"""Artifact verifier unit regression using a bundle built by shipped CLI processes."""

import copy
import hashlib
import json

import pytest
from test_approval_review import send
from test_approval_review import world as world
from test_at042_slice import HOST_TEXT, verify_export


@pytest.mark.parametrize("label", ["backend", "mock", "research_complete"])
def test_export_verifier_detects_stripped_mock_label_from_cli_bundle(world, label):
    entry, project, _, request = world
    preview = send(entry, request)["approval"]
    grant = entry.approve(preview, request, "share-grant")["data"]["id"]
    sent = send(entry, {**request, "share_grant_id": grant}, code=0)["data"]
    entry.cli("worker", "run", "--once")
    exchange = sent["exchange_id"]
    read_request = {"exchange_id": exchange, "refresh": True}
    preview = entry.cli("muse", "read", "--exchange", exchange, "--refresh", code=3)["approval"]
    grant = entry.approve(preview, read_request, "read-grant")["data"]["id"]
    entry.cli("muse", "read", "--exchange", exchange, "--refresh", "--access-grant", grant)
    entry.cli("worker", "run", "--once")
    read = entry.cli("muse", "read", "--exchange", exchange)["data"]
    observed = next(
        o
        for o in read["observations"]
        if o["raw_event"].get("payload", {}).get("role") == "assistant"
    )
    text = entry.inputs / "draft.txt"
    text.write_bytes(HOST_TEXT.encode())
    facts = entry.inputs / "facts.json"
    facts.write_text(json.dumps([observed["evidence_ref"]]))
    saved = entry.cli(
        "draft",
        "save",
        "--project",
        project,
        "--text-file",
        text,
        "--platform",
        "generic",
        "--facts-file",
        facts,
        "--request-key",
        "draft",
    )["data"]
    refs = entry.inputs / "export-refs.json"
    refs.write_text(
        json.dumps([{"kind": "draft_revision", "id": saved["draft_id"], "revision": 1}])
    )
    exported = entry.cli(
        "export",
        "--project",
        project,
        "--refs-file",
        refs,
        "--format",
        "bundle",
        "--request-key",
        "export",
    )["data"]
    verify_export(entry.workspace, exported, observed)
    # This remaining check is explicitly a verifier unit test: repair the file's
    # manifest hashes after stripping one label, so label loss is the only defect.
    directory = entry.workspace / exported["artifact"]
    path = directory / "records.jsonl"
    records = [json.loads(line) for line in path.read_bytes().splitlines()]
    del records[0][label]
    raw = b"".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        for row in records
    )
    path.write_bytes(raw)
    altered = copy.deepcopy(exported)
    manifest = altered["manifest"]
    member = next(member for member in manifest["files"] if member["name"] == path.name)
    member.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises((KeyError, AssertionError)):
        verify_export(entry.workspace, altered, observed)
