"""OFFLINE/INTEGRATION: canonical review through CLI and the official MCP client."""

import json

import pytest
from test_remote_approval import approval_key, collect, configure, no_created_objects, request
from test_remote_approval import entry as remote_entry
from test_remote_media import facebook_page
from test_remote_r8 import facebook
from test_remote_review import submit

entry = remote_entry


@pytest.mark.parametrize("channel", ["cli", "mcp"])
@pytest.mark.parametrize(
    "suffix",
    ["/page/videos/111/../222", "/page/videos/111/%2e%2e/222", "/watch/?v=111&%76=222"],
)
def test_r9_facebook_rewritten_selectors_refused_before_preview(entry, channel, suffix):
    facebook(entry, cookie=True)
    result = collect(
        entry, request(entry, url="https://www.facebook.com" + suffix), channel, code=2
    )
    assert result["error"]["code"] == "E_INPUT_INVALID"
    assert result["error"]["message"] == "Remote URL cannot be reviewed safely"
    assert result["approval"] is None
    no_created_objects(entry)


@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_r9_mobile_facebook_reviews_and_extracts_www(entry, channel, monkeypatch):
    facebook(entry, cookie=True)
    html = entry.inputs / "facebook.html"
    html.write_bytes(facebook_page("111"))
    for key, value in {
        "REMOTE_TEST_FACEBOOK_PAGE": str(html),
        "REMOTE_TEST_RECORD_EXTRACT": "1",
    }.items():
        monkeypatch.setenv(key, value)
        entry.env[key] = value
    original = "https://m.facebook.com/watch/?v=111"
    expected = "https://www.facebook.com/watch/?v=111"
    body = request(entry, url=original)
    shown = collect(entry, body, channel)["approval"]
    assert shown["destination"] == shown["request"]["inputs"][0]["url"] == expected
    no_created_objects(entry)
    grant = entry.approve(shown, body, approval_key(shown))["data"]["id"]
    assert "Destination: " + expected in entry.diagnostics[-1]
    accepted = submit(entry, {**body, "access_grant_id": grant}, channel)
    entry.cli("worker", "run", "--once")
    job = entry.cli("jobs", "get", accepted["data"]["job_id"])["data"]
    assert job["job"]["state"] == "succeeded", job
    assert job["job"]["payload"]["inputs"][0]["url"] == original
    rows = [json.loads(line) for line in entry.egress.read_text().splitlines()]
    assert [row["url"] for row in rows if row.get("event") == "extract"] == [expected]
    assert [row["url"] for row in rows if row.get("event") == "dial"] == [expected]
    assert all(row["cookie"] for row in rows if row.get("event") == "dial")


@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_r9_mobile_only_approval_refused_at_preview(entry, channel):
    configure(entry)
    path = entry.workspace / "metamuse.toml"
    path.write_text(
        path.read_text()
        + '\n[[remote_media.approvals]]\nextractor = "Facebook"\nhosts = ["m.facebook.com"]\n'
    )
    result = collect(entry, request(entry, url="https://m.facebook.com/watch/?v=111"), channel)
    assert result["error"]["code"] == "E_SCOPE_DENIED"
    assert result["approval"] is None
    no_created_objects(entry)
