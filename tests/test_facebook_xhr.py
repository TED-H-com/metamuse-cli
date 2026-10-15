"""Native XMLHttpRequest progress uncertainty and retained data; loopback only."""

import pytest
from browser_deadline import browser_deadline
from test_facebook import _synthetic_post_html
from test_facebook_review import browser_fixture, finish
from test_facebook_round5 import POST_PATH, rows, submit
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import facebook


def test_r9_synchronous_xhr_cdp_identity():
    """The former provenance seam is removed; native XHR has no outcome authority."""
    assert not hasattr(facebook._PlaywrightSession, "_note_expansion_request")
    assert not hasattr(facebook._PlaywrightSession, "_note_expansion_response")


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
        tmp_path,
        monkeypatch,
        {POST_PATH: markup, "/api/more": (500, None)},
        unsandboxed=False,
        read_timeout=2,
    ) as (ctx, project, permit, _, hits, *_):
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"] is None
        assert job["stop_reason"] == "expansion_timeout"
        assert job["state"] == "partial"
        assert job["coverage_status"] == "partial"
        assert "expansion_progress_timeout" in job["missing_fields"]
        assert len(rows(ctx)) == 2
        assert any(path == "/api/more" for path, _ in hits)


@pytest.mark.parametrize("identity", ["named-form", "synthesised"])
@browser_deadline
def test_r9_native_xhr_provenance(tmp_path, monkeypatch, identity, request):
    markup = _synthetic_post_html(
        comments=[{"id": "c_1", "text": "First"}], expand_controls=[{"id": "more"}]
    )
    control = (
        '<form data-expand-kind="more_comments" data-expand-id="more" '
        'style="width:150px;height:40px">MORE<input name="dataset" type="hidden"></form>'
    )
    markup = markup.replace(
        '<button data-expand-kind="more_comments" data-expand-id="more">More</button>', control
    )
    if identity == "synthesised":
        markup = markup.replace(' data-expand-id="more"', "")
    markup = markup.replace(
        "</body>",
        """<script>document.querySelector('form').onclick = event => {
        const xhr = new XMLHttpRequest(); xhr.open('GET', '/api/more'); xhr.send();
        document.querySelector('[data-fb-role=post]').setAttribute('data-comments-exhausted','true');
        event.currentTarget.remove();
        };</script></body>""",
    )
    with browser_fixture(
        tmp_path,
        monkeypatch,
        {POST_PATH: markup, "/api/more": (500, None)},
        unsandboxed=False,
        read_timeout=2,
    ) as (ctx, project, permit, _, hits, *_):
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"] is None, job
        assert job["stop_reason"] == "expansion_timeout", job
        assert job["state"] == "partial"
        assert len(rows(ctx)) == 2
        assert any(path == "/api/more" for path, _ in hits)
