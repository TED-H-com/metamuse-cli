"""Strict insertion-mode subset and a comparator that exposes every dimension."""

# ruff: noqa: E501

import copy

import pytest
from facebook_parity import normalise

from metamuse import _facebook_capture as capture
from metamuse import models as m


@pytest.mark.parametrize(
    "dimension", ["tag", "namespace", "attributes", "text", "rendered", "order", "container"]
)
def test_r10_normalise_detects_every_dimension(dimension):
    original = capture.fixture_capture(
        '<html><head><meta name="x"></head><body><span>A</span><b>B</b></body></html>'
    )
    changed = copy.deepcopy(original)
    span = next(n for n in changed if n["tag"] == "span")
    if dimension == "tag":
        span["tag"] = "i"
    elif dimension == "namespace":
        span["ns"] = capture.MATH
    elif dimension == "attributes":
        span["attrs"]["x"] = "changed"
    elif dimension == "text":
        next(n for n in changed if n["text"] == "A")["text"] = "B"
    elif dimension == "rendered":
        span["rendered"] = not span["rendered"]
    elif dimension == "order":
        a, b = [i for i, n in enumerate(changed) if n["tag"] in {"span", "b"}]
        changed[a], changed[b] = changed[b], changed[a]
    else:
        head, body, meta = [
            next(i for i, n in enumerate(changed) if n["tag"] == tag)
            for tag in ("head", "body", "meta")
        ]
        changed[head]["children"].remove(meta)
        changed[body]["children"].insert(0, meta)
    assert normalise(original) != normalise(changed), dimension


def test_r10_normalise_keeps_foreign_scaffold_names():
    nodes = capture.fixture_capture("<div></div>")
    node = next(n for n in nodes if n["tag"] == "div")
    for tag in ("html", "head", "body"):
        node.update(tag=tag, ns=capture.SVG)
        assert any(row[1:3] == (tag, capture.SVG) for row in normalise(nodes)["nodes"])


INSERTION_REFUSALS = [
    "<html><body><span>A</span></body></html>",
    '<head></head><meta name="x"><div>BODY</div>',
    '<html><head></head><meta name="x"><body></body></html>',
    '<html><head></head><script type="application/json">{}</script><body></body></html>',
    "<html><head></head><template><b>T</b></template><body></body></html>",
    "<html><head></head>AFTER_HEAD<body></body></html>",
    "<html><head></head><body></body>AFTER_BODY</html>",
    "<html><head></head><body></body></html><span>AFTER_HTML</span>",
    '<script type="application/json">{"a":"<!-- <script>"}</script><div data-page-state="login_wall"></div>',
    '<script type="application/json">{"a":"<!--"}</script>',
    "<button><button>B</button></button>",
    "<form><form>F</form></form>",
    "<a><a>A</a></a>",
    "<h1><h2>H</h2></h1>",
    "<svg><noscript>N</noscript></svg>",
    "<math><iframe>I</iframe></math>",
    "<p><div>P</div></p>",
    "<ul><li>A<li>B</li></li></ul>",
    "<table><tr><td>T</td></tr></table>",
    "<table><span>FOSTER</span></table>",
    '<image src="x">',
    "<select><option>O</option></select>",
    "<frameset></frameset>",
]


@pytest.mark.parametrize("markup", INSERTION_REFUSALS)
def test_r10_fixture_refuses_insertion_modes(markup):
    with pytest.raises(m.CoreError) as caught:
        capture.fixture_capture(markup)
    assert caught.value.detail.code == m.ErrorCode.SELECTOR_DRIFT
    assert caught.value.detail.message == "Facebook structured capture could not be completed"


def insertion_mode_corpus():
    # Deliberately include every insertion boundary and tokenizer escape spelling.
    for before in ("", "<meta>", "<!--C-->", "TEXT"):
        for middle in (
            "<meta>",
            '<script type="application/json">{}</script>',
            "<template><b>T</b></template>",
            "<style></style>",
            "<link>",
            "<!--C-->",
            "TEXT",
            " ",
        ):
            yield before + "<html><head></head>" + middle + "<body><span>B</span></body></html>"
    for boundary in ("<html>", "<head>", "</head>", "<body>", "</body>", "</html>"):
        for content in (
            "<meta>",
            "<script></script>",
            "<template></template>",
            "<!--C-->",
            "TEXT",
            " ",
        ):
            yield "<html><head></head><body></body></html>".replace(boundary, boundary + content, 1)
    for token in ("<!--", "-->", "<script", "</script", "<ScRiPt", "</ScRiPt"):
        yield '<script type="application/json">' + token + "</script>"
    yield from INSERTION_REFUSALS
    for outside, inside in (
        ("p", "section"),
        ("li", "li"),
        ("dd", "dt"),
        ("option", "optgroup"),
        ("rb", "rt"),
        ("rt", "rp"),
        ("h1", "h2"),
        ("a", "a"),
        ("button", "button"),
        ("form", "form"),
        ("nobr", "nobr"),
        ("table", "tr"),
        ("table", "col"),
        ("table", "td"),
    ):
        yield f"<{outside}><{inside}>TEXT</{inside}></{outside}>"


@pytest.mark.parametrize(
    "markup",
    [
        "TEXT<html><head></head><body></body></html>",
        '<!doctype html><meta name="x"><div>TEXT</div>',
        "<!doctype html><!doctype html><html><head></head><body></body></html>",
    ],
)
def test_r10_fixture_refuses_document_prefix_repairs(markup):
    with pytest.raises(m.CoreError) as caught:
        capture.fixture_capture(markup)
    assert caught.value.detail.code == m.ErrorCode.SELECTOR_DRIFT
