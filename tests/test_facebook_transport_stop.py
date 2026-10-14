"""A native socket event stops an in-flight sandboxed page before the read deadline."""

import threading

from browser_deadline import browser_deadline
from test_facebook import _synthetic_post_html
from test_facebook_review import browser_fixture, finish
from test_facebook_round5 import POST_PATH, submit
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import facebook


@browser_deadline
def test_r6_native_socket_event_interrupts_navigation(tmp_path, monkeypatch):
    release = threading.Event()

    def hold(request):
        release.wait(timeout=600)
        try:
            request.send_response(200)
            request.send_header("Content-Length", "0")
            request.end_headers()
        except OSError:
            pass

    markup = _synthetic_post_html().replace(
        "</body>",
        '<img src="/hold"><script>'
        "new WebSocketStream('wss://' + location.host + '/socket');</script></body>",
    )
    monkeypatch.setattr(facebook, "WEBSOCKET_BLOCK_SCRIPT", "", raising=False)
    try:
        with browser_fixture(
            tmp_path,
            monkeypatch,
            {POST_PATH: markup, "/hold": hold},
            unsandboxed=False,
            read_timeout=300,
        ) as (ctx, project, permit, _, hits, guard, _):
            job = finish(ctx, submit(ctx, project, permit))
            assert job["error"] and job["error"]["code"] == "E_PROTOCOL_MISMATCH", job["error"]
            assert (
                job["error"]["message"] == "Facebook WebSocket handshake bypassed browser blocking"
            )
            assert job["state"] == "failed"
            assert not any(upgrade for _, upgrade in hits)
            assert any(
                row["reason"] == "websocket_blocked" for row in guard.summary()["aborted_reasons"]
            )
    finally:
        release.set()
