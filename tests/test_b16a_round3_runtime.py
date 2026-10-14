"""Fixture and real-process regressions ported from the independent re-review."""

import contextlib
import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from b16a_r3_corpus import leak_matrix
from test_muse_lifecycle import fixture_ctx as fixture_ctx
from test_muse_lifecycle import grant, invoke, opened, packet, run

from metamuse import _muse_lifecycle as lifecycle
from metamuse import app, safety
from metamuse import models as m
from metamuse.muse import AttemptEvidence, StreamAttempt


def legacy_open(ctx, project, monkeypatch, topic, *, uncertain=False):
    request = m.MuseOpenRequest(project_id=project, topic=topic, request_key="legacy-open")
    with monkeypatch.context() as old:
        old.setattr(lifecycle, "_check_question", lambda text: None)
        prepared = lifecycle.prepare_open(ctx, request)
        request.access_grant_id = grant(
            ctx, project, safety.action_digest(prepared.target, prepared.payload)
        )
        accepted = invoke(ctx, "open", request)
        assert accepted.status == "accepted", accepted
        if uncertain:
            ctx.muse_gateway.mode = "open_timeout"
        run(ctx)
    ctx.muse_gateway.mode = "normal"
    return ctx.store.get(m.Discussion, accepted.data["discussion_id"])


@pytest.mark.parametrize("topic_kind", ["credential", "prose"])
def test_r2_5_stored_topic_and_benign_topic_stranded(fixture_ctx, project, monkeypatch, topic_kind):
    ctx = fixture_ctx
    marker = f"{ctx._frontend}_MARKER_STORED_ONLY"
    topic = f"Topic password={marker}" if topic_kind == "credential" else "Session: Opening keynote"
    discussion = legacy_open(ctx, project, monkeypatch, topic)
    pkt = packet(ctx, project)
    request = m.MuseSendRequest(
        discussion_id=discussion.id,
        packet_id=pkt.id,
        question="List evidence and gaps.",
        request_key="new-send",
    )
    result = invoke(ctx, "send", request)
    assert result.error.code == m.ErrorCode.SCOPE_REQUIRED and result.approval is not None
    assert marker not in result.model_dump_json()
    assert "Stored discussion title:" in result.approval.summary
    assert result.approval.topic == safety.redact_text(topic)[0]
    preview = safety.approval_preview(ctx, "muse.send", request.model_dump())
    prepared = lifecycle.prepare_send(ctx, request)
    assert preview.action_digest == safety.action_digest(prepared.target, prepared.payload)
    assert prepared.body["text"] == request.question + "\n\n" + pkt.rendered_text
    request.share_grant_id = grant(ctx, project, preview.action_digest, kind="share", packet=pkt)
    accepted = invoke(ctx, "send", request)
    assert accepted.status == "accepted"
    run(ctx)
    read_preview = invoke(
        ctx, "read", {"exchange_id": accepted.data["exchange_id"], "refresh": True}
    )
    assert read_preview.approval is not None
    assert marker not in read_preview.model_dump_json()
    assert "Stored discussion title:" in read_preview.approval.summary


@pytest.mark.parametrize("topic_kind", ["credential", "prose"])
def test_r2_5_reconcile_after_upgrade_releases_wedge(fixture_ctx, project, monkeypatch, topic_kind):
    ctx = fixture_ctx
    marker = f"{ctx._frontend}_MARKER_UNCERTAIN_ONLY"
    topic = f"Topic password={marker}" if topic_kind == "credential" else "Session: Opening keynote"
    discussion = legacy_open(ctx, project, monkeypatch, topic, uncertain=True)
    assert ctx.store.get(m.JobStep, discussion.open_step_id).state == "open_uncertain"
    fresh = m.MuseOpenRequest(project_id=project, topic="A benign new topic", request_key="fresh")
    prepared = lifecycle.prepare_open(ctx, fresh)
    fresh.access_grant_id = grant(
        ctx, project, safety.action_digest(prepared.target, prepared.payload)
    )
    assert invoke(ctx, "open", fresh).error.code == m.ErrorCode.EXTERNAL_STATE_UNKNOWN
    request = m.MuseOpenRequest(
        project_id=project,
        discussion_id=discussion.id,
        topic=topic,
        request_key="reconcile-after-upgrade",
    )
    review = invoke(ctx, "open", request)
    assert review.error.code == m.ErrorCode.SCOPE_REQUIRED and review.approval is not None
    assert marker not in review.model_dump_json()
    preview = safety.approval_preview(ctx, "muse.open", request.model_dump())
    request.access_grant_id = grant(ctx, project, preview.action_digest, operation="muse.read")
    accepted = invoke(ctx, "open", request)
    assert accepted.status == "accepted"
    run(ctx)
    discussion = ctx.store.get(m.Discussion, discussion.id)
    assert discussion.remote_session
    assert ctx.store.get(m.JobStep, discussion.open_step_id).state == "succeeded"
    assert invoke(ctx, "open", fresh).status == "accepted"
    assert [route for route, _ in ctx.muse_gateway.calls].count("session.start") == 1


def test_r2_5_approval_failure_propagates_refusal(fixture_ctx, project, monkeypatch):
    ctx = fixture_ctx
    refusal = m.CoreError(
        m.ErrorCode.REFERENCE_INVALID,
        "Packet is stale; prepare a new packet",
        next_action="Prepare a new packet",
    )
    request = m.MuseOpenRequest(project_id=project, topic="A topic", request_key="propagate")
    monkeypatch.setattr(safety, "approval_preview", lambda *args: (_ for _ in ()).throw(refusal))
    result = safety.approval_failure(
        ctx, request, m.CoreError(m.ErrorCode.SCOPE_REQUIRED, "A permit is required")
    )
    assert result.error == refusal.detail and result.approval is None


@pytest.mark.parametrize("unit", ["?token=;", "token=", "credential_512", "prefix_512"])
def test_r2_2_remote_detail_redacted_before_write_and_before_cut(
    fixture_ctx, project, monkeypatch, unit
):
    ctx = fixture_ctx
    opening, _ = opened(ctx, project)
    pkt = packet(ctx, project)
    request = m.MuseSendRequest(
        discussion_id=opening.data["discussion_id"],
        packet_id=pkt.id,
        question="List evidence.",
        request_key="error-detail",
    )
    prepared = lifecycle.prepare_send(ctx, request)
    request.share_grant_id = grant(
        ctx,
        project,
        safety.action_digest(prepared.target, prepared.payload),
        kind="share",
        packet=pkt,
    )
    marker = f"{ctx._frontend}_MARKER_STRADDLING_512"
    detail = (
        ("a " * 244 + "password=" + marker + " tail")
        if unit == "credential_512"
        else unit * (16384 // len(unit))
    )
    if unit == "prefix_512":
        marker = "gh" + "p_" + (f"{ctx._frontend}SyntheticMarker1234" * 3)[:36]
        detail = "a " * 249 + marker + " tail"
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
    transaction = ctx.store.transaction
    depth = 0

    @contextlib.contextmanager
    def tracked():
        nonlocal depth
        with transaction() as conn:
            depth += 1
            try:
                yield conn
            finally:
                depth -= 1

    monkeypatch.setattr(ctx.store, "transaction", tracked)
    real = lifecycle.redact_text
    calls = []

    def check(text):
        # Locally bounded settle views are covered by the separate 56-KiB measure.
        if text == detail:
            calls.append(text)
            assert depth == 0, "Remote-controlled detail redaction held a write lock"
        return real(text)

    monkeypatch.setattr(lifecycle, "redact_text", check)
    accepted = invoke(ctx, "send", request)
    assert accepted.status == "accepted"
    run(ctx)
    exchange = ctx.store.get(m.Exchange, accepted.data["exchange_id"])
    assert calls == [detail]
    assert exchange.attempt_detail == real(detail)[0][:512]
    assert marker not in exchange.attempt_detail
    if unit in {"credential_512", "prefix_512"}:
        assert "[REDACTED]" in exchange.attempt_detail
        assert marker[:8] not in exchange.attempt_detail


def test_r2_9_frozen_stale_refusal_guidance(fixture_ctx, project, monkeypatch):
    from test_b16a_privacy import evidence

    ctx = fixture_ctx
    opening, _ = opened(ctx, project)
    marker = f"{ctx._frontend}_MARKER_LEGACY_PACKET"
    ref = evidence(ctx, project, f"password={marker}!")
    with monkeypatch.context() as old:
        old.setattr("metamuse.content.redact_text", lambda text: (text, []))
        prepared_packet = app.prepare_packet(
            ctx, {"project_id": project, "selection": [ref], "request_key": "old-packet"}
        )
    pkt = ctx.store.get(m.Packet, prepared_packet.data["id"])
    request = m.MuseSendRequest(
        discussion_id=opening.data["discussion_id"],
        packet_id=pkt.id,
        question="Summarize the evidence.",
        request_key="queued-old-packet",
    )
    with monkeypatch.context() as old:
        old.setattr(lifecycle, "redact_text", lambda text: (text, []))
        prepared = lifecycle.prepare_send(ctx, request)
        request.share_grant_id = grant(
            ctx,
            project,
            safety.action_digest(prepared.target, prepared.payload),
            kind="share",
            packet=pkt,
        )
        accepted = invoke(ctx, "send", request)
        assert accepted.status == "accepted"
    run(ctx)
    job = ctx.store.get(m.Job, accepted.data["job_id"])
    exchange = ctx.store.get(m.Exchange, accepted.data["exchange_id"])
    assert job.state == "failed" and job.remote_state == "not_submitted"
    assert job.error["code"] == "E_REFERENCE_INVALID"
    assert "new packet" in job.error["next_action"].lower()
    assert (
        exchange.state,
        exchange.outcome,
        exchange.bytes_handed_off,
        exchange.delivery_proof.kind,
    ) == ("closed", "abandoned", False, "not_submitted")
    assert [route for route, _ in ctx.muse_gateway.calls] == ["session.start"]


def test_packet_fixpoint_backstop_fails_before_hash_or_write(ctx, project, monkeypatch):
    from test_b16a_privacy import evidence

    ref = evidence(ctx, project, "A synthetic source.")
    monkeypatch.setattr("metamuse.content.redact_text", lambda text: (text + "!", []))
    result = app.prepare_packet(
        ctx, {"project_id": project, "selection": [ref], "request_key": "nonfixpoint"}
    )
    assert result.error.code == m.ErrorCode.INPUT_INVALID
    assert result.error.message == "Packet redaction did not stabilize"
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM packets").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM request_receipts WHERE operation='packet.prepare'"
            ).fetchone()[0]
            == 0
        )


def test_r2_1_all_surfaces(fixture_ctx, project, monkeypatch, caplog):
    import test_b16a_privacy as privacy

    cases = leak_matrix()
    monkeypatch.setattr(privacy, "SHAPES", tuple(row[0] for row in cases))
    by_name = {name: (text, marker, "unused") for name, text, marker in cases}
    monkeypatch.setattr(privacy, "credential_shape", by_name.__getitem__)
    privacy.test_packet_bundle_fixture_outbound_jobs_and_mcp(
        fixture_ctx, project, monkeypatch, caplog
    )


def isolated_env(tmp_path):
    env = os.environ.copy()
    env.update(
        {
            name: str(tmp_path / name.lower())
            for name in (
                "HOME",
                "XDG_CONFIG_HOME",
                "XDG_DATA_HOME",
                "XDG_STATE_HOME",
                "XDG_CACHE_HOME",
            )
        }
    )
    env["UV_OFFLINE"] = "1"
    env["PYTHONPATH"] = os.environ.get(
        "PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src")
    )
    return env


@pytest.mark.parametrize("explicit_workspace", [False, True])
def test_r2_8_auth_list_revoke_no_workspace_and_working_guidance(tmp_path, explicit_workspace):
    env = isolated_env(tmp_path)
    store = safety.SecretStore(Path(env["XDG_CONFIG_HOME"]) / "metamuse" / "auth")
    valid = store.put(
        b"fixture", provider="fixture", account="a", created_at="2026-10-14T00:00:00Z"
    )
    second = store.put(
        b"fixture", provider="fixture", account="a", created_at="2026-10-14T00:00:00Z"
    )
    marker = f"{explicit_workspace}_MARKER_AUTH_CONTENT"
    kinds = {}
    for kind in ("corrupt", "deep", "oversized", "world", "symlink", "fifo", "directory"):
        ref = "auth_" + uuid.uuid4().hex
        kinds[kind] = ref
        path = store.root / (ref + ".json")
        if kind == "symlink":
            target = tmp_path / "outside"
            target.write_text(marker)
            path.symlink_to(target)
        elif kind == "fifo":
            os.mkfifo(path, 0o600)
        elif kind == "directory":
            path.mkdir()
        else:
            data = (
                marker
                if kind in ("corrupt", "world")
                else "[" * 20000 + marker + "]" * 20000
                if kind == "deep"
                else marker * 100000
            )
            path.write_text(data)
            path.chmod(0o644 if kind == "world" else 0o600)
    workspace = tmp_path / "workspace"
    base = (
        [str(Path(sys.executable).with_name("metamuse"))]
        + (["--workspace", str(workspace)] if explicit_workspace else [])
        + ["--json"]
    )

    def command(*args):
        result = subprocess.run(
            base + list(args), env=env, capture_output=True, text=True, timeout=30
        )
        assert marker not in result.stdout + result.stderr
        return json.loads(result.stdout)

    listing = command("auth", "list")
    assert listing["status"] == "partial"
    assert {row["auth_ref"] for row in listing["data"]["references"]} == {
        valid["auth_ref"],
        second["auth_ref"],
    }
    unavailable = {row["auth_ref"]: row for row in listing["data"]["unavailable"]}
    for kind, ref in kinds.items():
        next_action = unavailable[ref]["next_action"]
        if kind in ("fifo", "directory"):
            assert (
                next_action
                == "Manually remove the non-regular entry from the private auth directory"
            )
            refused = command("auth", "revoke", "--auth-ref", ref)
            assert refused["error"]["next_action"] == next_action
        else:
            assert shlex.split(next_action) == ["metamuse", "auth", "revoke", "--auth-ref", ref]
            assert command(*shlex.split(next_action)[1:])["status"] == "ok"
    assert (tmp_path / "outside").read_text() == marker
    assert command("auth", "revoke", "--auth-ref", valid["auth_ref"])["status"] == "ok"
    after = command("auth", "list")
    assert len(after["data"]["references"]) == 1
    assert {row["auth_ref"] for row in after["data"]["unavailable"]} == {
        kinds["fifo"],
        kinds["directory"],
    }
    with pytest.raises(m.CoreError) as error:
        store.resolve(valid["auth_ref"], provider="fixture", account="a")
    assert error.value.detail.code == m.ErrorCode.AUTH_REQUIRED
    assert not workspace.exists()
    assert not Path(env["XDG_DATA_HOME"]).exists()
    assert not Path(env["XDG_STATE_HOME"]).exists()


@pytest.mark.parametrize("operation", ["packet", "bundle"])
def test_raw_size_checked_before_any_redaction(ctx, project, monkeypatch, operation):
    from test_b16a_privacy import evidence

    marker = f"{operation}_MARKER_RAW_BUDGET"
    ref = evidence(ctx, project, "password=" + marker * 300)
    ctx.limits.text_max_bytes = 4096
    monkeypatch.setattr(
        "metamuse.content.redact_text", lambda *args: pytest.fail("Raw sizes must be checked first")
    )
    result = (
        app.prepare_packet(
            ctx, {"project_id": project, "selection": [ref], "request_key": "raw-budget"}
        )
        if operation == "packet"
        else app.export_objects(
            ctx,
            {
                "project_id": project,
                "selection": [ref],
                "format": "bundle",
                "request_key": "raw-budget",
            },
        )
    )
    assert result.error.code == m.ErrorCode.LIMIT_REACHED


def test_r2_10_mcp_stdio_imports_selected_tree(ctx, tmp_path, monkeypatch):
    import shutil

    import anyio
    from test_at042_review_fixes import mcp_calls

    import metamuse

    source = tmp_path / "mutant-src"
    shutil.copytree(
        Path(metamuse.__file__).parent,
        source / "metamuse",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    evidence_file = tmp_path / "imported-tree.txt"
    (source / "sitecustomize.py").write_text(
        "import os\nfrom pathlib import Path\nimport metamuse\n"
        'Path(os.environ["B16A_IMPORT_EVIDENCE"]).write_text(metamuse.__file__)\n'
    )
    monkeypatch.setenv("B16A_IMPORT_EVIDENCE", str(evidence_file))
    monkeypatch.setenv("PYTHONPATH", str(source))
    responses = anyio.run(mcp_calls, ctx, [("metamuse_inspect", {})], tmp_path / "stderr.log")
    assert responses[0]["status"] == "ok"
    assert Path(evidence_file.read_text()).is_relative_to(source)


def test_settle_view_enforces_local_bound_before_redaction(fixture_ctx, project, monkeypatch):
    from types import SimpleNamespace

    ctx = fixture_ctx
    opening, _ = opened(ctx, project)
    discussion = ctx.store.get(m.Discussion, opening.data["discussion_id"])
    oversized = SimpleNamespace(
        id="missing",
        baseline={"seq": 0},
        request_marker="marker",
        submitted_at_ms=0,
        outbound_payload=SimpleNamespace(body={"text": "a" * (49152 + 8192 + 3)}),
    )
    monkeypatch.setattr(
        "metamuse._muse_redact.sanitize_document",
        lambda *args: pytest.fail("Bound must precede in-lock redaction"),
    )
    with pytest.raises(m.CoreError) as error:
        lifecycle._exchange_view(ctx.store, discussion, oversized)
    assert error.value.detail.code == m.ErrorCode.LIMIT_REACHED


def test_packet_preview_refuses_legacy_rendering(ctx, project, monkeypatch):
    from test_b16a_privacy import evidence

    from metamuse.content import preview_packet

    marker = f"{'PREVIEW'}_MARKER-legacy"
    ref = evidence(ctx, project, f"password={marker}!")
    with monkeypatch.context() as legacy:
        legacy.setattr("metamuse.content.redact_text", lambda text: (text, []))
        pkt = app.prepare_packet(
            ctx,
            m.PacketPrepareRequest(
                project_id=project,
                selection=[ref],
                request_key="legacy-preview",
            ),
        )
    assert pkt.status == "ok"
    with pytest.raises(m.CoreError) as error:
        preview_packet(ctx, pkt.data["id"])
    assert error.value.detail.code == m.ErrorCode.REFERENCE_INVALID
    assert "prepare a new packet" in error.value.detail.message
    assert marker not in str(error.value)


@pytest.mark.parametrize("rule", ["xml", "flag", "jar", "prefix", "jwt", "pem"])
def test_new_rule_surfaces(fixture_ctx, project, monkeypatch, caplog, rule):
    import test_b16a_privacy as privacy

    marker = f"{rule}_MARKER_SYNTHETIC_ONLY"
    if rule == "prefix":
        marker = "gh" + "p_" + (f"{rule}SyntheticMarker1234" * 3)[:36]
    elif rule == "jwt":
        marker = "ey" + "JhbGciOiJIUzI1NiJ9." + marker + "." + marker
    text = {
        "xml": f"<password>{marker}</password>",
        "flag": f"--password {marker}",
        "jar": f"example.invalid\tTRUE\t/\tFALSE\t0\tsid\t{marker}",
        "prefix": marker,
        "jwt": marker,
        "pem": "-----BE" + "GIN PRIVATE KEY-----\n" + marker + "\n-----E" + "ND PRIVATE KEY-----",
    }[rule]
    monkeypatch.setattr(privacy, "SHAPES", (rule,))
    monkeypatch.setattr(privacy, "credential_shape", lambda shape: (text, marker, "unused"))
    privacy.test_packet_bundle_fixture_outbound_jobs_and_mcp(
        fixture_ctx, project, monkeypatch, caplog
    )
