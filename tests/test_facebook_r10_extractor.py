"""HTML tag semantics and isolation of unrendered identity containers."""

from browser_deadline import browser_deadline
from facebook_parity import capture_session
from test_facebook import _synthetic_post_html
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
    tags = getattr(
        facebook._DOMExtractor,
        "HTML_TAGS",
        capture.VOID
        | {
            "form",
            "script",
            "div",
            "p",
            "section",
            "article",
            "li",
            "blockquote",
            "pre",
            "h1",
            "h2",
            "h3",
        },
    )
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
