"""Independent review regressions via installed CLI and official MCP stdio client.

No application imports or database access. Discussion/exchange creation is atomic
with its job; unchanged shipped job inventories prove that those calls create none.
"""

import json
import shlex
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import pytest
from test_journey import Entrypoints


def inventory(entry, project):
    return (
        entry.cli("jobs", "list", "--project", project)["data"]["jobs"],
        entry.cli("permit", "list", "--project", project, operator=True)["data"]["permits"],
    )


def send(entry, request, *, code=3):
    path = entry.inputs / "question.txt"
    path.write_text(request["question"])
    args = [
        "muse",
        "send",
        "--discussion",
        request["discussion_id"],
        "--packet",
        request["packet_id"],
        "--question-file",
        path,
        "--request-key",
        request["request_key"],
    ]
    if request.get("share_grant_id"):
        args.extend(["--share-grant", request["share_grant_id"]])
    return entry.cli(*args, code=code)


def collect(entry, project, text, key):
    path = entry.inputs / "source.jsonl"
    path.write_text(
        json.dumps(
            {
                "platform": "generic",
                "kind": "post",
                "native_scope": "approval-review",
                "native_id": "post-one",
                "text": text,
            }
        )
        + "\n"
    )
    entry.cli(
        "add",
        "--project",
        project,
        "--file",
        path,
        "--kind",
        "jsonl",
        "--request-key",
        key,
        "--run",
    )


@pytest.fixture
def world(tmp_path):
    entry = Entrypoints(tmp_path)
    entry.configure()
    project = entry.cli("project", "create", "--name", "Review", "--request-key", "project")[
        "data"
    ]["id"]
    collect(entry, project, "Synthetic review evidence", "collect")
    hits = entry.cli("search", "--project", project, "--query", "Synthetic")["data"]["results"]
    refs = entry.inputs / "refs.json"
    refs.write_text(json.dumps([hit["ref"] for hit in hits]))
    packet = entry.cli(
        "packet",
        "prepare",
        "--project",
        project,
        "--selection-file",
        refs,
        "--request-key",
        "packet",
    )["data"]
    opening = {"project_id": project, "topic": "Synthetic review", "request_key": "open"}
    preview = entry.cli(
        "muse",
        "open",
        "--project",
        project,
        "--topic",
        opening["topic"],
        "--request-key",
        "open",
        code=3,
    )["approval"]
    grant = entry.approve(preview, opening, "open-grant")["data"]["id"]
    opened = entry.cli(
        "muse",
        "open",
        "--project",
        project,
        "--topic",
        opening["topic"],
        "--request-key",
        "open",
        "--access-grant",
        grant,
    )["data"]
    entry.cli("worker", "run", "--once")
    assert entry.cli("jobs", "get", opened["job_id"])["data"]["job"]["state"] == "succeeded"
    request = {
        "discussion_id": opened["discussion_id"],
        "packet_id": packet["id"],
        "question": "List evidence and gaps.",
        "request_key": "send",
    }
    return entry, project, packet, request


@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_nested_json_question_refused_before_preview(world, channel):
    entry, project, _, request = world
    marker = f"{channel}_MARKER_SYNTHETIC_ONLY"
    request = {**request, "question": 'Review {"password": "' + marker + '"} carefully.'}
    before = inventory(entry, project)
    if channel == "cli":
        envelope = send(entry, request, code=2)
    else:

        async def preview_call():
            async with entry.server() as session:
                return await entry.call(session, "muse_send", request, error=True)

        envelope = anyio.run(preview_call)
    assert envelope["error"]["code"] == "E_INPUT_INVALID"
    assert envelope["approval"] is None
    assert marker not in json.dumps(envelope)
    assert inventory(entry, project) == before
    for row in entry.transcript:
        assert marker not in json.dumps(row["envelope"])
        assert marker not in row.get("stderr", "")
    assert all(marker not in text for text in entry.diagnostics)


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "unknown",
        "expired",
        "revoked",
        "exhausted",
        "digest",
        "scope",
        "loose",
    ],
)
def test_grant_errors_preserve_code_message_and_no_side_effects(world, case):
    entry, project, packet, request = world
    preview = send(entry, request)["approval"]
    if case == "unknown":
        request["share_grant_id"] = "pmt_0123456789abcdef0123456789abcdef"
    elif case not in ("missing", "scope", "loose"):
        extra = ()
        if case == "expired":
            expiry = datetime.now(UTC) + timedelta(seconds=5)
            extra = ("--expires-at", expiry.isoformat())
        grant = entry.approve(preview, request, "share-grant", extra=extra)["data"]
        request["share_grant_id"] = grant["id"]
        if case == "expired":
            while datetime.now(UTC) < expiry:
                time.sleep(0.05)
        elif case == "revoked":
            entry.cli("permit", "revoke", grant["id"], "--request-key", "revoke", operator=True)
        elif case == "digest":
            request["question"] += " A changed question."
        elif case == "exhausted":
            sent = send(entry, request, code=0)["data"]
            entry.cli("worker", "run", "--once")
            exchange = sent["exchange_id"]
            read_request = {"exchange_id": exchange, "refresh": True}
            read_preview = entry.cli("muse", "read", "--exchange", exchange, "--refresh", code=3)[
                "approval"
            ]
            read_grant = entry.approve(read_preview, read_request, "read-grant")["data"]["id"]
            entry.cli(
                "muse", "read", "--exchange", exchange, "--refresh", "--access-grant", read_grant
            )
            entry.cli("worker", "run", "--once")
    elif case in ("scope", "loose"):
        path = entry.inputs / "scope.json"
        path.write_text(
            json.dumps({"packet_digests": ["0" * 64 if case == "scope" else packet["digest"]]})
        )
        args = [
            "permit",
            "create",
            "--project",
            project,
            "--kind",
            "share",
            "--scope-file",
            path,
            "--provider",
            "fixture",
            "--account",
            "fixture",
            "--expires-at",
            (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "--request-key",
            "share-grant",
        ]
        if case == "scope":
            args.extend(["--exact-digest", preview["action_digest"]])
        request["share_grant_id"] = entry.cli(*args, operator=True)["data"]["id"]
    before = inventory(entry, project)
    result = (
        entry.cli(
            "muse",
            "read",
            "--exchange",
            exchange,
            "--refresh",
            "--access-grant",
            read_grant,
            code=3,
        )
        if case == "exhausted"
        else send(entry, request)
    )
    required = case in ("missing", "unknown")
    message = (
        "A permit is required"
        if case == "missing"
        else "Permit does not exist"
        if case == "unknown"
        else "Muse share grant must bind the exact question, packet and target"
        if case == "loose"
        else "Permit does not cover this action"
    )
    assert result["error"]["code"] == ("E_SCOPE_REQUIRED" if required else "E_SCOPE_DENIED")
    assert result["error"]["message"] == message
    assert (result["approval"] is not None) is required
    if not required:
        assert (
            result["error"]["next_action"]
            == "Repeat the call without a grant to get a fresh approval preview"
        )
    assert inventory(entry, project) == before


@pytest.mark.parametrize("route", ["submit-with-grant", "submit-without-grant", "approve"])
def test_stale_packet_submit_and_preview_are_reference_errors(world, route):
    entry, project, packet, request = world
    preview = send(entry, request)["approval"]
    grant = entry.approve(preview, request, "share-grant")["data"]["id"]
    collect(entry, project, "Synthetic revised review evidence", "revision")
    assert entry.cli("packet", "preview", packet["id"])["data"]["stale"] is True
    before = inventory(entry, project)
    if route == "approve":
        result = entry.approve(preview, request, "stale-grant", code=2)
    else:
        if route == "submit-with-grant":
            request["share_grant_id"] = grant
        result = send(entry, request, code=2)
    assert result["error"]["code"] == "E_REFERENCE_INVALID"
    assert result["error"]["message"] == "Packet is stale; prepare a new packet"
    assert result["approval"] is None
    assert inventory(entry, project) == before


@pytest.mark.parametrize(
    "case,code,message",
    [
        ("disabled", "E_SCOPE_REQUIRED", "Muse is disabled"),
        ("offline", "E_SCOPE_DENIED", "External operations are disabled in offline mode"),
        ("backend", "E_SCOPE_DENIED", "Discussion backend differs from operator configuration"),
        ("secret", "E_AUTH_REQUIRED", "Muse cookie-header binding is unavailable"),
    ],
)
def test_policy_and_auth_errors_have_no_preview(world, monkeypatch, case, code, message):
    entry, project, _, request = world
    monkeypatch.setenv("XDG_CONFIG_HOME", str(entry.root / "operator-config"))
    auth_ref = "auth_0123456789abcdef0123456789abcdef"
    if case == "offline":
        path = entry.inputs / "synthetic-cookie.json"
        path.write_text(
            json.dumps({"format": "cookie-header", "cookie_header": "synthetic=fixture-only"})
        )
        path.chmod(0o600)
        auth_ref = entry.cli(
            "auth",
            "import",
            "--file",
            path,
            "--provider",
            "probe",
            "--account",
            "probe",
            operator=True,
        )["data"]["auth_ref"]
    config = entry.workspace / "metamuse.toml"
    text = config.read_text().replace('backend = "fixture"', 'backend = "live"')
    if case == "disabled":
        text = text.replace("enabled = true", "enabled = false")
    else:
        text += (
            f'\n[muse.binding]\nprovider = "probe"\naccount = "probe"\nauth_ref = "{auth_ref}"\n'
        )
    config.write_text(text)
    before = inventory(entry, project)
    if case == "backend":
        result = send(entry, request)
    else:
        result = entry.cli(
            "muse",
            "open",
            "--project",
            project,
            "--topic",
            "Policy review",
            "--request-key",
            "policy-open",
            code=3,
        )
    assert result["error"]["code"] == code
    assert result["error"]["message"] == message
    assert result["approval"] is None
    assert inventory(entry, project) == before


def test_fresh_preview_command_does_not_replay_exhausted_read_permit(world):
    entry, project, _, request = world
    preview = send(entry, request)["approval"]
    grant = entry.approve(preview, request, "share-grant")["data"]["id"]
    sent = send(entry, {**request, "share_grant_id": grant}, code=0)["data"]
    entry.cli("worker", "run", "--once")
    exchange = sent["exchange_id"]
    keys = set()
    grant_ids = set()
    digests = set()
    for _ in range(2):
        before = inventory(entry, project)
        envelope = entry.cli("muse", "read", "--exchange", exchange, "--refresh", code=3)
        assert envelope["error"]["code"] == "E_SCOPE_REQUIRED"
        preview = envelope["approval"]
        assert inventory(entry, project) == before
        Path(preview["request_file"]).write_text(json.dumps(preview["request"]))
        command = shlex.split(preview["next_action"])
        key = command[command.index("--request-key") + 1]
        assert key not in keys
        keys.add(key)
        digests.add(preview["action_digest"])
        # Execute the displayed operator command with its key unchanged.
        args = command[command.index("permit") :]
        grant = entry.cli(*args, operator=True)["data"]
        assert grant["id"] not in grant_ids and grant["used"] == 0
        grant_ids.add(grant["id"])
        refresh = entry.cli(
            "muse", "read", "--exchange", exchange, "--refresh", "--access-grant", grant["id"]
        )["data"]
        entry.cli("worker", "run", "--once")
        assert (
            entry.cli("jobs", "get", refresh["refresh_job_id"])["data"]["job"]["state"]
            == "succeeded"
        )
        # Retry the old command: receipt replay is still stable, without widening.
        replay = entry.cli(*args, operator=True)["data"]
        assert replay["id"] == grant["id"] and replay["replayed"] is True
        assert replay["expires_at"] == grant["expires_at"] and replay["used"] == 1
    assert len(digests) == 1
    permits = inventory(entry, project)[1]
    assert all(p["used"] == 1 and p["max_uses"] == 1 for p in permits if p["id"] in grant_ids)
