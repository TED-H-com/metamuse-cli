"""OFFLINE/FIXTURE: current policy, executable reviews and reusable approval entrypoints."""

import shlex

import pytest
import test_publishing
from test_facebook import _reset_facebook_seams as reset_fb_seams  # noqa: F401
from test_facebook import _setup_fb_env, _write_facebook_config

from metamuse import app, safety
from metamuse import models as m

setup = test_publishing.setup


def test_facebook_review_checks_current_policy_and_never_resolves_credentials(
    tmp_path, monkeypatch
):
    ctx, project, _, auth_ref, _ = _setup_fb_env(tmp_path)
    body = {
        "project_id": project,
        "inputs": [{"kind": "url", "url": "https://m.facebook.com/synth.lab/posts/1234"}],
        "resources": ["post"],
        "access_grant_id": None,
        "limits": {"max_urls": 1, "max_items": 100, "max_bytes": 10000, "wall_clock_seconds": 60},
        "request_key": "review",
    }

    def forbidden(*args, **kwargs):
        pytest.fail("Preview attempted secret resolution, a browser probe or network IO")

    from metamuse import facebook

    monkeypatch.setattr(facebook, "load_storage_state", forbidden)
    monkeypatch.setattr(facebook, "ensure_browser_available", forbidden)
    monkeypatch.setattr(facebook, "_active_egress", forbidden)
    preview = app.submit_collect(ctx, body)
    assert preview.error.code == m.ErrorCode.SCOPE_REQUIRED
    assert preview.approval.destination == "https://www.facebook.com/synth.lab/posts/1234"
    assert preview.approval.request["inputs"][0]["url"] == preview.approval.destination
    _write_facebook_config(ctx.paths.root, enabled=False, auth_ref=auth_ref)
    assert ctx.policy.facebook.enabled  # Long-lived MCP context is deliberately stale.
    refused = app.submit_collect(ctx, body)
    assert refused.error.code == m.ErrorCode.SCOPE_REQUIRED and refused.approval is None
    assert "disabled" in refused.error.message
    assert app.list_jobs(ctx, {}).data["jobs"] == []


def test_publication_review_command_is_executable_and_fresh_after_revocation(setup):
    env = setup
    preview = safety.approval_preview(env.ctx, "publication.send", env.request)
    command = shlex.split(preview.next_action)
    assert command[0] == "metamuse"
    key = command[command.index("--request-key") + 1]
    assert preview.action_digest in preview.summary
    assert preview.destination in preview.summary
    assert preview.account in preview.summary
    grant = safety.approve_permit(env.ctx, preview, expires_at=None, request_key=key).data["id"]
    safety.revoke_permit(env.ctx, grant, request_key="revoke")
    fresh = safety.approval_preview(env.ctx, "publication.send", env.request)
    assert fresh.action_digest == preview.action_digest
    assert fresh.next_action != preview.next_action
    command = shlex.split(fresh.next_action)
    new_key = command[command.index("--request-key") + 1]
    renewed = safety.approve_permit(env.ctx, fresh, expires_at=None, request_key=new_key)
    assert renewed.data["id"] != grant
