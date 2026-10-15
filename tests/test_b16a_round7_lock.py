"""Instrument every redactor binding during recovery and remote errors."""

import contextlib
import json
import time
import traceback

import pytest
from test_muse_lifecycle import fixture_ctx as fixture_ctx  # noqa: F401
from test_muse_lifecycle import grant, invoke, opened, read_action, refresh, run, sent

from metamuse import _muse_lifecycle as lifecycle
from metamuse import _muse_redact, content, muse, safety
from metamuse import models as m
from metamuse.muse import AttemptEvidence, StreamAttempt


def instrument_locks(ctx, monkeypatch):
    depth = {"n": 0}
    calls = []
    transaction = ctx.store.transaction

    @contextlib.contextmanager
    def tracked(*args, **kwargs):
        with transaction(*args, **kwargs) as conn:
            depth["n"] += 1
            try:
                yield conn
            finally:
                depth["n"] -= 1

    monkeypatch.setattr(ctx.store, "transaction", tracked)

    def make(binding, real):
        def wrapped(text, *args, **kwargs):
            started = time.process_time()
            result = real(text, *args, **kwargs)
            frames = [f.name for f in traceback.extract_stack(limit=12)[:-1]]
            calls.append(
                {
                    "binding": binding,
                    "chars": len(text) if isinstance(text, (str, bytes)) else 0,
                    "in_write_txn": depth["n"] > 0,
                    "cpu": round(time.process_time() - started, 6),
                    "stack": frames[-6:],
                }
            )
            return result

        return wrapped

    for module, name in (
        (safety, "safety"),
        (lifecycle, "lifecycle"),
        (content, "content"),
        (_muse_redact, "muse_redact"),
        (muse, "muse"),
    ):
        for function in ("redact_text", "sanitize_document"):
            if hasattr(module, function):
                monkeypatch.setattr(
                    module, function, make(name + "." + function, getattr(module, function))
                )
    return calls


@pytest.mark.parametrize("fixture_ctx", ["app"], indirect=True)
@pytest.mark.parametrize("flow", ["normal", "send_timeout", "app_error_big", "open_timeout"])
def test_r7_no_redaction_inside_write_transaction(fixture_ctx, project, monkeypatch, flow):
    ctx = fixture_ctx
    calls = instrument_locks(ctx, monkeypatch)
    if flow == "open_timeout":
        opening, _ = opened(ctx, project, "open_timeout")
        discussion = ctx.store.get(m.Discussion, opening.data["discussion_id"])
        target, payload = read_action(discussion, sessions=True)
        permit = grant(ctx, project, safety.action_digest(target, payload), operation="muse.read")
        request = {
            "discussion_id": discussion.id,
            "project_id": project,
            "topic": discussion.topic,
            "access_grant_id": permit,
            "request_key": "reconcile-open",
        }
        assert invoke(ctx, "open", request).status == "accepted"
        run(ctx)
    else:
        opening, _ = opened(ctx, project)
        if flow == "app_error_big":
            detail = "?token=;" * (1048576 // 8 - 64)
            original = ctx.muse_gateway.chat_stream

            def app_error(prepared, *, authorization, **kwargs):
                result = original(prepared, authorization=authorization, **kwargs)
                return StreamAttempt(
                    result.outbound_digest,
                    result.request_marker,
                    result.stream_id,
                    True,
                    AttemptEvidence("app_error", detail),
                    "send_uncertain",
                    status=200,
                    error_code="E_EXTERNAL_STATE_UNKNOWN",
                )

            monkeypatch.setattr(ctx.muse_gateway, "chat_stream", app_error)
            response, _ = sent(ctx, project, opening.data["discussion_id"])
        else:
            response, _ = sent(ctx, project, opening.data["discussion_id"], flow)
        refresh(ctx, response.data["exchange_id"])
    in_lock = [c for c in calls if c["in_write_txn"]]
    summary = {
        "flow": flow,
        "calls": len(calls),
        "document_calls": sum(c["binding"].endswith("sanitize_document") for c in calls),
        "in_lock_calls": len(in_lock),
        "in_lock_max_chars": max((c["chars"] for c in in_lock), default=0),
        "in_lock_sites": sorted({" > ".join(c["stack"][-3:]) for c in in_lock}),
        "max_chars_any": max((c["chars"] for c in calls), default=0),
        "max_cpu_in_lock": max((c["cpu"] for c in in_lock), default=0),
    }
    print("LOCK_REDACTION " + json.dumps(summary, sort_keys=True), flush=True)
    assert calls, "The instrumentation must observe real redaction calls"
    assert any(c["binding"].endswith("sanitize_document") for c in calls), summary
    assert summary["in_lock_calls"] == 0, summary
