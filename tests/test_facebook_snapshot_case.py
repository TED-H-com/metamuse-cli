"""Page-created SVG attributes cannot forge trusted snapshot metadata."""

import pytest
from test_facebook import _synthetic_post_html
from test_facebook_review import browser_fixture, finish
from test_facebook_round5 import POST_PATH, fixture_job, rows, submit
from test_facebook_round6 import reset_seams  # noqa: F401


@pytest.mark.parametrize("real_browser", [False, True], ids=["fixture", "chromium"])
def test_r7_case_preserving_svg_cannot_forge_visibility(tmp_path, monkeypatch, real_browser):
    markup = (
        _synthetic_post_html(post_text="BODY")
        .replace(
            ">BODY</div>",
            '>Lead<svg><text id="visible-svg" data-metamuse-invisible> VISIBLE</text></svg></div>',
        )
        .replace(
            "</body>",
            """<script>
        document.getElementById('visible-svg').setAttribute('data-MetaMuse-display-none', '');
        </script></body>""",
        )
    )
    if real_browser:
        with browser_fixture(tmp_path, monkeypatch, {POST_PATH: markup}, unsandboxed=False) as (
            ctx,
            project,
            permit,
            *_,
        ):
            job = finish(ctx, submit(ctx, project, permit))
            stored = rows(ctx)
    else:
        ctx, job = fixture_job(tmp_path, markup)
        stored = rows(ctx)
    assert stored[0]["text"] == "Lead VISIBLE"
    assert job["error"] is None
