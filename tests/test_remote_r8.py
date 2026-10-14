"""OFFLINE/FIXTURE regressions from the independent round-7 review."""

import json
from pathlib import Path

import pytest
from test_remote_approval import approval_key, collect, configure, no_created_objects, request
from test_remote_approval import entry as remote_entry
from test_remote_media import facebook_page
from test_remote_review import import_cookies, permit, submit
from yt_dlp.extractor.facebook import FacebookIE

entry = remote_entry


def facebook(entry, *, cookie=False):
    ref = import_cookies(entry)[0] if cookie else None
    path = entry.workspace / "metamuse.toml"
    path.write_text(
        path.read_text()
        + '\n[[remote_media.approvals]]\nextractor = "Facebook"\n'
        + 'hosts = ["www.facebook.com"]\n'
        + (f'auth_ref = "{ref}"\n' if ref else "")
    )


UNREVIEWABLE = [
    "https://www.facebook.com/watch/?v=111#!/watch/?v=222",  # E1, V3
    "https://www.facebook.com/watch/?sv=999&v=111",  # entry3, V0/V1/V3
    "https://www.facebook.com/watch/?sig=v=999&v=111",
    "https://www.facebook.com/watch/?oh=video_id=999&v=111",
    "https://www.facebook.com/story.php?token=xstory_fbid=999&story_fbid=111&id=5",
    "https://www.facebook.com/watch/?v=111&sv=999",
    "https://www.facebook.com/#!/watch/?v=222",  # D3
    "https://www.facebook.com/watch/?v=111#",
    "https://media.fixture.test/one?TOKEN=synthetic",
    "https://media.fixture.test/one?t%6fken=synthetic",
    "https://media.fixture.test/one?x-amz-signature=synthetic",
    "https://media.fixture.test/one?token",  # a secret key without '='
]


@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_r8_canonical_share_preview_explains_checked_resolution(entry, channel):
    original = "https://WWW.facebook.com:0443/share/r/synthetic/"
    preview = collect(entry, request(entry, url=original), channel)["approval"]
    assert preview["destination"] == "https://www.facebook.com/share/r/synthetic/"
    assert "checked redirects" in preview["summary"]
    assert "canonical share URL" in preview["summary"]
    no_created_objects(entry)


@pytest.mark.parametrize("url", UNREVIEWABLE)
@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_r8_hidden_facebook_selector_refused_before_preview(entry, channel, url):
    facebook(entry, cookie=True)
    # Real FacebookIE recognizes the dangerous selectors; no fake Facebook class.
    if url.startswith("https://www.facebook.com/watch/") and "v=" in url:
        assert FacebookIE.suitable(url)
    envelope = collect(entry, request(entry, url=url), channel, code=2)
    assert envelope["error"]["code"] == "E_INPUT_INVALID"
    assert envelope["error"]["message"] == "Remote URL cannot be reviewed safely"
    assert envelope["approval"] is None
    no_created_objects(entry)


@pytest.mark.parametrize("channel", ["cli", "mcp"])
@pytest.mark.parametrize("character", ["\u3164", "\u2800", "\u034f"])
def test_r8_canonical_destination_is_real_facebook_first_hop(
    entry, monkeypatch, channel, character
):
    facebook(entry)
    html = entry.inputs / "facebook.html"
    html.write_bytes(facebook_page("111"))
    for key, value in {
        "REMOTE_TEST_FACEBOOK_PAGE": str(html),
        "REMOTE_TEST_RECORD_EXTRACT": "1",
    }.items():
        monkeypatch.setenv(key, value)
        entry.env[key] = value
    original = "https://WWW.FACEBOOK.COM:0443/watch/?v=111&note=" + character
    expected = "https://www.facebook.com/watch/?v=111&note=" + "".join(
        f"%{byte:02X}" for byte in character.encode()
    )
    body = request(entry, url=original)
    preview = collect(entry, body, channel)["approval"]
    assert preview["destination"] == preview["request"]["inputs"][0]["url"] == expected
    assert preview["destination"].isascii()
    assert not entry.egress.exists()
    # Review the original request file too: stderr must show the canonical destination.
    grant = entry.approve(preview, body, approval_key(preview))["data"]["id"]
    assert "Destination: " + expected in entry.diagnostics[-1]
    accepted = submit(entry, {**body, "access_grant_id": grant}, channel)
    entry.cli("worker", "run", "--once")
    data = entry.cli("jobs", "get", accepted["data"]["job_id"])["data"]
    assert data["job"]["state"] == "succeeded", data
    assert data["job"]["payload"]["inputs"][0]["url"] == original
    assert data["steps"][0]["target"]["urls"] == [expected]
    rows = [json.loads(line) for line in entry.egress.read_text().splitlines()]
    assert [row["url"] for row in rows if row.get("event") == "extract"] == [expected]
    assert [row["url"] for row in rows if row.get("event") == "dial"] == [expected]


@pytest.mark.parametrize("channel", ["cli", "mcp"])
@pytest.mark.parametrize(
    "first,second",
    [
        ("https://www.facebook.com/watch/?v=111", UNREVIEWABLE[0]),
        ("https://www.facebook.com/#!/watch/?v=222", "https://www.facebook.com/#!/watch/?v=333"),
        (
            "https://media.fixture.test/one?v=1&token=abc;v=2",
            "https://media.fixture.test/one?v=1&token=xyz;v=3",
        ),
    ],
)
def test_r8_d2_hidden_destination_pairs_are_distinct_or_refused(entry, channel, first, second):
    facebook(entry)
    shown = []
    for url in (first, second):
        envelope = collect(
            entry, request(entry, url=url), channel, code=2 if "#" in url or "token=" in url else 3
        )
        if envelope["approval"]:
            shown.append(envelope["approval"]["destination"])
        else:
            assert envelope["error"]["message"] == "Remote URL cannot be reviewed safely"
    assert len(shown) < 2 or shown[0] != shown[1]
    no_created_objects(entry)


def set_cap(entry, name, value):
    path = entry.workspace / "metamuse.toml"
    lines = path.read_text().splitlines()
    path.write_text(
        "\n".join(f"{name} = {value}" if line.startswith(name + " =") else line for line in lines)
        + "\n"
    )


@pytest.mark.parametrize("channel", ["cli", "mcp"])
@pytest.mark.parametrize(
    "name,old,new",
    [
        ("safe_read_retries", 2, 0),
        ("safe_read_retries", 0, 2),
        ("read_timeout_seconds", 30, 15),
        ("read_timeout_seconds", 15, 30),
        ("media_max_bytes", 536870912, 100),
        ("media_max_bytes", 100, 536870912),
        ("text_max_bytes", 20971520, 10000),
        ("text_max_bytes", 10000, 20971520),
    ],
)
def test_r8_cap_edit_between_review_and_submission_requires_new_approval(
    entry, channel, name, old, new
):
    set_cap(entry, name, old)
    body = request(entry)
    preview = collect(entry, body, channel)["approval"]
    grant = entry.approve(preview, body, approval_key(preview))["data"]["id"]
    set_cap(entry, name, new)
    denied = collect(entry, {**body, "access_grant_id": grant}, channel)
    assert denied["error"]["code"] == "E_SCOPE_DENIED"
    assert denied["approval"] is None
    assert (
        denied["error"]["next_action"]
        == "Repeat the call without a grant to get a fresh approval preview"
    )
    assert permit(entry, grant)["used"] == 0
    assert entry.cli("jobs", "list", "--project", entry.project)["data"]["jobs"] == []
    assert not entry.egress.exists()
    fresh = collect(entry, body, channel)["approval"]
    assert fresh["action_digest"] != preview["action_digest"]
    assert "caps are part of this approval" in preview["summary"]
    assert "raised cap needs a new approval" in preview["summary"]


@pytest.mark.parametrize("channel", ["cli", "mcp"])
@pytest.mark.parametrize("kind", ["mode_0644", "symlink"])
def test_r8_unsafe_cookie_metadata_never_gets_preview(entry, channel, kind):
    ref, _marker = import_cookies(entry)
    configure(entry, auth_ref=ref)
    path = Path(entry.env["XDG_CONFIG_HOME"]) / "metamuse" / "auth" / (ref + ".json")
    if kind == "mode_0644":
        path.chmod(0o644)
    else:
        real = path.with_suffix(".saved")
        path.rename(real)
        path.symlink_to(real)
    envelope = collect(entry, request(entry), channel)
    assert envelope["error"]["code"] == "E_AUTH_REQUIRED"
    assert envelope["approval"] is None
    no_created_objects(entry)


@pytest.mark.parametrize("channel", ["cli", "mcp"])
@pytest.mark.parametrize("edit", ["offline", "limit", "approval_table"])
def test_r8_exact_replay_precedes_preparation_after_workspace_edits(entry, channel, edit):
    body = request(entry)
    preview = collect(entry, body, channel)["approval"]
    grant = entry.approve(preview, body, approval_key(preview))["data"]["id"]
    body["access_grant_id"] = grant
    accepted = submit(entry, body, channel)
    if edit == "limit":
        # CLI constructs the request's own limits from workspace defaults. Keep
        # those bytes unchanged for an exact replay; MCP supplies them explicitly.
        set_cap(entry, "media_max_bytes" if channel == "cli" else "max_bytes", 1000)
    else:
        configure(entry, live=edit != "offline", enabled=edit != "approval_table")
    replay = submit(entry, body, channel)
    assert replay["data"]["job_id"] == accepted["data"]["job_id"]
    assert len(entry.cli("jobs", "list", "--project", entry.project)["data"]["jobs"]) == 1
    assert permit(entry, grant)["used"] == 0
    assert not entry.egress.exists()


@pytest.mark.parametrize("channel", ["cli", "mcp"])
def test_r8_new_run_with_unchanged_caps_gets_unused_approval_key(entry, channel):
    body = request(entry)
    preview = collect(entry, body, channel)["approval"]
    grant = entry.approve(preview, body, approval_key(preview))["data"]["id"]
    submit(entry, {**body, "access_grant_id": grant}, channel)
    entry.cli("worker", "run", "--once")
    assert permit(entry, grant)["used"] == 1
    fresh_body = request(entry, key="new-run")
    fresh = collect(entry, fresh_body, channel)["approval"]
    assert fresh["action_digest"] == preview["action_digest"]
    assert approval_key(fresh) != approval_key(preview)
    new_grant = entry.approve(fresh, fresh_body, approval_key(fresh))["data"]["id"]
    assert new_grant != grant and permit(entry, new_grant)["used"] == 0
