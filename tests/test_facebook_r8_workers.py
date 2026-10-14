"""Worker attach re-entry during the fixed shutdown deadline."""

# ruff: noqa: E501

from types import SimpleNamespace

from test_facebook import _synthetic_post_html
from test_facebook_review import browser_fixture, finish
from test_facebook_round5 import POST_PATH, submit
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import facebook


def test_r8_verifier_reentrant_attach():
    session = object.__new__(facebook._PlaywrightSession)
    session._transport_error = None
    session._closing_targets = {"first"}
    session._browser = SimpleNamespace(is_connected=lambda: True)
    session._page = SimpleNamespace(is_closed=lambda: False, wait_for_timeout=lambda _: None)
    session._egress = SimpleNamespace(record_proxy_protocol_refusal=lambda **_: None)
    session.workers_blocked = 0
    polls = []

    def send(method, params=None):
        if method == "Target.getTargets":
            polls.append(method)
            if len(polls) == 1:
                session._on_worker_attached(
                    {
                        "targetInfo": {"type": "shared_worker", "targetId": "second"},
                        "waitingForDebugger": True,
                    }
                )
                return {"targetInfos": [{"targetId": "second"}]}
            return {"targetInfos": []}
        return {"success": True}

    session._worker_cdp = SimpleNamespace(send=send)
    session._verify_target_shutdown()
    assert session._transport_error is None
    assert not session._closing_targets
    assert len(polls) >= 2


def test_r8_verifier_unexpected_failure_is_guard_error():
    session = object.__new__(facebook._PlaywrightSession)
    session._transport_error = None
    session._closing_targets = {"first"}
    session._browser = SimpleNamespace(is_connected=lambda: True)
    session._target_gone = lambda _: (_ for _ in ()).throw(RuntimeError("page-secret"))
    session._verify_target_shutdown()
    assert session._transport_error.detail.code.value == "E_PROTOCOL_MISMATCH"
    assert "page-secret" not in session._transport_error.detail.message


def test_r8_interval_workers_eight_runs(tmp_path, monkeypatch):
    script = """<script>
    const u=URL.createObjectURL(new Blob(["fetch('/escape-interval')"],{type:'text/javascript'}));
    const start=Date.now(), timer=setInterval(()=>{new Worker(u);if(Date.now()-start>2500)clearInterval(timer)},10);
    document.querySelector('button').onclick=()=>setTimeout(()=>{
      document.querySelector('[data-comment-id] [data-fb-role="comment-body"]').textContent+='!';
      document.querySelector('button').remove();},500);
    </script>"""
    baseline = None
    for i in range(9):
        run_script = script.replace(">2500", ">7000") if i > 4 else script
        markup = _synthetic_post_html(
            comments=[{"id": "c1", "text": "First"}], expand_controls=[{"id": "more"}]
        ).replace(
            "</body>",
            (run_script if i else run_script.replace("new Worker(u);", ""))
            + '<img src="/hold"></body>',
        )
        with browser_fixture(
            tmp_path / str(i),
            monkeypatch,
            {POST_PATH: markup, "/hold": "ok"},
            delays={"/hold": 0.3},
            unsandboxed=False,
        ) as (ctx, project, permit, _, hits, _, driver):
            original = driver.open_session
            sessions = []

            def opened(*args, original=original, sessions=sessions, **kwargs):
                session = original(*args, **kwargs)
                sessions.append(session)
                return session

            monkeypatch.setattr(driver, "open_session", opened)
            job = finish(ctx, submit(ctx, project, permit))
            assert job["error"] is None, (i, job)
            outcome = (
                job["state"],
                job["coverage_status"],
                job["missing_fields"],
                job["next_action"] if "next_action" in job else None,
            )
            if i == 0:
                baseline = outcome
            assert outcome == baseline, (i, outcome, baseline)
            if i:
                assert sessions[-1].workers_blocked > 0
            assert not any("escape" in path or upgrade for path, upgrade in hits), hits
