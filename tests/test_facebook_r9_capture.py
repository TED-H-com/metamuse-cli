"""Round-9 structured observation regressions; synthetic loopback only."""

import pytest
from test_facebook import _synthetic_post_html
from test_facebook_round5 import parse

from metamuse import models as m


def test_r9_fixture_capture_preserves_core_error():
    markup = _synthetic_post_html().replace(
        "</body>", '<template shadowrootmode="closed"></template></body>'
    )
    with pytest.raises(m.CoreError) as caught:
        parse(markup)
    assert caught.value.detail.code == m.ErrorCode.SELECTOR_DRIFT
    assert caught.value.detail.message == "Facebook structured capture could not be completed"
