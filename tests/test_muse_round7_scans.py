"""A malformed first Retry-After value cannot escape the rate-limit mapping."""

import pytest
from test_muse_standard import Connecting
from test_muse_transport import PEER, FakeHttp, build, ok_frames

from metamuse._muse_http import HttpResponse
from metamuse.models import CoreError, ErrorCode
from metamuse.muse import _retry_after


@pytest.mark.parametrize("hint", ["²", "½", "١", "１２"])
def test_retry_after_non_ascii_digits_are_not_a_delay(hint):
    assert _retry_after({"Retry-After": hint}) == 0


def test_rate_limit_with_unparseable_digit_hint_keeps_429_mapping(ctx):
    http = FakeHttp({"/api/auth/check": HttpResponse(429, b"", {"Retry-After": "²"}, PEER)})
    connector = Connecting()
    transport, *_ = build(handler=ok_frames([]), store=ctx.store, http=http, connector=connector)
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.RATE_LIMITED
    assert caught.value.retry_after == 0 and not connector.urls


@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"other": "9", "Retry-After": "12"}, 12),
        ({"Retry-After": "invalid", "retry-after": "12"}, 0),
        ({"Retry-After": "1", "retry-after": "12"}, 1),
        ({"Retry-After": "000000"}, 0),
        ({"Retry-After": "1234567"}, 0),
    ],
)
def test_retry_after_first_header_is_a_bounded_scalar(headers, expected):
    assert _retry_after(headers) == expected
