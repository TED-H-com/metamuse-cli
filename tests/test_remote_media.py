"""Offline fixture tests for bounded remote media (B12).

The synthetic server is loopback-only. URL checks use ``check_fixture_url``.
No test contacts a real site. AT-040 stays blocked.
"""

import io
import json
import logging
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import tomllib
import traceback
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pydantic import ValidationError
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.networking.common import Request
from yt_dlp.networking.exceptions import RequestError
from yt_dlp.utils import ExtractorError

from metamuse import app, jobs, remote_media, safety
from metamuse import models as m

ROOT = Path(__file__).parents[1]
PROVIDER = "fixture-provider"
ACCOUNT = "fixture-account"
LIMITS = {
    "max_urls": 10,
    "max_items": 500,
    "max_bytes": 1_073_741_824,
    "wall_clock_seconds": 900,
}
VTT = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nsynthetic cue\n"
REAL_CHECKER = remote_media._checker_factory


def wav_bytes() -> bytes:
    samples = b"\x00\x00" * 800
    data_size = len(samples)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        8000,
        16000,
        2,
        16,
        b"data",
        data_size,
    )
    return header + samples


class MetamuseFixtureIE(InfoExtractor):
    _VALID_URL = r"https?://(?:media|flip)\.fixture\.test(?::\d+)?/(?P<id>[A-Za-z0-9_-]+)"

    def _real_extract(self, url):
        video_id = self._match_id(url)
        payload = self._download_json(url, video_id, note=False)
        if not isinstance(payload, dict):
            raise ExtractorError("Upstream response could not be parsed", expected=True)
        kind = payload.get("fixture")
        if kind == "login":
            self.raise_login_required("Login required")
        if kind == "challenge":
            raise ExtractorError("Platform challenge", expected=True)
        if kind == "geo":
            self.raise_geo_restricted("Geo restriction")
        if kind == "unavailable":
            raise ExtractorError("Media is unavailable", expected=True)
        if kind == "extractor":
            raise ExtractorError("synthetic extractor failure")
        if kind == "empty":
            raise ExtractorError("This playlist is empty", expected=True)
        return payload


class Resolver:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.rebind = False
        self._flipped = False
        self.extra: dict[str, list[str]] = {}

    def __call__(self, host: str) -> list[str]:
        self.calls.append(host)
        if host in self.extra:
            return self.extra[host]
        if host == "rebind.fixture.test":
            return ["10.1.2.3"]
        if host == "media.fixture.test":
            if self.rebind:
                if self._flipped:
                    return ["169.254.169.254"]
                self._flipped = True
                return ["127.0.0.1"]
            return ["127.0.0.1"]
        return ["203.0.113.9"]


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        self.server.hits.append(
            {
                "path": parsed.path,
                "query": parsed.query,
                "ua": self.headers.get("User-Agent"),
                "cookie": self.headers.get("Cookie"),
                "xff": self.headers.get("X-Forwarded-For"),
            }
        )
        self._dispatch(parsed.path)

    def _dispatch(self, path: str) -> None:
        if path.endswith("/provided.vtt") or path.endswith("/auto.vtt"):
            video, leaf = path.strip("/").split("/")
            kind = "provided" if leaf.startswith("provided") else "auto"
            if (video, kind) in self.server.denied_subs:
                self._send(403, b"no", "text/plain")
                return
            body = self.server.subtitles.get((video, kind), VTT)
            self._send(200, body, "text/vtt")
            return
        if path.endswith("/media.wav"):
            self._send(200, self.server.media, "audio/wav")
            return
        if path.endswith("/media-redirect"):
            port = self.server.server_address[1]
            self.send_response(302)
            self.send_header("Location", f"http://media.fixture.test:{port}/jump/fat-body")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        if path.endswith("/fat-body"):
            self._send(200, self.server.fat, "application/octet-stream")
            return
        if path.endswith("/index.m3u8"):
            self._send(200, b"#EXTM3U\n", "application/vnd.apple.mpegurl")
            return
        video = path.strip("/")
        route = self.server.routes.get(video)
        if route is None:
            self._send(404, b"missing", "text/plain")
            return
        if route["status"] in (301, 302, 303, 307, 308):
            self.send_response(route["status"])
            self.send_header("Location", route["location"])
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return
        body = route["body"]() if callable(route["body"]) else route["body"]
        extra = dict(route.get("headers") or {})
        self._send(route["status"], body, route.get("type", "application/json"), extra)

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
        self.send_response(status)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        return


def info_dict(port: int, video: str, title: str = "Synthetic clip") -> dict:
    base = f"http://media.fixture.test:{port}/{video}"
    return {
        "id": video,
        "title": title,
        "timestamp": 1700000000,
        "channel_id": "channel-1",
        "webpage_url": base,
        "formats": [
            {"url": f"{base}/index.m3u8", "ext": "m3u8", "protocol": "m3u8"},
            {"url": f"{base}/media.wav", "ext": "wav", "protocol": "http"},
        ],
        "subtitles": {"en": [{"url": f"{base}/provided.vtt", "ext": "vtt"}]},
        "automatic_captions": {"en": [{"url": f"{base}/auto.vtt", "ext": "vtt"}]},
    }


def dump(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


@pytest.fixture
def resolver():
    return Resolver()


@pytest.fixture(autouse=True)
def route_checks(monkeypatch, resolver):
    def factory(hosts, _system):
        def check(url, *, purpose):
            return safety.check_fixture_url(
                url, purpose=purpose, resolver=resolver, allowed_hosts=hosts
            )

        return check

    monkeypatch.setattr(remote_media, "_checker_factory", factory)


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail(f"Real DNS attempted: {args!r}")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "gethostbyname", forbidden)


@pytest.fixture(autouse=True)
def egress_guards(monkeypatch):
    from yt_dlp.networking.common import _REQUEST_HANDLERS

    for key, cls in list(_REQUEST_HANDLERS.items()):

        def blocked(self, request, _key=key):
            pytest.fail(f"stock handler {_key} sent a request")

        monkeypatch.setattr(cls, "_send", blocked)

    from curl_cffi.requests import Session

    real_init = Session.__init__
    real_request = Session.request

    def init(self, *args, **kwargs):
        if kwargs.get("impersonate"):
            pytest.fail("session was constructed with impersonation")
        if kwargs.get("trust_env") is not False or kwargs.get("allow_redirects") is not False:
            pytest.fail("session must ignore the environment and must not follow redirects")
        return real_init(self, *args, **kwargs)

    def request(self, *args, **kwargs):
        if "impersonate" in kwargs:
            pytest.fail("request carried an impersonation flag")
        if remote_media.HANDLER_FRAME not in "".join(traceback.format_stack()):
            pytest.fail("curl request bypassed the MetaMuse handler")
        return real_request(self, *args, **kwargs)

    monkeypatch.setattr(Session, "__init__", init)
    monkeypatch.setattr(Session, "request", request)


@pytest.fixture(autouse=True)
def fixture_extractor():
    remote_media.register_test_extractor(MetamuseFixtureIE)
    yield
    remote_media.unregister_test_extractor(MetamuseFixtureIE)


@pytest.fixture
def origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    server.hits = []
    server.media = wav_bytes()
    server.fat = b""
    server.subtitles = {}
    server.denied_subs = set()
    server.titles = {"clip": "Synthetic clip"}
    port = server.server_address[1]

    def page(video: str, **extra) -> bytes:
        payload = info_dict(port, video, title=server.titles.get(video, "Synthetic clip"))
        payload.update(extra)
        return dump(payload)

    server.routes = {
        "clip": {"status": 200, "body": lambda: page("clip")},
        "nosub": {
            "status": 200,
            "body": lambda: page("nosub", subtitles={}, automatic_captions={}),
        },
        "badcue": {"status": 200, "body": lambda: page("badcue", automatic_captions={})},
        "sub403": {"status": 200, "body": lambda: page("sub403")},
        "login": {"status": 200, "body": dump({"fixture": "login"})},
        "challenge": {"status": 200, "body": dump({"fixture": "challenge"})},
        "geo": {"status": 200, "body": dump({"fixture": "geo"})},
        "unavailable": {"status": 200, "body": dump({"fixture": "unavailable"})},
        "extractor": {"status": 200, "body": dump({"fixture": "extractor"})},
        "empty": {"status": 200, "body": dump({"fixture": "empty"})},
        "parser": {"status": 200, "body": b"not-json", "type": "text/plain"},
        "denied": {"status": 403, "body": b"no", "type": "text/plain"},
        "limited": {
            "status": 429,
            "body": b"wait",
            "type": "text/plain",
            "headers": {"Retry-After": "17"},
        },
        "huge": {"status": 200, "body": b"h" * 8000, "type": "text/plain"},
        "bounce": {"status": 302, "location": "http://10.1.2.3/secret"},
        "rebind": {"status": 302, "location": f"http://rebind.fixture.test:{port}/landed"},
    }
    server.subtitles[("badcue", "provided")] = b"WEBVTT\n\n"
    server.denied_subs.add(("sub403", "provided"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def configure(ctx, approvals, *, live=True, timeout=2, media_max=None):
    text = app.DEFAULT_CONFIG
    if live:
        text = text.replace('network_mode = "offline"', 'network_mode = "live"', 1)
    text = text.replace("read_timeout_seconds = 30", f"read_timeout_seconds = {timeout}", 1)
    if media_max is not None:
        text = text.replace("media_max_bytes = 536870912", f"media_max_bytes = {media_max}", 1)
    text = text.replace(
        "[remote_media]", f'[remote_media]\nprovider = "{PROVIDER}"\naccount = "{ACCOUNT}"'
    )
    lines = [text.rstrip("\n")]
    for item in approvals:
        extractor, hosts = item[0], item[1]
        auth_ref = item[2] if len(item) > 2 else None
        lines.append("[[remote_media.approvals]]")
        lines.append(f"extractor = {json.dumps(extractor)}")
        rendered = ", ".join(json.dumps(host) for host in hosts)
        lines.append(f"hosts = [{rendered}]")
        if auth_ref:
            lines.append(f"auth_ref = {json.dumps(auth_ref)}")
    path = ctx.paths.root / "metamuse.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    loaded = app.Config.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    ctx.policy = loaded
    ctx.limits = loaded.limits
    return loaded


def remote_request(project, url, resources, **extra):
    limits = dict(LIMITS)
    limits.update(extra.pop("limits", {}) or {})
    body = {
        "project_id": project,
        "inputs": [{"kind": "url", "url": url}],
        "resources": resources,
        "limits": limits,
        "access_grant_id": extra.pop("access_grant_id", None),
        "request_key": extra.pop("request_key", m.new_id("req")),
    }
    body.update(extra)
    return m.CollectRequest.model_validate(body)


def issue(ctx, project, req, keys):
    target, payload = remote_media.action_parts(req, PROVIDER, ACCOUNT, keys)
    digest = safety.action_digest(target, payload)
    expires = (datetime_from(ctx) + timedelta(days=1)).isoformat()
    created = safety.create_permit(
        ctx,
        m.PermitCreateRequest(
            project_id=project,
            kind="access",
            scope={"operations": ["collect.remote"], "target_digests": [digest]},
            provider=PROVIDER,
            account=ACCOUNT,
            issuer="fixture-operator",
            expires_at=expires,
            max_uses=3,
            request_key=m.new_id("req"),
        ),
    )
    return created.data["id"]


def datetime_from(ctx):
    from datetime import datetime

    return datetime.fromisoformat(ctx.clock())


def url_for(origin, video, query=""):
    port = origin.server_address[1]
    suffix = f"?{query}" if query else ""
    return f"http://media.fixture.test:{port}/{video}{suffix}"


def prepare(
    ctx, project, origin, video, resources, *, approvals=None, limits=None, query="", media_max=None
):
    configure(
        ctx,
        approvals or [("MetamuseFixture", ["media.fixture.test"])],
        media_max=media_max,
    )
    req = remote_request(project, url_for(origin, video, query), resources, limits=limits)
    permit_id = issue(ctx, project, req, ["MetamuseFixture"])
    return req.model_copy(update={"access_grant_id": permit_id}), permit_id


def finish(ctx, req, *, sleep=None):
    env = app.submit_collect(ctx, req)
    assert env.status == "accepted", env.error
    kwargs = {"sleep": sleep} if sleep is not None else {}
    with jobs.Executor(ctx.store, **kwargs) as executor:
        assert executor.run_once()
    return ctx.store.get(m.Job, env.data["job_id"])


def q(ctx, sql, args=()):
    with ctx.store.connection() as conn:
        return conn.execute(sql, args).fetchall()


def jobs_count(ctx) -> int:
    return q(ctx, "SELECT COUNT(*) AS n FROM jobs")[0]["n"]


def paths(origin) -> list[str]:
    return [hit["path"] for hit in origin.hits]


def step_data(job):
    return job.result["data"]["steps"][0]["data"]


def test_ytdlp_params_are_fixed(tmp_path):
    params = remote_media.ytdlp_params(
        tmp_path,
        max_items=3,
        socket_timeout=5,
        max_filesize=9,
        extractor="MetamuseFixture",
    )
    assert params["geo_bypass"] is False
    assert params["postprocessors"] == []
    assert params["fixup"] == "never"
    assert params["ffmpeg_location"] == str(tmp_path / ".no-ffmpeg")
    assert not Path(params["ffmpeg_location"]).exists()
    assert params["external_downloader"] is None
    assert params["proxy"] == ""
    assert params["cookiefile"] is None
    assert params["cookiesfrombrowser"] is None
    assert params["cachedir"] is False
    assert params["usenetrc"] is False
    assert params["netrc_location"] is None
    assert params["netrc_cmd"] is None
    assert params["username"] is None
    assert params["password"] is None
    assert params["videopassword"] is None
    assert params["ap_username"] is None
    assert params["ap_password"] is None
    assert params["client_certificate"] is None
    assert params["client_certificate_key"] is None
    assert params["client_certificate_password"] is None
    assert params["source_address"] is None
    assert params["retries"] == params["fragment_retries"] == params["extractor_retries"] == 0
    assert params["skip_download"] is True
    assert params["noplaylist"] is True
    assert params["ignoreconfig"] is True
    assert params["check_formats"] is False
    assert params["writesubtitles"] is False
    assert params["getcomments"] is False
    assert params["js_runtimes"] == {}
    assert params["remote_components"] == []
    assert params["allowed_extractors"] == ["MetamuseFixture"]
    assert "exec" not in params and "impersonate" not in params
    assert str(tmp_path) in params["outtmpl"]


def test_single_file_format_skips_manifests():
    chosen = remote_media._single_format(
        {
            "formats": [
                {"url": "http://cdn.example/a.m3u8", "ext": "m3u8", "protocol": "m3u8"},
                {"url": "http://cdn.example/a.mpd", "ext": "mpd", "protocol": "http"},
                {
                    "url": "http://cdn.example/a.mp4",
                    "ext": "mp4",
                    "protocol": "https",
                    "fragments": [{}],
                },
                {"url": "http://cdn.example/a.wav", "ext": "wav", "protocol": "http"},
            ]
        }
    )
    assert chosen == ("http://cdn.example/a.wav", "wav")


def test_production_checker_denies_loopback_without_the_fixture_toggle():
    check = REAL_CHECKER(("media.fixture.test",), lambda _host: ["127.0.0.1"])
    with pytest.raises(m.CoreError) as caught:
        check("http://media.fixture.test/clip", purpose="request")
    assert caught.value.detail.code == m.ErrorCode.SCOPE_DENIED
    assert "network policy" in caught.value.detail.message


def test_impersonation_and_proxies_are_refused(tmp_path):
    net = remote_media._Net(
        checker=lambda url, purpose: None,
        budget=remote_media._Budget(0, 10),
        media_max=10,
        timeout=1,
    )
    handler = remote_media._handler_class(net)(
        logger=logging.getLogger("remote-test"),
        headers={},
        timeout=1,
        proxies={"all": "__noproxy__"},
    )
    handler.validate(Request("https://media.fixture.test/clip"))
    with pytest.raises(RequestError) as impersonated:
        handler.validate(
            Request("https://media.fixture.test/clip", extensions={"impersonate": "chrome"})
        )
    assert impersonated.value.cause.detail.code == m.ErrorCode.UNSUPPORTED
    with pytest.raises(RequestError) as proxied:
        handler.validate(
            Request("https://media.fixture.test/clip", proxies={"https": "http://127.0.0.1:9"})
        )
    assert proxied.value.cause.detail.code == m.ErrorCode.SCOPE_DENIED
    del tmp_path


def test_socket_connect_guard_is_active():
    sock = socket.socket()
    try:
        with pytest.raises(BaseException, match="Network access attempted"):
            sock.connect(("127.0.0.1", 9))
    finally:
        sock.close()


def test_remote_capability_permission_follows_allowlist_and_network(ctx):
    def backend():
        rows = app.inspect_capabilities(ctx).data["capabilities"]
        return next(row for row in rows if row["name"] == "metamuse_collect.remote_media")

    def tool():
        rows = app.inspect_capabilities(ctx).data["capabilities"]
        return next(row for row in rows if row["name"] == "metamuse_collect")

    assert backend()["permission"] == "not_configured"
    assert backend()["effective"] is False
    assert tool()["environment"] == "ready"
    assert tool()["effective"] is True
    configure(ctx, [], live=True)
    assert backend()["permission"] == "not_configured"
    assert backend()["effective"] is False
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])], live=False)
    assert backend()["permission"] == "not_configured"
    assert backend()["effective"] is False
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])], live=True)
    row = backend()
    assert row["permission"] == "granted_scope"
    assert row["environment"] == "ready"
    assert row["effective"] is True
    assert tool()["permission"] == "granted_scope"
    assert tool()["effective"] is True


def test_config_allowlist_is_empty_by_default():
    assert app.Config().remote_media.approvals == []
    example = tomllib.loads(
        (ROOT / "docs/design/examples/metamuse.example.toml").read_text(encoding="utf-8")
    )
    assert app.Config.model_validate(example).remote_media.approvals == []
    with pytest.raises(ValidationError):
        app.Config.model_validate(
            {"remote_media": {"approvals": [{"extractor": "generic", "hosts": []}]}}
        )


def test_plugins_are_disabled_before_youtube_dl():
    script = """
import os, sys
os.environ.pop("YTDLP_NO_PLUGINS", None)
import metamuse.remote_media as rm
assert "yt_dlp" not in sys.modules
rm._disable_plugins()
from yt_dlp import YoutubeDL
from yt_dlp.globals import plugin_dirs
assert plugin_dirs.value == []
assert os.environ["YTDLP_NO_PLUGINS"] == "1"
YoutubeDL(
    {"quiet": True, "no_warnings": True, "ignoreconfig": True, "logger": rm._QuietLog()},
    auto_init=False,
)
print("plugins-disabled")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "plugins-disabled" in completed.stdout


def test_scope_is_required_before_a_job(ctx, project, origin):
    before = jobs_count(ctx)
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])], live=False)
    offline = app.submit_collect(
        ctx, remote_request(project, url_for(origin, "clip"), ["media_metadata"])
    )
    assert offline.error.code == m.ErrorCode.SCOPE_DENIED
    assert offline.error.message == "External operations are disabled in offline mode"
    configure(ctx, [])
    empty = app.submit_collect(
        ctx,
        remote_request(project, url_for(origin, "clip"), ["media_metadata"], request_key="empty"),
    )
    assert empty.error.code == m.ErrorCode.SCOPE_REQUIRED
    assert "remote_media.approvals" in empty.error.next_action
    configure(ctx, [("generic", ["media.fixture.test"])])
    generic = app.submit_collect(
        ctx,
        remote_request(project, url_for(origin, "clip"), ["media_metadata"], request_key="generic"),
    )
    assert generic.error.code == m.ErrorCode.SCOPE_DENIED
    assert "Generic and unknown" in generic.error.message
    configure(ctx, [("NotARealExtractor", ["media.fixture.test"])])
    unknown = app.submit_collect(
        ctx,
        remote_request(project, url_for(origin, "clip"), ["media_metadata"], request_key="unknown"),
    )
    assert unknown.error.code == m.ErrorCode.SCOPE_DENIED
    configure(ctx, [("MetamuseFixture", ["other.fixture.test"])])
    host = app.submit_collect(
        ctx,
        remote_request(project, url_for(origin, "clip"), ["media_metadata"], request_key="host"),
    )
    assert host.error.code == m.ErrorCode.SCOPE_DENIED
    assert "allowlist" in host.error.message
    assert "Subdomains are not included" in host.error.next_action
    configure(ctx, [("MetamuseFixture", ["fixture.test"])])
    parent = app.submit_collect(
        ctx,
        remote_request(project, url_for(origin, "clip"), ["media_metadata"], request_key="parent"),
    )
    assert parent.error.code == m.ErrorCode.SCOPE_DENIED
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    missing = app.submit_collect(
        ctx,
        remote_request(project, url_for(origin, "clip"), ["media_metadata"], request_key="grant"),
    )
    assert missing.error.code == m.ErrorCode.SCOPE_REQUIRED
    assert "collect.remote" in missing.error.next_action
    req = remote_request(project, url_for(origin, "clip"), ["media_metadata"], request_key="digest")
    permit = safety.create_permit(
        ctx,
        m.PermitCreateRequest(
            project_id=project,
            kind="access",
            scope={"operations": ["collect.remote"], "target_digests": ["ab" * 32]},
            provider=PROVIDER,
            account=ACCOUNT,
            issuer="fixture-operator",
            expires_at=(datetime_from(ctx) + timedelta(days=1)).isoformat(),
            max_uses=1,
            request_key=m.new_id("req"),
        ),
    )
    denied = app.submit_collect(ctx, req.model_copy(update={"access_grant_id": permit.data["id"]}))
    assert denied.error.code == m.ErrorCode.SCOPE_DENIED
    assert jobs_count(ctx) == before
    assert origin.hits == []


def test_other_url_shapes_stay_unsupported(ctx, project):
    before = jobs_count(ctx)
    comments = app.submit_collect(
        ctx,
        remote_request(
            project, "https://example.invalid/post", ["comments"], request_key="comments"
        ),
    )
    mixed = app.submit_collect(
        ctx,
        m.CollectRequest.model_validate(
            {
                "project_id": project,
                "inputs": [
                    {"kind": "url", "url": "https://example.invalid/post"},
                    {"kind": "text", "text": "local"},
                ],
                "resources": ["media_metadata"],
                "limits": LIMITS,
                "access_grant_id": None,
                "request_key": "mixed",
            }
        ),
    )
    both = app.submit_collect(
        ctx,
        remote_request(
            project,
            "https://example.invalid/post",
            ["media_metadata", "comments"],
            request_key="both",
        ),
    )
    for env in (comments, mixed, both):
        assert env.error.code == m.ErrorCode.UNSUPPORTED
        assert "B12" in env.error.next_action and "B13" in env.error.next_action
    assert jobs_count(ctx) == before


def test_missing_extra_does_not_block_local_import(ctx, project, tmp_path, monkeypatch):
    monkeypatch.setattr(remote_media, "dependency_available", lambda: False)
    status = app.inspect_capabilities(ctx).data["capabilities"]
    collect_row = next(row for row in status if row["name"] == "metamuse_collect")
    assert collect_row["environment"] == "ready"
    assert collect_row["effective"] is True
    backend = next(row for row in status if row["name"] == "metamuse_collect.remote_media")
    assert backend["environment"] == "missing_dependency"
    assert backend["effective"] is False
    remote = app.submit_collect(
        ctx, remote_request(project, "https://example.invalid/clip", ["media_metadata"])
    )
    assert remote.error.code == m.ErrorCode.DEPENDENCY_MISSING
    assert "yt-dlp==2026.8.19" in remote.error.next_action
    root = tmp_path / "imports"
    root.mkdir()
    path = root / "one.jsonl"
    path.write_text(
        json.dumps({"platform": "generic", "kind": "comment", "native_id": "n", "text": "local"})
        + "\n",
        encoding="utf-8",
    )
    lines = []
    for line in app.DEFAULT_CONFIG.splitlines():
        lines.append(line)
        if line.startswith("telemetry_enabled"):
            lines.append(f"import_roots = [{json.dumps(str(root))}]")
    config_path = ctx.paths.root / "metamuse.toml"
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    loaded = app.Config.model_validate(tomllib.loads(config_path.read_text(encoding="utf-8")))
    ctx.policy = loaded
    ctx.limits = loaded.limits
    from metamuse import collect

    env = collect.submit_collect(
        ctx,
        m.CollectRequest.model_validate(
            {
                "project_id": project,
                "inputs": [{"kind": "local_file", "path": str(path), "format": "jsonl"}],
                "resources": ["comments"],
                "limits": LIMITS,
                "access_grant_id": None,
                "request_key": "local-while-remote-missing",
            }
        ),
    )
    assert env.status == "accepted", env.error
    with jobs.Executor(ctx.store) as executor:
        assert executor.run_once()
    job = ctx.store.get(m.Job, env.data["job_id"])
    assert job.state == "succeeded"
    assert q(ctx, "SELECT collection_method FROM sources")[0]["collection_method"] == "file_import"


def test_auth_ref_is_rejected_until_core_adds_it(ctx, project):
    env = app.submit_collect(
        ctx,
        {
            "project_id": project,
            "inputs": [{"kind": "url", "url": "https://example.invalid/clip"}],
            "resources": ["media_metadata"],
            "limits": LIMITS,
            "access_grant_id": None,
            "request_key": "auth",
            "auth_ref": "auth_" + "a" * 32,
        },
    )
    assert env.error.code == m.ErrorCode.INPUT_INVALID
    assert jobs_count(ctx) == 0


def test_fixture_collection_stays_on_the_metamuse_handler(
    ctx, project, origin, monkeypatch, caplog
):
    assert shutil.which("ffprobe")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("all_proxy", "http://127.0.0.1:9")
    seen = []
    monkeypatch.setattr(remote_media, "_observer", seen.append)
    caplog.set_level(logging.DEBUG)
    req, permit_id = prepare(
        ctx,
        project,
        origin,
        "clip",
        ["media_metadata", "subtitles", "media_file"],
        query="sig=SECRET",
    )
    job = finish(ctx, req)
    assert job.state == "succeeded"
    assert job.coverage_status == "scope_satisfied"
    assert job.stop_reason == "input_exhausted"
    assert job.error is None
    assert job.operation == "collect.remote_media"
    assert remote_media.LAST_DIRECTOR_KEYS == ["MetaMuse"]
    assert seen and {item["peer"] for item in seen} == {"127.0.0.1"}
    assert all("SECRET" not in item["path"] and "sig" not in item["path"] for item in seen)
    assert "/clip/index.m3u8" not in paths(origin)
    assert paths(origin).count("/clip/media.wav") == 1
    assert paths(origin).count("/clip/provided.vtt") == 1
    assert paths(origin).count("/clip/auto.vtt") == 1
    agents = {hit["ua"] for hit in origin.hits}
    assert len(agents) == 1 and agents != {None}
    assert all(hit["cookie"] is None and hit["xff"] is None for hit in origin.hits)
    source = q(
        ctx,
        "SELECT platform, native_scope, native_id, collection_method, upstream_collection_method, "
        "original_url, canonical_url FROM sources WHERE native_id=?",
        ("clip",),
    )[0]
    assert source["platform"] == "metamusefixture"
    assert source["native_scope"] == "channel-1"
    assert source["collection_method"] == "yt-dlp"
    assert source["upstream_collection_method"] == "MetamuseFixture"
    assert "SECRET" in source["original_url"]
    assert source["upstream_collection_method"] != "manual"
    revision = q(ctx, "SELECT text, raw_time, evidence FROM item_revisions")[0]
    assert revision["text"] == "Synthetic clip"
    assert revision["raw_time"] == "1700000000"
    evidence = json.loads(revision["evidence"])
    assert evidence["collection_method"] == "yt-dlp"
    assert evidence["upstream_collection_method"] == "MetamuseFixture"
    assert evidence["upstream_timestamp"] == "1700000000"
    cues = q(ctx, "SELECT language, method FROM transcript_segments ORDER BY method")
    assert [(row["language"], row["method"]) for row in cues] == [
        ("en", "auto"),
        ("en", "provided"),
    ]
    assert q(ctx, "SELECT state, mime FROM assets")[0]["state"] == "ready"
    assert q(ctx, "SELECT mime FROM assets")[0]["mime"] == "audio/wav"
    assert q(ctx, "SELECT kind FROM items")[0]["kind"] == "post"
    events = q(ctx, "SELECT kind, detail FROM events")
    assert events
    assert all("SECRET" not in row["detail"] for row in events)
    assert "SECRET" not in caplog.text
    assert not (ctx.paths.root / "staging" / job.id).exists()
    assert ctx.store.get(m.Permit, permit_id).used == 1
    side = q(ctx, "SELECT side_effect_class FROM job_steps")[0]["side_effect_class"]
    assert side == "safe_read"


def test_metadata_only_does_not_download_or_probe(ctx, project, origin, monkeypatch):
    def forbid(*args, **kwargs):
        pytest.fail(f"unexpected process {args!r}")

    monkeypatch.setattr(subprocess, "Popen", forbid)
    req, _permit = prepare(ctx, project, origin, "clip", ["media_metadata"])
    job = finish(ctx, req)
    assert job.state == "succeeded" and job.coverage_status == "scope_satisfied"
    assert "/clip/media.wav" not in paths(origin)
    assert "/clip/provided.vtt" not in paths(origin)
    assert q(ctx, "SELECT COUNT(*) AS n FROM assets")[0]["n"] == 0
    assert q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments")[0]["n"] == 0


def test_subtitles_do_not_download_media(ctx, project, origin):
    req, _permit = prepare(ctx, project, origin, "clip", ["media_metadata", "subtitles"])
    job = finish(ctx, req)
    assert job.state == "succeeded"
    assert "/clip/media.wav" not in paths(origin)
    assert paths(origin).count("/clip/provided.vtt") == 1
    assert q(ctx, "SELECT COUNT(*) AS n FROM assets")[0]["n"] == 0
    assert q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments")[0]["n"] == 2


def test_missing_or_unusable_subtitles_are_partial_and_not_asr(ctx, project, origin):
    req, _permit = prepare(ctx, project, origin, "nosub", ["media_metadata", "subtitles"])
    missing = finish(ctx, req)
    assert missing.state == "partial" and missing.coverage_status == "partial"
    assert missing.missing_fields == ["subtitles"]
    assert q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments")[0]["n"] == 0
    bad, _permit = prepare(ctx, project, origin, "badcue", ["subtitles"], limits=None)
    bad = bad.model_copy(update={"request_key": "badcue"})
    unusable = finish(ctx, bad)
    assert unusable.state == "partial"
    assert "subtitles" in unusable.missing_fields
    assert q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments")[0]["n"] == 0
    denied, _permit = prepare(
        ctx, project, origin, "sub403", ["media_metadata", "subtitles", "media_file"]
    )
    denied = denied.model_copy(update={"request_key": "sub403"})
    stopped = finish(ctx, denied)
    assert stopped.state == "awaiting_user"
    assert stopped.error["code"] == m.ErrorCode.ACCESS_DENIED.value
    assert stopped.error["message"] == "Upstream refused the request"
    assert "/sub403/provided.vtt" in paths(origin)
    assert "/sub403/auto.vtt" not in paths(origin)
    assert "/sub403/media.wav" not in paths(origin)
    methods = [row["method"] for row in q(ctx, "SELECT method FROM transcript_segments")]
    assert methods == []
    assert "asr" not in methods and "manual" not in methods


def test_track_cap_is_partial(ctx, project, origin):
    req, _permit = prepare(
        ctx,
        project,
        origin,
        "clip",
        ["subtitles"],
        limits={"max_items": 1},
    )
    job = finish(ctx, req)
    assert job.state == "partial"
    assert paths(origin).count("/clip/provided.vtt") == 1
    assert "/clip/auto.vtt" not in paths(origin)
    assert q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments")[0]["n"] == 1


def test_dns_rebind_dials_only_the_checked_address(ctx, project, origin, resolver, monkeypatch):
    resolver.rebind = True
    seen = []
    monkeypatch.setattr(remote_media, "_observer", seen.append)
    req, _permit = prepare(ctx, project, origin, "clip", ["media_metadata"])
    job = finish(ctx, req)
    assert job.state == "succeeded"
    assert resolver.calls == ["media.fixture.test"]
    assert seen[0]["peer"] == "127.0.0.1"


def test_redirect_to_a_private_address_is_not_dialed(ctx, project, origin):
    req, permit_id = prepare(
        ctx,
        project,
        origin,
        "bounce",
        ["media_metadata"],
        approvals=[("MetamuseFixture", ["media.fixture.test", "10.1.2.3"])],
    )
    job = finish(ctx, req)
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.SCOPE_DENIED.value
    assert job.error["message"] == "Outbound URL is outside the network policy"
    assert "/secret" not in paths(origin)
    assert paths(origin) == ["/bounce"]
    assert ctx.store.get(m.Permit, permit_id).used == 1


def test_redirect_to_a_rebound_name_is_not_dialed(ctx, project, origin):
    req, _permit = prepare(
        ctx,
        project,
        origin,
        "rebind",
        ["media_metadata"],
        approvals=[("MetamuseFixture", ["media.fixture.test", "rebind.fixture.test"])],
    )
    job = finish(ctx, req)
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.SCOPE_DENIED.value
    assert "/landed" not in paths(origin)
    assert paths(origin) == ["/rebind"]


def test_redirected_media_keeps_the_media_byte_cap(ctx, project, origin):
    port = origin.server_address[1]
    fat = b"Q" * 200

    def body() -> bytes:
        payload = info_dict(port, "jump")
        payload["formats"] = [
            {
                "url": f"http://media.fixture.test:{port}/jump/media-redirect",
                "ext": "bin",
                "protocol": "http",
            }
        ]
        payload["subtitles"] = {}
        payload["automatic_captions"] = {}
        return dump(payload)

    origin.routes["jump"] = {"status": 200, "body": body}
    origin.fat = fat
    req, _permit = prepare(
        ctx,
        project,
        origin,
        "jump",
        ["media_metadata", "media_file"],
        media_max=64,
    )
    job = finish(ctx, req)
    assert job.state == "partial"
    assert job.stop_reason == "limit_reached"
    assert step_data(job)["code"] == m.ErrorCode.LIMIT_REACHED.value
    assert step_data(job)["bytes_used"] == len(body())
    assert paths(origin).count("/jump") == 1
    assert paths(origin).count("/jump/media-redirect") == 1
    assert paths(origin).count("/jump/fat-body") == 1
    assert q(ctx, "SELECT COUNT(*) AS n FROM assets")[0]["n"] == 0
    assert q(ctx, "SELECT COUNT(*) AS n FROM sources")[0]["n"] == 1
    leftover = []
    for directory in (ctx.paths.root / "staging", ctx.paths.root / "objects"):
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.is_file():
                leftover.append(path.read_bytes())
    assert all(fat not in blob for blob in leftover)


def test_oversize_response_is_limit_reached(ctx, project, origin):
    req, _permit = prepare(
        ctx, project, origin, "huge", ["media_metadata"], limits={"max_bytes": 64}
    )
    job = finish(ctx, req)
    assert job.state == "failed"
    assert job.error["code"] == m.ErrorCode.LIMIT_REACHED.value
    assert job.error["retry_class"] == "never"
    assert q(ctx, "SELECT COUNT(*) AS n FROM sources")[0]["n"] == 0


def test_failure_classes_stop_honestly(ctx, project, origin):
    waits = []
    cases = [
        ("login", "awaiting_user", m.ErrorCode.AUTH_REQUIRED, "Login required"),
        ("challenge", "awaiting_user", m.ErrorCode.CHECKPOINT_REQUIRED, "Platform challenge"),
        ("denied", "awaiting_user", m.ErrorCode.ACCESS_DENIED, "Upstream refused the request"),
        ("geo", "awaiting_user", m.ErrorCode.RESOURCE_UNAVAILABLE, "Geo restriction"),
        ("unavailable", "awaiting_user", m.ErrorCode.RESOURCE_UNAVAILABLE, "Media is unavailable"),
        ("extractor", "failed", m.ErrorCode.UNSUPPORTED, "Extractor failed"),
        ("parser", "failed", m.ErrorCode.INPUT_INVALID, "Upstream response could not be parsed"),
    ]
    for video, state, code, message in cases:
        origin.hits.clear()
        waits.clear()
        req, _permit = prepare(ctx, project, origin, video, ["media_metadata", "media_file"])
        req = req.model_copy(update={"request_key": video})
        job = finish(ctx, req, sleep=waits.append)
        assert job.state == state, video
        assert job.error["code"] == code.value
        assert job.error["message"] == message
        assert "VPN" not in job.error["message"] and "VPN" not in (job.error["next_action"] or "")
        assert paths(origin) == [f"/{video}"]
        assert waits == []
        assert q(ctx, "SELECT retries FROM job_steps WHERE job_id=?", (job.id,))[0]["retries"] == 0
        assert "/media.wav" not in "".join(paths(origin))


def test_rate_limit_reports_retry_after_without_retrying(ctx, project, origin):
    waits = []
    req, _permit = prepare(ctx, project, origin, "limited", ["media_metadata"])
    job = finish(ctx, req, sleep=waits.append)
    assert job.state == "partial" and job.coverage_status == "partial"
    assert job.stop_reason == "rate_limited"
    assert job.error is None
    assert step_data(job)["code"] == m.ErrorCode.RATE_LIMITED.value
    assert step_data(job)["retry_after"] == 17
    assert step_data(job)["message"] == "Upstream rate limit"
    assert paths(origin) == ["/limited"]
    assert waits == []
    assert q(ctx, "SELECT retries FROM job_steps")[0]["retries"] == 0


def test_empty_result_is_bounded(ctx, project, origin):
    req, _permit = prepare(ctx, project, origin, "empty", ["media_metadata"])
    job = finish(ctx, req)
    assert job.state == "succeeded"
    assert job.coverage_status == "bounded"
    assert job.stop_reason == "empty"
    assert job.error is None
    assert step_data(job)["rows"] == []
    assert q(ctx, "SELECT COUNT(*) AS n FROM sources")[0]["n"] == 0


def test_byte_budget_survives_restart(ctx, project, origin, monkeypatch):
    body = origin.routes["clip"]["body"]()
    calls = {"n": 0}

    def crash_once(step):
        if calls["n"] == 0 and (step.checkpoint or {}).get("phase") == "metadata":
            calls["n"] += 1
            raise SystemExit(7)

    monkeypatch.setattr(remote_media, "_note", crash_once)
    req, _permit = prepare(
        ctx,
        project,
        origin,
        "clip",
        ["media_metadata", "subtitles", "media_file"],
        limits={"max_bytes": len(body)},
    )
    env = app.submit_collect(ctx, req)
    assert env.status == "accepted", env.error
    with pytest.raises(SystemExit):
        with jobs.Executor(ctx.store) as executor:
            executor.run_once()
    held = ctx.store.get(m.Job, env.data["job_id"])
    assert held.state == "running"
    checkpoint = json.loads(q(ctx, "SELECT checkpoint FROM job_steps")[0]["checkpoint"])
    assert checkpoint["phase"] == "metadata"
    assert checkpoint["bytes_used"] == len(body)
    assert paths(origin) == ["/clip"]
    with jobs.Executor(ctx.store) as executor:
        assert executor.run_once()
    job = ctx.store.get(m.Job, env.data["job_id"])
    assert job.state == "partial"
    assert job.stop_reason == "limit_reached"
    assert step_data(job)["bytes_used"] == len(body)
    assert paths(origin) == ["/clip"]
    assert q(ctx, "SELECT COUNT(*) AS n FROM item_revisions")[0]["n"] == 1
    assert q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments")[0]["n"] == 0


@pytest.mark.parametrize("phase", ["metadata", "subtitles", "media"])
def test_resume_after_each_checkpoint(ctx, project, origin, monkeypatch, phase):
    seen = {"done": False}

    def crash_once(step):
        if not seen["done"] and (step.checkpoint or {}).get("phase") == phase:
            seen["done"] = True
            raise SystemExit(7)

    monkeypatch.setattr(remote_media, "_note", crash_once)
    req, permit_id = prepare(
        ctx, project, origin, "clip", ["media_metadata", "subtitles", "media_file"]
    )
    env = app.submit_collect(ctx, req)
    with pytest.raises(SystemExit):
        with jobs.Executor(ctx.store) as executor:
            executor.run_once()
    assert ctx.store.get(m.Job, env.data["job_id"]).state == "running"
    page_hits = paths(origin).count("/clip")
    subtitle_hits = paths(origin).count("/clip/provided.vtt")
    media_hits = paths(origin).count("/clip/media.wav")
    if phase == "metadata":
        assert page_hits == 1 and subtitle_hits == 0 and media_hits == 0
    elif phase == "subtitles":
        assert page_hits == 1 and subtitle_hits == 1 and media_hits == 0
    else:
        assert page_hits == 1 and subtitle_hits == 1 and media_hits == 1
    with jobs.Executor(ctx.store) as executor:
        assert executor.run_once()
    job = ctx.store.get(m.Job, env.data["job_id"])
    assert job.state == "succeeded"
    assert paths(origin).count("/clip/provided.vtt") == 1
    assert paths(origin).count("/clip/auto.vtt") == 1
    assert paths(origin).count("/clip/media.wav") == 1
    # A resume re-reads the page because the checkpoint no longer holds URLs.
    assert paths(origin).count("/clip") == (1 if phase == "media" else 2)
    assert ctx.store.get(m.Permit, permit_id).used == 1
    assert q(ctx, "SELECT COUNT(*) AS n FROM item_revisions")[0]["n"] == 1


def test_resume_does_not_publish_signed_upstream_urls(ctx, project, origin, monkeypatch, caplog):
    port = origin.server_address[1]
    query = "sig=SIGNEDMARK&expire=1"

    def body() -> bytes:
        payload = info_dict(port, "signed")
        payload["formats"][1]["url"] += "?" + query
        payload["subtitles"]["en"][0]["url"] += "?" + query
        payload["automatic_captions"]["en"][0]["url"] += "?" + query
        return dump(payload)

    origin.routes["signed"] = {"status": 200, "body": body}

    def crash_once(step):
        if (step.checkpoint or {}).get("phase") == "metadata":
            raise SystemExit(7)

    monkeypatch.setattr(remote_media, "_note", crash_once)
    caplog.set_level(logging.DEBUG)
    req, _permit = prepare(
        ctx, project, origin, "signed", ["media_metadata", "subtitles", "media_file"]
    )
    env = app.submit_collect(ctx, req)
    assert env.status == "accepted", env.error
    with pytest.raises(SystemExit):
        with jobs.Executor(ctx.store) as executor:
            executor.run_once()
    rendered = json.dumps(app.get_job(ctx, {"job_id": env.data["job_id"]}).model_dump(mode="json"))
    assert "SIGNEDMARK" not in rendered
    details = [row["detail"] for row in q(ctx, "SELECT detail FROM events")]
    assert details
    assert all("SIGNEDMARK" not in detail for detail in details)
    assert "SIGNEDMARK" not in caplog.text
    checkpoint = json.loads(q(ctx, "SELECT checkpoint FROM job_steps")[0]["checkpoint"])
    assert checkpoint["phase"] == "metadata"
    assert checkpoint["bytes_used"] == len(body())
    assert checkpoint["current"]["source_id"]
    assert checkpoint["current"]["text_hash"]
    assert "url" not in json.dumps(checkpoint["current"])
    with jobs.Executor(ctx.store) as executor:
        assert executor.run_once()
    job = ctx.store.get(m.Job, env.data["job_id"])
    assert job.state == "succeeded"
    finished = json.dumps(app.get_job(ctx, {"job_id": job.id}).model_dump(mode="json"))
    assert "SIGNEDMARK" not in finished
    assert all("SIGNEDMARK" not in row["detail"] for row in q(ctx, "SELECT detail FROM events"))
    assert "SIGNEDMARK" not in caplog.text
    assert step_data(job)["bytes_used"] > checkpoint["bytes_used"]
    assert paths(origin).count("/signed") == 2
    assert paths(origin).count("/signed/provided.vtt") == 1
    assert paths(origin).count("/signed/auto.vtt") == 1
    assert paths(origin).count("/signed/media.wav") == 1
    assert any(query in hit["query"] for hit in origin.hits)
    assert q(ctx, "SELECT COUNT(*) AS n FROM item_revisions")[0]["n"] == 1
    assert q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments")[0]["n"] == 2
    assert q(ctx, "SELECT COUNT(*) AS n FROM sources WHERE native_id='signed'")[0]["n"] == 1


def test_same_text_does_not_add_a_revision(ctx, project, origin):
    first = finish(ctx, prepare(ctx, project, origin, "clip", ["media_metadata"])[0])
    assert first.state == "succeeded"
    origin.hits.clear()
    again_req, _permit = prepare(ctx, project, origin, "clip", ["media_metadata"])
    again = finish(ctx, again_req.model_copy(update={"request_key": "again"}))
    assert step_data(again)["rows"][0]["status"] == "unchanged"
    assert q(ctx, "SELECT COUNT(*) AS n FROM item_revisions")[0]["n"] == 1
    origin.titles["clip"] = "Synthetic clip changed"
    changed_req, _permit = prepare(ctx, project, origin, "clip", ["media_metadata"])
    changed = finish(ctx, changed_req.model_copy(update={"request_key": "changed"}))
    assert step_data(changed)["rows"][0]["status"] == "committed"
    revisions = q(ctx, "SELECT revision, text FROM item_revisions ORDER BY revision")
    assert [row["text"] for row in revisions] == ["Synthetic clip", "Synthetic clip changed"]
    assert q(ctx, "SELECT COUNT(*) AS n FROM sources WHERE native_id='clip'")[0]["n"] == 1


def test_revoked_host_does_not_consume_the_permit(ctx, project, origin):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    req = remote_request(project, url_for(origin, "clip"), ["media_metadata"])
    permit_id = issue(ctx, project, req, ["MetamuseFixture"])
    req = req.model_copy(update={"access_grant_id": permit_id})
    env = app.submit_collect(ctx, req)
    assert env.status == "accepted"
    configure(ctx, [("MetamuseFixture", ["other.fixture.test"])])
    with jobs.Executor(ctx.store) as executor:
        assert executor.run_once()
    job = ctx.store.get(m.Job, env.data["job_id"])
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.SCOPE_DENIED.value
    assert ctx.store.get(m.Permit, permit_id).used == 0
    assert origin.hits == []


def _open_with_dial(tmp_path, monkeypatch, dial, ie=MetamuseFixtureIE, budget=1000):
    monkeypatch.setattr(remote_media, "_dial", dial)

    def check(url, *, purpose):
        return safety.check_url(url, purpose=purpose, resolver=lambda _host: ["8.8.8.8"])

    net = remote_media._Net(
        checker=check,
        budget=remote_media._Budget(0, budget),
        media_max=1000,
        timeout=2,
    )
    params = remote_media.ytdlp_params(
        tmp_path,
        max_items=5,
        socket_timeout=2,
        max_filesize=1000,
        extractor=ie.ie_key(),
    )
    return remote_media._open_ydl(params, ie, net), net


def test_redirect_bodies_and_errors_spend_the_byte_budget(tmp_path, monkeypatch):
    transferred = []

    def dial(method, url, headers, body, checked, timeout, limit):
        del method, headers, body, checked, timeout
        if url.endswith("/start"):
            status, payload = 302, b"x" * 90
            headers_out = {"Location": "/end"}
        elif url.endswith("/error"):
            status, payload = 403, b"x" * 90
            headers_out = {}
        else:
            status, payload = 200, b"y" * 10
            headers_out = {}
        assert len(payload) <= limit
        transferred.append(len(payload))
        return status, headers_out, payload, "8.8.8.8"

    ydl, net = _open_with_dial(tmp_path, monkeypatch, dial, budget=100)
    try:
        assert ydl.urlopen("https://media.fixture.test/start").read() == b"y" * 10
        with pytest.raises(RequestError):
            ydl.urlopen("https://media.fixture.test/error")
    finally:
        ydl.close()
    assert transferred == [90, 10]
    assert net.budget.used == 100


def test_https_downgrade_is_refused(tmp_path, monkeypatch):
    sent = []

    def dial(method, url, headers, body, checked, timeout, limit):
        del timeout, limit
        sent.append((method, checked.url, headers.get("Authorization"), body))
        if checked.url.startswith("https://"):
            return 307, {"Location": "http://media.fixture.test/landing"}, b"", "8.8.8.8"
        return 200, {}, b"ok", "8.8.8.8"

    ydl, _net = _open_with_dial(tmp_path, monkeypatch, dial)
    try:
        with pytest.raises(RequestError) as caught:
            ydl.urlopen(
                Request(
                    "https://media.fixture.test/start",
                    data=b"SYNTHETIC_CREDENTIAL",
                    headers={"authorization": "Bearer SYNTHETIC_TOKEN"},
                )
            )
    finally:
        ydl.close()
    assert caught.value.cause.detail.code == m.ErrorCode.SCOPE_DENIED
    assert "HTTPS redirect to HTTP" in caught.value.cause.detail.message
    assert [item[1] for item in sent] == ["https://media.fixture.test/start"]


def test_cross_host_307_does_not_forward_the_body(tmp_path, monkeypatch):
    sent = []

    def dial(method, url, headers, body, checked, timeout, limit):
        del method, url, timeout, limit
        sent.append((checked.hostname, headers.get("Authorization"), body))
        if len(sent) == 1:
            return 307, {"Location": "https://cdn.fixture.test/landing"}, b"", "8.8.8.8"
        return 200, {}, b"ok", "8.8.8.8"

    ydl, _net = _open_with_dial(tmp_path, monkeypatch, dial)
    try:
        with pytest.raises(RequestError) as caught:
            ydl.urlopen(
                Request(
                    "https://media.fixture.test/start",
                    data=b"SYNTHETIC_PASSWORD",
                    headers={"authorization": "Bearer SYNTHETIC_TOKEN"},
                )
            )
    finally:
        ydl.close()
    assert caught.value.cause.detail.code == m.ErrorCode.SCOPE_DENIED
    assert "request body" in caught.value.cause.detail.message
    assert sent == [("media.fixture.test", "Bearer SYNTHETIC_TOKEN", b"SYNTHETIC_PASSWORD")]


def test_port_change_drops_authorization(tmp_path, monkeypatch):
    sent = []

    def check(url, *, purpose):
        parts = urlsplit(url)
        return safety.CheckedURL(
            url, parts.hostname or "", parts.port or 443, ("8.8.8.8",), purpose
        )

    def dial(method, url, headers, body, checked, timeout, limit):
        del timeout, limit
        sent.append((method, checked.url, headers.get("Authorization"), body))
        if len(sent) == 1:
            return 302, {"Location": "https://media.fixture.test:8443/landing"}, b"", "8.8.8.8"
        return 200, {}, b"ok", "8.8.8.8"

    monkeypatch.setattr(remote_media, "_dial", dial)
    net = remote_media._Net(
        checker=check,
        budget=remote_media._Budget(0, 1000),
        media_max=1000,
        timeout=2,
    )
    params = remote_media.ytdlp_params(
        tmp_path,
        max_items=5,
        socket_timeout=2,
        max_filesize=1000,
        extractor="MetamuseFixture",
    )
    ydl = remote_media._open_ydl(params, MetamuseFixtureIE, net)
    try:
        assert (
            ydl.urlopen(
                Request(
                    "https://media.fixture.test/start",
                    headers={"authorization": "Bearer SYNTHETIC_TOKEN"},
                )
            ).read()
            == b"ok"
        )
    finally:
        ydl.close()
    assert sent[0][2] == "Bearer SYNTHETIC_TOKEN"
    assert sent[1][0] == "GET"
    assert sent[1][1].endswith(":8443/landing")
    assert sent[1][2] is None
    assert sent[1][3] is None


def test_iq_extractor_cannot_launch_a_process(ctx, project, resolver, tmp_path, monkeypatch):
    from yt_dlp.extractor import openload
    from yt_dlp.extractor.iqiyi import IqIE

    resolver.extra["www.iq.com"] = ["127.0.0.1"]
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(openload, "check_executable", lambda *_args, **_kwargs: "phantomjs")
    script = (
        ",function(a,b,c){var _qda=1;"
        'var x=new XMLHttpRequest();x.open("GET","http://127.0.0.1/private",false);'
        "x.send();_qdc()},"
    )
    monkeypatch.setattr(IqIE, "_extract_vms_player_js", lambda self, webpage, video_id: script)
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(
            {"props": {"initialState": {"play": {"curVideoInfo": {"tvId": "1", "vid": "2"}}}}}
        )
        + "</script>"
    )
    dials = []

    def dial(method, url, headers, body, checked, timeout, limit):
        del method, url, headers, body, checked, timeout, limit
        dials.append(True)
        return 200, {"Content-Type": "text/html"}, html.encode(), "127.0.0.1"

    monkeypatch.setattr(remote_media, "_dial", dial)
    attempted: list[object] = []
    launched: list[object] = []
    real_init = openload.Popen.__init__

    def init(self, args, *pos, **kwargs):
        attempted.append(args)
        real_init(self, args, *pos, **kwargs)
        launched.append(args)

    monkeypatch.setattr(openload.Popen, "__init__", init)
    configure(ctx, [("Iq", ["www.iq.com"])])
    req = remote_request(project, "https://www.iq.com/play/synthetic-123", ["media_metadata"])
    permit = issue(ctx, project, req, ["Iq"])
    job = finish(ctx, req.model_copy(update={"access_grant_id": permit}))
    assert dials
    assert attempted and launched == []
    assert job.state == "failed"
    assert job.error["code"] == m.ErrorCode.UNSUPPORTED.value
    assert job.error["message"] == "extractor requires an external process"
    assert q(ctx, "SELECT COUNT(*) AS n FROM sources")[0]["n"] == 0
    assert q(ctx, "SELECT COUNT(*) AS n FROM items")[0]["n"] == 0


def test_cached_token_under_xdg_is_not_used(tmp_path, monkeypatch):
    from yt_dlp.extractor.vimeo import VimeoBaseInfoExtractor, VimeoIE

    cache = tmp_path / "cache" / "yt-dlp" / "vimeo"
    cache.mkdir(parents=True)
    (cache / "oauth-token-android.json").write_text(
        json.dumps({"yt-dlp_version": "2026.08.19", "data": "SYNTHETIC_OTHER_ACCOUNT_TOKEN"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr(VimeoBaseInfoExtractor, "_oauth_tokens", {})

    def dial(*_args, **_kwargs):
        pytest.fail("a cached token must not be sent")

    ydl, _net = _open_with_dial(tmp_path, monkeypatch, dial, ie=VimeoIE)
    try:
        ie = ydl.get_info_extractor("Vimeo")
        ie.initialize()
        assert ydl.cache.enabled is False
        assert ie._DEFAULT_CLIENT != "android"
        assert "SYNTHETIC_OTHER_ACCOUNT_TOKEN" not in json.dumps(ie._oauth_tokens)
        with pytest.raises(ExtractorError):
            ie._fetch_oauth_token("android")
        assert ydl.params["username"] is None
        assert ydl.params["password"] is None
        assert ydl.params["cookiefile"] is None
    finally:
        ydl.close()


def test_each_input_uses_only_its_extractor_hosts(ctx, project, origin, resolver, monkeypatch):
    class SecondFixtureIE(MetamuseFixtureIE):
        _VALID_URL = r"https?://media\.fixture\.test(?::\d+)?/(?P<id>second)"

        def _real_extract(self, url):
            self._downloader.urlopen("https://cdn.fixture.test/from-second").read()
            return {"id": "second", "title": "second", "url": url, "ext": "mp4"}

    resolver.extra["cdn.fixture.test"] = ["127.0.0.1"]
    hits = []

    def dial(method, url, headers, body, checked, timeout, limit):
        del method, url, headers, body, timeout, limit
        hits.append(checked.hostname)
        return 200, {}, b"ok", "127.0.0.1"

    remote_media.register_test_extractor(SecondFixtureIE)
    monkeypatch.setattr(remote_media, "_dial", dial)
    original = MetamuseFixtureIE._real_extract

    def first_extract(self, url):
        self._downloader.urlopen("https://cdn.fixture.test/not-approved-for-first").read()
        return {"id": "clip", "title": "clip", "url": url, "ext": "mp4"}

    monkeypatch.setattr(MetamuseFixtureIE, "_real_extract", first_extract)
    try:
        configure(
            ctx,
            [
                ("SecondFixture", ["media.fixture.test", "cdn.fixture.test"]),
                ("MetamuseFixture", ["media.fixture.test"]),
            ],
        )
        req = remote_request(project, url_for(origin, "clip"), ["media_metadata"])
        req = req.model_copy(
            update={
                "inputs": [
                    req.inputs[0],
                    m.URLInput(kind="url", url=url_for(origin, "second")),
                ]
            }
        )
        permit = issue(ctx, project, req, ["MetamuseFixture", "SecondFixture"])
        denied = finish(ctx, req.model_copy(update={"access_grant_id": permit}))
        assert denied.state == "awaiting_user"
        assert denied.error["code"] == m.ErrorCode.SCOPE_DENIED.value
        assert hits == []
        monkeypatch.setattr(MetamuseFixtureIE, "_real_extract", original)
        alone = remote_request(project, url_for(origin, "second"), ["media_metadata"])
        alone_permit = issue(ctx, project, alone, ["SecondFixture"])
        allowed = finish(ctx, alone.model_copy(update={"access_grant_id": alone_permit}))
        assert allowed.state == "succeeded"
        assert hits == ["cdn.fixture.test"]
    finally:
        remote_media.unregister_test_extractor(SecondFixtureIE)


def test_wall_clock_is_checked_before_every_dial(ctx, project, origin, monkeypatch):
    now = datetime.fromisoformat(ctx.clock())
    elapsed = [0]
    calls = []
    monkeypatch.setattr(
        ctx.store, "clock", lambda: (now + timedelta(seconds=elapsed[0])).isoformat()
    )

    def extract(self, url):
        for _ in range(5):
            self._downloader.urlopen(url).read()
        return {"id": "clip", "title": "t", "url": url, "ext": "mp4"}

    def dial(method, url, headers, body, checked, timeout, limit):
        del method, url, headers, body, checked, limit
        calls.append((elapsed[0], timeout))
        elapsed[0] += 1
        return 200, {}, b"ok", "127.0.0.1"

    monkeypatch.setattr(MetamuseFixtureIE, "_real_extract", extract)
    monkeypatch.setattr(remote_media, "_dial", dial)
    req, _permit = prepare(
        ctx, project, origin, "clip", ["media_metadata"], limits={"wall_clock_seconds": 1}
    )
    job = finish(ctx, req)
    assert len(calls) == 1 and calls[0][0] == 0
    assert 0 < calls[0][1] <= 1
    assert job.state == "failed"
    assert job.error["code"] == m.ErrorCode.LIMIT_REACHED.value
    assert job.error["message"] == "Job wall clock limit reached"
    assert q(ctx, "SELECT COUNT(*) AS n FROM items")[0]["n"] == 0


def test_multi_entry_playlist_is_refused_and_one_entry_is_unwrapped(ctx, project, origin):
    port = origin.server_address[1]
    entries = [info_dict(port, native) for native in ("first", "second", "third")]
    origin.routes["playlist"] = {
        "status": 200,
        "body": dump(
            {
                "_type": "playlist",
                "id": "playlist",
                "title": "Synthetic playlist",
                "entries": entries,
                "playlist_count": 3,
            }
        ),
    }
    refused = finish(
        ctx,
        prepare(ctx, project, origin, "playlist", ["media_metadata"], limits={"max_items": 3})[0],
    )
    assert refused.state == "failed"
    assert refused.error["code"] == m.ErrorCode.UNSUPPORTED.value
    assert refused.error["next_action"] == "submit the individual media URL"
    assert q(ctx, "SELECT native_id FROM items") == []
    assert q(ctx, "SELECT native_id FROM sources") == []
    origin.routes["single"] = {
        "status": 200,
        "body": dump(
            {
                "_type": "playlist",
                "id": "single",
                "title": "Synthetic playlist",
                "entries": [info_dict(port, "only", title="Only clip")],
                "playlist_count": 1,
            }
        ),
    }
    kept = finish(
        ctx,
        prepare(ctx, project, origin, "single", ["media_metadata"])[0].model_copy(
            update={"request_key": "single-playlist"}
        ),
    )
    assert kept.state == "succeeded"
    assert kept.coverage_status == "scope_satisfied"
    assert [row["native_id"] for row in q(ctx, "SELECT native_id FROM items")] == ["only"]


def test_changed_subtitle_bytes_append_a_revision(ctx, project, origin):
    first = finish(ctx, prepare(ctx, project, origin, "clip", ["media_metadata", "subtitles"])[0])
    assert first.state == "succeeded"
    revised = VTT.replace(b"synthetic cue", b"CORRECTED_CUE")
    origin.subtitles[("clip", "provided")] = revised
    second_req, _permit = prepare(ctx, project, origin, "clip", ["media_metadata", "subtitles"])
    second = finish(ctx, second_req.model_copy(update={"request_key": "revised-subs"}))
    assert second.state == "succeeded"
    rows = q(
        ctx,
        "SELECT t.id, t.text, t.revision, s.use_basis FROM transcript_segments t "
        "JOIN sources s ON t.source_id=s.id WHERE t.method='provided' ORDER BY t.revision",
    )
    assert [row["text"] for row in rows] == ["synthetic cue", "CORRECTED_CUE"]
    assert [row["revision"] for row in rows] == [1, 2]
    assert rows[0]["id"] == rows[1]["id"]
    basis = json.loads(rows[0]["use_basis"])
    new_sha = remote_media._subtitle_sha(revised.decode())
    old_sha = remote_media._subtitle_sha(VTT.decode())
    assert basis["file_sha256"] == new_sha
    by_revision = {item["revision"]: item["file_sha256"] for item in basis["revisions"]}
    assert by_revision[1] == old_sha
    assert by_revision[2] == new_sha
    assert (
        q(ctx, "SELECT COUNT(*) AS n FROM sources WHERE native_id LIKE '%:subtitle:en:provided'")[
            0
        ]["n"]
        == 1
    )
    ref = {"kind": "transcript_segment", "id": rows[0]["id"], "revision": 1}
    packet = app.prepare_packet(
        ctx, {"project_id": project, "selection": [ref], "request_key": "cue-packet"}
    )
    draft = app.save_draft(
        ctx,
        {
            "project_id": project,
            "text": "host",
            "platform": "generic",
            "facts_refs": [ref],
            "request_key": "cue-draft",
        },
    )
    assert packet.data["stale"] is True
    assert draft.data["stale"] is True


def test_resume_rejects_a_changed_upstream_identity(ctx, project, origin, monkeypatch):
    req, permit = prepare(ctx, project, origin, "clip", ["media_metadata", "subtitles"])

    def crash(step):
        if (step.checkpoint or {}).get("phase") == "metadata":
            raise SystemExit(7)

    monkeypatch.setattr(remote_media, "_note", crash)
    env = app.submit_collect(ctx, req)
    with pytest.raises(SystemExit):
        with jobs.Executor(ctx.store) as executor:
            executor.run_once()
    port = origin.server_address[1]
    origin.routes["clip"] = {"status": 200, "body": dump(info_dict(port, "other"))}
    origin.subtitles[("other", "provided")] = VTT.replace(b"synthetic cue", b"OTHER_VIDEO_CUE")
    monkeypatch.setattr(remote_media, "_note", lambda step: None)
    with jobs.Executor(ctx.store) as executor:
        assert executor.run_once()
    job = ctx.store.get(m.Job, env.data["job_id"])
    assert job.state == "partial"
    assert job.error is None
    assert job.coverage_status == "partial"
    assert job.stop_reason == "data_stale"
    assert step_data(job)["code"] == m.ErrorCode.DATA_STALE.value
    assert step_data(job)["rows"][0]["code"] == m.ErrorCode.DATA_STALE.value
    assert q(ctx, "SELECT native_id FROM items")[0]["native_id"] == "clip"
    assert q(ctx, "SELECT COUNT(*) AS n FROM item_revisions")[0]["n"] == 1
    assert q(ctx, "SELECT COUNT(*) AS n FROM transcript_segments")[0]["n"] == 0
    assert "OTHER_VIDEO_CUE" not in json.dumps(
        [dict(row) for row in q(ctx, "SELECT text FROM transcript_segments")]
    )
    assert "/other/provided.vtt" not in paths(origin)
    assert ctx.store.get(m.Permit, permit).used == 1


def test_malformed_cue_keeps_the_track_incomplete(ctx, project, origin):
    origin.subtitles[("clip", "provided")] = VTT + b"\n00:00:03.000 --> 00:00:02.000\nBAD_CUE\n"
    job = finish(ctx, prepare(ctx, project, origin, "clip", ["media_metadata", "subtitles"])[0])
    assert job.state == "partial"
    assert "subtitles" in job.missing_fields
    bases = [
        json.loads(row["use_basis"])
        for row in q(
            ctx, "SELECT use_basis FROM sources WHERE native_id LIKE '%:subtitle:en:provided'"
        )
    ]
    assert bases[0]["complete"] == 0
    assert q(ctx, "SELECT text FROM transcript_segments WHERE method='provided'")[0]["text"] == (
        "synthetic cue"
    )
    again_req, _permit = prepare(ctx, project, origin, "clip", ["media_metadata", "subtitles"])
    again = finish(ctx, again_req.model_copy(update={"request_key": "bad-again"}))
    assert again.state == "partial"
    assert "subtitles" in again.missing_fields
    provided = q(ctx, "SELECT text, revision FROM transcript_segments WHERE method='provided'")
    assert len(provided) == 1
    refreshed = json.loads(
        q(ctx, "SELECT use_basis FROM sources WHERE native_id LIKE '%:subtitle:en:provided'")[0][
            "use_basis"
        ]
    )
    assert refreshed["complete"] == 0


def test_changed_timestamp_is_observed_without_a_new_revision(ctx, project, origin):
    first = finish(ctx, prepare(ctx, project, origin, "clip", ["media_metadata"])[0])
    assert first.state == "succeeded"
    old = q(ctx, "SELECT raw_time FROM item_revisions")[0]["raw_time"]
    port = origin.server_address[1]
    payload = info_dict(port, "clip")
    payload["timestamp"] = 1700000099
    origin.routes["clip"] = {"status": 200, "body": dump(payload)}
    second_req, _permit = prepare(ctx, project, origin, "clip", ["media_metadata"])
    second = finish(ctx, second_req.model_copy(update={"request_key": "time-change"}))
    rows = q(ctx, "SELECT raw_time FROM item_revisions")
    basis = json.loads(
        q(ctx, "SELECT use_basis FROM sources WHERE native_id='clip'")[0]["use_basis"]
    )
    assert len(rows) == 1
    assert rows[0]["raw_time"] == old == "1700000000"
    assert basis["upstream_timestamp"] == "1700000099"
    assert step_data(second)["rows"][0]["status"] == "unchanged"


def test_fractional_timestamp_keeps_its_text(ctx, project, origin):
    record = remote_media._normalize(
        {"id": "clip", "title": "t", "timestamp": 1700000000.125},
        "https://media.fixture.test/clip",
        "Fixture",
        10,
    )
    assert record["raw_time"] == "1700000000.125"
    port = origin.server_address[1]
    payload = info_dict(port, "clip")
    payload["timestamp"] = 1700000000.125
    origin.routes["fraction"] = {"status": 200, "body": dump(payload)}
    job = finish(ctx, prepare(ctx, project, origin, "fraction", ["media_metadata"])[0])
    assert job.state == "succeeded"
    assert q(ctx, "SELECT raw_time FROM item_revisions")[0]["raw_time"] == "1700000000.125"


def test_fabricated_title_is_stored_as_null(ctx, project, origin):
    payload = info_dict(origin.server_address[1], "clip")
    payload.pop("title")
    origin.routes["clip"] = {"status": 200, "body": dump(payload)}
    job = finish(ctx, prepare(ctx, project, origin, "clip", ["media_metadata"])[0])
    row = q(ctx, "SELECT text, complete, missing_fields FROM item_revisions")[0]
    assert job.state == "partial"
    assert job.coverage_status == "partial"
    assert "title" in job.missing_fields
    assert row["text"] is None
    assert row["complete"] == 0
    assert json.loads(row["missing_fields"]) == ["title"]
    assert row["text"] != "MetamuseFixture video #clip"
    again_req, _permit = prepare(ctx, project, origin, "clip", ["media_metadata"])
    again = finish(ctx, again_req.model_copy(update={"request_key": "title-again"}))
    assert again.state == "partial"
    assert q(ctx, "SELECT COUNT(*) AS n FROM item_revisions")[0]["n"] == 1


REEL_ID = "123456789"
SHARE_TOKEN = "Ab12token"
SHARE_R = f"https://www.facebook.com/share/r/{SHARE_TOKEN}/"
SHARE_V = "https://www.facebook.com/share/v/VideoToken/"
REEL_URL = f"https://www.facebook.com/reel/{REEL_ID}"
WATCH_URL = f"https://www.facebook.com/watch/?v={REEL_ID}"
HOP_CALLS: list[str] = []


def facebook_page(video_id: str) -> bytes:
    payload = {
        "require": [
            [
                "RelayPrefetchedStreamCache_synthetic",
                "next",
                [],
                [
                    "adp_synthetic",
                    {
                        "__bbox": {
                            "result": {
                                "data": {
                                    "video": {
                                        "id": video_id,
                                        "name": "Synthetic reel",
                                        "playable_url": "https://cdn.fixture.test/clip.mp4",
                                        "owner": {"id": "page1", "name": "Synthetic page"},
                                        "publish_time": 1700000000,
                                    }
                                }
                            }
                        }
                    },
                ],
            ]
        ]
    }
    html = (
        "<!doctype html><title>Synthetic reel</title>"
        '<script type="application/json" data-sjs>'
        + json.dumps(payload, separators=(",", ":"))
        + "</script>"
    )
    return html.encode()


def allow_facebook(resolver):
    resolver.extra["www.facebook.com"] = ["127.0.0.1"]
    resolver.extra["m.facebook.com"] = ["127.0.0.1"]


def route_dial(monkeypatch, routes):
    dials = []

    def dial(method, url, headers, body, checked, timeout, limit):
        del body, checked, timeout, limit
        parts = urlsplit(url)
        dials.append(
            {
                "method": (method or "GET").upper(),
                "url": url,
                "host": (parts.hostname or "").lower(),
                "path": parts.path,
                "query": parts.query,
                "cookie": headers.get("Cookie"),
            }
        )
        route = routes.get(parts.path)
        if route is None:
            return 404, {}, b"missing", "127.0.0.1", "Not Found"
        if route.get("location"):
            return (
                route.get("status", 302),
                {"Location": route["location"]},
                b"",
                "127.0.0.1",
                "Found",
            )
        return (
            route.get("status", 200),
            dict(route.get("headers") or {}),
            route.get("body") or b"",
            "127.0.0.1",
            route.get("reason") or "",
        )

    monkeypatch.setattr(remote_media, "_dial", dial)
    return dials


def run(ctx, req, secret_store=None):
    env = app.submit_collect(ctx, req)
    assert env.status == "accepted", env.error
    with jobs.Executor(ctx.store, secret_store=secret_store) as executor:
        assert executor.run_once()
    return ctx.store.get(m.Job, env.data["job_id"])


def live_request(ctx, project, url, resources, approvals, keys):
    configure(ctx, approvals)
    req = remote_request(project, url, resources)
    permit = issue(ctx, project, req, keys)
    return req.model_copy(update={"access_grant_id": permit}), permit


def stored_rows(ctx) -> int:
    sources = q(ctx, "SELECT COUNT(*) AS n FROM sources")[0]["n"]
    items = q(ctx, "SELECT COUNT(*) AS n FROM items")[0]["n"]
    return sources + items


def marker_value(prefix: str = "mm") -> str:
    return f"{prefix}_MARKERCOOKIE"


def netscape(domain, flag, path, secure, expiry, name, value) -> bytes:
    return (
        "# Netscape HTTP Cookie File\n"
        f"{domain}\t{flag}\t{path}\t{secure}\t{expiry}\t{name}\t{value}\n"
    ).encode()


def put_secret(tmp_path, ctx, secret, *, provider=PROVIDER, account=ACCOUNT):
    store = safety.SecretStore(root=tmp_path / "secrets")
    meta = store.put(secret, provider=provider, account=account, created_at=ctx.clock())
    return store, meta["auth_ref"]


def assert_marker_absent(ctx, marker, job, caplog):
    for path in ctx.paths.root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert marker.encode() not in path.read_bytes(), path
    dumped = json.dumps(job.model_dump(mode="json"), default=str)
    assert marker not in dumped
    assert marker not in caplog.text


# process_ie_result refuses a video dict with no formats. The URL is never fetched:
# extraction runs with download=False and these tests request metadata only.
_HOP_FORMAT = {
    "url": "https://hop.fixture.test/media.wav",
    "ext": "wav",
    "protocol": "http",
}


class HopAIE(InfoExtractor):
    _VALID_URL = r"https://hop\.fixture\.test/a/(?P<id>[A-Za-z0-9]+)"

    def _real_extract(self, url):
        del url
        return self.url_result("https://hop.fixture.test/b/1", "HopB", "1")


class HopBIE(InfoExtractor):
    _VALID_URL = r"https://hop\.fixture\.test/b/(?P<id>[A-Za-z0-9]+)"

    def _real_extract(self, url):
        del url
        return self.url_result("https://hop.fixture.test/c/1", "HopC", "1")


class HopCIE(InfoExtractor):
    _VALID_URL = r"https://hop\.fixture\.test/c/(?P<id>[A-Za-z0-9]+)"
    stop = False

    def _real_extract(self, url):
        del url
        HOP_CALLS.append("C")
        if self.stop:
            return self.url_result("https://hop.fixture.test/d/1", "HopD", "1")
        return {
            "id": "1",
            "title": "Two hops",
            "timestamp": 1700000000,
            "channel_id": "hop",
            "formats": [_HOP_FORMAT],
        }


class HopDIE(InfoExtractor):
    _VALID_URL = r"https://hop\.fixture\.test/d/(?P<id>[A-Za-z0-9]+)"

    def _real_extract(self, url):
        del url
        HOP_CALLS.append("D")
        return {
            "id": "1",
            "title": "Landed hop",
            "timestamp": 1700000000,
            "formats": [_HOP_FORMAT],
        }


class OuterIE(InfoExtractor):
    _VALID_URL = r"https://hop\.fixture\.test/outer/(?P<id>[A-Za-z0-9]+)"

    def _real_extract(self, url):
        del url
        return self.url_result(
            "https://hop.fixture.test/inner/1",
            "Inner",
            "1",
            url_transparent=True,
            title="Outer title",
        )


class InnerIE(InfoExtractor):
    _VALID_URL = r"https://hop\.fixture\.test/inner/(?P<id>[A-Za-z0-9]+)"

    def _real_extract(self, url):
        del url
        return {
            "id": "inner-id",
            "title": "Inner title",
            "timestamp": 1700000000,
            "channel_id": "inner-scope",
            "formats": [_HOP_FORMAT],
        }


class SiblingIE(InfoExtractor):
    _VALID_URL = r"https?://www\.fixture\.test(?::\d+)?/sib/(?P<id>[A-Za-z0-9]+)"

    def _real_extract(self, url):
        video_id = self._match_id(url)
        port = urlsplit(url).port
        self._request_webpage(f"http://m.fixture.test:{port}/sib-asset", video_id, note=False)
        return self._download_json(url, video_id)


def test_share_url_shape_and_canonical_form():
    assert remote_media._share_parts(SHARE_R) == ("www.facebook.com", "r", SHARE_TOKEN)
    assert remote_media._share_parts("https://www.facebook.com/share/v/VideoToken") == (
        "www.facebook.com",
        "v",
        "VideoToken",
    )
    assert remote_media._share_parts("https://www.facebook.com/share/r/Ab12token/?x=1") is None
    assert remote_media._share_parts("https://user@www.facebook.com/share/r/Ab12/") is None
    assert remote_media._share_parts("http://www.facebook.com/share/r/Ab12/") is None
    reel = remote_media._canonical_media("https://www.facebook.com/reel/123/?tracking=1#frag")
    watch = remote_media._canonical_media("https://m.facebook.com/watch/?v=123&_rdr")
    page = remote_media._canonical_media("https://www.facebook.com/page/videos/123?x=1")
    assert reel == "https://www.facebook.com/reel/123"
    assert watch == "https://m.facebook.com/watch/?v=123"
    assert page == "https://www.facebook.com/page/videos/123"


def test_facebook_challenge_page_stops_before_a_verify_post(ctx, project, resolver, monkeypatch):
    allow_facebook(resolver)
    dials = route_dial(
        monkeypatch,
        {
            "/watch/": {
                "status": 403,
                "body": b"fetch('/__rd_verify_synthetic')",
                "reason": "Forbidden",
            }
        },
    )
    req, permit = live_request(
        ctx,
        project,
        WATCH_URL,
        ["media_metadata"],
        [("Facebook", ["www.facebook.com"])],
        ["Facebook"],
    )
    job = run(ctx, req)
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.CHECKPOINT_REQUIRED.value
    assert job.error["message"] == "Platform challenge"
    assert job.error["next_action"] == "Stop. Do not solve a challenge or retry it."
    assert [item["method"] for item in dials] == ["GET"]
    assert dials[0]["path"] == "/watch/"
    assert all(not item["path"].startswith("/__rd_verify") for item in dials)
    assert stored_rows(ctx) == 0
    assert ctx.store.get(m.Permit, permit).used == 1


def test_plain_facebook_403_stays_access_denied(ctx, project, resolver, monkeypatch):
    allow_facebook(resolver)
    dials = route_dial(
        monkeypatch,
        {"/watch/": {"status": 403, "body": b"no", "reason": "Forbidden"}},
    )
    req, _permit = live_request(
        ctx,
        project,
        WATCH_URL,
        ["media_metadata"],
        [("Facebook", ["www.facebook.com"])],
        ["Facebook"],
    )
    job = run(ctx, req)
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.ACCESS_DENIED.value
    assert job.error["message"] == "Upstream refused the request"
    assert len(dials) == 1
    assert stored_rows(ctx) == 0


def test_cf_mitigated_challenge_stops_the_job(ctx, project, resolver, monkeypatch):
    allow_facebook(resolver)
    route_dial(
        monkeypatch,
        {
            "/watch/": {
                "status": 503,
                "body": b"no",
                "headers": {"cf-mitigated": "challenge"},
            }
        },
    )
    req, _permit = live_request(
        ctx,
        project,
        WATCH_URL,
        ["media_metadata"],
        [("Facebook", ["www.facebook.com"])],
        ["Facebook"],
    )
    job = run(ctx, req)
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.CHECKPOINT_REQUIRED.value
    assert stored_rows(ctx) == 0


def test_rd_verify_path_is_refused_before_the_dial(tmp_path, monkeypatch):
    """The extractor is already inside the pinned challenge branch.

    ``_download_fb_webpage_and_verify`` lives on ``FacebookAdsIE``, not
    ``FacebookIE``. Path refusal still prevents the verification POST.
    """

    from yt_dlp.extractor.facebook import FacebookAdsIE
    from yt_dlp.networking.common import Response
    from yt_dlp.networking.exceptions import HTTPError
    from yt_dlp.utils import ExtractorError

    dials = []

    def dial(*args, **kwargs):
        del kwargs
        dials.append(args)
        raise AssertionError("dial")

    monkeypatch.setattr(remote_media, "_dial", dial)

    def checker(url, *, purpose):
        raise AssertionError(f"checker {purpose} {url}")

    net = remote_media._Net(
        checker=checker,
        budget=remote_media._Budget(0, 1000),
        media_max=1000,
        timeout=2,
    )
    params = remote_media.ytdlp_params(
        tmp_path,
        max_items=1,
        socket_timeout=2,
        max_filesize=1000,
        extractor="FacebookAds",
    )
    ydl = remote_media._open_ydl(params, FacebookAdsIE, net)
    try:
        ie = ydl.get_info_extractor("FacebookAds")
        body = b"fetch('/__rd_verify_synthetic')"
        response = Response(
            io.BytesIO(body),
            "https://www.facebook.com/ads/library/?id=1",
            {},
            status=403,
            reason="Client challenge",
        )
        cause = HTTPError(response)

        def boom(*args, **kwargs):
            del args, kwargs
            raise ExtractorError("HTTP Error 403: Client challenge", cause=cause)

        ie._download_webpage = boom
        try:
            ie._download_fb_webpage_and_verify("https://www.facebook.com/ads/library/?id=1", "1")
            raised = None
        except Exception as exc:
            raised = exc
    finally:
        ydl.close()
    assert raised is not None
    assert dials == []
    cores = [node for node in remote_media._chain(raised) if isinstance(node, m.CoreError)]
    assert cores
    assert cores[0].detail.code == m.ErrorCode.CHECKPOINT_REQUIRED
    assert cores[0].detail.message == "Platform challenge"


def test_share_reel_resolves_and_hops_to_facebook(ctx, project, resolver, monkeypatch):
    allow_facebook(resolver)
    dials = route_dial(
        monkeypatch,
        {
            f"/share/r/{SHARE_TOKEN}/": {
                "status": 302,
                "location": f"{REEL_URL}/?tracking=1",
            },
            "/watch/": {"status": 200, "body": facebook_page(REEL_ID)},
        },
    )
    req, _permit = live_request(
        ctx,
        project,
        SHARE_R,
        ["media_metadata"],
        [
            ("FacebookReel", ["www.facebook.com"]),
            ("Facebook", ["m.facebook.com", "www.facebook.com"]),
        ],
        ["FacebookReel"],
    )
    job = run(ctx, req)
    assert job.state == "succeeded", job.error
    assert [item["path"] for item in dials] == [f"/share/r/{SHARE_TOKEN}/", "/watch/"]
    assert all(item["method"] == "GET" for item in dials)
    assert "tracking" not in dials[1]["url"] or "v=" in dials[1]["query"]
    assert all("__rd_verify" not in item["path"] for item in dials)
    source = q(
        ctx,
        "SELECT upstream_collection_method, use_basis FROM sources",
    )[0]
    basis = json.loads(source["use_basis"])
    assert source["upstream_collection_method"] == "Facebook"
    assert basis["requested_url"] == SHARE_R
    assert basis["resolved_url"] == REEL_URL
    assert basis["extractor_chain"] == ["FacebookReel", "Facebook"]
    assert "tracking" not in basis["resolved_url"]
    row = step_data(job)["rows"][0]
    assert row["requested_url"] == SHARE_R
    assert row["resolved_url"] == REEL_URL
    assert row["extractor_chain"] == ["FacebookReel", "Facebook"]
    checkpoint = json.loads(q(ctx, "SELECT checkpoint FROM job_steps")[0]["checkpoint"])
    assert "tracking" not in json.dumps(checkpoint)
    assert q(ctx, "SELECT text FROM item_revisions")[0]["text"] == "Synthetic reel"


def test_share_resume_reuses_the_canonical_url(ctx, project, resolver, monkeypatch):
    allow_facebook(resolver)
    routes = {
        f"/share/r/{SHARE_TOKEN}/": {"status": 302, "location": f"{REEL_URL}/?tracking=1"},
        "/watch/": {"status": 200, "body": facebook_page(REEL_ID)},
    }
    dials = route_dial(monkeypatch, routes)

    def crash(step):
        if (step.checkpoint or {}).get("phase") == "metadata":
            raise SystemExit(7)

    monkeypatch.setattr(remote_media, "_note", crash)
    req, permit = live_request(
        ctx,
        project,
        SHARE_R,
        ["media_metadata", "subtitles"],
        [
            ("FacebookReel", ["www.facebook.com"]),
            ("Facebook", ["m.facebook.com", "www.facebook.com"]),
        ],
        ["FacebookReel"],
    )
    env = app.submit_collect(ctx, req)
    assert env.status == "accepted", env.error
    with pytest.raises(SystemExit):
        with jobs.Executor(ctx.store) as executor:
            executor.run_once()
    routes["/watch/"] = {"status": 200, "body": facebook_page("999")}
    monkeypatch.setattr(remote_media, "_note", lambda step: None)
    with jobs.Executor(ctx.store) as executor:
        assert executor.run_once()
    job = ctx.store.get(m.Job, env.data["job_id"])
    assert job.state == "partial"
    assert job.stop_reason == "data_stale"
    assert [item["path"] for item in dials].count(f"/share/r/{SHARE_TOKEN}/") == 1
    assert q(ctx, "SELECT native_id FROM items")[0]["native_id"] == REEL_ID
    assert ctx.store.get(m.Permit, permit).used == 1


def test_share_hop_without_facebook_approval_stores_nothing(ctx, project, resolver, monkeypatch):
    allow_facebook(resolver)
    dials = route_dial(
        monkeypatch,
        {
            f"/share/r/{SHARE_TOKEN}/": {
                "status": 302,
                "location": f"{REEL_URL}/?tracking=1",
            }
        },
    )
    req, _permit = live_request(
        ctx,
        project,
        SHARE_R,
        ["media_metadata"],
        [("FacebookReel", ["www.facebook.com"])],
        ["FacebookReel"],
    )
    job = run(ctx, req)
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.SCOPE_DENIED.value
    assert "Facebook" in job.error["next_action"]
    assert "m.facebook.com" in job.error["next_action"]
    assert [item["path"] for item in dials] == [f"/share/r/{SHARE_TOKEN}/"]
    assert stored_rows(ctx) == 0


def test_reel_delegation_without_facebook_approval_is_refused(ctx, project, resolver, monkeypatch):
    allow_facebook(resolver)
    dials = route_dial(monkeypatch, {})
    req, _permit = live_request(
        ctx,
        project,
        REEL_URL,
        ["media_metadata"],
        [("FacebookReel", ["www.facebook.com"])],
        ["FacebookReel"],
    )
    job = run(ctx, req)
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.SCOPE_DENIED.value
    assert job.error["next_action"] == "Approve Facebook for m.facebook.com"
    assert dials == []
    assert stored_rows(ctx) == 0


def test_share_login_redirect_is_auth_required(ctx, project, resolver, monkeypatch):
    allow_facebook(resolver)
    dials = route_dial(
        monkeypatch,
        {
            f"/share/r/{SHARE_TOKEN}/": {
                "status": 302,
                "location": "https://www.facebook.com/login/?next=1",
            }
        },
    )
    req, _permit = live_request(
        ctx,
        project,
        SHARE_R,
        ["media_metadata"],
        [("FacebookReel", ["www.facebook.com"])],
        ["FacebookReel"],
    )
    job = run(ctx, req)
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.AUTH_REQUIRED.value
    assert job.error["message"] == "Login required"
    assert job.error["next_action"] == "Stop. Do not retry a login wall."
    assert len(dials) == 1
    assert stored_rows(ctx) == 0


def test_share_200_without_a_login_form_is_unsupported(ctx, project, resolver, monkeypatch):
    allow_facebook(resolver)
    dials = route_dial(
        monkeypatch,
        {f"/share/r/{SHARE_TOKEN}/": {"status": 200, "body": b"<html>hello</html>"}},
    )
    req, _permit = live_request(
        ctx,
        project,
        SHARE_R,
        ["media_metadata"],
        [("FacebookReel", ["www.facebook.com"])],
        ["FacebookReel"],
    )
    job = run(ctx, req)
    assert job.state == "failed"
    assert job.error["code"] == m.ErrorCode.UNSUPPORTED.value
    assert job.error["message"] == "The share link did not redirect"
    assert len(dials) == 1
    assert stored_rows(ctx) == 0


def test_share_redirect_to_an_unapproved_host_is_not_dialed(ctx, project, resolver, monkeypatch):
    allow_facebook(resolver)
    dials = route_dial(
        monkeypatch,
        {
            f"/share/r/{SHARE_TOKEN}/": {
                "status": 302,
                "location": f"https://evil.fixture.test/reel/{REEL_ID}",
            }
        },
    )
    req, _permit = live_request(
        ctx,
        project,
        SHARE_R,
        ["media_metadata"],
        [("FacebookReel", ["www.facebook.com"])],
        ["FacebookReel"],
    )
    job = run(ctx, req)
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.SCOPE_DENIED.value
    assert job.error["message"] == "Outbound URL is outside the network policy"
    assert [item["host"] for item in dials] == ["www.facebook.com"]
    assert stored_rows(ctx) == 0


def test_share_post_links_are_refused_at_submit(ctx, project):
    before = jobs_count(ctx)
    configure(ctx, [("Facebook", ["www.facebook.com"])])
    env = app.submit_collect(
        ctx,
        remote_request(
            project,
            "https://www.facebook.com/share/p/PostToken/",
            ["media_metadata"],
            request_key="share-p",
        ),
    )
    assert env.error.code == m.ErrorCode.UNSUPPORTED
    assert env.error.next_action == "Use the Facebook post collector for /share/p/ links."
    assert jobs_count(ctx) == before


def test_share_video_resolves_to_facebook(ctx, project, resolver, monkeypatch):
    allow_facebook(resolver)
    dials = route_dial(
        monkeypatch,
        {
            "/share/v/VideoToken/": {
                "status": 302,
                "location": f"{WATCH_URL}&tracking=1",
            },
            "/watch/": {"status": 200, "body": facebook_page(REEL_ID)},
        },
    )
    req, _permit = live_request(
        ctx,
        project,
        SHARE_V,
        ["media_metadata"],
        [("Facebook", ["www.facebook.com", "m.facebook.com"])],
        ["Facebook"],
    )
    job = run(ctx, req)
    assert job.state == "succeeded", job.error
    basis = json.loads(q(ctx, "SELECT use_basis FROM sources")[0]["use_basis"])
    assert basis["requested_url"] == SHARE_V
    assert basis["resolved_url"] == WATCH_URL
    assert basis["extractor_chain"] == ["Facebook"]
    assert [item["path"] for item in dials] == ["/share/v/VideoToken/", "/watch/"]


def test_mobile_host_approval_does_not_allow_the_desktop_dial(ctx, project, resolver, monkeypatch):
    allow_facebook(resolver)
    dials = route_dial(
        monkeypatch,
        {
            f"/share/r/{SHARE_TOKEN}/": {
                "status": 302,
                "location": f"{REEL_URL}/?tracking=1",
            },
            "/watch/": {"status": 200, "body": facebook_page(REEL_ID)},
        },
    )
    req, _permit = live_request(
        ctx,
        project,
        SHARE_R,
        ["media_metadata"],
        [
            ("FacebookReel", ["www.facebook.com"]),
            ("Facebook", ["m.facebook.com"]),
        ],
        ["FacebookReel"],
    )
    job = run(ctx, req)
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.SCOPE_DENIED.value
    assert [item["path"] for item in dials] == [f"/share/r/{SHARE_TOKEN}/"]
    assert stored_rows(ctx) == 0


def _register_hops():
    for cls in (HopAIE, HopBIE, HopCIE, HopDIE, OuterIE, InnerIE):
        remote_media.register_test_extractor(cls)


def _unregister_hops():
    for cls in (HopAIE, HopBIE, HopCIE, HopDIE, OuterIE, InnerIE):
        remote_media.unregister_test_extractor(cls)


def test_two_hops_store_and_a_third_is_refused(ctx, project):
    HOP_CALLS.clear()
    HopCIE.stop = False
    _register_hops()
    try:
        approvals = [
            ("HopA", ["hop.fixture.test"]),
            ("HopB", ["hop.fixture.test"]),
            ("HopC", ["hop.fixture.test"]),
            ("HopD", ["hop.fixture.test"]),
        ]
        req, _permit = live_request(
            ctx,
            project,
            "https://hop.fixture.test/a/1",
            ["media_metadata"],
            approvals,
            ["HopA"],
        )
        job = run(ctx, req)
        assert job.state == "succeeded", job.error
        basis = json.loads(q(ctx, "SELECT use_basis FROM sources")[0]["use_basis"])
        assert basis["extractor_chain"] == ["HopA", "HopB", "HopC"]
        assert q(ctx, "SELECT text FROM item_revisions")[0]["text"] == "Two hops"
        HopCIE.stop = True
        HOP_CALLS.clear()
        limited, _permit = live_request(
            ctx,
            project,
            "https://hop.fixture.test/a/1",
            ["media_metadata"],
            approvals,
            ["HopA"],
        )
        limited = limited.model_copy(update={"request_key": "hop-limit"})
        stopped = run(ctx, limited)
        assert stopped.state == "failed"
        assert stopped.error["code"] == m.ErrorCode.UNSUPPORTED.value
        assert "2 hops" in stopped.error["message"]
        assert "D" not in HOP_CALLS
        assert q(ctx, "SELECT COUNT(*) AS n FROM items")[0]["n"] == 1
    finally:
        HopCIE.stop = False
        _unregister_hops()


def test_url_transparent_outer_fields_override(ctx, project):
    _register_hops()
    try:
        req, _permit = live_request(
            ctx,
            project,
            "https://hop.fixture.test/outer/1",
            ["media_metadata"],
            [
                ("Outer", ["hop.fixture.test"]),
                ("Inner", ["hop.fixture.test"]),
            ],
            ["Outer"],
        )
        job = run(ctx, req)
        assert job.state == "succeeded", job.error
        assert q(ctx, "SELECT text FROM item_revisions")[0]["text"] == "Outer title"
        assert q(ctx, "SELECT native_id FROM items")[0]["native_id"] == "inner-id"
        basis = json.loads(q(ctx, "SELECT use_basis FROM sources")[0]["use_basis"])
        assert basis["extractor_chain"] == ["Outer", "Inner"]
    finally:
        _unregister_hops()


@pytest.mark.parametrize("suffix", [b"", b"\rX-Injected: pwned=1", b"\n"])
def test_cookie_header_line_is_refused(suffix):
    marker = marker_value()
    with pytest.raises(m.CoreError) as exc:
        remote_media._parse_cookie_secret(b"Cookie: sid=" + marker.encode() + suffix)
    assert exc.value.detail.code == m.ErrorCode.INPUT_INVALID
    assert exc.value.detail.message == "A Netscape cookie file is required"
    assert marker not in str(exc.value)
    assert marker not in repr(exc.value)
    host_only = remote_media._parse_cookie_secret(
        netscape("#HttpOnly_.www.fixture.test", "FALSE", "/", "TRUE", "4000000000", "sid", marker)
    )
    assert host_only[0].host_only is True
    assert host_only[0].secure is True
    sibling_hosts = frozenset({"www.fixture.test", "m.fixture.test"})
    assert (
        remote_media._cookie_header(host_only, "https://m.fixture.test/sib-asset", sibling_hosts)
        is None
    )
    assert (
        remote_media._cookie_header(
            host_only, "http://www.fixture.test/sib/clip", frozenset({"www.fixture.test"})
        )
        is None
    )
    expired = remote_media._parse_cookie_secret(
        netscape(".fixture.test", "TRUE", "/", "FALSE", "1", "sid", marker)
    )
    assert (
        remote_media._cookie_header(
            expired, "https://www.fixture.test/", frozenset({"www.fixture.test"})
        )
        is None
    )


def test_cookie_secret_does_not_echo_rejected_bytes():
    marker = marker_value("zz")
    with pytest.raises(m.CoreError) as exc:
        remote_media._parse_cookie_secret(b"not a cookie file " + marker.encode())
    assert exc.value.detail.code == m.ErrorCode.INPUT_INVALID
    assert marker not in str(exc.value)
    assert marker not in repr(exc.value)


def test_cookie_reaches_only_the_approved_host(ctx, project, origin, resolver, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    marker = marker_value()
    secret = netscape("www.fixture.test", "FALSE", "/", "FALSE", "0", "sid", marker)
    store, auth_ref = put_secret(tmp_path, ctx, secret)
    port = origin.server_address[1]
    payload = info_dict(port, "sibclip", title="Sibling clip")
    origin.routes["sib/clip"] = {"status": 200, "body": dump(payload)}
    origin.routes["sib-asset"] = {"status": 200, "body": b"ok", "type": "text/plain"}
    resolver.extra["www.fixture.test"] = ["127.0.0.1"]
    resolver.extra["m.fixture.test"] = ["127.0.0.1"]
    remote_media.register_test_extractor(SiblingIE)
    try:
        req, _permit = live_request(
            ctx,
            project,
            f"http://www.fixture.test:{port}/sib/clip",
            ["media_metadata"],
            [("Sibling", ["www.fixture.test", "m.fixture.test"], auth_ref)],
            ["Sibling"],
        )
        job = run(ctx, req, secret_store=store)
    finally:
        remote_media.unregister_test_extractor(SiblingIE)
    assert job.state == "succeeded", job.error
    by_path = {hit["path"]: hit["cookie"] for hit in origin.hits}
    assert marker in by_path["/sib/clip"]
    assert by_path["/sib-asset"] is None
    assert_marker_absent(ctx, marker, job, caplog)


def test_secure_cookie_is_not_sent_over_http(ctx, project, origin, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    marker = marker_value("sec")
    secret = netscape("media.fixture.test", "FALSE", "/", "TRUE", "0", "sid", marker)
    store, auth_ref = put_secret(tmp_path, ctx, secret)
    req, _permit = live_request(
        ctx,
        project,
        url_for(origin, "clip"),
        ["media_metadata"],
        [("MetamuseFixture", ["media.fixture.test"], auth_ref)],
        ["MetamuseFixture"],
    )
    job = run(ctx, req, secret_store=store)
    assert job.state == "succeeded", job.error
    assert origin.hits
    assert all(hit["cookie"] is None for hit in origin.hits)
    assert_marker_absent(ctx, marker, job, caplog)


def test_cookie_is_not_sent_to_an_unapproved_redirect(ctx, project, origin, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    marker = marker_value("cdn")
    secret = netscape(".fixture.test", "TRUE", "/", "FALSE", "0", "sid", marker)
    store, auth_ref = put_secret(tmp_path, ctx, secret)
    port = origin.server_address[1]
    origin.routes["away"] = {
        "status": 302,
        "location": f"http://cdn.fixture.test:{port}/cdn-asset",
    }
    req, _permit = live_request(
        ctx,
        project,
        url_for(origin, "away"),
        ["media_metadata"],
        [("MetamuseFixture", ["media.fixture.test"], auth_ref)],
        ["MetamuseFixture"],
    )
    job = run(ctx, req, secret_store=store)
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.SCOPE_DENIED.value
    assert paths(origin) == ["/away"]
    assert marker in origin.hits[0]["cookie"]
    assert_marker_absent(ctx, marker, job, caplog)


def test_cookie_binding_mismatch_dials_nothing(ctx, project, origin, tmp_path, monkeypatch):
    marker = marker_value("bad")
    secret = netscape("media.fixture.test", "FALSE", "/", "FALSE", "0", "sid", marker)
    store, auth_ref = put_secret(tmp_path, ctx, secret, account="other-account")
    dials = []

    def dial(*args, **kwargs):
        del kwargs
        dials.append(args)
        raise AssertionError("dial")

    monkeypatch.setattr(remote_media, "_dial", dial)
    req, _permit = live_request(
        ctx,
        project,
        url_for(origin, "clip"),
        ["media_metadata"],
        [("MetamuseFixture", ["media.fixture.test"], auth_ref)],
        ["MetamuseFixture"],
    )
    job = run(ctx, req, secret_store=store)
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.AUTH_REQUIRED.value
    assert job.error["message"] == "Secret file or reference is unavailable or unsafe"
    assert dials == []
    assert origin.hits == []
    assert marker not in json.dumps(job.error)
    assert stored_rows(ctx) == 0


def test_set_cookie_is_not_replayed(ctx, project, origin, tmp_path):
    marker = marker_value("set")
    port = origin.server_address[1]
    origin.routes["clip"] = {
        "status": 200,
        "body": lambda: dump(info_dict(port, "clip")),
        "headers": {"Set-Cookie": f"injected={marker}"},
    }
    req, _permit = prepare(ctx, project, origin, "clip", ["media_metadata", "subtitles"])
    job = finish(ctx, req)
    assert job.state == "succeeded", job.error
    assert all(hit["cookie"] is None for hit in origin.hits)
    for path in ctx.paths.root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert marker.encode() not in path.read_bytes(), path


def test_challenge_with_a_cookie_still_stops(ctx, project, resolver, monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    marker = marker_value("ch")
    secret = netscape(
        "#HttpOnly_www.facebook.com", "FALSE", "/", "TRUE", "4000000000", "sid", marker
    )
    store, auth_ref = put_secret(tmp_path, ctx, secret)
    allow_facebook(resolver)
    dials = route_dial(
        monkeypatch,
        {
            "/watch/": {
                "status": 403,
                "body": b"fetch('/__rd_verify_synthetic')",
                "reason": "Forbidden",
            }
        },
    )
    req, _permit = live_request(
        ctx,
        project,
        WATCH_URL,
        ["media_metadata"],
        [("Facebook", ["www.facebook.com"], auth_ref)],
        ["Facebook"],
    )
    job = run(ctx, req, secret_store=store)
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.CHECKPOINT_REQUIRED.value
    assert len(dials) == 1
    assert marker in (dials[0]["cookie"] or "")
    assert all(not item["path"].startswith("/__rd_verify") for item in dials)
    assert stored_rows(ctx) == 0
    assert_marker_absent(ctx, marker, job, caplog)


def test_approval_auth_ref_is_config_only():
    with pytest.raises(ValidationError):
        app.Config.model_validate(
            {
                "remote_media": {
                    "approvals": [
                        {
                            "extractor": "Facebook",
                            "hosts": ["www.facebook.com"],
                            "auth_ref": "not-a-ref",
                        }
                    ]
                }
            }
        )
    loaded = app.Config.model_validate(
        {
            "remote_media": {
                "approvals": [
                    {
                        "extractor": "Facebook",
                        "hosts": ["www.facebook.com"],
                        "auth_ref": "auth_" + "ab" * 16,
                    }
                ]
            }
        }
    )
    assert loaded.remote_media.approvals[0].auth_ref == "auth_" + "ab" * 16


def _checked_net():
    def checker(url, *, purpose):
        return safety.CheckedURL(url, "www.facebook.com", 443, ("127.0.0.1",), purpose)

    return remote_media._Net(
        checker=checker,
        budget=remote_media._Budget(0, 10000),
        media_max=10000,
        timeout=2,
    )


def test_page_cookie_does_not_reach_cdn(ctx, project, origin, resolver, tmp_path):
    """A page-host cookie stays on that host. A header line is not a cookie file."""

    marker = marker_value("cdn")
    secret = netscape("www.fixture.test", "FALSE", "/", "FALSE", "0", "sid", marker)
    store, auth_ref = put_secret(tmp_path, ctx, secret)
    port = origin.server_address[1]
    resolver.extra["www.fixture.test"] = ["127.0.0.1"]
    resolver.extra["cdn.fixture.test"] = ["127.0.0.1"]

    class PageWithCdnMediaIE(InfoExtractor):
        _VALID_URL = r"https?://www\.fixture\.test(?::\d+)?/watch/(?P<id>[A-Za-z0-9]+)"

        def _real_extract(self, url):
            video_id = self._match_id(url)
            self._request_webpage(url, video_id, note=False)
            return {
                "id": video_id,
                "title": "CDN test",
                "timestamp": 1700000000,
                "channel_id": "chan",
                "formats": [
                    {
                        "url": f"http://cdn.fixture.test:{port}/clip/media.wav",
                        "ext": "wav",
                        "protocol": "http",
                    }
                ],
                "subtitles": {
                    "en": [
                        {
                            "url": f"http://cdn.fixture.test:{port}/clip/provided.vtt",
                            "ext": "vtt",
                        }
                    ]
                },
            }

    origin.routes["watch/clip"] = {"status": 200, "body": b"ok", "type": "text/plain"}
    remote_media.register_test_extractor(PageWithCdnMediaIE)
    try:
        req, _permit = live_request(
            ctx,
            project,
            f"http://www.fixture.test:{port}/watch/clip",
            ["media_metadata", "subtitles", "media_file"],
            [("PageWithCdnMedia", ["www.fixture.test", "cdn.fixture.test"], auth_ref)],
            ["PageWithCdnMedia"],
        )
        job = run(ctx, req, secret_store=store)
    finally:
        remote_media.unregister_test_extractor(PageWithCdnMediaIE)
    assert job.state == "succeeded", job.error
    cookies_by_path = {hit["path"]: hit["cookie"] for hit in origin.hits}
    assert cookies_by_path["/watch/clip"] == f"sid={marker}"
    assert cookies_by_path["/clip/provided.vtt"] is None
    assert cookies_by_path["/clip/media.wav"] is None
    same_approval = frozenset({"www.fixture.test", "cdn.fixture.test"})
    domain_cookie = remote_media._parse_cookie_secret(
        netscape(".www.fixture.test", "TRUE", "/", "FALSE", "0", "sid", marker)
    )
    assert (
        remote_media._cookie_header(
            domain_cookie, f"http://www.fixture.test:{port}/watch/clip", same_approval
        )
        == f"sid={marker}"
    )
    assert (
        remote_media._cookie_header(
            domain_cookie, f"http://cdn.fixture.test:{port}/clip/media.wav", same_approval
        )
        is None
    )


@pytest.mark.parametrize("field", ["name", "value"])
@pytest.mark.parametrize(
    "invalid", ["\r", "\x00", "\x01", "\x07", "\x1f", "\x7f", ";", ",", "\\", '"', " "]
)
def test_cookie_line_rejects_controls_and_smuggling(field, invalid):
    name = f"si{invalid}d" if field == "name" else "sid"
    value = f"va{invalid}lue" if field == "value" else marker_value("ctl")
    secret = netscape("www.facebook.com", "FALSE", "/", "TRUE", "0", name, value)
    with pytest.raises(m.CoreError) as exc:
        remote_media._parse_cookie_secret(secret)
    assert exc.value.detail.code == m.ErrorCode.INPUT_INVALID
    assert exc.value.detail.message == "Cookie file line 2 is invalid"
    assert value not in str(exc.value)


@pytest.mark.parametrize(
    "domain", ["..facebook.com", "www.facebook.com..", "bad host.test", "bad/host.test"]
)
def test_cookie_invalid_domain_rejects_the_whole_file(domain):
    valid = netscape("www.facebook.com", "FALSE", "/", "TRUE", "0", "sid", "valid")
    bad = netscape(domain, "FALSE", "/", "TRUE", "0", "sid", "invalid").split(b"\n")[1]
    with pytest.raises(m.CoreError) as exc:
        remote_media._parse_cookie_secret(valid + bad + b"\n")
    assert exc.value.detail.message == "Cookie file line 3 is invalid"
    assert domain not in str(exc.value)


@pytest.mark.parametrize("value", ["", '""', '"quoted-value"', "normal=value"])
@pytest.mark.parametrize("terminator", [b"\n", b"\r\n", b""])
def test_cookie_empty_and_quoted_values_keep_the_seventh_column(value, terminator):
    secret = f"www.facebook.com\tFALSE\t/\tTRUE\t0\tsid\t{value}".encode() + terminator
    cookies = remote_media._parse_cookie_secret(secret)
    assert cookies[0].value == value
    assert (
        remote_media._cookie_header(
            cookies, "https://www.facebook.com/watch/", frozenset({"www.facebook.com"})
        )
        == f"sid={value}"
    )


def test_host_normalization_binds_idn_cookies(resolver):
    marker = marker_value("idn")
    cookies = remote_media._parse_cookie_secret(
        netscape("münchen.test", "FALSE", "/", "TRUE", "0", "sid", marker)
    )
    assert cookies[0].domain == "xn--mnchen-3ya.test"
    allowed = frozenset(remote_media._hosts(["münchen.test", "münchen.test."]))
    assert allowed == {"xn--mnchen-3ya.test"}
    resolver.extra["xn--mnchen-3ya.test"] = ["93.184.216.34"]
    checked = safety.check_url(
        "https://münchen.test/watch",
        purpose="request",
        resolver=resolver,
        allowed_hosts=("münchen.test",),
    )
    assert checked.hostname == "xn--mnchen-3ya.test"
    assert remote_media._cookie_header(cookies, checked.url, allowed) == f"sid={marker}"


def test_share_double_dot_host_is_refused_at_submit(ctx, project):
    configure(ctx, [("FacebookReel", ["www.facebook.com"])])
    before = jobs_count(ctx)
    req = remote_request(
        project, "https://www.facebook.com../share/r/Ab12token/", ["media_metadata"]
    )
    permit = issue(ctx, project, req, ["FacebookReel"])
    env = app.submit_collect(ctx, req.model_copy(update={"access_grant_id": permit}))
    assert env.status != "accepted"
    assert env.error.code == m.ErrorCode.SCOPE_DENIED
    assert jobs_count(ctx) == before


@pytest.mark.parametrize(
    "variant",
    [
        "https://www.facebook.com//__rd_verify_123",
        "https://www.facebook.com/./__rd_verify_123",
        "https://www.facebook.com/__rd_%76erify_123",
        "https://www.facebook.com/__RD_VERIFY_123",
        "https://www.facebook.com/%5f%5frd_verify_123",
        "https://www.facebook.com/foo/../__rd_verify_123",
        "https://www.facebook.com/challenge.html?hash429=abc",
        "https://www.facebook.com/CHALLENGE.html",
        "https://www.facebook.com/watch/?hash429=abc",
        "https://www.facebook.com/segment/__rd_verify_123",
    ],
)
def test_challenge_path_variants_stop_before_the_dial(monkeypatch, variant):
    dialed = []

    def dial(method, url, headers, body, checked, timeout, limit):
        del method, headers, body, checked, timeout, limit
        dialed.append(url)
        return 200, {}, b"ok", "127.0.0.1", "OK"

    monkeypatch.setattr(remote_media, "_dial", dial)
    net = _checked_net()
    with pytest.raises(m.CoreError) as exc:
        remote_media._exchange(net, "GET", variant, {}, None, follow=True)
    assert exc.value.detail.code == m.ErrorCode.CHECKPOINT_REQUIRED
    assert dialed == []


def test_share_challenge_redirect_stops_before_the_dial(ctx, project, resolver, monkeypatch):
    allow_facebook(resolver)
    dials = route_dial(
        monkeypatch,
        {
            f"/share/r/{SHARE_TOKEN}/": {
                "status": 302,
                "location": "https://www.facebook.com/__rd_%76erify_123",
            }
        },
    )
    req, _permit = live_request(
        ctx,
        project,
        SHARE_R,
        ["media_metadata"],
        [("FacebookReel", ["www.facebook.com"])],
        ["FacebookReel"],
    )
    job = run(ctx, req)
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.CHECKPOINT_REQUIRED.value
    assert job.error["message"] == "Platform challenge"
    assert [item["path"] for item in dials] == [f"/share/r/{SHARE_TOKEN}/"]


@pytest.mark.parametrize("status", [200, 302, 401, 403, 429, 503])
@pytest.mark.parametrize(
    "headers,payload,reason",
    [
        ({"cf-mitigated": "managed Challenge"}, b"ok", "OK"),
        ({}, b"fetch(`/__rd_verify_123`)", ""),
        ({}, b"fetch(new Request('/__rd_verify_123'))", ""),
        ({}, b"window.location.replace('/__rd_verify_123')", ""),
        ({"Refresh": "0;url=/__rd_verify_123"}, b"ok", ""),
        ({"Location": "/__rd_verify_123"}, b"ok", ""),
        ({}, b"plain", "Client challenge"),
    ],
)
def test_challenge_markers_stop_at_every_status(monkeypatch, status, headers, payload, reason):
    assert remote_media._challenge_marked(status, headers, payload, reason)
    dialed = []

    def dial(method, url, request_headers, body, checked, timeout, limit):
        dialed.append(url)
        return status, headers, payload, "127.0.0.1", reason

    monkeypatch.setattr(remote_media, "_dial", dial)
    net = _checked_net()
    with pytest.raises(m.CoreError) as exc:
        remote_media._exchange(net, "GET", WATCH_URL, {}, None, follow=True)
    assert exc.value.detail.code == m.ErrorCode.CHECKPOINT_REQUIRED
    assert len(dialed) == 1
    assert net.budget.used == len(payload)


def test_vk_solver_cannot_be_submitted(ctx, project):
    configure(ctx, [("VK", ["vk.com"])])
    before = jobs_count(ctx)
    req = remote_request(project, "https://vk.com/video-1_2", ["media_metadata"])
    permit = issue(ctx, project, req, ["VK"])
    env = app.submit_collect(ctx, req.model_copy(update={"access_grant_id": permit}))
    assert env.status != "accepted"
    assert env.error.code == m.ErrorCode.UNSUPPORTED
    assert env.error.message == "The extractor answers platform challenges"
    assert jobs_count(ctx) == before


def test_vk_solver_hop_never_computes_or_dials_an_answer(ctx, project, resolver, monkeypatch):
    resolver.extra["hop.fixture.test"] = ["127.0.0.1"]
    resolver.extra["vk.com"] = ["127.0.0.1"]
    dials = []

    def dial(method, url, headers, body, checked, timeout, limit):
        dials.append(url)
        parts = urlsplit(url)
        if parts.path == "/al_video.php" and len(dials) == 1:
            return (
                302,
                {"Location": "https://vk.com/challenge.html?hash429=abc"},
                b"",
                "127.0.0.1",
                "Found",
            )
        if parts.path == "/challenge.html" and "key=" not in parts.query:
            return (
                200,
                {},
                b"var salt = (function(){ return '42'; })(); var hash",
                "127.0.0.1",
                "OK",
            )
        if parts.path == "/challenge.html" and "key=" in parts.query:
            return 200, {}, b"solved", "127.0.0.1", "OK"
        return 404, {}, b"done", "127.0.0.1", "Not Found"

    monkeypatch.setattr(remote_media, "_dial", dial)

    class ToVkIE(InfoExtractor):
        _VALID_URL = r"https://hop\.fixture\.test/tovk/(?P<id>\d+)"

        def _real_extract(self, url):
            del url
            return self.url_result("https://vk.com/video-1_2", "VK", "1")

    remote_media.register_test_extractor(ToVkIE)
    try:
        hopped, _permit = live_request(
            ctx,
            project,
            "https://hop.fixture.test/tovk/1",
            ["media_metadata"],
            [("ToVk", ["hop.fixture.test"]), ("VK", ["vk.com"])],
            ["ToVk"],
        )
        job = run(ctx, hopped)
    finally:
        remote_media.unregister_test_extractor(ToVkIE)
    assert job.state == "failed"
    assert job.error["code"] == m.ErrorCode.UNSUPPORTED.value
    assert job.error["message"] == "The extractor answers platform challenges"
    assert dials == []


@pytest.mark.parametrize(
    "target,code",
    [
        ("/checkpoint/1501092823525282/", m.ErrorCode.CHECKPOINT_REQUIRED),
        ("/zh_TW/login/", m.ErrorCode.AUTH_REQUIRED),
        ("/zh_TW/login.php?next=1", m.ErrorCode.AUTH_REQUIRED),
        ("/login.php?next=1", m.ErrorCode.AUTH_REQUIRED),
    ],
)
def test_login_and_checkpoint_redirects_stop_before_the_dial(
    ctx, project, resolver, monkeypatch, target, code
):
    allow_facebook(resolver)
    dials = route_dial(
        monkeypatch,
        {
            "/watch/": {"status": 302, "location": f"https://www.facebook.com{target}"},
            target.split("?")[0]: {"status": 200, "body": b"<html>wall</html>"},
            f"/video/tahoe/async/{REEL_ID}/": {"status": 200, "body": b"for (;;);{}"},
        },
    )
    req, _permit = live_request(
        ctx,
        project,
        WATCH_URL,
        ["media_metadata"],
        [("Facebook", ["www.facebook.com"])],
        ["Facebook"],
    )
    job = run(ctx, req)
    assert job.state == "awaiting_user"
    assert job.error["code"] == code.value
    assert [item["path"] for item in dials] == ["/watch/"]
    assert all(item["method"] != "POST" for item in dials)
    assert stored_rows(ctx) == 0


@pytest.mark.parametrize(
    "resource,path", [("subtitles", "/files/provided.vtt"), ("media_file", "/files/media.wav")]
)
@pytest.mark.parametrize(
    "wall,code",
    [
        ("/zh_TW/login/", m.ErrorCode.AUTH_REQUIRED),
        ("/checkpoint/1/", m.ErrorCode.CHECKPOINT_REQUIRED),
    ],
)
def test_media_and_subtitle_login_redirects_halt(
    ctx, project, resolver, monkeypatch, resource, path, wall, code
):
    resolver.extra["www.fixture.test"] = ["127.0.0.1"]

    class LoginMediaIE(InfoExtractor):
        _VALID_URL = r"https://www\.fixture\.test/media-login/(?P<id>[A-Za-z0-9]+)"

        def _real_extract(self, url):
            return {
                "id": self._match_id(url),
                "title": "Login media",
                "timestamp": 1700000000,
                "channel_id": "chan",
                "formats": [
                    {
                        "url": "https://www.fixture.test/files/media.wav",
                        "ext": "wav",
                        "protocol": "https",
                    }
                ],
                "subtitles": {
                    "en": [
                        {
                            "url": "https://www.fixture.test/files/provided.vtt",
                            "ext": "vtt",
                        }
                    ]
                },
            }

    remote_media.register_test_extractor(LoginMediaIE)
    dials = route_dial(
        monkeypatch,
        {
            path: {"status": 302, "location": f"https://www.fixture.test{wall}"},
            wall: {"status": 200, "body": b"<html>wall</html>", "type": "text/html"},
        },
    )
    try:
        req, _permit = live_request(
            ctx,
            project,
            "https://www.fixture.test/media-login/1",
            ["media_metadata", resource],
            [("LoginMedia", ["www.fixture.test"])],
            ["LoginMedia"],
        )
        job = run(ctx, req)
    finally:
        remote_media.unregister_test_extractor(LoginMediaIE)
    assert job.state == "awaiting_user"
    assert job.error["code"] == code.value
    assert [item["path"] for item in dials] == [path]


@pytest.mark.parametrize("case", ["emptyfmt", "plhop", "lazy", "lazy-error", "lazy-process"])
def test_process_ie_result_stays_inside_the_hop_loop(ctx, project, resolver, case):
    resolver.extra["hop.fixture.test"] = ["127.0.0.1"]
    hop_count = [0]
    active_during_lazy = []

    class EmptyFormatsIE(InfoExtractor):
        _VALID_URL = r"https://hop\.fixture\.test/emptyfmt/(?P<id>[A-Za-z0-9]+)"

        def _real_extract(self, url):
            del url
            return {"id": "1", "title": "No formats", "formats": []}

    class PlaylistHopIE(InfoExtractor):
        _VALID_URL = r"https://hop\.fixture\.test/plhop/(?P<id>\d+)"

        def _real_extract(self, url):
            number = int(self._match_id(url))
            hop_count[0] += 1
            if number < 3:
                return self.url_result(
                    f"https://hop.fixture.test/plhop/{number + 1}",
                    "PlaylistHop",
                    str(number + 1),
                )
            if number == 3:
                return {
                    "_type": "playlist",
                    "id": "pl-3",
                    "entries": [
                        self.url_result("https://hop.fixture.test/plhop/4", "PlaylistHop", "4")
                    ],
                }
            return {
                "id": str(number),
                "title": f"Reached hop {number}",
                "uploader_id": "chan1",
                "timestamp": 1700000000,
                "formats": [_HOP_FORMAT],
            }

    class LazyPlaylistIE(InfoExtractor):
        _VALID_URL = r"https://hop\.fixture\.test/lazy/(?P<id>\d+)"

        def _entries(self):
            active_during_lazy.append(remote_media._extraction_active.get())
            if case == "lazy-error":
                raise ExtractorError("synthetic invalid lazy response")
            if case == "lazy-process":
                subprocess.run([sys.executable, "-c", "pass"], check=True)
            yield {
                "id": "1",
                "title": "Lazy entry",
                "uploader_id": "chan1",
                "timestamp": 1700000000,
                "formats": [_HOP_FORMAT],
            }

        def _real_extract(self, url):
            del url
            return self.playlist_result(self._entries(), "lazy-1")

    classes = (EmptyFormatsIE, PlaylistHopIE, LazyPlaylistIE)
    for cls in classes:
        remote_media.register_test_extractor(cls)
    try:
        key = {"emptyfmt": "EmptyFormats", "plhop": "PlaylistHop"}.get(case, "LazyPlaylist")
        path = case if case in {"emptyfmt", "plhop"} else "lazy"
        req, _permit = live_request(
            ctx,
            project,
            f"https://hop.fixture.test/{path}/1",
            ["media_metadata"],
            [(key, ["hop.fixture.test"])],
            [key],
        )
        job = run(ctx, req)
        if case == "lazy":
            assert job.state == "succeeded", job.error
            assert active_during_lazy == [True]
        else:
            assert job.state == "failed"
            assert job.error["code"] == m.ErrorCode.UNSUPPORTED.value
            if case == "plhop":
                assert "2 hops" in job.error["message"]
                assert hop_count[0] == 3
            elif case == "lazy-process":
                assert job.error["message"] == remote_media._process_error().detail.message
            else:
                assert job.error["message"] == "Extractor failed"
    finally:
        for cls in classes:
            remote_media.unregister_test_extractor(cls)


def test_transparent_inner_identity_and_media_win(ctx, project, resolver, monkeypatch):
    resolver.extra["outer.fixture.test"] = ["127.0.0.1"]
    resolver.extra["inner.fixture.test"] = ["127.0.0.1"]
    dials = route_dial(
        monkeypatch,
        {"/real.wav": {"status": 200, "body": wav_bytes(), "type": "audio/wav"}},
    )

    class ClipOuterIE(InfoExtractor):
        _VALID_URL = r"https://outer\.fixture\.test/clip/(?P<id>\d+)"

        def _real_extract(self, url):
            del url
            return {
                "_type": "url_transparent",
                "url": "https://inner.fixture.test/video/99",
                "ie_key": "ClipInner",
                "section_start": 0,
                "title": "Outer title",
                "formats": [
                    {
                        "url": "https://outer.fixture.test/injected.wav",
                        "ext": "wav",
                        "protocol": "https",
                    }
                ],
            }

    class ClipInnerIE(InfoExtractor):
        _VALID_URL = r"https://inner\.fixture\.test/video/(?P<id>\d+)"

        def _real_extract(self, url):
            del url
            return {
                "id": "99",
                "title": "Inner video",
                "webpage_url": "https://inner.fixture.test/video/99",
                "formats": [
                    {
                        "url": "https://inner.fixture.test/real.wav",
                        "ext": "wav",
                        "protocol": "https",
                    }
                ],
            }

    for cls in (ClipOuterIE, ClipInnerIE):
        remote_media.register_test_extractor(cls)
    try:
        req, _permit = live_request(
            ctx,
            project,
            "https://outer.fixture.test/clip/1",
            ["media_metadata", "media_file"],
            [
                ("ClipOuter", ["outer.fixture.test"]),
                ("ClipInner", ["inner.fixture.test"]),
            ],
            ["ClipOuter"],
        )
        job = run(ctx, req)
    finally:
        for cls in (ClipOuterIE, ClipInnerIE):
            remote_media.unregister_test_extractor(cls)
    assert job.state == "succeeded", job.error
    source = q(
        ctx,
        "SELECT canonical_url, platform, upstream_collection_method, use_basis "
        "FROM sources WHERE native_id='99'",
    )[0]
    assert source["canonical_url"] == "https://inner.fixture.test/video/99"
    assert source["platform"] == "clipinner"
    assert source["upstream_collection_method"] == "ClipInner"
    basis = json.loads(source["use_basis"])
    assert basis["extractor_chain"] == ["ClipOuter", "ClipInner"]
    assert q(ctx, "SELECT text FROM item_revisions")[0]["text"] == "Outer title"
    assert any(item["path"] == "/real.wav" for item in dials)
    assert all("injected.wav" not in item["url"] for item in dials)


def test_share_resume_rechecks_the_resolved_host(ctx, project, resolver, monkeypatch):
    allow_facebook(resolver)
    routes = {
        f"/share/r/{SHARE_TOKEN}/": {
            "status": 302,
            "location": f"https://m.facebook.com/reel/{REEL_ID}?tracking=1",
        },
        "/watch/": {"status": 200, "body": facebook_page(REEL_ID)},
    }
    dials = route_dial(monkeypatch, routes)

    def crash(step):
        if (step.checkpoint or {}).get("phase") == "metadata":
            raise SystemExit(7)

    monkeypatch.setattr(remote_media, "_note", crash)
    req, _permit = live_request(
        ctx,
        project,
        SHARE_R,
        ["media_metadata", "subtitles"],
        [
            ("FacebookReel", ["www.facebook.com", "m.facebook.com"]),
            ("Facebook", ["m.facebook.com", "www.facebook.com"]),
        ],
        ["FacebookReel"],
    )
    env = app.submit_collect(ctx, req)
    assert env.status == "accepted", env.error
    with pytest.raises(SystemExit):
        with jobs.Executor(ctx.store) as executor:
            executor.run_once()
    dialed_before_resume = len(dials)
    configure(
        ctx,
        [
            ("FacebookReel", ["www.facebook.com"]),
            ("Facebook", ["m.facebook.com", "www.facebook.com"]),
        ],
    )
    monkeypatch.setattr(remote_media, "_note", lambda step: None)
    with jobs.Executor(ctx.store) as executor:
        assert executor.run_once()
    job = ctx.store.get(m.Job, env.data["job_id"])
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.SCOPE_DENIED.value
    assert len(dials) == dialed_before_resume


def test_share_rate_limit_keeps_prior_rows_and_retry_after(ctx, project, resolver, monkeypatch):
    allow_facebook(resolver)
    routes = {
        "/watch/": {"status": 200, "body": facebook_page(REEL_ID)},
        f"/share/r/{SHARE_TOKEN}/": {
            "status": 429,
            "headers": {"Retry-After": "45"},
            "body": b"rate limited",
        },
    }
    route_dial(monkeypatch, routes)
    configure(
        ctx,
        [
            ("Facebook", ["www.facebook.com", "m.facebook.com"]),
            ("FacebookReel", ["www.facebook.com"]),
        ],
    )
    first = remote_request(
        project, f"https://www.facebook.com/watch/?v={REEL_ID}", ["media_metadata"]
    )
    req = first.model_copy(
        update={
            "inputs": [first.inputs[0], m.URLInput(kind="url", url=SHARE_R)],
        }
    )
    permit = issue(ctx, project, req, ["Facebook", "FacebookReel"])
    job = run(ctx, req.model_copy(update={"access_grant_id": permit}))
    assert job.state == "partial"
    assert job.coverage_status == "partial"
    assert job.stop_reason == "rate_limited"
    assert job.error is None
    assert step_data(job)["code"] == m.ErrorCode.RATE_LIMITED.value
    assert step_data(job)["retry_after"] == 45
    assert len(step_data(job)["rows"]) == 1


@pytest.mark.parametrize("target", ["/login", "/__rd_verify_123", "/checkpoint/1/"])
def test_share_redirect_approves_host_before_classifying(
    ctx, project, resolver, monkeypatch, target
):
    allow_facebook(resolver)
    dials = route_dial(
        monkeypatch,
        {
            f"/share/r/{SHARE_TOKEN}/": {
                "status": 302,
                "location": f"https://evil.fixture.test{target}",
            }
        },
    )
    req, _permit = live_request(
        ctx,
        project,
        SHARE_R,
        ["media_metadata"],
        [("FacebookReel", ["www.facebook.com"])],
        ["FacebookReel"],
    )
    job = run(ctx, req)
    assert job.state == "awaiting_user"
    assert job.error["code"] == m.ErrorCode.SCOPE_DENIED.value
    assert len(dials) == 1


@pytest.mark.parametrize(
    "status,payload,code",
    [
        (404, b"gone", m.ErrorCode.RESOURCE_UNAVAILABLE),
        (410, b"gone", m.ErrorCode.RESOURCE_UNAVAILABLE),
        (401, b"unauthorized", m.ErrorCode.AUTH_REQUIRED),
        (200, b'<form action="/login/"><input name="email"></form>', m.ErrorCode.AUTH_REQUIRED),
        (200, b"fetch(`/__rd_verify_123`)", m.ErrorCode.CHECKPOINT_REQUIRED),
        (200, b"plain page", m.ErrorCode.UNSUPPORTED),
    ],
)
def test_share_status_and_body_classification(
    ctx, project, resolver, monkeypatch, status, payload, code
):
    allow_facebook(resolver)
    dials = route_dial(
        monkeypatch, {f"/share/r/{SHARE_TOKEN}/": {"status": status, "body": payload}}
    )
    req, _permit = live_request(
        ctx,
        project,
        SHARE_R,
        ["media_metadata"],
        [("FacebookReel", ["www.facebook.com"])],
        ["FacebookReel"],
    )
    job = run(ctx, req)
    assert job.error["code"] == code.value
    assert job.state == ("failed" if code == m.ErrorCode.UNSUPPORTED else "awaiting_user")
    assert len(dials) == 1
    assert stored_rows(ctx) == 0


@pytest.mark.parametrize(
    "key,url",
    [
        ("VKUserVideos", "https://vk.com/video/@fixture"),
        ("VKWallPost", "https://vk.com/fixture?w=wall-1_2"),
        ("TikTok", "https://www.tiktok.com/@fixture/video/1234567890123456789"),
        ("TikTokUser", "https://www.tiktok.com/@fixture"),
        ("TikTokSound", "https://www.tiktok.com/music/Fixture-1234567890123456789"),
        ("TikTokEffect", "https://www.tiktok.com/sticker/Fixture-1234567"),
        ("TikTokTag", "https://www.tiktok.com/tag/fixture"),
        (
            "TikTokCollection",
            "https://www.tiktok.com/@fixture/collection/fixture-1234567890123456789",
        ),
        ("Douyin", "https://www.douyin.com/video/1234567890123456789"),
        ("TikTokLive", "https://www.tiktok.com/@fixture/live"),
        ("FacebookAds", "https://www.facebook.com/ads/library/?id=123456789"),
    ],
)
def test_audited_solver_subclasses_cannot_be_submitted(ctx, project, key, url):
    assert remote_media._extractor_class(key).suitable(url)
    configure(ctx, [(key, [urlsplit(url).hostname])])
    req = remote_request(project, url, ["media_metadata"])
    permit = issue(ctx, project, req, [key])
    env = app.submit_collect(ctx, req.model_copy(update={"access_grant_id": permit}))
    assert env.error is not None
    assert env.error.code == m.ErrorCode.UNSUPPORTED
    assert env.error.message == "The extractor answers platform challenges"
    assert jobs_count(ctx) == 0


@pytest.mark.parametrize(
    "key",
    [
        "VKBase",
        "TikTokBase",
        "TikTokBaseList",
        "VK",
        "VKUserVideos",
        "VKWallPost",
        "TikTok",
        "TikTokUser",
        "TikTokSound",
        "TikTokEffect",
        "TikTokTag",
        "TikTokCollection",
        "Douyin",
        "TikTokLive",
        "FacebookAds",
    ],
)
def test_every_audited_solver_key_is_refused_on_hop(key):
    with pytest.raises(m.CoreError) as exc:
        remote_media._prepare_hop(_checked_net(), "https://www.fixture.test/video/1", key, [])
    assert exc.value.detail.code == m.ErrorCode.UNSUPPORTED
    assert exc.value.detail.message == "The extractor answers platform challenges"


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "extractor",
        "extractor_key",
        "webpage_url",
        "original_url",
        "url",
        "formats",
        "requested_formats",
        "subtitles",
        "requested_subtitles",
        "automatic_captions",
        "additional_urls",
        "http_headers",
    ],
)
def test_transparent_inner_authority_for_each_protected_field(field):
    inner = {field: "inner", "title": "Inner title"}
    outer = {field: "outer", "section_start": 0, "title": "Outer title"}
    merged = remote_media._merge_transparent(inner, outer)
    assert merged[field] == "inner"
    assert merged["title"] == "Outer title"


def test_share_resume_rechecks_extractor_suitability(ctx, project, monkeypatch):
    configure(ctx, [("FacebookReel", ["www.facebook.com"])])
    cls = remote_media._extractor_class("FacebookReel")
    monkeypatch.setattr(cls, "suitable", classmethod(lambda cls, url: False))
    state = remote_media._State(current={"resolved_url": REEL_URL})
    with pytest.raises(m.CoreError) as exc:
        remote_media._page_url(
            state,
            SHARE_R,
            "FacebookReel",
            _checked_net(),
            list(remote_media._workspace(ctx).remote_media.approvals),
        )
    assert exc.value.detail.code == m.ErrorCode.SCOPE_DENIED


@pytest.mark.parametrize("kind", ["playlist", "multi_video", "compat_list", "nested", "additional"])
def test_playlist_children_and_additional_urls_never_escape_the_hop_loop(
    ctx, project, resolver, kind
):
    resolver.extra["hop.fixture.test"] = ["127.0.0.1"]
    extracted = []

    class HiddenHopIE(InfoExtractor):
        _VALID_URL = r"https://hop\.fixture\.test/hidden/(?P<id>\d+)"

        def _real_extract(self, url):
            number = int(self._match_id(url))
            extracted.append(number)
            if kind == "additional":
                return {
                    "id": str(number),
                    "title": "Direct",
                    "formats": [_HOP_FORMAT],
                    "additional_urls": ["https://hop.fixture.test/hidden/2"] if number == 1 else [],
                }
            if number < 3:
                return self.url_result(f"https://hop.fixture.test/hidden/{number + 1}", "HiddenHop")
            if number == 3:
                entry = self.url_result("https://hop.fixture.test/hidden/4", "HiddenHop")
                if kind == "nested":
                    entry = {"_type": "playlist", "id": "inner", "entries": [entry]}
                return {
                    "_type": "playlist" if kind == "nested" else kind,
                    "id": "outer",
                    "entries": [entry],
                }
            return {"id": str(number), "title": "Escaped", "formats": [_HOP_FORMAT]}

    remote_media.register_test_extractor(HiddenHopIE)
    try:
        req, _ = live_request(
            ctx,
            project,
            "https://hop.fixture.test/hidden/1",
            ["media_metadata"],
            [("HiddenHop", ["hop.fixture.test"])],
            ["HiddenHop"],
        )
        job = run(ctx, req)
    finally:
        remote_media.unregister_test_extractor(HiddenHopIE)
    if kind == "additional":
        assert job.state in {"succeeded", "partial"}, job.error
        assert extracted == [1]
    else:
        assert job.state == "failed"
        assert job.error["message"] == "Extractor chain exceeds 2 hops"
        assert extracted == [1, 2, 3]


@pytest.mark.parametrize("kind", ["playlist", "multi_video", "compat_list"])
def test_multi_entry_urls_are_refused_before_child_extraction(ctx, project, resolver, kind):
    resolver.extra["hop.fixture.test"] = ["127.0.0.1"]
    extracted = []

    class MultiChildIE(InfoExtractor):
        _VALID_URL = r"https://hop\.fixture\.test/multi/(?P<id>\d+)"

        def _real_extract(self, url):
            number = int(self._match_id(url))
            extracted.append(number)
            if number == 1:
                return {
                    "_type": kind,
                    "id": "list",
                    "entries": [
                        self.url_result("https://hop.fixture.test/multi/2", "MultiChild"),
                        self.url_result("https://hop.fixture.test/multi/3", "MultiChild"),
                    ],
                }
            return {"id": str(number), "title": "Child", "formats": [_HOP_FORMAT]}

    remote_media.register_test_extractor(MultiChildIE)
    try:
        req, _ = live_request(
            ctx,
            project,
            "https://hop.fixture.test/multi/1",
            ["media_metadata"],
            [("MultiChild", ["hop.fixture.test"])],
            ["MultiChild"],
        )
        job = run(ctx, req)
    finally:
        remote_media.unregister_test_extractor(MultiChildIE)
    assert job.error["message"] == "The upstream result is a playlist"
    assert extracted == [1]
    assert stored_rows(ctx) == 0


@pytest.mark.parametrize(
    "payload",
    [
        b'<form data-action="/login/" action="/search/">',
        b'<!-- <form action="/login/"> -->',
        b"<script>const s = '<form action=\"/login/\">';</script>",
    ],
)
def test_share_200_does_not_invent_a_login_form(ctx, project, resolver, monkeypatch, payload):
    allow_facebook(resolver)
    route_dial(monkeypatch, {f"/share/r/{SHARE_TOKEN}/": {"status": 200, "body": payload}})
    req, _ = live_request(
        ctx,
        project,
        SHARE_R,
        ["media_metadata"],
        [("FacebookReel", ["www.facebook.com"])],
        ["FacebookReel"],
    )
    job = run(ctx, req)
    assert job.error["code"] == m.ErrorCode.UNSUPPORTED.value
    assert job.error["message"] == "The share link did not redirect"


@pytest.mark.parametrize("follow", [False, True])
@pytest.mark.parametrize("target", ["/login/", "/checkpoint/1/"])
def test_redirect_response_challenge_precedes_login_and_checkpoint(monkeypatch, follow, target):
    dials = []

    def dial(method, url, headers, body, checked, timeout, limit):
        dials.append(url)
        return 302, {"Location": target, "cf-mitigated": "challenge"}, b"", "127.0.0.1", "Found"

    monkeypatch.setattr(remote_media, "_dial", dial)
    with pytest.raises(m.CoreError) as exc:
        remote_media._exchange(_checked_net(), "GET", WATCH_URL, {}, None, follow=follow)
    assert exc.value.detail.code == m.ErrorCode.CHECKPOINT_REQUIRED
    assert len(dials) == 1


@pytest.mark.parametrize(
    "secret,line",
    [
        (b"# comment\nwww.facebook.com\tFALSE\t/\tTRUE\t0\tsid\t\xff\n", 2),
        ("www.facebook.com\tFALSE\t/\tTRUE\t²\tsid\tval\n".encode(), 1),
        (b"www.facebook.com\tFALSE\t/\tTRUE\t0\tsid\tval\r", 1),
    ],
)
def test_invalid_cookie_encoding_expiry_and_bare_cr_report_the_line(secret, line):
    with pytest.raises(m.CoreError) as exc:
        remote_media._parse_cookie_secret(secret)
    assert exc.value.detail.code == m.ErrorCode.INPUT_INVALID
    assert exc.value.detail.message == f"Cookie file line {line} is invalid"


def test_cookie_header_job_stops_before_page_or_cdn(ctx, project, resolver, monkeypatch, tmp_path):
    marker = marker_value("header")
    store, ref = put_secret(tmp_path, ctx, f"Cookie: sid={marker}".encode())
    dials = route_dial(
        monkeypatch, {"/clip": {"status": 200, "body": dump(info_dict(443, "clip"))}}
    )
    req, _ = live_request(
        ctx,
        project,
        "https://media.fixture.test/clip",
        ["media_metadata"],
        [("MetamuseFixture", ["media.fixture.test", "cdn.fixture.test"], ref)],
        ["MetamuseFixture"],
    )
    job = run(ctx, req, secret_store=store)
    assert job.error is not None
    assert job.error["code"] == m.ErrorCode.INPUT_INVALID.value
    assert job.error["message"] == "A Netscape cookie file is required"
    assert dials == []
    assert stored_rows(ctx) == 0
    assert marker not in str(job.error)


def test_share_resume_uses_the_matching_approval_without_unioning_hosts(ctx, project):
    configure(
        ctx,
        [
            ("FacebookReel", ["www.facebook.com"]),
            ("FacebookReel", ["m.facebook.com"]),
        ],
    )
    state = remote_media._State(current={"resolved_url": f"https://m.facebook.com/reel/{REEL_ID}"})
    with pytest.raises(m.CoreError) as exc:
        remote_media._page_url(
            state,
            SHARE_R,
            "FacebookReel",
            _checked_net(),
            list(remote_media._workspace(ctx).remote_media.approvals),
        )
    assert exc.value.detail.code == m.ErrorCode.SCOPE_DENIED
