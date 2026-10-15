"""Round-6 shapes through shared app/CLI/MCP packet and send paths."""

import json

import pytest
from test_muse_lifecycle import fixture_ctx as fixture_ctx  # noqa: F401
from test_muse_lifecycle import grant, invoke, opened, run

from metamuse import _muse_lifecycle as lifecycle
from metamuse import app, content, safety
from metamuse import models as m


def surface_cases():
    marker = f"{'R6SURFACE'}_MARKER_SYNTHETIC_643829"
    for name, text in {
        "unclosed_json": json.dumps({"password": "${" + marker}),
        "header_partial_mask": 'Cookie: [REDACTED]"' + marker,
        "apostrophe_curl": "Here's what I ran: curl "
        + "-u 'alice:my pass "
        + marker
        + "' https://example.invalid",
        "relative_url": f"https://example.invalid,//alice:{marker}@example.invalid/p",
        "har_prefix": "HAR capture: "
        + json.dumps({"cookies": [{"name": "theme", "value": marker}]}),
        "har_repeated": json.dumps({"params": []})
        + '{"name":"sid","value":"'
        + marker
        + '","name":"theme"}',
        "cookie_nested": 'cookie = os.getenv("COOKIE", "set_cookie=' + marker + '")',
        "xml_nested": "<token>${T:-<password>" + marker + "</password>}</token>",
        "html_name": '<input name="password" value="' + marker + '">',
        "html_type": '<input value="' + marker + '" type="password">',
        "meta": '<meta content="' + marker + '" name="csrf_token">',
        "key_plus": "-----BE"
        + "GIN+OPENSSH+PRIVATE+KEY-----%0A"
        + marker
        + "%0A-----E"
        + "ND+OPENSSH+PRIVATE+KEY-----",
    }.items():
        yield name, text, marker


def test_r6_packet_bundle_gateway_and_agent_surfaces(fixture_ctx, project, monkeypatch, caplog):
    import test_b16a_privacy as privacy

    cases = {name: (text, value, "unused") for name, text, value in surface_cases()}
    monkeypatch.setattr(privacy, "SHAPES", tuple(cases))
    monkeypatch.setattr(privacy, "credential_shape", cases.__getitem__)
    privacy.test_packet_bundle_fixture_outbound_jobs_and_mcp(
        fixture_ctx, project, monkeypatch, caplog, exact_count=False, separate_records=True
    )


def test_r6_refused_text_has_no_preview_or_writes(fixture_ctx, project):
    from unittest.mock import patch

    import test_b16a_round5_runtime as previous

    with patch.object(previous, "surface_cases", surface_cases):
        previous.test_r5_question_topic_refusal_before_preview_or_writes(fixture_ctx, project)


def test_r6_stale_har_before_preview_prepare_and_wire(fixture_ctx, project, monkeypatch):
    from test_b16a_privacy import evidence

    opening, _ = opened(fixture_ctx, project)
    marker = f"{'STALEHAR'}_MARKER_PRIVATE_643829"
    ref = evidence(
        fixture_ctx, project, json.dumps({"cookies": [{"name": "theme", "value": marker}]})
    )
    with monkeypatch.context() as old:
        old.setattr(content, "redact_text", lambda text: (text, []))
        made = app.prepare_packet(
            fixture_ctx, {"project_id": project, "selection": [ref], "request_key": "legacy-har"}
        )
    packet = fixture_ctx.store.get(m.Packet, made.data["id"])
    assert marker in packet.rendered_text
    with pytest.raises(m.CoreError) as failure:
        content.preview_packet(fixture_ctx, packet.id)
    assert failure.value.detail.code == m.ErrorCode.REFERENCE_INVALID
    request = m.MuseSendRequest(
        discussion_id=opening.data["discussion_id"],
        packet_id=packet.id,
        question="Summarize the capture.",
        request_key="stale-har-send",
    )
    with pytest.raises(m.CoreError) as failure:
        lifecycle.prepare_send(fixture_ctx, request)
    assert failure.value.detail.code == m.ErrorCode.REFERENCE_INVALID
    with monkeypatch.context() as old:
        old.setattr(lifecycle, "redact_text", lambda text: (text, []))
        prepared = lifecycle.prepare_send(fixture_ctx, request)
        request.share_grant_id = grant(
            fixture_ctx,
            project,
            safety.action_digest(prepared.target, prepared.payload),
            kind="share",
            packet=packet,
        )
        accepted = invoke(fixture_ctx, "send", request)
    assert accepted.status == "accepted"
    run(fixture_ctx)
    job = fixture_ctx.store.get(m.Job, accepted.data["job_id"])
    assert job.state == "failed" and job.remote_state == "not_submitted"
    assert job.error["code"] == "E_REFERENCE_INVALID"
    assert marker not in json.dumps(fixture_ctx.muse_gateway.calls)
