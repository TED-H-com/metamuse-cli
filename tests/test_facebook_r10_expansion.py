"""DOM progress, never request attribution, determines expansion outcomes."""

# ruff: noqa: E501

import ast
import inspect
import time

import pytest
from browser_deadline import browser_deadline
from test_facebook import _synthetic_post_html
from test_facebook_review import browser_fixture, finish
from test_facebook_round5 import POST_PATH, rows, submit
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import facebook


def test_r10_no_debugger_or_expansion_network_authority():
    # Inspect all our session/capture source constants, including JSON CDP
    # commands. Worker waitForDebuggerOnStart is a Target pause, not Debugger.
    from metamuse import _facebook_capture, _facebook_dom

    for module in (facebook, _facebook_capture, _facebook_dom):
        tree = ast.parse(inspect.getsource(module))
        strings = [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        assert not any("Debugger." in s or "setAsyncCallStackDepth" in s for s in strings)
    from types import CodeType

    pending = [
        f.__code__ for f in vars(facebook._PlaywrightSession).values() if hasattr(f, "__code__")
    ]
    runtime_strings = []
    while pending:
        for value in pending.pop().co_consts:
            if isinstance(value, CodeType):
                pending.append(value)
            elif isinstance(value, str):
                runtime_strings.append(value)
    assert not any("Debugger." in s or "setAsyncCallStackDepth" in s for s in runtime_strings)
    source = inspect.getsource(facebook._PlaywrightSession)
    for removed in (
        "_note_expansion_request",
        "_note_expansion_response",
        "_expansion_network_requests",
        "_expansion_response_status",
        "_active_control",
        "Network.requestWillBeSent",
        "Network.responseReceived",
    ):
        assert removed not in source, removed


REQUESTS = {
    "fetch": "fetch('/api/fail')",
    "xhr": "const x=new XMLHttpRequest();x.open('GET','/api/fail');x.send()",
    "promise": "Promise.resolve().then(()=>fetch('/api/fail'))",
    "await": "(async()=>{await Promise.resolve();await fetch('/api/fail')})()",
    "timer": "setTimeout(()=>fetch('/api/fail'),0)",
    "channel": "const m=new MessageChannel();m.port1.onmessage=()=>fetch('/api/fail');m.port2.postMessage(1)",
}

PROGRESS = """
const s=document.querySelector('section');
s.insertAdjacentHTML('beforeend','<article data-fb-role="comment" data-comment-id="c2"><div data-fb-role="comment-body">SECOND</div></article>');
document.querySelector('[data-fb-role=post]').setAttribute('data-comments-exhausted','true');
document.querySelector('[data-fb-role=post]').setAttribute('data-comment-count','2');
document.querySelector('button').remove();
"""


def page(click="", extra=""):
    return _synthetic_post_html(
        comments=[{"id": "c1", "text": "INITIAL"}],
        expand_controls=[{"id": "more"}],
        comments_exhausted=False,
    ).replace(
        "</body>",
        "<script>document.querySelector('button').onclick=()=>{"
        + click
        + "};"
        + extra
        + "</script></body>",
    )


def outcome(tmp_path, monkeypatch, markup, *, max_bytes=1_000_000):
    with browser_fixture(
        tmp_path,
        monkeypatch,
        {POST_PATH: markup, "/api/fail": (500, None), "/api/noop": ""},
        unsandboxed=False,
        read_timeout=2,
    ) as (ctx, project, permit, _, hits, *_):
        started = time.monotonic()
        job = finish(ctx, submit(ctx, project, permit, max_bytes=max_bytes))
        assert job["error"] is None, job
        assert time.monotonic() - started < 45  # independent wrapper is 90s
        return job, rows(ctx), list(hits)


@pytest.mark.parametrize("request_kind", REQUESTS)
@browser_deadline
def test_r10_failed_request_without_dom_progress(tmp_path, monkeypatch, request_kind, request):
    job, stored, hits = outcome(tmp_path, monkeypatch, page(REQUESTS[request_kind]))
    assert job["stop_reason"] == "expansion_timeout", job
    assert job["state"] == "partial"
    assert job["coverage_status"] == "partial"
    assert "expansion_failed" not in job["missing_fields"]
    assert "expansion_progress_timeout" in job["missing_fields"]
    assert job["observed_scope"]["steps"][0]["comments_empty"] is False
    assert len(stored) == 2 and stored[-1]["text"] == "INITIAL"
    assert any(p == "/api/fail" for p, _ in hits)


@pytest.mark.parametrize("collateral", ["ancestor", "capture", "flood"])
@browser_deadline
def test_r10_collateral_and_flood_do_not_decide_outcome(tmp_path, monkeypatch, collateral, request):
    extra = {
        "ancestor": "document.querySelector('section').addEventListener('click',()=>fetch('/api/fail'))",
        "capture": "document.addEventListener('click',()=>fetch('/api/fail'),true)",
        "flood": "",
    }[collateral]
    click = (
        "for(let i=0;i<300;i++)fetch('/api/noop?i='+i);" if collateral == "flood" else ""
    ) + PROGRESS
    # HTTP/1.0 closes each response: 300 full TLS handshakes alone consume
    # about 1.15 MB. Allow 8 MiB for this DOM-progress test, including wire
    # overhead and structured captures; dedicated 24 KB tests cover cutoff.
    budget = 8 * 1024 * 1024 if collateral == "flood" else 1_000_000
    expected, expected_rows, _ = outcome(
        tmp_path / "control", monkeypatch, page(PROGRESS), max_bytes=budget
    )
    job, stored, _ = outcome(tmp_path / "attack", monkeypatch, page(click, extra), max_bytes=budget)
    assert (job["state"], job["coverage_status"], job["missing_fields"]) == (
        expected["state"],
        expected["coverage_status"],
        expected["missing_fields"],
    )
    assert stored == expected_rows


@pytest.mark.parametrize("where", ["inline", "timer", "click"])
@browser_deadline
def test_r10_debugger_statements_do_not_pause(tmp_path, monkeypatch, where, request):
    extra = {"inline": "debugger;", "timer": "setTimeout(()=>{debugger;},0)", "click": ""}[where]
    job, stored, _ = outcome(
        tmp_path, monkeypatch, page(("debugger;" if where == "click" else "") + PROGRESS, extra)
    )
    assert job["state"] == "succeeded", job
    assert [r["text"] for r in stored if r["kind"] == "comment"] == ["INITIAL", "SECOND"]


@browser_deadline
def test_r10_async_hidden_500(tmp_path, monkeypatch):
    """DOM evidence is page content: false exhaustion before a later failed
    request is indistinguishable from truthful exhaustion."""
    job, stored, hits = outcome(tmp_path, monkeypatch, page(PROGRESS + REQUESTS["promise"]))
    assert job["state"] == "succeeded", job
    assert job["coverage_status"] == "scope_satisfied"
    assert [r["text"] for r in stored if r["kind"] == "comment"] == ["INITIAL", "SECOND"]
    assert any(p == "/api/fail" for p, _ in hits)
