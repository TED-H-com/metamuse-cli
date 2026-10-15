"""Only admitted observations supply final records, controls and counts."""

import json

import pytest
from browser_deadline import browser_deadline
from test_facebook import _setup_fb_env, _synthetic_post_html
from test_facebook_r10_expansion import PROGRESS, page
from test_facebook_review import browser_fixture, finish, observation
from test_facebook_round5 import POST_PATH, rows, submit
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import facebook, jobs


@pytest.mark.parametrize("browser", [False, True], ids=["fixture", "chromium"])
@browser_deadline
def test_r11_cutoff_at_post_capture_checkpoint(tmp_path, monkeypatch, browser, request):
    captured = []
    latched = []
    original_checkpoint = jobs.StepContext.commit_checkpoint

    def checkpoint(step, payload, **kwargs):
        original_checkpoint(step, payload, **kwargs)
        # This is the exact post-capture checkpoint, before classification and
        # admission. No clock, scheduling or SQLite stall decides the boundary.
        if captured and not latched:
            captured[-1].mark_byte_limit_exceeded()
            latched.append(True)

    monkeypatch.setattr(jobs.StepContext, "commit_checkpoint", checkpoint)
    cls = facebook._PlaywrightSession if browser else facebook._FixtureSession
    original_expand = cls.expand_next
    clicks = []

    def expand(session, control):
        clicks.append(control)
        result = original_expand(session, control)
        captured.append(session._egress)
        return result

    monkeypatch.setattr(cls, "expand_next", expand)

    def run(ctx, project, permit):
        job = finish(ctx, submit(ctx, project, permit))
        error = job["result"]["data"]["steps"][0]["data"]["error"]
        assert error["code"] == "E_LIMIT_REACHED", error
        assert job["state"] == "partial" and job["coverage_status"] == "partial"
        assert job["stop_reason"] == "limit_reached"
        assert latched and clicks == ["more"]  # No second expansion after cutoff.
        stored = rows(ctx)
        assert [r["text"] for r in stored if r["kind"] == "comment"] == ["INITIAL"]
        scope = job["observed_scope"]["steps"][0]
        assert scope["comments_observed"] == 1 and scope["comments_empty"] is False
        assert scope["unexpanded_controls"] == 1, scope
        assert set(job["missing_fields"]) == {
            "byte_budget_exceeded",
            "unexpanded_comments",
            "comments_scope_unverified",
        }, job
        with ctx.store.connection() as conn:
            post = conn.execute(
                "SELECT evidence FROM item_revisions r JOIN items i ON i.id=r.item_id "
                "WHERE i.kind='post'"
            ).fetchone()
        evidence = json.loads(post["evidence"])
        assert evidence["comment_count"] == 1 and evidence["unexpanded_controls"] == 1

    if browser:
        with browser_fixture(
            tmp_path,
            monkeypatch,
            {POST_PATH: page(PROGRESS)},
            unsandboxed=False,
            read_timeout=2,
        ) as (ctx, project, permit, *_):
            run(ctx, project, permit)
    else:
        ctx, project, permit, *_ = _setup_fb_env(tmp_path)
        driver = facebook.FixtureBrowserDriver(
            observation(
                _synthetic_post_html(
                    comments=[{"id": "c1", "text": "INITIAL"}],
                    expand_controls=[{"id": "more"}],
                    comments_exhausted=False,
                )
            ),
            [
                observation(
                    _synthetic_post_html(
                        comments=[{"id": "c1", "text": "INITIAL"}, {"id": "c2", "text": "SECOND"}]
                    )
                )
            ],
        )
        facebook.set_driver_factory(lambda: driver)
        run(ctx, project, permit)
