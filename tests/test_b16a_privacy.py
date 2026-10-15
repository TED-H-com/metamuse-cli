"""B16/FR-019/020/AT-026/028: synthetic privacy guards, never LIVE."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest
from test_muse_lifecycle import fixture_ctx as fixture_ctx
from test_muse_lifecycle import grant, invoke, opened, run

from metamuse import app, cli, mcp, safety
from metamuse import models as m
from metamuse._muse_lifecycle import prepare_send, read_action

SHAPES = (
    "hatch",
    "mm",
    "json",
    "json_incomplete",
    "json_escaped",
    "notary",
    "noise",
    "encoded_1",
    "encoded_2",
    "encoded_3",
    "encoded_mixed",
    "encoded_nested",
    "encoded_value",
    "bearer",
    "userinfo",
    "colon",
    "json_prose",
    "bearer_1",
    "bearer_2",
    "bearer_3",
    "userinfo_1",
    "userinfo_2",
    "userinfo_3",
    "colon_1",
    "colon_2",
    "colon_3",
    "json_prose_1",
    "json_prose_2",
    "json_prose_3",
)
BENIGN = (
    "the session ended",
    "token economy",
    "cookie recipe",
    "password reset instructions",
    "session_count=2",
    "https://example.invalid/recipes?q=cookie&n=2#ingredients",
    "https://example.invalid/?next=%253Fq%253Drecipe%2526n%253D2",
    '{"session_count":"2", "note":"token economy"}',
    "繁體中文😀\r\n年增率 5%/年",
)


def credential_shape(prefix):
    marker = f"{prefix}_MARKER_SYNTHETIC_ONLY"
    kind = "session_cookie"
    match prefix:
        case "hatch" | "mm":
            text = f"{prefix}_sess={marker}"
        case "json" | "json_incomplete" | "json_escaped":
            text = json.dumps({"cookie_header": marker})
            if prefix == "json_incomplete":
                text = "broken document: " + text[:-1]
            if prefix == "json_escaped":
                text = text.replace("cookie_header", r"cookie\u005fheader")
            kind = "sensitive_field"
        case "notary":
            text = f"notary_token={marker}"
        case "noise":
            text = f"noiseNotaryToken={marker}"
        case "encoded_1" | "encoded_2" | "encoded_3":
            layers = int(prefix[-1])
            text = f"?token={marker}&view=1#keep"
            for _ in range(layers):
                text = quote(text, safe="")
            text = "https://example.invalid/" + text
            kind = "url_token"
        case "encoded_mixed":
            text = f"https://example.invalid/?token%253D{marker}%2526view%253D1#keep"
            kind = "url_token"
        case "encoded_nested":
            text = (
                f"https://example.invalid/?next=%253Fnotary%255Ftoken%253D{marker}%2526view%253D1"
            )
            kind = "url_token"
        case "encoded_value":
            text = f"https://example.invalid/?token%253Dstart%2520{marker}%2526view=1"
            kind = "url_token"
        case _:
            base, _, layer = prefix.partition("_")
            match base:
                case "bearer":
                    text = f"Bearer {marker}"
                    kind = "bearer_token"
                case "userinfo":
                    text = f"https://user:{marker}@example.invalid/a"
                    kind = "url_token"
                case "colon":
                    text = f"notary_token: {marker}"
                case "json":
                    text = "note " + json.dumps({"password": marker}) + " end"
                    kind = "sensitive_field"
                    layer = prefix.removeprefix("json_prose").lstrip("_")
                case _:
                    raise AssertionError(prefix)
            for _ in range(int(layer) if layer.isdecimal() else 0):
                text = quote(text, safe="")
    return text, marker, kind


@pytest.mark.parametrize("shape", SHAPES)
def test_credential_shapes_and_encoded_spans(shape):
    text, marker, kind = credential_shape(shape)
    output, counts = safety.redact_text(text)
    assert marker not in output
    assert counts == [{"kind": kind, "count": 1}]
    assert safety.redact_text(output) == (output, [])
    if shape.startswith("encoded"):
        assert "view" in output  # Adjacent benign query parameters keep their original encoding.
    if shape == "encoded_mixed":
        assert output == "https://example.invalid/?token%253D[REDACTED]%2526view%253D1#keep"
    if shape == "json":
        assert json.loads(output) == {"cookie_header": "[REDACTED]"}


@pytest.mark.parametrize("text", BENIGN)
def test_benign_privacy_corpus_survives_unchanged(text):
    assert safety.redact_text(text) == (text, [])
    from metamuse._muse_lifecycle import _check_question

    _check_question(text)


@pytest.mark.parametrize(
    "name",
    [
        "other_sess",
        "other_sessid",
        "other_session",
        "other_token",
        "other_secret",
        "other_cookie",
        "other_password",
        "cookie_header",
        "noiseNotaryToken",
    ],
)
def test_name_based_detection_in_text_and_structured_bundles(name):
    marker = f"{name}_MARKER_SYNTHETIC_ONLY"
    assert safety.redaction_sensitive_key(name)
    text = json.dumps({name: marker})
    assert marker not in safety.redact_text(text)[0]
    from metamuse.content import _safe_value

    counts = []
    assert _safe_value({name: marker}, counts) == {name: "[REDACTED]"}
    assert counts == [{"kind": "sensitive_field", "count": 1}]


def evidence(ctx, project, text):
    source = ctx.store.insert(
        m.Source(
            id=m.new_id("src"),
            project_id=project,
            platform="generic",
            collection_method="file_import",
            fetched_at=ctx.clock(),
        )
    )
    item = ctx.store.insert(
        m.Item(id=m.new_id("itm"), project_id=project, source_id=source.id, kind="post")
    )
    ctx.store.insert(
        m.ItemRevision(
            item_id=item.id,
            revision=1,
            text=text,
            text_hash=m.payload_digest(text),
            fetched_at=ctx.clock(),
            complete=True,
        )
    )
    return {"kind": "item_revision", "id": item.id, "revision": 1}


def assert_absent(markers, value):
    raw = value if isinstance(value, str) else m.canonical_json(value).decode()
    assert all(marker not in raw for marker in markers)


def test_packet_bundle_fixture_outbound_jobs_and_mcp(fixture_ctx, project, monkeypatch, caplog, *, exact_count=True):
    ctx = fixture_ctx
    cases = [credential_shape(shape) for shape in SHAPES]
    original = "\n".join(case[0] for case in cases)
    markers = [case[1] for case in cases]
    refs = [evidence(ctx, project, original), evidence(ctx, project, "Benign packet evidence.")]
    packet_result = mcp.call_tool(
        ctx,
        "metamuse_packet_prepare",
        {"project_id": project, "selection": refs, "request_key": "privacy-packet"},
    ).structured_content
    assert packet_result["status"] == "ok"
    packet = ctx.store.get(m.Packet, packet_result["data"]["id"])
    assert_absent(markers, packet_result)
    assert packet.digest == hashlib.sha256(packet.rendered_text.encode()).hexdigest()
    assert all(isinstance(record["count"], int) and record["count"] > 0 for record in packet.redactions)
    if exact_count:
        assert sum(record["count"] for record in packet.redactions) == len(cases)

    for format_name in ("bundle", "jsonl", "markdown", "text", "csv"):
        exported = app.export_objects(
            ctx,
            {
                "project_id": project,
                "selection": refs,
                "format": format_name,
                "request_key": "privacy-" + format_name,
            },
        )
        assert exported.status == "ok", exported
        directory = ctx.paths.root / exported.data["artifact"]
        manifest = exported.data["manifest"]
        if format_name == "bundle":
            assert manifest["text_policy"] == "redacted"
            assert (directory / "records.jsonl").is_file()
            for path in directory.iterdir():
                assert_absent(markers, path.read_text())
        else:
            assert manifest["text_policy"] == "original_private"
            assert manifest["redactions"] is None
            members = "\n".join(
                (directory / member["name"]).read_text() for member in manifest["files"]
            )
            assert all(marker in members for marker in markers)

    opening, _ = opened(ctx, project)
    request = m.MuseSendRequest(
        discussion_id=opening.data["discussion_id"],
        packet_id=packet.id,
        question="List evidence and gaps.",
        share_grant_id="pending",
        request_key="privacy-send",
    )
    prepared = prepare_send(ctx, request)
    assert prepared.body["text"] == request.question + "\n\n" + packet.rendered_text
    request.share_grant_id = grant(
        ctx,
        project,
        safety.action_digest(prepared.target, prepared.payload),
        kind="share",
        packet=packet,
    )
    outbound = []
    send = ctx.muse_gateway.chat_stream

    def capture(prepared, **kwargs):
        outbound.append(json.loads(prepared.body))
        return send(prepared, **kwargs)

    monkeypatch.setattr(ctx.muse_gateway, "chat_stream", capture)
    accepted = invoke(ctx, "send", request)
    assert accepted.status == "accepted", accepted
    run(ctx)
    assert len(outbound) == 1
    assert_absent(markers, outbound)
    job_id = accepted.data["job_id"]
    assert cli.main(["--json", "jobs", "get", job_id]) == 0
    job_output = ctx._capsys.readouterr()
    assert_absent(markers, job_output.out + job_output.err)
    assert json.loads(job_output.out)["data"]["job"]["state"] == "succeeded"

    exchange_id = accepted.data["exchange_id"]
    exchange = ctx.store.get(m.Exchange, exchange_id)
    discussion = ctx.store.get(m.Discussion, exchange.discussion_id)
    target, payload = read_action(discussion)
    permit = grant(ctx, project, safety.action_digest(target, payload), operation="muse.read")
    queued = invoke(
        ctx, "read", {"exchange_id": exchange_id, "refresh": True, "access_grant_id": permit}
    )
    assert queued.status == "accepted", queued
    worker = subprocess.run(
        [
            str(Path(sys.executable).with_name("metamuse")),
            "--workspace",
            str(ctx.paths.root),
            "--json",
            "worker",
            "run",
            "--once",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert worker.returncode == 0, worker.stderr
    assert_absent(markers, worker.stderr)
    assert original not in worker.stderr and "Benign packet evidence." not in worker.stderr
    read = invoke(ctx, "read", {"exchange_id": exchange_id})
    assert read.status == "ok", read
    assert read.data["observations"]
    assert_absent(markers, read.model_dump(mode="json"))
    listing = mcp.call_tool(ctx, "metamuse_job_list", {"project_id": project}).structured_content
    assert listing["status"] == "ok"
    assert any(job["id"] == job_id for job in listing["data"]["jobs"])
    assert_absent(markers, listing)
    # Search exposes original evidence, not packets. Check the benign selected ref;
    # private original evidence is intentionally retained by the existing search contract.
    search = mcp.call_tool(
        ctx,
        "metamuse_search",
        {"project_id": project, "query": "Benign packet evidence."},
    ).structured_content
    assert search["status"] == "ok" and len(search["data"]["results"]) == 1
    assert search["data"]["results"][0]["ref"] == refs[1]
    assert_absent(markers, search)
    with ctx.store.connection() as conn:
        details = "\n".join(row[0] for row in conn.execute("SELECT detail FROM events"))
    assert_absent(markers, details + caplog.text)
    assert original not in details and "Benign packet evidence." not in details


@pytest.mark.parametrize("shape", SHAPES)
def test_question_refusal_precedes_state_reads_preview_and_persistence(
    fixture_ctx, shape, monkeypatch
):
    ctx = fixture_ctx
    question, marker, _ = credential_shape(shape)
    request = m.MuseSendRequest(
        discussion_id="missing",
        packet_id="missing",
        question=question,
        share_grant_id="missing",
        request_key="refused",
    )
    monkeypatch.setattr(ctx.store, "get", lambda *a, **kw: pytest.fail("Refusal must come first"))
    with pytest.raises(m.CoreError) as caught:
        prepare_send(ctx, request)
    assert caught.value.detail.code == m.ErrorCode.INPUT_INVALID
    refused = invoke(ctx, "send", request)
    assert refused.status == "error" and refused.error.code == m.ErrorCode.INPUT_INVALID
    assert "Remove credentials" in refused.error.next_action
    assert_absent([marker], refused.model_dump(mode="json"))
    assert ctx.muse_gateway.calls == []
    with ctx.store.connection() as conn:
        for table in ("exchanges", "jobs", "events", "request_receipts", "permits"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_preexisting_packet_is_refused_without_preview_or_rewriting(fixture_ctx, project):
    ctx = fixture_ctx
    opening, _ = opened(ctx, project)
    text, marker, _ = credential_shape("hatch")
    packet = ctx.store.insert(
        m.Packet(
            id=m.new_id("pkt"),
            project_id=project,
            selection=[evidence(ctx, project, text)],
            rendered_text=text,
            digest=hashlib.sha256(text.encode()).hexdigest(),
            created_at=ctx.clock(),
        )
    )
    request = m.MuseSendRequest(
        discussion_id=opening.data["discussion_id"],
        packet_id=packet.id,
        question="List gaps.",
        share_grant_id="pending",
        request_key="legacy-packet",
    )
    with pytest.raises(m.CoreError) as caught:
        prepare_send(ctx, request)
    assert caught.value.detail.code == m.ErrorCode.REFERENCE_INVALID
    assert caught.value.detail.message == "Packet is stale; prepare a new packet"
    refused = invoke(ctx, "send", request)
    assert refused.approval is None and refused.error.code == m.ErrorCode.REFERENCE_INVALID
    assert marker not in refused.model_dump_json()
    assert ctx.store.get(m.Packet, packet.id).rendered_text == text


@pytest.mark.parametrize("field", ["question", "packet_text"])
def test_preexisting_prepared_body_is_refused_before_wire_construction(fixture_ctx, project, field):
    from metamuse._muse_lifecycle import _wire

    ctx = fixture_ctx
    opening, _ = opened(ctx, project)
    text, marker, _ = credential_shape("hatch")
    packet = ctx.store.insert(
        m.Packet(
            id=m.new_id("pkt"),
            project_id=project,
            selection=[evidence(ctx, project, "Benign evidence")],
            rendered_text="Benign evidence",
            digest=hashlib.sha256(b"Benign evidence").hexdigest(),
            created_at=ctx.clock(),
        )
    )
    prepared = prepare_send(
        ctx,
        m.MuseSendRequest(
            discussion_id=opening.data["discussion_id"],
            packet_id=packet.id,
            question="List gaps.",
            share_grant_id="pending",
            request_key="stored-send",
        ),
    )
    payload = {**prepared.payload, field: text}
    body = {**prepared.body, "text": payload["question"] + "\n\n" + payload["packet_text"]}
    unsafe = prepared.model_copy(update={"body": body, "payload": {**payload, "body": body}})
    with pytest.raises(m.CoreError) as caught:
        _wire(unsafe)
    assert caught.value.detail.code == (
        m.ErrorCode.INPUT_INVALID if field == "question" else m.ErrorCode.REFERENCE_INVALID
    )
    assert marker not in str(caught.value)
    assert [route for route, _ in ctx.muse_gateway.calls] == ["session.start"]
