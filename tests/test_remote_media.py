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
import threading
import tomllib
import traceback
from datetime import timedelta
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

    def __call__(self, host: str) -> list[str]:
        self.calls.append(host)
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
    assert params["ffmpeg_location"] is None
    assert params["external_downloader"] is None
    assert params["proxy"] == ""
    assert params["cookiefile"] is None
    assert params["cookiesfrombrowser"] is None
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
    denied, _permit = prepare(ctx, project, origin, "sub403", ["media_metadata", "subtitles"])
    denied = denied.model_copy(update={"request_key": "sub403"})
    partial = finish(ctx, denied)
    assert partial.state == "partial" and partial.state != "awaiting_user"
    assert partial.error is None
    assert "subtitles" in partial.missing_fields
    methods = [row["method"] for row in q(ctx, "SELECT method FROM transcript_segments")]
    assert methods == ["auto"]
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
