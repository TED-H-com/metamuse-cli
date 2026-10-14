"""Offline fixture tests for bounded remote media (B12).

The synthetic server is loopback-only. URL checks use ``check_fixture_url``.
No test contacts a real site. AT-040 stays blocked.
"""

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
    lines = [text.rstrip("\n")]
    for extractor, hosts in approvals:
        lines.append("[[remote_media.approvals]]")
        lines.append(f"extractor = {json.dumps(extractor)}")
        rendered = ", ".join(json.dumps(host) for host in hosts)
        lines.append(f"hosts = [{rendered}]")
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
