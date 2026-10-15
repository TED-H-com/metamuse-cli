"""Focused privacy/native-Muse integration at the named preparation parent."""

import json
from urllib.parse import quote

import pytest
from test_muse_lifecycle import fixture_ctx as fixture_ctx  # noqa: F401
from test_muse_lifecycle import invoke

from metamuse import models as m
from metamuse import safety
from metamuse._muse_codec import redact
from metamuse._muse_redact import sanitize_document, sanitize_text
from metamuse.muse import _Secrets
from metamuse.safety import redact_text


def vectors():
    marker = f"{'INTEGRATION'}_SYNTHETIC_MARKER_357913"
    rows = [
        f'password="left\\"{marker}"',
        f"the users' curl -u 'alice:{marker}'",
        f'curl -H "X:A;B" -b {marker}',
        f"curl x{chr(92)};y -u alice:{marker}",
        f"Cookie: theme=dark; hatch_sess={marker}",
        f"Authorization: Bearer {marker}",
        f"https://alice:{marker}@example.invalid/path",
        f"password=Ab3%3B{marker}&user=bob",
    ]
    return marker, [*rows, json.dumps({"cmd": rows[1]}), quote(rows[2], safe="")]


@pytest.mark.parametrize("index", range(10))
def test_native_sanitizers_keep_privacy_ownership_and_codec_coverage(index):
    marker, rows = vectors()
    text = rows[index]
    public, findings = redact_text(text)
    assert findings and marker not in public
    assert redact_text(public)[0] == public
    codec = redact(text)
    secrets = _Secrets()
    outputs = [
        sanitize_text(text),
        secrets.text(text),
        secrets.body(text.encode()).decode(),
        json.dumps(sanitize_document({"detail": text})),
    ]
    for output in outputs:
        assert marker not in output
        for offset in range(len(marker) - 5):
            piece = marker[offset : offset + 6]
            if piece not in codec:
                assert piece not in output


@pytest.mark.parametrize("operation", ["open", "send"])
def test_required_password_prose_refused_before_permission_boundary(
    fixture_ctx, monkeypatch, operation
):
    ctx = fixture_ctx
    text = "Password: required"
    request = (
        m.MuseOpenRequest(project_id="missing", topic=text, request_key="required-topic")
        if operation == "open"
        else m.MuseSendRequest(
            discussion_id="missing",
            packet_id="missing",
            question=text,
            request_key="required-question",
        )
    )
    monkeypatch.setattr(
        ctx.store, "get", lambda *a, **kw: pytest.fail("Credential check must precede state reads")
    )
    monkeypatch.setattr(
        safety,
        "approval_preview",
        lambda *a, **kw: pytest.fail("Refused text must never reach a human approval"),
    )
    result = invoke(ctx, operation, request)
    assert result.status == "error" and result.error.code == m.ErrorCode.INPUT_INVALID
    assert result.approval is None
    assert "Remove credentials" in result.error.next_action
    assert text not in result.model_dump_json()
    assert ctx.muse_gateway.calls == []
    with ctx.store.connection() as conn:
        for table in ("jobs", "exchanges", "discussions", "events", "request_receipts", "permits"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
