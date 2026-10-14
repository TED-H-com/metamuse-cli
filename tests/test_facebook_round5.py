"""Round-5 regressions: synthetic fixtures and loopback Chromium, never LIVE."""

import json

import pytest
from test_facebook import _setup_fb_env, _synthetic_post_html
from test_facebook_review import URL, finish, observation, submit

from metamuse import facebook

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
