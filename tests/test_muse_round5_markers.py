"""Association markers survive the real lifecycle's sanitizer."""

import pytest
from test_muse_lifecycle import fixture_ctx, opened, refresh, sent  # noqa: F401

from metamuse import models as m
from metamuse._muse_associate import _MARKER_KEYS
from metamuse._muse_redact import sanitize_document, sanitize_text
from metamuse.app import create_project


@pytest.mark.parametrize("fixture_ctx", ["app"], indirect=True)
@pytest.mark.parametrize("key", _MARKER_KEYS)
def test_r5_2_marker_echo_keeps_messages(fixture_ctx, key):
    ctx = fixture_ctx
    project = create_project(ctx, {"name": "Synthetic project", "request_key": "project-1"}).data[
        "id"
    ]
    opening, _ = opened(ctx, project)
    response, _ = sent(ctx, project, opening.data["discussion_id"])
    exchange = ctx.store.get(m.Exchange, response.data["exchange_id"])
    discussion = ctx.store.get(m.Discussion, exchange.discussion_id)
    user = next(
        e
        for e in ctx.muse_gateway.events[discussion.remote_session]
        if e["event_name"] == "message.user"
    )
    marker = user.pop("request_marker")
    user["payload"][key] = marker
    value = refresh(ctx, exchange.id)
    assert value.data["exchange"]["state"] == "sent"
    assert value.data["association"] == "progress"
    assert value.data["messages"]
    assert any(msg["role"] == "user" for msg in value.data["messages"])


@pytest.mark.parametrize("key", _MARKER_KEYS)
def test_r5_2_known_marker_value_is_still_secret(key):
    secret = "synthetic-marker-" + "secret42"
    assert secret not in str(sanitize_document({key: secret}, (secret,)))
    assert secret not in sanitize_text(f"{key}={secret}", (secret,))
    assert sanitize_document({key: "ordinary-marker"}) == {key: "ordinary-marker"}
    assert "ordinary-marker" in sanitize_text(f"{key}=ordinary-marker")
