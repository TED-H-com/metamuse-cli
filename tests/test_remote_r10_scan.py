"""OFFLINE: scan deadlines stay cheap while durable guards are time-paced."""

from types import SimpleNamespace

import pytest

from metamuse import remote_media


def test_r10_scan_guard_is_paced_and_finishes_with_a_guard(monkeypatch):
    clock = [0.0]
    durable, cheap = [], []
    monkeypatch.setattr(remote_media, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    normalize = remote_media.unicodedata.normalize

    def scan(form, value):
        result = normalize(form, value)
        clock[0] += 0.1
        return result

    monkeypatch.setattr(remote_media.unicodedata, "normalize", scan)
    payload = b"a" * (6 * 32768)
    checked = SimpleNamespace(url="https://media.fixture.test/clip", hostname="media.fixture.test")
    monkeypatch.setattr(remote_media, "_dial", lambda *a: (200, {}, payload, "127.0.0.1", "OK"))
    net = remote_media._Net(
        checker=lambda *a, **k: checked,
        budget=remote_media._Budget(0, len(payload) * 2),
        media_max=len(payload),
        timeout=2,
        before_dial=lambda: durable.append(clock[0]) or 2,
    )
    net.scan_deadline = lambda: cheap.append(clock[0]) or 2
    remote_media._exchange(net, "GET", checked.url, {}, None, follow=True)
    assert cheap == pytest.approx([0, 0.1, 0.2, 0.3, 0.4, 0.5])
    # One pre-dial guard, then first block, periodic guard, and post-scan guard.
    assert durable == pytest.approx([0, 0, 0.3, 0.6])


def test_r10_login_scan_uses_the_shared_paced_guard(monkeypatch):
    clock = [0.0]
    durable, cheap = [], []
    monkeypatch.setattr(remote_media, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    net = remote_media._Net(
        checker=lambda *a: None,
        budget=remote_media._Budget(0, 10),
        media_max=10,
        timeout=2,
        before_dial=lambda: durable.append(clock[0]) or 2,
    )
    net.scan_deadline = lambda: cheap.append(clock[0]) or 2
    real_feed = remote_media.HTMLParser.feed

    def feed(self, data):
        result = real_feed(self, data)
        clock[0] += 0.1
        return result

    monkeypatch.setattr(remote_media.HTMLParser, "feed", feed)
    body = b"<html>" + b" " * (6 * 32768) + b'<form action="/login/"></form>'
    with remote_media._scan_checks(net) as check:
        assert remote_media._login_form(
            body, "https://media.fixture.test/clip", check_deadline=check
        )
    assert len(cheap) == 8
    assert durable == pytest.approx([0, 0.3, 0.6, 0.8])
