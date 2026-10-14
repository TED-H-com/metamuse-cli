"""OFFLINE/FIXTURE regressions from the independent B12 rounds 5/6 review."""

import json
from urllib.parse import parse_qs, urlsplit

import anyio
import pytest
from test_remote_approval import ACCOUNT, PROVIDER, approval_key, collect, collect_cli, request
from test_remote_approval import entry as remote_entry

entry = remote_entry


def import_cookies(entry, *, account=ACCOUNT, content=None):
    marker = f"SYNTHETIC_{'HOP'}_COOKIE_VALUE"
    path = entry.inputs / "cookies.txt"
    path.write_text(content or f".facebook.com\tTRUE\t/\tTRUE\t0\tsid\t{marker}\n")
    path.chmod(0o600)
    metadata = entry.cli(
        "auth",
        "import",
        "--file",
        path,
        "--provider",
        PROVIDER,
        "--account",
        account,
        operator=True,
    )["data"]
    return metadata["auth_ref"], marker


def add_hop(entry, auth_ref):
    path = entry.workspace / "metamuse.toml"
    path.write_text(
        path.read_text()
        + (
            '\n[[remote_media.approvals]]\nextractor = "Facebook"\n'
            'hosts = ["www.facebook.com", "m.facebook.com"]\n'
            f'auth_ref = "{auth_ref}"\n'
        )
    )
    entry.env["REMOTE_TEST_HOP"] = "1"


def permit(entry, grant):
    permits = entry.cli("permit", "list", "--project", entry.project, operator=True)["data"][
        "permits"
    ]
    return next(row for row in permits if row["id"] == grant)


@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_review_hop_approval_and_cookie_are_shown_and_bound(entry, monkeypatch, channel):
    ref, marker = import_cookies(entry)
    add_hop(entry, ref)
    monkeypatch.setenv("REMOTE_TEST_HOP", "1")
    body = request(entry, url="https://www.facebook.com/reel/123456789")
    preview = collect(entry, body, channel)["approval"]
    assert "Facebook/m.facebook.com, www.facebook.com (may serve later hops)" in preview["summary"]
    assert (
        "FacebookReel/www.facebook.com (first hop; may also serve later hops)" in preview["summary"]
    )
    assert f"Cookie binding: {ref}; account: {ACCOUNT}" in preview["summary"]
    assert "unrelated approval requires a fresh preview" in preview["summary"]
    assert marker not in json.dumps(preview) and "sid" not in preview["summary"]
    grant = entry.approve(preview, preview["request"], approval_key(preview))["data"]["id"]
    accepted = collect_cli(entry, {**body, "access_grant_id": grant}, code=0)
    entry.cli("worker", "run", "--once")
    job = entry.cli("jobs", "get", accepted["data"]["job_id"])["data"]
    assert job["job"]["state"] == "succeeded", job
    entries = job["steps"][0]["target"]["approvals"]
    assert entries == sorted(
        entries, key=lambda value: (value["extractor"], value["hosts"], value["auth_ref"] or "")
    )
    assert next(value for value in entries if value["extractor"] == "Facebook") == {
        "extractor": "Facebook",
        "hosts": ["m.facebook.com", "www.facebook.com"],
        "auth_ref": ref,
    }
    dials = [json.loads(line) for line in entry.egress.read_text().splitlines()]
    cookies = [row for row in dials if row.get("cookie")]
    assert len(cookies) == 1 and marker in cookies[0]["cookie"]
    destination = urlsplit(cookies[0]["url"])
    assert destination.hostname in {"m.facebook.com", "www.facebook.com"}
    assert destination.path == "/watch/" and parse_qs(destination.query)["v"] == ["123456789"]
    assert permit(entry, grant)["used"] == 1


def submit(entry, body, channel="cli"):
    if channel == "cli":
        return collect_cli(entry, body, code=0)

    async def scenario():
        async with entry.server("--no-worker", env=entry.env) as session:
            return await entry.call(session, "collect", body)

    return anyio.run(scenario)


@pytest.mark.parametrize("change", ["auth_ref", "host", "unrelated"])
@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_review_approval_edits_stop_before_consumption_and_egress(entry, change, channel):
    body = request(entry)
    preview = collect(entry, body, channel)["approval"]
    grant = entry.approve(preview, preview["request"], approval_key(preview))["data"]["id"]
    accepted = submit(entry, {**body, "access_grant_id": grant}, channel)
    path = entry.workspace / "metamuse.toml"
    if change == "auth_ref":
        ref, _marker = import_cookies(
            entry, content=f"media.fixture.test\tFALSE\t/\tTRUE\t0\tsid\t{'synthetic'}\n"
        )
        path.write_text(
            path.read_text().replace(
                'hosts = ["media.fixture.test"]',
                f'hosts = ["media.fixture.test"]\nauth_ref = "{ref}"',
            )
        )
    elif change == "host":
        path.write_text(
            path.read_text().replace(
                'hosts = ["media.fixture.test"]',
                'hosts = ["media.fixture.test", "flip.fixture.test"]',
            )
        )
    else:
        path.write_text(
            path.read_text().replace(
                'hosts = ["www.facebook.com"]',
                'hosts = ["www.facebook.com", "m.facebook.com"]',
            )
        )
    entry.cli("worker", "run", "--once")
    data = entry.cli("jobs", "get", accepted["data"]["job_id"])["data"]
    assert data["job"]["state"] == "awaiting_user", data
    assert data["job"]["error"]["code"] == "E_SCOPE_DENIED"
    assert data["job"]["error"]["next_action"] == (
        "Repeat the call without a grant to get a fresh approval preview"
    )
    assert permit(entry, grant)["used"] == 0
    assert not entry.egress.exists()
    fresh = collect(entry, request(entry, key="fresh"), channel)["approval"]
    assert fresh["action_digest"] != preview["action_digest"]
