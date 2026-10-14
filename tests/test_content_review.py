"""Adversarial B07/B11 regressions using synthetic, disk-only workspaces."""

import csv
import io
import json
import time

from jsonschema import Draft202012Validator

from metamuse import app, content
from metamuse import models as m
from metamuse.paths import WorkspacePaths
from metamuse.safety import redact_text
from metamuse.schemas import schema_for
from metamuse.store import Store


def item(ctx, project, iid="itm_review", text="繁中", revision=1):
    with ctx.store.transaction() as conn:
        if revision == 1:
            ctx.store.insert(
                m.Source(
                    id="src_" + iid,
                    project_id=project,
                    platform="generic",
                    collection_method="file_import",
                    fetched_at=ctx.clock(),
                ),
                conn=conn,
            )
            ctx.store.insert(
                m.Item(id=iid, project_id=project, source_id="src_" + iid, kind="post"), conn=conn
            )
        ctx.store.insert(
            m.ItemRevision(
                item_id=iid,
                revision=revision,
                text=text,
                text_hash=m.payload_digest(text),
                fetched_at=ctx.clock(),
            ),
            conn=conn,
        )
    return {"kind": "item_revision", "id": iid, "revision": revision}


def draft(ctx, project, key="draft", **values):
    return app.save_draft(
        ctx,
        {
            "project_id": project,
            "text": "host",
            "platform": "generic",
            "request_key": key,
            **values,
        },
    )


def packet(ctx, project, ref, key="packet"):
    return app.prepare_packet(ctx, {"project_id": project, "selection": [ref], "request_key": key})


def export(ctx, project, ref, key="export", fmt="jsonl"):
    return app.export_objects(
        ctx, {"project_id": project, "selection": [ref], "request_key": key, "format": fmt}
    )


def error(response, code):
    assert response.status == "error", response
    assert response.error.code == code


def asset_segment(ctx, project):
    ref = item(ctx, project)
    asset = ctx.store.put_object(io.BytesIO(b"synthetic shared media"), mime="video/mp4")
    ctx.store.insert(
        m.ItemAsset(item_id=ref["id"], revision=1, asset_id=asset.id, relation="media")
    )
    ctx.store.insert(
        m.TranscriptSegment(
            id="seg_private",
            asset_id=asset.id,
            revision=1,
            start_ms=0,
            end_ms=100,
            text="SYNTHETIC_PRIVATE_TRANSCRIPT",
            method="manual",
        )
    )
    return asset, {"kind": "transcript_segment", "id": "seg_private", "revision": 1}


def test_review_01_shared_asset_hides_transcript(ctx, project):
    asset, ref = asset_segment(ctx, project)
    original = draft(ctx, project, facts_refs=[ref]).data
    other = app.create_project(ctx, {"name": "Other synthetic", "request_key": "other"}).data["id"]
    other_ref = item(ctx, other, "itm_other")
    duplicate = ctx.store.put_object(io.BytesIO(b"synthetic shared media"), mime="video/mp4")
    assert duplicate.id == asset.id
    ctx.store.insert(
        m.ItemAsset(item_id=other_ref["id"], revision=1, asset_id=asset.id, relation="media")
    )
    for owner in (project, other):
        result = app.search_evidence(
            ctx, {"project_id": owner, "query": "SYNTHETIC_PRIVATE_TRANSCRIPT"}
        )
        assert result.data["results"] == []
        error(packet(ctx, owner, ref, "packet-" + owner), m.ErrorCode.REFERENCE_INVALID)
        error(draft(ctx, owner, "draft-" + owner, facts_refs=[ref]), m.ErrorCode.REFERENCE_INVALID)
        error(export(ctx, owner, ref, "export-" + owner), m.ErrorCode.REFERENCE_INVALID)
    preview = app.preview_draft(ctx, {"draft_id": original["draft_id"], "revision": 1})
    assert preview.data["reference_issues"][0]["code"] == "E_REFERENCE_INVALID"


def test_review_02_redaction_bypasses():
    samples = [
        "https://example.invalid/#access_token=FAKE_FRAGMENT",
        "https://example.invalid/?api_key=FAKE_API&refresh_token=FAKE_REFRESH&client_secret=FAKE_CLIENT",
        "https://example.invalid/?next=https%3A%2F%2Fexample.invalid%2F%3Faccess_token%3DFAKE_ENCODED",
        "session%5Fid=FAKE_SESSION",
        "Authorization: Basic FAKE_BASIC",
        "Authorization: Token FAKE_TOKEN",
        "[http] Cookie: xs=FAKE_COOKIE",
        '{"access_token":"FAKE_JSON", "Authorization":"Bearer FAKE_AUTH", '
        '"sessionid":"FAKE_SESSION"}',
        'foo="FAKE QUOTED COOKIE"; bar=2',
        "Cookie: a=1;\r\n b=FAKE_FOLDED",
    ]
    for sample in samples:
        output, records = redact_text(sample)
        assert "FAKE" not in output and "FAKE" not in json.dumps(records), output
    records = []
    value = content._safe_value(
        {"token": "FAKE_TOKEN", "api-key": "FAKE_KEY", "refresh_token": "FAKE_REFRESH"}, records
    )
    assert "FAKE" not in json.dumps(value)


def test_review_03_redaction_false_positives_and_json_syntax():
    for text in (
        "年增率 5%/年",
        "費用（元）/人",
        "「台北」/「台中」",
        "請選擇：是 /否",
        "人數=100; 預算=200",
    ):
        assert redact_text(text) == (text, [])
    for text in (
        '{"path":"/tmp/synthetic.txt"}',
        '{"auth":"Authorization: Bearer FAKE_SECRET"}',
        '{"cookie":"sessionid=FAKE_SECRET"}',
    ):
        output, _ = redact_text(text)
        assert "FAKE" not in output
        json.loads(output)


def test_review_04_csv_prefix_bypasses():
    for text in (" =1+1", "\n=1+1", '"=1+1', " +1+1", " @SUM(1)", "|synthetic"):
        raw = content._csv_bytes(
            [
                {
                    "ref": {"kind": "item_revision", "id": "itm_one", "revision": 1},
                    "text": text,
                    "source": {},
                }
            ]
        )
        assert list(csv.reader(io.StringIO(raw.decode(), newline="")))[1][3] == "'" + text


def test_review_05_diamond_resolution_time_and_query_bound(ctx, project, monkeypatch):
    base = draft(ctx, project, "base").data
    ref = {"kind": "draft_revision", "id": base["draft_id"], "revision": 1}
    for i in range(8):
        result = draft(ctx, project, f"diamond-{i}", facts_refs=[ref, ref, ref])
        assert result.status == "ok"
        ref = {"kind": "draft_revision", "id": result.data["draft_id"], "revision": 1}
    calls = 0
    original = ctx.store.get

    def count(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(ctx.store, "get", count)
    started = time.perf_counter()
    result = app.preview_draft(ctx, {"draft_id": ref["id"], "revision": 1})
    elapsed = time.perf_counter() - started
    assert result.status == "ok"
    assert calls < 250, calls
    assert elapsed < 2, elapsed


def test_cached_subtree_keeps_depth_budget(ctx, project, monkeypatch):
    previous = None
    for i in range(3):
        saved = draft(ctx, project, f"depth-{i}", facts_refs=[previous] if previous else [])
        assert saved.status == "ok"
        previous = {"kind": "draft_revision", "id": saved.data["draft_id"], "revision": 1}
    wrapper = draft(ctx, project, "depth-wrapper", facts_refs=[previous])
    wrapper_ref = {"kind": "draft_revision", "id": wrapper.data["draft_id"], "revision": 1}
    monkeypatch.setattr(content, "MAX_RESOLUTION_DEPTH", 3)
    error(
        draft(ctx, project, "depth-root", facts_refs=[previous, wrapper_ref]),
        m.ErrorCode.REFERENCE_INVALID,
    )


def test_review_06_stateless_cursor_and_restored_export(ctx, project, tmp_path):
    refs = [item(ctx, project, f"itm_{i}") for i in range(2)]
    first = app.search_evidence(ctx, {"project_id": project, "query": "中", "limit": 1})
    assert not (ctx.paths.runtime / "search").exists()
    export(ctx, project, refs[0])
    backup = ctx.store.backup(tmp_path / "backup")
    Store.restore(backup, WorkspacePaths.resolve(tmp_path / "restored"))
    restored = app.AppContext.open(tmp_path / "restored")
    second = app.search_evidence(
        restored, {"project_id": project, "query": "中", "cursor": first.next_cursor}
    )
    assert second.status == "ok" and second.data["results"][0]["ref"] == refs[1]
    replay = export(restored, project, refs[0])
    error(replay, m.ErrorCode.RESOURCE_UNAVAILABLE)
    assert "backups do not contain export directories" in replay.error.message
    assert "new request key" in replay.error.next_action
    assert export(restored, project, refs[0], key="new-after-restore").status == "ok"


def test_review_07_removed_current_attachment_hides_segment(ctx, project):
    _, ref = asset_segment(ctx, project)
    item(ctx, project, text="current without asset", revision=2)
    search = app.search_evidence(
        ctx, {"project_id": project, "query": "SYNTHETIC_PRIVATE_TRANSCRIPT"}
    )
    assert search.data["results"] == []
    error(packet(ctx, project, ref), m.ErrorCode.REFERENCE_INVALID)


def test_review_08_receipts_prioritize_conflict_and_revalidate(ctx, project):
    ref = item(ctx, project)
    original = packet(ctx, project, ref).data
    bad = {**ref, "id": "itm_missing"}
    error(packet(ctx, "prj_missing", bad), m.ErrorCode.IDEMPOTENCY_CONFLICT)
    export(ctx, project, ref)
    error(export(ctx, project, bad), m.ErrorCode.IDEMPOTENCY_CONFLICT)
    request = {"facts_refs": [ref]}
    draft(ctx, project, **request)
    item(ctx, project, text="new", revision=2)
    assert packet(ctx, project, ref).data["stale"]
    assert ctx.store.get(m.Packet, original["id"]).stale
    with ctx.store.transaction() as conn:
        conn.execute("DELETE FROM item_revisions WHERE item_id=?", (ref["id"],))
    error(draft(ctx, project, **request), m.ErrorCode.REFERENCE_INVALID)


def test_review_09_already_superseded_and_specific_revision_stale(ctx, project):
    old = item(ctx, project)
    new = item(ctx, project, text="new", revision=2)
    assert packet(ctx, project, old).data["stale"]
    first = draft(ctx, project, facts_refs=[old]).data
    second = draft(
        ctx, project, "updated", draft_id=first["draft_id"], expected_revision=1, facts_refs=[new]
    )
    assert second.status == "ok"
    assert app.preview_draft(ctx, {"draft_id": first["draft_id"], "revision": 1}).data["stale"]
    assert not app.preview_draft(ctx, {"draft_id": first["draft_id"], "revision": 2}).data["stale"]


def test_review_10_transitive_conflicts_ignore_date_number_tokens(ctx, project):
    ref = item(ctx, project, text="2026-10-01")
    direct = draft(ctx, project, "direct", text="2026-10-01 數量500", facts_refs=[ref]).data
    preview = app.preview_draft(ctx, {"draft_id": direct["draft_id"], "revision": 1})
    assert preview.data["conflict_candidates"] == []
    parent = draft(ctx, project, "parent", facts_refs=[ref]).data
    child = draft(
        ctx,
        project,
        "child",
        text="2026-12-31",
        facts_refs=[{"kind": "draft_revision", "id": parent["draft_id"], "revision": 1}],
    ).data
    preview = app.preview_draft(ctx, {"draft_id": child["draft_id"], "revision": 1})
    assert any(c["kind"] == "date" and c["ref"] == ref for c in preview.data["conflict_candidates"])


def test_review_11_long_query_retained_in_snippet(ctx, project):
    query = "語" * 200
    item(ctx, project, text="前" * 100 + query + "後" * 100)
    result = app.search_evidence(ctx, {"project_id": project, "query": query})
    assert query in result.data["results"][0]["snippet"]


def test_review_12_unredacted_export_label(ctx, project):
    ref = item(ctx, project)
    result = export(ctx, project, ref, fmt="markdown").data
    assert result["manifest"]["redactions"] is None
    raw = (ctx.paths.root / result["artifact"] / "object-0001.md").read_text()
    assert raw.startswith("Notice: Private unredacted export")


def test_review_13_bounded_typed_contracts():
    validator = Draft202012Validator(schema_for(m.SearchRequest))
    assert list(validator.iter_errors({"project_id": "prj_one", "query": ""}))
    schema = schema_for(m.DraftSaveRequest)
    preferences = schema["$defs"]["DraftPreferences"]["properties"]
    assert preferences["language"]["maxLength"] <= 64
    assert preferences["emoji"]["maxLength"] <= 64
    assert "$ref" in schema_for(m.DraftRevision)["properties"]["preferences"]


def test_f16_all_core_tools_effective_and_stubs_absent(ctx):
    # Moved core guard: feature implementations can now coexist with effective core tools.
    from metamuse.schemas import generated_schemas

    rows = app.inspect_capabilities(ctx).data["capabilities"]
    core = [row for row in rows if row["name"] in app.CORE_IMPLEMENTED_TOOLS]
    assert {row["name"] for row in core if row["effective"]} == app.CORE_IMPLEMENTED_TOOLS
    assert all(not row["effective"] for row in rows if row["implementation"] == "absent")
    assert len(generated_schemas()) == 40


def test_cursor_tamper_fields_and_no_per_query_state(ctx, project):
    import base64

    refs = [item(ctx, project, f"itm_{i}") for i in range(3)]
    first = app.search_evidence(ctx, {"project_id": project, "query": "中", "limit": 1})
    raw = base64.urlsafe_b64decode(first.next_cursor + "=" * (-len(first.next_cursor) % 4))
    original = json.loads(raw[:-32])
    for name, value in (
        ("project_id", "prj_foreign"),
        ("query_digest", "0" * 64),
        ("count", 99),
        ("item_watermark", 0),
        ("last", [0, "itm_missing", 1]),
    ):
        payload = {**original, name: value}
        tampered = (
            base64.urlsafe_b64encode(m.canonical_json(payload) + raw[-32:]).decode().rstrip("=")
        )
        error(
            app.search_evidence(ctx, {"project_id": project, "query": "中", "cursor": tampered}),
            m.ErrorCode.INPUT_INVALID,
        )
    second = app.search_evidence(
        ctx, {"project_id": project, "query": "中", "cursor": first.next_cursor, "limit": 1}
    )
    replay = app.search_evidence(
        ctx, {"project_id": project, "query": "中", "cursor": first.next_cursor, "limit": 1}
    )
    assert second.data == replay.data and second.next_cursor == replay.next_cursor
    assert second.data["results"][0]["ref"] == refs[1]
    assert not (ctx.paths.runtime / "search").exists()
    with ctx.store.connection() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM request_receipts WHERE operation='content.cursor_key'"
            ).fetchone()[0]
            == 1
        )


def test_watermark_detects_deleted_replaced_membership_even_with_equal_count(ctx, project):
    item(ctx, project, "itm_0", revision=1)
    item(ctx, project, "itm_0", revision=2)
    item(ctx, project, "itm_1")
    first = app.search_evidence(ctx, {"project_id": project, "query": "中", "limit": 1})
    with ctx.store.transaction() as conn:
        conn.execute("DELETE FROM item_revisions WHERE item_id=? AND revision=2", ("itm_0",))
    error(
        app.search_evidence(
            ctx, {"project_id": project, "query": "中", "cursor": first.next_cursor}
        ),
        m.ErrorCode.REFERENCE_INVALID,
    )


def test_diamond_export_is_flat_and_total_edge_node_budgets(ctx, project, monkeypatch):
    base = draft(ctx, project, "base").data
    ref = {"kind": "draft_revision", "id": base["draft_id"], "revision": 1}
    for i in range(8):
        left = draft(ctx, project, f"left-{i}", facts_refs=[ref]).data
        right = draft(ctx, project, f"right-{i}", facts_refs=[ref]).data
        refs = [
            {"kind": "draft_revision", "id": x["draft_id"], "revision": 1} for x in (left, right)
        ]
        merged = draft(ctx, project, f"merge-{i}", facts_refs=refs).data
        ref = {"kind": "draft_revision", "id": merged["draft_id"], "revision": 1}
    queries = 0
    original = ctx.store._open

    def traced():
        conn = original()

        def trace(_):
            nonlocal queries
            queries += 1

        conn.set_trace_callback(trace)
        return conn

    monkeypatch.setattr(ctx.store, "_open", traced)
    started = time.perf_counter()
    response = export(ctx, project, ref)
    elapsed = time.perf_counter() - started
    assert response.status == "ok" and queries < 600 and elapsed < 2
    lines = (ctx.paths.root / response.data["artifact"] / "records.jsonl").read_bytes().splitlines()
    records = [json.loads(line) for line in lines]
    assert len(records) == 25 and all("facts" not in record for record in records)
    monkeypatch.setattr(content, "MAX_RESOLUTION_NODES", 5)
    error(app.preview_draft(ctx, {"draft_id": ref["id"], "revision": 1}), m.ErrorCode.LIMIT_REACHED)
    monkeypatch.setattr(content, "MAX_RESOLUTION_NODES", 2000)
    monkeypatch.setattr(content, "MAX_RESOLUTION_EDGES", 5)
    error(app.preview_draft(ctx, {"draft_id": ref["id"], "revision": 1}), m.ErrorCode.LIMIT_REACHED)


def test_save_receipt_revalidates_asset_and_transitive_refs(ctx, project):
    ref = item(ctx, project)
    asset = ctx.store.put_object(io.BytesIO(b"synthetic attachment"), mime="text/plain")
    ctx.store.insert(
        m.ItemAsset(item_id=ref["id"], revision=1, asset_id=asset.id, relation="media")
    )
    original = draft(ctx, project, assets=[asset.id], facts_refs=[ref])
    assert original.status == "ok"
    ctx.paths.object_path(asset.sha256).unlink()
    error(
        draft(ctx, project, assets=[asset.id], facts_refs=[ref]), m.ErrorCode.RESOURCE_UNAVAILABLE
    )
    error(
        draft(ctx, "prj_missing", assets=["ast_missing"], facts_refs=[ref]),
        m.ErrorCode.IDEMPOTENCY_CONFLICT,
    )


def test_transcript_new_revision_stale_in_packet_draft_and_preview(ctx, project):
    asset, ref = asset_segment(ctx, project)
    packet(ctx, project, ref)
    original = draft(ctx, project, facts_refs=[ref]).data
    ctx.store.insert(
        m.TranscriptSegment(
            id=ref["id"],
            asset_id=asset.id,
            revision=2,
            start_ms=0,
            end_ms=100,
            text="new transcript",
            method="manual",
        )
    )
    assert packet(ctx, project, ref).data["stale"]
    assert draft(ctx, project, facts_refs=[ref]).data["stale"]
    assert app.preview_draft(ctx, {"draft_id": original["draft_id"], "revision": 1}).data["stale"]
    assert packet(ctx, project, ref, key="new-old-reference").data["stale"]


def test_preview_nested_asset_failure_keeps_other_facts_and_conflicts(ctx, project):
    first = item(ctx, project, "itm_first", "2026-10-01 數量100")
    second = item(ctx, project, "itm_second", "2026-10-02 數量200")
    asset = ctx.store.put_object(io.BytesIO(b"synthetic attachment"), mime="text/plain")
    ctx.store.insert(
        m.ItemAsset(item_id=first["id"], revision=1, asset_id=asset.id, relation="media")
    )
    parent = draft(ctx, project, "parent", facts_refs=[first, second], assets=[asset.id]).data
    child = draft(
        ctx,
        project,
        "child",
        text="2026-12-31 數量500",
        facts_refs=[{"kind": "draft_revision", "id": parent["draft_id"], "revision": 1}],
    ).data
    ctx.paths.object_path(asset.sha256).unlink()
    result = app.preview_draft(ctx, {"draft_id": child["draft_id"], "revision": 1})
    assert result.status == "ok"
    assert result.data["reference_issues"] == [
        {
            "kind": "asset",
            "asset_id": asset.id,
            "code": "E_RESOURCE_UNAVAILABLE",
            "ref": {"kind": "draft_revision", "id": parent["draft_id"], "revision": 1},
        }
    ]
    assert {c["ref"]["id"] for c in result.data["conflict_candidates"]} >= {
        first["id"],
        second["id"],
    }


def test_core_cas_helper_and_sql_clock_are_used(ctx, project, monkeypatch):
    calls = []
    original = ctx.store.append_draft_revision

    def traced(record, *, expected_revision, conn=None):
        calls.append(expected_revision)
        return original(record, expected_revision=expected_revision, conn=conn)

    monkeypatch.setattr(ctx.store, "append_draft_revision", traced)
    ctx.store.clock = lambda: "2026-10-13T01:02:03+00:00"
    ctx.clock = lambda: "2026-10-13T09:09:09+00:00"
    first = draft(ctx, project).data
    assert (
        draft(ctx, project, "update", draft_id=first["draft_id"], expected_revision=1).status
        == "ok"
    )
    assert calls == [None, 1]
    ref = item(ctx, project)
    assert packet(ctx, project, ref).data["created_at"] == ctx.store.clock()
    with ctx.store.connection() as conn:
        assert {
            row[0]
            for row in conn.execute(
                "SELECT created_at FROM events WHERE kind IN ('draft.saved','packet.prepared')"
            )
        } == {ctx.store.clock()}


def test_csv_expanded_rule_and_exact_jsonl_across_whitespace_quotes(ctx, project):
    samples = [
        " =1+1",
        "\n=1+1",
        '"=1+1',
        " +1+1",
        " @SUM(1)",
        "|synthetic",
        "\u00a0\" '\t=1",
        "\tplain",
        "\rplain",
        "\nplain",
        ' "  |synthetic',
    ]
    for index, text in enumerate(samples):
        ref = item(ctx, project, f"itm_csv_{index}", text)
        response = export(ctx, project, ref, key=f"csv-{index}", fmt="csv").data
        rows = list(
            csv.reader(
                io.StringIO(
                    (ctx.paths.root / response["artifact"] / "records.csv").read_bytes().decode(),
                    newline="",
                )
            )
        )
        assert rows[1][3] == "'" + text
        response = export(ctx, project, ref, key=f"jsonl-{index}").data
        assert (
            json.loads((ctx.paths.root / response["artifact"] / "records.jsonl").read_bytes())[
                "text"
            ]
            == text
        )
    records = [
        {
            "ref": {"kind": "item_revision", "id": "itm_normal", "revision": 1},
            "source": {},
            "text": "  普通文字😀",
        }
    ]
    assert (
        list(csv.reader(io.StringIO(content._csv_bytes(records).decode(), newline="")))[1][3]
        == records[0]["text"]
    )


def test_structured_sensitive_key_variants_are_redacted():
    names = [
        "token",
        "secret",
        "password",
        "client_secret",
        "client-secret",
        "refresh_token",
        "id_token",
        "api-key",
        "access-token",
        "auth-token",
        "set_cookie",
        "private_key",
    ]
    records = []
    output = content._safe_value({name: f"FAKE_{i}" for i, name in enumerate(names)}, records)
    assert set(output) == set(names) and set(output.values()) == {"[REDACTED]"}
    assert "FAKE_" not in json.dumps(records)


def test_preferences_bounds_typed_storage_and_neutral_fields(ctx, project):
    for prefs in (
        {"language": ""},
        {"language": "a" * 36},
        {"emoji": ""},
        {"emoji": "a" * 33},
        {"formula": "fake"},
    ):
        error(draft(ctx, project, preferences=prefs), m.ErrorCode.INPUT_INVALID)
    response = draft(
        ctx, project, "bounded", preferences={"language": "zh-Hant", "emoji": "one per paragraph"}
    ).data
    stored = ctx.store.get(m.DraftRevision, response["draft_id"], revision=1)
    assert isinstance(stored.preferences, m.DraftPreferences)
    assert stored.preferences.scope == "this_draft"
