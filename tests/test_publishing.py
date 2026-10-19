"""B19 exact approval and durable publication recovery with no network or accounts."""

import hashlib
import io
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from metamuse import app, cli, mcp, publishing, safety
from metamuse import models as m
from metamuse.config import load_config
from metamuse.jobs import Executor


class FixtureGateway:
    backend = "fixture"

    def __init__(self):
        self.sent = []
        self.reads = []
        self.page = "123456"
        self.read_error = False
        self.send_error = None
        self.observed = None
        self.on_identity = lambda: None

    def identity(self):
        self.on_identity()
        return self.page

    def send(self, page, message):
        self.sent.append((page, message))
        if self.send_error:
            raise self.send_error
        return page + "_98765"

    def read(self, remote_id):
        self.reads.append(remote_id)
        if self.read_error:
            raise m.CoreError(m.ErrorCode.NETWORK_TIMEOUT, "Synthetic read timeout")
        return self.observed or {
            "id": remote_id,
            "from": {"id": self.page},
            "message": self.sent[0][1],
            "is_published": True,
        }


_PAGE_BINDING = {
    "account": "synthetic-page",
    "page_id": "123456",
    "api_version": "v25.0",
}


@pytest.mark.parametrize(
    "network,config,expected_error",
    [
        pytest.param("offline", {}, m.ErrorCode.UNSUPPORTED, id="default"),
        pytest.param(
            "offline",
            {**_PAGE_BINDING, "enabled": False, "backend": "fixture"},
            m.ErrorCode.UNSUPPORTED,
            id="disabled-bound",
        ),
        pytest.param(
            "offline", {"enabled": True}, m.ErrorCode.AUTH_REQUIRED, id="enabled-unconfigured"
        ),
        *[
            pytest.param(
                "offline",
                {
                    **{key: value for key, value in _PAGE_BINDING.items() if key != missing},
                    "enabled": True,
                    "backend": "fixture",
                },
                m.ErrorCode.AUTH_REQUIRED,
                id="fixture-missing-" + missing,
            )
            for missing in ("account", "page_id", "api_version")
        ],
        pytest.param(
            "offline",
            {**_PAGE_BINDING, "enabled": True, "backend": "fixture"},
            None,
            id="fixture-ready",
        ),
        pytest.param(
            "offline",
            {**_PAGE_BINDING, "enabled": True, "auth_ref": "auth_" + "0" * 32},
            m.ErrorCode.AUTH_REQUIRED,
            id="live-offline",
        ),
        pytest.param(
            "live", {**_PAGE_BINDING, "enabled": True}, m.ErrorCode.AUTH_REQUIRED, id="live-no-auth"
        ),
        pytest.param(
            "live",
            {**_PAGE_BINDING, "enabled": True, "auth_ref": "auth_" + "0" * 32},
            None,
            id="live-bound",
        ),
    ],
)
def test_publication_capabilities_follow_current_binding_without_remote_probes(
    setup, monkeypatch, network, config, expected_error
):
    env = setup
    accepted = app.submit_publication(env.ctx, approve(env))
    assert accepted.status == "accepted"
    # Keep ctx.policy at the original fixture binding: a long-lived process must
    # observe the TOML change just as the real preview does.
    assert env.ctx.policy.publishing.backend == "fixture"
    path = env.ctx.paths.root / "metamuse.toml"
    path.write_text(
        "[app]\nnetwork_mode = "
        + json.dumps(network)
        + "\n[publishing]\n"
        + "\n".join(key + " = " + json.dumps(value) for key, value in config.items())
        + "\n"
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Local publication capability/preview accessed credentials or remote I/O")

    monkeypatch.setattr(safety, "resolve_secret", forbidden)
    monkeypatch.setattr(publishing, "GraphPageGateway", forbidden)
    monkeypatch.setattr(publishing.socket, "getaddrinfo", forbidden)
    inspected = app.inspect_capabilities(env.ctx)
    assert inspected.status == "ok"
    rows = {row["name"]: row for row in inspected.data["capabilities"]}
    preview = app.preview_publication(
        env.ctx, {key: value for key, value in env.request.items() if key != "request_key"}
    )
    row = rows["metamuse_publication_preview"]
    assert row["environment"] == "ready"
    assert row["effective"] is (expected_error is None)
    assert row["permission"] == ("granted_scope" if expected_error is None else "not_configured")
    if expected_error is None:
        assert preview.status == "ok" and preview.data["live_verified"] is False
    else:
        assert preview.status == "error" and preview.error.code == expected_error
    assert rows["metamuse_publication_get"]["effective"] is True
    saved = app.get_publication(env.ctx, {"publication_id": accepted.data["publication_id"]})
    assert saved.status == "ok" and saved.data["state"] == "queued"
    for name in ("metamuse_publication_submit", "metamuse_publication_reconcile"):
        assert rows[name]["permission"] == "not_configured"
        assert rows[name]["effective"] is False
        assert rows[name]["environment"] == (
            "ready" if config.get("backend") == "fixture" else "needs_auth"
        )
    assert env.gateway.sent == [] and env.gateway.reads == []


@pytest.mark.parametrize(
    "current_config",
    [
        pytest.param("[publishing]\nenabled = [\n", id="malformed-toml"),
        pytest.param(
            '[publishing]\nenabled = true\napi_version = "latest"\n', id="invalid-typed-config"
        ),
    ],
)
def test_publication_capabilities_reject_malformed_current_config(setup, current_config):
    env = setup
    accepted = app.submit_publication(env.ctx, approve(env))
    assert accepted.status == "accepted" and env.ctx.policy.publishing.enabled
    (env.ctx.paths.root / "metamuse.toml").write_text(current_config)
    inspected = app.inspect_capabilities(env.ctx)
    assert inspected.status == "error" and inspected.data is None
    assert inspected.error.code == m.ErrorCode.INPUT_INVALID
    preview = app.preview_publication(
        env.ctx, {key: value for key, value in env.request.items() if key != "request_key"}
    )
    assert preview.status == "error" and preview.error.code == m.ErrorCode.INPUT_INVALID
    assert (
        app.get_publication(env.ctx, {"publication_id": accepted.data["publication_id"]}).status
        == "ok"
    )
    assert env.gateway.sent == [] and env.gateway.reads == []


@pytest.fixture
def setup(tmp_path):
    now = [datetime.now(UTC)]
    ctx = app.AppContext.open(tmp_path / "workspace", clock=lambda: now[0].isoformat())
    path = ctx.paths.root / "metamuse.toml"
    path.write_text(
        path.read_text().replace(
            "[publishing]\nenabled = false",
            '[publishing]\nenabled = true\nbackend = "fixture"\naccount = "synthetic-page"\n'
            'page_id = "123456"\napi_version = "v25.0"',
        )
    )
    ctx.policy = load_config(ctx.paths.root)
    project = app.create_project(
        ctx, {"name": "Synthetic publishing", "request_key": "project"}
    ).data["id"]
    draft = app.save_draft(
        ctx,
        {
            "project_id": project,
            "text": "活動回顧 😀\r\n字幕與講義。",
            "platform": "facebook",
            "request_key": "draft",
        },
    ).data
    request = {
        "project_id": project,
        "draft_id": draft["draft_id"],
        "revision": 1,
        "account": "synthetic-page",
        "destination": "123456",
        "request_key": "send",
    }
    gateway = FixtureGateway()
    ctx.publishing_gateway = gateway
    app.register_builtin_operations()
    return SimpleNamespace(ctx=ctx, request=request, gateway=gateway, now=now)


def approve(env, request=None, operation="publication.send", key="approve"):
    request = env.request if request is None else request
    preview = safety.approval_preview(env.ctx, operation, request)
    grant = safety.approve_permit(
        env.ctx, preview, expires_at=(env.now[0] + timedelta(hours=1)).isoformat(), request_key=key
    )
    assert grant.status == "ok", grant
    return {**request, "publication_grant": grant.data["id"]}


def run(env):
    with Executor(env.ctx.store, publishing_gateway=env.gateway) as executor:
        executor.run(once=True)


def test_exact_flow_requires_human_approval_and_one_send_even_new_keys(setup):
    env = setup
    refused = app.submit_publication(env.ctx, env.request)
    assert refused.error.code == m.ErrorCode.SCOPE_REQUIRED
    assert refused.approval.kind == "publication"
    assert env.gateway.sent == []
    request = approve(env)
    accepted = app.submit_publication(env.ctx, request)
    assert accepted.status == "accepted" and accepted.data["worker_available"] is False
    assert accepted.data["state"] == "queued" and env.gateway.sent == []
    run(env)
    saved = app.get_publication(env.ctx, {"publication_id": accepted.data["publication_id"]})
    assert saved.data["state"] == "sent_verified" and saved.data["live_verified"] is False
    assert env.ctx.store.get(m.Job, saved.data["job_id"]).remote_state == "confirmed"
    assert len(saved.data["attempts"]) == 1
    assert env.gateway.sent == [("123456", "活動回顧 😀\r\n字幕與講義。")]
    assert env.gateway.reads == ["123456_98765"]
    replay = app.submit_publication(env.ctx, {**request, "request_key": "different-key"})
    assert replay.data["publication_id"] == accepted.data["publication_id"]
    run(env)
    assert len(env.gateway.sent) == 1


def test_receipt_persists_before_read_failure_reconcile_settles_original_step(setup):
    env = setup
    accepted = app.submit_publication(env.ctx, approve(env))
    env.gateway.read_error = True
    run(env)
    publication_id = accepted.data["publication_id"]
    saved = app.get_publication(env.ctx, {"publication_id": publication_id}).data
    assert saved["state"] == "needs_reconcile" and saved["remote_id"] == "123456_98765"
    assert (
        app.submit_publication(env.ctx, {**env.request, "request_key": "bypass"}).error.code
        == m.ErrorCode.EXTERNAL_STATE_UNKNOWN
    )
    ctx2 = app.AppContext.open(env.ctx.paths.root, clock=env.ctx.clock)
    env.ctx = ctx2
    run(env)
    assert len(env.gateway.sent) == 1
    req = {"publication_id": publication_id, "request_key": "reconcile"}
    blocked = app.reconcile_publication(ctx2, req)
    assert blocked.error.code == m.ErrorCode.SCOPE_REQUIRED
    req = approve(env, req, "publication.reconcile", "approve-read")
    assert app.reconcile_publication(ctx2, req).status == "accepted"
    env.gateway.read_error = False
    run(env)
    final = app.get_publication(ctx2, {"publication_id": publication_id}).data
    assert final["state"] == "sent_verified"
    assert ctx2.store.get(m.JobStep, final["step_id"]).state == "succeeded"
    assert ctx2.store.get(m.Job, final["job_id"]).remote_state == "confirmed"
    assert len(env.gateway.sent) == 1


@pytest.mark.parametrize("reconcile", [False, True])
def test_terminal_lf_readback_preserves_approved_payload_and_one_attempt(setup, reconcile):
    env = setup
    text = "活動回顧 😀\r\n字幕與講義。\n"
    draft = app.save_draft(
        env.ctx,
        {
            "project_id": env.request["project_id"],
            "text": text,
            "platform": "facebook",
            "request_key": "terminal-lf-draft",
        },
    )
    env.request = {**env.request, "draft_id": draft.data["draft_id"]}
    env.gateway.observed = {
        "id": "123456_98765",
        "from": {"id": "123456"},
        "message": text[:-1],
        "is_published": True,
    }
    env.gateway.read_error = reconcile
    request = approve(env)
    accepted = app.submit_publication(env.ctx, request)
    run(env)
    publication_id = accepted.data["publication_id"]
    if reconcile:
        before = app.get_publication(env.ctx, {"publication_id": publication_id}).data
        assert before["state"] == "needs_reconcile"
        env.ctx = app.AppContext.open(env.ctx.paths.root, clock=env.ctx.clock)
        env.gateway.read_error = False
        read_request = approve(
            env,
            {"publication_id": publication_id, "request_key": "terminal-lf-read"},
            "publication.reconcile",
            "approve-terminal-lf-read",
        )
        assert app.reconcile_publication(env.ctx, read_request).status == "accepted"
        run(env)
    saved = app.get_publication(env.ctx, {"publication_id": publication_id}).data
    assert saved["state"] == "sent_verified" and saved["live_verified"] is False
    assert saved["payload"] == {"message": text}
    assert len(saved["attempts"]) == 1 and env.gateway.sent == [("123456", text)]
    step = env.ctx.store.get(m.JobStep, saved["step_id"])
    assert step.state == "succeeded"
    assert step.checkpoint["message_readback"] == {
        "comparison": "single_terminal_lf_removed",
        "approved_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "observed_sha256": hashlib.sha256(text[:-1].encode()).hexdigest(),
    }
    # A fresh process and a new request key cannot turn reconciliation into another send.
    env.ctx = app.AppContext.open(env.ctx.paths.root, clock=env.ctx.clock)
    replay = app.submit_publication(env.ctx, {**request, "request_key": "terminal-lf-alias"})
    assert replay.data["publication_id"] == publication_id
    run(env)
    assert len(env.gateway.sent) == 1


@pytest.mark.parametrize(
    "approved,observed",
    [
        ("正文\n", "正文\n"),
        ("正文\n", "正文"),
        ("正文\n\n", "正文\n"),
        ("正文 \n", "正文 "),
        ("首行\r\n末行\n", "首行\r\n末行"),
        ("正文", "正文"),
    ],
)
def test_readback_comparison_records_exact_or_one_terminal_lf(approved, observed):
    publication = SimpleNamespace(
        remote_id="123456_98765", destination="123456", payload={"message": approved}
    )
    result = publishing._verified(
        publication,
        {
            "id": publication.remote_id,
            "from": {"id": publication.destination},
            "message": observed,
            "is_published": True,
        },
    )
    assert result["comparison"] == (
        "exact" if approved == observed else "single_terminal_lf_removed"
    )
    assert result["observed_sha256"] == hashlib.sha256(observed.encode()).hexdigest()


@pytest.mark.parametrize(
    "approved,observed",
    [
        ("正文\r\n", "正文"),
        ("正文\r\n", "正文\r"),
        ("正文\n\n", "正文"),
        ("正文 \n", "正文"),
        ("正文\t\n", "正文"),
        ("正文\u00a0\n", "正文"),
        ("\n正文\n", "正文"),
        ("首行\n末行\n", "首行末行"),
        ("首行\r\n末行\n", "首行\n末行"),
        ("正文\n", "改文"),
        ("正文", "正文\n"),
        ("正文\n", None),
    ],
)
def test_readback_does_not_trim_other_whitespace_or_rewrite_text(approved, observed):
    publication = SimpleNamespace(
        remote_id="123456_98765", destination="123456", payload={"message": approved}
    )
    with pytest.raises(m.CoreError) as caught:
        publishing._verified(
            publication,
            {
                "id": publication.remote_id,
                "from": {"id": publication.destination},
                "message": observed,
                "is_published": True,
            },
        )
    assert caught.value.detail.code == m.ErrorCode.EXTERNAL_STATE_UNKNOWN


@pytest.mark.parametrize("bad", ["id", "from", "is_published"])
def test_terminal_lf_exception_never_ignores_receipt_page_or_published_state(bad):
    publication = SimpleNamespace(
        remote_id="123456_98765", destination="123456", payload={"message": "正文\n"}
    )
    observed = {
        "id": publication.remote_id,
        "from": {"id": publication.destination},
        "message": "正文",
        "is_published": True,
    }
    observed[bad] = {"id": "999999"} if bad == "from" else False
    with pytest.raises(m.CoreError) as caught:
        publishing._verified(publication, observed)
    assert caught.value.detail.code == m.ErrorCode.EXTERNAL_STATE_UNKNOWN


@pytest.mark.parametrize("crash", [False, True])
def test_unknown_without_receipt_never_resends_after_restart_new_grant_or_revision(setup, crash):
    env = setup
    env.gateway.send_error = KeyboardInterrupt() if crash else TimeoutError()
    accepted = app.submit_publication(env.ctx, approve(env))
    if crash:
        with pytest.raises(KeyboardInterrupt):
            run(env)
    else:
        run(env)
    run(env)
    assert len(env.gateway.sent) == 1
    new = approve(env, {**env.request, "request_key": "new-key"}, key="new-approval")
    assert app.submit_publication(env.ctx, new).error.code == m.ErrorCode.EXTERNAL_STATE_UNKNOWN
    revised = app.save_draft(
        env.ctx,
        {
            "project_id": env.request["project_id"],
            "draft_id": env.request["draft_id"],
            "expected_revision": 1,
            "text": "Edited post",
            "platform": "facebook",
            "request_key": "edit",
        },
    )
    assert revised.status == "ok"
    new = approve(
        env, {**env.request, "revision": 2, "request_key": "new-revision"}, key="revision-approval"
    )
    assert app.submit_publication(env.ctx, new).error.code == m.ErrorCode.EXTERNAL_STATE_UNKNOWN
    assert (
        app.reconcile_publication(
            env.ctx,
            {"publication_id": accepted.data["publication_id"], "request_key": "missing-receipt"},
        ).error.code
        == m.ErrorCode.EXTERNAL_STATE_UNKNOWN
    )
    assert len(env.gateway.sent) == 1


@pytest.mark.parametrize(
    "change", ["revoke", "expire", "revision", "stale", "page", "account", "version", "backend"]
)
def test_queue_rechecks_exact_binding_before_send(setup, change):
    env = setup
    request = approve(env)
    accepted = app.submit_publication(env.ctx, request)
    if change == "revoke":
        safety.revoke_permit(env.ctx, request["publication_grant"], request_key="revoke")
    elif change == "expire":
        env.now[0] += timedelta(hours=2)
    elif change == "revision":
        app.save_draft(
            env.ctx,
            {
                "project_id": env.request["project_id"],
                "draft_id": env.request["draft_id"],
                "expected_revision": 1,
                "text": "Edited",
                "platform": "facebook",
                "request_key": "edit",
            },
        )
    elif change == "stale":
        with env.ctx.store.transaction() as conn:
            conn.execute("UPDATE drafts SET stale=1 WHERE id=?", (env.request["draft_id"],))
    else:
        path = env.ctx.paths.root / "metamuse.toml"
        text = path.read_text()
        old, new = {
            "page": ('page_id = "123456"', 'page_id = "999999"'),
            "account": ('account = "synthetic-page"', 'account = "other-page"'),
            "version": ('api_version = "v25.0"', 'api_version = "v24.0"'),
            "backend": ('backend = "fixture"', 'backend = "live"'),
        }[change]
        path.write_text(text.replace(old, new))
    run(env)
    assert env.gateway.sent == []
    assert env.ctx.store.get(m.Job, accepted.data["job_id"]).remote_state == "not_submitted"
    with env.ctx.store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM publication_attempts").fetchone()[0] == 0


@pytest.mark.parametrize("bad", ["id", "from", "message", "is_published"])
def test_receipt_readback_mismatch_cannot_claim_sent_or_resend(setup, bad):
    env = setup
    env.gateway.observed = {
        "id": "123456_98765",
        "from": {"id": "123456"},
        "message": "活動回顧 😀\r\n字幕與講義。",
        "is_published": True,
    }
    env.gateway.observed[bad] = {"id": "999999"} if bad == "from" else False
    result = app.submit_publication(env.ctx, approve(env))
    run(env)
    saved = app.get_publication(env.ctx, {"publication_id": result.data["publication_id"]})
    assert saved.data["state"] == "needs_reconcile"
    run(env)
    assert len(env.gateway.sent) == 1


@pytest.mark.parametrize(
    "field,value", [("parent", "123456_3"), ("account", "wrong"), ("destination", "999999")]
)
def test_wrong_request_target_refused_before_queue(setup, field, value):
    result = app.submit_publication(setup.ctx, {**setup.request, field: value})
    assert result.status == "error" and setup.gateway.sent == []


def test_page_token_identity_mismatch_before_attempt(setup):
    env = setup
    result = app.submit_publication(env.ctx, approve(env))
    env.gateway.page = "999999"
    run(env)
    assert env.gateway.sent == []
    assert env.ctx.store.get(m.Job, result.data["job_id"]).remote_state == "not_submitted"


def test_approval_recomputes_current_draft_and_mcp_cannot_mint_permissions(setup):
    env = setup
    preview = safety.approval_preview(env.ctx, "publication.send", env.request)
    app.save_draft(
        env.ctx,
        {
            "project_id": env.request["project_id"],
            "draft_id": env.request["draft_id"],
            "expected_revision": 1,
            "text": "Edited",
            "platform": "facebook",
            "request_key": "edit",
        },
    )
    with pytest.raises(m.CoreError):
        safety.approve_permit(env.ctx, preview, expires_at=None, request_key="stale-approve")
    assert mcp.call_tool(env.ctx, "permit_approve", {}).is_error
    result = mcp.call_tool(
        env.ctx, "metamuse_publication_submit", {**env.request, "approved": True}
    )
    assert result.is_error


def test_publication_snapshot_and_attempt_are_immutable(setup):
    env = setup
    result = app.submit_publication(env.ctx, approve(env))
    run(env)
    with pytest.raises(m.CoreError), env.ctx.store.transaction() as conn:
        conn.execute(
            "UPDATE publications SET payload='{}' WHERE id=?", (result.data["publication_id"],)
        )
    with pytest.raises(m.CoreError), env.ctx.store.transaction() as conn:
        conn.execute("UPDATE publication_attempts SET action_digest=?", ("0" * 64,))


def test_cli_preview_human_approval_submit_and_mcp_get(setup, tmp_path, capsys):
    env = setup
    base = ["--workspace", str(env.ctx.paths.root), "--json"]
    arguments = [
        "--project",
        env.request["project_id"],
        "--draft",
        env.request["draft_id"],
        "--revision",
        "1",
        "--account",
        "synthetic-page",
        "--destination",
        "123456",
    ]
    assert cli.main([*base, "publication", "preview", *arguments]) == 0
    preview = json.loads(capsys.readouterr().out)["data"]["approval"]
    request_path = tmp_path / "review.json"
    request_path.write_text(json.dumps(preview["request"]))
    assert (
        cli.main(
            [
                *base,
                "permit",
                "approve",
                "--operation",
                "publication.send",
                "--request-file",
                str(request_path),
                "--exact-digest",
                preview["action_digest"],
                "--request-key",
                "cli-approve",
            ]
        )
        == 0
    )
    grant = json.loads(capsys.readouterr().out)["data"]["id"]
    assert (
        cli.main(
            [
                *base,
                "publication",
                "submit",
                *arguments,
                "--publication-grant",
                grant,
                "--request-key",
                "cli-send",
            ]
        )
        == 0
    )
    accepted = json.loads(capsys.readouterr().out)["data"]
    run(env)
    result = mcp.call_tool(
        env.ctx, "metamuse_publication_get", {"publication_id": accepted["publication_id"]}
    )
    assert result.structured_content["data"]["state"] == "sent_verified"


def test_graph_provider_exact_endpoints_token_header_peer_and_no_error_leaks():
    from metamuse._muse_http import HttpResponse

    calls = []
    responses = [
        b'{"id":"123456"}',
        b'{"id":"123456_98765"}',
        b'{"id":"123456_98765","from":{"id":"123456"},"message":"hello","is_published":true}',
    ]

    class Http:
        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            return HttpResponse(200, responses.pop(0), {}, "93.184.216.34")

    gateway = publishing.GraphPageGateway(
        b"syntheticPageToken", "v25.0", 7, http=Http(), resolver=lambda _: ["93.184.216.34"]
    )
    assert gateway.identity() == "123456"
    assert gateway.send("123456", "中文\r\n😀") == "123456_98765"
    assert gateway.read("123456_98765")["is_published"] is True
    assert [call[:2] for call in calls] == [
        ("GET", "https://graph.facebook.com/v25.0/me?fields=id"),
        ("POST", "https://graph.facebook.com/v25.0/123456/feed"),
        (
            "GET",
            "https://graph.facebook.com/v25.0/123456_98765?fields=id%2Cfrom%2Cmessage%2Cis_published",
        ),
    ]
    assert all("syntheticPageToken" not in call[1] for call in calls)
    assert calls[1][2]["body"] == b"message=%E4%B8%AD%E6%96%87%0D%0A%F0%9F%98%80"
    assert calls[1][2]["headers"]["Authorization"] == "Bearer syntheticPageToken"

    class BadHttp:
        def request(self, *args, **kwargs):
            raise RuntimeError("Bearer syntheticPageToken")

    gateway.http = BadHttp()
    with pytest.raises(m.CoreError) as caught:
        gateway.identity()
    assert "syntheticPageToken" not in str(caught.value)
    assert caught.value.__context__ is None


def test_preboundary_expiry_allows_new_approval_but_retains_one_attempt(setup):
    env = setup
    request = approve(env)
    accepted = app.submit_publication(env.ctx, request)
    env.now[0] += timedelta(hours=2)
    run(env)
    assert env.gateway.sent == []
    old_job = env.ctx.store.get(m.Job, accepted.data["job_id"])
    assert old_job.remote_state == "not_submitted"
    new_request = approve(env, {**env.request, "request_key": "fresh-send"}, key="fresh-approval")
    retried = app.submit_publication(env.ctx, new_request)
    assert retried.status == "accepted", retried
    assert retried.data["publication_id"] == accepted.data["publication_id"]
    assert retried.data["job_id"] != old_job.id
    run(env)
    saved = app.get_publication(env.ctx, {"publication_id": accepted.data["publication_id"]}).data
    assert saved["state"] == "sent_verified" and len(saved["attempts"]) == 1
    assert len(env.gateway.sent) == 1


def test_same_request_key_changed_grant_conflicts_including_success_alias(setup):
    env = setup
    request = approve(env)
    app.submit_publication(env.ctx, request)
    run(env)
    alias = {**request, "request_key": "alias-key"}
    assert app.submit_publication(env.ctx, alias).status == "ok"
    result = app.submit_publication(env.ctx, {**alias, "publication_grant": "other-permit"})
    assert result.error.code == m.ErrorCode.IDEMPOTENCY_CONFLICT
    assert len(env.gateway.sent) == 1


@pytest.mark.parametrize("change", ["expire", "revoke"])
def test_permission_changed_during_identity_read_refuses_boundary(setup, change):
    env = setup
    request = approve(env)
    accepted = app.submit_publication(env.ctx, request)

    def changed():
        if change == "expire":
            env.now[0] += timedelta(hours=2)
        else:
            safety.revoke_permit(
                env.ctx, request["publication_grant"], request_key="revoke-in-read"
            )

    env.gateway.on_identity = changed
    run(env)
    assert env.gateway.sent == []
    assert env.ctx.store.get(m.Job, accepted.data["job_id"]).remote_state == "not_submitted"
    with env.ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM publication_attempts").fetchone()[0] == 0


def test_grant_creation_rechecks_revision_inside_transaction(setup, monkeypatch):
    env = setup
    preview = safety.approval_preview(env.ctx, "publication.send", env.request)
    original = safety.create_permit

    def create(ctx, request):
        app.save_draft(
            ctx,
            {
                "project_id": env.request["project_id"],
                "draft_id": env.request["draft_id"],
                "expected_revision": 1,
                "text": "Changed during approval",
                "platform": "facebook",
                "request_key": "approval-race",
            },
        )
        return original(ctx, request)

    monkeypatch.setattr(safety, "create_permit", create)
    with pytest.raises(m.CoreError) as caught:
        safety.approve_permit(env.ctx, preview, expires_at=None, request_key="race-approval")
    assert caught.value.detail.code == m.ErrorCode.DATA_STALE
    with env.ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM permits").fetchone()[0] == 0


def test_crash_after_readback_checkpoint_does_not_finalize_or_allow_new_revision(
    setup, monkeypatch
):
    from metamuse.jobs import StepContext

    env = setup
    accepted = app.submit_publication(env.ctx, approve(env))
    original = StepContext.commit_checkpoint

    def checkpoint(step, value, **kwargs):
        original(step, value, **kwargs)
        if value.get("read_back_verified"):
            raise KeyboardInterrupt()

    with monkeypatch.context() as patch:
        patch.setattr(StepContext, "commit_checkpoint", checkpoint)
        with pytest.raises(KeyboardInterrupt):
            run(env)
    run(env)
    saved = app.get_publication(env.ctx, {"publication_id": accepted.data["publication_id"]}).data
    assert saved["state"] == "needs_reconcile" and saved["remote_id"] == "123456_98765"
    assert env.ctx.store.get(m.Publication, saved["id"]).state == "submitting"
    app.save_draft(
        env.ctx,
        {
            "project_id": env.request["project_id"],
            "draft_id": env.request["draft_id"],
            "expected_revision": 1,
            "text": "A new version",
            "platform": "facebook",
            "request_key": "new",
        },
    )
    new_request = approve(
        env, {**env.request, "revision": 2, "request_key": "second"}, key="second-grant"
    )
    assert (
        app.submit_publication(env.ctx, new_request).error.code
        == m.ErrorCode.EXTERNAL_STATE_UNKNOWN
    )
    reconcile = approve(
        env,
        {"publication_id": saved["id"], "request_key": "settle"},
        "publication.reconcile",
        "settle-grant",
    )
    assert app.reconcile_publication(env.ctx, reconcile).status == "accepted"
    run(env)
    assert env.ctx.store.get(m.Publication, saved["id"]).state == "sent_verified"
    assert env.ctx.store.get(m.JobStep, saved["step_id"]).state == "succeeded"
    assert len(env.gateway.sent) == 1


def test_finish_hook_failure_rolls_back_domain_and_job_together(setup, monkeypatch):
    from metamuse.jobs import OPERATIONS

    env = setup
    accepted = app.submit_publication(env.ctx, approve(env))
    operation = OPERATIONS["publication.send"]

    def fail_after_update(writer, step, result):
        operation.on_finish(writer, step, result)
        raise m.CoreError(m.ErrorCode.STORAGE, "Synthetic finish failure")

    with monkeypatch.context() as patch:
        patch.setitem(
            OPERATIONS, "publication.send", replace(operation, on_finish=fail_after_update)
        )
        run(env)
    saved = env.ctx.store.get(m.Publication, accepted.data["publication_id"])
    assert saved.state == "submitting" and saved.verified_at is None
    assert env.ctx.store.get(m.JobStep, saved.step_id).state == "needs_reconcile"
    assert env.ctx.store.get(m.Job, saved.job_id).state == "awaiting_user"
    assert len(env.gateway.sent) == 1


def test_exact_successful_submit_replay_survives_revision_stale_config_and_revocation(setup):
    env = setup
    request = approve(env)
    accepted = app.submit_publication(env.ctx, request)
    run(env)
    app.save_draft(
        env.ctx,
        {
            "project_id": env.request["project_id"],
            "draft_id": env.request["draft_id"],
            "expected_revision": 1,
            "text": "A later draft",
            "platform": "facebook",
            "request_key": "later",
        },
    )
    with env.ctx.store.transaction() as conn:
        conn.execute("UPDATE drafts SET stale=1 WHERE id=?", (env.request["draft_id"],))
    path = env.ctx.paths.root / "metamuse.toml"
    path.write_text(
        path.read_text().replace("[publishing]\nenabled = true", "[publishing]\nenabled = false")
    )
    safety.revoke_permit(env.ctx, request["publication_grant"], request_key="revoke-later")
    env.now[0] += timedelta(days=2)
    replay = app.submit_publication(env.ctx, request)
    assert (
        replay.status == "ok" and replay.data["publication_id"] == accepted.data["publication_id"]
    )
    assert replay.data["state"] == "sent_verified" and len(env.gateway.sent) == 1
    changed = app.submit_publication(env.ctx, {**request, "revision": 2})
    assert changed.error.code == m.ErrorCode.IDEMPOTENCY_CONFLICT


def test_reconcile_safe_read_retry_spends_one_grant_and_replay_returns_original_job(setup):
    env = setup
    sent = app.submit_publication(env.ctx, approve(env))
    env.gateway.read_error = True
    run(env)
    request = approve(
        env,
        {"publication_id": sent.data["publication_id"], "request_key": "retry-read"},
        "publication.reconcile",
        "read-permit",
    )
    accepted = app.reconcile_publication(env.ctx, request)
    run(env)  # transient failure requeues the safe read with its reservation retained
    assert env.ctx.store.get(m.Permit, request["publication_grant"]).used == 1
    replay = app.reconcile_publication(env.ctx, request)
    assert replay.data["job_id"] == accepted.data["job_id"]
    env.gateway.read_error = False
    run(env)
    assert env.ctx.store.get(m.Permit, request["publication_grant"]).used == 1
    assert env.ctx.store.get(m.Job, accepted.data["job_id"]).state == "succeeded"
    assert env.ctx.store.get(m.Publication, sent.data["publication_id"]).state == "sent_verified"
    assert len(env.gateway.sent) == 1
    assert app.reconcile_publication(env.ctx, request).data["job_id"] == accepted.data["job_id"]


@pytest.mark.parametrize("table", ["publications", "publication_attempts"])
@pytest.mark.parametrize("operation", ["replace", "rowid", "delete"])
def test_publication_identity_sql_cannot_replace_move_or_remove(setup, table, operation):
    env = setup
    app.submit_publication(env.ctx, approve(env))
    run(env)
    with pytest.raises(m.CoreError), env.ctx.store.transaction() as conn:
        if operation == "replace":
            conn.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM {table}")
        elif operation == "rowid":
            conn.execute(f"UPDATE {table} SET rowid=rowid+1000")
        else:
            conn.execute(f"DELETE FROM {table}")


def test_migration_007_preserves_existing_schema6_draft_and_creates_backup(tmp_path):
    from metamuse.paths import WorkspacePaths
    from metamuse.store import MIGRATIONS, Store

    paths = WorkspacePaths.resolve(tmp_path / "old-workspace")
    old = Store(
        paths,
        schema_version=6,
        migrations={key: value for key, value in MIGRATIONS.items() if key <= 6},
    )
    old.insert(m.Project(id="prj_old", name="Synthetic old workspace", created_at=old.clock()))
    old.insert(m.Draft(id="dft_old", project_id="prj_old"))
    old.insert(
        m.DraftRevision(
            draft_id="dft_old",
            revision=1,
            text="Preserved text",
            platform="facebook",
            digest=m.payload_digest("Preserved text"),
        )
    )
    upgraded = Store(paths)
    assert upgraded.schema_version == 7
    assert upgraded.get(m.DraftRevision, "dft_old", revision=1).text == "Preserved text"
    assert upgraded.pre_upgrade_backups()
    with upgraded.connection() as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
        assert conn.execute("SELECT count(*) FROM publications").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["variant", "asset"])
def test_text_only_publisher_refuses_saved_variant_or_attachment_without_dropping_it(setup, kind):
    env = setup
    fields = {}
    if kind == "variant":
        fields["variant"] = {"facebook": {"text": "Different copy"}}
    else:
        project = env.request["project_id"]
        source = m.Source(
            id="src_attachment",
            project_id=project,
            platform="generic",
            collection_method="file_import",
            fetched_at=env.ctx.clock(),
        )
        env.ctx.store.insert(source)
        asset = env.ctx.store.put_object(io.BytesIO(b"synthetic attachment"), mime="text/plain")
        env.ctx.store.insert(
            m.SourceAsset(
                source_id=source.id, project_id=project, asset_id=asset.id, relation="attached"
            )
        )
        fields["assets"] = [asset.id]
    saved = app.save_draft(
        env.ctx,
        {
            "project_id": env.request["project_id"],
            "text": "Text with extra",
            "platform": "facebook",
            "request_key": "extra",
            **fields,
        },
    )
    assert saved.status == "ok"
    result = app.submit_publication(env.ctx, {**env.request, "draft_id": saved.data["draft_id"]})
    assert result.error.code == m.ErrorCode.UNSUPPORTED and env.gateway.sent == []
    revision = env.ctx.store.get(m.DraftRevision, saved.data["draft_id"], revision=1)
    assert (
        getattr(revision, "variant" if kind == "variant" else "assets")
        == fields["variant" if kind == "variant" else "assets"]
    )


@pytest.mark.parametrize("mismatch", ["project", "step", "attempt"])
def test_sql_rejects_cross_project_or_wrong_job_step_ownership(setup, mismatch):
    from metamuse.jobs import StepSpec, submit_job

    env = setup
    accepted = app.submit_publication(env.ctx, approve(env))
    publication = env.ctx.store.get(m.Publication, accepted.data["publication_id"])
    other = app.create_project(env.ctx, {"name": "Other project", "request_key": "other"}).data[
        "id"
    ]
    project = other if mismatch == "project" else publication.project_id
    target = {**publication.target, "intent_id": "pub_bad", "project_id": project}
    fake_job = submit_job(
        env.ctx.store,
        "publication.send",
        "bad-owner",
        env.request,
        project_id=project,
        steps=[StepSpec(target=target)],
    )
    assert fake_job.status == "accepted"
    with env.ctx.store.connection() as conn:
        step_id = conn.execute(
            "SELECT id FROM job_steps WHERE job_id=?", (fake_job.data["job_id"],)
        ).fetchone()[0]
    if mismatch == "step":
        second = submit_job(
            env.ctx.store,
            "publication.send",
            "another-owner",
            env.request,
            project_id=project,
            steps=[StepSpec(target=target)],
        )
        with env.ctx.store.connection() as conn:
            step_id = conn.execute(
                "SELECT id FROM job_steps WHERE job_id=?", (second.data["job_id"],)
            ).fetchone()[0]
    if mismatch == "attempt":
        record = m.PublicationAttempt(
            id="attempt_bad",
            publication_id=publication.id,
            step_id=step_id,
            action_digest=publication.action_digest,
            started_at=env.ctx.clock(),
        )
    else:
        digest = safety.action_digest(target, publication.payload)
        record = publication.model_copy(
            update={
                "id": "pub_bad",
                "project_id": project,
                "job_id": fake_job.data["job_id"],
                "step_id": step_id,
                "target": target,
                "action_digest": digest,
            }
        )
    with pytest.raises(m.CoreError) as caught:
        env.ctx.store.insert(record)
    assert caught.value.detail.code == m.ErrorCode.REFERENCE_INVALID
