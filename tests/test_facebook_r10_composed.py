"""Closed-root slot ancestry and browser-derived containment semantics."""

# ruff: noqa: E501

import json

import pytest
from browser_deadline import browser_deadline
from facebook_parity import capture_session
from test_facebook import _synthetic_post_html
from test_facebook_r10_expansion import PROGRESS, outcome
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import facebook


@browser_deadline
def test_r10_closed_slot_rendering(tmp_path, monkeypatch):
    with capture_session(monkeypatch) as session:
        for wrapper in (
            '<div style="display:none"><slot></slot></div>',
            "<svg><defs><foreignObject><slot></slot></foreignObject></defs></svg>",
        ):
            results = []
            for mode in ("open", "closed"):
                markup = _synthetic_post_html(post_text="BODY", comments_exhausted=False).replace(
                    ">BODY</div>",
                    '>Lead<div id="host" style="content-visibility:auto;position:absolute;top:99999px">'
                    '<span style="display:contents">UNPAINTED</span>DIRECT_TEXT'
                    '<i style="display:contents" data-comments-exhausted="true"></i></div></div>',
                )
                markup = markup.replace(
                    "</body>",
                    '<script>document.getElementById("host").attachShadow({mode:'
                    + json.dumps(mode)
                    + "}).innerHTML="
                    + json.dumps(wrapper)
                    + ";</script></body>",
                )
                session._page.set_content(markup)
                session._network_cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
                _, nodes, gaps = session._capture()
                assert not gaps
                e = facebook._DOMExtractor()
                e.walk(nodes)
                result = ("".join(e.post_text_parts), e.comments_exhausted)
                assert "UNPAINTED" not in result[0] and "DIRECT_TEXT" not in result[0], (
                    mode,
                    wrapper,
                    result,
                )
                assert not result[1], (mode, wrapper)
                results.append(result)
            assert results[0] == results[1]


@pytest.mark.parametrize("mode", ["open", "closed"])
@browser_deadline
def test_r10_closed_slot_click(tmp_path, monkeypatch, mode, request):
    light = "<span>LABEL</span>"
    markup = _synthetic_post_html(
        comments=[{"id": "c1", "text": "INITIAL"}], comments_exhausted=False
    ).replace("</section>", '<div id="host">' + light + "</div></section>")
    markup = markup.replace(
        "</body>",
        '<script>const root=document.getElementById("host").attachShadow({mode:'
        + json.dumps(mode)
        + "});"
        'root.innerHTML=\'<div data-expand-kind="more_comments" data-expand-id="more" style="display:inline-block;padding:6px"><slot></slot></div>\';'
        'root.querySelector("div").onclick=()=>{'
        + PROGRESS.replace(
            "document.querySelector('button').remove();", "root.querySelector('div').remove();"
        )
        + "};</script></body>",
    )
    job, stored, _ = outcome(tmp_path, monkeypatch, markup)
    assert job["state"] == "succeeded", job
    assert [r["text"] for r in stored if r["kind"] == "comment"] == ["INITIAL", "SECOND"]
