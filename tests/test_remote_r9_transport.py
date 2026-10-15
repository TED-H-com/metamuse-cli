"""FIXTURE: checked extractor/redirect hops keep their bytes through production curl."""

import pytest
from test_remote_media import FixtureHandler, no_dns, route_checks  # noqa: F401
from test_remote_media import origin as remote_origin
from test_remote_media import resolver as remote_resolver

from metamuse import remote_media, safety

origin = remote_origin
resolver = remote_resolver


@pytest.mark.parametrize(
    "target",
    [
        "/clip/../kept?x=%41",
        "/clip/%2e%2e/kept?x=%7E",
        "/clip?x=%zz&n=%E4%B8%AD",
        "/clip/%2F?x=%26%E4%B8%AD",
    ],
)
def test_r9_checked_hop_bytes_are_not_requoted_or_squashed(origin, resolver, monkeypatch, target):
    from curl_cffi import CurlOpt
    from curl_cffi.requests import Session

    seen = []
    approved = []
    path_policy = []
    quote_policy = []
    real_init = Session.__init__
    real_request = Session.request

    def session(self, *args, **kwargs):
        # Keep the explicit no-path-rewriting policy even where REQUEST_TARGET
        # also protects the wire target from curl's internal URL parser.
        path_policy.append(kwargs["curl_options"].get(CurlOpt.PATH_AS_IS))
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(Session, "__init__", session)

    def request(self, *args, **kwargs):
        quote_policy.append(kwargs.get("quote"))
        return real_request(self, *args, **kwargs)

    monkeypatch.setattr(Session, "request", request)

    def response(self):
        seen.append(self.path)
        self._send(200, b"synthetic", "text/plain")

    monkeypatch.setattr(FixtureHandler, "do_GET", response)
    url = f"http://media.fixture.test:{origin.server_address[1]}{target}"

    def checker(value, *, purpose):
        approved.append(value)
        return safety.check_fixture_url(
            value, purpose=purpose, resolver=resolver, allowed_hosts=["media.fixture.test"]
        )

    net = remote_media._Net(
        checker=checker, budget=remote_media._Budget(0, 10000), media_max=10000, timeout=2
    )
    result = remote_media._exchange(net, "GET", url, {}, None, follow=True)
    assert result[0] == 200
    assert approved == [url]
    assert seen == [target]
    assert path_policy == [1]
    assert quote_policy == [False]


def test_r9_yt_dlp_normalized_signed_hop_keeps_checked_bytes(origin, resolver, monkeypatch):
    from yt_dlp.networking.common import Request

    seen = []

    def response(self):
        seen.append(self.path)
        self._send(200, b"synthetic", "text/plain")

    monkeypatch.setattr(FixtureHandler, "do_GET", response)
    prefix = f"http://media.fixture.test:{origin.server_address[1]}"
    request = Request(prefix + "/a/../clip/{payload}?sig=SYNTHETIC&value=%41%2f&n=中")
    expected = "/clip/%7Bpayload%7D?sig=SYNTHETIC&value=%41%2f&n=%E4%B8%AD"
    assert request.url == prefix + expected
    checked = safety.check_fixture_url(
        request.url, purpose="subresource", resolver=resolver, allowed_hosts=["media.fixture.test"]
    )
    status, _headers, _body, peer, _reason = remote_media._dial(
        "GET", checked.url, {}, None, checked, 2, 10000
    )
    assert status == 200 and peer == "127.0.0.1"
    assert checked.url == request.url and seen == [expected]


def test_r10_redirect_later_hop_keeps_lowercase_escape_target(origin, resolver, monkeypatch):
    wire, approved = [], []
    prefix = f"http://media.fixture.test:{origin.server_address[1]}"
    later = "/kept/%2f?x=%7e%2f"

    def response(self):
        wire.append(self.path)
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", prefix + later)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
        else:
            self._send(200, b"Synthetic later hop", "text/plain")

    monkeypatch.setattr(FixtureHandler, "do_GET", response)

    def checker(value, *, purpose):
        approved.append((value, purpose))
        return safety.check_fixture_url(
            value, purpose=purpose, resolver=resolver, allowed_hosts=["media.fixture.test"]
        )

    net = remote_media._Net(
        checker=checker, budget=remote_media._Budget(0, 10000), media_max=10000, timeout=2
    )
    result = remote_media._exchange(net, "GET", prefix + "/start", {}, None, follow=True)
    assert result[0] == 200
    assert approved == [
        (prefix + "/start", "request"),
        (prefix + later, "redirect"),
        (prefix + later, "redirect"),
    ]
    assert wire == ["/start", later]
