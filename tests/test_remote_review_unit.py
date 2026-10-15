"""OFFLINE/MEASURE checks for B12 preparation and boundary internals."""

import os
from pathlib import Path

import pytest
from test_remote_media import ACCOUNT, PROVIDER, REEL_URL, configure, put_secret, remote_request

from metamuse import app, remote_media
from metamuse import models as m


def test_review_preview_reads_metadata_without_secret_bytes(ctx, project, tmp_path, monkeypatch):
    marker = f"SYNTHETIC_{'METADATA_ONLY'}_COOKIE_VALUE"
    secret = f".facebook.com\tTRUE\t/\tTRUE\t0\tsid\t{marker}\n".encode()
    store, ref = put_secret(tmp_path, ctx, secret)
    configure(ctx, [("FacebookReel", ["www.facebook.com"], ref)])
    path = store.root / (ref + ".json")
    boundary = path.read_bytes().index(b',"secret_b64":')
    positions = []
    original = os.fdopen

    class PrefixOnly:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            assert size > 0 and self.stream.tell() + size <= boundary
            chunk = self.stream.read(size)
            positions.append(self.stream.tell())
            return chunk

    def opened(fd, *args, **kwargs):
        stream = original(fd, *args, **kwargs)
        return PrefixOnly(stream) if Path(os.readlink(f"/proc/self/fd/{fd}")) == path else stream

    def forbidden(*args, **kwargs):
        raise AssertionError("Preview resolved secret bytes or attempted egress")

    monkeypatch.setattr(os, "fdopen", opened)
    monkeypatch.setattr(remote_media.safety, "resolve_secret", forbidden)
    monkeypatch.setattr(remote_media, "_checker_factory", forbidden)
    envelope = app.submit_collect(ctx, remote_request(project, REEL_URL, ["media_metadata"]))
    assert envelope.error.code == m.ErrorCode.SCOPE_REQUIRED
    assert envelope.approval is not None
    assert positions and max(positions) == boundary
    assert marker not in envelope.model_dump_json()
    assert envelope.approval.provider == PROVIDER and envelope.approval.account == ACCOUNT


def test_review_preview_cpu_at_url_cap_and_early_oversize_refusal(ctx, project, monkeypatch):
    import time

    from metamuse import safety

    configure(ctx, [("FacebookReel", ["www.facebook.com"])])
    prefix = REEL_URL + "?pad="
    url = prefix + "a" * (safety.URL_MAX_BYTES - len(prefix.encode()))
    started = time.process_time()
    envelope = app.submit_collect(ctx, remote_request(project, url, ["media_metadata"]))
    elapsed = time.process_time() - started
    print(f"MEASURE preview at {safety.URL_MAX_BYTES} bytes: {elapsed:.6f} CPU seconds")
    assert elapsed < 1.0
    assert envelope.approval is not None

    def forbidden(*args, **kwargs):
        raise AssertionError("Oversize URL reached display/redaction")

    monkeypatch.setattr(safety, "redact_text", forbidden)
    envelope = app.submit_collect(ctx, remote_request(project, url + "a", ["media_metadata"]))
    assert envelope.error.code == m.ErrorCode.SCOPE_DENIED
    assert envelope.approval is None


@pytest.mark.parametrize(
    "path,code",
    [
        ("/checkpoint/", "E_CHECKPOINT_REQUIRED"),
        ("/checkpoint;x=1/", "E_CHECKPOINT_REQUIRED"),
        ("/checkpoint%3Bx=1/", "E_CHECKPOINT_REQUIRED"),
        ("/checkpoint%3bx=1/", "E_CHECKPOINT_REQUIRED"),
        ("/login", "E_AUTH_REQUIRED"),
        ("/login;x=1", "E_AUTH_REQUIRED"),
        ("/login.php;x=1", "E_AUTH_REQUIRED"),
        ("/challenge.html;x=1", "E_CHECKPOINT_REQUIRED"),
        ("/__rd_verify;x=1", "E_CHECKPOINT_REQUIRED"),
        ("/%255f%255frd_verify_1", None),
        ("/ｃheckpoint/", None),
        ("/LOGİN", None),
    ],
)
def test_review_every_path_parameter_probe(path, code):
    if code:
        with pytest.raises(m.CoreError) as held:
            remote_media._stop_for_target("https://www.facebook.com" + path)
        assert held.value.detail.code.value == code
    else:
        remote_media._stop_for_target("https://www.facebook.com" + path)


@pytest.mark.parametrize("field", ["channel_id", "uploader_id", "channel_url", "uploader_url"])
def test_review_outer_identity_fields_never_override_inner(field):
    inner = {"id": "77", field: "inner-real-scope", "title": "Inner"}
    outer = {field: "outer-chosen-scope", "title": "Outer"}
    merged = remote_media._merge_transparent(inner, outer)
    assert merged[field] == "inner-real-scope"
    assert merged["title"] == "Outer"


@pytest.mark.parametrize(
    "html,expected",
    [
        (b'<form action="login.php"></form>', True),
        (b'<form action="&#47;login&#47;"></form>', True),
        (b'<form action="https://www.facebook.com/login/device-based/"></form>', True),
        (b'<base href="https://www.facebook.com/login/"><form action="?next=1"></form>', True),
        (b'<base href="/login/"><form action="./"></form>', True),
        (b'<base href="/login/"><form method="post"></form>', False),
        (b'<base href="/login/"><form action=""></form>', False),
        (b'<form action="./"></form><base href="/login/">', True),
    ],
)
def test_review_login_forms_resolve_against_base(html, expected):
    assert (
        remote_media._login_form(html, "https://www.facebook.com/share/r/Synthetic1/") is expected
    )


@pytest.mark.parametrize(
    "prefix", ["/", "\\/", "\\u002f", "&#47;", "&#x2f;", "%2F", "", "relative/"]
)
@pytest.mark.parametrize("channel", ["body", "refresh", "location"])
def test_review_challenge_token_all_spellings(prefix, channel):
    value = prefix + "__RD_VERIFY_1"
    body = value.encode() if channel == "body" else b""
    headers = {} if channel == "body" else {channel: value}
    assert remote_media._challenge_marked(200, headers, body, "OK")


@pytest.mark.parametrize("domain", [".com", "com", "localhost"])
def test_review_cookie_domains_need_two_labels(domain):
    secret = f"{domain}\tTRUE\t/\tTRUE\t0\tsid\t{'synthetic'}\n".encode()
    with pytest.raises(m.CoreError) as held:
        remote_media._parse_cookie_secret(secret)
    assert held.value.detail.code == m.ErrorCode.INPUT_INVALID
    assert held.value.detail.message == "Cookie file line 1 is invalid"
    assert domain not in held.value.detail.message
