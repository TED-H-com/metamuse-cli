"""Synthetic regression for server-issued standard-VM notary endorsements."""

import base64
from urllib.parse import parse_qs, urlsplit

import pytest
from test_muse_standard import Connecting, assignment, response, rv
from test_muse_transport import FakeHttp, build, ok_frames

from metamuse.models import CoreError, ErrorCode, canonical_json


@pytest.fixture(autouse=True)
def disk_store(tmp_path, monkeypatch):
    monkeypatch.setenv("METAMUSE_TEST_STORE_ROOT", str(tmp_path / "stores"))


def encoded(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def endorsement(*, version="v1", uri="/v1/noise", signature=b"s" * 64):
    document = {
        "identity": "synthetic-viewer",
        "public_key": {"key": encoded(b"k" * 32), "prefix": "", "algorithm": "synthetic"},
        "restrictions": ["timeout:9999999999"],
    }
    return ".".join(
        [
            "endorsement",
            version,
            encoded(canonical_json(document)),
            encoded(signature),
            encoded(canonical_json({"uri": uri})),
            encoded(b"t" * 64),
        ]
    )


@pytest.mark.parametrize(
    "alias", ["notary_token", "notaryToken", "noiseNotaryToken", "endorsement"]
)
def test_server_endorsement_passes_unchanged_through_standard_handshake(alias):
    notary = endorsement()
    connector = Connecting()
    transport, _, _, _ = build(
        handler=ok_frames([]),
        connector=connector,
        http=FakeHttp({"/api/hatch/token": response({"token": "synthetic", alias: notary})}),
    )
    with transport:
        assert transport.sessions_list().status == 200
    assert parse_qs(urlsplit(connector.urls[0]).query)["notary_token"] == [notary]
    peer = connector.peers[0]
    assert peer.party.finished and peer.msg3_payload == b""


@pytest.mark.parametrize(
    "notary",
    [
        endorsement(version="v2"),
        endorsement(uri="/other"),
        endorsement(signature=b"s" * 63),
        endorsement().replace("endorsement.", "Endorsement.", 1),
        endorsement().replace("endorsement.", "delegation.", 1),
        "endorsement.v1.a.b.c.d",
    ],
)
def test_unsupported_or_malformed_endorsement_stops_before_wake(notary):
    connector = Connecting()
    transport, http, _, _ = build(
        handler=ok_frames([]),
        connector=connector,
        http=FakeHttp(
            {"/api/hatch/token": response({"token": "synthetic", "notary_token": notary})}
        ),
    )
    with pytest.raises(CoreError) as caught:
        transport.sessions_list()
    assert caught.value.detail.code == ErrorCode.PROTOCOL_MISMATCH
    assert caught.value.detail.retry_class == "never"
    assert not connector.urls
    assert not any("/wake" in c["url"] for c in http.calls)


def test_notary_endorsement_is_not_an_access_token():
    connector = Connecting()
    transport, _, _, _ = build(
        handler=ok_frames([]),
        connector=connector,
        http=FakeHttp({"/api/hatch/token": response({"token": endorsement()})}),
    )
    with pytest.raises(CoreError):
        transport.sessions_list()
    assert not connector.urls


def test_endorsement_does_not_allow_confidential_session_metadata():
    connector = Connecting()
    session = assignment(vm_type="hatch_cvm")
    transport, http, _, _ = build(
        handler=ok_frames([]),
        connector=connector,
        http=FakeHttp(
            {
                "/api/session": response(session),
                "/api/hatch/token": response({"token": "synthetic", "notary_token": endorsement()}),
            }
        ),
    )
    with pytest.raises(CoreError):
        transport.sessions_list()
    assert not connector.urls
    assert not any("/token" in c["url"] for c in http.calls)


def test_endorsement_does_not_allow_rv_handshake():
    connector = Connecting(transform=lambda *_: rv())
    transport, _, _, _ = build(
        handler=ok_frames([]),
        connector=connector,
        http=FakeHttp(
            {"/api/hatch/token": response({"token": "synthetic", "notary_token": endorsement()})}
        ),
    )
    with pytest.raises(CoreError):
        transport.sessions_list()
    peer = connector.peers[0]
    assert len(peer.sent) == 1 and not peer.frames
    with transport._trust.store.connection() as conn:
        assert (
            conn.execute("SELECT confidential_required FROM muse_trust_accounts").fetchone()[0] == 1
        )
