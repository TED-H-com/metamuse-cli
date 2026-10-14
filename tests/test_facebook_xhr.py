"""Trusted XMLHttpRequest expansion provenance (M8); synthetic loopback only."""

from browser_deadline import browser_deadline
from test_facebook import _synthetic_post_html
from test_facebook_review import browser_fixture, finish
from test_facebook_round5 import POST_PATH, rows, submit
from test_facebook_round6 import reset_seams  # noqa: F401


@browser_deadline
def test_r6_xhr_expansion_failure_preserves_observed_data(tmp_path, monkeypatch):
    markup = _synthetic_post_html(
        comments=[{"id": "c_1", "text": "First"}], expand_controls=[{"id": "more"}]
    ).replace(
        "</body>",
        """<script>
      document.querySelector('button').onclick = event => {
        event.target.remove();
        const xhr = new XMLHttpRequest(); xhr.open('GET', '/api/more'); xhr.send();
      };
      </script></body>""",
    )
    with browser_fixture(
        tmp_path, monkeypatch, {POST_PATH: markup, "/api/more": (500, None)}, unsandboxed=False
    ) as (ctx, project, permit, _, hits, *_):
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"] is None
        assert job["stop_reason"] == "expansion_failed"
        assert job["state"] == "partial"
        assert job["coverage_status"] == "partial"
        assert "expansion_failed" in job["missing_fields"]
        assert len(rows(ctx)) == 2
        assert any(path == "/api/more" for path, _ in hits)
