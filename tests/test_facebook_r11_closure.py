"""Response delivery may overlap owned shutdown; active peer checks remain binding."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from browser_deadline import browser_deadline
from facebook_parity import capture_session
from test_facebook_review import URL

from metamuse import facebook
from metamuse import models as m


def controlled_session():
    handlers = {}
    closed = []

    def schedule(coroutine):
        coroutine.close()
        return SimpleNamespace(cancel=lambda: None)

    async def close_async():
        closed.append(True)

    page = SimpleNamespace(
        main_frame=object(),
        is_closed=lambda: bool(closed),
        on=lambda event, handler: handlers.setdefault(event, []).append(handler),
        remove_listener=lambda event, handler: handlers[event].remove(handler),
        close=lambda: closed.append(True),
        _impl_obj=SimpleNamespace(_loop=SimpleNamespace(create_task=schedule), close=close_async),
    )
    cdp = Mock()
    cdp.send.return_value = {"targetInfo": {"targetId": "main"}}
    browser = Mock()
    browser.is_connected.return_value = True
    browser.new_browser_cdp_session.return_value = cdp
    context = Mock()
    context.new_cdp_session.return_value = cdp
    guard = facebook.EgressGuard.for_fixture_test(
        allowed_hosts=("www.facebook.com",), resolver=lambda _: ["93.184.216.34"]
    )
    session = facebook._PlaywrightSession(Mock(), browser, context, page, guard, timeout_ms=300000)
    response = SimpleNamespace(
        request=SimpleNamespace(
            is_navigation_request=lambda: True,
            frame=page.main_frame,
            url=URL,
            resource_type="document",
        ),
        url=URL,
        status=200,
        headers={},
        server_addr=lambda: {"ipAddress": "93.184.216.34", "port": 443},
    )
    return session, response, handlers


@pytest.mark.parametrize("closer", ["page", "session", "byte_watcher"])
def test_r11_response_peer_lookup_during_close(closer):
    from playwright._impl._errors import TargetClosedError

    session, response, handlers = controlled_session()
    callback = handlers["response"][0]

    def peer():
        if closer == "page":
            session._page.close()
        elif closer == "session":
            session.close()
        else:
            session._egress.mark_byte_limit_exceeded()
            asyncio.run(session._watch_byte_limit())
        raise TargetClosedError("Synthetic closed response")

    response.server_addr = peer
    unhandled = []
    try:
        callback(response)
    except Exception as error:
        unhandled.append(error)
    assert unhandled == [], unhandled
    assert session._peer_error is None and session._transport_error is None
    if closer != "page":
        assert handlers["response"] == []  # No further owned response work at shutdown.


def test_r11_response_active_forbidden_peer_stays_denied():
    session, response, handlers = controlled_session()
    response.server_addr = lambda: {"ipAddress": "8.8.8.8", "port": 443}
    handlers["response"][0](response)
    assert session._peer_error and session._peer_error.detail.code == m.ErrorCode.SCOPE_DENIED
    assert session._transport_error is None


def test_r11_response_unexpected_close_is_guard_failure():
    from playwright._impl._errors import TargetClosedError

    session, response, handlers = controlled_session()

    def peer():
        raise TargetClosedError("Active response lost its target")

    response.server_addr = peer
    unhandled = []
    try:
        handlers["response"][0](response)
    except Exception as error:
        unhandled.append(error)
    assert unhandled == [], unhandled
    assert session._transport_error.detail.code == m.ErrorCode.PROTOCOL_MISMATCH


def test_r11_response_unexpected_error_is_not_suppressed():
    session, response, handlers = controlled_session()

    def peer():
        raise RuntimeError("Synthetic unexpected browser defect")

    response.server_addr = peer
    with pytest.raises(RuntimeError, match="Synthetic unexpected browser defect"):
        handlers["response"][0](response)


@browser_deadline
def test_r11_chromium_response_delivery_overlaps_byte_close(tmp_path, monkeypatch, caplog):
    from playwright.sync_api import Response

    with capture_session(monkeypatch) as session:
        response = session._page.goto(session._page.url)
        errors, delivered = [], []
        loop = session._page._impl_obj._loop
        prior = loop.get_exception_handler()
        loop.set_exception_handler(lambda _, context: errors.append(context))
        original = Response.server_addr

        def peer(response):
            delivered.append(response.url)
            session._egress.mark_byte_limit_exceeded()
            # Close the real target while its response callback is in peer
            # lookup, rather than relying on event-loop scheduling to race it.
            session._page.close()
            return original(response)

        monkeypatch.setattr(Response, "server_addr", peer)
        try:
            # Deliver the real Chromium response through Playwright's emitter.
            # No pending navigation can switch the caller's greenlet while the
            # callback deliberately closes the page inside peer lookup.
            unhandled = []
            try:
                session._page._impl_obj.emit("response", response._impl_obj)
            except Exception as error:
                unhandled.append(error)
            assert unhandled == [], unhandled
            assert delivered
            assert session._page.is_closed()
            assert session._peer_error is None and session._transport_error is None
            assert errors == [], errors
            assert "Exception in callback" not in caplog.text, caplog.text
        finally:
            loop.set_exception_handler(prior)
