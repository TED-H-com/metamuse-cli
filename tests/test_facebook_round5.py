"""Round-5 regressions: synthetic fixtures and loopback Chromium, never LIVE."""

import json

import pytest
from test_facebook import _setup_fb_env, _synthetic_post_html
from test_facebook_review import URL, browser_fixture, finish, observation

from metamuse import app, facebook

POST_PATH = "/synth.lab/posts/pfbid0101synth"


@pytest.fixture(autouse=True)
def reset_seams():
    yield
    facebook.set_driver_factory(None)
    facebook.set_egress_factory(None)
    facebook.set_secret_store_factory(None)
    facebook.set_resolver_factory(None)
    facebook.set_batch_hook(None)


def json_page(comments=(), **post_fields):
    payload = {
        "post": {
            "post_id": "pfbid0101synth",
            "actor": "synth.lab",
            "text": "Synthetic",
            **post_fields,
        },
        "comments": list(comments),
        "comments_exhausted": True,
    }
    return (
        '<script type="application/json" data-fb-post-payload>' + json.dumps(payload) + "</script>"
    )


def submit(ctx, project, permit, *, url=URL, max_items=100, max_bytes=1_000_000):
    # Synthetic jobs allow disk stalls; browser read/expansion deadlines stay 2s.
    result = app.submit_collect(
        ctx,
        {
            "project_id": project,
            "inputs": [{"kind": "url", "url": url}],
            "resources": ["post", "comments"],
            "access_grant_id": permit,
            "limits": {
                "max_urls": 1,
                "max_items": max_items,
                "max_bytes": max_bytes,
                "wall_clock_seconds": 300,
            },
            "request_key": "review-round5-job",
        },
    )
    assert result.status == "accepted", result
    return result.data["job_id"]


def fixture_job(tmp_path, markup):
    ctx, project, permit, _, _ = _setup_fb_env(tmp_path)
    driver = facebook.FixtureBrowserDriver(observation(markup))
    facebook.set_driver_factory(lambda: driver)
    return ctx, finish(ctx, submit(ctx, project, permit))


def rows(ctx):
    with ctx.store.connection() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT i.native_id, i.identity_confidence, i.kind, i.unresolved_parent, "
                "r.text, r.complete, r.missing_fields FROM items i JOIN item_revisions r "
                "ON r.item_id=i.id ORDER BY i.rowid, r.revision"
            )
        ]


def parse(markup):
    return facebook.classify_and_parse_observation(
        observation(markup),
        permalink=facebook.parse_post_permalink(URL),
        require_comments_section=True,
    )


@pytest.mark.parametrize("kind", ["dom", "json"])
@pytest.mark.parametrize("count", ["1,234", "1.2K", "100 comments", ""])
def test_r5_11_present_unparsed_comment_count_is_uncertainty(tmp_path, kind, count):
    markup = (
        json_page(comment_count=count)
        if kind == "json"
        else _synthetic_post_html().replace(
            'data-comment-count="0"', f'data-comment-count="{count}"'
        )
    )
    assert "comment_count_unparsed" in parse(markup).missing_fields


def test_r5_11_unparsed_count_keeps_job_and_post_incomplete(tmp_path):
    ctx, job = fixture_job(tmp_path, json_page(comment_count="1.2K"))
    assert job["error"] is None
    assert job["coverage_status"] == "partial"
    assert "comment_count_unparsed" in job["missing_fields"]
    post = rows(ctx)[0]
    assert not post["complete"]
    assert "comment_count_unparsed" in json.loads(post["missing_fields"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("comment_count", "²"),
        ("reaction_count", "¹²"),
        ("share_count", "①"),
        ("comment_count", "١٢٣"),
        ("comment_count", "1" * 5000),
    ],
)
@pytest.mark.parametrize("kind", ["dom", "json"])
def test_r5_n6_counts_accept_only_bounded_ascii_digits(tmp_path, kind, field, value):
    markup = json_page(**{field: value})
    if kind == "dom":
        attr = "data-" + field.replace("_", "-")
        old = {"comment_count": "0", "reaction_count": "12", "share_count": "1"}[field]
        markup = _synthetic_post_html().replace(f'{attr}="{old}"', f'{attr}="{value}"')
    assert field + "_unparsed" in parse(markup).missing_fields


@pytest.mark.parametrize("kind", ["dom", "json"])
def test_r5_n6_unparseable_remaining_hint_is_a_gap(tmp_path, kind):
    if kind == "dom":
        markup = _synthetic_post_html(expand_controls=[{"id": "more", "remaining": "³"}])
    else:
        payload = {
            "post": {"post_id": "pfbid0101synth", "actor": "synth.lab", "text": "Synthetic"},
            "comments": [],
            "expand_controls": [{"id": "more", "remaining": "³"}],
        }
        markup = (
            '<script type="application/json" data-fb-post-payload>'
            + json.dumps(payload)
            + "</script>"
        )
    assert "remaining_count_unparsed" in parse(markup).missing_fields


def test_r5_n6_unicode_count_keeps_observed_post(tmp_path):
    ctx, job = fixture_job(tmp_path, json_page(comment_count="²"))
    assert job["error"] is None
    assert len(rows(ctx)) == 1
    assert "comment_count_unparsed" in job["missing_fields"]


@pytest.mark.parametrize("parent", ["   ", "", "bad parent", "bad/parent"])
@pytest.mark.parametrize("kind", ["dom", "json"])
def test_r5_12_invalid_parent_has_item_and_job_gap(tmp_path, kind, parent):
    comments = [{"id": "r_1", "kind": "reply", "parent_id": parent, "text": "Reply"}]
    markup = json_page(comments) if kind == "json" else _synthetic_post_html(comments=comments)
    assert parse(markup).comments[0].parent_native_id is None


def test_r5_12_json_whitespace_parent_gaps_item_and_job(tmp_path):
    markup = json_page([{"id": "r_1", "kind": "reply", "parent_id": "   ", "text": "Reply"}])
    ctx, job = fixture_job(tmp_path, markup)
    reply = next(row for row in rows(ctx) if row["kind"] == "reply")
    assert not reply["complete"]
    assert reply["unresolved_parent"] is None
    assert "parent_comment" in json.loads(reply["missing_fields"])
    assert "parent_comment" in job["missing_fields"]


@pytest.mark.parametrize("kind", ["dom", "json"])
@pytest.mark.parametrize("identity", ["  ", "bad id", "bad/id"])
def test_r5_n7_invalid_comment_ids_are_distinct_weak_observations(tmp_path, kind, identity):
    comments = [
        {"id": identity, "text": "Same text"},
        {"id": identity, "text": "Same text"},
        {"id": "  c_valid  ", "text": "Valid"},
    ]
    markup = json_page(comments) if kind == "json" else _synthetic_post_html(comments=comments)
    assert [c.native_id for c in parse(markup).comments] == [None, None, "c_valid"]


def test_r5_n7_weak_comments_do_not_merge_or_abort_batch(tmp_path):
    markup = json_page([{"id": " ", "text": "Same"}, {"id": " ", "text": "Same"}])
    ctx, job = fixture_job(tmp_path, markup)
    assert job["error"] is None
    comments = [row for row in rows(ctx) if row["kind"] == "comment"]
    assert len(comments) == 2
    assert [row["identity_confidence"] for row in comments] == ["weak", "weak"]
    assert all(row["native_id"] is None for row in comments)


def test_r5_n8_resume_without_committed_batch_keeps_weak_records(tmp_path):
    ctx, project, permit, _, _ = _setup_fb_env(tmp_path)
    facebook.set_driver_factory(
        lambda: facebook.FixtureBrowserDriver(
            facebook.PageObservation(url=URL, status_code=401, html="<html>login</html>")
        )
    )
    job_id = submit(ctx, project, permit)
    assert finish(ctx, job_id)["error"]["code"] == "E_AUTH_REQUIRED"
    assert not rows(ctx)
    markup = _synthetic_post_html(comments=[{"text": "Weak one"}, {"text": "Weak two"}])
    facebook.set_driver_factory(lambda: facebook.FixtureBrowserDriver(observation(markup)))
    result = app.control_job(
        ctx, {"job_id": job_id, "operation": "resume", "request_key": "resume-r5"}
    )
    assert result.error is None
    job = finish(ctx, job_id)
    assert len(rows(ctx)) == 3
    assert "weak_identity_resume" not in job["missing_fields"]


@pytest.mark.parametrize("kind", ["stream", "passthrough"])
def test_r5_04_network_blocks_page_websocket_bypasses(tmp_path, monkeypatch, kind):
    script = (
        "new WebSocketStream('wss://' + location.host + '/socket');"
        if kind == "stream"
        else "const s = new WebSocket('wss://' + location.host + '/socket'); s._apiPassThrough();"
    )
    markup = _synthetic_post_html().replace("</body>", "<script>" + script + "</script></body>")
    with browser_fixture(tmp_path, monkeypatch, {POST_PATH: markup}, unsandboxed=False) as env:
        ctx, _, _, _, hits, guard, driver = env
        state = facebook.load_storage_state(ctx.policy.facebook.auth_ref, account="acct_synth")
        session = driver.open_session(state, guard, timeout_ms=5000)
        try:
            session.goto_post(URL)
            session._page.wait_for_timeout(500)
            assert not any(upgrade for _, upgrade in hits)
            assert any(
                row["reason"] == "websocket_blocked" for row in guard.summary()["aborted_reasons"]
            )
        finally:
            session.close()


def test_r5_04_unexpected_handshake_fails_job_closed(tmp_path, monkeypatch):
    # Deliberately sabotage the network block in this test only, to exercise detection.
    script = "new WebSocketStream('wss://' + location.host + '/socket');"
    markup = _synthetic_post_html().replace("</body>", "<script>" + script + "</script></body>")
    with browser_fixture(
        tmp_path,
        monkeypatch,
        {POST_PATH: markup.replace("</body>", '<img src="/hold"></body>'), "/hold": ""},
        unsandboxed=False,
        delays={"/hold": 0.5},
    ) as env:
        ctx, project, permit, _, hits, _, driver = env
        original = driver.open_session

        def sabotaged(*args, **kwargs):
            session = original(*args, **kwargs)
            cdp = session._network_cdp
            cdp.send("Network.setBlockedURLs", {"urls": []})
            cdp.send("Network.emulateNetworkConditionsByRule", {"matchedNetworkConditions": []})
            return session

        monkeypatch.setattr(driver, "open_session", sabotaged)
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"]["code"] == "E_PROTOCOL_MISMATCH"
        assert job["error"]["message"] == "Facebook WebSocket handshake bypassed browser blocking"
        assert any(upgrade for _, upgrade in hits)
        assert not rows(ctx)


@pytest.mark.parametrize("refusal", ["websocket", "worker", "image"])
def test_r5_n1_unrelated_refusal_cannot_reclassify_navigation_timeout(
    tmp_path, monkeypatch, refusal
):
    scripts = {
        "websocket": "new WebSocket('wss://' + location.host + '/socket');",
        "worker": "new Worker(URL.createObjectURL(new Blob(['postMessage(1)'])));",
        "image": "new Image().src='https://denied.example/image';",
    }
    markup = _synthetic_post_html().replace(
        "</body>", '<img src="/slow.png"><script>' + scripts[refusal] + "</script></body>"
    )
    with browser_fixture(
        tmp_path, monkeypatch, {POST_PATH: markup, "/slow.png": ""}, delays={"/slow.png": 4}
    ) as env:
        ctx, project, permit, _, _, _, _ = env
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"]["code"] == "E_NETWORK_TIMEOUT"
        assert job["error"]["retry_class"] == "safe_read"


def test_r5_n1_resolver_capacity_is_transient_dns_failure(tmp_path, monkeypatch):
    import threading

    from metamuse import _browser_egress

    monkeypatch.setattr(_browser_egress, "_RESOLUTION_SLOTS", threading.BoundedSemaphore(0))
    with browser_fixture(tmp_path, monkeypatch, {POST_PATH: _synthetic_post_html()}) as env:
        ctx, project, permit, _, hits, _, _ = env
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"]["code"] in ("E_NETWORK_TIMEOUT", "E_RESOURCE_UNAVAILABLE")
        assert "dns" in job["error"]["next_action"].lower()
        assert not hits
