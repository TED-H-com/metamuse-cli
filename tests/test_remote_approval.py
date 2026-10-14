"""OFFLINE/INTEGRATION/FIXTURE: shipped CLI and official MCP approval workflow."""

import json
import os
import shlex
from pathlib import Path

import anyio
import pytest
from test_journey import Entrypoints

PROVIDER = "fixture-provider"
ACCOUNT = "fixture-account"
URL = "https://media.fixture.test/one"
CLOCK = "2030-01-01T00:00:00+00:00"


@pytest.fixture
def entry(tmp_path, monkeypatch):
    hook = tmp_path / "hook"
    hook.mkdir()
    egress = tmp_path / "egress.jsonl"
    # Only the existing extractor, clock and egress seams are replaced. The command,
    # approval registry, permits, receipts, submission and worker remain production code.
    (hook / "sitecustomize.py").write_text(
        """import json, os, socket
from pathlib import Path
from urllib.parse import urlsplit
from metamuse import app, remote_media, safety
original_open = app.AppContext.open
app.AppContext.open = classmethod(lambda cls, workspace=None, **kw:
    original_open(workspace, clock=lambda: os.environ['REMOTE_TEST_CLOCK']))
def fixture_class():
    from yt_dlp.extractor.common import InfoExtractor
    class MetamuseFixtureIE(InfoExtractor):
        _VALID_URL = r'https://media\\.fixture\\.test/(?P<id>[a-z]+)'
        def _real_extract(self, url):
            return self._download_json(url, self._match_id(url), note=False)
    return MetamuseFixtureIE
original_class = remote_media._extractor_class
def extractor(key):
    if key == 'MetamuseFixture':
        return fixture_class()
    if key == 'Facebook' and os.environ.get('REMOTE_TEST_HOP'):
        from yt_dlp.extractor.common import InfoExtractor
        class FacebookIE(InfoExtractor):
            _VALID_URL = r'https://(?:www|m)\\.facebook\\.com/watch/\\?v=(?P<id>\\d+)'
            def _real_extract(self, url):
                return self._download_json(url, self._match_id(url), note=False)
        return FacebookIE
    return original_class(key)
remote_media._extractor_class = extractor
if os.environ.get('REMOTE_TEST_MISSING_EXTRA'):
    remote_media.dependency_available = lambda: False
def forbidden(*args, **kwargs):
    raise AssertionError('Real network attempted')
socket.getaddrinfo = forbidden
socket.gethostbyname = forbidden
def factory(hosts, resolver):
    def check(url, *, purpose):
        with Path(os.environ['REMOTE_TEST_EGRESS']).open('a') as handle:
            handle.write(json.dumps({'url': url, 'purpose': purpose}) + '\\n')
        return safety.check_fixture_url(url, purpose=purpose,
            resolver=lambda host: ['127.0.0.1'], allowed_hosts=hosts)
    return check
remote_media._checker_factory = factory
def dial(method, url, headers, body, checked, timeout, limit):
    with Path(os.environ['REMOTE_TEST_EGRESS']).open('a') as handle:
        handle.write(json.dumps({'event': 'dial', 'url': url,
            'cookie': headers.get('Cookie'), 'limit': limit}) + '\\n')
    payload = {'id': urlsplit(url).path.strip('/'), 'title': 'Synthetic media',
        'webpage_url': url, 'formats': [{'format_id': 'fixture', 'ext': 'mp4',
        'url': 'https://media.fixture.test/media.mp4'}]}
    return (200, {'Content-Type': 'application/json'},
        json.dumps(payload).encode(), '127.0.0.1', 'OK')
remote_media._dial = dial
"""
    )
    env = {
        "PYTHONPATH": str(hook) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "REMOTE_TEST_EGRESS": str(egress),
        "REMOTE_TEST_CLOCK": CLOCK,
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "UV_OFFLINE": "1",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    result = Entrypoints(tmp_path)
    result.cli_timeout = 180  # Disk contention can delay cold imports on the shared host.
    result.env = env
    result.egress = egress
    result.cli("init")
    result.project = result.cli(
        "project", "create", "--name", "Remote approval", "--request-key", "project"
    )["data"]["id"]
    configure(result)
    return result


def configure(entry, *, live=True, enabled=True, auth_ref=None):
    path = entry.workspace / "metamuse.toml"
    text = path.read_text().split("[remote_media]")[0]
    text = text.replace('network_mode = "offline"', 'network_mode = "live"')
    if not live:
        text = text.replace('network_mode = "live"', 'network_mode = "offline"')
    text += f'[remote_media]\nprovider = "{PROVIDER}"\naccount = "{ACCOUNT}"\n'
    if enabled:
        text += '[[remote_media.approvals]]\nextractor = "MetamuseFixture"\n'
        text += 'hosts = ["media.fixture.test"]\n'
        if auth_ref:
            text += f'auth_ref = "{auth_ref}"\n'
        text += '[[remote_media.approvals]]\nextractor = "FacebookReel"\n'
        text += 'hosts = ["www.facebook.com"]\n'
    path.write_text(text)


def request(entry, *, url=URL, key="collect", grant=None):
    return {
        "project_id": entry.project,
        "inputs": [{"kind": "url", "url": url}],
        "resources": ["media_metadata"],
        "limits": {
            "max_urls": 10,
            "max_items": 500,
            "max_bytes": 1073741824,
            "wall_clock_seconds": 900,
        },
        "access_grant_id": grant,
        "request_key": key,
        "attach_item_id": None,
        "attach_asset_id": None,
        "language": None,
    }


def collect_cli(entry, body, *, code=3, run=False):
    args = [
        "add",
        "--project",
        entry.project,
        "--url",
        body["inputs"][0]["url"],
        "--resources",
        ",".join(body["resources"]),
        "--request-key",
        body["request_key"],
    ]
    if body["access_grant_id"]:
        args += ["--access-grant", body["access_grant_id"]]
    if run:
        args += ["--run"]
    return entry.cli(*args, code=code)


def collect(entry, body, channel="cli", *, code=3):
    if channel == "cli":
        return collect_cli(entry, body, code=code)

    async def scenario():
        async with entry.server("--no-worker", env=entry.env) as session:
            return await entry.call(session, "collect", body, error=True)

    return anyio.run(scenario)


def approval_key(preview):
    command = shlex.split(preview["next_action"])
    return command[command.index("--request-key") + 1]


def no_created_objects(entry):
    assert entry.cli("jobs", "list", "--project", entry.project)["data"]["jobs"] == []
    assert (
        entry.cli("permit", "list", "--project", entry.project, operator=True)["data"]["permits"]
        == []
    )
    assert not entry.egress.exists()


def assert_preview(entry, envelope, body, *, missing=False):
    assert envelope["data"] is None
    assert envelope["error"]["code"] == "E_SCOPE_REQUIRED"
    assert envelope["error"]["message"] == (
        "Permit does not exist" if missing else "A permit is required"
    )
    preview = envelope["approval"]
    assert preview["operation"] == preview["permit_operation"] == "collect.remote"
    assert preview["kind"] == "access" and preview["project_id"] == entry.project
    assert preview["provider"] == PROVIDER and preview["account"] == ACCOUNT
    assert preview["destination"] == body["inputs"][0]["url"]
    assert len(preview["action_digest"]) == 64
    assert preview["request"] == {**body, "access_grant_id": None}
    assert not Path(preview["request_file"]).exists()
    assert Path(preview["request_file"]).parent == entry.workspace
    command = shlex.split(preview["next_action"])
    assert command[command.index("permit") :][:2] == ["permit", "approve"]
    assert command[command.index("--operation") + 1] == "collect.remote"
    assert command[command.index("--exact-digest") + 1] == preview["action_digest"]
    assert command[command.index("--request-file") + 1] == preview["request_file"]
    assert envelope["error"]["next_action"] == preview["next_action"]
    for name, value in body["limits"].items():
        assert f"{name}={value}" in preview["summary"]
    for field in ("safe_read_retries=2", "read_timeout_seconds=30", "media_max_bytes=536870912"):
        assert field in preview["summary"]
    assert "Stores: source provenance and media metadata" in preview["summary"]
    return preview


@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_remote_no_grant_preview_creates_nothing_and_no_receipt(entry, channel):
    body = request(entry)
    preview = assert_preview(entry, collect(entry, body, channel), body)
    no_created_objects(entry)
    # Public request-key reuse with changed bytes proves a preview wrote no collect receipt.
    changed = request(entry, url=URL.replace("one", "two"))
    other = assert_preview(entry, collect(entry, changed, channel), changed)
    assert preview["action_digest"] != other["action_digest"]
    no_created_objects(entry)
    grant = entry.approve(other, other["request"], "approve")["data"]["id"]
    accepted = collect_cli(entry, {**changed, "access_grant_id": grant}, code=0)
    assert accepted["status"] == "accepted" and accepted["data"]["state"] == "queued"


@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_remote_approve_repeat_runs_fake_extractor(entry, channel):
    body = request(entry)
    preview = assert_preview(entry, collect(entry, body, channel), body)
    no_created_objects(entry)
    grant = entry.approve(preview, preview["request"], approval_key(preview))["data"]
    assert grant["exact_digest"] == preview["action_digest"]
    repeated = {**body, "access_grant_id": grant["id"]}
    if channel == "cli":
        result = collect_cli(entry, repeated, code=0, run=True)
        job = entry.cli("jobs", "get", result["data"]["job_id"])["data"]
    else:

        async def scenario():
            async with entry.server(env=entry.env) as session:
                accepted = await entry.call(session, "collect", repeated)
                assert accepted["status"] == "accepted"
                return await entry.terminal(session, accepted["data"]["job_id"])

        job = anyio.run(scenario)
    assert job["job"]["state"] == "succeeded"
    assert job["steps"][0]["result"]["observed_scope"]["rows"] == 1
    assert job["steps"][0]["target"]["provider"] == PROVIDER
    assert entry.egress.exists()
    permits = entry.cli("permit", "list", "--project", entry.project, operator=True)["data"][
        "permits"
    ]
    assert permits[0]["used"] == 1


@pytest.mark.parametrize("field", ["url", "limits", "resources"])
def test_remote_edited_request_file_rejects_exact_digest(entry, field):
    body = request(entry)
    preview = collect(entry, body)["approval"]
    if field == "url":
        body["inputs"][0]["url"] = URL.replace("one", "two")
    elif field == "limits":
        body["limits"]["max_bytes"] -= 1
    else:
        body["resources"] = ["subtitles"]
    refused = entry.approve(preview, body, "tampered", code=3)
    assert refused["error"]["code"] == "E_SCOPE_DENIED"
    assert refused["error"]["message"] == "Approval digest differs from this request"
    no_created_objects(entry)
    # The failed approval wrote no permit receipt either: same key, original bytes succeeds.
    grant = entry.approve(preview, preview["request"], "tampered")["data"]
    assert grant["replayed"] is False


@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_remote_unknown_permit_preserves_message_with_preview(entry, channel):
    body = request(entry, grant="prm_" + "a" * 32)
    assert_preview(entry, collect(entry, body, channel), body, missing=True)
    no_created_objects(entry)


@pytest.mark.parametrize(
    "denial", ["expired", "revoked", "exhausted", "digest", "scope", "account"]
)
@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_remote_grant_denials_have_no_preview(entry, monkeypatch, denial, channel):
    body = request(entry)
    preview = collect(entry, body)["approval"]
    if denial == "scope":
        scope = entry.inputs / "scope.json"
        scope.write_text(
            json.dumps({"operations": ["muse.open"], "target_digests": [preview["action_digest"]]})
        )
        grant = entry.cli(
            "permit",
            "create",
            "--project",
            entry.project,
            "--kind",
            "access",
            "--scope-file",
            scope,
            "--provider",
            PROVIDER,
            "--account",
            ACCOUNT,
            "--expires-at",
            "2030-01-02T00:00:00+00:00",
            "--max-uses",
            "1",
            "--request-key",
            "wrong-scope",
            operator=True,
        )["data"]["id"]
    else:
        grant = entry.approve(preview, preview["request"], approval_key(preview))["data"]["id"]
    if denial == "expired":
        entry.env["REMOTE_TEST_CLOCK"] = "2030-01-03T00:00:00+00:00"
        monkeypatch.setenv("REMOTE_TEST_CLOCK", entry.env["REMOTE_TEST_CLOCK"])
    elif denial == "revoked":
        entry.cli("permit", "revoke", grant, "--request-key", "revoke", operator=True)
    elif denial == "exhausted":
        collect_cli(entry, {**body, "access_grant_id": grant}, code=0, run=True)
    elif denial == "digest":
        body = request(entry, url=URL.replace("one", "two"))
    elif denial == "account":
        path = entry.workspace / "metamuse.toml"
        path.write_text(
            path.read_text().replace(f'account = "{ACCOUNT}"', 'account = "different-account"')
        )
    denied = collect(entry, {**body, "access_grant_id": grant, "request_key": "denied"}, channel)
    assert denied["error"]["code"] == "E_SCOPE_DENIED"
    assert denied["error"]["message"] == "Permit does not cover this action"
    assert denied["approval"] is None
    assert "without a grant" in denied["error"]["next_action"]
    fresh = collect(entry, {**body, "request_key": "fresh"}, channel)["approval"]
    if denial in ("expired", "revoked", "exhausted"):
        assert fresh["action_digest"] == preview["action_digest"]
        assert fresh["next_action"] != preview["next_action"]


@pytest.mark.parametrize(
    "case,code,message",
    [
        ("offline", "E_SCOPE_DENIED", "External operations are disabled in offline mode"),
        ("disabled", "E_SCOPE_REQUIRED", "No remote extractor is approved"),
        ("extra", "E_DEPENDENCY_MISSING", "yt-dlp is not installed"),
        ("invalid", "E_INPUT_INVALID", "Remote URL is not a bare http(s) URL"),
        ("port", "E_INPUT_INVALID", "Remote URL is not a bare http(s) URL"),
        ("whitespace", "E_INPUT_INVALID", "Remote URL is not a bare http(s) URL"),
        ("unsupported", "E_UNSUPPORTED", "A /share/p/ link is a post"),
    ],
)
@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_remote_preparation_errors_have_no_preview(
    entry, monkeypatch, case, code, message, channel
):
    body = request(entry)
    if case in ("offline", "disabled"):
        configure(entry, live=case != "offline", enabled=case != "disabled")
    elif case == "extra":
        monkeypatch.setenv("REMOTE_TEST_MISSING_EXTRA", "1")
        entry.env["REMOTE_TEST_MISSING_EXTRA"] = "1"
    elif case in ("invalid", "port", "whitespace"):
        body = request(
            entry,
            url={
                "invalid": "https://",
                "port": "https://media.fixture.test:bad/one",
                "whitespace": URL + "?note=invalid space",
            }[case],
        )
    else:
        body = request(entry, url="https://www.facebook.com/share/p/synthetic/")
    exit_code = (
        4
        if case in ("extra", "unsupported")
        else 2
        if case in ("invalid", "port", "whitespace")
        else 3
    )
    failed = collect(entry, body, channel, code=exit_code)
    assert failed["error"]["code"] == code
    assert failed["error"]["message"] == message
    assert failed["approval"] is None
    no_created_objects(entry)


@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_remote_share_preview_never_calls_egress(entry, channel):
    body = request(entry, url="https://www.facebook.com/share/r/synthetic/")
    preview = assert_preview(entry, collect(entry, body, channel), body)
    assert "checked redirects" in preview["summary"]
    assert "two approved extractor hops" in preview["summary"]
    assert "original share URL" in preview["summary"]
    assert "FacebookReel" in preview["summary"]
    no_created_objects(entry)


@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_remote_cookie_binding_and_nested_request_fields_are_redacted(entry, channel):
    cookie_marker, url_marker = (
        f"SYNTHETIC_{label}_PRIVATE_VALUE" for label in ("COOKIE", "QUERY")
    )
    secret = entry.inputs / "cookies.txt"
    secret.write_text(
        "# Netscape HTTP Cookie File\n"
        f"media.fixture.test\tFALSE\t/\tTRUE\t0\tsession\t{cookie_marker}\n"
    )
    secret.chmod(0o600)
    binding = entry.cli(
        "auth",
        "import",
        "--file",
        secret,
        "--provider",
        PROVIDER,
        "--account",
        ACCOUNT,
        operator=True,
    )["data"]
    configure(entry, auth_ref=binding["auth_ref"])
    body = request(entry, url=URL + "?token=" + url_marker)
    envelope = collect(entry, body, channel)
    preview = envelope["approval"]
    assert preview["account"] == ACCOUNT
    assert binding["auth_ref"] in preview["summary"]
    assert "Cookie binding:" in preview["summary"]
    assert "[REDACTED]" in preview["request"]["inputs"][0]["url"]
    assert preview["request"]["inputs"][0]["url"] == preview["destination"]
    path = Path(preview["request_file"])
    assert not path.exists()
    path.write_text(json.dumps(preview["request"]))
    for raw in [
        json.dumps(envelope),
        path.read_text(),
        *entry.diagnostics,
        *[json.dumps(row["envelope"]) for row in entry.transcript],
    ]:
        assert cookie_marker not in raw and url_marker not in raw
    # Redaction changes displayed bytes, so the original digest cannot approve the safe copy.
    refused = entry.approve(preview, preview["request"], "redacted", code=3)
    assert refused["error"]["message"] == "Approval digest differs from this request"
    no_created_objects(entry)
    for raw in [path.read_text(), *entry.diagnostics, json.dumps(refused)]:
        assert cookie_marker not in raw and url_marker not in raw


@pytest.mark.parametrize("change", ["account", "allowlist"])
def test_remote_step_boundary_rechecks_operator_configuration(entry, change):
    body = request(entry)
    preview = collect(entry, body)["approval"]
    grant = entry.approve(preview, preview["request"], approval_key(preview))["data"]["id"]
    accepted = collect_cli(entry, {**body, "access_grant_id": grant}, code=0)
    assert accepted["status"] == "accepted"
    path = entry.workspace / "metamuse.toml"
    if change == "account":
        path.write_text(path.read_text().replace(f'account = "{ACCOUNT}"', 'account = "changed"'))
    else:
        path.write_text(
            path.read_text().replace(
                'hosts = ["media.fixture.test"]', 'hosts = ["other.fixture.test"]'
            )
        )
    entry.cli("worker", "run", "--once")
    job = entry.cli("jobs", "get", accepted["data"]["job_id"])["data"]["job"]
    assert job["state"] == "awaiting_user" and job["error"]["code"] == "E_SCOPE_DENIED"
    assert not entry.egress.exists()
    permit = entry.cli("permit", "list", "--project", entry.project, operator=True)["data"][
        "permits"
    ][0]
    assert permit["used"] == 0
