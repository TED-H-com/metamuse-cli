"""Bounded remote media through yt-dlp's Python API.

Every yt-dlp request uses one MetaMuse request handler. The handler checks the
URL with the B08 policy, dials only the addresses from that check, and follows
redirects itself. Plugins, config files, proxies, impersonation, and cookies
stay off unless a future explicit auth reference is wired by core.

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
import os
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from metamuse import jobs, safety
from metamuse import media as media_mod
from metamuse import models as m
from metamuse._muse_http import _ca_bundle, _options, load_curl
from metamuse.config import load_config
from metamuse.jobs import StepContext, StepSpec, submit_job
from metamuse.store import TransactionWriter

OPERATION = "collect.remote_media"
PERMIT_OPERATION = "collect.remote"
YTDLP_VERSION = "2026.08.19"
YTDLP_REQUIREMENT = "yt-dlp==2026.8.19"
RESOURCES = frozenset({"media_metadata", "subtitles", "media_file"})
REJECTED_EXTRACTORS = frozenset({"generic", "unsupported", "end"})
COLLECTION_METHOD = "yt-dlp"
MAX_REDIRECTS = 5
_OWNER_NAME = ".collect-owner"
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
HANDLER_FRAME = "metamuse_request_handler_send"

log = logging.getLogger(__name__)
_purpose: contextvars.ContextVar[str] = contextvars.ContextVar(
    "remote_media_purpose", default="request"
)
_TEST_EXTRACTORS: list[type] = []
_observer: Callable[[dict[str, Any]], None] | None = None
LAST_DIRECTOR_KEYS: list[str] = []


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
        "cookiefile": None,
        "cookiesfrombrowser": None,
        "source_address": None,
        "geo_bypass": False,
        "nocheckcertificate": False,
        "legacyserverconnect": False,
        "postprocessors": [],
        "fixup": "never",
        "ffmpeg_location": None,
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


def load_cookie_header(auth_ref: str, *, provider: str, account: str) -> bytes:
    """Read a cookie secret. Submit never calls this: CollectRequest has no auth_ref."""

    return safety.resolve_secret(auth_ref, provider=provider, account=account)


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


def _grant_required() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.SCOPE_REQUIRED,
        "A permit is required",
        next_action=(
            "Create a B08 access permit for collect.remote whose target digest matches this request"
        ),
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
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or parsed.username is not None or parsed.password is not None:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Remote URL is not a bare http(s) URL")
    return host


def _hosts(values: Iterable[str]) -> set[str]:
    return {item.lower().rstrip(".") for item in values}


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


def _match(url: str, approvals: list[Any]) -> tuple[type, Any]:
    host = _host_of(url)
    suitable: list[tuple[type, Any]] = []
    for approval in approvals:
        cls = _extractor_class(approval.extractor)
        if cls is not None and cls.suitable(url):
            suitable.append((cls, approval))
    if not suitable:
        raise _extractor_denied()
    for cls, approval in suitable:
        if host in _hosts(approval.hosts):
            return cls, approval
    raise _host_denied()


def action_parts(
    request: m.CollectRequest, provider: str, account: str, extractors: list[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Permit target and payload. Hosts and byte limits are not part of the digest."""

    urls = [item.url for item in request.inputs if item.kind == "url"]
    resources = sorted(request.resources)
    target = {
        "project_id": request.project_id,
        "provider": provider,
        "account": account,
        "operation": PERMIT_OPERATION,
        "urls": urls,
        "resources": resources,
        "extractors": list(extractors),
    }
    payload = {"urls": urls, "resources": resources, "extractors": list(extractors)}
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


def submit_remote_media(ctx: Any, request: m.CollectRequest) -> m.Envelope:
    """Refuse before a job exists unless the extra, allowlist, and grant all pass."""

    register_operations()
    if not dependency_available():
        raise _dependency_error()
    ctx.store.get(m.Project, request.project_id)
    workspace = _workspace(ctx)
    _check_limits(request, workspace)
    if workspace.app.network_mode == "offline":
        raise _offline_error()
    approvals = list(workspace.remote_media.approvals)
    if not approvals:
        raise _allowlist_required()
    matched = [_match(item.url, approvals) for item in request.inputs if item.kind == "url"]
    if not request.access_grant_id:
        raise _grant_required()
    with ctx.store.connection() as conn:
        row = conn.execute(
            "SELECT provider, account FROM permits WHERE id=?",
            (request.access_grant_id,),
        ).fetchone()
    if row is None or not row["provider"] or not row["account"]:
        raise _grant_required()
    keys = [cls.ie_key() for cls, _approval in matched]
    hosts: list[str] = []
    for _cls, approval in matched:
        for host in approval.hosts:
            if host not in hosts:
                hosts.append(host)
    target, payload = action_parts(request, row["provider"], row["account"], keys)
    use = safety.PermitUse(
        kind="access",
        project_id=request.project_id,
        provider=row["provider"],
        account=row["account"],
        operation=PERMIT_OPERATION,
        digest=safety.action_digest(target, payload),
    )
    with ctx.store.transaction() as conn:
        safety.verify_permit(ctx.store, request.access_grant_id, use, conn=conn, reserve=False)
    limits = request.limits.model_dump(mode="json")
    limits["safe_read_retries"] = workspace.limits.safe_read_retries
    limits["read_timeout_seconds"] = workspace.limits.read_timeout_seconds
    limits["media_max_bytes"] = workspace.limits.media_max_bytes
    limits["text_max_bytes"] = workspace.limits.text_max_bytes
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
        limits=limits,
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
    allow_cookies: bool = False


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
) -> tuple[int, dict[str, str], bytes, str]:
    session_type, _websocket, curl_opt = load_curl()
    options = _options(curl_opt, checked)
    bundle = _ca_bundle()
    try:
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
    return status, _header_map(getattr(response, "headers", {})), bytes(writer.data), peer


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


def metamuse_request_handler_send(handler: Any, request: Any) -> Any:
    """Dial one checked request and follow redirects inside this handler."""

    from yt_dlp.networking.common import Response
    from yt_dlp.networking.exceptions import HTTPError

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
    if not net.allow_cookies:
        headers.pop("Cookie", None)
    url = request.url
    purpose = _purpose.get()
    # media_max follows the body. Redirect hops are still checked as "redirect".
    body_cap = net.media_max if purpose == "media" else None
    redirects = 0
    while True:
        if net.budget.remaining <= 0:
            raise _limit_error()
        checked = net.checker(url, purpose=purpose)
        limit = net.budget.remaining
        if body_cap is not None:
            limit = min(limit, body_cap)
        status, response_headers, payload, peer = _dial(
            method, checked.url, headers, body, checked, net.timeout, limit
        )
        parsed = urlsplit(checked.url)
        _observe(checked.hostname, parsed.path, peer, purpose, status, len(payload))
        if status in _REDIRECTS:
            redirects += 1
            if redirects > MAX_REDIRECTS:
                raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Too many redirects")
            target = _location(response_headers)
            if not target:
                raise m.CoreError(
                    m.ErrorCode.INPUT_INVALID, "Upstream response could not be parsed"
                )
            url = urljoin(checked.url, target)
            purpose = "redirect"
            if status in (301, 302, 303):
                method = "GET"
                body = None
            if urlsplit(url).hostname != checked.hostname:
                headers.pop("Authorization", None)
            continue
        if 200 <= status < 300:
            net.budget.take(len(payload))
            if net.state is not None:
                net.state.bytes_used = net.budget.used
        stream = io.BytesIO(payload)
        response = Response(stream, checked.url, response_headers, status=status)
        if status >= 400:
            raise HTTPError(response)
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
    for node in _chain(exc):
        if isinstance(node, m.CoreError) and core is None:
            core = node
        found = _http_status(node)
        if found is not None and http is None:
            http = found
        if node.__class__.__name__ == "GeoRestrictedError":
            geo = True
        texts.append(str(node))
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
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return value
    return None


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
        entries = info.get("entries") or []
        if not entries:
            return None
        first = next((item for item in entries if isinstance(item, dict)), None)
        if first is None:
            return None
        info = first
    native = info.get("id")
    if not isinstance(native, str) or not native:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Upstream response could not be parsed")
    title = info.get("title") if isinstance(info.get("title"), str) else None
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

    def checkpoint(self) -> dict[str, Any]:
        # get_job returns this document. Upstream media and subtitle URLs stay
        # in memory for this process and are fetched again after a resume.
        return {
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
    """Ids, revision, hashes, and counts. No upstream URL is copied."""

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
    if isinstance(title, str):
        try:
            m.utf8_text(title, text_max)
        except ValueError as exc:
            raise _limit_error() from exc
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
    state.rows.append(
        {
            "input": state.next_input,
            "status": status,
            "source_id": source_id,
            "item_id": item_id,
            "revision": revision,
        }
    )
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
                        complete=title is not None,
                        missing_fields=[] if title is not None else ["text"],
                        evidence=evidence,
                    )
                )

    _commit(step, state, write)


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
            if parsed.errors and not parsed.cues:
                if "subtitles" not in state.missing:
                    state.missing.append("subtitles")
                continue
            if not parsed.cues:
                if "subtitles" not in state.missing:
                    state.missing.append("subtitles")
                continue
            language = str(track["language"])
            method = str(track["method"])
            digest = _subtitle_sha(payload)
            native = f"{current['native_id']}:subtitle:{language}:{method}"
            basis = {
                "file_sha256": digest,
                "language": language,
                "role": "subtitles",
                "item_id": item_id,
                "asset_id": None,
                "complete": 1,
                "cue_count": len(parsed.cues),
                "method": method,
            }

            def write(
                writer: TransactionWriter,
                native: str = native,
                basis: dict[str, Any] = basis,
                digest: str = digest,
                cues: list[Any] = parsed.cues,
                language: str = language,
                method: str = method,
            ) -> None:
                found = writer.execute(
                    "SELECT id, json_extract(use_basis, '$.complete') AS complete, "
                    "json_extract(use_basis, '$.file_sha256') AS sha FROM sources "
                    "WHERE project_id=? AND platform=? AND native_scope=? AND native_id=?",
                    (project, current["platform"], current["scope"], native),
                ).fetchone()
                if found is not None and found["complete"] and found["sha"] == digest:
                    return
                if found is None:
                    source_id = m.new_id("src")
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
                    source_id = str(found["id"])
                    writer.execute(
                        "UPDATE sources SET use_basis=? WHERE id=?",
                        (m.canonical_json(basis).decode(), source_id),
                    )
                for cue in cues:
                    exists = writer.execute(
                        "SELECT 1 FROM transcript_segments WHERE source_id=? AND project_id=? "
                        "AND revision=1 AND cue_index=?",
                        (source_id, project, cue.cue_index),
                    ).fetchone()
                    if exists is not None:
                        continue
                    writer.insert(
                        m.TranscriptSegment(
                            id=m.new_id("cue"),
                            project_id=project,
                            asset_id=None,
                            item_id=item_id,
                            source_id=source_id,
                            revision=1,
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
    if not dependency_available():
        raise _dependency_error()
    request = m.CollectRequest.model_validate(step.payload)
    workspace = load_config(step.store.paths.root)
    if workspace.app.network_mode == "offline":
        raise _offline_error()
    approvals = list(workspace.remote_media.approvals)
    if not approvals:
        raise _allowlist_required()
    urls = [item.url for item in request.inputs if item.kind == "url"]
    keys = list(step.target.get("extractors") or [])
    if len(keys) != len(urls):
        raise m.CoreError(
            m.ErrorCode.REFERENCE_INVALID, "Stored extractor list does not match the inputs"
        )
    hosts: list[str] = []
    classes: list[type] = []
    for url, key in zip(urls, keys, strict=True):
        if not _still_approved(url, key, approvals):
            raise _host_denied() if _extractor_class(key) is not None else _extractor_denied()
        cls = _extractor_class(key)
        if cls is None:
            raise _extractor_denied()
        classes.append(cls)
        for approval in approvals:
            if approval.extractor == key and _host_of(url) in _hosts(approval.hosts):
                for host in approval.hosts:
                    if host not in hosts:
                        hosts.append(host)
    provider = str(step.target["provider"])
    account = str(step.target["account"])
    _target, payload = action_parts(request, provider, account, keys)
    use = safety.PermitUse(
        kind="access",
        project_id=request.project_id,
        provider=provider,
        account=account,
        operation=PERMIT_OPERATION,
        digest=safety.action_digest(step.target, payload),
    )

    def load_payload(conn: Any, target: dict[str, Any]) -> dict[str, Any]:
        del conn
        stored = m.CollectRequest.model_validate(step.job.payload)
        rebuilt, _payload = action_parts(
            stored,
            str(target["provider"]),
            str(target["account"]),
            list(target["extractors"]),
        )
        del rebuilt
        return _payload

    step.before_external_action(
        step.target,
        verify=safety.external_action_verifier(
            step,
            permit_id=str(request.access_grant_id),
            use=use,
            load_payload=load_payload,
        ),
    )
    state = _State.load(step.checkpoint or {})
    resources = set(request.resources)
    text_max = int(step.job.limits.get("text_max_bytes") or workspace.limits.text_max_bytes)
    media_max = int(step.job.limits.get("media_max_bytes") or workspace.limits.media_max_bytes)
    timeout = int(
        step.job.limits.get("read_timeout_seconds") or workspace.limits.read_timeout_seconds
    )
    budget = _Budget(used=state.bytes_used, limit=request.limits.max_bytes)
    net = _Net(
        checker=_checker_factory(tuple(hosts), _system_resolver),
        budget=budget,
        media_max=media_max,
        timeout=float(timeout),
        state=state,
    )
    staging = _claim_staging(step.store, step.job.id)
    _sweep(step.store, step.job.id, remove_current=False)
    try:
        while state.next_input < len(urls) and state.halt is None:
            step.guard()
            index = state.next_input
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
                    )
                    if state.halt:
                        break
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
                    )
                    if state.halt:
                        break
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
    state.bytes_used = budget.used
    return _result(step, state, resources)


def _still_approved(url: str, key: str, approvals: list[Any]) -> bool:
    if key.lower() in REJECTED_EXTRACTORS:
        return False
    host = _host_of(url)
    cls = _extractor_class(key)
    if cls is None or not cls.suitable(url):
        return False
    return any(
        approval.extractor == key and host in _hosts(approval.hosts) for approval in approvals
    )


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
) -> dict[str, Any] | None:
    """Fetch one page. None means this input stopped; the body still counts."""

    params = ytdlp_params(
        staging,
        max_items=max_items,
        socket_timeout=timeout,
        max_filesize=media_max,
        extractor=key,
    )
    ydl = _open_ydl(params, ie_cls, net)
    try:
        try:
            info = ydl.extract_info(url, download=False)
        except m.CoreError:
            raise
        except Exception as exc:
            kind, value = _classify(exc)
            if kind == "empty":
                state.halt = "empty"
                return None
            if kind == "rate":
                state.halt = "rate_limited"
                state.retry_after = value
                state.code = m.ErrorCode.RATE_LIMITED.value
                state.message = "Upstream rate limit"
                return None
            if kind == "core":
                raise value from exc
            raise value from exc
    finally:
        ydl.close()
    if not isinstance(info, dict):
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Upstream response could not be parsed")
    record = _normalize(info, url, key, max_items)
    if record is None:
        state.halt = "empty"
        return None
    if str(record["extractor"]).lower() == "manual":
        raise m.CoreError(m.ErrorCode.UNSUPPORTED, "Extractor failed")
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
) -> None:
    """Re-read the page after a resume. Signed URLs are not stored on the step."""

    if state.current is None or state.current.get("urls_ready"):
        return
    record = _read_info(state, url, ie_cls, key, net, staging, max_items, timeout, media_max)
    if record is None:
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
) -> None:
    if (
        state.rows
        and len([row for row in state.rows if row.get("status") in ("committed", "unchanged")])
        >= max_items
    ):
        state.halt = "limit_reached"
        state.code = m.ErrorCode.LIMIT_REACHED.value
        return
    record = _read_info(state, url, ie_cls, key, net, staging, max_items, timeout, media_max)
    if record is None:
        return
    _store_metadata(step, state, record, url, text_max)
    state.bytes_used = net.budget.used
