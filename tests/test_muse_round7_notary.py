"""Ordered notary cache hints, decoding controls and bounded mint integration."""

import base64
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from test_muse_standard import Connecting, response
from test_muse_transport import FakeHttp, build, ok_frames

from metamuse import muse
from metamuse.models import CoreError, ErrorCode


def notary(document):
    segment = base64.urlsafe_b64encode(json.dumps(document).encode()).decode().rstrip("=")
    return ".".join(("one", "two", segment, "four", "five", "six"))


@pytest.mark.parametrize("bad", ["abc", "", "nan", "inf", "-inf", "-5", "0"])
def test_unusable_timeout_does_not_hide_later_stale_hint(bad):
    assert muse._notary_stale(notary({"restrictions": ["timeout:" + bad, "timeout:1"]}), 100)


@pytest.mark.parametrize(
    "restrictions,stale",
    [
        ([1, True, None, {"timeout": 1}, ["timeout:1"], "timeout:1"], True),
        (["timeout:abc", "timeout:1000", "timeout:1"], False),
        (["timeout:abc", "timeout:nan", "timeout:0"], False),
        (["timeout:160"], True),
        (["timeout:160.001"], False),
        (["timeout:1", "timeout:1000"], True),
        (["timeout:0", "timeout:1000"], False),
        (["timeout:-5", "timeout:1000"], False),
        (["other:1", "timeout:1"], True),
        (["timeout:  +1.6e2  "], True),
        (["timeout:1_60"], True),
        (["timeout:0xA0", "timeout:1"], True),
        (["timeout:١٦٠"], True),
        (["TIMEOUT:1", " timeout:1"], False),
    ],
)
def test_first_usable_timeout_decides(restrictions, stale):
    assert muse._notary_stale(notary({"restrictions": restrictions}), 100) is stale


@pytest.mark.parametrize("token", [None, "one", "one.two", "one.two.x", "one.two.$"])
def test_undecodable_token_has_no_hint(token):
    assert muse._notary_stale(token, 100) is False


@pytest.mark.parametrize(
    "document",
    [
        None,
        [],
        True,
        42,
        "text",
        {},
        {"restrictions": "timeout:1"},
        {"restrictions": {}},
        {"restrictions": True},
        {"restrictions": 1},
        {"restrictions": None},
        {"restrictions": ""},
    ],
)
def test_nonobject_or_nonlist_has_no_hint(document):
    assert muse._notary_stale(notary(document), 100) is False


def test_bad_json_has_no_hint():
    segment = base64.urlsafe_b64encode(b'{"restrictions":').decode().rstrip("=")
    assert muse._notary_stale("one.two." + segment, 100) is False


def test_non_string_restrictions_are_not_coerced(monkeypatch):
    # Spy on this module's string conversion only, preserving isinstance semantics.
    # JSON-native nonstrings have no matching textual timeout prefix, but the
    # contract requires skipping them without conversion, not merely equal output.
    class StringKind(type):
        def __instancecheck__(cls, value):
            return isinstance(value, str)

    class Strings(metaclass=StringKind):
        def __new__(cls, value):
            assert isinstance(value, str), "non-string hint was coerced"
            return value

    token = notary({"restrictions": [1, True, None, {"timeout": 1}, ["timeout:1"], "timeout:1"]})
    monkeypatch.setattr(muse, "str", Strings, raising=False)
    assert muse._notary_stale(token, 100)


class MintSequence(FakeHttp):
    def __init__(self, tokens):
        super().__init__()
        self.tokens = iter(tokens)

    def request(self, method, url, **kwargs):
        result = super().request(method, url, **kwargs)
        return response(next(self.tokens)) if urlsplit(url).path == "/api/hatch/token" else result


def test_unusable_then_stale_mints_twice_without_wake_or_dial(ctx):
    stale = notary({"restrictions": ["timeout:abc", "timeout:1"]})
    http = MintSequence([{"token": "first_hatch", "notary_token": stale}] * 2)
    connector = Connecting()
    transport, *_ = build(
        handler=ok_frames([]),
        store=ctx.store,
        http=http,
        connector=connector,
        wall_clock=lambda: 100,
    )
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.RESOURCE_UNAVAILABLE
    assert [urlsplit(call["url"]).path for call in http.calls] == [
        "/api/auth/check",
        "/api/session",
        "/api/hatch/token",
        "/api/hatch/token",
    ]
    assert not connector.urls


def test_unusable_then_stale_remint_connects_with_fresh_credentials(ctx):
    stale = notary({"restrictions": ["timeout:abc", "timeout:1"]})
    fresh = notary({"restrictions": ["timeout:1000"]})
    http = MintSequence(
        [
            {"token": "first_hatch", "notary_token": stale},
            {"token": "reminted_hatch", "notary_token": fresh},
        ]
    )
    connector = Connecting()
    transport, *_ = build(
        handler=ok_frames([]),
        store=ctx.store,
        http=http,
        connector=connector,
        wall_clock=lambda: 100,
    )
    with transport:
        transport.sessions_list()
    assert [urlsplit(call["url"]).path for call in http.calls] == [
        "/api/auth/check",
        "/api/session",
        "/api/hatch/token",
        "/api/hatch/token",
        "/api/hatch/vm/wake",
    ]
    assert len(connector.urls) == 1
    query = parse_qs(urlsplit(connector.urls[0]).query)
    assert query["auth_token"] == ["reminted_hatch"] and query["notary_token"] == [fresh]
