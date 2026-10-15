"""Independent B16a review regressions; synthetic OFFLINE evidence only."""

import json
import sqlite3
import subprocess
import sys
import tracemalloc
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from test_muse_lifecycle import fixture_ctx as fixture_ctx
from test_muse_lifecycle import invoke

from metamuse import models as m
from metamuse import safety

BENIGN = (
    '"?token="+t',
    "https://example.invalid/?session=",
    "the session ended",
    "token economy",
    "cookie recipe",
    "def refresh(token): ...",
    "max_tokens=256",
    "num_tokens=256",
    "password_min_length = 12",
    'session=os.getenv("SESSION")',
    "password=config.password",
    "the Authorization header carries a bearer token",
    "Authorization schemes include Bearer and Basic",
    "the Cookie header carries credentials",
    "Set Cookie attributes include SameSite",
    "/docs/plan.md",
    "/etc/hosts",
    '{"nextPageToken":"CAUQAA", "eos_token":"end", "bos_token":"start"}',
    '{"session_count":"Morning keynote"}',
    "active_session=42",
    "active_session=3.5",
)


@pytest.mark.parametrize("text", BENIGN)
def test_f3_benign_credential_corpus(text):
    from metamuse._muse_lifecycle import _check_question

    assert safety.redact_text(text) == (text, [])
    _check_question(text)


def test_f3_path_preview_approves_exact_outbound_text(fixture_ctx, project):
    from test_muse_lifecycle import opened, packet

    from metamuse._muse_lifecycle import prepare_open, prepare_send

    ctx = fixture_ctx
    text = "Compare with /docs/plan.md and /tmp/synthetic.txt please"
    opening = m.MuseOpenRequest(project_id=project, topic=text, request_key="path-topic")
    preview = invoke(ctx, "open", opening).approval
    assert preview.topic == preview.request["topic"] == text and text in preview.summary
    assert prepare_open(ctx, opening).body["title"] == text
    accepted, _ = opened(ctx, project)
    request = m.MuseSendRequest(
        discussion_id=accepted.data["discussion_id"],
        packet_id=packet(ctx, project).id,
        question=text,
        request_key="path-question",
    )
    preview = invoke(ctx, "send", request).approval
    assert preview.question == preview.request["question"] == text and text in preview.summary
    assert prepare_send(ctx, request).body["text"].startswith(text + "\n\n")


def adversarial_redaction(text):
    # CPU growth is tested over four doubling sizes in round 3. This helper
    # keeps the original adversarial shapes as output regressions, with no alarm.
    return safety.redact_text(text)


@pytest.mark.parametrize("case", ["unicode", "quotes"])
def test_f1_json_key_complexity(case):
    text = '"' + (r"\u0061" * 26 if case == "unicode" else r"\"" * 24575)
    assert adversarial_redaction(text) == (text, [])


@pytest.mark.parametrize("prefix", ["", "Cookie: ", "Set-Cookie: "])
@pytest.mark.parametrize("session_first", [False, True])
def test_f2_cookie_chain_keeps_all_members_private(prefix, session_first):
    marker = f"csrf_{session_first}_MARKER_SYNTHETIC_ONLY"
    pairs = ["theme=dark", "hatch_sess=S1", f"hatch_csrf={marker}"]
    if session_first:
        pairs.reverse()
    text = prefix + "; ".join(pairs) + "; Path=/; SameSite=Lax; HttpOnly; Secure"
    output, counts = safety.redact_text(text)
    assert marker not in output and "S1" not in output
    assert any(record["kind"] == "cookie" for record in counts)
    assert safety.redact_text(output) == (output, [])


def test_f5_topic_refusal_before_preview_or_state(fixture_ctx, monkeypatch):
    from metamuse._muse_lifecycle import _wire, prepare_open

    ctx = fixture_ctx
    marker = f"{ctx._frontend}_MARKER_SYNTHETIC_ONLY"
    request = m.MuseOpenRequest(
        project_id="missing", topic=f"hatch_sess={marker}", request_key="topic-refused"
    )
    # Freeze a body using the previous rules, then exercise the worker's second check.
    with monkeypatch.context() as old_rules:
        old_rules.setattr("metamuse._muse_lifecycle._check_question", lambda value: None)
        legacy = prepare_open(ctx, request)
    monkeypatch.setattr(ctx.store, "get", lambda *a, **kw: pytest.fail("Refusal must come first"))
    for call in (lambda: prepare_open(ctx, request), lambda: _wire(legacy)):
        with pytest.raises(m.CoreError) as error:
            call()
        assert error.value.detail.code == m.ErrorCode.INPUT_INVALID
        assert marker not in str(error.value)
    result = invoke(ctx, "open", request)
    assert result.error.code == m.ErrorCode.INPUT_INVALID and result.approval is None
    assert marker not in result.model_dump_json()
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM discussions").fetchone()[0] == 0
    assert ctx.muse_gateway.calls == []


def test_f4_replay_after_upgrade_and_frozen_refusal(fixture_ctx, project, monkeypatch):
    from test_muse_lifecycle import grant, opened, packet, run

    from metamuse import _muse_lifecycle as lifecycle
    from metamuse import app

    ctx = fixture_ctx
    opening, _ = opened(ctx, project)
    pkt = packet(ctx, project)
    marker = f"{ctx._frontend}_MARKER_LEGACY_ONLY"
    request = m.MuseSendRequest(
        discussion_id=opening.data["discussion_id"],
        packet_id=pkt.id,
        question=f"hatch_sess={marker}",
        request_key="old-request",
    )
    with monkeypatch.context() as old_rules:
        old_rules.setattr(lifecycle, "_check_question", lambda text: None)
        prepared = lifecycle.prepare_send(ctx, request)
        request.share_grant_id = grant(
            ctx,
            project,
            safety.action_digest(prepared.target, prepared.payload),
            kind="share",
            packet=pkt,
        )
        original = invoke(ctx, "send", request)
    assert original.status == "accepted"
    # Even preparation must return the original frozen body before the new detector.
    assert lifecycle.prepare_send(ctx, request) == prepared
    with monkeypatch.context() as read_only:
        read_only.setattr(ctx.store, "transaction", lambda: pytest.fail("Replay must be read-only"))
        replay = invoke(ctx, "send", request)
    assert replay.status == "accepted" and replay.data["job_id"] == original.data["job_id"]
    run(ctx)
    job = ctx.store.get(m.Job, original.data["job_id"])
    exchange = ctx.store.get(m.Exchange, original.data["exchange_id"])
    assert job.remote_state == "not_submitted"
    assert exchange.state == "closed" and exchange.outcome == "abandoned"
    assert exchange.bytes_handed_off is False and exchange.delivery_proof.kind == "not_submitted"
    assert "Remove credentials" in job.error["next_action"]
    assert [route for route, _ in ctx.muse_gateway.calls] == ["session.start"]
    next_request = request.model_copy(
        update={"question": "List gaps.", "request_key": "new-request", "share_grant_id": None}
    )
    result = app.submit_muse_exchange(ctx, next_request)
    assert result.error.code == m.ErrorCode.SCOPE_REQUIRED and result.approval is not None


def test_f4_open_replay_and_frozen_topic_refusal(fixture_ctx, project, monkeypatch):
    from test_muse_lifecycle import grant, run

    from metamuse import _muse_lifecycle as lifecycle

    ctx = fixture_ctx
    marker = f"{ctx._frontend}_MARKER_LEGACY_TOPIC"
    request = m.MuseOpenRequest(
        project_id=project, topic=f"hatch_sess={marker}", request_key="old-open"
    )
    with monkeypatch.context() as old_rules:
        old_rules.setattr(lifecycle, "_check_question", lambda text: None)
        prepared = lifecycle.prepare_open(ctx, request)
        request.access_grant_id = grant(
            ctx, project, safety.action_digest(prepared.target, prepared.payload)
        )
        original = invoke(ctx, "open", request)
    assert original.status == "accepted"
    with monkeypatch.context() as read_only:
        read_only.setattr(ctx.store, "transaction", lambda: pytest.fail("Replay must be read-only"))
        assert lifecycle.prepare_open(ctx, request) == prepared
        assert invoke(ctx, "open", request).data["job_id"] == original.data["job_id"]
    run(ctx)
    job = ctx.store.get(m.Job, original.data["job_id"])
    discussion = ctx.store.get(m.Discussion, original.data["discussion_id"])
    assert job.remote_state == "not_submitted"
    assert (
        discussion.open_bytes_handed_off is False
        and discussion.open_delivery_proof.kind == "not_submitted"
    )
    assert ctx.muse_gateway.calls == []


@pytest.mark.parametrize("separator", ["?", "%3F", "%253F", "%25253F", "&", "#", "%2526", "%2523"])
@pytest.mark.parametrize("sentinel", ["", "[REDACTED]", "%255BREDACTED%255D"])
def test_f7_original_url_value_boundaries(separator, sentinel):
    marker = f"{len(separator)}_MARKER_SYNTHETIC_ONLY"
    text = f"https://example.invalid/?password={sentinel}{separator}{marker}&view=1"
    output, _ = safety.redact_text(text)
    if separator in {"?", "%2526", "%2523"}:
        assert marker not in output  # Deeper decoded delimiters are value data.
    else:
        assert marker in output  # Encoded '?' is the query exception; raw '&/#' separate fields.
    assert "view=1" in output
    prefix_value = f"https://example.invalid/?token={sentinel}{marker}&view=1"
    assert marker not in safety.redact_text(prefix_value)[0]
    exact = "https://example.invalid/?token=[REDACTED]&view=1"
    assert safety.redact_text(exact) == (exact, [])


@pytest.mark.parametrize("operation", ["resolve", "list"])
def test_f8_deep_secret_has_fixed_detached_error(tmp_path, operation):
    secret = safety.SecretStore(tmp_path / "auth")
    secret.root.mkdir(mode=0o700)
    auth_ref = "auth_" + uuid.uuid4().hex
    marker = f"{operation}_MARKER_SYNTHETIC_ONLY"
    path = secret.root / (auth_ref + ".json")
    path.write_text("[" * 20000 + '"' + marker + '"' + "]" * 20000)
    path.chmod(0o600)
    with pytest.raises(m.CoreError) as error:
        if operation == "resolve":
            secret.resolve(auth_ref, provider="synthetic", account="synthetic")
        else:
            secret.list()
    assert error.value.detail.code == m.ErrorCode.AUTH_REQUIRED
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert marker not in str(error.value)


@pytest.mark.parametrize(
    "command",
    [
        ["auth", "list"],
        ["auth", "import", "--file", "missing"],
        ["auth", "revoke", "--auth-ref", "missing"],
        ["permit", "list", "--project", "missing"],
        ["permit", "revoke", "missing", "--request-key", "refused"],
        [
            "permit",
            "approve",
            "--operation",
            "muse.open",
            "--request-file",
            "missing",
            "--request-key",
            "refused",
        ],
        [
            "permit",
            "create",
            "--project",
            "missing",
            "--kind",
            "access",
            "--scope-file",
            "missing",
            "--provider",
            "synthetic",
            "--account",
            "synthetic",
            "--issuer",
            "operator",
            "--expires-at",
            "2026-10-15T00:00:00Z",
            "--max-uses",
            "1",
            "--request-key",
            "refused",
        ],
    ],
)
def test_f9_human_only_subprocess_never_opens_workspace(tmp_path, command):
    workspace = tmp_path / "must-not-exist"
    process = subprocess.run(
        [
            str(Path(sys.executable).with_name("metamuse")),
            "--workspace",
            str(workspace),
            "--json",
            "--non-interactive",
            *command,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert process.returncode == 3, process.stderr
    assert json.loads(process.stdout)["error"]["code"] == "E_SCOPE_REQUIRED"
    assert not workspace.exists()


def test_f10_auth_list_partial_retains_valid_refs_and_exact_revoke(ctx, tmp_path):
    secret = safety.SecretStore(tmp_path / "auth")
    ctx.secret_store = secret
    marker = f"{tmp_path.name}_MARKER_SYNTHETIC_ONLY"
    valid = [
        secret.put(
            marker.encode(), provider="synthetic", account="synthetic", created_at=ctx.clock()
        )
        for _ in range(2)
    ]
    bad_ref = "auth_" + uuid.uuid4().hex
    path = secret.root / (bad_ref + ".json")
    path.write_text('{"secret": "' + marker)
    path.chmod(0o600)
    ctx.policy.muse.binding = m.MuseBinding(
        provider="synthetic", account="synthetic", auth_ref=bad_ref
    )
    result = safety.list_auth(ctx)
    assert result.status == "partial"
    assert {record["auth_ref"] for record in result.data["references"]} == {
        record["auth_ref"] for record in valid
    }
    corrupt = result.data["unavailable"]
    assert len(corrupt) == 1 and corrupt[0]["auth_ref"] == bad_ref
    assert corrupt[0]["reason"] == "Secret record is unavailable or invalid"
    assert corrupt[0]["next_action"] == f"metamuse auth revoke --auth-ref {bad_ref}"
    assert (
        marker not in result.model_dump_json() and str(secret.root) not in result.model_dump_json()
    )
    assert safety.revoke_auth(ctx, bad_ref).data["config_bound"]
    assert safety.list_auth(ctx).status == "ok"


def test_f11_sparse_percent_escape_memory():
    text = "%41" + "a" * (2 * 1024 * 1024 - 3)
    tracemalloc.start()
    try:
        assert safety.redact_text(text) == (text, [])
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < len(text) * 4


@pytest.mark.parametrize("case", ["cookies", "path", "encoded"])
def test_f13_linear_redaction_complexity(case):
    if case == "cookies":
        text = "a=" * 24576
    elif case == "path":
        text = '"' + "/a" * 24575
    else:
        unit = "?token=" + "%20" * 64
        text = (unit * (20 * 1024 * 1024 // len(unit) + 1))[: 20 * 1024 * 1024]
    adversarial_redaction(text)


@pytest.mark.parametrize("case", ["header", "quoted"])
def test_f13_unmatched_value_helpers_are_bounded(case):
    text = (
        "Cookie: " + "a" * 49152
        if case == "header"
        else '{"password": "' + "a" * 98304 + ' prose"}'
    )
    output, findings = adversarial_redaction(text)
    assert output != text and findings
    assert safety.redact_text(output) == (output, [])


@pytest.mark.parametrize("operation", ["packet", "bundle"])
def test_f13_redaction_does_not_hold_sqlite_write_lock(ctx, project, monkeypatch, operation):
    from test_b16a_privacy import evidence

    from metamuse import app, content

    ref = evidence(ctx, project, "Synthetic evidence")
    redact = content.redact_text
    calls = []

    def unlocked(text):
        with ctx.store.connection() as conn:
            conn.execute("PRAGMA busy_timeout=1")
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                pytest.fail("Redaction holds the SQLite write lock")
            conn.rollback()
        calls.append(True)
        return redact(text)

    monkeypatch.setattr(content, "redact_text", unlocked)
    request = {"project_id": project, "selection": [ref], "request_key": "unlocked"}
    result = (
        app.prepare_packet(ctx, request)
        if operation == "packet"
        else app.export_objects(ctx, {**request, "format": "bundle"})
    )
    assert result.status == "ok" and calls


@pytest.mark.parametrize(
    "template",
    [
        "<a href=/docs/setup>token={marker}</a>",
        "cfg=/home/synthetic/x)token={marker}",
        "opts=/srv/app/conf`password={marker}",
        "user=bob|password={marker}",
        "a=1/token={marker}",
    ],
)
def test_f6_redaction_reaches_fixpoint_before_digest(template):
    from metamuse._muse_lifecycle import _check_question

    marker = f"{len(template)}_MARKER_SYNTHETIC_ONLY"
    text = template.format(marker=marker)
    output, counts = safety.redact_text(text)
    assert marker not in output and counts
    assert safety.redact_text(output) == (output, [])
    with pytest.raises(m.CoreError) as refusal:
        _check_question(text)
    assert refusal.value.detail.code == m.ErrorCode.INPUT_INVALID


@pytest.mark.parametrize(
    "name",
    [
        "hatch_sess",
        "mm_sess",
        "cookie_header",
        "notary_token",
        "noiseNotaryToken",
        "password",
        "secret",
        "api_key",
        "sessionid",
    ],
)
@pytest.mark.parametrize("layers", [0, 1, 2, 3])
def test_handoff_sensitive_colon_names(name, layers):
    from urllib.parse import quote

    from metamuse._muse_lifecycle import _check_question

    marker = f"{name}_{layers}_MARKER_SYNTHETIC_ONLY"
    text = f"{name}: {marker}"
    for _ in range(layers):
        text = quote(text, safe="")
    output, counts = safety.redact_text(text)
    assert marker not in output and counts
    assert safety.redact_text(output) == (output, [])
    with pytest.raises(m.CoreError):
        _check_question(text)


def test_handoff_topics_refuse_all_shapes_before_preview(fixture_ctx):
    from test_b16a_privacy import SHAPES, credential_shape

    for shape in SHAPES:
        text, marker, _ = credential_shape(shape)
        result = invoke(
            fixture_ctx,
            "open",
            {"project_id": "missing", "topic": text, "request_key": "topic-" + shape},
        )
        assert result.error.code == m.ErrorCode.INPUT_INVALID and result.approval is None
        assert marker not in result.model_dump_json()
    assert fixture_ctx.muse_gateway.calls == []


@pytest.mark.parametrize("layers", [1, 2, 3])
@pytest.mark.parametrize("form", ["assignment", "json"])
def test_f12_credential_name_encoding_layers(layers, form):
    from urllib.parse import quote

    key = "hatch_sess"
    for _ in range(layers):
        key = key.replace("_", "%5F") if "%" not in key else quote(key, safe="")
    marker = f"{form}_{layers}_MARKER_SYNTHETIC_ONLY"
    text = f"{key}={marker}" if form == "assignment" else json.dumps({key: marker})
    assert marker not in safety.redact_text(text)[0]


def test_f12_submit_has_its_own_guard(fixture_ctx, monkeypatch):
    from metamuse import _muse_lifecycle as lifecycle

    marker = f"{fixture_ctx._frontend}_MARKER_SYNTHETIC_ONLY"
    monkeypatch.setattr(
        lifecycle, "prepare_send", lambda *a: pytest.fail("Submit skipped its check")
    )
    result = invoke(
        fixture_ctx,
        "send",
        {
            "discussion_id": "missing",
            "packet_id": "missing",
            "question": f"hatch_sess={marker}",
            "request_key": "submit-check",
        },
    )
    assert result.error.code == m.ErrorCode.INPUT_INVALID and result.approval is None


def test_f12_wire_binds_checked_text_to_actual_body(fixture_ctx, project):
    from test_muse_lifecycle import opened, packet

    from metamuse._muse_lifecycle import _wire, prepare_send

    opening, _ = opened(fixture_ctx, project)
    prepared = prepare_send(
        fixture_ctx,
        m.MuseSendRequest(
            discussion_id=opening.data["discussion_id"],
            packet_id=packet(fixture_ctx, project).id,
            question="Checked question",
            request_key="body-check",
        ),
    )
    body = {**prepared.body, "text": "Different unchecked body"}
    changed = prepared.model_copy(
        update={"body": body, "payload": {**prepared.payload, "body": body}}
    )
    with pytest.raises(m.CoreError) as refusal:
        _wire(changed)
    assert refusal.value.detail.code == m.ErrorCode.PROTOCOL_MISMATCH
    assert refusal.value.detail.message == "Prepared send body differs from checked inputs"


def test_f12_preview_refuses_before_project_lookup(fixture_ctx, monkeypatch):
    marker = f"{fixture_ctx._frontend}_MARKER_SYNTHETIC_ONLY"
    monkeypatch.setattr(
        fixture_ctx.store, "get", lambda *a, **kw: pytest.fail("Preview read before refusal")
    )
    with pytest.raises(m.CoreError) as refusal:
        safety.approval_preview(
            fixture_ctx,
            "muse.open",
            {
                "project_id": "missing",
                "topic": f"hatch_sess={marker}",
                "request_key": "direct-preview",
            },
        )
    assert refusal.value.detail.code == m.ErrorCode.INPUT_INVALID


@pytest.mark.parametrize("name", ["password", "token", "hatch_sess", "secret"])
def test_numeric_credentials_are_not_session_counters(name):
    value = f"{12}{34}"
    assert value not in safety.redact_text(f"{name}={value}")[0]
    assert value not in safety.redact_text(f"Cookie: active_session={value}")[0]


@pytest.mark.parametrize("form", ["document", "prose", "incomplete"])
@pytest.mark.parametrize("layers", range(4))
def test_f3_json_escaped_credential_values_are_classified(form, layers):
    from metamuse._muse_lifecycle import _check_question
    from metamuse.content import _safe_value

    marker = f"{form}_{layers}_MARKER_SYNTHETIC_ONLY"
    value = "".join(f"\\u{ord(char):04x}" for char in marker)
    text = '{"password": "' + value + '"}'
    if form == "prose":
        text = "note " + text + " end"
    elif form == "incomplete":
        text = text[:-1]
    for _ in range(layers):
        text = quote(text, safe="")
    output, counts = safety.redact_text(text)
    assert "[REDACTED]" in output
    assert counts == [{"kind": "sensitive_field", "count": 1}]
    assert safety.redact_text(output) == (output, [])
    structured_counts = []
    assert _safe_value({"text": text}, structured_counts) == {"text": output}
    assert structured_counts == counts
    with pytest.raises(m.CoreError) as error:
        _check_question(text)
    assert error.value.detail.code == m.ErrorCode.INPUT_INVALID
