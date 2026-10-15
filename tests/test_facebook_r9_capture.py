"""Round-9 structured observation regressions; synthetic loopback only."""

# ruff: noqa: E501

import pytest
from browser_deadline import browser_deadline
from facebook_parity import capture_session
from test_facebook import _synthetic_post_html
from test_facebook_r8_capture import run
from test_facebook_round5 import parse
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


def test_r9_fixture_refuses_metadata_display_override():
    # Found by the full depth-five generator: Chromium permits a metadata box
    # override that the fixture's fixed metadata defaults do not model.
    with pytest.raises(m.CoreError) as caught:
        capture.fixture_capture('<meta style="display:contents">')
    assert caught.value.detail.code == m.ErrorCode.SELECTOR_DRIFT
    assert caught.value.detail.message == "Facebook structured capture could not be completed"


def test_r9_fixture_counts_implicit_document_nodes():
    nodes = capture.fixture_capture("<!doctype html><html><body><span>TEXT</span></body></html>")
    assert len(nodes) == 7  # document, doctype, html, head, body, span, text
    assert [n["tag"] for n in nodes if n["kind"] == 1] == ["html", "head", "body", "span"]


@pytest.mark.parametrize(
    "markup",
    [
        '<span title="&notit;">TEXT</span>',
        '<span title="&ampfoo">TEXT</span>',
        "<span>A\0B</span>",
        "<svg><text><![CDATA[TEXT]]></text></svg>",
        "<?test value?><span>TEXT</span>",
    ],
)
def test_r9_fixture_refuses_lexical_differentials(markup):
    with pytest.raises(m.CoreError) as caught:
        capture.fixture_capture(markup)
    assert caught.value.detail.code == m.ErrorCode.SELECTOR_DRIFT
    assert caught.value.detail.message == "Facebook structured capture could not be completed"


@pytest.mark.parametrize(
    "markup,value",
    [
        ('<span title="&amp;">TEXT</span>', "&"),
        ('<span title="&notin;">TEXT</span>', "∉"),
        ('<span title="&unknown;">TEXT</span>', "&unknown;"),
    ],
)
def test_r9_fixture_accepts_unambiguous_entities(markup, value):
    node = next(n for n in capture.fixture_capture(markup) if n["tag"] == "span")
    assert node["attrs"]["title"] == value


@pytest.mark.parametrize(
    "markup", ["<a>\n</a>", "<div> </div>", "<math><mtext><mi>TEXT</mi></mtext></math>"]
)
def test_r9_fixture_refuses_unmodelled_layout_and_integration(markup):
    with pytest.raises(m.CoreError) as caught:
        capture.fixture_capture(markup)
    assert caught.value.detail.code == m.ErrorCode.SELECTOR_DRIFT
    assert caught.value.detail.message == "Facebook structured capture could not be completed"


# The table is the single vocabulary for both the corpus and the source rule.
def test_r9_no_direct_dom_members():
    from facebook_dom_rule import check

    from metamuse import _facebook_dom as dom

    for source in (capture.WALK, dom.HIT_TEST, dom.POPUP_CHECK):
        check(source, dom.ACCESSOR_TABLE, dom.ACCESSORS)


def test_r9_evaluated_function_inventory():
    """New evaluation sites must join the checked dialect or this inert list."""
    import ast
    import inspect

    expected = {
        "facebook": {
            "capture_dom.HIT_TEST",
            "f'({capture_dom.POPUP_CHECK})()'",
            "'self.close()'",
        },
        "_facebook_capture": {
            "'globalThis.__metamuseClosed=new WeakMap()'",
            "f'({WALK})()'",
            "'function(root){globalThis.__metamuseClosed.set(this,root)}'",
            "f'globalThis.__metamuseNodes[{i}]'",
        },
    }
    for module in (facebook, capture):
        tree = ast.parse(inspect.getsource(module))
        actual = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if isinstance(key, ast.Constant) and key.value in {
                        "expression",
                        "functionDeclaration",
                    }:
                        actual.add(ast.unparse(value))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "evaluate":
                    assert ast.literal_eval(node.args[0]) == "0"
                if node.func.attr == "add_init_script":
                    assert ast.unparse(node.args[0]) == "WEBSOCKET_BLOCK_SCRIPT"
        assert actual == expected[module.__name__.split(".")[-1]]


@pytest.mark.parametrize(
    "expression",
    [
        "n_node.attributes",
        "n_node['attributes']",
        "n_node?.attributes",
        "D.childNodes(n_node).length",
        "D.content(n_node)['childNodes']",
        "const v_alias=D.shadowRoot(n_node);v_alias.length",
        "const v_alias=n_node;v_alias.length",
        "v_raw[0].length",
        "v_pending.pop().length",
        "const v_alias=v_raw.pop();v_alias.length",
        "const v_alias=v_children[0];v_alias.length",
        "parent(n_node).nodeType",
        "const v_alias=shadow(n_node);v_alias.length",
        "apply(computedStyle,globalThis,[n_node])[v_property]",
        "const v_alias=apply(computedStyle,globalThis,[n_node]);v_alias.length",
        "document.contentType",
        "this.contains(n_node)",
        "unknown.nodeType",
    ],
)
def test_r9_accessor_rule_rejects_dom_aliases(expression):
    from facebook_dom_rule import check

    from metamuse import _facebook_dom as dom

    with pytest.raises(AssertionError):
        check(dom.ACCESSOR_TABLE + expression, dom.ACCESSOR_TABLE, dom.ACCESSORS)


@browser_deadline
def test_r9_clobbering_accessor_corpus(tmp_path, monkeypatch):
    from metamuse import _facebook_dom as dom

    def result(session, markup):
        session._page.set_content(markup, wait_until="load")
        session._network_cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
        _, nodes, gaps = session._capture()
        e = facebook._DOMExtractor()
        e.walk(nodes)
        return (e.meta_state, e.post_attrs, e.post_text_parts, e.comments, gaps)

    with capture_session(monkeypatch) as session:
        contexts = [
            '<div data-page-state="login_wall"></div>',
            '<div data-checkpoint="true"></div>',
            '<article data-fb-role="comment" data-comment-id="c1"><div data-fb-role="comment-body">PAINTED_IN_FORM</div></article>',
        ]
        for marker in contexts:
            baseline = result(
                session, _synthetic_post_html().replace("</body>", marker + "</body>")
            )
            for name in sorted(
                {v[1] for v in dom.ACCESSORS.values()} | set(dom.GLOBAL_METHODS.values())
            ):
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
    "kind", ["missing", "duplicate", "own-shadow", "slotted", "overlay", "hidden-before-capture"]
)
@browser_deadline
def test_r9_click_identity(tmp_path, monkeypatch, kind, request):
    markup = _synthetic_post_html(
        comments=[{"id": "c1", "text": "INITIAL"}], expand_controls=[{"id": "more"}]
    )
    script = """<script>
    const target=document.querySelector('[data-comment-id] [data-fb-role="comment-body"]');
    const buttons=document.querySelectorAll('[data-expand-kind]');
    buttons.forEach((btn,i)=>btn.onclick=()=>{target.textContent=i?'WRONG':'EXPANDED';document.querySelector('[data-fb-role=post]').setAttribute('data-comments-exhausted','true');buttons.forEach(b=>b.remove())});
    </script>"""
    if kind == "missing":
        markup = markup.replace('data-expand-id="more"', "")
    elif kind == "duplicate":
        markup = markup.replace(
            "<button ",
            '<button data-expand-id="more" data-expand-kind="more_comments">First</button><button ',
            1,
        )
    elif kind == "own-shadow":
        markup = markup.replace(
            "<button ", '<div id="control" style="display:inline-block;width:150px;height:40px" '
        ).replace("More</button>", "More</div>")
        script = (
            '<script>document.getElementById("control").attachShadow({mode:"open"}).innerHTML=\'<span style="display:block;width:150px;height:40px">Label</span>\';</script>'
            + script
        )
    elif kind == "slotted":
        markup = markup.replace(
            '<button data-expand-kind="more_comments" data-expand-id="more">More</button>',
            '<div id="host"><span style="display:block;width:150px;height:40px">LABEL</span></div>',
        )
        script = (
            '<script>document.getElementById("host").attachShadow({mode:"open"}).innerHTML=\'<div data-expand-kind="more_comments" data-expand-id="more" style="display:inline-block;width:150px;height:40px"><slot></slot></div>\';</script>'
            + script.replace(
                "document.querySelectorAll('[data-expand-kind]')",
                "document.getElementById('host').shadowRoot.querySelectorAll('[data-expand-kind]')",
            )
        )
    elif kind == "hidden-before-capture":
        markup = markup.replace(
            '<button data-expand-kind="more_comments" data-expand-id="more">More</button>',
            '<div data-expand-kind="more_comments" data-expand-id="more" style="width:150px;height:40px"><span style="display:block;width:150px;height:40px;visibility:visible">More</span></div>',
        )
        original = facebook._PlaywrightSession._progress_signature

        def changed_before_capture(session):
            session._page.evaluate(
                "document.querySelector('[data-expand-kind]').style.visibility='hidden'"
            )
            return original(session)

        monkeypatch.setattr(
            facebook._PlaywrightSession, "_progress_signature", changed_before_capture
        )
    else:
        script += "<div style=\"position:fixed;inset:0;z-index:99999\" onclick=\"document.querySelector('[data-comment-id] [data-fb-role=comment-body]').textContent='WRONG'\"></div>"
    markup = markup.replace("</body>", script + "</body>")
    job, stored = run(tmp_path, monkeypatch, markup, "chromium")
    assert job["error"] is None, job
    texts = [r["text"] for r in stored if r["kind"] == "comment"]
    blocked = kind in {"overlay", "hidden-before-capture"}
    assert texts[-1] == ("INITIAL" if blocked else "EXPANDED")
    assert "WRONG" not in texts
    assert job["coverage_status"] == ("partial" if blocked else "scope_satisfied"), job


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
                "select",
                "option",
                "optgroup",
                "frameset",
                "frame",
                "embed",
            )
        ],
        "<div popover>TEXT</div>",
        '<svg><noscript><g data-page-state="login_wall"></g></noscript></svg>',
        "<svg><template></template></svg>",
        "<svg><slot></slot></svg>",
        "<math><mtext><mi>TEXT</mi></mtext></math>",
        "<p><summary></summary></p>",
        "<table><math></math></table>",
        "<p><details></details></p>",
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
