import json
import socket
from pathlib import Path

import pytest

from metamuse import models as m
from metamuse.safety import URL_MAX_BYTES, OutboundURLPolicy, check_fixture_url, check_url

PUBLIC = "93.184.216.34"


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Real DNS attempted")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "gethostbyname", forbidden)


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.1",
        "2130706433",
        "0x7f000001",
        "017700000001",
        "0x7f.01",
        "0177.0.0.1",
        "0",
        "0.0.0.0",
        "10.1",
        "172.16.0.1",
        "192.168.1.1",
        "100.64.0.1",
        "100.127.255.255",
        "169.254.169.254",
        "169.254.1.1",
        "224.0.0.1",
        "255.255.255.255",
        "240.0.0.1",
        "192.0.2.1",
        "198.18.0.1",
        "198.51.100.1",
        "203.0.113.1",
        "[::1]",
        "[::]",
        "[::ffff:127.0.0.1]",
        "[::ffff:169.254.169.254]",
        "[::127.0.0.1]",
        "[::169.254.169.254]",
        "[fd00:ec2::254]",
        "[fd20:ce::254]",
        "[fe80::1]",
        "[ff02::1]",
        "[2001:db8::1]",
        "[3fff::1]",
        "[64:ff9b::a9fe:a9fe]",
        "[2002:7f00:1::1]",
    ],
)
def test_all_nonpublic_and_obscured_addresses_denied(host):
    with pytest.raises(m.CoreError) as error:
        check_url(f"https://{host}/", purpose="seed")
    assert error.value.detail.code == m.ErrorCode.SCOPE_DENIED
    assert error.value.detail.next_action


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.test/",
        "javascript:approved=true",
        "https://user@example.test/",
        "https://user:FAKE_SECRET@example.test/",
        "https://example.test@127.0.0.1/",
        "https://example.test\\@127.1/",
        " https://example.test/",
        "https://example.test/\n",
        "https://example.test/%0d%0a",
        "https://example.test/\x00",
        "https://example.test/\x7f",
        "https://example.test:65536/",
        "https://example.test:0/",
        "https://example.test:/",
        "https://%65xample.test/",
        "https://example.test../",
        "https://[2606:4700:4700::1111]suffix/",
        "https://[fe80::1%25eth0]/",
        "https://xn--invalid-/",
        "https://4294967296/",
        "https://1.2.3.999/",
        "https://example.test/" + "a" * 8192,
    ],
)
def test_malformed_urls_denied_without_echo(url):
    with pytest.raises(m.CoreError) as error:
        check_url(url, purpose="seed", resolver=lambda host: [PUBLIC])
    assert "FAKE_SECRET" not in str(error.value)


@pytest.mark.parametrize("purpose", ["seed", "request", "redirect", "subresource", "media"])
def test_each_actual_request_checks_all_addresses_and_rebinding(purpose):
    current = [PUBLIC]
    calls = []

    def resolve(host):
        calls.append(host)
        return current

    policy = OutboundURLPolicy(resolve, ("example.test",))
    assert policy.before_request("https://example.test/", purpose=purpose).addresses == (PUBLIC,)
    current[:] = [PUBLIC, "169.254.169.254"]
    with pytest.raises(m.CoreError):
        policy.before_request("https://example.test/", purpose=purpose)
    assert calls == ["example.test", "example.test"]


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/facebook.com?allowed=facebook.com",
        "https://facebook.com.evil.test/",
        "https://evilfacebook.com/",
    ],
)
def test_allowlist_is_hostname_only(url):
    with pytest.raises(m.CoreError):
        check_url(
            url, purpose="seed", resolver=lambda host: [PUBLIC], allowed_hosts=["facebook.com"]
        )


def test_idn_case_and_trailing_dot_normalized():
    calls = []

    def resolve(host):
        calls.append(host)
        return [PUBLIC]

    checked = check_url(
        "HTTPS://BÜCHER.example.:443/原文?key=FAKE_SIGNED_SECRET",
        purpose="seed",
        resolver=resolve,
        allowed_hosts=["xn--bcher-kva.example"],
    )
    assert checked.hostname == "xn--bcher-kva.example"
    assert checked.url.startswith("https://xn--bcher-kva.example:443/")
    assert calls == ["xn--bcher-kva.example"]
    assert "FAKE_SIGNED_SECRET" not in repr(checked)


@pytest.mark.parametrize(
    "url",
    [
        "https://93.184.216.34/",
        "https://1572395042/",
        "https://0x5db8d822/",
        "https://[2606:4700:4700::1111]/",
        "https://[::ffff:93.184.216.34]/",
    ],
)
def test_public_literal_forms_do_not_resolve(url):
    def forbidden(host):
        pytest.fail("Literal address reached DNS resolver")

    assert check_url(url, purpose="seed", resolver=forbidden).addresses


@pytest.mark.parametrize("resolved", [[], [PUBLIC, "not an IP"], "93.184.216.34", [PUBLIC] * 65])
def test_missing_invalid_and_unbounded_dns_fail_closed(resolved):
    with pytest.raises(m.CoreError):
        check_url("https://example.test/", purpose="seed", resolver=lambda host: resolved)


def test_dns_is_never_implicit_and_errors_are_sanitized():
    with pytest.raises(m.CoreError):
        check_url("https://example.test/", purpose="seed")

    def broken(host):
        raise RuntimeError("FAKE_DNS_SECRET")

    with pytest.raises(m.CoreError) as error:
        check_url("https://example.test/?key=FAKE_QUERY_SECRET", purpose="seed", resolver=broken)
    assert "FAKE" not in str(error.value)

    def broken_iterator(host):
        yield PUBLIC
        raise RuntimeError("FAKE_DNS_SECRET")

    with pytest.raises(m.CoreError) as error:
        check_url("https://example.test/", purpose="seed", resolver=broken_iterator)
    assert "FAKE" not in str(error.value)


def test_peer_must_match_checked_addresses():
    policy = OutboundURLPolicy(lambda host: [PUBLIC])
    assert policy.before_request("https://example.test/", peer_address=PUBLIC)
    with pytest.raises(m.CoreError):
        policy.before_request("https://example.test/", peer_address="127.0.0.1")


def test_fixture_mode_is_explicit_and_only_adds_loopback():
    with pytest.raises(m.CoreError):
        check_url("http://localhost:8000/", purpose="request", resolver=lambda host: ["127.0.0.1"])
    assert check_fixture_url(
        "http://localhost:8000/", purpose="request", resolver=lambda host: ["127.0.0.1", "::1"]
    )
    with pytest.raises(m.CoreError):
        check_fixture_url("http://10.0.0.1/", purpose="request")


CORPUS = Path(__file__).parent / "fixtures/corpus/security/urls.jsonl"
CORPUS_CASES = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines()]
# Corpus remains owned by its producer. These two expectations contradict the B08 contract.
SPEC_CORPUS_EXPECTATIONS = {
    "overlong_url": "allow",  # Corpus's 4120 UTF-8 bytes is below the documented 8192-byte bound.
    "trailing_dot_host": "allow",  # Normalize a single trailing dot, then apply host/IP policy.
}


@pytest.mark.parametrize("case", CORPUS_CASES, ids=[case["reason"] for case in CORPUS_CASES])
def test_shared_url_corpus_with_explicit_policy_context(case):
    reason = case["reason"]
    allowed_hosts = (
        ("facebook.com",)
        if reason in {"lookalike_host", "domain_only_in_query", "idn_punycode_homograph"}
        else ()
    )

    def resolve(host):
        # Localhost rows explicitly exercise the loopback DNS case; all other reserved names
        # resolve to one fixed public address, with no real DNS or network activity.
        return ["127.0.0.1", "::1"] if reason in {"localhost", "localhost_with_port"} else [PUBLIC]

    try:
        result = check_url(
            case["url"], purpose="seed", resolver=resolve, allowed_hosts=allowed_hosts
        )
        verdict = "allow"
    except m.CoreError as error:
        assert error.detail.code == m.ErrorCode.SCOPE_DENIED
        verdict = "reject"
    expected_verdict = SPEC_CORPUS_EXPECTATIONS.get(reason, case["expected_verdict"])
    assert verdict == expected_verdict
    if reason in SPEC_CORPUS_EXPECTATIONS:
        assert case["expected_verdict"] == "reject"  # Detect when the producer corrects these rows.
    if reason == "overlong_url":
        assert len(case["url"].encode("utf-8")) == 4120 < URL_MAX_BYTES
    if reason == "trailing_dot_host":
        assert result.hostname == "example.com" and result.url == "http://example.com/path"


@pytest.mark.parametrize("port", [22, 25, 6379, 8080])
def test_production_ports_are_http_standard_and_fixture_exceptions_are_loopback_only(port):
    with pytest.raises(m.CoreError):
        check_url(
            f"https://example.test:{port}/", purpose="request", resolver=lambda host: [PUBLIC]
        )
    with pytest.raises(m.CoreError):
        check_fixture_url(
            f"https://example.test:{port}/", purpose="request", resolver=lambda host: [PUBLIC]
        )
    assert check_fixture_url(f"http://127.0.0.1:{port}/", purpose="request")


def test_url_byte_limit_accepts_exact_boundary_and_rejects_next_byte():
    prefix = "https://example.test/"
    exact = prefix + "a" * (URL_MAX_BYTES - len(prefix.encode("utf-8")))
    assert check_url(exact, purpose="seed", resolver=lambda host: [PUBLIC])
    with pytest.raises(m.CoreError):
        check_url(exact + "a", purpose="seed", resolver=lambda host: [PUBLIC])
