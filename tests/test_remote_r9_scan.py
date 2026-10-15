"""OFFLINE: all claimed marker encodings at every block-edge placement and deadlines."""

from types import SimpleNamespace

import pytest

from metamuse import models as m
from metamuse import remote_media


def pct(value):
    return "".join(f"%{byte:02X}" for byte in value.encode())


MARKER = "__rd_verify"
FULL = {char: chr(ord(char) + 0xFEE0) for char in set(MARKER)}
BOLD = {char: chr(0x1D41A + ord(char) - ord("a")) for char in "rdverify"}
CAPITAL = {char: chr(0x1D400 + ord(char) - ord("a")) for char in "rdverify"}
FORMS = {
    "plain": MARKER,
    "upper": MARKER.upper(),
    "percent": pct(MARKER),
    "js_u": MARKER.replace("_", r"\u005f"),
    "js_x": MARKER.replace("_", r"\x5f"),
    "js_upper": MARKER.replace("_", r"\U005F"),
    "percent_js": MARKER.replace("_", "%5Cu005f"),
    "fullwidth": "".join(FULL[c] for c in MARKER),
    "percent_fullwidth": pct("".join(FULL[c] for c in MARKER)),
    "percent_bold": pct("".join(FULL[c] if c == "_" else BOLD[c] for c in MARKER)),
    "percent_capital": pct("".join(FULL[c] if c == "_" else CAPITAL[c] for c in MARKER)),
}


@pytest.mark.parametrize("name", FORMS)
def test_r9_challenge_marker_every_block_edge_placement(name):
    raw = FORMS[name].encode()
    block = 32 * 1024
    for edge in (block, 2 * block):
        for offset in range(-len(raw), 2):
            position = edge + offset
            body = b"a" * position + raw + b"a" * (3 * block - position - len(raw))
            assert remote_media._challenge_marked(200, {}, body, "OK"), (name, edge, offset)


def test_r9_response_scan_checks_step_deadline_each_block(monkeypatch):
    clock = [0.0]
    step = SimpleNamespace(
        guard=lambda: None,
        executor=SimpleNamespace(limits=SimpleNamespace(wall_clock_seconds=1)),
        job=SimpleNamespace(limits={"wall_clock_seconds": 1}, started_at=None),
        started=0.0,
    )
    monkeypatch.setattr(remote_media, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    real_normalize = remote_media.unicodedata.normalize
    scanned = []

    def normalize(form, value):
        scanned.append(len(value))
        clock[0] += 0.6
        return real_normalize(form, value)

    monkeypatch.setattr(remote_media.unicodedata, "normalize", normalize)
    body = b"a" * (4 * 32768)
    monkeypatch.setattr(remote_media, "_dial", lambda *args: (200, {}, body, "127.0.0.1", "OK"))
    checked = SimpleNamespace(
        url="https://media.fixture.test/clip",
        hostname="media.fixture.test",
        addresses=("127.0.0.1",),
    )
    net = remote_media._Net(
        checker=lambda *a, **k: checked,
        budget=remote_media._Budget(0, len(body) * 2),
        media_max=len(body),
        timeout=2,
        before_dial=remote_media._dial_timeout(step, 2),
        scan_deadline=remote_media._dial_timeout(step, 2, guard=False),
    )
    with pytest.raises(m.CoreError) as held:
        remote_media._exchange(net, "GET", checked.url, {}, None, follow=True)
    assert held.value.detail.code == m.ErrorCode.LIMIT_REACHED
    assert held.value.detail.message == "Job wall clock limit reached"
    assert len(scanned) == 2
