"""Bounded Facebook single-post permalink collection (B13).

Scope is strictly one approved page type (`post_permalink`): a single post
permalink with the post body, top-level comments, and their replies. Profile,
group, feed, watch, reel, story, and search URLs stay `E_UNSUPPORTED`.

Browser egress boundary (AT-030, FR-019):
- Playwright's `context.route` only intercepts the initial hop of a redirect chain
  and allows Chromium to resolve DNS and follow 3xx hops internally.
- All Chromium traffic is therefore routed through `BrowserEgressProxy`
  (`src/metamuse/_browser_egress.py`) bound to `127.0.0.1:0` with Chromium's own
  DNS resolver disabled (`--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1`),
  QUIC disabled (`--disable-quic`), and non-proxied WebRTC UDP disabled.
- The local egress proxy is the true network egress boundary: it checks every
  `CONNECT` tunnel and plain-HTTP request (including redirect hops) against B08
  `OutboundURLPolicy`, dials only the checked IP address without re-resolving,
  verifies the connected socket peer, and enforces the job `max_bytes` budget.
- `context.route` is retained only for browser resource-type blocking
  (`websocket`, `eventsource`, `manifest`, `service_worker`) and the initial
  navigation page-type check, and `Response.server_addr()` is retained as a
  secondary check.

Third-party provenance:
- Post field checklist (`actor`, `creation_time`, message text, post id,
  permalink, reaction/comment/share counts, attachment URLs) references
  `tlyu0419/facebook_crawler` commit `82ec3ec46aa0a324252bbb6274b57cbf29c27e6e`,
  original path `paser.py` (`_parse_edge`), under the MIT License:
  Copyright (c) 2021 tlyu0419. Full permission notice in `THIRD_PARTY_NOTICES.md`.
  No crawler loop, retry-and-change-IP flow, or HTTP code was copied.
- `boo283/Facebook_comment_crawler` has no license and was neither copied nor
  mechanically translated.
"""

from __future__ import annotations

import hashlib
import html as html_lib
import ipaddress
import json
import os
import re
import socket
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import parse_qs, urlsplit

from metamuse import collect, jobs, safety
from metamuse import models as m
from metamuse._browser_egress import (
    BrowserEgressProxy,
    build_chromium_launch_args,
)
from metamuse.jobs import StepContext, StepSpec, submit_job
from metamuse.store import TransactionWriter

OPERATION = "collect.facebook_post"
PAGE_TYPE_POST_PERMALINK = "post_permalink"
COLLECTION_METHOD = "browser_playwright"
UPSTREAM_METHOD = "facebook_post_permalink"
BATCH_SIZE = 50

PRIMARY_FACEBOOK_HOSTS: frozenset[str] = frozenset(
    {
        "www.facebook.com",
        "facebook.com",
        "m.facebook.com",
        "web.facebook.com",
    }
)
ALL_FACEBOOK_HOSTS: frozenset[str] = PRIMARY_FACEBOOK_HOSTS | frozenset(
    {
        "mbasic.facebook.com",
        "fb.com",
        "www.fb.com",
    }
)

RESERVED_ACTORS: frozenset[str] = frozenset(
    {
        "ads",
        "bookmarks",
        "challenge",
        "checkpoint",
        "events",
        "friends",
        "gaming",
        "groups",
        "hashtag",
        "help",
        "login",
        "login.php",
        "marketplace",
        "memories",
        "messages",
        "notifications",
        "pages",
        "permalink.php",
        "photo",
        "photo.php",
        "photos",
        "policies",
        "privacy",
        "profile.php",
        "reel",
        "reels",
        "saved",
        "search",
        "settings",
        "share",
        "sharer",
        "sharer.php",
        "stories",
        "story.php",
        "video",
        "video.php",
        "videos",
        "watch",
    }
)

PROXY_ENV_VARS: tuple[str, ...] = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)

_ACTOR_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_POST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@dataclass(frozen=True)
class PostPermalinkTarget:
    original_url: str
    canonical_url: str
    fetch_url: str
    host: str
    actor: str
    post_id: str
    scope: str
    page_type: Literal["post_permalink"] = "post_permalink"


@dataclass(frozen=True)
class ExpandControl:
    control_id: str
    kind: Literal["more_comments", "more_replies"]
    parent_comment_id: str | None = None
    remaining_hint: int | None = None


@dataclass(frozen=True)
class ParsedComment:
    kind: Literal["comment", "reply"]
    native_id: str | None
    parent_native_id: str | None
    author_display: str | None
    text: str | None
    created_at_raw: str | None
    parsed_time: str | None
    permalink: str | None = None
    mention_tag: str | None = None


@dataclass(frozen=True)
class ParsedPostPage:
    post_id: str
    actor: str | None
    author_display: str | None
    text: str | None
    created_at_raw: str | None
    parsed_time: str | None
    permalink: str | None
    reaction_count: int | None
    comment_count: int | None
    share_count: int | None
    attachments: list[dict[str, str]]
    comments: list[ParsedComment]
    expand_controls: list[ExpandControl]
    comments_section_present: bool


@dataclass(frozen=True)
class SubresourceRequest:
    url: str
    resource_type: str = "subresource"
    is_navigation: bool = False
    redirected_from: str | None = None
    server_addr: dict[str, Any] | None = None


@dataclass(frozen=True)
class PageObservation:
    url: str
    status_code: int
    html: str
    server_addr: dict[str, Any] | None = None
    subresources: tuple[SubresourceRequest, ...] = ()
    redirect_chain: tuple[str, ...] = ()


@dataclass(frozen=True)
class EgressDecision:
    hostname: str
    purpose: str
    resource_type: str
    allowed: bool
    reason: str
    peer_ip: str | None = None
    peer_verified: bool = False


class SanitizedStorageState:
    """Operator storage state filtered strictly by parsed hostname.

    Never exposes cookies, localStorage values, or raw bytes via repr/str.
    """

    __slots__ = ("_cookies", "_origins", "_secret_tokens", "cookie_hosts", "dropped_cookie_domains")

    def __init__(
        self,
        *,
        cookies: list[dict[str, Any]],
        origins: list[dict[str, Any]],
        secret_tokens: tuple[str, ...],
        cookie_hosts: tuple[str, ...],
        dropped_cookie_domains: tuple[str, ...],
    ) -> None:
        self._cookies = cookies
        self._origins = origins
        self._secret_tokens = secret_tokens
        self.cookie_hosts = cookie_hosts
        self.dropped_cookie_domains = dropped_cookie_domains

    def __repr__(self) -> str:
        return "<SanitizedStorageState redacted>"

    def __str__(self) -> str:
        return "<SanitizedStorageState redacted>"

    def as_playwright_dict(self) -> dict[str, Any]:
        return {
            "cookies": [dict(c) for c in self._cookies],
            "origins": [
                {
                    "origin": o["origin"],
                    "localStorage": [dict(item) for item in o.get("localStorage", [])],
                }
                for o in self._origins
            ],
        }

    def redact(self, text: str) -> str:
        cleaned, _ = safety.redact_text(text)
        for token in self._secret_tokens:
            if token and len(token) >= 4 and token in cleaned:
                cleaned = cleaned.replace(token, "[REDACTED]")
        return cleaned


class BrowserSession(Protocol):
    def goto_post(self, url: str) -> PageObservation: ...

    def expand_next(self, control_id: str) -> PageObservation: ...

    def close(self) -> None: ...


class BrowserDriver(Protocol):
    def is_available(self) -> bool: ...

    def open_session(
        self,
        storage_state: SanitizedStorageState,
        egress: EgressGuard,
        *,
        timeout_ms: int,
        max_bytes: int | None = None,
    ) -> BrowserSession: ...


# Test/integration seams (replaceable in Python tests only; unreachable from config, CLI, MCP)
_DRIVER_FACTORY: Callable[[], BrowserDriver] | None = None
_SECRET_STORE_FACTORY: Callable[[], safety.SecretStore] | None = None
_RESOLVER_FACTORY: Callable[[], safety.Resolver] | None = None
_EGRESS_FACTORY: Callable[[Iterable[str]], EgressGuard] | None = None
_NOTE_BATCH: Callable[[StepContext], None] | None = None


def set_driver_factory(factory: Callable[[], BrowserDriver] | None) -> None:
    global _DRIVER_FACTORY
    _DRIVER_FACTORY = factory


def set_secret_store_factory(factory: Callable[[], safety.SecretStore] | None) -> None:
    global _SECRET_STORE_FACTORY
    _SECRET_STORE_FACTORY = factory


def set_resolver_factory(factory: Callable[[], safety.Resolver] | None) -> None:
    global _RESOLVER_FACTORY
    _RESOLVER_FACTORY = factory


def set_egress_factory(factory: Callable[[Iterable[str]], EgressGuard] | None) -> None:
    global _EGRESS_FACTORY
    _EGRESS_FACTORY = factory


def set_batch_hook(hook: Callable[[StepContext], None] | None) -> None:
    global _NOTE_BATCH
    _NOTE_BATCH = hook


def _default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    addrs: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr and isinstance(sockaddr[0], str) and sockaddr[0] not in addrs:
            addrs.append(sockaddr[0])
    return addrs


def _active_resolver() -> safety.Resolver:
    if _RESOLVER_FACTORY is not None:
        return _RESOLVER_FACTORY()
    return _default_resolver


def _active_secret_store() -> safety.SecretStore:
    if _SECRET_STORE_FACTORY is not None:
        return _SECRET_STORE_FACTORY()
    return safety.SecretStore()


def _active_egress(allowed_hosts: Iterable[str]) -> EgressGuard:
    if _EGRESS_FACTORY is not None:
        return _EGRESS_FACTORY(allowed_hosts)
    return EgressGuard(allowed_hosts=allowed_hosts, resolver=_active_resolver())


def find_playwright_browser() -> Path | None:
    """Return an installed Playwright Chromium executable without downloading or launching."""
    try:
        import playwright  # lazy import of optional extra
    except ImportError:
        return None
    pkg_file = getattr(playwright, "__file__", None)
    if not pkg_file:
        return None
    override = os.environ.get("METAMUSE_CHROMIUM_EXECUTABLE")
    if override:
        candidate = Path(override)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
        return None
    pkg_dir = Path(pkg_file).resolve().parent
    browsers_json = pkg_dir / "driver" / "package" / "browsers.json"
    if not browsers_json.is_file():
        return None
    try:
        manifest = json.loads(browsers_json.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    revisions: dict[str, str] = {}
    for entry in manifest.get("browsers", []):
        if isinstance(entry, Mapping):
            name = entry.get("name")
            rev = entry.get("revision")
            if isinstance(name, str) and isinstance(rev, str):
                revisions[name] = rev
    browsers_path_env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browsers_path_env == "0":
        cache_root = pkg_dir / "driver" / "package" / ".local-browsers"
    elif browsers_path_env:
        cache_root = Path(browsers_path_env).expanduser()
    else:
        cache_root = Path.home() / ".cache" / "ms-playwright"
    if not cache_root.is_dir():
        return None
    candidates: list[Path] = []
    if "chromium-headless-shell" in revisions:
        rev = revisions["chromium-headless-shell"]
        candidates.extend(
            [
                cache_root
                / f"chromium_headless_shell-{rev}"
                / "chrome-headless-shell-linux64"
                / "chrome-headless-shell",
                cache_root / f"chromium_headless_shell-{rev}" / "chrome-linux" / "headless_shell",
            ]
        )
    if "chromium" in revisions:
        rev = revisions["chromium"]
        candidates.extend(
            [
                cache_root / f"chromium-{rev}" / "chrome-linux64" / "chrome",
                cache_root / f"chromium-{rev}" / "chrome-linux" / "chrome",
            ]
        )
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def is_browser_available() -> bool:
    if _DRIVER_FACTORY is not None:
        return _DRIVER_FACTORY().is_available()
    return find_playwright_browser() is not None


def ensure_browser_available() -> None:
    if not is_browser_available():
        raise m.CoreError(
            m.ErrorCode.DEPENDENCY_MISSING,
            "Playwright or the Chromium browser binary is not installed",
            next_action=(
                "Install the optional facebook extra (uv sync --extra facebook) "
                "and install the Chromium binary as an operator step"
            ),
        )


def _has_valid_storage_state(fb_cfg: Any) -> bool:
    if fb_cfg is None:
        return False
    auth_ref = getattr(fb_cfg, "auth_ref", None)
    account = getattr(fb_cfg, "account", None)
    if not auth_ref or not account:
        return False
    try:
        load_storage_state(auth_ref, account=account)
    except m.CoreError:
        return False
    return True


def capability_status(ctx: Any) -> dict[str, dict[str, str]]:
    """Report the metamuse_collect.facebook_post backend capability row."""
    fb_cfg = getattr(getattr(ctx, "policy", None), "facebook", None)
    net_mode = getattr(
        getattr(getattr(ctx, "policy", None), "app", None), "network_mode", "offline"
    )
    if (
        fb_cfg is not None
        and getattr(fb_cfg, "enabled", False)
        and PAGE_TYPE_POST_PERMALINK in getattr(fb_cfg, "approved_page_types", ())
        and net_mode == "live"
    ):
        permission = "granted_scope"
    else:
        permission = "not_configured"

    if not is_browser_available():
        environment = "missing_dependency"
    elif not _has_valid_storage_state(fb_cfg):
        environment = "needs_auth"
    else:
        environment = "ready"

    return {
        "metamuse_collect.facebook_post": {
            "verification": "fixture_passed",
            "environment": environment,
            "permission": permission,
        }
    }


def register_operations(registry: dict[str, jobs.Operation] | None = None) -> None:
    target = jobs.OPERATIONS if registry is None else registry
    if OPERATION in target:
        return
    jobs.register_operation(
        OPERATION,
        run_facebook_post,
        "safe_read",
        request_model=m.CollectRequest,
        registry=target,
    )


def _normalize_host(raw_host: str | None) -> str | None:
    if not raw_host or not isinstance(raw_host, str):
        return None
    cleaned = raw_host.strip().rstrip(".").lower()
    if not cleaned or any(ch in cleaned for ch in "%\\/@[] \t\r\n"):
        return None
    if ":" in cleaned:
        try:
            return str(ipaddress.ip_address(cleaned))
        except ValueError:
            return None
    try:
        ascii_host = cleaned.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if len(ascii_host) > 253:
        return None
    for label in ascii_host.split("."):
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label):
            return None
    return ascii_host


def _is_forbidden_local_or_ip_host(host: str) -> bool:
    if host in ("localhost", "localhost.localdomain") or host.endswith(
        (".localhost", ".local", ".internal")
    ):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or True  # Raw IP literals are never valid Facebook hosts
    )


def is_facebook_candidate_url(url: str) -> bool:
    """Return True only when the URL's parsed hostname is a Facebook domain."""
    if not isinstance(url, str) or "\\" in url or any(c.isspace() for c in url):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = _normalize_host(parsed.hostname)
    if host is None or _is_forbidden_local_or_ip_host(host):
        return False
    return (
        host in ALL_FACEBOOK_HOSTS or host.endswith(".facebook.com") or host.endswith(".fbcdn.net")
    )


def is_facebook_post_request(request: m.CollectRequest) -> bool:
    """Narrow dispatch predicate for collect.submit_collect.

    Routes to Facebook only when every input is a URL whose parsed host is a
    Facebook host and the requested resources are post/comment text. Media-only
    resources (media_metadata, subtitles, media_file) belong to B12.
    """
    if not request.inputs or not request.resources:
        return False
    if not set(request.resources) <= {"post", "comments"}:
        return False
    return all(
        item.kind == "url" and is_facebook_candidate_url(item.url) for item in request.inputs
    )


def parse_post_permalink(url: str) -> PostPermalinkTarget:
    """Validate and canonicalize a single Facebook post permalink URL.

    Refuses loopback/private/IP hosts and non-HTTPS schemes with E_SCOPE_DENIED,
    and raises E_UNSUPPORTED for any non-approved Facebook URL shape (profiles,
    groups, reels, watch, stories, photos, search, feeds, or non-permalink paths).
    """
    if (
        not isinstance(url, str)
        or len(url.encode("utf-8")) > safety.URL_MAX_BYTES
        or "\\" in url
        or any(c.isspace() for c in url)
    ):
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID,
            "Invalid Facebook post URL",
            next_action="Supply a valid single Facebook post permalink URL",
        )
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    host = _normalize_host(parsed.hostname)
    if (
        host is None
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or _is_forbidden_local_or_ip_host(host)
    ):
        raise m.CoreError(
            m.ErrorCode.SCOPE_DENIED,
            "Outbound URL is outside the network policy",
            next_action=(
                "Use an approved HTTPS Facebook post permalink without credentials "
                "or local/private hosts"
            ),
        )
    if scheme != "https":
        raise m.CoreError(
            m.ErrorCode.SCOPE_DENIED,
            "Facebook collection requires HTTPS",
            next_action="Use an https:// Facebook post permalink URL",
        )
    if host not in PRIMARY_FACEBOOK_HOSTS:
        if is_facebook_candidate_url(url):
            raise m.CoreError(
                m.ErrorCode.UNSUPPORTED,
                "Only primary Facebook post permalinks (post_permalink) are supported in B13",
                next_action=(
                    "Provide a single post permalink on www.facebook.com "
                    "(/<actor>/posts/<post_id> or /permalink.php?story_fbid=...&id=...)"
                ),
            )
        raise m.CoreError(
            m.ErrorCode.UNSUPPORTED,
            "Remote collection is not implemented for this host",
            next_action=collect.REMOTE_NEXT,
        )

    segments = [seg for seg in parsed.path.split("/") if seg]
    actor: str | None = None
    post_id: str | None = None

    if len(segments) == 3 and segments[1] == "posts":
        candidate_actor, _, candidate_post = segments
        if candidate_actor.lower() in RESERVED_ACTORS:
            raise m.CoreError(
                m.ErrorCode.UNSUPPORTED,
                f"Facebook /{candidate_actor}/ URLs are not an approved page type",
                next_action="Use a single post permalink (/<actor>/posts/<post_id>)",
            )
        if _ACTOR_RE.fullmatch(candidate_actor) and _POST_ID_RE.fullmatch(candidate_post):
            if not parsed.query:
                actor = candidate_actor
                post_id = candidate_post
    elif len(segments) == 1 and segments[0] in ("permalink.php", "story.php"):
        params = parse_qs(parsed.query, keep_blank_values=True)
        if set(params.keys()) == {"story_fbid", "id"}:
            fbids = params.get("story_fbid", [])
            ids = params.get("id", [])
            if (
                len(fbids) == 1
                and len(ids) == 1
                and _POST_ID_RE.fullmatch(fbids[0])
                and _ACTOR_RE.fullmatch(ids[0])
                and ids[0].lower() not in RESERVED_ACTORS
            ):
                actor = ids[0]
                post_id = fbids[0]

    if actor is None or post_id is None:
        raise m.CoreError(
            m.ErrorCode.UNSUPPORTED,
            "URL is not an approved Facebook post_permalink page type",
            next_action=(
                "B13 supports only a single post permalink "
                "(/<actor>/posts/<post_id> or /permalink.php?story_fbid=<id>&id=<actor>)"
            ),
        )

    canonical_url = f"https://www.facebook.com/{actor}/posts/{post_id}"
    return PostPermalinkTarget(
        original_url=url,
        canonical_url=canonical_url,
        fetch_url=canonical_url,
        host=host,
        actor=actor,
        post_id=post_id,
        scope=f"{actor}/{post_id}",
    )


def build_target(
    *,
    project_id: str,
    account: str,
    url: str,
    page_type: str = PAGE_TYPE_POST_PERMALINK,
) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "provider": "facebook",
        "account": account,
        "operation": "collect.remote",
        "page_type": page_type,
        "url": url,
    }


def build_action_payload(
    *,
    url: str,
    resources: Iterable[str],
) -> dict[str, Any]:
    return {
        "url": url,
        "resources": sorted(set(resources)),
    }


def permit_digest(
    *,
    project_id: str,
    account: str,
    url: str,
    resources: Iterable[str] = ("post", "comments"),
) -> str:
    """Compute the exact B08 action_digest for a single Facebook post permalink read."""
    parsed = parse_post_permalink(url)
    target = build_target(project_id=project_id, account=account, url=parsed.canonical_url)
    payload = build_action_payload(url=parsed.canonical_url, resources=resources)
    return safety.action_digest(target, payload)


def sanitize_storage_state(
    raw_bytes: bytes,
    *,
    allowed_cookie_hosts: Iterable[str] = PRIMARY_FACEBOOK_HOSTS,
) -> SanitizedStorageState:
    """Parse and filter a Playwright storage state by parsed hostname only.

    Never performs substring matching on domain strings. Drops cookies and
    origins for CDN hosts, lookalike domains, and third-party sites.
    """
    allowed_hosts: set[str] = set()
    for candidate in allowed_cookie_hosts:
        norm = _normalize_host(candidate)
        if norm is not None and norm in PRIMARY_FACEBOOK_HOSTS:
            allowed_hosts.add(norm)
    if not allowed_hosts:
        raise m.CoreError(
            m.ErrorCode.AUTH_REQUIRED,
            "No approved cookie hosts configured for Facebook storage state",
            next_action="Configure approved Facebook hosts before importing storage state",
        )
    try:
        decoded = raw_bytes.decode("utf-8")
        data = json.loads(decoded)
    except (UnicodeDecodeError, ValueError, TypeError):
        raise m.CoreError(
            m.ErrorCode.AUTH_REQUIRED,
            "Stored Facebook authentication state is not valid JSON",
            next_action=(
                "Re-import a valid Playwright storage_state JSON file with metamuse auth import"
            ),
        ) from None
    if not isinstance(data, Mapping):
        raise m.CoreError(
            m.ErrorCode.AUTH_REQUIRED,
            "Stored Facebook authentication state must be a JSON object",
            next_action=(
                "Re-import a valid Playwright storage_state JSON file with metamuse auth import"
            ),
        )

    raw_cookies = data.get("cookies", [])
    raw_origins = data.get("origins", [])
    if not isinstance(raw_cookies, list) or not isinstance(raw_origins, list):
        raise m.CoreError(
            m.ErrorCode.AUTH_REQUIRED,
            "Stored Facebook authentication state has invalid cookies or origins structure",
            next_action=(
                "Re-import a valid Playwright storage_state JSON file with metamuse auth import"
            ),
        )

    kept_cookies: list[dict[str, Any]] = []
    dropped_domains: list[str] = []
    matched_hosts: set[str] = set()
    secret_tokens: list[str] = []

    for entry in raw_cookies:
        if not isinstance(entry, Mapping):
            raise m.CoreError(
                m.ErrorCode.AUTH_REQUIRED,
                "Stored cookie entry is invalid",
                next_action="Re-import a valid Playwright storage_state JSON file",
            )
        name = entry.get("name")
        value = entry.get("value")
        if not isinstance(name, str) or not name or not isinstance(value, str):
            raise m.CoreError(
                m.ErrorCode.AUTH_REQUIRED,
                "Stored cookie entry is missing name or value",
                next_action="Re-import a valid Playwright storage_state JSON file",
            )
        secret_tokens.append(value)

        cookie_url = entry.get("url")
        cookie_domain = entry.get("domain")
        parsed_host: str | None = None
        if isinstance(cookie_url, str) and cookie_url:
            try:
                split_u = urlsplit(cookie_url)
            except ValueError:
                split_u = None
            if split_u is not None and split_u.hostname:
                parsed_host = _normalize_host(split_u.hostname)
        elif isinstance(cookie_domain, str) and cookie_domain:
            stripped = cookie_domain[1:] if cookie_domain.startswith(".") else cookie_domain
            if not stripped.startswith("."):
                parsed_host = _normalize_host(stripped)

        if parsed_host is None or parsed_host not in allowed_hosts:
            dropped_domains.append(parsed_host or "invalid")
            continue

        # If cookie was scoped to bare "facebook.com", emit exact host-bound cookies for the
        # approved primary web hosts only, never wildcard subdomains.
        target_hosts = (
            sorted(allowed_hosts & PRIMARY_FACEBOOK_HOSTS)
            if parsed_host == "facebook.com"
            else [parsed_host]
        )
        for exact_host in target_hosts:
            sanitized_cookie: dict[str, Any] = {
                "name": name,
                "value": value,
                "domain": exact_host,
                "path": str(entry.get("path") or "/"),
                "httpOnly": bool(entry.get("httpOnly", True)),
                "secure": True,
            }
            if isinstance(entry.get("expires"), (int, float)):
                sanitized_cookie["expires"] = float(entry["expires"])
            same_site = entry.get("sameSite")
            if same_site in ("Strict", "Lax", "None"):
                sanitized_cookie["sameSite"] = same_site
            kept_cookies.append(sanitized_cookie)
            matched_hosts.add(exact_host)

    kept_origins: list[dict[str, Any]] = []
    for origin_entry in raw_origins:
        if not isinstance(origin_entry, Mapping):
            continue
        raw_origin = origin_entry.get("origin")
        if not isinstance(raw_origin, str):
            continue
        try:
            split_origin = urlsplit(raw_origin)
        except ValueError:
            continue
        origin_scheme = split_origin.scheme.lower()
        origin_host = _normalize_host(split_origin.hostname)
        if origin_host is None or origin_host not in allowed_hosts:
            continue
        if origin_scheme != "https":
            continue
        local_storage = origin_entry.get("localStorage", [])
        clean_ls: list[dict[str, str]] = []
        if isinstance(local_storage, list):
            for item in local_storage:
                if isinstance(item, Mapping):
                    k = item.get("name")
                    v = item.get("value")
                    if isinstance(k, str) and isinstance(v, str):
                        secret_tokens.append(v)
                        clean_ls.append({"name": k, "value": v})
        port_sfx = f":{split_origin.port}" if split_origin.port else ""
        kept_origins.append(
            {
                "origin": f"{origin_scheme}://{origin_host}{port_sfx}",
                "localStorage": clean_ls,
            }
        )
        matched_hosts.add(origin_host)

    if not kept_cookies:
        raise m.CoreError(
            m.ErrorCode.AUTH_REQUIRED,
            "Stored Facebook storage state contains no cookies for approved Facebook hosts",
            next_action=(
                "Import a Playwright storage state containing valid Facebook session cookies"
            ),
        )

    return SanitizedStorageState(
        cookies=kept_cookies,
        origins=kept_origins,
        secret_tokens=tuple(secret_tokens),
        cookie_hosts=tuple(sorted(matched_hosts)),
        dropped_cookie_domains=tuple(dropped_domains),
    )


def load_storage_state(
    auth_ref: str,
    *,
    account: str,
    secret_store: safety.SecretStore | None = None,
) -> SanitizedStorageState:
    raw = safety.resolve_secret(
        auth_ref,
        provider="facebook",
        account=account,
        secret_store=secret_store or _active_secret_store(),
    )
    return sanitize_storage_state(
        raw,
        allowed_cookie_hosts=PRIMARY_FACEBOOK_HOSTS,
    )


def _normalize_allowed_hosts(configured_hosts: Iterable[str]) -> tuple[str, ...]:
    from metamuse.config import validate_facebook_hosts

    return tuple(validate_facebook_hosts(list(configured_hosts)))


class EgressGuard:
    """Egress validator backing BrowserEgressProxy and secondary browser checks (AT-030, FR-019).

    The local BrowserEgressProxy is the true network egress boundary: it checks every CONNECT
    tunnel and plain-HTTP request (including every 3xx redirect hop) via check_proxy_target,
    dials only the first checked IP address without re-resolving, and verifies the connected
    peer via verify_proxy_peer. Production always uses B08 OutboundURLPolicy (check_url) and
    the real resolver.
    """

    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str],
        resolver: safety.Resolver,
        _fixture_url_checker: Callable[[str, safety.URLPurpose], safety.CheckedURL] | None = None,
        _fixture_nav_validator: Callable[[str], None] | None = None,
    ) -> None:
        self.allowed_hosts = (
            tuple(allowed_hosts)
            if _fixture_url_checker is not None
            else _normalize_allowed_hosts(allowed_hosts)
        )
        self.resolver = resolver
        self.policy = safety.OutboundURLPolicy(
            resolver=resolver,
            allowed_hosts=self.allowed_hosts,
        )
        self._fixture_url_checker = _fixture_url_checker
        self._fixture_nav_validator = _fixture_nav_validator
        self._lock = threading.Lock()
        self.decisions: list[EgressDecision] = []
        self.checked_by_url: dict[str, safety.CheckedURL] = {}
        self.checked_by_authority: dict[tuple[str, int], safety.CheckedURL] = {}
        self.verified_peers: dict[tuple[str, int], str] = {}
        self.aborted_urls: dict[str, m.CoreError] = {}
        self.last_proxy_error: m.CoreError | None = None
        self.navigation_error: m.CoreError | None = None
        self.proxy_port: int | None = None
        self.bytes_relayed: int = 0
        self.byte_limit_exceeded: bool = False
        self.authorized_permalink: PostPermalinkTarget | None = None

    def authorize_permalink(self, target: PostPermalinkTarget) -> None:
        self.authorized_permalink = target

    def validate_navigation_identity(self, url: str) -> None:
        target = self.authorized_permalink
        if target is None:
            return
        parsed = urlsplit(url)
        # Local origins exist solely in the injected fixture egress seam.
        if self._fixture_nav_validator is not None:
            url = "https://www.facebook.com" + parsed.path
            if parsed.query:
                url += "?" + parsed.query
        observed = parse_post_permalink(url)
        if (observed.actor, observed.post_id) != (target.actor, target.post_id):
            raise m.CoreError(
                m.ErrorCode.REFERENCE_INVALID,
                "Facebook navigation changed the authorized post identity",
                next_action="Approve a separate permalink and permit for the other post",
            )

    @classmethod
    def for_fixture_test(
        cls,
        *,
        allowed_hosts: Iterable[str],
        resolver: safety.Resolver,
    ) -> EgressGuard:
        """Test-only constructor wiring B08 check_fixture_url for local synthetic servers."""
        host_tuple = tuple(
            h.strip().lower() for h in allowed_hosts if isinstance(h, str) and h.strip()
        )

        def _checker(url: str, purpose: safety.URLPurpose) -> safety.CheckedURL:
            return safety.check_fixture_url(
                url,
                purpose=purpose,
                resolver=resolver,
                allowed_hosts=host_tuple,
            )

        def _nav_validator(url: str) -> None:
            parsed = urlsplit(url)
            segs = [s for s in parsed.path.split("/") if s]
            if not (len(segs) == 3 and segs[1] == "posts" and segs[0] not in RESERVED_ACTORS):
                raise m.CoreError(
                    m.ErrorCode.UNSUPPORTED,
                    "URL is not an approved Facebook post_permalink page type",
                )

        guard = cls(
            allowed_hosts=allowed_hosts,
            resolver=resolver,
            _fixture_url_checker=_checker,
            _fixture_nav_validator=_nav_validator,
        )
        guard.allowed_hosts = host_tuple
        return guard

    def _classify_navigation_barrier(self, url: str) -> m.CoreError | None:
        try:
            parsed = urlsplit(url)
        except ValueError:
            return None
        path_lower = parsed.path.lower()
        segments = [seg.lower() for seg in path_lower.split("/") if seg]
        if (
            any(seg in ("checkpoint", "challenge") for seg in segments)
            or "checkpoint" in path_lower
        ):
            return m.CoreError(
                m.ErrorCode.CHECKPOINT_REQUIRED,
                "Facebook redirected to a security checkpoint or challenge",
                next_action=(
                    "Complete the account checkpoint outside MetaMuse and import a fresh "
                    "storage state"
                ),
            )
        if any(seg in ("login", "login.php") for seg in segments):
            return m.CoreError(
                m.ErrorCode.AUTH_REQUIRED,
                "Facebook redirected to a login wall",
                next_action=(
                    "Log in outside MetaMuse, export Playwright storage_state, and run "
                    "metamuse auth import"
                ),
            )
        return None

    def check_browser_route(
        self,
        url: str,
        *,
        resource_type: str = "document",
        is_navigation: bool = False,
        redirected_from: str | None = None,
    ) -> None:
        """Secondary browser-level check for context.route (resource-type & initial nav shape).

        Does not perform DNS resolution; BrowserEgressProxy performs the authoritative
        OutboundURLPolicy check and pinned dial for every hop.
        """
        rtype = (resource_type or "other").lower()
        if is_navigation:
            purpose: safety.URLPurpose = "redirect" if redirected_from else "request"
        elif rtype in ("image", "media", "font"):
            purpose = "media"
        else:
            purpose = "subresource"

        host_guess = "invalid"
        try:
            host_guess = _normalize_host(urlsplit(url).hostname) or "invalid"
        except ValueError:
            pass

        if rtype in ("websocket", "eventsource", "manifest", "service_worker"):
            with self._lock:
                self.decisions.append(
                    EgressDecision(
                        hostname=host_guess,
                        purpose=purpose,
                        resource_type=rtype,
                        allowed=False,
                        reason=f"blocked_resource_type:{rtype}",
                    )
                )
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                f"Browser resource type {rtype} is blocked by egress policy",
            )

        if is_navigation:
            barrier = self._classify_navigation_barrier(url)
            if barrier is not None:
                with self._lock:
                    self.navigation_error = barrier
                    self.decisions.append(
                        EgressDecision(
                            hostname=host_guess,
                            purpose=purpose,
                            resource_type=rtype,
                            allowed=False,
                            reason=f"navigation_barrier:{barrier.detail.code.value}",
                        )
                    )
                raise barrier
            try:
                if self._fixture_nav_validator is not None:
                    self._fixture_nav_validator(url)
                else:
                    parse_post_permalink(url)
                self.validate_navigation_identity(url)
            except m.CoreError as exc:
                with self._lock:
                    self.navigation_error = exc
                    self.decisions.append(
                        EgressDecision(
                            hostname=host_guess,
                            purpose=purpose,
                            resource_type=rtype,
                            allowed=False,
                            reason=f"navigation_unapproved:{exc.detail.code.value}",
                        )
                    )
                raise

    def record_proxy_protocol_refusal(self, *, hostname: str, reason: str) -> None:
        exc = m.CoreError(
            m.ErrorCode.SCOPE_DENIED,
            "Proxy refused request",
        )
        with self._lock:
            self.last_proxy_error = exc
            self.decisions.append(
                EgressDecision(
                    hostname=hostname,
                    purpose="request",
                    resource_type="proxy",
                    allowed=False,
                    reason=f"proxy_refused:{reason}",
                )
            )

    def mark_byte_limit_exceeded(self) -> None:
        with self._lock:
            self.byte_limit_exceeded = True
            self.decisions.append(
                EgressDecision(
                    hostname="proxy",
                    purpose="request",
                    resource_type="proxy",
                    allowed=False,
                    reason="max_bytes_exceeded",
                )
            )

    def check_proxy_target(
        self,
        url: str,
        *,
        method: str,
        is_connect: bool,
        purpose: safety.URLPurpose = "request",
        is_navigation: bool = False,
    ) -> safety.CheckedURL:
        """Authoritative proxy egress check run before opening any upstream socket."""
        del method
        rtype = "connect" if is_connect else ("document" if is_navigation else "subresource")
        host_guess = "invalid"
        try:
            host_guess = _normalize_host(urlsplit(url).hostname) or "invalid"
        except ValueError:
            pass

        try:
            if self._fixture_url_checker is not None:
                checked = self._fixture_url_checker(url, purpose)
            else:
                if not is_connect:
                    raise m.CoreError(
                        m.ErrorCode.SCOPE_DENIED,
                        "Plain HTTP is refused by the Facebook browser egress proxy",
                        next_action="Use HTTPS for all Facebook requests",
                    )
                checked = self.policy.before_request(url, purpose=purpose)
        except m.CoreError as exc:
            with self._lock:
                self.last_proxy_error = exc
                self.aborted_urls[url] = exc
                if is_navigation and self.navigation_error is None:
                    self.navigation_error = exc
                self.decisions.append(
                    EgressDecision(
                        hostname=host_guess,
                        purpose=purpose,
                        resource_type=rtype,
                        allowed=False,
                        reason=exc.detail.code.value,
                    )
                )
            raise

        if not is_connect:
            barrier = self._classify_navigation_barrier(url)
            if barrier is not None:
                with self._lock:
                    self.last_proxy_error = barrier
                    self.aborted_urls[url] = barrier
                    if self.navigation_error is None:
                        self.navigation_error = barrier
                    self.decisions.append(
                        EgressDecision(
                            hostname=checked.hostname,
                            purpose=purpose,
                            resource_type=rtype,
                            allowed=False,
                            reason=f"navigation_barrier:{barrier.detail.code.value}",
                        )
                    )
                raise barrier
            if is_navigation:
                try:
                    if self._fixture_nav_validator is not None:
                        self._fixture_nav_validator(url)
                    else:
                        parse_post_permalink(url)
                    self.validate_navigation_identity(url)
                except m.CoreError as exc:
                    with self._lock:
                        self.last_proxy_error = exc
                        self.aborted_urls[url] = exc
                        if self.navigation_error is None:
                            self.navigation_error = exc
                        self.decisions.append(
                            EgressDecision(
                                hostname=checked.hostname,
                                purpose=purpose,
                                resource_type=rtype,
                                allowed=False,
                                reason=f"navigation_unapproved:{exc.detail.code.value}",
                            )
                        )
                    raise

        with self._lock:
            self.checked_by_url[url] = checked
            self.checked_by_url[checked.url] = checked
            self.checked_by_authority[(checked.hostname, checked.port)] = checked
            self.decisions.append(
                EgressDecision(
                    hostname=checked.hostname,
                    purpose=purpose,
                    resource_type=rtype,
                    allowed=True,
                    reason="allowed",
                )
            )
        return checked

    def verify_proxy_peer(
        self,
        checked: safety.CheckedURL,
        *,
        peer_ip: str,
        peer_port: int,
    ) -> str:
        """Verify the connected upstream socket peer against the pinned checked.addresses."""
        try:
            normalized_ip = str(ipaddress.ip_address(peer_ip))
        except ValueError:
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Connected upstream socket peer IP is not a valid IP literal",
            ) from None

        if self._fixture_url_checker is None:
            pinned_policy = safety.OutboundURLPolicy(
                resolver=lambda _host: checked.addresses,
                allowed_hosts=self.allowed_hosts,
            )
            pinned_policy.before_request(
                checked.url,
                purpose=checked.purpose,
                peer_address=normalized_ip,
            )
        elif normalized_ip not in checked.addresses:
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Connected upstream peer IP does not match the checked resolver addresses",
            )

        if peer_port != checked.port or normalized_ip not in checked.addresses:
            with self._lock:
                self.decisions.append(
                    EgressDecision(
                        hostname=checked.hostname,
                        purpose=checked.purpose,
                        resource_type="proxy",
                        allowed=False,
                        reason="peer_mismatch",
                        peer_ip=normalized_ip,
                        peer_verified=False,
                    )
                )
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Connected upstream peer address or port did not match the checked destination",
            )

        with self._lock:
            self.verified_peers[(checked.hostname, checked.port)] = normalized_ip
            self.decisions.append(
                EgressDecision(
                    hostname=checked.hostname,
                    purpose=checked.purpose,
                    resource_type="proxy",
                    allowed=True,
                    reason="peer_verified",
                    peer_ip=normalized_ip,
                    peer_verified=True,
                )
            )
        return normalized_ip

    def check_request(
        self,
        url: str,
        *,
        resource_type: str = "document",
        is_navigation: bool = False,
        redirected_from: str | None = None,
    ) -> safety.CheckedURL:
        rtype = (resource_type or "other").lower()
        if is_navigation:
            purpose: safety.URLPurpose = "redirect" if redirected_from else "request"
        elif rtype in ("image", "media", "font"):
            purpose = "media"
        else:
            purpose = "subresource"

        self.check_browser_route(
            url,
            resource_type=rtype,
            is_navigation=is_navigation,
            redirected_from=redirected_from,
        )

        host_guess = "invalid"
        try:
            host_guess = _normalize_host(urlsplit(url).hostname) or "invalid"
        except ValueError:
            pass

        try:
            if self._fixture_url_checker is not None:
                checked = self._fixture_url_checker(url, purpose)
            else:
                checked = self.policy.before_request(url, purpose=purpose)
        except m.CoreError as exc:
            with self._lock:
                self.decisions.append(
                    EgressDecision(
                        hostname=host_guess,
                        purpose=purpose,
                        resource_type=rtype,
                        allowed=False,
                        reason=exc.detail.code.value,
                    )
                )
            raise

        with self._lock:
            self.checked_by_url[url] = checked
            self.checked_by_url[checked.url] = checked
            self.checked_by_authority[(checked.hostname, checked.port)] = checked
            self.decisions.append(
                EgressDecision(
                    hostname=checked.hostname,
                    purpose=purpose,
                    resource_type=rtype,
                    allowed=True,
                    reason="allowed",
                )
            )
        return checked

    def check_response_peer(
        self,
        url: str,
        *,
        purpose: safety.URLPurpose = "request",
        resource_type: str = "document",
        server_addr: Mapping[str, Any] | None,
    ) -> str:
        with self._lock:
            checked = self.checked_by_url.get(url)
            if checked is None:
                try:
                    parsed_u = urlsplit(url)
                    h_norm = _normalize_host(parsed_u.hostname)
                    p_norm = parsed_u.port or (443 if parsed_u.scheme.lower() == "https" else 80)
                    if h_norm is not None:
                        checked = self.checked_by_authority.get((h_norm, p_norm))
                except ValueError:
                    checked = None
        if checked is None:
            if self._fixture_url_checker is not None:
                checked = self._fixture_url_checker(url, purpose)
            else:
                checked = self.policy.before_request(url, purpose=purpose)
        if not isinstance(server_addr, Mapping):
            with self._lock:
                self.decisions.append(
                    EgressDecision(
                        hostname=checked.hostname,
                        purpose=purpose,
                        resource_type=resource_type,
                        allowed=False,
                        reason="missing_server_addr",
                        peer_verified=False,
                    )
                )
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Browser response did not expose a verifiable peer address",
                next_action=(
                    "Ensure requests are served over a direct verifiable connection "
                    "without caching or proxies"
                ),
            )
        raw_ip = server_addr.get("ipAddress")
        raw_port = server_addr.get("port")
        if not isinstance(raw_ip, str) or not isinstance(raw_port, int):
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Browser response returned an invalid peer address structure",
            )
        try:
            normalized_ip = str(ipaddress.ip_address(raw_ip))
        except ValueError:
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Browser response peer IP is not a valid IP literal",
            ) from None

        # When routed through the session-local BrowserEgressProxy, Playwright's server_addr()
        # reports 127.0.0.1:<proxy_port>; confirm the proxy verified the upstream socket peer.
        if (
            self.proxy_port is not None
            and normalized_ip == "127.0.0.1"
            and raw_port == self.proxy_port
        ):
            with self._lock:
                upstream_peer = self.verified_peers.get((checked.hostname, checked.port))
            if upstream_peer is None or upstream_peer not in checked.addresses:
                raise m.CoreError(
                    m.ErrorCode.SCOPE_DENIED,
                    "Proxy-routed response had no verified upstream peer address",
                )
            with self._lock:
                self.decisions.append(
                    EgressDecision(
                        hostname=checked.hostname,
                        purpose=purpose,
                        resource_type=resource_type,
                        allowed=True,
                        reason="peer_verified",
                        peer_ip=upstream_peer,
                        peer_verified=True,
                    )
                )
            return upstream_peer

        if self._fixture_url_checker is None:
            pinned_policy = safety.OutboundURLPolicy(
                resolver=lambda _host: checked.addresses,
                allowed_hosts=self.allowed_hosts,
            )
            pinned_policy.before_request(
                checked.url,
                purpose=purpose,
                peer_address=normalized_ip,
            )
        elif normalized_ip not in checked.addresses:
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Connected peer IP does not match the checked resolver addresses",
            )

        if raw_port != checked.port or normalized_ip not in checked.addresses:
            with self._lock:
                self.decisions.append(
                    EgressDecision(
                        hostname=checked.hostname,
                        purpose=purpose,
                        resource_type=resource_type,
                        allowed=False,
                        reason="peer_mismatch",
                        peer_ip=normalized_ip,
                        peer_verified=False,
                    )
                )
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Connected peer address or port did not match the checked destination",
            )

        with self._lock:
            self.decisions.append(
                EgressDecision(
                    hostname=checked.hostname,
                    purpose=purpose,
                    resource_type=resource_type,
                    allowed=True,
                    reason="peer_verified",
                    peer_ip=normalized_ip,
                    peer_verified=True,
                )
            )
        return normalized_ip

    def summary(self) -> dict[str, Any]:
        with self._lock:
            decisions_snapshot = list(self.decisions)
        counts: dict[tuple[str, str], int] = {}
        for decision in decisions_snapshot:
            if decision.allowed:
                continue
            purpose = (
                decision.purpose
                if decision.purpose in ("seed", "request", "redirect", "subresource", "media")
                else "request"
            )
            reason = decision.reason
            if reason not in {code.value for code in m.ErrorCode} | {"max_bytes_exceeded"}:
                reason = next(
                    (
                        prefix
                        for prefix in (
                            "proxy_refused",
                            "navigation_barrier",
                            "navigation_unapproved",
                            "blocked_resource_type",
                        )
                        if reason.startswith(prefix + ":")
                    ),
                    "policy_refused",
                )
            counts[(purpose, reason)] = counts.get((purpose, reason), 0) + 1
        return {
            "total_checks": len(decisions_snapshot),
            "allowed": sum(1 for d in decisions_snapshot if d.allowed and d.reason == "allowed"),
            "aborted": sum(1 for d in decisions_snapshot if not d.allowed),
            "peer_verified": sum(1 for d in decisions_snapshot if d.peer_verified),
            "aborted_reasons": [
                {"purpose": purpose, "reason": reason, "count": count}
                for (purpose, reason), count in sorted(counts.items())
            ],
        }


def refuse_proxies(env: Mapping[str, str] | None = None) -> None:
    source = os.environ if env is None else env
    for key in PROXY_ENV_VARS:
        val = source.get(key)
        if val and val.strip():
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                (
                    f"Proxy environment variable {key} is set; "
                    "proxies are refused for Facebook collection"
                ),
                next_action=f"Unset {key} before running Facebook collection",
            )


class _PlaywrightSession:
    def __init__(
        self,
        pw: Any,
        browser: Any,
        context: Any,
        page: Any,
        egress: EgressGuard,
        *,
        proxy: BrowserEgressProxy | None = None,
        timeout_ms: int,
        _test_origin: str | None = None,
    ) -> None:
        self._pw = pw
        self._browser = browser
        self._context = context
        self._page = page
        self._egress = egress
        self._proxy = proxy
        self._timeout_ms = timeout_ms
        self._test_origin = _test_origin.rstrip("/") if _test_origin else None
        self.popups_blocked = 0
        self.downloads_blocked = 0
        self._peer_error: m.CoreError | None = None

        def _on_popup(popup: Any) -> None:
            self.popups_blocked += 1
            try:
                popup.close()
            except Exception:
                pass

        def _on_download(download: Any) -> None:
            self.downloads_blocked += 1
            try:
                download.cancel()
            except Exception:
                pass

        def _on_route(route: Any) -> None:
            req = route.request
            redirected_from = req.redirected_from.url if req.redirected_from else None
            try:
                self._egress.check_browser_route(
                    req.url,
                    resource_type=req.resource_type,
                    is_navigation=req.is_navigation_request(),
                    redirected_from=redirected_from,
                )
            except m.CoreError:
                try:
                    route.abort("blockedbyclient")
                except Exception:
                    pass  # The document may already have been cancelled by Chromium.
                return
            route.continue_()

        def _on_response(response: Any) -> None:
            req = response.request
            with self._egress._lock:
                aborted_err = self._egress.aborted_urls.get(response.url)
                last_proxy_err = self._egress.last_proxy_error
            aborted_by_proxy = (
                response.headers.get("x-metamuse-proxy-aborted") == "1" or aborted_err is not None
            )
            if aborted_by_proxy:
                if req.is_navigation_request() and self._egress.navigation_error is None:
                    self._egress.navigation_error = (
                        aborted_err
                        or last_proxy_err
                        or m.CoreError(
                            m.ErrorCode.SCOPE_DENIED,
                            "Outbound navigation blocked by egress proxy",
                        )
                    )
                return
            purpose: safety.URLPurpose = (
                "request"
                if req.is_navigation_request()
                else ("media" if req.resource_type in ("image", "media", "font") else "subresource")
            )
            try:
                self._egress.check_response_peer(
                    response.url,
                    purpose=purpose,
                    resource_type=req.resource_type,
                    server_addr=response.server_addr(),
                )
            except m.CoreError as exc:
                if self._peer_error is None:
                    self._peer_error = exc

        def _on_frame_navigated(frame: Any) -> None:
            if frame == self._page.main_frame and self._egress.navigation_error is None:
                try:
                    self._egress.check_browser_route(frame.url, is_navigation=True)
                except m.CoreError as exc:
                    self._egress.navigation_error = exc

        self._context.on("page", _on_popup)
        self._page.on("popup", _on_popup)
        self._page.on("download", _on_download)
        self._context.route("**/*", _on_route)
        self._page.on("response", _on_response)
        self._page.on("framenavigated", _on_frame_navigated)

    def _raise_if_byte_limit(self) -> None:
        if self._egress.byte_limit_exceeded or (
            self._proxy is not None and self._proxy.byte_limit_exceeded
        ):
            raise m.CoreError(
                m.ErrorCode.LIMIT_REACHED,
                "Facebook page response exceeded max_bytes budget",
                next_action="Increase limits.max_bytes or narrow the requested resources",
            )

    def goto_post(self, url: str) -> PageObservation:
        target_url = url
        if self._test_origin is not None:
            parsed = urlsplit(url)
            suffix = f"?{parsed.query}" if parsed.query else ""
            target_url = f"{self._test_origin}{parsed.path}{suffix}"
        self._egress.navigation_error = None
        self._egress.last_proxy_error = None
        self._peer_error = None
        try:
            response = self._page.goto(target_url, wait_until="load", timeout=self._timeout_ms)
        except Exception as exc:
            self._raise_if_byte_limit()
            if self._egress.navigation_error is not None:
                raise self._egress.navigation_error from None
            if self._peer_error is not None:
                raise self._peer_error from None
            if self._egress.last_proxy_error is not None:
                raise self._egress.last_proxy_error from None
            raise m.CoreError(
                m.ErrorCode.NETWORK_TIMEOUT,
                "Browser navigation to Facebook post permalink failed",
                next_action="Verify connectivity and the post permalink before retrying",
            ) from exc
        # Dispatch immediate script/meta navigations before the first DOM snapshot.
        self._page.wait_for_timeout(50)
        self._raise_if_byte_limit()
        if self._egress.navigation_error is not None:
            raise self._egress.navigation_error
        self._egress.check_browser_route(self._page.url, is_navigation=True)
        if self._peer_error is not None:
            raise self._peer_error
        if response is None:
            raise m.CoreError(
                m.ErrorCode.NETWORK_TIMEOUT,
                "Browser navigation returned no HTTP response",
            )
        return PageObservation(
            url=self._page.url,
            status_code=response.status,
            html=self._page.content(),
            server_addr=response.server_addr(),
        )

    def expand_next(self, control_id: str) -> PageObservation:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", control_id):
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid expand control identifier")
        self._egress.navigation_error = None
        self._egress.last_proxy_error = None
        self._peer_error = None
        locator = self._page.locator(f'[data-expand-id="{control_id}"]').first
        try:
            locator.click(timeout=self._timeout_ms)
            self._page.wait_for_load_state("load", timeout=self._timeout_ms)
        except Exception as exc:
            self._raise_if_byte_limit()
            if self._egress.navigation_error is not None:
                raise self._egress.navigation_error from None
            if self._peer_error is not None:
                raise self._peer_error from None
            raise m.CoreError(
                m.ErrorCode.SELECTOR_DRIFT,
                "Comment or reply expansion control failed to respond",
                next_action="Inspect the page markup before retrying",
            ) from exc
        self._raise_if_byte_limit()
        if self._egress.navigation_error is not None:
            raise self._egress.navigation_error
        if self._peer_error is not None:
            raise self._peer_error
        return PageObservation(
            url=self._page.url,
            status_code=200,
            html=self._page.content(),
            server_addr=None,
        )

    def close(self) -> None:
        for obj in (self._page, self._context, self._browser):
            try:
                obj.close()
            except Exception:
                pass
        try:
            self._pw.stop()
        except Exception:
            pass
        if self._proxy is not None:
            try:
                self._proxy.close()
            except Exception:
                pass


class PlaywrightBrowserDriver:
    """Thin Playwright Chromium implementation behind BrowserDriver."""

    def __init__(self, *, _test_origin: str | None = None) -> None:
        self._test_origin = _test_origin

    def is_available(self) -> bool:
        return find_playwright_browser() is not None

    def open_session(
        self,
        storage_state: SanitizedStorageState,
        egress: EgressGuard,
        *,
        timeout_ms: int,
        max_bytes: int | None = None,
    ) -> BrowserSession:
        refuse_proxies()
        executable = find_playwright_browser()
        if executable is None:
            ensure_browser_available()
        from playwright.sync_api import sync_playwright

        idle_timeout_s = max(1.0, float(timeout_ms) / 1000.0)
        proxy = BrowserEgressProxy(
            egress,
            max_bytes=max_bytes,
            idle_timeout_s=idle_timeout_s,
        )
        clean_env = {k: v for k, v in os.environ.items() if k not in PROXY_ENV_VARS}
        pw: Any = None
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(
                executable_path=str(executable) if executable else None,
                headless=True,
                chromium_sandbox=False,
                env=clean_env,
                args=build_chromium_launch_args(proxy.port),
            )
            context = browser.new_context(
                storage_state=storage_state.as_playwright_dict(),  # type: ignore[arg-type]
                service_workers="block",
                accept_downloads=False,
                java_script_enabled=True,
            )
            page = context.new_page()
            return _PlaywrightSession(
                pw,
                browser,
                context,
                page,
                egress,
                proxy=proxy,
                timeout_ms=timeout_ms,
                _test_origin=self._test_origin,
            )
        except Exception:
            proxy.close()
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass
            raise


class FixtureBrowserDriver:
    """Synthetic BrowserDriver for deterministic offline tests."""

    def __init__(
        self,
        initial: PageObservation,
        expansions: Mapping[str, PageObservation] | Sequence[PageObservation] = (),
        *,
        available: bool = True,
    ) -> None:
        self.initial = initial
        if isinstance(expansions, Mapping):
            self.expansions_by_id = dict(expansions)
            self.expansions_seq: list[PageObservation] = []
        else:
            self.expansions_by_id = {}
            self.expansions_seq = list(expansions)
        self.available = available
        self.visited_urls: list[str] = []
        self.expanded_controls: list[str] = []
        self.closed = False
        self.last_cookie_hosts: tuple[str, ...] = ()

    def is_available(self) -> bool:
        return self.available

    def open_session(
        self,
        storage_state: SanitizedStorageState,
        egress: EgressGuard,
        *,
        timeout_ms: int,
        max_bytes: int | None = None,
    ) -> BrowserSession:
        del timeout_ms, max_bytes
        refuse_proxies()
        if not self.available:
            ensure_browser_available()
        self.last_cookie_hosts = storage_state.cookie_hosts
        return _FixtureSession(self, egress)


class _FixtureSession:
    def __init__(self, driver: FixtureBrowserDriver, egress: EgressGuard) -> None:
        self._driver = driver
        self._egress = egress
        self._seq_index = 0

    def _run_observation(self, obs: PageObservation, *, request_url: str) -> PageObservation:
        prev_url: str | None = None
        for hop_url in obs.redirect_chain:
            self._egress.check_request(
                hop_url,
                resource_type="document",
                is_navigation=True,
                redirected_from=prev_url or request_url,
            )
            prev_url = hop_url
        nav_url = obs.url or request_url
        self._egress.check_request(
            nav_url,
            resource_type="document",
            is_navigation=True,
            redirected_from=prev_url,
        )
        peer = obs.server_addr or {"ipAddress": "93.184.216.34", "port": 443}
        self._egress.check_response_peer(
            nav_url,
            purpose="redirect" if prev_url else "request",
            resource_type="document",
            server_addr=peer,
        )
        for sub in obs.subresources:
            try:
                self._egress.check_request(
                    sub.url,
                    resource_type=sub.resource_type,
                    is_navigation=sub.is_navigation,
                    redirected_from=sub.redirected_from,
                )
            except m.CoreError:
                if sub.is_navigation:
                    raise
                # Non-navigation subresources are aborted by route handler without crashing nav
                continue
            if sub.server_addr is not None:
                sub_purpose: safety.URLPurpose = (
                    "media" if sub.resource_type in ("image", "media", "font") else "subresource"
                )
                self._egress.check_response_peer(
                    sub.url,
                    purpose=sub_purpose,
                    resource_type=sub.resource_type,
                    server_addr=sub.server_addr,
                )
        return obs

    def goto_post(self, url: str) -> PageObservation:
        self._driver.visited_urls.append(url)
        return self._run_observation(self._driver.initial, request_url=url)

    def expand_next(self, control_id: str) -> PageObservation:
        self._driver.expanded_controls.append(control_id)
        if control_id in self._driver.expansions_by_id:
            obs = self._driver.expansions_by_id[control_id]
        elif self._seq_index < len(self._driver.expansions_seq):
            obs = self._driver.expansions_seq[self._seq_index]
            self._seq_index += 1
        else:
            raise m.CoreError(
                m.ErrorCode.SELECTOR_DRIFT,
                "No expansion observation configured for control",
            )
        return self._run_observation(obs, request_url=obs.url)

    def close(self) -> None:
        self._driver.closed = True


class _DOMExtractor(HTMLParser):
    """Lightweight stdlib HTML parser for synthetic and Facebook post permalink DOMs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta_state: str | None = None
        self.json_scripts: list[str] = []
        self._in_json_script = False
        self._json_buf: list[str] = []
        self.post_attrs: dict[str, str] | None = None
        self.post_text_parts: list[str] = []
        self._in_post_body = 0
        self.attachments: list[dict[str, str]] = []
        self.comments_section_present = False
        self.comments: list[dict[str, Any]] = []
        self._current_comment: dict[str, Any] | None = None
        self._in_comment_body = 0
        self.expand_controls: list[ExpandControl] = []
        self._stack: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        self._stack.append((tag.lower(), attr_map))

        fb_state = attr_map.get("data-fb-state") or attr_map.get("data-page-state")
        if fb_state:
            self.meta_state = fb_state.strip().lower()
        if tag.lower() == "form" and (
            attr_map.get("id") == "login_form" or attr_map.get("data-testid") == "royal_login_form"
        ):
            self.meta_state = self.meta_state or "login_wall"
        if attr_map.get("data-checkpoint") == "true" or attr_map.get("id") == "checkpoint_Form":
            self.meta_state = self.meta_state or "checkpoint"

        if (
            tag.lower() == "script"
            and attr_map.get("type", "").lower() == "application/json"
            and "data-fb-post-payload" in attr_map
        ):
            self._in_json_script = True
            self._json_buf = []
            return

        role = attr_map.get("role", "").lower()
        fb_role = attr_map.get("data-fb-role", "").lower()

        if fb_role == "post" or (role == "article" and "data-post-id" in attr_map):
            if self.post_attrs is None:
                self.post_attrs = dict(attr_map)
        if fb_role == "post-body" or attr_map.get("data-ad-preview") == "message":
            self._in_post_body += 1
        elif self._in_post_body > 0:
            self._in_post_body += 1

        if fb_role == "attachment" and "data-attachment-url" in attr_map:
            self.attachments.append(
                {
                    "url": attr_map["data-attachment-url"],
                    "kind": attr_map.get("data-attachment-kind", "image"),
                }
            )

        if fb_role == "comments-section" or attr_map.get("data-comments-section") is not None:
            self.comments_section_present = True

        if fb_role in ("comment", "reply") or (
            role == "article" and ("data-comment-id" in attr_map or "data-comment-kind" in attr_map)
        ):
            self.comments_section_present = True
            kind = (
                "reply"
                if (
                    fb_role == "reply"
                    or attr_map.get("data-comment-kind") == "reply"
                    or "data-parent-comment-id" in attr_map
                )
                else "comment"
            )
            self._current_comment = {
                "kind": kind,
                "native_id": attr_map.get("data-comment-id") or None,
                "parent_native_id": attr_map.get("data-parent-comment-id") or None,
                "author_display": attr_map.get("data-author") or None,
                "created_at_raw": attr_map.get("data-created-at") or None,
                "permalink": attr_map.get("data-permalink") or None,
                "mention_tag": attr_map.get("data-mention") or None,
                "text_parts": [],
            }
            self.comments.append(self._current_comment)

        if self._current_comment is not None and (
            fb_role == "comment-body" or attr_map.get("data-comment-body") is not None
        ):
            self._in_comment_body += 1
        elif self._in_comment_body > 0:
            self._in_comment_body += 1

        expand_kind = attr_map.get("data-expand-kind", "").lower()
        if expand_kind in ("more_comments", "more_replies"):
            self.comments_section_present = True
            control_id = attr_map.get("data-expand-id") or f"expand_{len(self.expand_controls) + 1}"
            hint_raw = attr_map.get("data-remaining-count")
            hint = int(hint_raw) if (hint_raw and hint_raw.isdigit()) else None
            self.expand_controls.append(
                ExpandControl(
                    control_id=control_id,
                    kind="more_replies" if expand_kind == "more_replies" else "more_comments",
                    parent_comment_id=attr_map.get("data-parent-comment-id") or None,
                    remaining_hint=hint,
                )
            )

    def handle_endtag(self, tag: str) -> None:
        if self._in_json_script and tag.lower() == "script":
            self._in_json_script = False
            self.json_scripts.append("".join(self._json_buf))
            self._json_buf = []
        if self._in_post_body > 0:
            self._in_post_body -= 1
        if self._in_comment_body > 0:
            self._in_comment_body -= 1
        if self._stack:
            popped_tag, popped_attrs = self._stack.pop()
            fb_role = popped_attrs.get("data-fb-role", "").lower()
            role = popped_attrs.get("role", "").lower()
            if (
                fb_role in ("comment", "reply")
                or (
                    role == "article"
                    and ("data-comment-id" in popped_attrs or "data-comment-kind" in popped_attrs)
                )
            ) and popped_tag == tag.lower():
                self._current_comment = None

    def handle_data(self, data: str) -> None:
        if self._in_json_script:
            self._json_buf.append(data)
            return
        if self._in_post_body > 0:
            self.post_text_parts.append(data)
        if self._in_comment_body > 0 and self._current_comment is not None:
            self._current_comment["text_parts"].append(data)


def _parse_iso_time(raw: str | None) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.isdigit():
        try:
            dt = datetime.fromtimestamp(int(text), tz=UTC)
            return dt.isoformat().replace("+00:00", "Z")
        except (ValueError, OSError, OverflowError):
            return None
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None or dt.utcoffset() is None:
            return None
        return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


def _opt_int(val: Any) -> int | None:
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    if isinstance(val, str) and val.strip().isdigit():
        return int(val.strip())
    return None


def _validate_extracted_identity(post_id: str, actor: Any, target: PostPermalinkTarget) -> None:
    if (actor, post_id) != (target.actor, target.post_id):
        raise m.CoreError(
            m.ErrorCode.REFERENCE_INVALID,
            "Extracted Facebook post does not match the authorized identity",
            next_action="Review the approved permalink and its exact actor and post token",
        )


def classify_and_parse_observation(
    obs: PageObservation,
    *,
    permalink: PostPermalinkTarget,
    require_comments_section: bool,
) -> ParsedPostPage:
    """Classify failure states (AT-041) or parse the post permalink DOM/JSON."""
    extractor = _DOMExtractor()
    try:
        extractor.feed(obs.html)
    except Exception as exc:
        raise m.CoreError(
            m.ErrorCode.SELECTOR_DRIFT,
            "Facebook response HTML could not be parsed",
            next_action="Inspect the page markup against the approved post_permalink parser",
        ) from exc

    # 1. Explicit status code and barrier checks (AT-041)
    if obs.status_code == 429 or extractor.meta_state == "rate_limited":
        raise m.CoreError(
            m.ErrorCode.RATE_LIMITED,
            "Facebook rate-limited the browser session (HTTP 429 or rate-limit barrier)",
            next_action=(
                "Stop collection and wait before retrying; do not rotate accounts or proxies"
            ),
        )
    if extractor.meta_state in ("checkpoint", "challenge"):
        raise m.CoreError(
            m.ErrorCode.CHECKPOINT_REQUIRED,
            "Facebook presented an account checkpoint or challenge",
            next_action=(
                "Complete the challenge manually outside MetaMuse and re-import storage_state"
            ),
        )
    if obs.status_code == 401 or extractor.meta_state == "login_wall":
        raise m.CoreError(
            m.ErrorCode.AUTH_REQUIRED,
            "Facebook required authentication (login wall encountered)",
            next_action=(
                "Log in outside MetaMuse, export a fresh storage_state, and run "
                "metamuse auth import"
            ),
        )
    if obs.status_code == 403 or extractor.meta_state == "access_denied":
        raise m.CoreError(
            m.ErrorCode.ACCESS_DENIED,
            "Facebook denied access to this post permalink (HTTP 403 or permission barrier)",
            next_action="Verify that the authenticated account has permission to view this post",
        )
    if obs.status_code >= 400:
        raise m.CoreError(
            m.ErrorCode.PROTOCOL_MISMATCH,
            f"Unexpected HTTP status {obs.status_code} on Facebook post permalink",
            next_action="Inspect the target permalink and try again later",
        )

    # 2. Check embedded JSON script payload if present (tlyu0419/facebook_crawler _parse_edge)
    if extractor.json_scripts and extractor.post_attrs is None:
        return _parse_json_payload(
            extractor.json_scripts[0],
            permalink=permalink,
            require_comments_section=require_comments_section,
        )

    # 3. Validate DOM post structure
    if extractor.post_attrs is None:
        raise m.CoreError(
            m.ErrorCode.SELECTOR_DRIFT,
            "Facebook post container was not found on the permalink page (selector drift)",
            next_action="Update the post_permalink parser selectors after reviewing fixture markup",
        )

    attrs = extractor.post_attrs
    dom_post_id = (attrs.get("data-post-id") or "").strip()
    if not dom_post_id:
        raise m.CoreError(
            m.ErrorCode.SELECTOR_DRIFT,
            "Facebook post container is missing a post identifier",
        )
    if require_comments_section and not extractor.comments_section_present:
        raise m.CoreError(
            m.ErrorCode.SELECTOR_DRIFT,
            "Facebook comments section container is missing from the post permalink DOM",
            next_action="Inspect the comment container selectors before retrying",
        )

    _validate_extracted_identity(dom_post_id, attrs.get("data-actor"), permalink)

    post_text_raw = html_lib.unescape("".join(extractor.post_text_parts)).strip()
    post_text = post_text_raw if post_text_raw else (attrs.get("data-post-text") or None)
    raw_time = attrs.get("data-created-at") or None

    parsed_comments: list[ParsedComment] = []
    for c in extractor.comments:
        c_text_raw = html_lib.unescape("".join(c["text_parts"])).strip()
        c_text = c_text_raw if c_text_raw else None
        c_kind: Literal["comment", "reply"] = "reply" if c["kind"] == "reply" else "comment"
        parent_native = c["parent_native_id"]
        if c_kind == "comment" and parent_native is None:
            parent_native = dom_post_id
        c_raw_time = c["created_at_raw"]
        parsed_comments.append(
            ParsedComment(
                kind=c_kind,
                native_id=c["native_id"],
                parent_native_id=parent_native,
                author_display=c["author_display"],
                text=c_text,
                created_at_raw=c_raw_time,
                parsed_time=_parse_iso_time(c_raw_time),
                permalink=c["permalink"],
                mention_tag=c["mention_tag"],
            )
        )

    return ParsedPostPage(
        post_id=dom_post_id,
        actor=attrs.get("data-actor") or permalink.actor,
        author_display=attrs.get("data-author") or None,
        text=post_text,
        created_at_raw=raw_time,
        parsed_time=_parse_iso_time(raw_time),
        permalink=attrs.get("data-permalink") or permalink.canonical_url,
        reaction_count=_opt_int(attrs.get("data-reaction-count")),
        comment_count=_opt_int(attrs.get("data-comment-count")),
        share_count=_opt_int(attrs.get("data-share-count")),
        attachments=list(extractor.attachments),
        comments=parsed_comments,
        expand_controls=list(extractor.expand_controls),
        comments_section_present=extractor.comments_section_present,
    )


def _parse_json_payload(
    raw_json: str,
    *,
    permalink: PostPermalinkTarget,
    require_comments_section: bool,
) -> ParsedPostPage:
    try:
        payload = json.loads(raw_json)
    except (ValueError, TypeError) as exc:
        raise m.CoreError(
            m.ErrorCode.SELECTOR_DRIFT,
            "Embedded Facebook post JSON payload is malformed",
        ) from exc
    if not isinstance(payload, Mapping) or not isinstance(payload.get("post"), Mapping):
        raise m.CoreError(
            m.ErrorCode.SELECTOR_DRIFT,
            "Embedded Facebook post JSON payload is missing the post object",
        )
    post_obj = payload["post"]
    post_id = str(post_obj.get("post_id") or post_obj.get("id") or "").strip()
    if not post_id:
        raise m.CoreError(
            m.ErrorCode.SELECTOR_DRIFT,
            "Embedded Facebook post JSON is missing post_id",
        )
    _validate_extracted_identity(post_id, post_obj.get("actor"), permalink)
    has_comments_key = "comments" in payload
    if require_comments_section and not has_comments_key:
        raise m.CoreError(
            m.ErrorCode.SELECTOR_DRIFT,
            "Embedded Facebook post JSON is missing the comments section",
        )
    raw_time = (
        str(post_obj["creation_time"])
        if post_obj.get("creation_time") is not None
        else (str(post_obj["created_at"]) if post_obj.get("created_at") is not None else None)
    )
    attachments: list[dict[str, str]] = []
    for att in post_obj.get("attachments", []) or []:
        if isinstance(att, Mapping) and isinstance(att.get("url"), str):
            attachments.append({"url": att["url"], "kind": str(att.get("kind") or "image")})

    comments_list: list[ParsedComment] = []
    for entry in payload.get("comments", []) or []:
        if not isinstance(entry, Mapping):
            continue
        kind_val = str(entry.get("kind") or "comment")
        c_kind: Literal["comment", "reply"] = "reply" if kind_val == "reply" else "comment"
        native_id = str(entry["id"]).strip() if entry.get("id") else None
        parent_id = (
            str(entry["parent_id"]).strip()
            if entry.get("parent_id")
            else (post_id if c_kind == "comment" else None)
        )
        c_time = str(entry["created_at"]) if entry.get("created_at") is not None else None
        comments_list.append(
            ParsedComment(
                kind=c_kind,
                native_id=native_id,
                parent_native_id=parent_id,
                author_display=str(entry["author"]) if entry.get("author") else None,
                text=str(entry["text"]) if entry.get("text") is not None else None,
                created_at_raw=c_time,
                parsed_time=_parse_iso_time(c_time),
                permalink=str(entry["permalink"]) if entry.get("permalink") else None,
                mention_tag=str(entry["mention_tag"]) if entry.get("mention_tag") else None,
            )
        )

    controls: list[ExpandControl] = []
    for ctrl in payload.get("expand_controls", []) or []:
        if not isinstance(ctrl, Mapping):
            continue
        cid = str(ctrl.get("id") or f"expand_{len(controls) + 1}")
        ckind: Literal["more_comments", "more_replies"] = (
            "more_replies" if ctrl.get("kind") == "more_replies" else "more_comments"
        )
        controls.append(
            ExpandControl(
                control_id=cid,
                kind=ckind,
                parent_comment_id=(
                    str(ctrl["parent_comment_id"]) if ctrl.get("parent_comment_id") else None
                ),
                remaining_hint=_opt_int(ctrl.get("remaining")),
            )
        )

    return ParsedPostPage(
        post_id=post_id,
        actor=str(post_obj.get("actor") or permalink.actor),
        author_display=str(post_obj["author"]) if post_obj.get("author") else None,
        text=str(post_obj["text"]) if post_obj.get("text") is not None else None,
        created_at_raw=raw_time,
        parsed_time=_parse_iso_time(raw_time),
        permalink=str(post_obj.get("permalink") or permalink.canonical_url),
        reaction_count=_opt_int(post_obj.get("reaction_count")),
        comment_count=_opt_int(post_obj.get("comment_count")),
        share_count=_opt_int(post_obj.get("share_count")),
        attachments=attachments,
        comments=comments_list,
        expand_controls=controls,
        comments_section_present=has_comments_key,
    )


def submit_facebook_collect(ctx: Any, request: m.CollectRequest) -> m.Envelope:
    """Validate and submit a bounded single-post Facebook collection job."""
    register_operations()
    fb_cfg = ctx.policy.facebook

    # 1. Non-Facebook URLs and mixed inputs stay E_UNSUPPORTED; loopback/private are E_SCOPE_DENIED
    for item in request.inputs:
        if item.kind != "url":
            raise m.CoreError(
                m.ErrorCode.UNSUPPORTED,
                "Remote collection is not implemented in this build",
                next_action=collect.REMOTE_NEXT,
            )
        try:
            parsed_item = urlsplit(item.url)
        except ValueError:
            parsed_item = None
        item_host = _normalize_host(parsed_item.hostname) if parsed_item is not None else None
        if item_host is not None and _is_forbidden_local_or_ip_host(item_host):
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Loopback, private, and IP-literal URLs are forbidden",
                next_action="Use an approved HTTPS Facebook post permalink on www.facebook.com",
            )
        if not is_facebook_candidate_url(item.url):
            raise m.CoreError(
                m.ErrorCode.UNSUPPORTED,
                "Remote collection is not implemented in this build",
                next_action=collect.REMOTE_NEXT,
            )
    if len(request.inputs) > request.limits.max_urls:
        raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Input count exceeds max_urls")
    if len(request.inputs) != 1:
        raise m.CoreError(
            m.ErrorCode.UNSUPPORTED,
            "Facebook collection is bounded to a single post permalink per job",
            next_action="Submit one Facebook post permalink URL per job",
        )
    url_input = request.inputs[0]
    assert isinstance(url_input, m.URLInput)
    permalink = parse_post_permalink(url_input.url)

    # 2. Project and workspace limit checks
    ctx.store.get(m.Project, request.project_id)
    for name in m.CollectLimits.model_fields:
        if getattr(request.limits, name) > getattr(ctx.limits, name):
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Request limit exceeds the workspace limit",
                next_action="Lower the request limit or raise it in the workspace configuration",
            )
    requested = set(request.resources)
    if not requested <= {"post", "comments"}:
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID,
            "Facebook post permalink collection supports only 'post' and 'comments' resources",
            next_action="Request resources=['post', 'comments'] (or ['post'] / ['comments'])",
        )
    if (
        request.language is not None
        or request.attach_asset_id is not None
        or request.attach_item_id is not None
    ):
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID,
            "Subtitle or media attachment fields do not apply to a Facebook post permalink",
        )

    # 3. Operator-configured approved page-type setting (default off)
    if not fb_cfg.enabled or PAGE_TYPE_POST_PERMALINK not in fb_cfg.approved_page_types:
        raise m.CoreError(
            m.ErrorCode.SCOPE_REQUIRED,
            "Facebook post_permalink collection is disabled by default",
            next_action=(
                "Set facebook.enabled = true and "
                'facebook.approved_page_types = ["post_permalink"] in metamuse.toml'
            ),
        )

    # 4. Network mode check
    if ctx.policy.app.network_mode != "live":
        raise m.CoreError(
            m.ErrorCode.SCOPE_DENIED,
            "External Facebook collection is disabled when app.network_mode is offline",
            next_action=(
                'Set app.network_mode = "live" in metamuse.toml after operator authorization'
            ),
        )

    # 5. Account, access grant permit, and storage state checks (refuse before a job exists)
    if not fb_cfg.account:
        raise m.CoreError(
            m.ErrorCode.SCOPE_REQUIRED,
            "Facebook account label is not configured",
            next_action="Set facebook.account in metamuse.toml to match the permit and auth_ref",
        )
    if not request.access_grant_id:
        raise m.CoreError(
            m.ErrorCode.SCOPE_REQUIRED,
            "An access_grant_id permit is required for Facebook collection",
            next_action=(
                "Create an access permit with metamuse permit create and pass access_grant_id"
            ),
        )
    target = build_target(
        project_id=request.project_id,
        account=fb_cfg.account,
        url=permalink.canonical_url,
    )
    action_payload = build_action_payload(
        url=permalink.canonical_url,
        resources=request.resources,
    )
    use = safety.PermitUse(
        kind="access",
        project_id=request.project_id,
        provider="facebook",
        account=fb_cfg.account,
        operation="collect.remote",
        digest=safety.action_digest(target, action_payload),
    )
    with ctx.store.connection() as conn:
        safety.verify_permit(
            ctx.store,
            request.access_grant_id,
            use,
            conn=conn,
            reserve=False,
        )

    if not fb_cfg.auth_ref:
        raise m.CoreError(
            m.ErrorCode.AUTH_REQUIRED,
            "Facebook storage-state auth_ref is not configured",
            next_action=(
                "Import a Playwright storage state with metamuse auth import and set "
                "facebook.auth_ref"
            ),
        )
    # Validate secret resolution and storage state format before creating any job
    load_storage_state(
        fb_cfg.auth_ref,
        account=fb_cfg.account,
    )

    # 6. Check Playwright & Chromium binary availability before creating a job
    ensure_browser_available()

    # 7. Seed URL egress check before creating a job (OutboundURLPolicy + real resolver)
    if _EGRESS_FACTORY is None:
        egress = _active_egress(fb_cfg.allowed_hosts)
        egress.check_request(permalink.fetch_url, resource_type="document", is_navigation=True)

    job_payload = request.model_dump(mode="json")
    limits_dict = request.limits.model_dump(mode="json")
    # Do not blind-retry browser challenges or rate limits in a loop
    limits_dict["safe_read_retries"] = 0

    return submit_job(
        ctx.store,
        OPERATION,
        request.request_key,
        job_payload,
        project_id=request.project_id,
        steps=[StepSpec(target=target)],
        requested_scope={
            "page_type": PAGE_TYPE_POST_PERMALINK,
            "url": permalink.canonical_url,
            "resources": list(request.resources),
            "max_expansions": fb_cfg.max_expansions,
        },
        limits=limits_dict,
    )


def _load_verified_action_payload(
    conn: Any, target: dict[str, Any], *, job_id: str, store: Any
) -> dict[str, Any]:
    job = store.get(m.Job, job_id, conn=conn)
    req = m.CollectRequest.model_validate(job.payload)
    if len(req.inputs) != 1 or req.inputs[0].kind != "url":
        raise m.CoreError(m.ErrorCode.SCOPE_DENIED, "Persisted job input is not a single URL")
    parsed = parse_post_permalink(req.inputs[0].url)
    if parsed.canonical_url != target.get("url"):
        raise m.CoreError(m.ErrorCode.SCOPE_DENIED, "Persisted job URL does not match target")
    return build_action_payload(url=parsed.canonical_url, resources=req.resources)


def _records_from_page(
    parsed: ParsedPostPage,
    *,
    permalink: PostPermalinkTarget,
    resources: set[str],
) -> tuple[list[collect.Record], list[dict[str, str]] | None]:
    records: list[collect.Record] = []
    row_num = 0
    post_media = parsed.attachments if parsed.attachments else None
    if "post" in resources:
        row_num += 1
        records.append(
            collect.Record(
                row=row_num,
                line=None,
                platform="facebook",
                kind="post",
                native_id=parsed.post_id,
                scope=permalink.scope,
                parent_native_id=None,
                parent_set=True,
                text=parsed.text,
                source_url=parsed.permalink or permalink.canonical_url,
                upstream=UPSTREAM_METHOD,
                created_at_raw=parsed.created_at_raw,
                parsed_time=parsed.parsed_time,
                author_display=parsed.author_display,
                media=post_media,
            )
        )
    if "comments" in resources:
        for comment in parsed.comments:
            row_num += 1
            records.append(
                collect.Record(
                    row=row_num,
                    line=None,
                    platform="facebook",
                    kind=comment.kind,
                    native_id=comment.native_id,
                    scope=permalink.scope,
                    parent_native_id=comment.parent_native_id,
                    parent_set=True,
                    text=comment.text,
                    source_url=comment.permalink or permalink.canonical_url,
                    upstream=UPSTREAM_METHOD,
                    created_at_raw=comment.created_at_raw,
                    parsed_time=comment.parsed_time,
                    author_display=comment.author_display,
                    media=None,
                )
            )
    return records, post_media


def _insert_sources_before_flush(
    writer: TransactionWriter,
    pending: Sequence[collect._Item],
    *,
    project_id: str,
    now: str,
    canonical_url: str,
    original_url: str,
    permit_id: str,
    account: str,
    page_sha: str,
) -> None:
    """Insert Source rows with full provenance before book.flush runs (no post-insert UPDATE)."""
    for item in pending:
        if not item.source_new:
            continue
        use_basis = {
            "permit_id": permit_id,
            "account": account,
            "page_type": PAGE_TYPE_POST_PERMALINK,
            "input_sha256": page_sha,
        }
        writer.insert(
            m.Source(
                id=item.source_id,
                project_id=project_id,
                platform=item.platform,
                native_scope=item.scope if item.native_id else (item.scope or None),
                native_id=item.native_id,
                original_url=item.original_url or original_url,
                canonical_url=canonical_url,
                collection_method=item.collection_method,
                upstream_collection_method=item.upstream,
                assurance="browser_verified",
                fetched_at=now,
                use_basis=use_basis,
            )
        )
        item.source_new = False


def run_facebook_post(step: StepContext) -> m.JobResult:
    from metamuse.config import load_config

    ensure_browser_available()
    cfg = load_config(step.store.paths.root)
    fb_cfg = cfg.facebook
    request = m.CollectRequest.model_validate(step.payload)
    if not fb_cfg.enabled or PAGE_TYPE_POST_PERMALINK not in fb_cfg.approved_page_types:
        raise m.CoreError(
            m.ErrorCode.SCOPE_REQUIRED,
            "Facebook post_permalink collection is disabled in workspace configuration",
        )
    if not request.access_grant_id:
        raise m.CoreError(m.ErrorCode.SCOPE_REQUIRED, "A permit is required")
    if not fb_cfg.account:
        raise m.CoreError(m.ErrorCode.SCOPE_REQUIRED, "Facebook account is not configured")
    if not fb_cfg.auth_ref:
        raise m.CoreError(m.ErrorCode.AUTH_REQUIRED, "Facebook auth_ref is not configured")

    permit_id = request.access_grant_id
    auth_ref = fb_cfg.auth_ref
    account = fb_cfg.account
    url_input = request.inputs[0]
    assert isinstance(url_input, m.URLInput)
    permalink = parse_post_permalink(url_input.url)

    target = build_target(
        project_id=request.project_id,
        account=account,
        url=permalink.canonical_url,
    )
    action_payload = build_action_payload(
        url=permalink.canonical_url,
        resources=request.resources,
    )
    use = safety.PermitUse(
        kind="access",
        project_id=request.project_id,
        provider="facebook",
        account=account,
        operation="collect.remote",
        digest=safety.action_digest(target, action_payload),
    )

    # Reserve or re-verify permit use inside Core's external action boundary
    step.before_external_action(
        step.target,
        verify=safety.external_action_verifier(
            step,
            permit_id=permit_id,
            use=use,
            load_payload=lambda conn, tgt: _load_verified_action_payload(
                conn, tgt, job_id=step.job.id, store=step.store
            ),
        ),
    )

    storage_state = load_storage_state(
        auth_ref,
        account=account,
    )
    egress = _active_egress(fb_cfg.allowed_hosts)
    egress.authorize_permalink(permalink)

    saved = dict(step.checkpoint or {})
    resources = set(request.resources)
    require_comments = "comments" in resources
    max_expansions = int(fb_cfg.max_expansions)
    pacing_ms = int(fb_cfg.pacing_ms)
    timeout_ms = int(cfg.limits.read_timeout_seconds) * 1000
    wall_clock_s = float(request.limits.wall_clock_seconds)
    max_bytes = int(request.limits.max_bytes)
    max_items = int(request.limits.max_items)

    driver = _DRIVER_FACTORY() if _DRIVER_FACTORY is not None else PlaywrightBrowserDriver()
    started = time.monotonic()
    expansions_used = 0
    bytes_used = 0
    halt: str | None = None
    obs: PageObservation | None = None
    parsed: ParsedPostPage | None = None
    byte_limit_error: m.ErrorDetail | None = None

    session = driver.open_session(
        storage_state,
        egress,
        timeout_ms=timeout_ms,
        max_bytes=max_bytes,
    )
    try:
        step.guard()
        obs = session.goto_post(permalink.fetch_url)
        egress.check_browser_route(obs.url, is_navigation=True)
        bytes_used = max(egress.bytes_relayed, len(obs.html.encode("utf-8")))
        if egress.byte_limit_exceeded or bytes_used > max_bytes:
            raise m.CoreError(
                m.ErrorCode.LIMIT_REACHED,
                "Facebook page response exceeded max_bytes budget",
                next_action="Increase limits.max_bytes or narrow the requested resources",
            )
        parsed = classify_and_parse_observation(
            obs,
            permalink=permalink,
            require_comments_section=require_comments,
        )

        while require_comments and parsed.expand_controls:
            step.guard()
            elapsed = time.monotonic() - started
            if elapsed >= wall_clock_s:
                halt = "limit_reached"
                break
            if expansions_used >= max_expansions:
                halt = "expand_budget_exhausted"
                break
            projected_items = (1 if "post" in resources else 0) + len(parsed.comments)
            if projected_items >= max_items:
                halt = "limit_reached"
                break
            if pacing_ms > 0:
                step.executor.sleep(pacing_ms / 1000.0)
            ctrl = parsed.expand_controls[0]
            obs = session.expand_next(ctrl.control_id)
            egress.check_browser_route(obs.url, is_navigation=True)
            expansions_used += 1
            bytes_used = max(
                egress.bytes_relayed,
                bytes_used + len(obs.html.encode("utf-8")),
            )
            if egress.byte_limit_exceeded or bytes_used > max_bytes:
                raise m.CoreError(
                    m.ErrorCode.LIMIT_REACHED,
                    "Facebook page response exceeded max_bytes budget",
                    next_action="Increase limits.max_bytes or narrow the requested resources",
                )
            parsed = classify_and_parse_observation(
                obs,
                permalink=permalink,
                require_comments_section=require_comments,
            )
    except m.CoreError as exc:
        # Scrub any secret token from error messages before surfacing
        cleaned_msg = storage_state.redact(exc.detail.message)
        if exc.detail.code == m.ErrorCode.LIMIT_REACHED or egress.byte_limit_exceeded:
            halt = "limit_reached"
            bytes_used = max(bytes_used, egress.bytes_relayed)
            byte_limit_error = m.ErrorDetail(
                code=m.ErrorCode.LIMIT_REACHED,
                message=cleaned_msg,
                retry_class=exc.detail.retry_class,
                next_action=exc.detail.next_action,
            )
        else:
            raise m.CoreError(
                exc.detail.code,
                cleaned_msg,
                next_action=exc.detail.next_action,
                retry_after=exc.retry_after,
            ) from None
    finally:
        session.close()

    final_html = obs.html if obs is not None else ""
    egress_summary = egress.summary()
    page_sha = hashlib.sha256(final_html.encode("utf-8")).hexdigest()

    unexpanded_gaps: list[str] = []
    if parsed is not None and require_comments and parsed.expand_controls:
        for ctrl in parsed.expand_controls:
            gap_name = (
                "unexpanded_replies" if ctrl.kind == "more_replies" else "unexpanded_comments"
            )
            if gap_name not in unexpanded_gaps:
                unexpanded_gaps.append(gap_name)

    if parsed is not None:
        records, post_media = _records_from_page(
            parsed,
            permalink=permalink,
            resources=resources,
        )
        post_extra_evidence: dict[str, Any] = {
            "canonical_url": permalink.canonical_url,
            "original_url": permalink.original_url,
            "page_type": PAGE_TYPE_POST_PERMALINK,
            "reaction_count": parsed.reaction_count,
            "comment_count": parsed.comment_count,
            "share_count": parsed.share_count,
            "expansions_used": expansions_used,
            "unexpanded_controls": len(parsed.expand_controls),
        }
    else:
        records = []
        post_media = None
        post_extra_evidence = {}

    with step.store.connection() as conn:
        book = collect._Book(request.project_id)
        book.load(conn)

    # Identity-based resume (AT-013, FR-010): pass every record from the fresh observation
    # through book.apply; items committed in an earlier batch before a crash are already in
    # book.by_key and become "unchanged" without index slicing or storing HTML in checkpoints.
    rows: list[dict[str, Any]] = []
    missing: set[str] = set(saved.get("missing_fields", [])) | set(unexpanded_gaps)
    if byte_limit_error is not None:
        missing.add("byte_budget_exceeded")
    accepted = 0
    batch_count = 0

    def _flush_checkpoint(processed_count: int) -> None:
        nonlocal batch_count
        batch_count = 0
        cp_payload = {
            "expansions_used": expansions_used,
            "bytes_used": bytes_used,
            "halt": halt,
            "missing_fields": sorted(missing),
            "accepted": accepted,
        }

        def _write(writer: TransactionWriter) -> None:
            now = step.store.clock()
            _insert_sources_before_flush(
                writer,
                book.pending,
                project_id=request.project_id,
                now=now,
                canonical_url=permalink.canonical_url,
                original_url=permalink.original_url,
                permit_id=permit_id,
                account=account,
                page_sha=page_sha,
            )
            book.flush(writer, step.store)
            writer.insert(
                m.Event(
                    id=m.new_id("evt"),
                    object_id=step.job.id,
                    job_id=step.job.id,
                    project_id=step.job.project_id,
                    kind="collect.facebook_batch",
                    detail={"processed": processed_count, "accepted": accepted},
                    created_at=now,
                )
            )

        step.commit_checkpoint(cp_payload, write_batch=_write)
        if _NOTE_BATCH is not None:
            _NOTE_BATCH(step)

    for idx, rec in enumerate(records):
        step.guard()
        if accepted >= max_items:
            halt = "limit_reached"
            if len(records) > idx:
                missing.add("uncollected_items_budget")
            _flush_checkpoint(idx)
            break
        ev_media = post_media if rec.kind == "post" else None
        try:
            item, status = book.apply(
                rec,
                method=COLLECTION_METHOD,
                sha=page_sha,
                evidence_media=ev_media,
            )
        except collect._Invalid as exc:
            rows.append(
                {
                    "input": 0,
                    "row": rec.row,
                    "line": None,
                    "status": "rejected",
                    "item_id": None,
                    "revision": None,
                    "source_id": None,
                    "asset_id": None,
                    "segment_id": None,
                    "code": exc.code.value,
                    "field": exc.field,
                    "message": exc.message,
                }
            )
        else:
            if item.kind == "post" and item.revision_new:
                merged_missing = list(item.missing_fields)
                for gap in unexpanded_gaps:
                    if gap not in merged_missing:
                        merged_missing.append(gap)
                item.missing_fields = merged_missing
                item.complete = not merged_missing
                item.evidence.update(post_extra_evidence)
            accepted += 1
            for mf in item.missing_fields:
                missing.add(mf)
            if item.unresolved_parent is not None:
                missing.add("parent_comment")
            rows.append(
                {
                    "input": 0,
                    "row": rec.row,
                    "line": None,
                    "status": status,
                    "item_id": item.item_id,
                    "revision": item.revision,
                    "source_id": item.source_id,
                    "asset_id": None,
                    "segment_id": None,
                    "code": None,
                    "field": None,
                    "message": None,
                }
            )
        batch_count += 1
        if batch_count >= BATCH_SIZE or idx + 1 == len(records):
            _flush_checkpoint(idx + 1)

    if byte_limit_error is not None:
        rows.append(
            {
                "input": 0,
                "row": None,
                "line": None,
                "status": "rejected",
                "item_id": None,
                "revision": None,
                "source_id": None,
                "asset_id": None,
                "segment_id": None,
                "code": m.ErrorCode.LIMIT_REACHED.value,
                "field": "max_bytes",
                "message": byte_limit_error.message,
            }
        )
        _flush_checkpoint(len(records))
    elif not records:
        _flush_checkpoint(0)

    rejected = any(r.get("status") == "rejected" for r in rows)
    has_gaps = (
        bool(unexpanded_gaps)
        or byte_limit_error is not None
        or "byte_budget_exceeded" in missing
        or "uncollected_items_budget" in missing
        or "parent_comment" in missing
    )
    comments_observed = sum(1 for r in records[:accepted] if r.kind in ("comment", "reply"))
    comments_empty = (
        parsed is not None
        and require_comments
        and len(parsed.comments) == 0
        and len(parsed.expand_controls) == 0
    )

    if has_gaps or rejected:
        coverage: m.CoverageStatus = "partial"
        if halt in ("limit_reached", "expand_budget_exhausted"):
            stop_reason = halt
        elif unexpanded_gaps:
            stop_reason = "unexpanded_controls"
        elif rejected:
            stop_reason = "row_errors"
        else:
            stop_reason = "incomplete_parent_tree"
    elif halt == "limit_reached":
        coverage = "bounded"
        stop_reason = "limit_reached"
    elif accepted == 0:
        coverage = "bounded"
        stop_reason = "empty_input"
    else:
        coverage = "scope_satisfied"
        stop_reason = "input_exhausted"

    observed_scope = {
        "page_type": PAGE_TYPE_POST_PERMALINK,
        "canonical_url": permalink.canonical_url,
        "committed": sum(r.get("status") == "committed" for r in rows),
        "unchanged": sum(r.get("status") == "unchanged" for r in rows),
        "rejected": sum(r.get("status") == "rejected" for r in rows),
        "comments_observed": comments_observed,
        "comments_empty": comments_empty,
        "expansions_used": expansions_used,
        "unexpanded_controls": len(parsed.expand_controls) if parsed is not None else 0,
        "bytes_used": bytes_used,
    }
    result_data: dict[str, Any] = {
        "rows": rows,
        "egress": egress_summary,
    }
    if byte_limit_error is not None:
        result_data["error"] = byte_limit_error.model_dump(mode="json")
        result_data["inputs"] = [
            {
                "input": 0,
                "code": m.ErrorCode.LIMIT_REACHED.value,
                "message": byte_limit_error.message,
                "field": "max_bytes",
            }
        ]
    return m.JobResult(
        coverage_status=coverage,
        requested_scope=step.job.requested_scope,
        observed_scope=observed_scope,
        stop_reason=stop_reason,
        missing_fields=sorted(missing),
        data=result_data,
    )
