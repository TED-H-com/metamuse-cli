"""R2-5 closure through the documented human path (README save_request + permit approve).

FIXTURE/INTEGRATION, offline. The open is left open_uncertain under the old rules; the
host sends the reconciliation request with the original topic; the human saves
approval.request to approval.request_file and runs the real CLI permit approve.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_b16a_round3_runtime import legacy_open
from test_muse_lifecycle import fixture_ctx as fixture_ctx  # noqa: F401
from test_muse_lifecycle import grant, invoke, run

from metamuse import _muse_lifecycle as lifecycle
from metamuse import models as m
from metamuse import safety


def human_env(tmp_path):
    env = os.environ.copy()
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[name] = str(tmp_path / ("human-" + name.lower()))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["UV_OFFLINE"] = "1"
    return env


def current_open(ctx, project, topic):
    """Open under the CURRENT rules (no monkeypatch); a post-handoff timeout leaves it uncertain."""
    from metamuse._muse_lifecycle import prepare_open

    request = m.MuseOpenRequest(project_id=project, topic=topic, request_key="current-open")
    prepared = prepare_open(ctx, request)
    request.access_grant_id = grant(
        ctx, project, safety.action_digest(prepared.target, prepared.payload)
    )
    accepted = invoke(ctx, "open", request)
    assert accepted.status == "accepted", accepted
    ctx.muse_gateway.mode = "open_timeout"
    run(ctx)
    ctx.muse_gateway.mode = "normal"
    return ctx.store.get(m.Discussion, accepted.data["discussion_id"])


@pytest.mark.parametrize("topic_kind", ["credential", "prose", "control", "local_path_current"])
def test_reconcile_via_saved_request(fixture_ctx, project, monkeypatch, tmp_path, topic_kind):
    ctx = fixture_ctx
    marker = f"{ctx._frontend}_MARKER_RECONCILE_ONLY"
    topic = {
        "credential": f"Topic password={marker}",
        "prose": "Session: Opening keynote",
        "control": "Synthetic review topic",
        "local_path_current": "Review /home/alice/notes.txt",
    }[topic_kind]
    if topic_kind == "local_path_current":
        discussion = current_open(ctx, project, topic)
    else:
        discussion = legacy_open(ctx, project, monkeypatch, topic, uncertain=True)
    assert ctx.store.get(m.JobStep, discussion.open_step_id).state == "open_uncertain"
    request = m.MuseOpenRequest(
        project_id=project, discussion_id=discussion.id, topic=topic, request_key="reconcile"
    )
    review = invoke(ctx, "open", request)
    row = {
        "frontend": ctx._frontend,
        "topic_kind": topic_kind,
        "review_code": review.error.code.value if review.error else review.status,
        "has_approval": review.approval is not None,
        "marker_in_review": marker in review.model_dump_json(),
    }
    assert review.approval is not None, review
    approval = review.approval
    row["saved_topic_equals_stored"] = approval.request.get("topic") == topic
    # README save_request: json.dump(a["request"], open(a["request_file"], "w"))
    Path(approval.request_file).write_text(json.dumps(approval.request, ensure_ascii=False))
    # In-process equivalent of cli permit approve.
    try:
        preview = safety.approval_preview(
            ctx, "muse.open", json.loads(Path(approval.request_file).read_text())
        )
        row["inprocess_preview"] = "ok"
        row["inprocess_digest_matches"] = preview.action_digest == approval.action_digest
    except m.CoreError as error:
        row["inprocess_preview"] = error.detail.code.value + ": " + error.detail.message
    # Real CLI subprocess, exactly as README's operator() runs it.
    command = [
        str(Path(sys.executable).with_name("metamuse")),
        "--workspace",
        str(ctx.paths.root),
        "--json",
        "permit",
        "approve",
        "--operation",
        "muse.open",
        "--request-file",
        approval.request_file,
        "--exact-digest",
        approval.action_digest,
        "--request-key",
        "human-reconcile",
    ]
    result = subprocess.run(
        command, env=human_env(tmp_path), capture_output=True, text=True, timeout=120
    )
    envelope = json.loads(result.stdout)
    row["cli_exit"] = result.returncode
    row["cli_status"] = envelope["status"]
    row["cli_error"] = envelope.get("error")
    row["marker_in_cli_output"] = marker in result.stdout + result.stderr
    if envelope["status"] == "ok":
        request = m.MuseOpenRequest.model_validate(approval.request)
        request.access_grant_id = envelope["data"]["id"]
        accepted = invoke(ctx, "open", request)
        row["reconcile_status"] = accepted.status
        if accepted.status == "accepted":
            run(ctx)
            row["step_state"] = ctx.store.get(m.JobStep, discussion.open_step_id).state
    else:
        row["step_state"] = ctx.store.get(m.JobStep, discussion.open_step_id).state
    assert result.returncode == 0, row
    assert row["reconcile_status"] == "accepted", row
    assert row["step_state"] != "open_uncertain", row
    assert not row["marker_in_review"] and not row["marker_in_cli_output"], row
    assert approval.request["topic"] == safety.redact_text(topic)[0]


def test_reconcile_saved_display_changed_after_upgrade(fixture_ctx, project, monkeypatch):
    ctx = fixture_ctx
    topic = "Review /home/synthetic/notes.txt"
    discussion = legacy_open(ctx, project, monkeypatch, topic, uncertain=True)
    request = m.MuseOpenRequest(
        project_id=project, discussion_id=discussion.id, topic=topic, request_key="display-upgrade"
    )
    review = invoke(ctx, "open", request)
    assert review.approval is not None
    saved = m.MuseOpenRequest.model_validate(review.approval.request)
    saved.request_key = "display-upgrade-retry"
    monkeypatch.setattr(lifecycle, "redact_display", lambda text: "Current display title")
    result = invoke(ctx, "open", saved)
    assert result.error.code == m.ErrorCode.REFERENCE_INVALID
    assert "reconciliation again" in result.error.next_action.lower()
    assert result.approval is None
    assert ctx.store.get(m.JobStep, discussion.open_step_id).state == "open_uncertain"
