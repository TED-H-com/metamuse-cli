"""HTML tag semantics and isolation of unrendered identity containers."""

# ruff: noqa: E501

import ast
import inspect
import json
import re
from pathlib import Path

from browser_deadline import browser_deadline
from facebook_parity import capture_session
from test_facebook import _synthetic_post_html
from test_facebook_round5 import parse
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import _facebook_capture as capture
from metamuse import facebook


def extracted(nodes):
    e = facebook._DOMExtractor()
    e.walk(nodes)
    return e


@browser_deadline
def test_r10_namespace_tag_table(tmp_path, monkeypatch):
    # The production table is the vocabulary; no hand-picked foreign subset.
    tags = getattr(facebook._DOMExtractor, "HTML_TAGS", None)
    if tags is None:
        # Historical baseline only: derive its vocabulary from its own AST,
        # rather than importing a table absent from that older implementation.
        tags = capture.VOID | {
            n.value
            for n in ast.walk(ast.parse(inspect.getsource(facebook._DOMExtractor)))
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and re.fullmatch(r"[a-z][a-z0-9]*", n.value)
        }
    with capture_session(monkeypatch) as session:
        for ns in (capture.SVG, capture.MATH):
            for tag in sorted(tags):
                session._page.set_content(_synthetic_post_html(post_text="BODY"))
                session._page.evaluate(
                    """([ns,tag])=>{
                  const n=document.createElementNS(ns,tag);n.setAttribute('id','login_form');
                  n.setAttribute('type','application/json');n.setAttribute('data-fb-post-payload','');
                  document.querySelector('[data-fb-role=post-body]').appendChild(n);
                }""",
                    [ns, tag],
                )
                e = extracted(session._capture()[1])
                assert e.meta_state is None, (ns, tag, e.meta_state)
                assert e.json_scripts == [], (ns, tag)
                assert "".join(e.post_text_parts) == "BODY", (ns, tag, e.post_text_parts)


@browser_deadline
def test_r10_unrendered_identity_isolates_subtree(tmp_path, monkeypatch):
    cases = json.loads(Path("tests/fixtures/facebook/parity/round10-nesting.json").read_text())
    hidden = '<article data-fb-role="post" style="visibility:hidden"><div data-fb-role="post-body"><b style="visibility:visible">UNATTRIBUTED</b></div></article>'
    cases.append(
        {
            "name": "sibling-before",
            "markup": _synthetic_post_html(post_text="BODY").replace("<body>", "<body>" + hidden),
        }
    )
    with capture_session(monkeypatch) as session:
        for case in cases:
            markup = case["markup"]
            fixture = extracted(capture.fixture_capture(markup))
            session._page.set_content(markup)
            browser = extracted(session._capture()[1])
            for e in (fixture, browser):
                joined = "".join(e.post_text_parts) + "".join(
                    "".join(c["text_parts"]) for c in e.comments
                )
                for sentinel in (
                    "VISIBLE_LEAF_IN_UNRENDERED_POST",
                    "VISIBLE_SPAN_INSIDE_UNRENDERED_COMMENT",
                    "UNATTRIBUTED",
                ):
                    assert sentinel not in joined, (case["name"], joined)
                gap = case["name"] in {
                    "unrendered_post_inside_visible_post",
                    "unrendered_comment_with_visible_leaf_inside_visible_comment",
                    "sibling-before",
                }
                assert ("unattributed_rendered_text" in e.count_gaps) == gap, (
                    case["name"],
                    e.count_gaps,
                )
            assert fixture.post_text_parts == browser.post_text_parts
            assert fixture.comments == browser.comments
            assert fixture.count_gaps == browser.count_gaps
            if "visible_comment_inside" in case["name"]:
                assert any(
                    "VISIBLE_INNER_COMMENT" in "".join(c["text_parts"]) for c in fixture.comments
                )
            if case["name"] == "sibling-before":
                parsed = parse(markup)
                assert "unattributed_rendered_text" in parsed.missing_fields


def test_r10_unrendered_comments_section_is_not_identity():
    e = facebook._DOMExtractor()
    e.feed(
        '<section data-fb-role="comments-section" style="visibility:hidden"><b style="visibility:visible"></b></section>'
    )
    assert not e.comments_section_present


def test_r10_unrendered_identity_isolates_image_alt():
    markup = _synthetic_post_html(post_text="BODY").replace(
        ">BODY</div>",
        '>BODY<article data-fb-role="post" style="visibility:hidden">'
        '<div data-fb-role="post-body"><img style="visibility:visible" alt="ORPHAN_ALT">'
        "</div></article></div>",
    )
    parsed = parse(markup)
    assert "ORPHAN_ALT" not in parsed.text
    assert "unattributed_rendered_text" in parsed.missing_fields


def test_r10_unrendered_identity_cannot_insert_block_boundary():
    markup = _synthetic_post_html(post_text="BODY").replace(
        ">BODY</div>", '>BODY<article data-fb-role="post" style="visibility:hidden">'
        '<div data-fb-role="post-body"><div style="visibility:visible">ORPHAN</div>'
        '</div></article>TAIL</div>'
    )
    parsed = parse(markup)
    assert parsed.text == "BODYTAIL"
    assert "unattributed_rendered_text" in parsed.missing_fields
