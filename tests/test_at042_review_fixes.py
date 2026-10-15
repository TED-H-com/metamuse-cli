"""B10/FR-021/AT-042/AT-028 independent-review regressions; no network or accounts."""

import copy
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import traceback
from pathlib import Path

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from metamuse import app
from metamuse import models as m
from metamuse import store as core
from metamuse.paths import WorkspacePaths

SECRET = "SYNTHETIC_REVIEW_COOKIE"
EXPORT_ERROR = "Export artifact is unavailable or changed"


def observation(ctx, project, identity="obs_local", text="Synthetic progress", backend="fixture"):
    discussion = ctx.store.insert(
        m.Discussion(
            id="dsc_" + identity,
            project_id=project,
            provider="fixture",
            account="fixture",
            auth_ref="fixture",
            backend=backend,
            topic="Synthetic review",
        )
    )
    event = {"payload": {"role": "assistant", "content": text, "status": "running"}}
    ctx.store.insert(
        m.MuseObservation(
            id=identity,
            discussion_id=discussion.id,
            identity_key=identity,
            raw_event=event,
            observation_digest=m.payload_digest(event),
            observed_at=ctx.clock(),
            backend=backend,
        )
    )
    return {"kind": "muse_observation", "id": identity, "revision": 1}


def raw_draft(conn, project, refs, *, identity="drf_raw", variant="{}", preferences="{}"):
    conn.execute(
        "INSERT INTO drafts(id,project_id,current_revision,stale) VALUES(?,?,1,0)",
        (identity, project),
    )
    conn.execute(
        "INSERT INTO draft_revisions"
        "(draft_id,revision,text,platform,variant,facts_refs,assets,preferences,digest) "
        "VALUES(?,1,'Synthetic host text','generic',?,?,'[]',?,?)",
        (identity, variant, refs, preferences, m.payload_digest("synthetic")),
    )


@pytest.mark.parametrize("table", ["draft_revisions", "packets"])
@pytest.mark.parametrize("key", ["kind", "id", "revision"])
@pytest.mark.parametrize("order", ["safe_first", "unsafe_first", "identical"])
def test_duplicate_identity_keys_fail_raw_sql_even_when_first_key_looks_safe(
    ctx, project, table, key, order
):
    local = observation(ctx, project)
    if table == "packets" and key != "kind":
        local["kind"] = "item_revision"
    other = app.create_project(ctx, {"name": "Other synthetic", "request_key": "other"}).data["id"]
    observation(ctx, other, "obs_foreign")
    # Duplicate kind starts as original evidence; duplicate id starts in the local project.
    alternatives = {
        "kind": ["item_revision", "muse_observation"],
        "id": ["obs_local", "obs_foreign"],
        "revision": [1, 2],
    }
    values = alternatives[key]
    if order == "unsafe_first":
        values = values[::-1]
    elif order == "identical":
        values = [values[0], values[0]]
    pairs = [(name, value) for name, value in local.items() if name != key]
    pairs.extend((key, value) for value in values)
    raw = (
        "[{" + ",".join(json.dumps(name) + ":" + json.dumps(value) for name, value in pairs) + "}]"
    )
    with ctx.store.transaction() as conn:
        with pytest.raises(
            sqlite3.IntegrityError, match="draft Muse evidence|Muse observations cannot"
        ):
            if table == "draft_revisions":
                raw_draft(conn, project, raw)
            else:
                conn.execute(
                    "INSERT INTO packets(id,project_id,selection,rendered_text,redactions,"
                    "coverage_status,digest,created_at,stale) "
                    "VALUES(?,?,?,'Synthetic','[]','bounded',?,?,0)",
                    ("pkt_raw", project, raw, m.payload_digest("synthetic"), ctx.clock()),
                )
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("revision", [True, False, 1.0, "1", None, 2])
def test_muse_revision_requires_json_integer_one_at_sql_boundary(ctx, project, revision):
    ref = observation(ctx, project)
    raw = json.dumps([{**ref, "revision": revision}])
    with ctx.store.transaction() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="draft Muse evidence"):
            raw_draft(conn, project, raw)
        assert conn.execute("SELECT count(*) FROM draft_revisions").fetchone()[0] == 0


@pytest.mark.parametrize("field", ["variant", "preferences"])
@pytest.mark.parametrize("nested", [False, True])
def test_store_decode_rejects_duplicate_json_keys_without_value_echo(ctx, project, field, nested):
    value = '{"language":"und","language":' + json.dumps(SECRET) + "}"
    raw = '{"nested":' + value + "}" if nested else value
    with ctx.store.transaction() as conn:
        raw_draft(conn, project, "[]", **{field: raw})
    with pytest.raises(m.CoreError) as caught:
        ctx.store.get(m.DraftRevision, "drf_raw", revision=1)
    assert caught.value.detail.code == m.ErrorCode.STORAGE
    assert caught.value.detail.message == "Stored record violates its contract"
    assert caught.value.__cause__ is None
    assert SECRET not in "".join(traceback.format_exception(caught.value))


def test_receipt_decode_rejects_duplicate_keys_without_echo(ctx):
    with ctx.store.transaction() as conn:
        conn.execute(
            "INSERT INTO request_receipts VALUES('review','duplicate',?,?)",
            (
                m.payload_digest({}),
                '{"nested":{"value":"safe","value":' + json.dumps(SECRET) + "}}",
            ),
        )
    with ctx.store.transaction() as conn:
        with pytest.raises(m.CoreError) as caught:
            ctx.store.mutate_once(conn, "review", "duplicate", {}, lambda: {})
    assert caught.value.detail.code == m.ErrorCode.STORAGE
    assert caught.value.detail.message == "Stored receipt is invalid"
    assert caught.value.__cause__ is None
    assert SECRET not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("model", [m.DraftRevision, m.Packet])
def test_store_rejects_duplicate_evidence_keys_already_stored_in_v3(tmp_path, model):
    old = core.Store(
        WorkspacePaths.resolve(tmp_path / "v3"),
        schema_version=3,
        migrations={key: value for key, value in core.MIGRATIONS.items() if key <= 3},
    )
    ctx = app.AppContext(old.paths, old)
    project = app.create_project(ctx, {"name": "Synthetic v3", "request_key": "project"}).data["id"]
    observation(ctx, project)
    raw = (
        '[{"kind":"muse_observation","id":"obs_local","revision":1,"id":'
        + json.dumps(SECRET)
        + "}]"
    )
    with old.transaction() as conn:
        if model is m.DraftRevision:
            raw_draft(conn, project, raw)
        else:
            conn.execute(
                "INSERT INTO packets(id,project_id,selection,rendered_text,redactions,"
                "coverage_status,digest,created_at,stale) "
                "VALUES('pkt_raw',?,?,'Synthetic','[]','bounded',?,?,0)",
                (project, raw, m.payload_digest("synthetic"), ctx.clock()),
            )
    with pytest.raises(m.CoreError) as caught:
        if model is m.DraftRevision:
            old.get(model, "drf_raw", revision=1)
        else:
            old.get(model, "pkt_raw")
    assert caught.value.detail.code == m.ErrorCode.STORAGE
    assert caught.value.detail.message == "Stored record violates its contract"
    assert caught.value.__cause__ is None
    assert SECRET not in "".join(traceback.format_exception(caught.value))


def bundle(ctx, project, *, text="Synthetic progress", backend="fixture"):
    ref = observation(ctx, project, text=text, backend=backend)
    saved = app.save_draft(
        ctx,
        {
            "project_id": project,
            "text": "Host-written synthetic draft",
            "platform": "generic",
            "facts_refs": [ref],
            "request_key": "draft",
        },
    )
    assert saved.status == "ok"
    response = app.export_objects(
        ctx,
        {
            "project_id": project,
            "selection": [{"kind": "draft_revision", "id": saved.data["draft_id"], "revision": 1}],
            "format": "bundle",
            "request_key": "bundle",
        },
    )
    assert response.status == "ok", response
    return m.Export.model_validate(response.data)


def repair_files(directory, manifest):
    for entry in manifest["files"]:
        raw = (directory / entry["name"]).read_bytes()
        entry.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    (directory / "manifest.json").write_bytes(m.canonical_json(manifest) + b"\n")


def corrupt_attribution(ctx, exported, target, label, replacement):
    directory = ctx.paths.root / exported.artifact
    manifest = copy.deepcopy(exported.manifest)
    path = directory / "records.jsonl"
    records = [json.loads(line) for line in path.read_bytes().splitlines()]
    if target.startswith("manifest"):
        value = manifest["muse_evidence"][0] if target == "manifest_entry" else manifest
    elif target.startswith("markdown"):
        path = directory / "object-0001.md"
        header = path.read_text().split("\n\n", 2)
        source = json.loads(header[1][len("Source: ") :])
        value = source["muse_evidence"][0] if target == "markdown_entry" else source
    else:
        record = records[-1] if target == "observation" else records[0]
        value = record["muse_evidence"][0] if target == "record_entry" else record
        if target == "record_source":
            value = record["source"]
    if replacement == "remove":
        del value[label]
    else:
        value[label] = {"backend": "live", "mock": False, "research_complete": True}[label]
    if target.startswith("markdown"):
        header[1] = "Source: " + m.canonical_json(source).decode()
        path.write_text("\n\n".join(header))
    elif not target.startswith("manifest"):
        path.write_bytes(b"".join(m.canonical_json(record) + b"\n" for record in records))
    repair_files(directory, manifest)
    return exported.model_copy(update={"manifest": manifest})


@pytest.mark.parametrize(
    "target",
    [
        "draft",
        "observation",
        "record_source",
        "record_entry",
        "manifest",
        "manifest_entry",
        "markdown_source",
        "markdown_entry",
    ],
)
@pytest.mark.parametrize("label", ["backend", "mock", "research_complete"])
@pytest.mark.parametrize("replacement", ["remove", "alter"])
def test_product_verifier_rejects_label_loss_or_lies_even_with_consistent_bytes(
    ctx, project, target, label, replacement
):
    exported = bundle(ctx, project)
    assert ctx.store.verify_export(exported).is_dir()
    altered = corrupt_attribution(ctx, exported, target, label, replacement)
    # This is the review's in-memory manifest probe, not the slice's assertion helper.
    with pytest.raises(m.CoreError) as caught:
        core._verify_export(altered.model_dump(mode="json"), ctx.paths.root)
    assert caught.value.detail.code == m.ErrorCode.RESOURCE_UNAVAILABLE
    assert caught.value.detail.message == EXPORT_ERROR
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("target", ["manifest", "draft", "observation"])
def test_product_verifier_requires_muse_evidence(ctx, project, target):
    exported = bundle(ctx, project)
    altered = corrupt_attribution(ctx, exported, target, "muse_evidence", "remove")
    with pytest.raises(m.CoreError, match=EXPORT_ERROR):
        ctx.store.verify_export(altered)


@pytest.mark.parametrize("label,number", [("mock", 1), ("research_complete", 0)])
@pytest.mark.parametrize("target", ["manifest", "manifest_entry", "draft", "record_entry"])
def test_product_verifier_rejects_numbers_in_place_of_boolean_labels(
    ctx, project, label, number, target
):
    exported = bundle(ctx, project)
    directory = ctx.paths.root / exported.artifact
    manifest = copy.deepcopy(exported.manifest)
    records = [json.loads(line) for line in (directory / "records.jsonl").read_bytes().splitlines()]
    values = {
        "manifest": manifest,
        "manifest_entry": manifest["muse_evidence"][0],
        "draft": records[0],
        "record_entry": records[0]["muse_evidence"][0],
    }
    values[target][label] = number
    (directory / "records.jsonl").write_bytes(
        b"".join(m.canonical_json(record) + b"\n" for record in records)
    )
    repair_files(directory, manifest)
    with pytest.raises(m.CoreError, match=EXPORT_ERROR):
        ctx.store.verify_export(exported.model_copy(update={"manifest": manifest}))


@pytest.mark.parametrize("backend", ["fixture", "live"])
def test_product_verification_and_backup_restore_keep_stored_backend(
    ctx, project, tmp_path, backend
):
    exported = bundle(ctx, project, backend=backend)
    assert ctx.store.verify_export(exported).is_dir()
    backup = ctx.store.backup(tmp_path / "archive")
    assert backup.status == "complete"
    restored = core.Store.restore(backup.path, WorkspacePaths.resolve(tmp_path / "restored"))
    assert restored.status == "complete"
    assert restored.store.verify_export(restored.store.get(m.Export, exported.id)).is_dir()


def test_product_verifier_accepts_direct_and_transitive_mixed_evidence(ctx, project):
    fixture_ref = observation(ctx, project)
    live_ref = observation(ctx, project, "obs_metadata_only", backend="live")
    child = app.save_draft(
        ctx,
        {
            "project_id": project,
            "text": "Synthetic child",
            "platform": "generic",
            "facts_refs": [fixture_ref, live_ref],
            "request_key": "child",
        },
    )
    assert child.status == "ok"
    child_ref = {"kind": "draft_revision", "id": child.data["draft_id"], "revision": 1}
    parent = app.save_draft(
        ctx,
        {
            "project_id": project,
            "text": "Synthetic parent",
            "platform": "generic",
            "facts_refs": [child_ref, fixture_ref],
            "request_key": "parent",
        },
    )
    assert parent.status == "ok"
    parent_ref = {"kind": "draft_revision", "id": parent.data["draft_id"], "revision": 1}
    for key, selection in (
        ("direct", [fixture_ref, live_ref]),
        ("transitive", [parent_ref]),
        ("repeated", [parent_ref, parent_ref]),
    ):
        response = app.export_objects(
            ctx,
            {"project_id": project, "selection": selection, "format": "bundle", "request_key": key},
        )
        assert response.status == "ok", response
        exported = m.Export.model_validate(response.data)
        assert ctx.store.verify_export(exported).is_dir()
        assert exported.manifest["backend"] == "mixed"
        assert exported.manifest["mock"] is True
        assert exported.manifest["research_complete"] is False
        assert {entry["backend"] for entry in exported.manifest["muse_evidence"]} == {
            "fixture",
            "live",
        }


def cli(ctx, *arguments, expected_exit=0):
    executable = Path(sys.executable).with_name("metamuse")
    assert executable.is_file()
    result = subprocess.run(
        [
            str(executable),
            "--workspace",
            str(ctx.paths.root),
            "--json",
            "--non-interactive",
            *arguments,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == expected_exit, (result.stdout, result.stderr)
    assert SECRET not in result.stdout + result.stderr
    envelope = json.loads(result.stdout)
    m.Envelope.model_validate(envelope)
    return envelope


async def mcp_calls(ctx, calls, errlog):
    executable = Path(sys.executable).with_name("metamuse")
    params = StdioServerParameters(
        env={
            "PYTHONDONTWRITEBYTECODE": "1",
            **(
                {"B16A_IMPORT_EVIDENCE": os.environ["B16A_IMPORT_EVIDENCE"]}
                if "B16A_IMPORT_EVIDENCE" in os.environ
                else {}
            ),
            "PYTHONPATH": os.environ.get(
                "PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src")
            ),
        },
        command=str(executable),
        args=["--workspace", str(ctx.paths.root), "--non-interactive", "mcp"],
    )
    envelopes = []
    with errlog.open("w+") as log:
        async with stdio_client(params, errlog=log) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for name, arguments in calls:
                    result = await session.call_tool(name, arguments)
                    envelope = result.structured_content
                    assert json.loads(result.content[0].text) == envelope
                    assert result.is_error == (envelope["status"] == "error")
                    assert SECRET not in m.canonical_json(envelope).decode()
                    m.Envelope.model_validate(envelope)
                    envelopes.append(envelope)
    assert SECRET not in errlog.read_text()
    return envelopes


def test_cli_and_mcp_reject_duplicate_stored_json_and_invalid_ref_revisions(ctx, project, tmp_path):
    ref = observation(ctx, project)
    with ctx.store.transaction() as conn:
        raw_draft(
            conn, project, "[]", variant='{"value":"safe","value":' + json.dumps(SECRET) + "}"
        )
    preview = cli(ctx, "draft", "preview", "drf_raw", "--revision", "1", expected_exit=7)
    assert preview["error"]["message"] == "Stored record violates its contract"
    text = tmp_path / "host.txt"
    text.write_text("Host text")
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps([{**ref, "revision": True}]))
    invalid = cli(
        ctx,
        "draft",
        "save",
        "--project",
        project,
        "--text-file",
        str(text),
        "--platform",
        "generic",
        "--facts-file",
        str(facts),
        "--request-key",
        "boolean",
        expected_exit=2,
    )
    assert invalid["error"]["code"] == "E_INPUT_INVALID"
    # CLI file parsing also rejects duplicate keys before the request reaches the store.
    facts.write_text(
        '[{"kind":"muse_observation","id":"obs_local","id":"obs_foreign","revision":1}]'
    )
    duplicate = cli(
        ctx,
        "draft",
        "save",
        "--project",
        project,
        "--text-file",
        str(text),
        "--platform",
        "generic",
        "--facts-file",
        str(facts),
        "--request-key",
        "duplicate",
        expected_exit=2,
    )
    assert duplicate["error"]["code"] == "E_INPUT_INVALID"
    results = anyio.run(
        mcp_calls,
        ctx,
        [
            ("metamuse_draft_preview", {"draft_id": "drf_raw", "revision": 1}),
            (
                "metamuse_draft_save",
                {
                    "project_id": project,
                    "text": "Host text",
                    "platform": "generic",
                    "facts_refs": [{**ref, "revision": True}],
                    "request_key": "mcp-boolean",
                },
            ),
            (
                "metamuse_draft_save",
                {
                    "project_id": project,
                    "text": "Host text",
                    "platform": "generic",
                    "facts_refs": [ref],
                    "request_key": "mcp-valid",
                },
            ),
        ],
        tmp_path / "mcp-errors.log",
    )
    assert results[0]["error"]["message"] == "Stored record violates its contract"
    assert results[1]["error"]["code"] == "E_INPUT_INVALID"
    assert results[2]["status"] == "ok" and results[2]["data"]["mock"] is True


def test_cli_and_mcp_replay_use_product_attribution_verifier(ctx, project, tmp_path):
    exported = bundle(ctx, project)
    # Model a producer bug via a fresh staging row; keep all ready-row freezes intact.
    bad = ctx.store.insert(
        m.Export(
            id="exp_review_bad", project_id=project, selection=exported.selection, format="bundle"
        )
    )
    directory = ctx.paths.root / "exports" / bad.id
    shutil.copytree(ctx.paths.root / exported.artifact, directory)
    manifest = copy.deepcopy(exported.manifest)
    manifest["export_id"] = bad.id
    bad = bad.model_copy(
        update={"artifact": f"exports/{bad.id}", "manifest": manifest, "state": "ready"}
    )
    repair_files(directory, manifest)
    bad = corrupt_attribution(ctx, bad, "draft", "mock", "alter")
    request = m.ExportRequest(
        project_id=project,
        selection=exported.selection,
        format="bundle",
        request_key="bad-producer",
    )
    with ctx.store.transaction() as conn:
        conn.execute(
            "UPDATE exports SET manifest=?,artifact=?,state='ready' WHERE id=?",
            (m.canonical_json(bad.manifest).decode(), bad.artifact, bad.id),
        )
        conn.execute(
            "INSERT INTO request_receipts VALUES('export.objects',?,?,?)",
            (request.request_key, m.payload_digest(request), m.canonical_json(bad).decode()),
        )
    refs = tmp_path / "refs.json"
    refs.write_bytes(m.canonical_json([ref.model_dump(mode="json") for ref in request.selection]))
    result = cli(
        ctx,
        "export",
        "--project",
        project,
        "--refs-file",
        str(refs),
        "--format",
        "bundle",
        "--request-key",
        request.request_key,
        expected_exit=3,
    )
    assert result["error"]["message"] == EXPORT_ERROR
    results = anyio.run(
        mcp_calls,
        ctx,
        [("metamuse_export", request.model_dump(mode="json"))],
        tmp_path / "mcp-export.log",
    )
    assert results[0]["error"]["code"] == "E_RESOURCE_UNAVAILABLE"
    assert results[0]["error"]["message"] == EXPORT_ERROR


@pytest.fixture
def bare_cookie_bundle(ctx, project):
    exported = bundle(ctx, project, text=f"hatch_sess={SECRET}")
    return ctx.paths.root / exported.artifact


def test_b16a_bare_muse_cookie_must_not_leak_into_redacted_bundle(bare_cookie_bundle):
    assert all(SECRET.encode() not in path.read_bytes() for path in bare_cookie_bundle.iterdir())
