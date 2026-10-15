# ruff: noqa: E501
"""Reviewer mutation survivors: exact bounds, roots, deadline and all stop codes."""

import time
from types import SimpleNamespace

import pytest
from browser_deadline import browser_deadline
from test_facebook import _synthetic_post_html
from test_facebook_r8_capture import run
from test_facebook_r8_cutoff import session_for
from test_facebook_review import URL, browser_fixture
from test_facebook_round5 import POST_PATH
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import _facebook_capture as capture
from metamuse import facebook
from metamuse import models as m


@browser_deadline
def test_r9_two_closed_roots(tmp_path, monkeypatch):
    markup = _synthetic_post_html(post_text="BODY").replace(
        ">BODY</div>",
        '>Lead <span id="h1"></span><span id="h2"></span></div><script>document.getElementById("h1").attachShadow({mode:"closed"}).textContent="FIRST";document.getElementById("h2").attachShadow({mode:"closed"}).textContent="SECOND";</script>',
    )
    job, stored = run(tmp_path, monkeypatch, markup, "chromium")
    assert job["error"] is None, job
    assert "FIRST" in stored[0]["text"]
    assert "SECOND" in stored[0]["text"]


@pytest.mark.parametrize("driver", ["fixture", "chromium"])
@browser_deadline
def test_r9_exact_node_bounds(tmp_path, monkeypatch, driver, request):
    base = _synthetic_post_html()
    if driver == "fixture":
        size = len(capture.fixture_capture(base))
        exact = base.replace("</body>", "<br>" * (65536 - size) + "</body>")
        assert len(capture.fixture_capture(exact)) == 65536
        with pytest.raises(m.CoreError) as caught:
            capture.fixture_capture(exact.replace("</body>", "<br></body>"))
        assert caught.value.detail.code == m.ErrorCode.SELECTOR_DRIFT
        return
    with browser_fixture(
        tmp_path, monkeypatch, {POST_PATH: base}, unsandboxed=False, read_timeout=300
    ) as (_, _, _, _, _, guard, driver):
        session = driver.open_session(
            facebook.SanitizedStorageState(
                cookies=[], origins=[], secret_tokens=(), cookie_hosts=(), dropped_cookie_domains=()
            ),
            guard,
            timeout_ms=300000,
            max_bytes=100000000,
        )
        try:
            session.goto_post(URL)
            size = len(session._capture()[1])
            exact = base.replace("</body>", "<br>" * (65536 - size) + "</body>")
            session._page.set_content(exact)
            assert len(session._capture()[1]) == 65536
            session._page.set_content(exact.replace("</body>", "<br></body>"))
            with pytest.raises(m.CoreError) as caught:
                session._capture()
            assert caught.value.detail.code == m.ErrorCode.SELECTOR_DRIFT
        finally:
            session.close()


def test_r9_verifier_single_deadline(monkeypatch):
    session = object.__new__(facebook._PlaywrightSession)
    session._transport_error = None
    session._closing_targets = {"t0"}
    session._browser = SimpleNamespace(is_connected=lambda: True)
    session._page = SimpleNamespace(is_closed=lambda: False, wait_for_timeout=lambda _: None)
    session._egress = SimpleNamespace(record_proxy_protocol_refusal=lambda **_: None)
    session.workers_blocked = 0
    now, steps = [100.0], []
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    def send(method, params=None):
        if method == "Target.getTargets":
            steps.append(method)
            now[0] += 2
            if len(steps) <= 10:
                identity = f"t{len(steps)}"
                session._closing_targets.add(identity)
                return {"targetInfos": [{"targetId": identity}]}
            return {"targetInfos": []}
        return {"success": True}

    session._worker_cdp = SimpleNamespace(send=send)
    session._verify_target_shutdown()
    assert session._transport_error.detail.code == m.ErrorCode.PROTOCOL_MISMATCH
    assert len(steps) <= 4


@pytest.mark.parametrize("code", [c for c in m.ErrorCode if c != m.ErrorCode.LIMIT_REACHED])
@pytest.mark.parametrize("order", ["fatal-first", "cutoff-first"])
def test_r9_every_core_stop_beats_cutoff(code, order):
    guard = facebook.EgressGuard(
        allowed_hosts=("www.facebook.com",), resolver=lambda _: ["93.184.216.34"]
    )
    session = session_for(guard)
    if order == "cutoff-first":
        guard.mark_byte_limit_exceeded()
    error = m.CoreError(code, "Synthetic stop", next_action="Synthetic action")
    guard.navigation_error = error
    if order == "fatal-first":
        guard.mark_byte_limit_exceeded()
    with pytest.raises(m.CoreError) as caught:
        session._raise_if_byte_limit()
    assert caught.value.detail.code == code
    assert caught.value is error
