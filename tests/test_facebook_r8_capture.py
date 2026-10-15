"""Hostile page code and Chromium rendering parity, loopback only."""

# ruff: noqa: E501

import json

import pytest
from browser_deadline import browser_deadline
from test_facebook import _synthetic_post_html
from test_facebook_review import browser_fixture, finish
from test_facebook_round5 import POST_PATH, fixture_job, rows, submit
from test_facebook_round6 import reset_seams  # noqa: F401

CASES = {
    "void-hidden": ("<bgsound hidden><span>VISIBLE</span>", True),
    "math-marker": ("<math><mtext data-metamuse-display-none>VISIBLE</mtext></math>", True),
    "svg-marker": ("<svg><text data-metamuse-display-none>VISIBLE</text></svg>", True),
    "ordinary": ("<span>VISIBLE</span>", True),
    "opacity": ('<span style="opacity:0">VISIBLE</span>', True),
    "offscreen": ('<span style="position:absolute;left:-99999px">VISIBLE</span>', True),
    "zero-clipped": (
        '<span style="display:block;width:0;height:0;overflow:hidden">VISIBLE</span>',
        True,
    ),
    "auto": (
        '<div style="content-visibility:auto;position:absolute;top:99999px"><span>VISIBLE</span></div>',
        True,
    ),
    "aria": ('<span aria-hidden="true">VISIBLE</span>', True),
    "hidden-initial": ('<span hidden style="display:initial">VISIBLE</span>', True),
    "hidden-unset": ('<span hidden style="display:unset">VISIBLE</span>', True),
    "hidden-revert-layer": ('<span hidden style="display:revert-layer">VISIBLE</span>', False),
    "hidden-revert": ('<span hidden style="display:revert">VISIBLE</span>', True),
    "hidden-invalid": ('<span hidden style="display:invalid-token">VISIBLE</span>', False),
    "invalid-last": (
        '<span style="visibility:visible;visibility:invalid-token">VISIBLE</span>',
        True,
    ),
    "hidden-override": ('<span hidden style="display:inline">VISIBLE</span>', True),
    "display-none": ('<span style="display:none">VISIBLE</span>', False),
    "hidden": ("<span hidden>VISIBLE</span>", False),
    "until-found": ('<span hidden="until-found">VISIBLE</span>', False),
    "cv-hidden": (
        '<span style="content-visibility:hidden;display:block"><span>VISIBLE</span></span>',
        False,
    ),
    "details": ("<details><summary></summary><span>VISIBLE</span></details>", False),
    "details-open": ("<details open><summary></summary><span>VISIBLE</span></details>", True),
    "summary": ("<details><summary>VISIBLE</summary>HIDDEN</details>", True),
    "noscript": ("<noscript><span>VISIBLE</span></noscript>", False),
    "template": ("<template><span>VISIBLE</span></template>", False),
    "visibility-hidden": ('<span style="visibility:hidden">VISIBLE</span>', False),
    "collapse": ('<span style="visibility:collapse">VISIBLE</span>', False),
    "visible-child": (
        '<span style="visibility:hidden">HIDDEN<span style="visibility:visible">VISIBLE</span></span>',
        True,
    ),
    "contents": ('<span style="display:contents">VISIBLE</span>', True),
    "bgsound": (
        '<span style="visibility:hidden"><b style="visibility:visible"><bgsound></b>VISIBLE</span>',
        False,
    ),
    "foreign-input": (
        '<span style="visibility:hidden"><svg><input></input></svg>VISIBLE</span>',
        False,
    ),
    "noscript-marker": (
        "<noscript><span data-metamuse-display-none><span></noscript>VISIBLE",
        True,
    ),
}


def run(tmp_path, monkeypatch, markup, driver):
    if driver == "fixture":
        ctx, job = fixture_job(tmp_path, markup)
        return job, rows(ctx)
    with browser_fixture(
        tmp_path, monkeypatch, {POST_PATH: markup}, unsandboxed=False, read_timeout=300
    ) as (
        ctx,
        project,
        permit,
        _,
        _,
        _,
        _,
    ):
        job = finish(ctx, submit(ctx, project, permit))
        return job, rows(ctx)


@pytest.mark.parametrize("case", list(CASES))
@browser_deadline
def test_r8_rendering_table_parity(tmp_path, monkeypatch, case, request):
    inner, visible = CASES[case]
    markup = _synthetic_post_html(post_text="BODY").replace(
        ">BODY</div>", ">LEAD" + inner + "</div>"
    )
    outcomes = []
    for driver in ["fixture", "chromium"]:
        job, stored = run(tmp_path / driver, monkeypatch, markup, driver)
        if driver == "fixture" and case == "foreign-input":
            assert (job.get("error") or {}).get("code") == "E_SELECTOR_DRIFT", job
            assert job["error"]["message"] == "Facebook structured capture could not be completed"
            assert stored == []
            continue
        assert job["error"] is None, job
        text = stored[0]["text"]
        assert ("VISIBLE" in text) == visible, (driver, case, text)
        assert "HIDDEN" not in text, (driver, case, text)
        outcomes.append(
            (
                job["state"],
                job["coverage_status"],
                job["missing_fields"],
                [(r["native_id"], r["text"]) for r in stored],
            )
        )
    if case != "foreign-input":
        assert outcomes[0] == outcomes[1]


TAMPERS = {
    "remove": "const o=Element.prototype.removeAttribute;Element.prototype.removeAttribute=function(n){if(n.toLowerCase().startsWith('data-metamuse-'))return;return o.call(this,n)};",
    "style": "const g=getComputedStyle;window.getComputedStyle=n=>n.matches('[data-comment-id]')?{display:'none',visibility:'hidden'}:n.id==='fake-done'?{display:'block',visibility:'visible'}:g(n);",
    "serialize": "const d=Object.getOwnPropertyDescriptor(Element.prototype,'outerHTML');Object.defineProperty(Element.prototype,'outerHTML',{get(){return this.tagName==='HTML'&&!this.isConnected?'FAKE':d.get.call(this)}});",
    "constructor": "customElements.define('x-tamper',class extends HTMLElement{constructor(){super();if(!this.isConnected){let b=this.getRootNode().querySelector('[data-fb-role=post-body]');if(b)b.textContent='FORGED'}}});",
    "callback": "customElements.define('x-hider',class extends HTMLElement{static get observedAttributes(){return ['data-metamuse-visibility']}attributeChangedCallback(){if(!this.isConnected&&!this.hasAttribute('data-metamuse-display-none'))this.setAttribute('data-metamuse-display-none','')}});",
    "remove-node": "customElements.define('x-tamper',class extends HTMLElement{constructor(){super();if(!this.isConnected)this.getRootNode().querySelector('[data-comment-id]')?.remove()}});",
    "namespace": "document.querySelector('[data-fb-role=post-body] span').setAttributeNS(null,'DATA-METAMUSE-DISPLAY-NONE','');",
    "walker": "Element.prototype.checkVisibility=()=>false;window.getComputedStyle=()=>({display:'none',visibility:'hidden'});Object.defineProperty(Node.prototype,'childNodes',{get(){return []}});Object.defineProperty(Element.prototype,'shadowRoot',{get(){return null}});HTMLSlotElement.prototype.assignedNodes=()=>[];",
}
TAMPERS["combined"] = "".join(TAMPERS[k] for k in ["remove", "style", "constructor", "callback"])


@pytest.mark.parametrize("tamper", list(TAMPERS))
@browser_deadline
def test_r8_capture_cannot_be_forged(tmp_path, monkeypatch, tamper, request):
    markup = (
        _synthetic_post_html(post_text="BODY", comments=[{"id": "c1", "text": "COMMENT"}])
        .replace(
            ">BODY</div>",
            ">Lead<span data-metamuse-display-none> VISIBLE</span><x-hider> PAINTED</x-hider></div>",
        )
        .replace("</body>", "<x-tamper></x-tamper></body>")
    )
    expected = "Lead VISIBLE PAINTED"
    if tamper == "style":
        markup = (
            markup.replace('data-comment-count="1"', 'data-comment-count="0"')
            .replace('data-comments-exhausted="true"', 'data-comments-exhausted="false"')
            .replace(
                "</body>",
                '<div id="fake-done" style="display:none" data-comments-exhausted="true"></div></body>',
            )
        )
    if tamper == "walker":
        markup = markup.replace(
            "</x-hider></div>",
            '</x-hider><span id="shadow-host"><b slot="chosen"> SLOTTED</b></span></div>',
        )
        markup = markup.replace(
            "</body>",
            """<script>document.getElementById("shadow-host").attachShadow({mode:"open"}).innerHTML='<span> SHADOW</span><slot name="chosen"></slot>';</script></body>""",
        )
        expected += " SHADOW SLOTTED"
    results = []
    for label, script in [("control", ""), ("tamper", TAMPERS[tamper])]:
        page = markup.replace("</body>", "<script>" + script + "</script></body>")
        job, stored = run(tmp_path / label, monkeypatch, page, "chromium")
        assert job["error"] is None, job
        results.append(
            (
                job["state"],
                job["coverage_status"],
                job["missing_fields"],
                [(r["native_id"], r["text"]) for r in stored],
            )
        )
    assert results[0] == results[1]
    assert results[1][3][0][1] == expected


@pytest.mark.parametrize("mode", ["open", "closed", "declarative", "slotted", "unslotted"])
@browser_deadline
def test_r8_shadow_rendering(tmp_path, monkeypatch, mode, request):
    if mode == "declarative":
        inner = '<span><template shadowrootmode="open">SHADOW</template></span>'
        script = ""
    else:
        inner = '<span id="host"><span slot="chosen">SLOTTED</span><span>UNSLOTTED</span></span>'
        content = '<slot name="chosen"></slot>' if mode in {"slotted", "unslotted"} else "SHADOW"
        script = f"document.getElementById('host').attachShadow({{mode:{json.dumps('closed' if mode == 'closed' else 'open')}}}).innerHTML={json.dumps(content)};"
    markup = (
        _synthetic_post_html(post_text="BODY")
        .replace(">BODY</div>", ">LEAD" + inner + "</div>")
        .replace("</body>", "<script>" + script + "</script></body>")
    )
    job, stored = run(tmp_path, monkeypatch, markup, "chromium")
    assert job["error"] is None, job
    assert "UNSLOTTED" not in stored[0]["text"]
    assert ("SLOTTED" if mode in {"slotted", "unslotted"} else "SHADOW") in stored[0]["text"]
    assert job["coverage_status"] == "scope_satisfied", job


@browser_deadline
def test_r8_barriers_in_every_tree(tmp_path, monkeypatch):
    import pytest
    from facebook_parity import capture_session, captured_parse
    from test_facebook_round5 import parse

    from metamuse import models as m

    with capture_session(monkeypatch) as session:
        for location in (
            "head",
            "meta",
            "hidden",
            "unslotted",
            "template",
            "open-shadow",
            "closed-shadow",
        ):
            marker = '<i data-page-state="login_wall"></i>'
            markup = _synthetic_post_html()
            if location == "head":
                markup = markup.replace(
                    "<head></head><body>", '<head data-page-state="login_wall"></head><body>'
                )
            elif location == "meta":
                markup = markup.replace(
                    "<head></head><body>", '<head><meta data-page-state="login_wall"></head><body>'
                )
            elif location == "template":
                markup = markup.replace("</body>", "<template>" + marker + "</template></body>")
            elif location == "hidden":
                markup = markup.replace("</body>", "<div hidden>" + marker + "</div></body>")
            else:
                content = marker if location.endswith("shadow") else ""
                light = marker if location == "unslotted" else ""
                mode = "closed" if location == "closed-shadow" else "open"
                markup = markup.replace(
                    "</body>",
                    '<div id="host">'
                    + light
                    + '</div><script>document.getElementById("host").attachShadow({mode:'
                    + json.dumps(mode)
                    + "}).innerHTML="
                    + json.dumps(content)
                    + "</script></body>",
                )

            if location in {"head", "meta", "hidden", "template"}:
                with pytest.raises(m.CoreError) as caught:
                    parse(markup)
                assert caught.value.detail.code == m.ErrorCode.AUTH_REQUIRED
            with pytest.raises(m.CoreError) as caught:
                captured_parse(session, markup)
            assert caught.value.detail.code == m.ErrorCode.AUTH_REQUIRED


@pytest.mark.parametrize("kind", ["application/xhtml+xml", "application/xml", "image/svg+xml"])
@browser_deadline
def test_r8_non_html_refused(tmp_path, monkeypatch, kind, request):
    body = (
        _synthetic_post_html()
        .replace("<html>", '<html xmlns="http://www.w3.org/1999/xhtml">')
        .replace("</body>", "<noscript><i>NOT HTML EVIDENCE</i></noscript></body>")
    ).encode()

    def serve(req):
        req.send_response(200)
        req.send_header("Content-Type", kind)
        req.send_header("Content-Length", str(len(body)))
        req.end_headers()
        req.wfile.write(body)

    with browser_fixture(
        tmp_path, monkeypatch, {POST_PATH: serve}, unsandboxed=False, read_timeout=300
    ) as (
        ctx,
        project,
        permit,
        _,
        _,
        _,
        _,
    ):
        job = finish(ctx, submit(ctx, project, permit))
        assert (job.get("error") or {}).get("code") == "E_SELECTOR_DRIFT", job
        assert job["error"]["message"] == "Facebook structured capture could not be completed"
        assert job["state"] == "failed"


@browser_deadline
def test_r8_rendered_control_identity(tmp_path, monkeypatch, request):
    markup = _synthetic_post_html(
        comments=[{"id": "c1", "text": "COMMENT"}], expand_controls=[{"id": "more"}]
    )
    markup = markup.replace(
        "<button ",
        '<button hidden data-expand-id="more" onclick="throw Error(\'WRONG\')">Hidden</button><button ',
        1,
    )
    markup = markup.replace(
        "</body>",
        """<script>document.querySelector('button:not([hidden])').onclick=()=>{
    document.querySelector('[data-comment-id] [data-fb-role="comment-body"]').textContent='EXPANDED';
    document.querySelector('button:not([hidden])').remove();};</script></body>""",
    )
    job, stored = run(tmp_path, monkeypatch, markup, "chromium")
    assert job["error"] is None, job
    assert any(row["text"] == "EXPANDED" for row in stored), stored


@pytest.mark.parametrize("driver", ["fixture", "chromium"])
@browser_deadline
def test_r8_noscript_cannot_fabricate_evidence(tmp_path, monkeypatch, driver, request):
    markup = (
        _synthetic_post_html()
        .replace('data-comments-exhausted="true"', 'data-comments-exhausted="false"')
        .replace(
            "</body>",
            '<div style="display:none"><noscript></div></noscript><div data-comments-exhausted="true"></div></div><noscript><article data-fb-role="comment" data-comment-id="fake"><div data-fb-role="comment-body">FAKE</div></article><div data-comments-exhausted="true"></div></noscript></body>',
        )
    )
    job, stored = run(tmp_path, monkeypatch, markup, driver)
    assert job["error"] is None, job
    assert job["coverage_status"] == "partial"
    assert not any(row["native_id"] == "fake" for row in stored)


def test_r8_fixture_declarative_shadow_refused(tmp_path):
    ctx, job = fixture_job(
        tmp_path,
        _synthetic_post_html().replace(
            "</body>", '<div><template shadowrootmode="open">SHADOW</template></div></body>'
        ),
    )
    assert (job.get("error") or {}).get("code") == "E_SELECTOR_DRIFT", job
    assert job["state"] == "failed"


@browser_deadline
def test_r8_capture_is_read_only(tmp_path, monkeypatch, request):
    from metamuse import facebook

    markup = _synthetic_post_html().replace(
        "</body>",
        """<x-count></x-count><script>
    window.constructed=0;window.reactions=0;window.mutations=0;
    customElements.define('x-count',class extends HTMLElement{
      constructor(){super();window.constructed++}
      static get observedAttributes(){return ['data-capture-test']}
      attributeChangedCallback(){window.reactions++}
    });
    new MutationObserver(list=>window.mutations+=list.length).observe(document,{attributes:true,childList:true,subtree:true});
    </script></body>""",
    )
    with browser_fixture(
        tmp_path, monkeypatch, {POST_PATH: markup}, unsandboxed=False, read_timeout=300
    ) as (
        _,
        _,
        _,
        _,
        _,
        guard,
        driver,
    ):
        session = driver.open_session(
            facebook.SanitizedStorageState(
                cookies=[], origins=[], secret_tokens=(), cookie_hosts=(), dropped_cookie_domains=()
            ),
            guard,
            timeout_ms=300000,
            max_bytes=1000000,
        )
        try:
            session.goto_post(POST_PATH)
            before = session._page.evaluate("() => [constructed,reactions,mutations]")
            for _ in range(3):
                session._capture()
            after = session._page.evaluate("() => [constructed,reactions,mutations]")
            assert after == before
        finally:
            session.close()


@pytest.mark.parametrize("bound", ["nodes", "bytes"])
@browser_deadline
def test_r8_capture_bounds_fixed_error(tmp_path, monkeypatch, bound, request):
    # Generate locally in page script, staying below the HTTP/job byte budget.
    script = (
        "document.body.append(...Array.from({length:66000},()=>document.createElement('i')));"
        if bound == "nodes"
        else "document.body.append('x'.repeat(8388609));"
    )
    markup = _synthetic_post_html().replace("</body>", "<script>" + script + "</script></body>")
    job, stored = run(tmp_path, monkeypatch, markup, "chromium")
    assert (job.get("error") or {}).get("code") == "E_SELECTOR_DRIFT", job
    assert job["error"]["message"] == "Facebook structured capture could not be completed"
    assert job["state"] == "failed"
    assert stored == []


@browser_deadline
def test_r8_closed_shadow_gap_is_partial(tmp_path, monkeypatch, request):
    from metamuse import _facebook_capture

    markup = _synthetic_post_html().replace(
        "</body>",
        '<div id="host"></div><script>document.getElementById("host").attachShadow({mode:"closed"}).textContent="SHADOW";</script></body>',
    )
    original = _facebook_capture.capture

    def capture(cdp):
        send = cdp.send

        def refusing(method, params=None):
            if method == "DOM.resolveNode":
                raise RuntimeError("synthetic inaccessible root")
            return send(method, params)

        cdp.send = refusing
        try:
            return original(cdp)
        finally:
            cdp.send = send

    monkeypatch.setattr(_facebook_capture, "capture", capture)
    job, _ = run(tmp_path, monkeypatch, markup, "chromium")
    assert job["error"] is None, job
    assert job["coverage_status"] == "partial"
    assert "closed_shadow_capture_unavailable" in job["missing_fields"]


@pytest.mark.parametrize(
    "style", ['aria-hidden="true"', 'style="content-visibility:auto;position:absolute;top:99999px"']
)
@browser_deadline
def test_r8_painted_comments_cannot_disappear(tmp_path, monkeypatch, style, request):
    markup = (
        _synthetic_post_html(comments=[{"id": "c1", "text": "PAINTED COMMENT"}])
        .replace('data-comment-id="c1"', 'data-comment-id="c1" ' + style)
        .replace('data-comment-count="1"', 'data-comment-count="0"')
    )
    outcomes = []
    for driver in ["fixture", "chromium"]:
        job, stored = run(tmp_path / driver, monkeypatch, markup, driver)
        assert job["error"] is None, job
        assert any(r["native_id"] == "c1" and r["text"] == "PAINTED COMMENT" for r in stored)
        assert job["coverage_status"] == "partial"
        assert "rendered_count_conflict" in job["missing_fields"]
        outcomes.append(
            (
                job["state"],
                job["coverage_status"],
                job["missing_fields"],
                [(r["native_id"], r["text"]) for r in stored],
            )
        )
    assert outcomes[0] == outcomes[1]


@browser_deadline
def test_r8_closed_root_created_during_capture_is_partial(tmp_path, monkeypatch, request):
    markup = _synthetic_post_html().replace("</body>", '<div id="late-host"></div></body>')
    with browser_fixture(
        tmp_path, monkeypatch, {POST_PATH: markup}, unsandboxed=False, read_timeout=300
    ) as (ctx, project, permit, _, _, _, driver):
        original = driver.open_session

        def opened(*a, **k):
            session = original(*a, **k)
            cdp = session._network_cdp
            send = cdp.send
            injected = False

            def changed(method, params=None):
                nonlocal injected
                result = send(method, params)
                if method == "DOM.getDocument" and not injected:
                    injected = True
                    send(
                        "Runtime.evaluate",
                        {
                            "expression": 'document.getElementById("late-host").attachShadow({mode:"closed"}).textContent="LATE"'
                        },
                    )
                return result

            monkeypatch.setattr(cdp, "send", changed)
            return session

        monkeypatch.setattr(driver, "open_session", opened)
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"] is None, job
        assert job["coverage_status"] == "partial"
        assert "capture_changed_during_observation" in job["missing_fields"]


@pytest.mark.parametrize(
    "wrapper,rendered",
    [
        ('<div aria-hidden="true">MARKER</div>', True),
        ('<div hidden style="display:block">MARKER</div>', True),
        ("<div hidden>MARKER</div>", False),
        ('<div style="display:none">MARKER</div>', False),
        ('<div style="content-visibility:hidden">MARKER</div>', False),
        ('<div style="content-visibility:auto;position:absolute;top:99999px">MARKER</div>', True),
        ("<details><summary>Summary</summary>MARKER</details>", False),
        ("<noscript>MARKER</noscript>", False),
        ("<template>MARKER</template>", False),
        ('<div hidden="until-found">MARKER</div>', False),
        ('<span style="display:contents">MARKER</span>', True),
        (
            '<div style="visibility:hidden"><span style="visibility:visible">MARKER</span></div>',
            True,
        ),
    ],
    ids=[
        "aria",
        "hidden-override",
        "hidden",
        "display-none",
        "cv-hidden",
        "auto",
        "details",
        "noscript",
        "template",
        "until-found",
        "contents",
        "visible-child",
    ],
)
@browser_deadline
def test_r8_completeness_requires_rendering(tmp_path, monkeypatch, wrapper, rendered, request):
    marker = '<i data-comments-exhausted="true"></i>'
    markup = _synthetic_post_html(comments_exhausted=False).replace(
        "</body>", wrapper.replace("MARKER", marker) + "</body>"
    )
    outcomes = []
    for driver in ["fixture", "chromium"]:
        job, stored = run(tmp_path / driver, monkeypatch, markup, driver)
        assert job["error"] is None, job
        assert job["coverage_status"] == ("scope_satisfied" if rendered else "partial"), job
        outcomes.append(
            (
                job["state"],
                job["coverage_status"],
                job["missing_fields"],
                [(r["native_id"], r["text"]) for r in stored],
            )
        )
    assert outcomes[0] == outcomes[1]


@pytest.mark.parametrize("assigned", [False, True])
@browser_deadline
def test_r8_shadow_completeness_requires_composition(tmp_path, monkeypatch, assigned, request):
    slot = "chosen" if assigned else "other"
    content = f'''<div id="host"><i slot="chosen" data-comments-exhausted="true"></i></div>
    <script>document.getElementById('host').attachShadow({{mode:'closed'}}).innerHTML='<slot name="{slot}"></slot>';</script>'''
    markup = _synthetic_post_html(comments_exhausted=False).replace("</body>", content + "</body>")
    job, _ = run(tmp_path, monkeypatch, markup, "chromium")
    assert job["error"] is None, job
    assert job["coverage_status"] == ("scope_satisfied" if assigned else "partial"), job
