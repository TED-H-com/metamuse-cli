"""Bounded remote media through yt-dlp's Python API.

Every yt-dlp request uses one MetaMuse request handler. The handler checks the
URL with the B08 policy, dials only the addresses from that check, and follows
redirects itself. Plugins, config files, proxies, impersonation, cookies,
netrc, the yt-dlp cache, passwords, and client certificates stay off.

One process-wide audit hook refuses ``subprocess`` and ``os`` process events
while extraction or the request handler is active. Local ffprobe runs with
that flag clear. Each input is checked only against its own extractor's
approved hosts. Redirects refuse an HTTPS to HTTP downgrade and a 307 or 308
that would carry a body to another origin, and they drop ``Authorization``
when the scheme, host, or port changes. Every received body counts toward
``max_bytes``. The wall clock and a pending cancel are checked before each dial.

A playlist with more than one entry is refused. Changed subtitle bytes append
a transcript revision. A resume whose native id, extractor, platform, or scope
no longer matches the checkpoint records ``E_DATA_STALE`` and does not attach
new resources to the old item. A fabricated title is stored as SQL NULL. An
unchanged title keeps its item revision; a changed upstream timestamp is an
observation on the source ``use_basis``.

A platform challenge stops the job inside the handler, before yt-dlp can
answer it. The stop does not depend on HTTP status. A ``cf-mitigated`` value
containing ``challenge``, a reason phrase containing ``challenge``, or
``__rd_verify`` (case-insensitive, with or without a preceding slash) in a
body, ``Refresh``, or ``Location`` is
``E_CHECKPOINT_REQUIRED``. The same code is used for a normalized path segment
that starts with ``__rd_verify``, a ``challenge.html`` segment, or a
``hash429`` query, and it is raised before that URL is dialed. A plain 403
stays ``E_ACCESS_DENIED``. Extractors that compute a challenge answer cannot
be approved.

Facebook ``/share/r/`` and ``/share/v/`` links are resolved by MetaMuse, then
extracted with at most two checked delegation hops. ``/share/p/`` is refused
at submit. An optional ``auth_ref`` on one approval binds a Netscape cookie
file. A ``Cookie:`` header line is refused. ``CollectRequest`` still has no
``auth_ref``.

The optional extra is ``yt-dlp==2026.8.19``. Importing this module does not
import yt-dlp. Offline local import does not call into this operation.
"""

from __future__ import annotations

import contextvars
import hashlib
import io
import ipaddress
import json
import logging
import math
import os
import re
import shutil
import stat
import sys
import time
import unicodedata
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit, urlunsplit

from metamuse import jobs, safety
from metamuse import media as media_mod
from metamuse import models as m
from metamuse._muse_http import _ca_bundle, _options, load_curl
from metamuse.config import RemoteApproval, load_config
from metamuse.jobs import StepContext, StepSpec, submit_job
from metamuse.store import TransactionWriter, _json_loads

OPERATION = "collect.remote_media"
PERMIT_OPERATION = "collect.remote"
YTDLP_VERSION = "2026.08.19"
YTDLP_REQUIREMENT = "yt-dlp==2026.8.19"
RESOURCES = frozenset({"media_metadata", "subtitles", "media_file"})
_WORKSPACE_CAPS = ("safe_read_retries", "read_timeout_seconds", "media_max_bytes", "text_max_bytes")
REJECTED_EXTRACTORS = frozenset({"generic", "unsupported", "end"})
# These extractors compute or submit an anti-bot, rate-limit, or captcha answer.
# Player signature and nsig deciphering are not on this list. See COLLECT.md.
_CHALLENGE_SOLVERS = frozenset(
    {
        "vk",
        "vkbase",
        "vkuservideos",
        "vkwallpost",
        "tiktok",
        "tiktokbase",
        "tiktokbaselist",
        "tiktokuser",
        "tiktoksound",
        "tiktokeffect",
        "tiktoktag",
        "tiktokcollection",
        "tiktoklive",
        "douyin",
        "facebookads",
    }
)
COLLECTION_METHOD = "yt-dlp"
MAX_REDIRECTS = 5
MAX_HOPS = 2
_OWNER_NAME = ".collect-owner"
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_LIST_TYPES = frozenset({"playlist", "multi_video", "compat_list"})
_COOKIE_NAME = re.compile(r"^[0-9A-Za-z!#$%&'*+.^_`|~-]+$")
# Inner result wins identity and media. Outer may replace only these fields.
_OUTER_DESCRIPTIVE = frozenset(
    {
        "title",
        "fulltitle",
        "alt_title",
        "description",
        "timestamp",
        "upload_date",
        "release_timestamp",
        "release_date",
        "duration",
        "channel",
        "uploader",
        "creator",
        "artist",
        "album",
        "track",
        "genre",
        "categories",
        "tags",
        "thumbnail",
        "thumbnails",
        "view_count",
        "like_count",
        "comment_count",
        "availability",
        "age_limit",
        "live_status",
        "series",
        "season",
        "episode",
        "episode_number",
        "season_number",
        "section_start",
        "section_end",
    }
)
_SHARE_URL = re.compile(
    r"^https://(?P<host>[A-Za-z0-9.-]+)/share/(?P<kind>r|v|p)/(?P<token>[A-Za-z0-9]{1,32})/?$"
)
_SHARE_TARGET = {"r": "FacebookReel", "v": "Facebook"}
HANDLER_FRAME = "metamuse_request_handler_send"

log = logging.getLogger(__name__)
_purpose: contextvars.ContextVar[str] = contextvars.ContextVar(
    "remote_media_purpose", default="request"
)
_extraction_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "remote_media_extraction", default=False
)
_TEST_EXTRACTORS: list[type] = []
_observer: Callable[[dict[str, Any]], None] | None = None
LAST_DIRECTOR_KEYS: list[str] = []


class _ExternalProcessRefused(Exception):
    """An extractor tried to spawn a process while collection was active."""


def _process_event(event: str) -> bool:
    return event in {
        "subprocess.Popen",
        "os.system",
        "os.posix_spawn",
        "os.posix_spawnp",
    } or event.startswith(("os.exec", "os.fork"))


def _audit_process(event: str, args: tuple[Any, ...]) -> None:
    """Refuse child processes only while remote-media extraction is active.

    ffprobe for a local file runs with the flag clear, so this hook leaves it alone.
    """

    del args
    if not _extraction_active.get() or not _process_event(event):
        return
    raise _ExternalProcessRefused("extractor requires an external process")


def _install_process_guard() -> None:
    """One process-wide hook. A second import in this process does not add another."""

    if getattr(_install_process_guard, "installed", False):
        return
    sys.addaudithook(_audit_process)
    _install_process_guard.installed = True  # type: ignore[attr-defined]


_install_process_guard()


@contextmanager
def _extracting():
    token = _extraction_active.set(True)
    try:
        yield
    finally:
        _extraction_active.reset(token)


def _process_error() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.UNSUPPORTED,
        "extractor requires an external process",
        next_action="Stop. Do not run an extractor that needs an external process.",
    )


def _note(step: StepContext) -> None:
    """Called after each durable checkpoint. Tests replace this to simulate a crash."""

    del step


def register_test_extractor(cls: type) -> None:
    """Register a fixture extractor. Production never calls this."""

    if cls not in _TEST_EXTRACTORS:
        _TEST_EXTRACTORS.append(cls)


def unregister_test_extractor(cls: type) -> None:
    if cls in _TEST_EXTRACTORS:
        _TEST_EXTRACTORS.remove(cls)


def dependency_available() -> bool:
    """True only for the pinned yt-dlp version. Does not construct YoutubeDL."""

    try:
        from yt_dlp.version import __version__
    except ImportError:
        return False
    return __version__ == YTDLP_VERSION


def _disable_plugins() -> None:
    """Run before the first YoutubeDL. Empty plugin dirs skip plugin loading."""

    os.environ["YTDLP_NO_PLUGINS"] = "1"
    from yt_dlp.globals import plugin_dirs

    plugin_dirs.value = []


def ytdlp_params(
    staging: Path,
    *,
    max_items: int,
    socket_timeout: int,
    max_filesize: int,
    extractor: str,
) -> dict[str, Any]:
    """Fixed YoutubeDL parameters. Callers do not merge operator or environment config."""

    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _QuietLog(),
        "noplaylist": True,
        "playlistend": max_items,
        "max_downloads": max_items,
        "ignoreconfig": True,
        "proxy": "",
        "cachedir": False,
        "usenetrc": False,
        "netrc_location": None,
        "netrc_cmd": None,
        "username": None,
        "password": None,
        "videopassword": None,
        "ap_username": None,
        "ap_password": None,
        "client_certificate": None,
        "client_certificate_key": None,
        "client_certificate_password": None,
        "cookiefile": None,
        "cookiesfrombrowser": None,
        "source_address": None,
        "geo_bypass": False,
        "nocheckcertificate": False,
        "legacyserverconnect": False,
        "postprocessors": [],
        "fixup": "never",
        # None makes yt-dlp search PATH and run `ffmpeg -bsfs` inside extract_info.
        # A path we never create disables that probe. Local ffprobe is separate.
        "ffmpeg_location": str(staging / ".no-ffmpeg"),
        "external_downloader": None,
        "retries": 0,
        "fragment_retries": 0,
        "extractor_retries": 0,
        "file_access_retries": 0,
        "socket_timeout": socket_timeout,
        "max_filesize": max_filesize,
        "outtmpl": str(staging / "%(id)s.%(ext)s"),
        "restrictfilenames": True,
        "allowed_extractors": [extractor],
        "js_runtimes": {},
        "remote_components": [],
        "check_formats": False,
        "writesubtitles": False,
        "writeautomaticsub": False,
        "writethumbnail": False,
        "writeinfojson": False,
        "getcomments": False,
        "skip_download": True,
        "compat_opts": set(),
        "http_headers": {},
        "sleep_interval_requests": 0,
    }


class _QuietLog:
    """Discard yt-dlp screen output so signed URLs are not written to stderr."""

    def debug(self, message: str) -> None:
        del message

    def info(self, message: str) -> None:
        del message

    def warning(self, message: str) -> None:
        del message

    def error(self, message: str) -> None:
        del message


def _dependency_error() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.DEPENDENCY_MISSING,
        "yt-dlp is not installed",
        next_action=f"Install the optional extra remote ({YTDLP_REQUIREMENT})",
    )


def _allowlist_required() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.SCOPE_REQUIRED,
        "No remote extractor is approved",
        next_action="Add the extractor key and its exact hosts under remote_media.approvals",
    )


def _offline_error() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.SCOPE_DENIED,
        "External operations are disabled in offline mode",
        next_action="Enable live mode explicitly and configure the required permission",
    )


def _extractor_denied() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.SCOPE_DENIED,
        "Generic and unknown extractors are not approved",
        next_action="Approve the exact extractor key and host. Generic extractors stay off.",
    )


def _host_denied() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.SCOPE_DENIED,
        "The host is not on the operator allowlist for this extractor",
        next_action="Add the exact host to remote_media.approvals. Subdomains are not included.",
    )


def _limit_error() -> m.CoreError:
    return m.CoreError(m.ErrorCode.LIMIT_REACHED, "Input exceeds the byte limit")


def _wall_error() -> m.CoreError:
    return m.CoreError(m.ErrorCode.LIMIT_REACHED, "Job wall clock limit reached")


def _dial_timeout(step: StepContext, read_timeout: float) -> Callable[[], float]:
    """Before every dial: pending cancel, wall clock, then the shorter timeout."""

    def remaining() -> float:
        step.guard()
        wall_limit = min(
            step.executor.limits.wall_clock_seconds,
            int(step.job.limits.get("wall_clock_seconds", 900)),
        )
        elapsed = time.monotonic() - step.started
        started_at = step.job.started_at
        if started_at is not None:
            elapsed = max(
                elapsed,
                (
                    datetime.fromisoformat(step.store.clock()) - datetime.fromisoformat(started_at)
                ).total_seconds(),
            )
        left = wall_limit - elapsed
        if left <= 0:
            raise _wall_error()
        return min(read_timeout, left)

    return remaining


def _scope_url() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.SCOPE_DENIED,
        "Outbound URL is outside the network policy",
        next_action="Use an approved public HTTP(S) destination and a controlled backend",
    )


def register_operations(registry: dict[str, jobs.Operation] | None = None) -> None:
    target = jobs.OPERATIONS if registry is None else registry
    if OPERATION in target:
        return
    jobs.register_operation(
        OPERATION,
        run_remote_media,
        "safe_read",
        request_model=m.CollectRequest,
        registry=target,
    )


def _workspace(ctx: Any) -> Any:
    return load_config(ctx.store.paths.root)


def _host_of(url: str) -> str:
    try:
        parsed = urlsplit(url)
        invalid = (
            parsed.scheme.lower() not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port == 0
            or parsed.netloc.endswith(":")
            or "\\" in url
            or any(char.isspace() for char in url)
            or any(unicodedata.category(char) in ("Cc", "Cf") for char in unquote(url))
        )
    except ValueError:
        invalid = True
    if invalid:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Remote URL is not a bare http(s) URL")
    return safety.normalize_hostname(parsed.hostname)


def _hosts(values: Iterable[str]) -> set[str]:
    return {safety.normalize_hostname(item) for item in values}


def _secret_query_key(key: str) -> bool:
    # Ask the shared url_token rule about the key only, never the destination/value.
    # This includes its signed-URL keys without maintaining another key list.
    _display, audit = safety.redact_text("?" + key + "=synthetic")
    return any(entry["kind"] == "url_token" for entry in audit)


def _display_url(url: str) -> str:
    """The ASCII canonical URL reviewed by the human and passed to the extractor."""
    parsed = urlsplit(url)
    host = _host_of(url)
    if host == "m.facebook.com":
        host = "www.facebook.com"
    scheme = parsed.scheme.lower()
    authority = f"[{host}]" if ":" in host else host
    if parsed.port is not None and parsed.port != (443 if scheme == "https" else 80):
        authority += ":" + str(parsed.port)
    # Intersection of the pinned escape_rfc3986/requote_uri safe sets, plus
    # urllib.quote's implicit RFC 3986 unreserved set. Preserve valid reserved
    # escapes; decode unreserved ones before the fixed-point assertion below.
    ascii_safe = "%/;:@&=+$,!~*'()?#[]"

    def component(value: str) -> str:
        if re.search(r"%(?![0-9a-fA-F]{2})", value):
            raise _unreviewable_url()

        def escape(match: re.Match[str]) -> str:
            char = chr(int(match.group()[1:], 16))
            if char in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~":
                return char
            return match.group().upper()

        value = re.sub(r"%[0-9a-fA-F]{2}", escape, value)
        return quote(value, safe=ascii_safe, encoding="utf-8", errors="strict")

    path = component(parsed.path or "/")
    query = component(parsed.query)
    return urlunsplit((scheme, authority, path, query, ""))


def _unreviewable_url() -> m.CoreError:
    return m.CoreError(m.ErrorCode.INPUT_INVALID, "Remote URL cannot be reviewed safely")


def _require_reviewable_url(url: str) -> str:
    _host_of(url)
    parsed = urlsplit(url)
    if "#" in url or any(
        _secret_query_key(part.partition("=")[0]) for part in parsed.query.split("&")
    ):
        raise _unreviewable_url()
    # Python's IDNA2003 codec differs from IDNA2008 on these deviation
    # characters. Also test the mapped host before normalize_hostname strips dots.
    if any(char in parsed.hostname for char in ("ß", "ς")) or parsed.hostname.encode("idna").decode(
        "ascii"
    ).endswith("."):
        raise _unreviewable_url()
    display = _display_url(url)
    if len(display) > safety.URL_MAX_BYTES:
        safety._url_error()
    canonical = urlsplit(display)
    if any(segment.partition(";")[0] in (".", "..") for segment in canonical.path.split("/")):
        raise _unreviewable_url()
    keys = (
        [part.partition("=")[0] for part in canonical.query.split("&")] if canonical.query else []
    )
    if len(set(keys)) != len(keys):
        raise _unreviewable_url()
    if safety.redact_text(display)[0] != display:
        raise _unreviewable_url()
    from curl_cffi.requests.utils import requote_uri
    from yt_dlp.utils.networking import normalize_url

    if display != requote_uri(normalize_url(display)):
        raise _unreviewable_url()
    return display


def _share_parts(url: str) -> tuple[str, str, str] | None:
    """Exact ``https`` share URL: host, kind ``r``/``v``/``p``, and token."""

    match = _SHARE_URL.fullmatch(url)
    if match is None:
        return None
    # A second trailing dot is outside the host policy and is refused at submit.
    return safety.normalize_hostname(match.group("host")), match.group("kind"), match.group("token")


def _share_key(url: str) -> str | None:
    parts = _share_parts(url)
    if parts is None:
        return None
    return _SHARE_TARGET.get(parts[1])


def _approval_for(key: str, url: str, approvals: list[Any]) -> Any | None:
    host = _host_of(url)
    for approval in approvals:
        if approval.extractor == key and host in _hosts(approval.hosts):
            return approval
    return None


def _path_segments(path: str) -> list[str]:
    """Decode, remove each segment's parameters, collapse dots, then lowercase."""

    decoded = unquote(path or "/")
    segments: list[str] = []
    for segment in decoded.split("/"):
        segment = segment.split(";", 1)[0]
        if segment == "" or segment == ".":
            continue
        if segment == "..":
            if segments:
                segments.pop()
            continue
        segments.append(segment.lower())
    return segments


def _challenge_target(url: str) -> bool:
    parts = urlsplit(url)
    for segment in _path_segments(parts.path or "/"):
        if segment.startswith("__rd_verify") or segment == "challenge.html":
            return True
    return "hash429" in unquote(parts.query or "").lower()


def _checkpoint_target(url: str) -> bool:
    return any(segment == "checkpoint" for segment in _path_segments(urlsplit(url).path or "/"))


def _login_target(url: str) -> bool:
    """A segment equal to ``login`` or ``login.php``, or starting with ``login.``."""

    for segment in _path_segments(urlsplit(url).path or "/"):
        if segment == "login" or segment == "login.php" or segment.startswith("login."):
            return True
    return False


def _stop_for_target(url: str) -> None:
    """Challenge, then checkpoint, then login. The caller already approved the host."""

    if _challenge_target(url):
        raise _challenge_error()
    if _checkpoint_target(url):
        raise _challenge_error()
    if _login_target(url):
        raise _login_error()


def _canonical_media(url: str) -> str | None:
    """Reel, watch, or page video URL with tracking parameters removed."""

    parts = urlsplit(url)
    if parts.scheme.lower() != "https" or parts.username or parts.password or parts.port:
        return None
    try:
        host = safety.normalize_hostname(parts.hostname or "")
    except m.CoreError:
        return None
    path = parts.path or ""
    reel = re.fullmatch(r"/reel/(\d+)/?", path)
    if reel:
        return f"https://{host}/reel/{reel.group(1)}"
    if re.fullmatch(r"/watch/?", path):
        values = parse_qs(parts.query, keep_blank_values=False).get("v") or []
        if len(values) == 1 and values[0].isdigit():
            return f"https://{host}/watch/?v={values[0]}"
        return None
    videos = re.fullmatch(r"/([^/]+)/videos/(\d+)/?", path)
    if videos and re.fullmatch(r"[A-Za-z0-9._-]+", videos.group(1)):
        return f"https://{host}/{videos.group(1)}/videos/{videos.group(2)}"
    return None


@dataclass(frozen=True)
class _Cookie:
    """One parsed cookie. ``repr`` omits the value."""

    name: str
    value: str = field(repr=False)
    domain: str = ""
    host_only: bool = False
    path: str = "/"
    secure: bool = False
    expires: int | None = None

    def __str__(self) -> str:
        return f"_Cookie(name={self.name!r}, domain={self.domain!r})"


def _cookie_value_ok(value: str) -> bool:
    """RFC 6265 cookie-octets, optionally wrapped in one DQUOTE pair."""

    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        body = value[1:-1]
    else:
        body = value
    for char in body:
        code = ord(char)
        if code < 0x21 or code > 0x7E or char in {'"', ",", ";", "\\"}:
            return False
    return True


def _parse_cookie_secret(secret: bytes) -> tuple[_Cookie, ...]:
    """Netscape cookie file only. A ``Cookie:`` line has no domain scope."""

    try:
        text = secret.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _cookie_line_invalid(secret[: exc.start].count(b"\n") + 1) from None
    parsed: list[_Cookie] = []
    lines = text.split("\n")
    for index, raw in enumerate(lines, start=1):
        # Only CRLF/LF are terminators. A bare CR remains invalid cookie data.
        line = raw[:-1] if index < len(lines) and raw.endswith("\r") else raw
        comment = line.lstrip()
        if not comment or (comment.startswith("#") and not comment.startswith("#HttpOnly_")):
            continue
        if comment.lower().startswith("cookie:"):
            raise _cookie_invalid()
        parsed.append(_parse_netscape_line(line, index))
    if not parsed:
        raise _cookie_invalid()
    return tuple(parsed)


def _parse_netscape_line(line: str, line_no: int) -> _Cookie:
    fields = line.split("\t")
    if len(fields) != 7:
        raise _cookie_line_invalid(line_no)
    domain, flag, path, secure, expiry, name, value = fields
    if domain.startswith("#HttpOnly_"):
        domain = domain[len("#HttpOnly_") :]
    if domain.startswith("."):
        domain = domain[1:]
    try:
        domain = safety.normalize_hostname(domain)
    except m.CoreError:
        raise _cookie_line_invalid(line_no) from None
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        raise _cookie_line_invalid(line_no)
    if len(domain.split(".")) < 2:
        raise _cookie_line_invalid(line_no)
    if _COOKIE_NAME.fullmatch(name) is None or not _cookie_value_ok(value):
        raise _cookie_line_invalid(line_no)
    if flag.upper() not in ("TRUE", "FALSE"):
        raise _cookie_line_invalid(line_no)
    if secure.upper() not in ("TRUE", "FALSE") or not path.startswith("/"):
        raise _cookie_line_invalid(line_no)
    if expiry != "0" and (not expiry.isdigit() or expiry.startswith("0")):
        raise _cookie_line_invalid(line_no)
    try:
        expires = None if expiry == "0" else int(expiry)
    except ValueError:
        raise _cookie_line_invalid(line_no) from None
    return _Cookie(
        name=name,
        value=value,
        domain=domain,
        host_only=flag.upper() == "FALSE",
        path=path,
        secure=secure.upper() == "TRUE",
        expires=expires,
    )


def _domain_match(host: str, cookie: _Cookie) -> bool:
    domain = cookie.domain
    if not domain:
        return False
    if cookie.host_only:
        return host == domain
    return host == domain or host.endswith("." + domain)


def _path_match(request_path: str, cookie_path: str) -> bool:
    path = request_path or "/"
    prefix = cookie_path or "/"
    if path == prefix:
        return True
    if not path.startswith(prefix):
        return False
    if prefix.endswith("/"):
        return True
    return len(path) > len(prefix) and path[len(prefix)] == "/"


def _cookie_header(cookies: tuple[Any, ...], url: str, allowed_hosts: frozenset[str]) -> str | None:
    parts = urlsplit(url)
    if not parts.hostname:
        return None
    try:
        host = safety.normalize_hostname(parts.hostname)
    except m.CoreError:
        return None
    if host not in allowed_hosts:
        return None
    scheme = (parts.scheme or "").lower()
    path = parts.path or "/"
    now = int(time.time())
    pairs: list[str] = []
    for cookie in cookies:
        if cookie.expires is not None and cookie.expires <= now:
            continue
        if cookie.secure and scheme != "https":
            continue
        if not _domain_match(host, cookie):
            continue
        if not _path_match(path, cookie.path):
            continue
        pairs.append(f"{cookie.name}={cookie.value}")
    if not pairs:
        return None
    return "; ".join(pairs)


def _secret_error() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.AUTH_REQUIRED,
        "Secret file or reference is unavailable or unsafe",
        next_action="Import an owned regular file with mode 0600 using metamuse auth import",
    )


def _check_cookie_metadata(ref: str, provider: str, account: str, secret_store: Any) -> None:
    """Read only the native private-file metadata prefix, never secret_b64 bytes.

    B08 has no public metadata reader yet. Its canonical file format writes metadata
    first. Use the existing no-symlink directory walk without creation/chmod, and
    unbuffered reads ending at the metadata object's closing brace.
    """
    if re.fullmatch(r"auth_[0-9a-f]{32}", ref) is None:
        raise _secret_error()
    store = secret_store or safety.SecretStore()
    try:
        directory = safety._open_directory(store.root)
        try:
            fd = os.open(
                ref + ".json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
            )
        finally:
            os.close(directory)
        with os.fdopen(fd, "rb", buffering=0) as stream:
            before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_mode & 0o077
                or not 0 < before.st_size <= safety.SECRET_MAX_BYTES * 2
                or stream.read(12) != b'{"metadata":'
            ):
                raise _secret_error()
            encoded = bytearray()
            depth = 0
            quoted = False
            escaped = False
            while len(encoded) < 8192:
                byte = stream.read(1)
                if not byte or not encoded and byte != b"{":
                    raise _secret_error()
                encoded.extend(byte)
                if quoted:
                    if escaped:
                        escaped = False
                    elif byte == b"\\":
                        escaped = True
                    elif byte == b'"':
                        quoted = False
                elif byte == b'"':
                    quoted = True
                elif byte == b"{":
                    depth += 1
                elif byte == b"}":
                    depth -= 1
                    if depth == 0:
                        break
            else:
                raise _secret_error()
            metadata = json.loads(encoded)
            after = os.fstat(stream.fileno())
            if (
                (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or metadata["auth_ref"] != ref
                or metadata["storage_kind"] != "private_file"
                or metadata["provider"] != provider
                or metadata["account"] != account
            ):
                raise _secret_error()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise _secret_error() from None


def _load_bound_cookies(
    approvals: list[Any], provider: str, account: str, secret_store: Any
) -> dict[str, tuple[Any, ...]]:
    return {
        ref: _parse_cookie_secret(
            safety.resolve_secret(
                ref, provider=provider, account=account, secret_store=secret_store
            )
        )
        for ref in sorted({approval.auth_ref for approval in approvals if approval.auth_ref})
    }


def _bind_cookies(net: _Net, approval: Any, provider: str, account: str, secret_store: Any) -> None:
    """Load this approval's secret before its first dial. Set-Cookie is never stored."""

    net.cookies = ()
    net.cookie_hosts = frozenset(_hosts(approval.hosts))
    ref = getattr(approval, "auth_ref", None)
    if not ref:
        return
    if net.bound_cookies is not None:
        if ref not in net.bound_cookies:
            raise _secret_error()
        net.cookies = net.bound_cookies[ref]
        return
    secret = safety.resolve_secret(
        ref, provider=provider, account=account, secret_store=secret_store
    )
    net.cookies = _parse_cookie_secret(secret)


def _extractor_class(key: str) -> type | None:
    if key.lower() in REJECTED_EXTRACTORS:
        return None
    for cls in _TEST_EXTRACTORS:
        if cls.ie_key() == key:
            return cls
    if not dependency_available():
        return None
    _disable_plugins()
    from yt_dlp.extractor import get_info_extractor

    try:
        return get_info_extractor(key)
    except Exception:
        return None


def _solver_key(key: str) -> bool:
    return key.lower() in _CHALLENGE_SOLVERS


def _match(url: str, approvals: list[Any]) -> tuple[type, Any]:
    host = _host_of(url)
    suitable: list[tuple[type, Any]] = []
    for approval in approvals:
        if _solver_key(approval.extractor):
            cls = _extractor_class(approval.extractor)
            if cls is not None and cls.suitable(url):
                raise _solver_error()
            continue
        cls = _extractor_class(approval.extractor)
        if cls is not None and cls.suitable(url):
            suitable.append((cls, approval))
    if not suitable:
        raise _extractor_denied()
    for cls, approval in suitable:
        if host in _hosts(approval.hosts):
            return cls, approval
    raise _host_denied()


def _select(url: str, approvals: list[Any]) -> tuple[type, Any]:
    """Pick the extractor for one input. Share links never use the generic extractor."""

    parts = _share_parts(url)
    if parts is None:
        return _match(url, approvals)
    _host, kind, _token = parts
    if kind == "p":
        raise _post_share_error()
    key = _SHARE_TARGET[kind]
    cls = _extractor_class(key)
    if cls is None:
        raise _extractor_denied()
    approval = _approval_for(key, url, approvals)
    if approval is not None:
        return cls, approval
    if any(item.extractor == key for item in approvals):
        raise _host_denied()
    raise _extractor_denied()


def _canonical_approvals(approvals: Iterable[Any]) -> list[dict[str, Any]]:
    entries = [
        {
            "extractor": approval.extractor,
            "hosts": sorted(_hosts(approval.hosts)),
            "auth_ref": approval.auth_ref,
        }
        for approval in approvals
    ]
    return sorted(
        entries, key=lambda entry: (entry["extractor"], entry["hosts"], entry["auth_ref"] or "")
    )


def _bound_approvals(target: dict[str, Any]) -> list[RemoteApproval]:
    return [RemoteApproval.model_validate(entry) for entry in target.get("approvals", [])]


def action_parts(
    request: m.CollectRequest,
    provider: str,
    account: str,
    extractors: list[str],
    *,
    approvals: Iterable[Any] = (),
    urls: list[str] | None = None,
    workspace_caps: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind the canonical first hops, approval table and displayed limits."""

    if urls is None:
        urls = [_require_reviewable_url(item.url) for item in request.inputs if item.kind == "url"]
    resources = sorted(request.resources)
    target = {
        "project_id": request.project_id,
        "provider": provider,
        "account": account,
        "operation": PERMIT_OPERATION,
        "urls": urls,
        "resources": resources,
        "extractors": list(extractors),
        "approvals": _canonical_approvals(approvals),
    }
    payload = {
        "urls": urls,
        "resources": resources,
        "extractors": list(extractors),
        "limits": {**request.limits.model_dump(mode="json"), **(workspace_caps or {})},
    }
    return target, payload


def _check_limits(request: m.CollectRequest, workspace: Any) -> None:
    for name in m.CollectLimits.model_fields:
        if getattr(request.limits, name) > getattr(workspace.limits, name):
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Request limit exceeds the workspace limit",
                next_action="Lower the request limit or raise it in the workspace configuration",
            )
    if len(request.inputs) > request.limits.max_urls:
        raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Input count exceeds max_urls")


@dataclass
class _PreparedRemote:
    workspace: Any
    matched: list[tuple[type, Any]]
    target: dict[str, Any]
    payload: dict[str, Any]
    limits: dict[str, Any]


def prepare_remote_media(
    ctx: Any,
    request: m.CollectRequest,
    *,
    workspace: Any = None,
    conn: Any = None,
    workspace_caps: dict[str, Any] | None = None,
) -> _PreparedRemote:
    """Read-only preparation shared by operator review, submission and execution."""

    for item in request.inputs:
        if item.kind == "url":
            try:
                over_limit = len(item.url.encode("utf-8")) > safety.URL_MAX_BYTES
            except UnicodeError:
                over_limit = True
            if over_limit:
                safety._url_error()
            _host_of(item.url)
            parsed = urlsplit(item.url)
            if parsed.netloc.startswith("[") and not re.fullmatch(
                r"\[[^\]]+\](?::[0-9]+)?", parsed.netloc
            ):
                safety._url_error()
    if not dependency_available():
        raise _dependency_error()
    ctx.store.get(m.Project, request.project_id, conn=conn)
    workspace = _workspace(ctx) if workspace is None else workspace
    for name in ("provider", "account"):
        label = getattr(workspace.remote_media, name)
        if safety.redact_text(label)[0] != label:
            raise m.CoreError(
                m.ErrorCode.INPUT_INVALID, "Invalid remote_media." + name + " setting"
            )
    _check_limits(request, workspace)
    if workspace.app.network_mode == "offline":
        raise _offline_error()
    configured = workspace.remote_media.approvals
    priority = {
        key: index for index, key in enumerate(dict.fromkeys(a.extractor for a in configured))
    }
    # Extractor priority remains operator-owned and the selected keys are bound.
    # Entries within one key have canonical order, including cookie bindings.
    approvals = [
        RemoteApproval.model_validate(entry)
        for entry in sorted(
            _canonical_approvals(configured), key=lambda e: priority[e["extractor"]]
        )
    ]
    urls = [_require_reviewable_url(item.url) for item in request.inputs if item.kind == "url"]
    caps = {name: getattr(workspace.limits, name) for name in _WORKSPACE_CAPS}
    if workspace_caps is not None:
        caps = dict(workspace_caps)
    try:
        if not approvals:
            raise _allowlist_required()
        matched = [_select(url, approvals) for url in urls]
    except m.CoreError:
        if isinstance(ctx, StepContext) and ctx.target.get("approvals") != _canonical_approvals(
            approvals
        ):
            # Removing access must invalidate the old grant with the same recovery
            # instruction as adding access, even when no current extractor matches.
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Permit does not cover this action",
                next_action="Repeat the call without a grant to get a fresh approval preview",
            ) from None
        raise
    keys = [cls.ie_key() for cls, _approval in matched]
    target, payload = action_parts(
        request,
        workspace.remote_media.provider,
        workspace.remote_media.account,
        keys,
        approvals=approvals,
        urls=urls,
        workspace_caps=caps,
    )
    if conn is None and (not isinstance(ctx, StepContext) or ctx.target == target):
        # A changed table reaches the step's fresh-preview refusal before inspecting
        # new, unbound credentials. The DB-only verifier rebuild reuses checks that
        # already ran on this workspace snapshot, outside its transaction.
        secret_store = (
            ctx.executor.secret_store if isinstance(ctx, StepContext) else ctx.secret_store
        )
        for ref in sorted({approval.auth_ref for approval in approvals if approval.auth_ref}):
            _check_cookie_metadata(
                ref, workspace.remote_media.provider, workspace.remote_media.account, secret_store
            )
    limits = request.limits.model_dump(mode="json")
    limits.update(caps)
    return _PreparedRemote(workspace, matched, target, payload, limits)


def _safe_display(value: Any) -> Any:
    """Redact every raw text field before JSON encoding can hide credentials."""
    if isinstance(value, str):
        return safety.redact_text(value)[0]
    if isinstance(value, list):
        return [_safe_display(item) for item in value]
    if isinstance(value, dict):
        return {key: _safe_display(item) for key, item in value.items()}
    return value


def preview_remote_media(ctx: Any, request: m.CollectRequest) -> m.ApprovalPreview:
    prepared = prepare_remote_media(ctx, request)
    digest = safety.action_digest(prepared.target, prepared.payload)
    review_request = request.model_dump(mode="json")
    for item, url in zip(review_request["inputs"], prepared.target["urls"], strict=True):
        item["url"] = url
    safe_request = _safe_display(review_request)
    safe_request["access_grant_id"] = None
    provider = _safe_display(prepared.target["provider"])
    account = _safe_display(prepared.target["account"])
    destination = "\n".join(item["url"] for item in safe_request["inputs"])
    details = [
        "Would: collect remote " + ", ".join(safe_request["resources"]),
        "Destination: " + destination,
        "Provider/account: " + provider + "/" + account,
        "Project: " + safe_request["project_id"],
        "Action digest: " + digest,
    ]
    first_entries = {
        m.canonical_json(approval.model_dump(mode="json")) for _cls, approval in prepared.matched
    }
    for approval in _bound_approvals(prepared.target):
        first = m.canonical_json(approval.model_dump(mode="json")) in first_entries
        role = "first hop; may also serve later hops" if first else "may serve later hops"
        details.append(
            "Extractor/allowed hosts: "
            + _safe_display(approval.extractor)
            + "/"
            + ", ".join(_safe_display(approval.hosts))
            + " ("
            + role
            + ")"
        )
        if approval.auth_ref:
            details.append(
                "Cookie binding: " + _safe_display(approval.auth_ref) + "; account: " + account
            )
    details.append(
        "The digest binds every configured remote-media approval, including later-hop entries. "
        "Editing even an unrelated approval requires a fresh preview and grant."
    )
    details.append(
        "Limits: " + "; ".join(f"{key}={value}" for key, value in prepared.limits.items())
    )
    details.append(
        "Workspace caps are part of this approval. Lower workspace caps at run time apply; "
        "a raised cap needs a new approval and cannot exceed this reviewed snapshot."
    )
    details.append(
        "Stores: source provenance and media metadata; requested subtitle tracks and cues; "
        "media bytes only when media_file is requested. No cookie values are stored."
    )
    if any(_share_parts(url) for url in prepared.target["urls"]):
        details.append(
            "Share links resolve only during execution through checked redirects and at most "
            "two approved extractor hops. This digest binds the canonical share URL."
        )
    request_file = str(ctx.paths.root / ("approval-" + digest + ".json"))
    approval_key = "approve-" + digest
    with ctx.store.connection() as conn:
        used_keys = {
            row["request_key"]
            for row in conn.execute(
                "SELECT request_key FROM request_receipts "
                "WHERE operation='permit.create' AND request_key LIKE ?",
                (approval_key + "%",),
            )
        }
    candidate = approval_key
    ordinal = 0
    while candidate in used_keys:
        ordinal += 1
        candidate = approval_key + "-" + str(ordinal)
    return m.ApprovalPreview(
        operation=PERMIT_OPERATION,
        permit_operation=PERMIT_OPERATION,
        kind="access",
        project_id=request.project_id,
        provider=provider,
        account=account,
        action_digest=digest,
        destination=destination,
        summary="\n".join(details),
        request_file=request_file,
        request=safe_request,
        next_action=safety.operator_command(
            ctx,
            "permit",
            "approve",
            "--operation",
            PERMIT_OPERATION,
            "--request-file",
            request_file,
            "--exact-digest",
            digest,
            "--request-key",
            candidate,
        ),
    )


def _replay_remote(ctx: Any, request: m.CollectRequest) -> m.Envelope | None:
    payload = request.model_dump(mode="json")
    with ctx.store.connection() as conn:
        receipt = conn.execute(
            "SELECT * FROM request_receipts WHERE operation=? AND request_key=?",
            (OPERATION, request.request_key),
        ).fetchone()
        if receipt:
            if receipt["payload_digest"] != m.payload_digest(payload):
                raise m.CoreError(
                    m.ErrorCode.IDEMPOTENCY_CONFLICT, "Request key has a different payload"
                )
            job = ctx.store.get(m.Job, _json_loads(receipt["response"])["job_id"], conn=conn)
        else:
            # Pre-receipt B12 jobs retain their original core identity on replay.
            row = conn.execute(
                "SELECT * FROM jobs WHERE operation=? AND request_key=?",
                (OPERATION, request.request_key),
            ).fetchone()
            if row is None:
                return None
            job = ctx.store.decode(m.Job, row)
        if job.payload != payload:
            raise m.CoreError(
                m.ErrorCode.IDEMPOTENCY_CONFLICT, "Request key has a different payload"
            )
        targets = [
            ctx.store.decode(m.JobStep, row).target
            for row in conn.execute(
                "SELECT * FROM job_steps WHERE job_id=? ORDER BY sequence", (job.id,)
            )
        ]
    return submit_job(
        ctx.store,
        OPERATION,
        job.request_key,
        job.payload,
        project_id=job.project_id,
        steps=[StepSpec(target=target) for target in targets],
        requested_scope=job.requested_scope,
        limits=job.limits,
    )


def submit_remote_media(ctx: Any, request: m.CollectRequest) -> m.Envelope:
    """Refuse before a job exists unless preparation and the configured grant pass."""
    register_operations()
    replay = _replay_remote(ctx, request)
    if replay is not None:
        return replay
    prepared = prepare_remote_media(ctx, request)
    target, payload = prepared.target, prepared.payload
    use = safety.PermitUse(
        kind="access",
        project_id=request.project_id,
        provider=target["provider"],
        account=target["account"],
        operation=PERMIT_OPERATION,
        digest=safety.action_digest(target, payload),
    )
    with ctx.store.transaction() as conn:
        safety.verify_permit(ctx.store, request.access_grant_id, use, conn=conn, reserve=False)

    def accept(writer: TransactionWriter, job: m.Job, steps: list[m.JobStep]) -> None:
        snapshot = {name: prepared.limits[name] for name in _WORKSPACE_CAPS}
        writer.execute(
            "UPDATE job_steps SET checkpoint=? WHERE id=?",
            (m.canonical_json({"remote_media_caps": snapshot}).decode(), steps[0].id),
        )
        writer.execute(
            "INSERT INTO request_receipts VALUES(?,?,?,?)",
            (
                OPERATION,
                request.request_key,
                m.payload_digest(request.model_dump(mode="json")),
                m.canonical_json({"job_id": job.id}).decode(),
            ),
        )

    return submit_job(
        ctx.store,
        OPERATION,
        request.request_key,
        request.model_dump(mode="json"),
        project_id=request.project_id,
        steps=[StepSpec(target=target)],
        requested_scope={
            "resources": sorted(request.resources),
            "input_count": len(request.inputs),
        },
        limits=request.limits.model_dump(mode="json"),
        prepare_domain=accept,
    )


def _checker_factory(hosts: tuple[str, ...], resolver: Callable[[str], Iterable[str]]) -> Callable:
    policy = safety.OutboundURLPolicy(resolver=resolver, allowed_hosts=hosts)

    def check(url: str, *, purpose: str) -> safety.CheckedURL:
        return policy.before_request(url, purpose=purpose)  # type: ignore[arg-type]

    return check


def _system_resolver(host: str) -> list[str]:
    """One DNS lookup for the production checker. Tests replace the checker factory."""

    import socket

    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    found: list[str] = []
    for info in infos:
        address = str(ipaddress.ip_address(info[4][0]))
        if address not in found:
            found.append(address)
    return found


@dataclass
class _Budget:
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    def take(self, count: int) -> None:
        if count < 0 or self.used + count > self.limit:
            raise _limit_error()
        self.used += count


@dataclass
class _Net:
    checker: Callable[..., safety.CheckedURL]
    budget: _Budget
    media_max: int
    timeout: float
    state: _State | None = None
    before_dial: Callable[[], float] | None = None
    cookies: tuple[Any, ...] = ()
    cookie_hosts: frozenset[str] = frozenset()
    secret_store: Any = None
    provider: str = ""
    account: str = ""
    bound_cookies: dict[str, tuple[Any, ...]] | None = None


def _peer(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    text = str(value or "").strip()
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        raise _scope_url() from None


def _header_map(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    copied: dict[str, str] = {}
    items = headers.items() if hasattr(headers, "items") else []
    for key, value in items:
        name = key.decode("ascii", "replace") if isinstance(key, bytes) else str(key)
        text = value.decode("latin-1", "replace") if isinstance(value, bytes) else str(value)
        copied[name] = text
    return copied


def _location(headers: dict[str, str]) -> str | None:
    for key, value in headers.items():
        if key.lower() == "location":
            return value
    return None


def _retry_after(headers: dict[str, str]) -> int | None:
    for key, value in headers.items():
        if key.lower() == "retry-after":
            try:
                number = int(value)
            except (TypeError, ValueError):
                return None
            if number < 0:
                return None
            return number
    return None


class _Cap:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.data = bytearray()
        self.overflow = False

    def __call__(self, chunk: Any) -> int:
        piece = bytes(chunk)
        if len(self.data) + len(piece) > self.limit:
            self.overflow = True
            raise RuntimeError("response cap")
        self.data.extend(piece)
        return len(piece)


def _dial(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    checked: safety.CheckedURL,
    timeout: float,
    limit: int,
) -> tuple[int, dict[str, str], bytes, str, str]:
    session_type, _websocket, curl_opt = load_curl()
    options = _options(curl_opt, checked)
    bundle = _ca_bundle()
    try:
        options[curl_opt.PATH_AS_IS] = 1
        # libcurl still upper-cases escapes in its URL parser with PATH_AS_IS.
        # Pin the HTTP request-target too, including extractor-derived hop bytes.
        parsed = urlsplit(checked.url)
        request_target = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
        options[curl_opt.REQUEST_TARGET] = request_target.encode("utf-8")
        session = session_type(
            verify=bundle,
            trust_env=False,
            allow_redirects=False,
            curl_options=options,
        )
    except (TypeError, AttributeError) as exc:
        raise m.CoreError(
            m.ErrorCode.DEPENDENCY_MISSING,
            "curl_cffi API is not the pinned shape",
            next_action="Install curl_cffi==0.16.3. Do not retry this process.",
        ) from exc
    writer = _Cap(limit)
    failure: m.CoreError | None = None
    response = None
    try:
        kwargs: dict[str, Any] = {
            "quote": False,
            "headers": headers,
            "verify": bundle,
            "allow_redirects": False,
            "timeout": timeout,
            "content_callback": writer,
        }
        if body is not None:
            kwargs["data"] = body
        try:
            response = session.request(method, url, **kwargs)
        except (TypeError, AttributeError) as exc:
            failure = m.CoreError(
                m.ErrorCode.DEPENDENCY_MISSING,
                "curl_cffi API is not the pinned shape",
                next_action="Install curl_cffi==0.16.3. Do not retry this process.",
            )
            failure.__cause__ = exc
        except Exception as exc:
            if writer.overflow:
                failure = _limit_error()
            else:
                failure = m.CoreError(
                    m.ErrorCode.NETWORK_TIMEOUT,
                    "Remote read failed",
                    next_action="Inspect the job status before retrying the read.",
                )
            failure.__cause__ = exc
    finally:
        closer = getattr(session, "close", None)
        if closer is not None:
            try:
                closer()
            except Exception:
                log.debug("remote media session close failed")
    if failure is not None:
        raise failure
    if writer.overflow or len(writer.data) > limit:
        raise _limit_error()
    peer = _peer(getattr(response, "primary_ip", ""))
    if peer not in checked.addresses:
        raise _scope_url()
    status = int(response.status_code)
    reason = getattr(response, "reason", None)
    if reason is None:
        reason = getattr(response, "reason_phrase", "")
    return (
        status,
        _header_map(getattr(response, "headers", {})),
        bytes(writer.data),
        peer,
        str(reason or ""),
    )


def _observe(host: str, path: str, peer: str, purpose: str, status: int, size: int) -> None:
    if _observer is None:
        return
    _observer(
        {
            "host": host,
            "path": path,
            "peer": peer,
            "purpose": purpose,
            "status": status,
            "bytes": size,
        }
    )


def _drop_header(headers: dict[str, str], name: str) -> None:
    for key in list(headers):
        if key.lower() == name.lower():
            headers.pop(key)


def _endpoint(url: str) -> tuple[str, str, int]:
    """Scheme, host, and explicit or default port. These three are the origin."""

    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    host = safety.normalize_hostname(parts.hostname or "")
    if parts.port is not None:
        port = parts.port
    elif scheme == "https":
        port = 443
    elif scheme == "http":
        port = 80
    else:
        port = -1
    return scheme, host, port


def _downgrade_error() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.SCOPE_DENIED,
        "HTTPS redirect to HTTP is refused",
        next_action="Stop. Do not follow a redirect that leaves TLS.",
    )


def _body_forward_error() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.SCOPE_DENIED,
        "A redirect would forward the request body to another origin",
        next_action="Stop. Do not send this request body to a different origin.",
    )


def _challenge_error() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.CHECKPOINT_REQUIRED,
        "Platform challenge",
        next_action="Stop. Do not solve a challenge or retry it.",
    )


def _login_error() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.AUTH_REQUIRED,
        "Login required",
        next_action="Stop. Do not retry a login wall.",
    )


def _share_unsupported() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.UNSUPPORTED,
        "Share link did not resolve to an approved media URL",
        next_action="Stop. Submit the individual media URL.",
    )


def _post_share_error() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.UNSUPPORTED,
        "A /share/p/ link is a post",
        next_action="Use the Facebook post collector for /share/p/ links.",
    )


def _hop_denied(key: str, host: str) -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.SCOPE_DENIED,
        "Extractor hop is not approved",
        next_action=f"Approve {key} for {host}",
    )


def _hop_limit() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.UNSUPPORTED,
        "Extractor chain exceeds 2 hops",
        next_action="Stop. Submit the individual media URL.",
    )


def _cookie_invalid() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.INPUT_INVALID,
        "A Netscape cookie file is required",
        next_action="Import a Netscape cookie file. A Cookie header line is not accepted.",
    )


def _cookie_line_invalid(line_no: int) -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.INPUT_INVALID,
        f"Cookie file line {line_no} is invalid",
        next_action="Import a Netscape cookie file",
    )


def _solver_error() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.UNSUPPORTED,
        "The extractor answers platform challenges",
        next_action="Stop. MetaMuse does not run an extractor that answers a challenge.",
    )


def _share_no_redirect() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.UNSUPPORTED,
        "The share link did not redirect",
        next_action="Stop. Submit the individual media URL.",
    )


def _unavailable() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.RESOURCE_UNAVAILABLE,
        "Media is unavailable",
        next_action="Stop. The upstream item is not available.",
    )


def _header_values(headers: dict[str, str], name: str) -> list[str]:
    return [value for key, value in headers.items() if key.lower() == name]


def _challenge_marked(status: int, headers: dict[str, str], payload: bytes, reason: str) -> bool:
    """True for a challenge marker at any HTTP status. MetaMuse does not answer it."""

    del status
    for value in _header_values(headers, "cf-mitigated"):
        if "challenge" in value.lower():
            return True
    if "challenge" in (reason or "").lower():
        return True

    def marked(value: str) -> bool:
        # Slash entities need no expansion: the token matches with any prefix.
        decoded = unquote(value)
        decoded = re.sub(r"\\(?:u005f|x5f)", "_", decoded, flags=re.IGNORECASE)
        return "__rd_verify" in unicodedata.normalize("NFKC", decoded).lower()

    # Bodies include media bytes. Bound temporary Unicode allocations while
    # retaining enough overlap for a complete encoded marker across a block edge.
    block = 32 * 1024
    overlap = 256
    for start in range(0, len(payload), block):
        part = payload[max(0, start - overlap) : start + block + overlap]
        if marked(part.decode("utf-8", "replace")):
            return True
    for name in ("refresh", "location"):
        for value in _header_values(headers, name):
            if marked(value):
                return True
    return False


def _unpack_dial(
    result: tuple[Any, ...],
) -> tuple[int, dict[str, str], bytes, str, str]:
    if len(result) >= 5:
        status, headers, payload, peer, reason = result[:5]
        return int(status), dict(headers), bytes(payload), str(peer), str(reason or "")
    status, headers, payload, peer = result
    return int(status), dict(headers), bytes(payload), str(peer), ""


def metamuse_request_handler_send(handler: Any, request: Any) -> Any:
    """Dial one checked request and follow redirects inside this handler."""

    from yt_dlp.networking.common import Response
    from yt_dlp.networking.exceptions import HTTPError

    with _extracting():
        return _send_checked(handler, request, Response, HTTPError)


def _apply_cookies(headers: dict[str, str], net: _Net, url: str) -> None:
    """Replace any incoming Cookie with the approval's read-only jar, or drop it."""

    for name in list(headers):
        if name.lower() == "cookie":
            headers.pop(name)
    if not net.cookies:
        return
    header = _cookie_header(net.cookies, url, net.cookie_hosts)
    if header:
        headers["Cookie"] = header


def _exchange(
    net: _Net,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    *,
    follow: bool,
) -> tuple[int, dict[str, str], bytes, str]:
    """One checked exchange. ``follow`` is false for the share-link GET."""

    purpose = _purpose.get()
    body_cap = net.media_max if purpose == "media" else None
    redirects = 0
    followed = False
    while True:
        # A challenge URL is refused before DNS. Redirect targets are approved first.
        if not followed and _challenge_target(url):
            raise _challenge_error()
        if net.budget.remaining <= 0:
            raise _limit_error()
        checked = net.checker(url, purpose=purpose)
        if followed:
            _stop_for_target(checked.url)
        _apply_cookies(headers, net, checked.url)
        limit = net.budget.remaining
        if body_cap is not None:
            limit = min(limit, body_cap)
        timeout = net.timeout if net.before_dial is None else net.before_dial()
        status, response_headers, payload, peer, reason = _unpack_dial(
            _dial(method, checked.url, headers, body, checked, timeout, limit)
        )
        parsed = urlsplit(checked.url)
        _observe(checked.hostname, parsed.path, peer, purpose, status, len(payload))
        # Every received body spends the job budget, including redirects and errors.
        net.budget.take(len(payload))
        if net.state is not None:
            net.state.bytes_used = net.budget.used
        # Check the redirect host before interpreting any challenge/login marker,
        # including a marker in Location itself. This also covers follow=False.
        target = _location(response_headers) if status in _REDIRECTS else None
        redirect_url = None
        if target:
            nxt = urljoin(checked.url, target)
            redirect = net.checker(nxt, purpose="redirect")
            redirect_url = redirect.url
        if _challenge_marked(status, response_headers, payload, reason):
            raise _challenge_error()
        if redirect_url is not None:
            _stop_for_target(redirect_url)
        # Set-Cookie is not a credential we keep. The jar extension stays stripped.
        for name in list(response_headers):
            if name.lower() == "set-cookie":
                response_headers.pop(name)
        if follow and status in _REDIRECTS:
            redirects += 1
            if redirects > MAX_REDIRECTS:
                raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Too many redirects")
            target = _location(response_headers)
            if not target:
                raise m.CoreError(
                    m.ErrorCode.INPUT_INVALID, "Upstream response could not be parsed"
                )
            nxt = urljoin(checked.url, target)
            previous = _endpoint(checked.url)
            following = _endpoint(nxt)
            if previous[0] == "https" and following[0] == "http":
                raise _downgrade_error()
            moved = previous != following
            if moved:
                _drop_header(headers, "Authorization")
            if status in (307, 308) and body is not None and moved:
                raise _body_forward_error()
            url = nxt
            purpose = "redirect"
            followed = True
            if status in (301, 302, 303):
                method = "GET"
                body = None
            continue
        return status, response_headers, payload, checked.url


def _send_checked(handler: Any, request: Any, response_type: type, http_error: type) -> Any:
    net: _Net = handler._net
    method = request.method or ("POST" if request.data is not None else "GET")
    if request.data is None:
        body: bytes | None = None
    elif isinstance(request.data, bytes):
        body = request.data
    else:
        raise m.CoreError(m.ErrorCode.UNSUPPORTED, "Request body is not a single buffer")
    headers = dict(handler._get_headers(request))
    for name in list(headers):
        if name.lower() in ("cookie", "x-forwarded-for", "proxy-authorization"):
            headers.pop(name)
    status, response_headers, payload, final_url = _exchange(
        net, method, request.url, headers, body, follow=True
    )
    stream = io.BytesIO(payload)
    response = response_type(stream, final_url, response_headers, status=status)
    if status >= 400:
        raise http_error(response)
    return response


def _handler_class(net: _Net) -> type:
    from yt_dlp.networking.common import RequestHandler
    from yt_dlp.networking.exceptions import RequestError, UnsupportedRequest

    class MetaMuseRH(RequestHandler):
        _SUPPORTED_URL_SCHEMES = ("http", "https")
        _SUPPORTED_PROXY_SCHEMES = ()
        _SUPPORTED_FEATURES = None

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._net = net

        def _check_extensions(self, extensions: dict[str, Any]) -> None:
            extensions.pop("cookiejar", None)
            extensions.pop("timeout", None)
            extensions.pop("legacy_ssl", None)
            extensions.pop("keep_header_casing", None)
            if "impersonate" in extensions:
                raise RequestError(
                    "Browser impersonation is refused",
                    cause=m.CoreError(
                        m.ErrorCode.UNSUPPORTED,
                        "Browser impersonation is refused",
                        next_action="Do not set an impersonate target. Retry is not a bypass.",
                    ),
                )

        def _validate(self, request: Any) -> None:
            self._check_url_scheme(request)
            proxies = request.proxies or self.proxies or {}
            for value in proxies.values():
                if value in (None, "", "__noproxy__"):
                    continue
                raise RequestError(
                    "A proxy is refused",
                    cause=m.CoreError(
                        m.ErrorCode.SCOPE_DENIED,
                        "A proxy is refused",
                        next_action=(
                            "Unset proxy environment variables. Remote media dials directly."
                        ),
                    ),
                )
            extensions = request.extensions.copy()
            self._check_extensions(extensions)
            if extensions:
                raise UnsupportedRequest(f"Unsupported extensions: {', '.join(extensions)}")

        def _send(self, request: Any) -> Any:
            try:
                return metamuse_request_handler_send(self, request)
            except RequestError:
                raise
            except m.CoreError as exc:
                raise RequestError(exc.detail.message, cause=exc) from exc

    return MetaMuseRH


def _open_ydl(params: dict[str, Any], ie_cls: type, net: _Net) -> Any:
    _disable_plugins()
    from yt_dlp import YoutubeDL

    ydl = YoutubeDL(params, auto_init=False)
    ydl.add_info_extractor(ie_cls())
    director = ydl.build_request_director([_handler_class(net)])
    ydl.__dict__["_request_director"] = director
    LAST_DIRECTOR_KEYS[:] = sorted(director.handlers)
    return ydl


def _chain(exc: BaseException) -> Iterable[BaseException]:
    seen: set[int] = set()
    stack: list[BaseException | None] = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        stack.append(current.__cause__)
        stack.append(current.__context__)
        info = getattr(current, "exc_info", None)
        if isinstance(info, tuple) and len(info) > 1 and isinstance(info[1], BaseException):
            stack.append(info[1])
        cause = getattr(current, "cause", None)
        if isinstance(cause, BaseException):
            stack.append(cause)
        for name in ("unexpected_errors", "unsupported_errors"):
            extra = getattr(current, name, None)
            if isinstance(extra, list):
                stack.extend(item for item in extra if isinstance(item, BaseException))


def _http_status(node: BaseException) -> tuple[int, dict[str, str]] | None:
    if node.__class__.__name__ != "HTTPError":
        return None
    status = getattr(node, "status", None)
    response = getattr(node, "response", None)
    headers: dict[str, str] = {}
    if response is not None:
        message = getattr(response, "headers", None)
        if message is not None and hasattr(message, "items"):
            headers = {str(key): str(value) for key, value in message.items()}
    if isinstance(status, int):
        return status, headers
    return None


def _classify(exc: BaseException) -> tuple[str, Any]:
    core: m.CoreError | None = None
    http: tuple[int, dict[str, str]] | None = None
    geo = False
    texts: list[str] = []
    refused = False
    for node in _chain(exc):
        if isinstance(node, _ExternalProcessRefused):
            refused = True
        if isinstance(node, m.CoreError) and core is None:
            core = node
        found = _http_status(node)
        if found is not None and http is None:
            http = found
        if node.__class__.__name__ == "GeoRestrictedError":
            geo = True
        texts.append(str(node))
    if refused:
        return "raise", _process_error()
    if core is not None:
        return "core", core
    blob = "\n".join(texts)
    lowered = blob.lower()
    if http is not None:
        status, headers = http
        if status == 429:
            return "rate", _retry_after(headers)
        if status == 403:
            return "raise", m.CoreError(
                m.ErrorCode.ACCESS_DENIED,
                "Upstream refused the request",
                next_action="Stop. Do not rotate credentials or addresses.",
            )
        if status == 401:
            return "raise", m.CoreError(
                m.ErrorCode.AUTH_REQUIRED,
                "Login required",
                next_action="Stop. Do not retry a login wall.",
            )
        if status in (404, 410):
            return "raise", m.CoreError(
                m.ErrorCode.RESOURCE_UNAVAILABLE,
                "Media is unavailable",
                next_action="Stop. The upstream item is not available.",
            )
    if "http error 403" in lowered or "403 forbidden" in lowered:
        return "raise", m.CoreError(
            m.ErrorCode.ACCESS_DENIED,
            "Upstream refused the request",
            next_action="Stop. Do not rotate credentials or addresses.",
        )
    if "http error 401" in lowered:
        return "raise", m.CoreError(
            m.ErrorCode.AUTH_REQUIRED,
            "Login required",
            next_action="Stop. Do not retry a login wall.",
        )
    if "http error 429" in lowered or "too many requests" in lowered:
        return "rate", None
    if "http error 404" in lowered or "http error 410" in lowered:
        return "raise", m.CoreError(
            m.ErrorCode.RESOURCE_UNAVAILABLE,
            "Media is unavailable",
            next_action="Stop. The upstream item is not available.",
        )
    if geo or "Geo restriction" in blob:
        return "raise", m.CoreError(
            m.ErrorCode.RESOURCE_UNAVAILABLE,
            "Geo restriction",
            next_action="Stop. Do not change address or use a proxy.",
        )
    if "platform challenge" in lowered or "captcha" in lowered:
        return "raise", m.CoreError(
            m.ErrorCode.CHECKPOINT_REQUIRED,
            "Platform challenge",
            next_action="Stop. Do not solve a challenge or retry it.",
        )
    if "login required" in lowered or "registered users" in lowered:
        return "raise", m.CoreError(
            m.ErrorCode.AUTH_REQUIRED,
            "Login required",
            next_action="Stop. Do not retry a login wall.",
        )
    if "media is unavailable" in lowered or "video is unavailable" in lowered:
        return "raise", m.CoreError(
            m.ErrorCode.RESOURCE_UNAVAILABLE,
            "Media is unavailable",
            next_action="Stop. The upstream item is not available.",
        )
    if "playlist is empty" in lowered:
        return "empty", None
    if "could not be parsed" in lowered or "json" in lowered and "expecting" in lowered:
        return "raise", m.CoreError(
            m.ErrorCode.INPUT_INVALID, "Upstream response could not be parsed"
        )
    return "raise", m.CoreError(m.ErrorCode.UNSUPPORTED, "Extractor failed")


def _raw_time(value: Any) -> str | None:
    """Keep the upstream timestamp as text, including a fractional second."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return format(value, ".15g")
    if isinstance(value, str) and value:
        return value
    return None


def _playlist_error() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.UNSUPPORTED,
        "The upstream result is a playlist",
        next_action="submit the individual media URL",
    )


def _single_format(info: dict[str, Any]) -> tuple[str, str] | None:
    formats = info.get("formats")
    if not isinstance(formats, list):
        formats = [info] if info.get("url") else []
    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        url = fmt.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        protocol = str(fmt.get("protocol") or "http").lower()
        if protocol not in ("http", "https"):
            continue
        if fmt.get("fragments") or "m3u8" in protocol or "dash" in protocol or "m3u8" in url:
            continue
        ext = str(fmt.get("ext") or "bin").lower()
        if ext in ("m3u8", "mpd"):
            continue
        return url, ext
    return None


def _tracks(info: dict[str, Any], cap: int) -> tuple[list[dict[str, str | None]], bool]:
    chosen: list[dict[str, str | None]] = []
    groups = (("provided", "subtitles"), ("auto", "automatic_captions"))
    for method, key in groups:
        group = info.get(key) or {}
        if not isinstance(group, dict):
            continue
        for language, items in group.items():
            if len(chosen) >= cap:
                return chosen, True
            if not isinstance(language, str) or not isinstance(items, list):
                continue
            pick: dict[str, Any] | None = None
            unsupported = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                ext = str(item.get("ext") or "").lower()
                if ext in ("vtt", "srt") and isinstance(item.get("url"), str):
                    pick = item
                    break
                if item.get("url"):
                    unsupported = True
            if pick is not None:
                chosen.append(
                    {
                        "language": language,
                        "method": method,
                        "url": str(pick["url"]),
                        "ext": str(pick.get("ext") or "vtt").lower(),
                    }
                )
            elif unsupported:
                chosen.append(
                    {"language": language, "method": method, "url": None, "ext": "unsupported"}
                )
    return chosen, False


def _normalize(info: dict[str, Any], url: str, extractor: str, cap: int) -> dict[str, Any] | None:
    if info.get("_type") in ("playlist", "multi_video"):
        entries = [item for item in (info.get("entries") or []) if isinstance(item, dict)]
        declared = info.get("playlist_count")
        several = len(entries) > 1 or (isinstance(declared, int) and declared > 1)
        if several:
            raise _playlist_error()
        if len(entries) == 1:
            info = entries[0]
        else:
            return None
    native = info.get("id")
    if not isinstance(native, str) or not native:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Upstream response could not be parsed")
    title = info.get("title") if isinstance(info.get("title"), str) else None
    # yt-dlp stores a missing title as fulltitle None and then invents "<extractor> video #<id>".
    if "fulltitle" in info and not info.get("fulltitle"):
        title = None
    elif isinstance(title, str) and isinstance(info.get("extractor"), str):
        invented = f"{str(info['extractor']).replace(':', '-')} video #{native}"
        if title == invented:
            title = None
    scope = info.get("channel_id") if isinstance(info.get("channel_id"), str) else ""
    if not scope and isinstance(info.get("uploader_id"), str):
        scope = info["uploader_id"]
    canonical = info.get("webpage_url") if isinstance(info.get("webpage_url"), str) else url
    if not canonical.startswith(("http://", "https://")):
        canonical = url
    key = info.get("extractor_key") if isinstance(info.get("extractor_key"), str) else extractor
    media = _single_format(info)
    tracks, truncated = _tracks(info, cap)
    return {
        "native_id": native,
        "title": title,
        "raw_time": _raw_time(info.get("timestamp")),
        "scope": scope or "",
        "canonical_url": canonical,
        "extractor": key,
        "platform": key.lower(),
        "subtitles": tracks,
        "subtitles_truncated": truncated,
        "media_url": None if media is None else media[0],
        "media_ext": None if media is None else media[1],
    }


@dataclass
class _State:
    next_input: int = 0
    phase: str = "start"
    bytes_used: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    halt: str | None = None
    current: dict[str, Any] | None = None
    retry_after: int | None = None
    code: str | None = None
    message: str | None = None
    caps: dict[str, int] = field(default_factory=dict)

    def checkpoint(self) -> dict[str, Any]:
        # get_job returns this document. Upstream media and subtitle URLs stay
        # in memory for this process and are fetched again after a resume.
        return {
            "remote_media_caps": self.caps,
            "next_input": self.next_input,
            "phase": self.phase,
            "bytes_used": self.bytes_used,
            "rows": self.rows,
            "missing_fields": self.missing,
            "halt": self.halt,
            "current": _durable_current(self.current),
            "retry_after": self.retry_after,
            "code": self.code,
            "message": self.message,
        }

    @classmethod
    def load(cls, payload: dict[str, Any]) -> _State:
        return cls(
            caps=dict(payload.get("remote_media_caps") or {}),
            next_input=int(payload.get("next_input") or 0),
            phase=str(payload.get("phase") or "start"),
            bytes_used=int(payload.get("bytes_used") or 0),
            rows=list(payload.get("rows") or []),
            missing=list(payload.get("missing_fields") or []),
            halt=payload.get("halt"),
            current=payload.get("current"),
            retry_after=payload.get("retry_after"),
            code=payload.get("code"),
            message=payload.get("message"),
        )


def _durable_current(current: dict[str, Any] | None) -> dict[str, Any] | None:
    """Ids, revision, hashes, and counts.

    Media, subtitle, and tracking URLs stay out. A canonical share target may
    be stored so a resume does not request the share link again.
    """

    if not current:
        return None
    tracks = current.get("subtitles") if isinstance(current.get("subtitles"), list) else []
    durable: dict[str, Any] = {
        "source_id": current.get("source_id"),
        "item_id": current.get("item_id"),
        "revision": current.get("revision"),
        "native_id": current.get("native_id"),
        "platform": current.get("platform"),
        "scope": current.get("scope"),
        "extractor": current.get("extractor"),
        "text_hash": current.get("text_hash"),
        "subtitles_truncated": bool(current.get("subtitles_truncated")),
        "subtitle_count": len(tracks),
    }
    resolved = current.get("resolved_url")
    if isinstance(resolved, str) and _canonical_media(resolved) == resolved:
        durable["resolved_url"] = resolved
    return {key: value for key, value in durable.items() if value is not None}


def _remember_urls(state: _State, record: dict[str, Any]) -> None:
    """Keep discovered URLs in this process only. The checkpoint drops them."""

    current = dict(state.current or {})
    current["subtitles"] = record["subtitles"]
    current["subtitles_truncated"] = bool(record["subtitles_truncated"])
    current["media_url"] = record["media_url"]
    current["media_ext"] = record["media_ext"]
    current["urls_ready"] = True
    state.current = current


def _same_identity(current: dict[str, Any], record: dict[str, Any]) -> bool:
    """Native id, extractor, platform, and scope are the checkpoint identity."""

    return all(
        current.get(key) == record[key] for key in ("native_id", "extractor", "platform", "scope")
    )


def _finish_stale_input(step: StepContext, state: _State, resources: set[str], used: int) -> None:
    """Keep the committed metadata and do not attach this page's resources to it."""

    message = "Upstream identity no longer matches the checkpoint"
    current = state.current or {}
    for row in state.rows:
        if row.get("input") == state.next_input and row.get("item_id") == current.get("item_id"):
            row["code"] = m.ErrorCode.DATA_STALE.value
            row["message"] = message
    state.code = m.ErrorCode.DATA_STALE.value
    state.message = message
    for name in ("subtitles", "media_file"):
        if name in resources and name not in state.missing:
            state.missing.append(name)
    state.next_input += 1
    state.phase = "start"
    state.current = None
    state.bytes_used = used
    _commit(step, state, None)


def _marker_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads((path / _OWNER_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _claim_staging(store: Any, job_id: str) -> Path:
    directory = store.paths.root / "staging" / job_id
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / _OWNER_NAME
    payload = {"job_id": job_id, "operation": OPERATION}
    if _marker_payload(directory) != payload:
        marker.write_text(json.dumps(payload), encoding="utf-8")
        marker.chmod(0o600)
    return directory


def _sweep(store: Any, current: str, *, remove_current: bool) -> None:
    staging = store.paths.root / "staging"
    if not staging.is_dir() or staging.is_symlink():
        return
    root = staging.resolve()
    with store.connection() as conn:
        known = {
            row["id"]: (row["operation"], row["state"])
            for row in conn.execute("SELECT id, operation, state FROM jobs")
        }
    for child in staging.iterdir():
        if child.is_symlink() or not child.is_dir():
            continue
        try:
            child.resolve().relative_to(root)
        except ValueError:
            log.debug("staging left in place; directory escapes the staging root")
            continue
        payload = _marker_payload(child)
        if payload is None or payload.get("job_id") != child.name:
            continue
        if payload.get("operation") != OPERATION:
            log.debug("staging left in place; ownership marker is not remote media")
            continue
        job = known.get(child.name)
        if job is None or job[0] != OPERATION:
            continue
        if (remove_current and child.name == current) or job[1] in jobs.TERMINAL:
            shutil.rmtree(child)


def _commit(
    step: StepContext, state: _State, write: Callable[[TransactionWriter], None] | None
) -> None:
    phase = state.phase
    index = state.next_input

    def batch(writer: TransactionWriter) -> None:
        if write is not None:
            write(writer)
        writer.insert(
            m.Event(
                id=m.new_id("evt"),
                object_id=step.job.id,
                job_id=step.job.id,
                project_id=step.job.project_id,
                kind="remote_media.checkpoint",
                detail={"input": index, "phase": phase},
                created_at=step.store.clock(),
            )
        )

    step.commit_checkpoint(state.checkpoint(), write_batch=batch)
    log.debug("remote media checkpoint input %s phase %s", index, phase)
    _note(step)


def _store_metadata(
    step: StepContext,
    state: _State,
    record: dict[str, Any],
    url: str,
    text_max: int,
) -> None:
    title = record["title"]
    title_missing = title is None
    if isinstance(title, str):
        try:
            m.utf8_text(title, text_max)
        except ValueError as exc:
            raise _limit_error() from exc
    if title_missing and "title" not in state.missing:
        state.missing.append("title")
    digest = m.payload_digest({"text": title})
    project = step.job.project_id
    platform = record["platform"]
    scope = record["scope"]
    native = record["native_id"]
    now = step.store.clock()
    evidence = {
        "collection_method": COLLECTION_METHOD,
        "upstream_collection_method": record["extractor"],
        "extractor": record["extractor"],
        "original_url": url,
        "canonical_url": record["canonical_url"],
        "native_id": native,
        "fetched_at": now,
        "upstream_timestamp": record["raw_time"],
    }
    basis = {
        "extractor": record["extractor"],
        "original_url": url,
        "canonical_url": record["canonical_url"],
        "native_id": native,
        "fetched_at": now,
        "upstream_timestamp": record["raw_time"],
    }
    chain = record.get("extractor_chain")
    if isinstance(chain, list) and chain:
        evidence["extractor_chain"] = list(chain)
        basis["extractor_chain"] = list(chain)
    if record.get("resolved_url"):
        evidence["requested_url"] = record["requested_url"]
        evidence["resolved_url"] = record["resolved_url"]
        basis["requested_url"] = record["requested_url"]
        basis["resolved_url"] = record["resolved_url"]
    with step.store.connection() as conn:
        found = conn.execute(
            "SELECT id FROM sources WHERE project_id=? AND platform=? "
            "AND native_scope=? AND native_id=?",
            (project, platform, scope, native),
        ).fetchone()
        source_id = str(found["id"]) if found is not None else m.new_id("src")
        source_new = found is None
        item = None
        if not source_new:
            item = conn.execute(
                "SELECT id FROM items WHERE source_id=? AND native_id=? "
                "AND identity_confidence='reliable'",
                (source_id, native),
            ).fetchone()
        item_id = str(item["id"]) if item is not None else m.new_id("itm")
        item_new = item is None
        current = None
        if not item_new:
            current = conn.execute(
                "SELECT revision, text_hash FROM item_revisions WHERE item_id=? "
                "ORDER BY revision DESC LIMIT 1",
                (item_id,),
            ).fetchone()
    if current is not None and current["text_hash"] == digest:
        revision = int(current["revision"])
        status = "unchanged"
        revision_new = False
    else:
        revision = 1 if current is None else int(current["revision"]) + 1
        status = "committed"
        revision_new = True
    state.current = {
        "source_id": source_id,
        "item_id": item_id,
        "revision": revision,
        "status": status,
        "subtitles": record["subtitles"],
        "subtitles_truncated": bool(record["subtitles_truncated"]),
        "media_url": record["media_url"],
        "media_ext": record["media_ext"],
        "native_id": native,
        "platform": platform,
        "scope": scope,
        "extractor": record["extractor"],
        "text_hash": digest,
        "urls_ready": True,
    }
    if record.get("resolved_url"):
        state.current["resolved_url"] = record["resolved_url"]
    row = {
        "input": state.next_input,
        "status": status,
        "source_id": source_id,
        "item_id": item_id,
        "revision": revision,
    }
    if isinstance(chain, list) and chain:
        row["extractor_chain"] = list(chain)
    if record.get("resolved_url"):
        row["requested_url"] = record["requested_url"]
        row["resolved_url"] = record["resolved_url"]
    state.rows.append(row)
    state.phase = "metadata"

    def write(writer: TransactionWriter) -> None:
        existing = writer.execute(
            "SELECT id FROM sources WHERE project_id=? AND platform=? "
            "AND native_scope=? AND native_id=?",
            (project, platform, scope, native),
        ).fetchone()
        if existing is None:
            writer.insert(
                m.Source(
                    id=source_id,
                    project_id=project,
                    platform=platform,
                    native_scope=scope,
                    native_id=native,
                    original_url=url,
                    canonical_url=record["canonical_url"],
                    collection_method=COLLECTION_METHOD,
                    upstream_collection_method=record["extractor"],
                    fetched_at=now,
                    use_basis=basis,
                )
            )
        held = writer.execute(
            "SELECT id FROM items WHERE source_id=? AND native_id=? "
            "AND identity_confidence='reliable'",
            (source_id, native),
        ).fetchone()
        if held is None:
            writer.insert(
                m.Item(
                    id=item_id,
                    project_id=project,
                    source_id=source_id,
                    kind="post",
                    native_id=native,
                    identity_confidence="reliable",
                )
            )
        if revision_new:
            prior = writer.execute(
                "SELECT 1 FROM item_revisions WHERE item_id=? AND revision=?",
                (item_id, revision),
            ).fetchone()
            if prior is None:
                writer.insert(
                    m.ItemRevision(
                        item_id=item_id,
                        revision=revision,
                        text=title,
                        text_hash=digest,
                        raw_time=record["raw_time"],
                        parsed_time=None,
                        fetched_at=now,
                        complete=not title_missing,
                        missing_fields=["title"] if title_missing else [],
                        evidence=evidence,
                    )
                )
        if not source_new:
            # B05 keeps one item revision when the title hash is unchanged.
            # A new upstream timestamp is an observation on the mutable source.
            _observe_timestamp(writer, source_id, record["raw_time"], now)

    _commit(step, state, write)


def _observe_timestamp(
    writer: TransactionWriter, source_id: str, raw_time: str | None, now: str
) -> None:
    """Record a timestamp change without appending an item revision."""

    held = writer.execute("SELECT use_basis FROM sources WHERE id=?", (source_id,)).fetchone()
    if held is None or not held["use_basis"]:
        return
    previous = json.loads(held["use_basis"])
    if not isinstance(previous, dict) or previous.get("upstream_timestamp") == raw_time:
        return
    observed = dict(previous)
    observed["upstream_timestamp"] = raw_time
    observed["fetched_at"] = now
    writer.execute(
        "UPDATE sources SET use_basis=? WHERE id=?",
        (m.canonical_json(observed).decode(), source_id),
    )


def _subtitle_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _store_subtitles(
    step: StepContext,
    state: _State,
    ydl: Any,
    *,
    wanted: bool,
) -> None:
    current = state.current or {}
    tracks = list(current.get("subtitles") or [])
    stored = 0
    if not wanted:
        state.phase = "subtitles"
        _commit(step, state, None)
        return
    if not tracks:
        if "subtitles" not in state.missing:
            state.missing.append("subtitles")
        state.phase = "subtitles"
        _commit(step, state, None)
        return
    project = step.job.project_id
    item_id = current["item_id"]
    now = step.store.clock()
    token = _purpose.set("subresource")
    try:
        for track in tracks:
            if track.get("url") is None or track.get("ext") not in ("vtt", "srt"):
                if "subtitles" not in state.missing:
                    state.missing.append("subtitles")
                continue
            try:
                response = ydl.urlopen(track["url"])
                payload = response.read().decode("utf-8")
            except m.CoreError:
                raise
            except Exception as exc:
                kind, value = _classify(exc)
                if kind == "core":
                    raise value from exc
                if kind == "raise" and value.detail.code in (
                    m.ErrorCode.AUTH_REQUIRED,
                    m.ErrorCode.ACCESS_DENIED,
                    m.ErrorCode.CHECKPOINT_REQUIRED,
                    m.ErrorCode.RESOURCE_UNAVAILABLE,
                ):
                    raise value from exc
                if kind == "rate":
                    state.halt = "rate_limited"
                    state.retry_after = value
                    state.code = m.ErrorCode.RATE_LIMITED.value
                    state.message = "Upstream rate limit"
                    if "subtitles" not in state.missing:
                        state.missing.append("subtitles")
                    break
                if "subtitles" not in state.missing:
                    state.missing.append("subtitles")
                continue
            parsed = media_mod.parse_subtitles(payload, str(track["ext"]))
            # Any malformed cue keeps the track incomplete. Valid cues are still stored.
            incomplete = bool(parsed.errors)
            if incomplete and "subtitles" not in state.missing:
                state.missing.append("subtitles")
            if not parsed.cues:
                if "subtitles" not in state.missing:
                    state.missing.append("subtitles")
                continue
            language = str(track["language"])
            method = str(track["method"])
            digest = _subtitle_sha(payload)
            native = f"{current['native_id']}:subtitle:{language}:{method}"

            def write(
                writer: TransactionWriter,
                native: str = native,
                digest: str = digest,
                cues: list[Any] = parsed.cues,
                language: str = language,
                method: str = method,
                incomplete: bool = incomplete,
            ) -> None:
                _write_subtitle_track(
                    writer,
                    project=project,
                    current=current,
                    item_id=item_id,
                    native=native,
                    digest=digest,
                    cues=cues,
                    language=language,
                    method=method,
                    incomplete=incomplete,
                    now=now,
                )

            _commit_cues(step, state, write)
            stored += 1
    finally:
        _purpose.reset(token)
    if current.get("subtitles_truncated") and "subtitles" not in state.missing:
        state.missing.append("subtitles")
    if stored == 0 and "subtitles" not in state.missing:
        state.missing.append("subtitles")
    state.phase = "subtitles"
    _commit(step, state, None)


def _write_subtitle_track(
    writer: TransactionWriter,
    *,
    project: str,
    current: dict[str, Any],
    item_id: str,
    native: str,
    digest: str,
    cues: list[Any],
    language: str,
    method: str,
    incomplete: bool,
    now: str,
) -> None:
    """Store one track. A new file hash appends a revision; an incomplete file never shortcuts."""

    found = writer.execute(
        "SELECT id, use_basis, json_extract(use_basis, '$.complete') AS complete, "
        "json_extract(use_basis, '$.file_sha256') AS sha FROM sources "
        "WHERE project_id=? AND platform=? AND native_scope=? AND native_id=?",
        (project, current["platform"], current["scope"], native),
    ).fetchone()
    if found is not None and found["complete"] and found["sha"] == digest:
        return
    changed = found is not None and found["sha"] != digest
    if found is None:
        source_id = m.new_id("src")
        revision = 1
    else:
        source_id = str(found["id"])
        peak = writer.execute(
            "SELECT MAX(revision) AS revision FROM transcript_segments "
            "WHERE source_id=? AND project_id=?",
            (source_id, project),
        ).fetchone()
        held = 0 if peak is None or peak["revision"] is None else int(peak["revision"])
        revision = held + 1 if changed else max(held, 1)
    prior: list[dict[str, Any]] = []
    if found is not None and found["use_basis"]:
        loaded = json.loads(found["use_basis"])
        if isinstance(loaded, dict) and isinstance(loaded.get("revisions"), list):
            prior = [item for item in loaded["revisions"] if isinstance(item, dict)]
    revisions = prior
    if found is None or changed:
        revisions = [*prior, {"revision": revision, "file_sha256": digest}]
    basis = {
        "file_sha256": digest,
        "language": language,
        "role": "subtitles",
        "item_id": item_id,
        "asset_id": None,
        "complete": 0 if incomplete else 1,
        "cue_count": len(cues),
        "method": method,
        "revisions": revisions,
    }
    if found is None:
        writer.insert(
            m.Source(
                id=source_id,
                project_id=project,
                platform=current["platform"],
                native_scope=current["scope"],
                native_id=native,
                collection_method=COLLECTION_METHOD,
                upstream_collection_method=current["extractor"],
                fetched_at=now,
                use_basis=basis,
            )
        )
    else:
        writer.execute(
            "UPDATE sources SET use_basis=? WHERE id=?",
            (m.canonical_json(basis).decode(), source_id),
        )
    for cue in cues:
        existing = writer.execute(
            "SELECT id FROM transcript_segments WHERE source_id=? AND project_id=? "
            "AND cue_index=? ORDER BY revision DESC LIMIT 1",
            (source_id, project, cue.cue_index),
        ).fetchone()
        if existing is not None and not changed:
            continue
        writer.insert(
            m.TranscriptSegment(
                id=str(existing["id"]) if existing is not None else m.new_id("cue"),
                project_id=project,
                asset_id=None,
                item_id=item_id,
                source_id=source_id,
                revision=revision,
                cue_index=cue.cue_index,
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                text=cue.text,
                language=language,
                method=method,  # type: ignore[arg-type]
                review="unreviewed",
                overlap=cue.overlap,
            )
        )


def _commit_cues(
    step: StepContext, state: _State, write: Callable[[TransactionWriter], None]
) -> None:
    """Cue rows commit with the current phase still at metadata until the phase note."""

    def batch(writer: TransactionWriter) -> None:
        write(writer)

    step.commit_checkpoint(state.checkpoint(), write_batch=batch)


def _store_media(
    step: StepContext, state: _State, ydl: Any, staging: Path, timeout: int, media_max: int
) -> None:
    current = state.current or {}
    url = current.get("media_url")
    if not url:
        if "media_file" not in state.missing:
            state.missing.append("media_file")
        state.phase = "media"
        _commit(step, state, None)
        return
    token = _purpose.set("media")
    staged = staging / f"media-{state.next_input}.bin"
    try:
        try:
            response = ydl.urlopen(url)
            payload = response.read()
        except m.CoreError:
            raise
        except Exception as exc:
            kind, value = _classify(exc)
            if kind == "core":
                raise value from exc
            if kind == "raise":
                raise value from exc
            if kind == "rate":
                state.halt = "rate_limited"
                state.retry_after = value
                state.code = m.ErrorCode.RATE_LIMITED.value
                state.message = "Upstream rate limit"
            if "media_file" not in state.missing:
                state.missing.append("media_file")
            state.phase = "media"
            _commit(step, state, None)
            return
        staged.write_bytes(payload)
        staged.chmod(0o600)
        try:
            info = media_mod.analyze_file(staged, max_bytes=media_max, read_timeout_seconds=timeout)
            media_mod.place_object(step.store, staged, info)
            media_mod.require_compatible_mime(step.store, info)
        except m.CoreError:
            if staged.exists():
                staged.unlink()
            if "media_file" not in state.missing:
                state.missing.append("media_file")
            state.phase = "media"
            _commit(step, state, None)
            return
    finally:
        _purpose.reset(token)

    def write(writer: TransactionWriter) -> None:
        asset = media_mod.ensure_asset(step.store, writer, info)
        source_id = current["source_id"]
        project = step.job.project_id
        linked = writer.execute(
            "SELECT 1 FROM source_assets WHERE source_id=? AND asset_id=? AND relation=?",
            (source_id, asset.id, "media"),
        ).fetchone()
        if linked is None:
            writer.insert(
                m.SourceAsset(
                    source_id=source_id,
                    project_id=project,
                    asset_id=asset.id,
                    relation="media",
                )
            )
        item_id = current["item_id"]
        revision = int(current["revision"])
        attached = writer.execute(
            "SELECT 1 FROM item_assets WHERE item_id=? AND revision=? "
            "AND asset_id=? AND relation=?",
            (item_id, revision, asset.id, "media"),
        ).fetchone()
        if attached is None:
            writer.insert(
                m.ItemAsset(
                    item_id=item_id,
                    revision=revision,
                    asset_id=asset.id,
                    relation="media",
                )
            )
        current["asset_id"] = asset.id
        current["asset_state"] = asset.state

    state.phase = "media"
    _commit(step, state, write)


def _result(step: StepContext, state: _State, resources: set[str]) -> m.JobResult:
    missing = list(state.missing)
    if state.halt == "rate_limited":
        coverage = "partial"
        stop = "rate_limited"
    elif state.halt == "limit_reached":
        coverage = "partial"
        stop = "limit_reached"
    elif state.halt == "data_stale":
        coverage = "partial"
        stop = "data_stale"
    elif state.halt == "empty" and not state.rows:
        coverage = "bounded"
        stop = "empty"
    elif state.halt == "empty":
        coverage = "partial"
        stop = "empty"
    elif missing:
        coverage = "partial"
        stop = "partial"
    else:
        satisfied = True
        if "subtitles" in resources and "subtitles" in missing:
            satisfied = False
        coverage = "scope_satisfied" if satisfied else "partial"
        stop = "input_exhausted" if coverage == "scope_satisfied" else "partial"
    data: dict[str, Any] = {"rows": state.rows, "bytes_used": state.bytes_used}
    if state.code:
        data["code"] = state.code
    if state.message:
        data["message"] = state.message
    if state.halt == "rate_limited":
        data["retry_after"] = state.retry_after
    return m.JobResult(
        coverage_status=coverage,  # type: ignore[arg-type]
        requested_scope=step.job.requested_scope,
        observed_scope={
            "inputs": state.next_input,
            "rows": len(state.rows),
            "bytes_used": state.bytes_used,
        },
        stop_reason=stop,
        missing_fields=sorted(set(missing)),
        data=data,
    )


def run_remote_media(step: StepContext) -> m.JobResult:
    request = m.CollectRequest.model_validate(step.payload)
    state = _State.load(step.checkpoint or {})
    snapshot = state.caps or {
        name: step.job.limits[name] for name in _WORKSPACE_CAPS if name in step.job.limits
    }
    if set(snapshot) != set(_WORKSPACE_CAPS):
        raise m.CoreError(
            m.ErrorCode.SCOPE_DENIED,
            "Remote acceptance caps are unavailable",
            next_action="Repeat the call without a grant to get a fresh approval preview",
        )
    try:
        checked_caps = m.Limits.model_validate(snapshot)
    except ValueError:
        raise m.CoreError(
            m.ErrorCode.STORAGE, "Stored remote acceptance caps are invalid"
        ) from None
    prepared = prepare_remote_media(step, request, workspace_caps=snapshot)
    workspace = prepared.workspace
    approvals = _bound_approvals(step.target)
    original_urls = [item.url for item in request.inputs if item.kind == "url"]
    urls = prepared.target["urls"]
    keys = list(step.target.get("extractors") or [])
    if len(keys) != len(urls):
        raise m.CoreError(
            m.ErrorCode.REFERENCE_INVALID, "Stored extractor list does not match the inputs"
        )
    if step.target != prepared.target:
        raise m.CoreError(
            m.ErrorCode.SCOPE_DENIED,
            "Permit does not cover this action",
            next_action="Repeat the call without a grant to get a fresh approval preview",
        )
    classes = [cls for cls, _approval in prepared.matched]
    input_hosts = [tuple(approval.hosts) for _cls, approval in prepared.matched]
    provider = prepared.target["provider"]
    account = prepared.target["account"]
    payload = prepared.payload
    use = safety.PermitUse(
        kind="access",
        project_id=request.project_id,
        provider=provider,
        account=account,
        operation=PERMIT_OPERATION,
        digest=safety.action_digest(step.target, payload),
    )

    def load_payload(conn: Any, target: dict[str, Any]) -> dict[str, Any]:
        stored = step.store.get(m.Job, step.job.id, conn=conn)
        # Use the boundary's policy snapshot: verification does no file/network IO
        # inside its transaction, while rebuilding the actual persisted request.
        current = prepare_remote_media(
            step,
            m.CollectRequest.model_validate(stored.payload),
            workspace=workspace,
            conn=conn,
            workspace_caps=snapshot,
        )
        if current.target != target:
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Permit does not cover this action",
                next_action="Repeat the call without a grant to get a fresh approval preview",
            )
        return current.payload

    caps = {
        name: min(getattr(checked_caps, name), getattr(workspace.limits, name))
        for name in _WORKSPACE_CAPS
    }
    state.caps = snapshot
    if step.step.retries > caps["safe_read_retries"]:
        previous = m.ErrorDetail.model_validate(
            step.step.error
            or {
                "code": m.ErrorCode.LIMIT_REACHED,
                "message": "Remote safe-read retry cap reached",
                "retry_class": "never",
            }
        )
        error = m.CoreError(
            previous.code,
            previous.message,
            next_action="The accepted retry cap was reached; start a newly approved request",
        )
        # This is a terminal local guard, even when the underlying read error is
        # normally retryable. Keep its original code/message for the operator.
        error.detail = error.detail.model_copy(update={"retry_class": "never"})
        raise error
    bound_cookies = _load_bound_cookies(approvals, provider, account, step.executor.secret_store)
    step.before_external_action(
        step.target,
        verify=safety.external_action_verifier(
            step,
            permit_id=str(request.access_grant_id),
            use=use,
            load_payload=load_payload,
        ),
    )
    resources = set(request.resources)
    text_max = caps["text_max_bytes"]
    media_max = caps["media_max_bytes"]
    timeout = caps["read_timeout_seconds"]
    budget = _Budget(used=state.bytes_used, limit=request.limits.max_bytes)
    net = _Net(
        checker=_checker_factory(input_hosts[0] if input_hosts else (), _system_resolver),
        budget=budget,
        media_max=media_max,
        timeout=float(timeout),
        state=state,
        before_dial=_dial_timeout(step, float(timeout)),
        secret_store=step.executor.secret_store,
        provider=provider,
        account=account,
        bound_cookies=bound_cookies,
    )
    staging = _claim_staging(step.store, step.job.id)
    _sweep(step.store, step.job.id, remove_current=False)
    try:
        while state.next_input < len(urls) and state.halt is None:
            step.guard()
            index = state.next_input
            approval = _approval_for(keys[index], urls[index], approvals)
            if approval is None:
                raise _host_denied()
            # Select only cookies already parsed before permit consumption.
            _bind_cookies(net, approval, provider, account, step.executor.secret_store)
            net.checker = _checker_factory(tuple(approval.hosts), _system_resolver)
            if state.phase in ("", "start") or state.current is None and state.phase == "start":
                state.phase = "start"
            if state.phase == "start":
                _extract_one(
                    step,
                    state,
                    urls[index],
                    classes[index],
                    keys[index],
                    net,
                    staging,
                    request.limits.max_items,
                    timeout,
                    media_max,
                    text_max,
                    approvals,
                    provider,
                    account,
                    original_url=original_urls[index],
                )
                if state.halt:
                    break
            if state.phase == "metadata":
                if "subtitles" in resources and not (state.current or {}).get("urls_ready"):
                    if budget.remaining <= 0:
                        state.halt = "limit_reached"
                        state.code = m.ErrorCode.LIMIT_REACHED.value
                        if "subtitles" not in state.missing:
                            state.missing.append("subtitles")
                        if "media_file" in resources and "media_file" not in state.missing:
                            state.missing.append("media_file")
                        break
                    _ensure_urls(
                        state,
                        urls[index],
                        classes[index],
                        keys[index],
                        net,
                        staging,
                        request.limits.max_items,
                        timeout,
                        media_max,
                        approvals,
                        provider,
                        account,
                    )
                    if state.halt:
                        break
                if (state.current or {}).get("stale"):
                    _finish_stale_input(step, state, resources, budget.used)
                    continue
                if budget.remaining <= 0 and "subtitles" in resources:
                    state.halt = "limit_reached"
                    state.code = m.ErrorCode.LIMIT_REACHED.value
                    if "subtitles" not in state.missing:
                        state.missing.append("subtitles")
                    if "media_file" in resources and "media_file" not in state.missing:
                        state.missing.append("media_file")
                    break
                params = ytdlp_params(
                    staging,
                    max_items=request.limits.max_items,
                    socket_timeout=timeout,
                    max_filesize=media_max,
                    extractor=keys[index],
                )
                ydl = _open_ydl(params, classes[index], net)
                try:
                    _store_subtitles(step, state, ydl, wanted="subtitles" in resources)
                finally:
                    ydl.close()
                if state.halt:
                    break
            if state.phase == "subtitles":
                if "media_file" not in resources:
                    state.phase = "media"
                    _commit(step, state, None)
                elif budget.remaining <= 0:
                    state.halt = "limit_reached"
                    state.code = m.ErrorCode.LIMIT_REACHED.value
                    if "media_file" not in state.missing:
                        state.missing.append("media_file")
                    break
                elif not (state.current or {}).get("urls_ready"):
                    _ensure_urls(
                        state,
                        urls[index],
                        classes[index],
                        keys[index],
                        net,
                        staging,
                        request.limits.max_items,
                        timeout,
                        media_max,
                        approvals,
                        provider,
                        account,
                    )
                    if state.halt:
                        break
                    if (state.current or {}).get("stale"):
                        _finish_stale_input(step, state, resources, budget.used)
                        continue
                    if budget.remaining <= 0:
                        state.halt = "limit_reached"
                        state.code = m.ErrorCode.LIMIT_REACHED.value
                        if "media_file" not in state.missing:
                            state.missing.append("media_file")
                        break
                if state.phase == "subtitles" and "media_file" in resources:
                    params = ytdlp_params(
                        staging,
                        max_items=request.limits.max_items,
                        socket_timeout=timeout,
                        max_filesize=media_max,
                        extractor=keys[index],
                    )
                    ydl = _open_ydl(params, classes[index], net)
                    try:
                        _store_media(step, state, ydl, staging, timeout, media_max)
                    finally:
                        ydl.close()
                    if state.halt:
                        break
            state.next_input += 1
            state.phase = "start"
            state.current = None
            state.bytes_used = budget.used
            _commit(step, state, None)
    except m.CoreError as exc:
        if exc.detail.code == m.ErrorCode.LIMIT_REACHED and state.rows:
            state.halt = "limit_reached"
            state.code = exc.detail.code.value
            state.bytes_used = budget.used
            return _result(step, state, resources)
        raise
    finally:
        if state.halt is None:
            _sweep(step.store, step.job.id, remove_current=True)
    if any(row.get("code") == m.ErrorCode.DATA_STALE.value for row in state.rows):
        state.halt = "data_stale"
        state.code = m.ErrorCode.DATA_STALE.value
        if not state.message:
            state.message = "Upstream identity no longer matches the checkpoint"
    state.bytes_used = budget.used
    return _result(step, state, resources)


def _still_approved(url: str, key: str, approvals: list[Any]) -> bool:
    if key.lower() in REJECTED_EXTRACTORS:
        return False
    parts = _share_parts(url)
    if parts is not None:
        if parts[1] == "p" or _SHARE_TARGET.get(parts[1]) != key:
            return False
        return _approval_for(key, url, approvals) is not None
    cls = _extractor_class(key)
    if cls is None or not cls.suitable(url):
        return False
    return _approval_for(key, url, approvals) is not None


def _access_denied() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.ACCESS_DENIED,
        "Upstream refused the request",
        next_action="Stop. Do not rotate credentials or addresses.",
    )


class _InputHalted(Exception):
    """The share resolver stopped this input. The job result is already on ``state``."""


def _halt_rate(net: _Net, headers: dict[str, str]) -> None:
    state = net.state
    if state is None:
        raise m.CoreError(
            m.ErrorCode.RATE_LIMITED,
            "Upstream rate limit",
            next_action="Wait for Retry-After before reading again.",
        )
    state.halt = "rate_limited"
    state.retry_after = _retry_after(headers)
    state.code = m.ErrorCode.RATE_LIMITED.value
    state.message = "Upstream rate limit"
    raise _InputHalted()


def _login_form(payload: bytes, url: str) -> bool:
    class LoginForms(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.bases: list[str] = []
            self.actions: list[str | None] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            values = dict(attrs)
            if tag == "base" and values.get("href") is not None:
                self.bases.append(values["href"])
            if tag == "form":
                self.actions.append(values.get("action"))

    parser = LoginForms()
    parser.feed(payload.decode("utf-8", "replace"))
    base = url
    for href in parser.bases:
        try:
            base = urljoin(url, href)
            break
        except ValueError:
            continue
    for action in parser.actions:
        try:
            # Missing/empty actions submit to the document URL, independent of base.
            if _login_target(urljoin(base, action) if action else url):
                return True
        except ValueError:
            continue
    return False


def _resolve_share(net: _Net, url: str, key: str, approval: Any) -> str:
    """One checked GET. The first redirect is read and not followed."""

    status, headers, payload, _final = _exchange(net, "GET", url, {}, None, follow=False)
    if status in _REDIRECTS:
        target = _location(headers)
        if not target:
            raise _share_unsupported()
        nxt = urljoin(url, target)
        if _endpoint(url)[0] == "https" and _endpoint(nxt)[0] == "http":
            raise _downgrade_error()
        parts = urlsplit(nxt)
        if parts.username is not None or parts.password is not None:
            raise _share_unsupported()
        try:
            host = safety.normalize_hostname(parts.hostname or "")
        except m.CoreError:
            raise _hop_denied(key, "unknown") from None
        if host not in _hosts(approval.hosts):
            raise _hop_denied(key, host)
        _stop_for_target(nxt)
        canonical = _canonical_media(nxt)
        cls = _extractor_class(key)
        if canonical is None or cls is None or not cls.suitable(canonical):
            raise _share_unsupported()
        if _host_of(canonical) not in _hosts(approval.hosts):
            raise _hop_denied(key, _host_of(canonical))
        return canonical
    if status == 429:
        _halt_rate(net, headers)
    if status in (404, 410):
        raise _unavailable()
    if status == 401:
        raise _login_error()
    if status == 403:
        raise _access_denied()
    if status == 200:
        if _login_form(payload, url):
            raise _login_error()
        raise _share_no_redirect()
    raise _share_unsupported()


def _resolved_still_approved(stored: str, key: str, approval: Any) -> None:
    """A resumed canonical URL must still match this approval and extractor."""

    host = _host_of(stored)
    if host not in _hosts(approval.hosts):
        raise _host_denied()
    cls = _extractor_class(key)
    if cls is None or not cls.suitable(stored):
        raise _host_denied()


def _page_url(
    state: _State, url: str, key: str, net: _Net, approvals: list[Any]
) -> tuple[str, str | None]:
    """Share links become a canonical media URL. A resume reuses the stored one."""

    if _share_parts(url) is None:
        return url, None
    approval = _approval_for(key, url, approvals)
    if approval is None:
        raise _host_denied()
    stored = (state.current or {}).get("resolved_url")
    if isinstance(stored, str) and _canonical_media(stored) == stored:
        _resolved_still_approved(stored, key, approval)
        return stored, stored
    resolved = _resolve_share(net, url, key, approval)
    return resolved, resolved


def _merge_transparent(inner: dict[str, Any], outer: dict[str, Any]) -> dict[str, Any]:
    """Outer descriptive fields may override. Identity and media stay with the inner result.

    This is narrower than ``YoutubeDL.process_ie_result`` for ``url_transparent``.
    That copy lets the outer page replace ``webpage_url``, and ``extractor_key``
    when ``section_start`` is set. The stored canonical URL and platform come
    from the final inner extractor.
    """

    merged = dict(inner)
    for name, value in outer.items():
        if value is not None and name in _OUTER_DESCRIPTIVE:
            merged[name] = value
    if merged.get("_type") == "url":
        merged["_type"] = "url_transparent"
    return merged


def _hop_target(info: dict[str, Any]) -> tuple[str, str]:
    hop_url = info.get("url")
    hop_key = info.get("ie_key")
    if not isinstance(hop_url, str) or not hop_url or not isinstance(hop_key, str) or not hop_key:
        raise m.CoreError(
            m.ErrorCode.UNSUPPORTED,
            "Extractor hop is missing a target",
            next_action="Stop. Submit the individual media URL.",
        )
    return hop_url, hop_key


def _prepare_hop(net: _Net, hop_url: str, hop_key: str, approvals: list[Any]) -> tuple[type, Any]:
    if _solver_key(hop_key):
        raise _solver_error()
    if hop_key.lower() in REJECTED_EXTRACTORS:
        raise _extractor_denied()
    host = _host_of(hop_url)
    approval = _approval_for(hop_key, hop_url, approvals)
    if approval is None:
        raise _hop_denied(hop_key, host)
    cls = _extractor_class(hop_key)
    if cls is None:
        raise _extractor_denied()
    if not cls.suitable(hop_url):
        raise _hop_denied(hop_key, host)
    net.checker = _checker_factory(tuple(approval.hosts), _system_resolver)
    _bind_cookies(net, approval, net.provider, net.account, net.secret_store)
    return cls, approval


def _apply_extraction_error(state: _State, exc: Exception) -> None:
    kind, value = _classify(exc)
    if kind == "empty":
        state.halt = "empty"
        return
    if kind == "rate":
        state.halt = "rate_limited"
        state.retry_after = value
        state.code = m.ErrorCode.RATE_LIMITED.value
        state.message = "Upstream rate limit"
        return
    if kind == "core":
        raise value from exc
    raise value from exc


def _coerce_result(info: dict[str, Any]) -> dict[str, Any]:
    """Refuse a multi-entry list, drop ``additional_urls``, and unwrap one entry.

    A single ``url`` or ``url_transparent`` entry is returned so the hop loop
    applies ``MAX_HOPS``, the approval, and ``suitable()``. Iteration of a lazy
    playlist happens here, inside ``_extracting``.
    """

    seen: dict[int, dict[str, Any]] = {}
    while True:
        info.pop("additional_urls", None)
        if (info.get("_type") or "video") not in _LIST_TYPES:
            return info
        if id(info) in seen:
            raise _playlist_error()
        seen[id(info)] = info
        declared = info.get("playlist_count")
        if isinstance(declared, int) and declared > 1:
            raise _playlist_error()
        entries: list[Any] = []
        raw = info.get("entries")
        if raw is not None:
            for item in raw:
                if item is None:
                    continue
                if isinstance(item, str):
                    item = {"_type": "url", "url": item}
                entries.append(item)
                if len(entries) > 1:
                    raise _playlist_error()
        if not entries:
            return {**info, "entries": []}
        only = entries[0]
        if not isinstance(only, dict):
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Upstream response could not be parsed")
        info = only


def _extract_chain(
    state: _State,
    url: str,
    ie_cls: type,
    key: str,
    net: _Net,
    staging: Path,
    max_items: int,
    timeout: int,
    media_max: int,
    approvals: list[Any],
) -> tuple[dict[str, Any], list[str]] | None:
    """Extract without letting yt-dlp follow ``url`` results itself."""

    chain = [key]
    current_url = url
    current_cls = ie_cls
    current_key = key
    pending: dict[str, Any] | None = None
    hops = 0
    while True:
        params = ytdlp_params(
            staging,
            max_items=max_items,
            socket_timeout=timeout,
            max_filesize=media_max,
            extractor=current_key,
        )
        ydl = _open_ydl(params, current_cls, net)
        follow: tuple[str, type, str] | None = None
        try:
            try:
                with _extracting():
                    info = ydl.extract_info(current_url, download=False, process=False)
                    if not isinstance(info, dict):
                        raise m.CoreError(
                            m.ErrorCode.INPUT_INVALID, "Upstream response could not be parsed"
                        )
                    info = _coerce_result(info)
                    if pending is not None:
                        info = _merge_transparent(info, pending)
                        pending = None
                    result_type = info.get("_type") or "video"
                    if result_type in ("url", "url_transparent"):
                        if hops >= MAX_HOPS:
                            raise _hop_limit()
                        hop_url, hop_key = _hop_target(info)
                        hop_cls, _approval = _prepare_hop(net, hop_url, hop_key, approvals)
                        if result_type == "url_transparent":
                            pending = info
                        hops += 1
                        chain.append(hop_key)
                        follow = (hop_url, hop_cls, hop_key)
                    else:
                        info.pop("additional_urls", None)
                        info = ydl.process_ie_result(info, download=False)
            except m.CoreError:
                raise
            except Exception as exc:
                _apply_extraction_error(state, exc)
                return None
        finally:
            ydl.close()
        if follow is None:
            if not isinstance(info, dict):
                raise m.CoreError(
                    m.ErrorCode.INPUT_INVALID, "Upstream response could not be parsed"
                )
            return info, chain
        current_url, current_cls, current_key = follow


def _read_info(
    state: _State,
    url: str,
    ie_cls: type,
    key: str,
    net: _Net,
    staging: Path,
    max_items: int,
    timeout: int,
    media_max: int,
    approvals: list[Any],
) -> dict[str, Any] | None:
    """Fetch one page. None means this input stopped; the body still counts."""

    try:
        extract_url, resolved = _page_url(state, url, key, net, approvals)
    except _InputHalted:
        return None
    found = _extract_chain(
        state,
        extract_url,
        ie_cls,
        key,
        net,
        staging,
        max_items,
        timeout,
        media_max,
        approvals,
    )
    if found is None:
        return None
    info, chain = found
    record = _normalize(info, extract_url, chain[-1], max_items)
    if record is None:
        state.halt = "empty"
        return None
    if str(record["extractor"]).lower() == "manual":
        raise m.CoreError(m.ErrorCode.UNSUPPORTED, "Extractor failed")
    record["extractor_chain"] = chain
    if resolved is not None:
        record["requested_url"] = url
        record["resolved_url"] = resolved
    state.bytes_used = net.budget.used
    return record


def _ensure_urls(
    state: _State,
    url: str,
    ie_cls: type,
    key: str,
    net: _Net,
    staging: Path,
    max_items: int,
    timeout: int,
    media_max: int,
    approvals: list[Any],
    provider: str,
    account: str,
) -> None:
    """Re-read the page after a resume. Signed URLs are not stored on the step."""

    del provider, account
    if state.current is None or state.current.get("urls_ready") or state.current.get("stale"):
        return
    record = _read_info(
        state, url, ie_cls, key, net, staging, max_items, timeout, media_max, approvals
    )
    if record is None:
        return
    if not _same_identity(state.current, record):
        current = dict(state.current)
        current["stale"] = True
        state.current = current
        return
    _remember_urls(state, record)


def _extract_one(
    step: StepContext,
    state: _State,
    url: str,
    ie_cls: type,
    key: str,
    net: _Net,
    staging: Path,
    max_items: int,
    timeout: int,
    media_max: int,
    text_max: int,
    approvals: list[Any],
    provider: str,
    account: str,
    *,
    original_url: str | None = None,
) -> None:
    del provider, account
    if (
        state.rows
        and len([row for row in state.rows if row.get("status") in ("committed", "unchanged")])
        >= max_items
    ):
        state.halt = "limit_reached"
        state.code = m.ErrorCode.LIMIT_REACHED.value
        return
    record = _read_info(
        state, url, ie_cls, key, net, staging, max_items, timeout, media_max, approvals
    )
    if record is None:
        return
    _store_metadata(step, state, record, url if original_url is None else original_url, text_max)
    state.bytes_used = net.budget.used
