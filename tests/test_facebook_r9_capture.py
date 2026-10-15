"""Round-9 structured observation regressions; synthetic loopback only."""

# ruff: noqa: E501

import pytest
from browser_deadline import browser_deadline
from test_facebook import _synthetic_post_html
from test_facebook_r8_capture import run
from test_facebook_review import URL, browser_fixture
from test_facebook_round5 import POST_PATH, parse
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import _facebook_capture as capture
from metamuse import facebook
from metamuse import models as m


def test_r9_fixture_capture_preserves_core_error():
    markup = _synthetic_post_html().replace(
        "</body>", '<template shadowrootmode="closed"></template></body>'
    )
    with pytest.raises(m.CoreError) as caught:
        parse(markup)
    assert caught.value.detail.code == m.ErrorCode.SELECTOR_DRIFT
    assert caught.value.detail.message == "Facebook structured capture could not be completed"


# The table is the single vocabulary for both the corpus and the source rule.
def test_r9_no_direct_dom_members():
    import re

    from metamuse import _facebook_capture as cap
    from metamuse import _facebook_dom as dom

    for source in (cap.WALK, dom.HIT_TEST):
        assert dom.ACCESSOR_TABLE in source
        source = source.replace(dom.ACCESSOR_TABLE, "")
        # Lex strings/comments first: dots in text are not JavaScript member reads.
        tokens = re.sub(
            r""""(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|//[^\n]*|/\*.*?\*/""",
            " STRING ",
            source,
            flags=re.S,
        )
        # n_* are platform handles. r_* / v_* are records and arrays made by
        # this program, never values obtained by a DOM property lookup.
        assert not re.search(r"\b(?:n_\w+|document|this)\s*(?:\?\.)?\s*[.\[]", tokens)
        names = {member for _, member, _ in dom.ACCESSORS.values()} - {"length", "item"}
        for match in re.finditer(r"\b([\w$]+)\s*\.\s*([\w$]+)", tokens):
            receiver, member = match.groups()
            assert member not in names or receiver == "D", match.group()


@browser_deadline
def test_r9_clobbering_accessor_corpus(tmp_path, monkeypatch):
    from metamuse import _facebook_dom as dom

    def result(session, markup):
        session._page.set_content(markup)
        session._network_cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
        _, nodes, gaps = session._capture()
        e = facebook._DOMExtractor()
        e.walk(nodes)
        return (e.meta_state, e.post_attrs, e.post_text_parts, e.comments, gaps)

    with browser_fixture(
        tmp_path,
        monkeypatch,
        {POST_PATH: _synthetic_post_html()},
        unsandboxed=False,
        read_timeout=300,
    ) as (_, _, _, _, _, guard, driver):
        state = facebook.SanitizedStorageState(
            cookies=[], origins=[], secret_tokens=(), cookie_hosts=(), dropped_cookie_domains=()
        )
        session = driver.open_session(state, guard, timeout_ms=300000, max_bytes=100000000)
        try:
            session.goto_post(URL)
            contexts = [
                '<div data-page-state="login_wall"></div>',
                '<div data-checkpoint="true"></div>',
                '<article data-fb-role="comment" data-comment-id="c1"><div data-fb-role="comment-body">PAINTED_IN_FORM</div></article>',
            ]
            for marker in contexts:
                baseline = result(
                    session, _synthetic_post_html().replace("</body>", marker + "</body>")
                )
                for name in sorted({v[1] for v in dom.ACCESSORS.values()}):
                    plants = [
                        f'<form><input name="{name}">{marker}</form>',
                        *[
                            f'<{tag} name="{name}">'
                            + ("" if tag in capture.VOID else f"</{tag}>")
                            + marker
                            for tag in ("form", "embed", "img", "iframe", "object")
                        ],
                    ]
                    for plant in plants:
                        actual = result(
                            session, _synthetic_post_html().replace("</body>", plant + "</body>")
                        )
                        assert actual == baseline, (name, marker, plant, actual, baseline)
        finally:
            session.close()


@pytest.mark.parametrize(
    "plant,code",
    [
        ('<form id="login_form"><input name="nodeType"></form>', "E_AUTH_REQUIRED"),
        ('<form id="login_form"><input name="attributes"></form>', "E_AUTH_REQUIRED"),
        (
            '<form><input name="childNodes"><div data-page-state="login_wall"></div></form>',
            "E_AUTH_REQUIRED",
        ),
        (
            '<form><input name="childNodes"><article data-fb-role="comment" data-comment-id="c1"><div data-fb-role="comment-body">PAINTED_IN_FORM</div></article></form>',
            None,
        ),
        ('<form id="checkpoint_form"><input name="localName"></form>', "E_CHECKPOINT_REQUIRED"),
        ('<form><input name="checkVisibility"></form>', None),
        ('<form name="contentType"><input name="x"></form>', None),
        ('<embed name="contentType">', None),
        (
            "<script>Object.defineProperty(document,'contentType',{get:()=> 'application/xml'});</script>",
            None,
        ),
    ],
)
@browser_deadline
def test_r9_reviewer_clobbering_rows(tmp_path, monkeypatch, plant, code, request):
    markup = _synthetic_post_html().replace("</body>", plant + "</body>")
    job, stored = run(tmp_path, monkeypatch, markup, "chromium")
    assert (job.get("error") or {}).get("code") == code, job
    if "PAINTED_IN_FORM" in plant:
        assert any(r["text"] == "PAINTED_IN_FORM" for r in stored)
        assert job["coverage_status"] == "partial"


@pytest.mark.parametrize("tag", ["template", "slot"])
@pytest.mark.parametrize("foreign", ["svg", "math"])
@browser_deadline
def test_r9_foreign_template_slot(tmp_path, monkeypatch, tag, foreign, request):
    markup = _synthetic_post_html().replace(
        "</body>", f"<{foreign}><{tag}></{tag}></{foreign}></body>"
    )
    job, _ = run(tmp_path, monkeypatch, markup, "chromium")
    assert job["error"] is None, job
    assert job["coverage_status"] == "scope_satisfied"


@pytest.mark.parametrize("driver", ["fixture", "chromium"])
@pytest.mark.parametrize("kind", ["comment", "post", "comments-section", "img"])
@browser_deadline
def test_r9_unrendered_container_state(tmp_path, monkeypatch, driver, kind, request):
    if kind == "img":
        markup = _synthetic_post_html(post_text="BODY").replace(
            ">BODY</div>", '>Lead<img style="display:contents" alt="UNPAINTED_ALT"></div>'
        )
    else:
        markup = _synthetic_post_html(
            comments=[{"id": "c1", "text": "REAL_C1_BODY"}], comments_exhausted=False
        )
        hidden = f'<article data-fb-role="{kind}" data-comment-id="ghost" data-post-id="wrong" style="visibility:hidden"><span style="visibility:visible"></span></article>'
        if kind == "comment":
            markup = markup.replace(
                '<div data-fb-role="comment-body">', hidden + '<div data-fb-role="comment-body">'
            )
        else:
            markup = markup.replace("<body>", "<body>" + hidden)
    job, stored = run(tmp_path, monkeypatch, markup, driver)
    assert job["error"] is None, job
    assert not any(r["native_id"] == "ghost" for r in stored)
    assert not any("UNPAINTED_ALT" in (r["text"] or "") for r in stored)
    if kind == "comment":
        assert [r["text"] for r in stored if r["kind"] == "comment"] == ["REAL_C1_BODY"]


@browser_deadline
def test_r9_slot_contents_hidden_composed_ancestor(tmp_path, monkeypatch):
    markup = (
        _synthetic_post_html(post_text="BODY", comments_exhausted=False)
        .replace(
            ">BODY</div>",
            '>Lead<div id="host" style="content-visibility:auto;position:absolute;top:99999px"><span style="display:contents"> NEVER_PAINTED_SLOTTED</span><i style="display:contents" data-comments-exhausted="true"></i></div></div>',
        )
        .replace(
            "</body>",
            '<script>document.getElementById("host").attachShadow({mode:"open"}).innerHTML=\'<div style="display:none"><slot></slot></div>\';</script></body>',
        )
    )
    job, stored = run(tmp_path, monkeypatch, markup, "chromium")
    assert job["error"] is None, job
    assert job["coverage_status"] == "partial"
    assert "NEVER_PAINTED" not in stored[0]["text"]




@pytest.mark.parametrize(
    "markup",
    [
        *[
            f"<{tag}>TEXT</{tag}>"
            for tag in (
                "xmp",
                "iframe",
                "noembed",
                "noframes",
                "plaintext",
                "textarea",
                "title",
                "dialog",
            )
        ],
        "<div popover>TEXT</div>",
        '<svg><noscript><g data-page-state="login_wall"></g></noscript></svg>',
        "<svg><template></template></svg>",
        "<svg><slot></slot></svg>",
        "<svg><input></input></svg>",
        '<div style="display:none"><svg><desc></div></desc></svg></div>',
        "<div><span></div></span>",
        "<p><div>TEXT</div></p>",
        "<table><tr><td>TEXT</td></tr></table>",
        '<template shadowrootmode="open"></template>',
        '<script>document.body.textContent="changed";</script>',
        "<style>span{display:none}</style>",
    ],
)
def test_r9_fixture_refuses_unsupported_constructs(markup):
    with pytest.raises(m.CoreError) as caught:
        capture.fixture_capture(markup)
    assert caught.value.detail.code == m.ErrorCode.SELECTOR_DRIFT
    assert caught.value.detail.message == "Facebook structured capture could not be completed"
