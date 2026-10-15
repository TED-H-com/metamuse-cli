"""The trusted hover happens before identity hit verification."""

# ruff: noqa: E501

import pytest
from browser_deadline import browser_deadline
from test_facebook_r10_expansion import outcome, page
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import facebook


@pytest.mark.parametrize("event", ["mousemove", "pointermove", "mouseover", "mouseenter"])
@browser_deadline
def test_r10_hover_overlay_is_never_clicked(tmp_path, monkeypatch, event, request):
    markup = page().replace(
        "</body>",
        """<div id="overlay" style="display:none;position:fixed;inset:0;z-index:9999" onclick="window.overlayClicks++"></div>
      <script>window.overlayClicks=0;
      document.querySelector('button').addEventListener('EVENT',()=>{
        document.getElementById('overlay').style.display='block';
      });</script></body>""".replace("EVENT", event),
    )
    counters = []
    original = facebook._PlaywrightSession.close

    def close(self):
        counters.append(self._page.evaluate("window.overlayClicks"))
        return original(self)

    monkeypatch.setattr(facebook._PlaywrightSession, "close", close)
    job, stored, _ = outcome(tmp_path, monkeypatch, markup)
    assert job["stop_reason"] == "expansion_failed", job
    assert job["state"] == "partial"
    assert counters == [0]
    assert len(stored) == 2 and stored[-1]["text"] == "INITIAL"
