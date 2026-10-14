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
