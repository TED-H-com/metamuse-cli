"""Closed-root slot ancestry and browser-derived containment semantics."""

# ruff: noqa: E501

import json
import os
from pathlib import Path

import pytest
from browser_deadline import browser_deadline
from facebook_parity import as_document, capture_session, normalise
from test_facebook import _synthetic_post_html
from test_facebook_r10_expansion import PROGRESS, outcome
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import _facebook_capture as capture
from metamuse import facebook
from metamuse import models as m


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


DISPLAYS = (
    "block inline inline-block flow-root flex inline-flex grid inline-grid list-item table inline-table "
    "table-row-group table-header-group table-footer-group table-row table-column-group table-column "
    "table-cell table-caption ruby ruby-base ruby-text ruby-base-container ruby-text-container contents none"
).split()


@browser_deadline
def test_r10_content_visibility_display_oracle(tmp_path, monkeypatch):
    results = []
    with capture_session(monkeypatch) as session:
        for display in DISPLAYS:
            markup = (
                '<div id="outer" style="display:'
                + display
                + ';content-visibility:hidden"><span id="probe">PROBE</span>DIRECT</div>'
            )
            session._page.set_content(as_document(markup))
            # This oracle runs independently of WALK. Tests may read the page;
            # production observations still use only captured native accessors.
            oracle = session._page.evaluate("""() => {
                const p=document.getElementById('probe'), o=document.getElementById('outer');
                return {visible:p.checkVisibility(), rects:p.getClientRects().length,
                        display:getComputedStyle(o).display};
            }""")
            _, nodes, gaps = session._capture()
            assert not gaps
            probe = next(n for n in nodes if n["attrs"].get("id") == "probe")
            direct = next(n for n in nodes if n["text"] == "DIRECT")
            expected = oracle["visible"] and oracle["rects"] > 0
            assert probe["rendered"] == expected, (display, oracle, probe)
            assert direct["rendered"] == expected, (display, oracle, direct)
            try:
                fixture = capture.fixture_capture(markup)
            except m.CoreError as error:
                assert error.detail.code == m.ErrorCode.SELECTOR_DRIFT
                accepted = False
            else:
                assert normalise(fixture) == normalise(nodes), (display, oracle)
                accepted = True
            results.append(dict(authored=display, **oracle, rendered=expected, fixture=accepted))
    print(json.dumps(results))
    if os.environ.get("B13_CV_REPORT"):
        Path(os.environ["B13_CV_REPORT"]).write_text(json.dumps(results, indent=2) + "\n")
