"""Three accepted fixture defaults and their explicit-inline mirrors."""

import pytest
from browser_deadline import browser_deadline
from facebook_parity import capture_session, normalise
from test_facebook import _allow_loopback_sockets

from metamuse import _facebook_capture as capture


@pytest.mark.parametrize("tag", ["html", "body", "legend"])
@browser_deadline
def test_r11_default_containment_display(tmp_path, monkeypatch, tag, request):
    _allow_loopback_sockets(monkeypatch)
    with capture_session(monkeypatch) as session:
        for inline in (False, True):
            style = (
                ' style="content-visibility:hidden' + (";display:inline" if inline else "") + '"'
            )
            if tag == "html":
                markup = "<html" + style + "><head></head><body><span>PROBE</span></body></html>"
            elif tag == "body":
                markup = "<html><head></head><body" + style + "><span>PROBE</span></body></html>"
            else:
                markup = (
                    "<html><head></head><body><fieldset><legend"
                    + style
                    + "><span>PROBE</span></legend></fieldset></body></html>"
                )
            session._page.set_content(markup)
            session._network_cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
            _, nodes, gaps = session._capture()
            assert not gaps
            fixture = capture.fixture_capture(markup)
            expected = next(n["rendered"] for n in nodes if n["text"] == "PROBE")
            # Root and first legend are blockified by Chromium even when
            # authored inline. Body's inline mirror remains non-containing.
            assert expected is (inline and tag == "body"), (tag, inline, expected)
            assert normalise(fixture) == normalise(nodes), (tag, inline, expected)
