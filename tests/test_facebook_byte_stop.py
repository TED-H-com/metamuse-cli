"""Prompt byte cutoff, with production deadlines far beyond the independent test deadline."""

from browser_deadline import browser_deadline
from test_facebook import _synthetic_post_html
from test_facebook_review import browser_fixture, finish
from test_facebook_round5 import POST_PATH, submit
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import app


def run_byte_stop(tmp_path, monkeypatch, expansion):
    markup = _synthetic_post_html(
        comments=[{"id": "c_1", "text": "First"}],
        expand_controls=[{"id": "more"}] if expansion else [],
    )
    if expansion:
        markup = markup.replace(
            "</body>",
            """<script>
          document.querySelector('button').onclick=() => fetch('/oversize');
          </script></body>""",
        )
    else:
        markup = markup.replace("Synthetic announcement post body.", "X" * 100_000)
    with browser_fixture(
        tmp_path, monkeypatch, {POST_PATH: markup, "/oversize": "X" * 100_000}, unsandboxed=False
    ) as (ctx, project, permit, *_):
        config = ctx.paths.root / "metamuse.toml"
        config.write_text(
            config.read_text().replace("read_timeout_seconds = 30", "read_timeout_seconds = 300")
        )
        ctx = app.AppContext.open(ctx.paths.root)
        job = finish(ctx, submit(ctx, project, permit, max_bytes=24_000))
        error = job["result"]["data"]["steps"][0]["data"]["error"]
        assert error and error["code"] == "E_LIMIT_REACHED", error
        assert job["state"] == "partial"
        assert job["coverage_status"] == "partial"
        assert job["stop_reason"] == "limit_reached"
        assert "byte_budget_exceeded" in job["missing_fields"]


@browser_deadline
def test_r6_byte_limit_interrupts_navigation(tmp_path, monkeypatch):
    run_byte_stop(tmp_path, monkeypatch, False)


@browser_deadline
def test_r6_byte_limit_interrupts_expansion(tmp_path, monkeypatch):
    run_byte_stop(tmp_path, monkeypatch, True)


def test_r6_proxy_byte_cutoff_closes_registered_sockets():
    import socket

    from metamuse import _browser_egress, facebook

    guard = facebook.EgressGuard.for_fixture_test(
        allowed_hosts=("www.facebook.com",), resolver=lambda _: ["127.0.0.1"]
    )
    proxy = _browser_egress.BrowserEgressProxy(guard, max_bytes=1, idle_timeout_s=300)
    sockets = [socket.socketpair() for _ in range(2)]
    try:
        for client, upstream in sockets:
            assert proxy._register_socket(client)
            assert proxy._register_socket(upstream)
        assert not proxy._charge_bytes(2)
        assert proxy.byte_limit_exceeded
        assert all(sock.fileno() == -1 for pair in sockets for sock in pair)
        refused, peer = socket.socketpair()
        try:
            assert not proxy._register_socket(refused)
            assert refused.fileno() == -1
        finally:
            refused.close()
            peer.close()
    finally:
        proxy.close()
        for pair in sockets:
            for sock in pair:
                sock.close()


def test_r6_byte_cutoff_closes_connecting_upstream(monkeypatch):
    import socket
    import threading

    from test_facebook import _allow_loopback_sockets

    from metamuse import _browser_egress, facebook

    _allow_loopback_sockets(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    candidates = []
    target = socket.socket()
    target.bind(("127.0.0.1", 0))
    destination = target.getsockname()
    connect = socket.socket.connect

    def stalled(sock, address):
        if address == destination:
            candidates.append(sock)
            entered.set()
            release.wait(timeout=600)
            raise OSError("synthetic interrupted connect")
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", stalled)
    guard = facebook.EgressGuard.for_fixture_test(
        allowed_hosts=("allowed.test",), resolver=lambda _: ["127.0.0.1"]
    )
    proxy = _browser_egress.BrowserEgressProxy(guard, max_bytes=1, idle_timeout_s=300)
    client = socket.create_connection(("127.0.0.1", proxy.port), timeout=300)
    try:
        client.sendall(f"CONNECT allowed.test:{destination[1]} HTTP/1.1\r\n\r\n".encode())
        assert entered.wait(timeout=30)
        assert not proxy._charge_bytes(2)
        assert candidates[0].fileno() == -1
    finally:
        release.set()
        proxy.close()
        client.close()
        target.close()


@browser_deadline
def test_r6_byte_limit_interrupts_policy_wait(tmp_path, monkeypatch):
    import threading

    from metamuse import safety

    resolving, release = threading.Event(), threading.Event()
    prefix = (
        _synthetic_post_html()
        .replace("</body>", '<img src="https://waiting.example/hold"></body>')
        .encode()
    )
    suffix = b"X" * 100_000

    def streaming(request):
        request.send_response(200)
        request.send_header("Content-Type", "text/html")
        request.send_header("Content-Length", str(len(prefix) + len(suffix)))
        request.end_headers()
        request.wfile.write(prefix)
        request.wfile.flush()
        if resolving.wait(timeout=60):
            try:
                request.wfile.write(suffix)
            except OSError:
                pass

    with browser_fixture(
        tmp_path, monkeypatch, {POST_PATH: streaming}, unsandboxed=False, read_timeout=300
    ) as (ctx, project, permit, _, _, guard, _):
        guard.allowed_hosts = ("www.facebook.com", "waiting.example")

        def resolver(host):
            if host == "waiting.example":
                resolving.set()
                release.wait(timeout=600)
            return ["127.0.0.1"]

        guard.resolver = resolver
        guard._fixture_url_checker = lambda url, purpose: safety.check_fixture_url(
            url, purpose=purpose, resolver=resolver, allowed_hosts=guard.allowed_hosts
        )
        try:
            job = finish(ctx, submit(ctx, project, permit, max_bytes=24_000))
            error = job["result"]["data"]["steps"][0]["data"]["error"]
            assert error and error["code"] == "E_LIMIT_REACHED", error
            assert job["state"] == "partial"
            assert resolving.is_set()
        finally:
            release.set()
