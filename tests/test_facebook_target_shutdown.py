"""Asynchronous target destruction without ever resuming worker source."""

from types import SimpleNamespace

import pytest
from test_facebook import _synthetic_post_html
from test_facebook_review import browser_fixture, finish
from test_facebook_round5 import POST_PATH, submit
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import facebook


def test_r7_worker_false_close_waits_for_disappearance():
    session = object.__new__(facebook._PlaywrightSession)
    session._transport_error = None
    session._closing_targets = set()
    session._browser = SimpleNamespace(is_connected=lambda: True)
    polls = []

    def send(method, params=None):
        if method == "Target.closeTarget":
            return {"success": False}
        polls.append(method)
        return {"targetInfos": [{"targetId": "closing"}] if len(polls) < 3 else []}

    session._worker_cdp = SimpleNamespace(send=send)
    session._page = SimpleNamespace(wait_for_timeout=lambda _: None, is_closed=lambda: False)
    session._close_target("closing")
    assert session._transport_error is None
    assert "closing" in session._closing_targets
    session._verify_target_shutdown()
    assert session._transport_error is None
    assert not session._closing_targets


@pytest.mark.parametrize("connected", [True, False])
def test_r7_target_that_stays_or_disconnects_fails_closed(monkeypatch, connected):
    session = object.__new__(facebook._PlaywrightSession)
    session._transport_error = None
    session._closing_targets = set()
    session._browser = SimpleNamespace(is_connected=lambda: connected)
    session._worker_cdp = SimpleNamespace(
        send=lambda method, params=None: (
            {"success": False}
            if method == "Target.closeTarget"
            else {"targetInfos": [{"targetId": "stuck"}]}
        )
    )
    clock = iter([0, 6, 12, 18])
    monkeypatch.setattr(facebook.time, "monotonic", lambda: next(clock))
    session._page = SimpleNamespace(wait_for_timeout=lambda _: None, is_closed=lambda: False)
    session._close_target("stuck")
    session._verify_target_shutdown()
    assert session._transport_error.detail.code.value == "E_PROTOCOL_MISMATCH"


def test_r7_worker_shutdown_twenty_consecutive_runs(tmp_path, monkeypatch):
    # Both reviewer probes combined: dedicated source tries a nested worker and
    # fetch/socket, concurrently with a SharedWorker. None may execute.
    script = """<script>
      const inner = "fetch('/escape-inner'); new WebSocketStream('wss://'+location.host+'/escape-ws')";
      const outer = "new Worker(URL.createObjectURL(new Blob(["+JSON.stringify(inner)+"],{type:'text/javascript'})));fetch('/escape-outer')";
      new Worker(URL.createObjectURL(new Blob([outer],{type:'text/javascript'})));
      new SharedWorker(URL.createObjectURL(new Blob(["onconnect=()=>fetch('/escape-shared')"],{type:'text/javascript'})));
      </script>"""
    markup = _synthetic_post_html().replace("</body>", script + '<img src="/hold"></body>')
    for index in range(20):
        with browser_fixture(
            tmp_path / str(index),
            monkeypatch,
            {POST_PATH: markup, "/hold": "ok"},
            delays={"/hold": 0.1},
            unsandboxed=False,
        ) as (ctx, project, permit, _, hits, guard, driver):
            original = driver.open_session
            sessions = []

            def opened(*args, **kwargs):
                session = original(*args, **kwargs)
                session._worker_cdp.send("Security.setIgnoreCertificateErrors", {"ignore": True})
                sessions.append(session)
                return session

            monkeypatch.setattr(driver, "open_session", opened)
            job = finish(ctx, submit(ctx, project, permit))
            assert job["error"] is None, (index, job["error"])
            assert job["state"] == "succeeded"
            assert sessions[-1].workers_blocked >= 2
            assert not any("escape" in path or upgrade for path, upgrade in hits), hits
