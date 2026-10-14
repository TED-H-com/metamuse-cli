"""Round-7 visibility parity, synthetic loopback pages in sandboxed Chromium."""

import pytest
from test_facebook import _setup_fb_env, _synthetic_post_html
from test_facebook_review import browser_fixture, finish, observation
from test_facebook_round5 import POST_PATH, fixture_job, rows, submit
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import facebook


@pytest.mark.parametrize("real_browser", [False, True], ids=["fixture", "chromium"])
@pytest.mark.parametrize("case", ["post", "comment", "expand", "visible-child"])
def test_r7_page_cannot_hide_visible_evidence(tmp_path, monkeypatch, real_browser, case):
    markup = _synthetic_post_html(post_text="BODY", comments=[{"id": "c1", "text": "COMMENT"}])
    if case == "post":
        markup = markup.replace(
            ">BODY</div>", ">Lead<span data-metamuse-invisible> VISIBLE</span></div>"
        )
    elif case == "comment":
        markup = markup.replace(
            'data-comment-id="c1"', 'data-comment-id="c1" data-metamuse-invisible'
        )
    elif case == "expand":
        markup = markup.replace(
            "</body>",
            '<button data-expand-kind="more_comments" data-expand-id="more" '
            'data-remaining-count="many" data-metamuse-invisible>More</button></body>',
        )
    else:
        markup = markup.replace(
            ">BODY</div>",
            '>Lead<span style="visibility:hidden">HIDDEN'
            '<span style="visibility:visible"> VISIBLE</span></span></div>',
        )
    if real_browser:
        with browser_fixture(tmp_path, monkeypatch, {POST_PATH: markup}, unsandboxed=False) as (
            ctx,
            project,
            permit,
            _,
            _,
            _,
            driver,
        ):
            if case == "expand":
                # The click is real; make progress immediate rather than waiting for
                # a production timeout. Fixture's missing expansion is classified too.
                original = driver.open_session

                def opened(*args, **kwargs):
                    session = original(*args, **kwargs)
                    expand = session.expand_next

                    def expanded(control):
                        session._page.evaluate("""() => {
                          document.querySelector('button').onclick = () => {
                            const node = document.querySelector('[data-comment-id]');
                            node.querySelector('[data-fb-role="comment-body"]').textContent += '!';
                            document.querySelector('button').remove();
                          };
                        }""")
                        return expand(control)

                    monkeypatch.setattr(session, "expand_next", expanded)
                    return session

                monkeypatch.setattr(driver, "open_session", opened)
            job = finish(ctx, submit(ctx, project, permit))
            stored = rows(ctx)
    else:
        if case == "expand":
            ctx, project, permit, _, _ = _setup_fb_env(tmp_path)
            after = markup.replace("<button", "<button hidden")
            driver = facebook.FixtureBrowserDriver(
                observation(markup), {"more": observation(after)}
            )
            facebook.set_driver_factory(lambda: driver)
            job = finish(ctx, submit(ctx, project, permit))
            assert driver.expanded_controls == ["more"]
        else:
            ctx, job = fixture_job(tmp_path, markup)
        stored = rows(ctx)
    if case in {"post", "visible-child"}:
        assert stored[0]["text"] == "Lead VISIBLE"
    elif case == "comment":
        assert any(row["native_id"] == "c1" and row["text"] == "COMMENT" for row in stored)
    else:
        assert "remaining_count_unparsed" in job["missing_fields"]
        assert job["coverage_status"] == "partial"
        assert job["state"] == "partial"


@pytest.mark.parametrize("hiding", ["hidden", 'style="display:none"'])
def test_r7_subtree_hiding_cannot_be_overridden(tmp_path, monkeypatch, hiding):
    markup = (
        _synthetic_post_html(post_text="BODY", comments_exhausted=False)
        .replace(
            ">BODY</div>",
            ">Lead<span " + hiding + '><span style="visibility:visible">HIDDEN</span></span></div>',
        )
        .replace(
            "</body>",
            "<div " + hiding + '><span style="visibility:visible" '
            'data-comments-exhausted="true"></span></div></body>',
        )
    )
    _, fixture = fixture_job(tmp_path / "fixture", markup)
    facebook.set_driver_factory(None)
    facebook.set_egress_factory(None)
    with browser_fixture(
        tmp_path / "chromium", monkeypatch, {POST_PATH: markup}, unsandboxed=False
    ) as (ctx, project, permit, *_):
        browser = finish(ctx, submit(ctx, project, permit))
        assert rows(ctx)[0]["text"] == "Lead"
    for job in (fixture, browser):
        assert job["coverage_status"] != "scope_satisfied"


@pytest.mark.parametrize(
    "style",
    [
        "display:none;display:block",
        "display:block!important;display:none",
        "visibility:hidden;visibility:visible",
        "visibility:visible!important;visibility:collapse",
    ],
)
def test_r7_inline_css_declaration_precedence(style):
    from test_facebook_round5 import parse

    markup = _synthetic_post_html(post_text="BODY").replace(
        ">BODY</div>", f'><span style="{style}">VISIBLE</span></div>'
    )
    assert parse(markup).text == "VISIBLE"


@pytest.mark.parametrize(
    "marker",
    [
        "data-metamuse-invisible",
        "data-metamuse-display-none",
        'data-metamuse-visibility="hidden"',
    ],
)
def test_r7_raw_html_ignores_all_snapshot_markers(marker):
    from test_facebook_round5 import parse

    markup = _synthetic_post_html(post_text="BODY").replace(
        ">BODY</div>", f"><span {marker}>VISIBLE</span></div>"
    )
    assert parse(markup).text == "VISIBLE"
