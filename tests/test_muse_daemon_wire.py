"""Protocol-shaped regression independent of permissive transport fakes."""

import json

import pytest

from metamuse import _muse_lifecycle as life
from metamuse.models import CoreError


def test_session_start_uses_the_documented_daemon_method_envelope():
    prepared = life._prepared(
        "session.start",
        {},
        {"title": "合成驗收", "node_id": "prj_synthetic"},
        "open-synthetic",
        "prj_synthetic",
    )
    assert prepared.body == {
        "method": "/api/session/start",
        "params": {"origin": "fresh", "lifecycle": "persistent", "title": "合成驗收"},
    }
    assert json.loads(life._wire(prepared).body) == prepared.body


def test_chat_stream_uses_one_exact_text_item_without_changing_unicode_or_newlines():
    text = "請核對 e\u0301\r\n\n\n合成資料"
    prepared = life._prepared(
        "chat.stream",
        {"session_id": "synthetic-session"},
        {"session_id": "synthetic-session", "text": text},
        "send-synthetic",
        "prj_synthetic",
    )
    prepared = prepared.model_copy(
        update={
            "payload": {
                **prepared.payload,
                "question": "請核對 e\u0301\r\n",
                "packet_text": "合成資料",
            }
        }
    )
    wire = json.loads(life._wire(prepared).body)
    assert wire == {
        "session_id": "synthetic-session",
        "items": [{"type": "text", "text": text}],
        "node_id": prepared.node_id,
        "capabilities": {},
    }
    assert len(prepared.node_id) == 16 and int(prepared.node_id, 16) >= 0


def test_wrapped_native_history_returns_events_without_dropping_the_page():
    events = [{"seq": 1, "event_name": "message.user", "payload": {"content": "合成資料"}}]
    assert (
        life._events(json.dumps({"ok": True, "result": {"chat_events": events}}).encode()) == events
    )


@pytest.mark.parametrize(
    "body",
    [
        b'{"ok":false,"result":{"chat_events":[]}}',
        b'{"result":{"chat_events":[]}}',
        b'{"ok":true,"result":null}',
        b'{"ok":true,"result":{"chat_events":[],"events":[{}]}}',
        b'{"ok":true,"result":{"chat_events":[],"chat_events":[{}]}}',
    ],
)
def test_error_malformed_duplicate_and_conflicting_wrappers_are_not_empty_success(body):
    with pytest.raises(CoreError):
        life._events(body)


def test_old_flat_session_body_is_refused_without_rewriting_frozen_bytes():
    prepared = life._prepared(
        "session.start", {}, {"title": "合成驗收"}, "open-synthetic", "prj_synthetic"
    )
    flat = {"title": "合成驗收", "node_id": "prj_synthetic"}
    old = prepared.model_copy(update={"body": flat, "payload": {**prepared.payload, "body": flat}})
    with pytest.raises(CoreError):
        life._wire(old)
    assert old.body == flat
