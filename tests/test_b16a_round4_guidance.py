"""Fixed actionable refusals on all three shared application frontends."""

from test_b16a_privacy import evidence
from test_muse_lifecycle import fixture_ctx as fixture_ctx  # noqa: F401
from test_muse_lifecycle import invoke, opened

from metamuse import app
from metamuse import models as m


def test_r39_stale_packet_send_next_action(fixture_ctx, project, monkeypatch):
    ctx = fixture_ctx
    opening, _ = opened(ctx, project)
    value = f"{ctx._frontend}_MARKER_LEGACY_PACKET"
    ref = evidence(ctx, project, "password=" + value)
    with monkeypatch.context() as old:
        old.setattr("metamuse.content.redact_text", lambda text: (text, []))
        result = app.prepare_packet(
            ctx, {"project_id": project, "selection": [ref], "request_key": "legacy-packet"}
        )
    request = m.MuseSendRequest(
        discussion_id=opening.data["discussion_id"],
        packet_id=result.data["id"],
        question="Summarize.",
        request_key="new-send",
    )
    result = invoke(ctx, "send", request)
    assert result.error.code == m.ErrorCode.REFERENCE_INVALID
    assert (
        result.error.next_action
        == "Prepare a new packet and send request with a new exact share grant"
    )
    assert result.approval is None
    assert value not in result.model_dump_json()
