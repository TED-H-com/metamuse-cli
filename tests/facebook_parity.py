# ruff: noqa: E501
"""Differential normalisation and a deterministic, deliberately hostile corpus."""

import ast
import http.server
import json
import random
import re
import threading
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def capture_session(monkeypatch):
    """Read-only browser checks need no store, permit, secrets or TLS key files."""
    import pytest
    from test_facebook import _allow_loopback_sockets, _synthetic_post_html
    from test_facebook_review import URL

    from metamuse import facebook

    if facebook.find_playwright_browser() is None:
        pytest.skip("Playwright or Chromium is not installed")
    _allow_loopback_sockets(monkeypatch)
    for key in facebook.PROXY_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    body = _synthetic_post_html().encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    session = None
    try:
        guard = facebook.EgressGuard.for_fixture_test(
            allowed_hosts=("www.facebook.com", "127.0.0.1"), resolver=lambda _: ["127.0.0.1"]
        )
        driver = facebook.PlaywrightBrowserDriver(
            _test_origin=f"http://127.0.0.1:{server.server_port}"
        )
        state = facebook.SanitizedStorageState(
            cookies=[], origins=[], secret_tokens=(), cookie_hosts=(), dropped_cookie_domains=()
        )
        session = driver.open_session(state, guard, timeout_ms=300000, max_bytes=100000000)
        session.goto_post(URL)
        yield session
    finally:
        try:
            if session is not None:
                session.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


def normalise(nodes):
    # Ignore document/fragment/doctype/comment nodes and empty HTML scaffolding,
    # which Chromium inserts for fragments. Preserve evidence nodes, their light
    # parents, namespace, attributes, text, rendering and both traversal orders.
    kept = {
        i: ordinal
        for ordinal, i in enumerate(
            i
            for i, n in enumerate(nodes)
            if n["kind"] in {1, 3} and not (n["tag"] in {"html", "head", "body"} and not n["attrs"])
        )
    }
    parents = {child: i for i, node in enumerate(nodes) for child in node["children"]}
    records = []
    for i in kept:
        node = nodes[i]
        parent = parents.get(i)
        container = None
        ancestor = parent
        while ancestor is not None:
            if nodes[ancestor]["ns"] == "http://www.w3.org/1999/xhtml" and nodes[ancestor][
                "tag"
            ] in {"head", "body"}:
                container = nodes[ancestor]["tag"]
                break
            ancestor = parents.get(ancestor)
        while parent is not None and parent not in kept:
            parent = parents.get(parent)
        records.append(
            (
                node["kind"],
                node["tag"],
                node["ns"],
                sorted(node["attrs"].items()),
                node["text"],
                node["rendered"],
                kept.get(parent),
                container,
            )
        )
    pending, composed = [0], []
    while pending:
        i = pending.pop()
        if i in kept:
            composed.append(kept[i])
        pending.extend(reversed(nodes[i]["composed"]))
    return {"nodes": records, "composed": composed}


def literal_corpus():
    result = set()
    for path in Path("tests").rglob("*"):
        if path.suffix == ".html":
            result.add(path.read_text())
        elif path.suffix == ".py":
            tree = ast.parse(path.read_text())
            result.update(
                n.value
                for n in ast.walk(tree)
                if isinstance(n, ast.Constant)
                and isinstance(n.value, str)
                and "<" in n.value
                and ">" in n.value
            )
    result.update(
        row["markup"]
        for path in sorted(Path("tests/fixtures/facebook/parity").glob("*.json"))
        for row in json.loads(path.read_text())
    )
    return result


# Every marker/role read by the extractor, plus its DOM identity/count fields.
# The differential test derives the required names from the actual source.
MARKER_VALUES = {
    "data-fb-state": ("login_wall", "checkpoint", "challenge", "rate_limited", "access_denied"),
    "data-page-state": ("login_wall",),
    "data-checkpoint": ("true",),
    "id": ("login_form", "checkpoint_form"),
    "data-testid": ("royal_login_form",),
    "role": ("article",),
    "data-fb-role": (
        "post",
        "post-body",
        "comments-section",
        "comment",
        "reply",
        "comment-body",
        "attachment",
    ),
    "data-ad-preview": ("message",),
    "data-comments-section": ("true",),
    "data-comment-body": ("true",),
    "data-comment-kind": ("reply",),
    "data-comments-exhausted": ("true",),
    "data-fb-post-payload": ("true",),
    "data-expand-kind": ("more_comments", "more_replies"),
    "data-expand-id": ("more",),
    "data-remaining-count": ("many", "1"),
    "data-post-id": ("pfbid0101synth",),
    "data-actor": ("synth.author",),
    "data-post-text": ("POST",),
    "data-author": ("SYNTHETIC",),
    "data-created-at": ("2026-01-01T00:00:00Z",),
    "data-permalink": ("https://www.facebook.com/synth.author/posts/pfbid0101synth",),
    "data-comment-id": ("c1",),
    "data-parent-comment-id": ("c0",),
    "data-mention": ("SYNTHETIC",),
    "data-reaction-count": ("0",),
    "data-comment-count": ("0",),
    "data-share-count": ("0",),
    "data-attachment-url": ("https://www.facebook.com/synthetic.png",),
    "data-attachment-kind": ("image",),
    "alt": ("SYNTHETIC",),
}


def seeded_corpus(size=160):
    rng = random.Random(139009)
    vocabulary = "div span p article section form input button a img ul li table tbody thead tfoot tr td th caption colgroup col template svg math noscript xmp iframe noembed noframes plaintext textarea title details summary dialog head meta slot".split()
    attrs = [
        "",
        " hidden",
        ' hidden="until-found"',
        ' style="display:none"',
        ' style="display:contents"',
        ' style="visibility:hidden"',
        ' style="visibility:visible"',
        ' style="content-visibility:hidden"',
        ' style="content-visibility:auto"',
        " popover",
        ' data-page-state="login_wall"',
        ' data-checkpoint="true"',
        ' data-fb-role="comment" data-comment-id="c1"',
        ' data-comments-exhausted="true"',
        ' data-expand-kind="more_comments"',
        ' role="article" data-post-id="pfbid0101synth"',
    ]
    marker_attrs = [
        f' {name}="{value}"' for name, values in MARKER_VALUES.items() for value in values
    ]
    attrs.extend(marker_attrs)
    # Include every marker in the default subset, independently of RNG draws.
    result = [f"<div{attr}>TEXT</div>" for attr in marker_attrs]
    result.extend(f'<form id="{identity}"></form>' for identity in MARKER_VALUES["id"])
    for _ in range(size):
        tags = [rng.choice(vocabulary) for _ in range(rng.randrange(1, 6))]
        markup = "".join(f"<{tag}{rng.choice(attrs)}>" for tag in tags) + rng.choice(
            ["TEXT", " ", "\n", ""]
        )
        markup += "".join(
            f"</{tag}>" for tag in reversed(tags) if tag not in {"input", "img", "meta", "col"}
        )
        if rng.randrange(5) == 0:
            markup = markup.replace("</", "</span></", 1)
        result.append(markup)
    return result


def class_corpus():
    """Every Appendix B element and the slot/contents ancestor combinations."""
    from metamuse import _facebook_capture as capture

    result = []
    for tag in sorted(capture.CONTENTS_NONE):
        end = "" if tag in capture.VOID else f"FALLBACK</{tag}>"
        markup = f'<{tag} style="display:contents" data-expand-kind="more_comments">{end}'
        if tag == "frame":
            markup = "<html><head></head><frameset>" + markup + "</frameset></html>"
        elif tag == "frameset":
            markup = "<html><head></head>" + markup + "</html>"
        result.append(markup)
    for mode in ("open", "closed"):
        for display in ("none", "contents", "block"):
            result.append(
                '<div><span style="display:contents">SLOTTED</span>'
                f'<template shadowrootmode="{mode}"><div style="display:{display}">'
                "<slot></slot></div></template></div>"
            )
    return result


def as_document(markup):
    # Fixture fragments use the HTML5 body-fragment context. Full documents
    # retain their explicit document/head/body context.
    if re.search(r"<(?:!doctype|html|head|body)(?:\s|>)", markup, re.I):
        return markup
    return "<html><head></head><body>" + markup + "</body></html>"


def captured_parse(session, markup):
    from metamuse import facebook
    from test_facebook_review import URL

    session._page.set_content(as_document(markup), wait_until="load")
    session._network_cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
    html, nodes, gaps = session._capture()
    return facebook.classify_and_parse_observation(
        facebook.PageObservation(
            url=URL, status_code=200, html=html, capture_nodes=nodes, capture_gaps=gaps
        ),
        permalink=facebook.parse_post_permalink(URL),
        require_comments_section=True,
    )
