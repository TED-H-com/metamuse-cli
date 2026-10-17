"""OFFLINE/FIXTURE/INTEGRATION: public approval and the connected content workflow."""

import json
import os
import shlex
from pathlib import Path

import anyio
import pytest
from test_facebook import _synthetic_post_html, _synthetic_storage_state_bytes
from test_journey import Entrypoints

URL = "https://www.facebook.com/synth.lab/posts/pfbid0101synth"
SECRET = "SYNTHETIC_FACEBOOK_APPROVAL_PRIVATE_VALUE"


@pytest.fixture
def entry(tmp_path, monkeypatch):
    hook = tmp_path / "hook"
    hook.mkdir()
    page = tmp_path / "post.html"
    page.write_text(
        _synthetic_post_html(
            post_text="測試活動的原始公告。",
            comments=[{"id": "comment1", "text": "測試活動的留言。", "parent_id": None}],
        )
    )
    events = tmp_path / "events.jsonl"
    (hook / "sitecustomize.py").write_text("""import json, os, socket
from pathlib import Path
from metamuse import facebook, jobs
def record(event, **data):
    with Path(os.environ["FB_EVENTS"]).open("a") as handle:
        handle.write(json.dumps({"event": event, **data}) + "\\n")
def forbidden(*args, **kwargs):
    raise AssertionError("Real network attempted")
socket.getaddrinfo = forbidden
socket.socket.connect = forbidden
facebook.set_resolver_factory(lambda: lambda host: ["93.184.216.34"])
class Driver(facebook.FixtureBrowserDriver):
    def open_session(self, *args, **kwargs):
        record("browser")
        return super().open_session(*args, **kwargs)
def driver():
    return Driver(facebook.PageObservation(
        url=os.environ["FB_URL"], status_code=200,
        html=Path(os.environ["FB_HTML"]).read_text()))
facebook.set_driver_factory(driver)
class Page:
    backend = "fixture"
    def identity(self):
        return "123456"
    def send(self, page, message):
        record("post", page=page, message=message)
        return page + "_98765"
    def read(self, remote_id):
        sent = [json.loads(line) for line in Path(os.environ["FB_EVENTS"]).read_text().splitlines()
                if json.loads(line)["event"] == "post"][-1]
        return {"id": remote_id, "from": {"id": sent["page"]},
                "message": sent["message"], "is_published": True}
original_init = jobs.Executor.__init__
def init(self, *args, **kwargs):
    kwargs["publishing_gateway"] = Page()
    original_init(self, *args, **kwargs)
jobs.Executor.__init__ = init
""")
    env = {
        "PYTHONPATH": str(hook) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "FB_EVENTS": str(events),
        "FB_URL": URL,
        "FB_HTML": str(page),
        "UV_OFFLINE": "1",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    result = Entrypoints(tmp_path)
    result.env, result.events = env, events
    result.configure()
    result.project = result.cli(
        "project", "create", "--name", "內容流程測試", "--request-key", "project"
    )["data"]["id"]
    auth = result.inputs / "facebook-state.json"
    auth.write_bytes(_synthetic_storage_state_bytes(secret_cookie=SECRET))
    auth.chmod(0o600)
    result.cli(
        "connect",
        "facebook",
        "--account",
        "synthetic-reader",
        "--auth-file",
        auth,
        "--enable-network",
        operator=True,
    )
    return result


def request(entry, **overrides):
    return {
        "project_id": entry.project,
        "inputs": [{"kind": "url", "url": URL}],
        "resources": ["post", "comments"],
        "limits": {
            "max_urls": 10,
            "max_items": 500,
            "max_bytes": 1073741824,
            "wall_clock_seconds": 900,
        },
        "access_grant_id": None,
        "request_key": "collect",
        **overrides,
    }


def collect_cli(entry, body, *, run=False, code=3):
    args = [
        "add",
        "--project",
        body["project_id"],
        "--url",
        body["inputs"][0]["url"],
        "--resources",
        ",".join(body["resources"]),
        "--request-key",
        body["request_key"],
    ]
    if body.get("access_grant_id"):
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


def no_work(entry):
    assert entry.cli("jobs", "list")["data"]["jobs"] == []
    assert (
        entry.cli("permit", "list", "--project", entry.project, operator=True)["data"]["permits"]
        == []
    )
    assert not entry.events.exists()


@pytest.mark.parametrize("channel", ["cli", "mcp"])
@pytest.mark.parametrize("grant", [None, "prm_" + "a" * 32])
def test_missing_permit_returns_exact_preview_without_io_or_job(entry, channel, grant):
    body = request(entry, access_grant_id=grant)
    result = collect(entry, body, channel)
    assert result["error"]["code"] == "E_SCOPE_REQUIRED" and result["data"] is None
    preview = result["approval"]
    assert preview["operation"] == preview["permit_operation"] == "collect.remote"
    assert preview["provider"] == "facebook" and preview["account"] == "synthetic-reader"
    assert preview["destination"] == URL and preview["request"]["access_grant_id"] is None
    assert preview["kind"] == "access" and len(preview["action_digest"]) == 64
    assert not Path(preview["request_file"]).exists()
    assert "max_expansions=10" in preview["summary"]
    assert "does not share data with Muse or publish" in preview["summary"]
    assert SECRET not in json.dumps(result)
    no_work(entry)


@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_human_approval_reads_and_preserves_source(entry, channel):
    body = request(entry)
    preview = collect(entry, body, channel)["approval"]
    grant = entry.approve(preview, preview["request"], "approve-read")["data"]["id"]
    accepted = collect_cli(entry, {**body, "access_grant_id": grant}, run=True, code=0)
    job = entry.cli("jobs", "get", accepted["data"]["job_id"])["data"]["job"]
    assert job["state"] == "succeeded"
    hits = entry.cli("search", "--project", entry.project, "--query", "測試活動")["data"]["results"]
    assert len(hits) == 2
    assert len(entry.events.read_text().splitlines()) == 1
    for raw in [json.dumps(hits), *entry.diagnostics]:
        assert SECRET not in raw


@pytest.mark.parametrize("field", ["url", "resources"])
def test_request_edit_cannot_reuse_review_digest(entry, field):
    preview = collect(entry, request(entry))["approval"]
    changed = json.loads(json.dumps(preview["request"]))
    if field == "url":
        changed["inputs"][0]["url"] = URL.replace("0101", "0202")
    else:
        changed["resources"] = ["post"]
    refused = entry.approve(preview, changed, "tamper", code=3)
    assert refused["error"]["code"] == "E_SCOPE_DENIED"
    no_work(entry)


@pytest.mark.parametrize(
    "case,code",
    [("disabled", 3), ("offline", 3), ("no-auth", 3), ("profile", 4), ("secret-url", 2)],
)
def test_configuration_or_input_refusal_has_no_preview(entry, case, code):
    body = request(entry)
    path = entry.workspace / "metamuse.toml"
    text = path.read_text()
    if case == "disabled":
        text = text.replace("[facebook]\nenabled = true", "[facebook]\nenabled = false")
    elif case == "offline":
        text = text.replace('network_mode = "live"', 'network_mode = "offline"')
    elif case == "no-auth":
        text = "\n".join(line for line in text.splitlines() if not line.startswith("auth_ref ="))
    elif case == "profile":
        body["inputs"][0]["url"] = "https://www.facebook.com/synth.lab"
    else:
        body["inputs"][0]["url"] += "#access_token=" + SECRET
    path.write_text(text)
    result = collect(entry, body, code=code)
    assert result["approval"] is None
    assert SECRET not in json.dumps(result)
    no_work(entry)


def test_expired_or_revoked_grant_requires_fresh_approval_key(entry):
    body = request(entry)
    preview = collect(entry, body)["approval"]
    grant = entry.approve(preview, preview["request"], shlex.split(preview["next_action"])[-1])[
        "data"
    ]["id"]
    entry.cli("permit", "revoke", grant, "--request-key", "revoke", operator=True)
    refused = collect(entry, {**body, "access_grant_id": grant})
    assert refused["approval"] is None and refused["error"]["code"] == "E_SCOPE_DENIED"
    fresh = collect(entry, body)["approval"]
    assert fresh["action_digest"] == preview["action_digest"]
    assert fresh["next_action"] != preview["next_action"]
