"""Not-submitted accounting preserves the classified pre-frame storage failure."""

import sqlite3
from contextlib import contextmanager

import pytest
from test_muse_round8c_registration import setup_connection
from test_muse_transport import Token, mutation

from metamuse.models import CoreError, ErrorCode
from metamuse.muse import MuseNotSubmitted


@pytest.mark.parametrize("retry", ["safe_read", "user_action", "never"])
def test_not_submitted_preserves_classified_error_metadata(retry):
    original = CoreError(
        ErrorCode.STORAGE, "Fixed storage advice", next_action="Fixed next action", retry_after=2.5
    )
    original.detail.retry_class = retry
    wrapped = MuseNotSubmitted(original)
    assert wrapped.detail == original.detail
    assert wrapped.retry_after == original.retry_after
    assert wrapped.bytes_handed_off is False
    assert str(wrapped) == str(original)
    assert original.detail.retry_class == retry


@pytest.mark.parametrize("path", ["freeze", "application"])
@pytest.mark.parametrize("code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED])
def test_session_start_preserves_real_storage_retry_before_any_frame(ctx, monkeypatch, path, code):
    transport, http, connector, _ = setup_connection(ctx)
    original_transaction = ctx.store.transaction
    failing = path == "freeze"

    @contextmanager
    def transaction():
        if failing:
            error = sqlite3.OperationalError("synthetic private SQL must not escape")
            error.sqlite_errorcode = code
            try:
                raise error
            except sqlite3.Error:
                raise CoreError(ErrorCode.STORAGE, "SQLite operation failed") from None
        with original_transaction() as conn:
            yield conn

    monkeypatch.setattr(ctx.store, "transaction", transaction)
    if path == "application":
        original_handoff = ctx.store.muse_application_handoff

        def fail_at_handoff(*args, **kwargs):
            nonlocal failing
            failing = True
            return original_handoff(*args, **kwargs)

        monkeypatch.setattr(ctx.store, "muse_application_handoff", fail_at_handoff)
    token = Token()
    with transport, pytest.raises(MuseNotSubmitted) as caught:
        transport.session_start(
            mutation("session.start", {"topic": "synthetic"}), authorization=token
        )
    assert caught.value.detail.code == ErrorCode.STORAGE
    assert caught.value.detail.retry_class == "safe_read"
    assert "writer" in caught.value.detail.next_action
    assert "synthetic private" not in str(caught.value.detail)
    assert caught.value.bytes_handed_off is False
    assert token.calls == 1
    assert all(not peer.frames for peer in connector.peers)
    assert len(connector.urls) == (path == "application")
    assert len(http.calls) == (4 if path == "application" else 2)


def test_default_not_submitted_keeps_network_retry_policy():
    error = MuseNotSubmitted()
    assert error.detail.code == ErrorCode.NETWORK_TIMEOUT
    assert error.detail.retry_class == "safe_read"
    assert error.retry_after == 0
    assert error.bytes_handed_off is False
