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
- `context.route` applies the bounded URL policy before resource and navigation
  classification, preserving validated addresses for the proxy's pinned dial.
  `Response.server_addr()` remains a secondary check.

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

import asyncio
import hashlib
import ipaddress
import json
import math
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import parse_qs, urlsplit, urlunsplit

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
SANDBOX_DISABLED_WARNING = "Facebook browser sandbox is disabled by operator configuration"
SANDBOX_NEXT_ACTION = (
    "Install an AppArmor profile granting userns to the Playwright Chromium binary "
    "(see docs/dev/FACEBOOK.md), or explicitly set [facebook] allow_unsandboxed_browser = true "
    "with OS isolation after reviewing the documented risk"
)


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
    is_share: bool = False


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
    comments_exhausted: bool = False
    missing_fields: tuple[str, ...] = ()


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
    progress_confirmed: bool = True
    expansion_failed: bool = False
    snapshot_visibility: bool = False


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
        allow_unsandboxed_browser: bool = False,
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


def _observed_sandbox_mode(browser: Any) -> str:
    """Observe running Chromium, not the requested launch options."""
    try:
        cdp = browser.new_browser_cdp_session()
        args = cdp.send("Browser.getBrowserCommandLine")["arguments"]
        if any(arg == "--no-sandbox" or arg.startswith("--no-sandbox=") for arg in args):
            return "disabled_by_operator"
        if sys.platform.startswith("linux"):
            renderers = [
                entry
                for entry in cdp.send("SystemInfo.getProcessInfo")["processInfo"]
                if entry["type"] == "renderer"
            ]
            if not renderers:
                return "unverified"
            for entry in renderers:
                status = Path(f"/proc/{int(entry['id'])}/status").read_text()
                if not re.search(r"^Seccomp:\s*2\s*$", status, re.MULTILINE):
                    return "unverified"
        return "enabled"
    except Exception:
        return "unverified"


@lru_cache(maxsize=8)
def _sandbox_launch_check(executable: str, modified_ns: int, minute: int) -> bool:
    """Bounded, cached local launch only; no navigation or credentials."""
    del modified_ns, minute
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    script = """import sys
from playwright.sync_api import sync_playwright
from metamuse._browser_egress import build_chromium_launch_args
from metamuse.facebook import _observed_sandbox_mode
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=sys.argv[1], headless=True,
        chromium_sandbox=True, timeout=8000,
        args=build_chromium_launch_args(int(sys.argv[2])))
    page = browser.new_context(service_workers="block").new_page()
    page.evaluate("0")
    verified = _observed_sandbox_mode(browser) == "enabled"
    browser.close()
    sys.exit(0 if verified else 1)
"""
    env = {key: value for key, value in os.environ.items() if key not in PROXY_ENV_VARS}
    process = subprocess.Popen(
        [sys.executable, "-c", script, executable, str(port)],
        env=env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        return process.wait(timeout=10) == 0
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        return False


def capability_status(ctx: Any) -> dict[str, dict[str, str]]:
    """Report the metamuse_collect.facebook_post backend capability row."""
    fb_cfg = getattr(getattr(ctx, "policy", None), "facebook", None)
    unsandboxed = bool(getattr(fb_cfg, "allow_unsandboxed_browser", False))
    warnings = getattr(getattr(ctx, "store", None), "open_warnings", None)
    if isinstance(warnings, list):
        if unsandboxed and SANDBOX_DISABLED_WARNING not in warnings:
            warnings.append(SANDBOX_DISABLED_WARNING)
        elif not unsandboxed and SANDBOX_DISABLED_WARNING in warnings:
            warnings.remove(SANDBOX_DISABLED_WARNING)
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
    elif (
        not unsandboxed
        and not (
            _DRIVER_FACTORY() if _DRIVER_FACTORY else PlaywrightBrowserDriver()
        ).sandbox_available()
    ):
        environment = "unavailable"
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

    share = re.fullmatch(r"/share/p/([A-Za-z0-9]{1,64})/", parsed.path)
    if share is not None and "?" not in url and "#" not in url:
        canonical = f"https://www.facebook.com/share/p/{share[1]}/"
        return PostPermalinkTarget(
            original_url=url,
            canonical_url=canonical,
            fetch_url=canonical,
            host=host,
            actor="",
            post_id="",
            scope="",
            is_share=True,
        )
    segments = [seg for seg in parsed.path.split("/") if seg]
    if segments and segments[0].lower() in ("share", "reel", "reels", "watch", "videos", "groups"):
        raise _unsupported_resolution()
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


def _unsupported_resolution() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.UNSUPPORTED,
        "URL is not a supported single Facebook post",
        next_action=(
            "For Reels and videos use the remote-media (B12) path; "
            "group post URLs are unsupported in this round. Supply a single post permalink"
        ),
    )


def parse_resolved_permalink(url: str) -> PostPermalinkTarget:
    parsed = urlsplit(url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) == 3 and segments[1] == "posts":
        url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    target = parse_post_permalink(url)
    if target.is_share:
        raise _unsupported_resolution()
    return target


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
        path = entry.get("path", "/")
        expiry = entry.get("expires", -1)
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in path)
            or isinstance(expiry, bool)
            or not isinstance(expiry, (int, float))
            or not (expiry == -1 or 0 <= expiry <= 253402300799)
            or not math.isfinite(expiry)
            or any(
                key in entry and not isinstance(entry[key], bool) for key in ("httpOnly", "secure")
            )
            or ("sameSite" in entry and entry["sameSite"] not in ("Strict", "Lax", "None"))
            or re.search(r"[\x00-\x20\x7f()<>@,;:\"/\[\]?={}]", name)
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
        ):
            raise m.CoreError(
                m.ErrorCode.AUTH_REQUIRED,
                "Invalid Facebook storage-state cookie",
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
                "path": path,
                "httpOnly": entry.get("httpOnly", True),
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
        try:
            if not isinstance(origin_entry, Mapping):
                raise ValueError
            raw_origin = origin_entry.get("origin")
            if not isinstance(raw_origin, str):
                raise ValueError
            split_origin = urlsplit(raw_origin)
            origin_scheme = split_origin.scheme.lower()
            origin_host = _normalize_host(split_origin.hostname)
            origin_port = split_origin.port  # Parse inside the fixed-error boundary.
            if (
                origin_host is None
                or origin_scheme != "https"
                or origin_port not in (None, 443)
                or split_origin.username is not None
                or split_origin.password is not None
                or split_origin.query
                or split_origin.fragment
                or split_origin.path not in ("", "/")
                or "\\" in raw_origin
                or any(ch.isspace() for ch in raw_origin)
            ):
                raise ValueError
        except (ValueError, UnicodeError):
            raise m.CoreError(
                m.ErrorCode.AUTH_REQUIRED,
                "Invalid Facebook storage-state origin",
                next_action="Import a valid HTTPS Playwright storage state",
            ) from None
        if origin_host not in allowed_hosts:
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
        port_sfx = ""
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
        self.proxy_errors_by_authority: dict[tuple[str, int], m.CoreError] = {}
        self.last_proxy_error: m.CoreError | None = None
        self.navigation_error: m.CoreError | None = None
        self.proxy_port: int | None = None
        self.bytes_relayed: int = 0
        self.byte_limit_exceeded: bool = False
        self.authorized_permalink: PostPermalinkTarget | None = None
        self.resume_identity: PostPermalinkTarget | None = None
        self._route_url_checker = self._check_route_destination
        self._route_checked_by_authority: dict[tuple[str, int], safety.CheckedURL] = {}

    def authorize_permalink(
        self, target: PostPermalinkTarget, expected_resolution: PostPermalinkTarget | None = None
    ) -> None:
        self.authorized_permalink = target
        self.resume_identity = expected_resolution

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
        if target.is_share:
            if re.fullmatch(r"/share/p/[A-Za-z0-9]{1,64}/", urlsplit(url).path):
                observed = parse_post_permalink(url)
                if observed.canonical_url == target.canonical_url:
                    return
                raise m.CoreError(
                    m.ErrorCode.REFERENCE_INVALID,
                    "Facebook navigation changed the approved share link",
                )
            observed = parse_resolved_permalink(url)
            if self.resume_identity is not None and (observed.actor, observed.post_id) != (
                self.resume_identity.actor,
                self.resume_identity.post_id,
            ):
                raise m.CoreError(
                    m.ErrorCode.DATA_STALE,
                    "Facebook share link resolved to a different post on resume",
                    next_action="Review the changed target and approve a new share-link collection",
                )
            self.authorized_permalink = observed
            return
        observed = parse_resolved_permalink(url)
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
            mapped = "https://www.facebook.com" + parsed.path
            if parsed.query:
                mapped += "?" + parsed.query
            if re.fullmatch(r"/share/p/[A-Za-z0-9]{1,64}/", parsed.path):
                parse_post_permalink(mapped)
            else:
                parse_resolved_permalink(mapped)

        guard = cls(
            allowed_hosts=allowed_hosts,
            resolver=resolver,
            _fixture_url_checker=_checker,
            _fixture_nav_validator=_nav_validator,
        )
        guard.allowed_hosts = host_tuple
        return guard

    @staticmethod
    def _classify_navigation_barrier(url: str) -> m.CoreError | None:
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

    def _checked_destination(self, url: str, purpose: safety.URLPurpose) -> safety.CheckedURL:
        authority = self._authority(url)
        with self._lock:
            pinned = self.checked_by_authority.get(
                authority
            ) or self._route_checked_by_authority.get(authority)
        if pinned is not None:
            # Revalidate every URL against the shared policy, while retaining the
            # already validated address set through route -> proxy -> pinned dial.
            checker = safety.check_fixture_url if self._fixture_url_checker else safety.check_url
            return checker(
                url,
                purpose=purpose,
                allowed_hosts=self.allowed_hosts,
                resolver=lambda _: pinned.addresses,
            )
        if self._fixture_url_checker is not None:
            return self._fixture_url_checker(url, purpose)
        return self.policy.before_request(url, purpose=purpose)

    def _check_route_destination(self, url: str, purpose: safety.URLPurpose) -> safety.CheckedURL:
        try:
            if self._fixture_url_checker is None and urlsplit(url).scheme.lower() != "https":
                raise m.CoreError(m.ErrorCode.SCOPE_DENIED, "HTTPS required")
            checked = self._checked_destination(url, purpose)
            with self._lock:
                self._route_checked_by_authority.setdefault(
                    (checked.hostname, checked.port), checked
                )
            return checked
        except (m.CoreError, ValueError):
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Facebook browser destination is denied by egress policy",
            ) from None

    def check_browser_route(
        self,
        url: str,
        *,
        resource_type: str = "document",
        is_navigation: bool = False,
        redirected_from: str | None = None,
    ) -> None:
        """Secondary browser-level check for context.route (resource-type & initial nav shape).

        Destination policy precedes classification. In Chromium the proxy bounds this
        check's DNS work; it also independently checks and pins every actual dial.
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

        try:
            self._route_url_checker(url, purpose)
        except m.CoreError as exc:
            with self._lock:
                if is_navigation:
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
                    if re.fullmatch(r"/share/p/[A-Za-z0-9]{1,64}/", urlsplit(url).path):
                        parse_post_permalink(url)
                    else:
                        parse_resolved_permalink(url)
                self.validate_navigation_identity(url)
            except m.CoreError as exc:
                if (
                    exc.detail.code == m.ErrorCode.UNSUPPORTED
                    and self.authorized_permalink is not None
                    and not self.authorized_permalink.is_share
                ):
                    exc = m.CoreError(
                        m.ErrorCode.CHECKPOINT_REQUIRED,
                        "Facebook navigation left the approved post",
                        next_action=(
                            "Check the account session in a browser before resuming collection"
                        ),
                    )
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
                raise exc from None

    def record_browser_refusal(self, reason: str) -> None:
        assert reason in {"websocket_blocked", "blocked_subframe", "popup_blocked"}
        with self._lock:
            self.decisions.append(
                EgressDecision(
                    hostname="browser",
                    purpose="subresource",
                    resource_type="browser",
                    allowed=False,
                    reason=reason,
                )
            )

    @staticmethod
    def _authority(url: str) -> tuple[str, int] | None:
        try:
            parsed = urlsplit(url)
            host = _normalize_host(parsed.hostname)
            return (host, parsed.port or 443) if host else None
        except ValueError:
            return None

    def navigation_proxy_error(self, urls: Iterable[str]) -> m.CoreError | None:
        # CONNECT has no document path. Only DNS/policy errors for an authority in
        # this document's own request chain can explain its failed connection.
        with self._lock:
            for url in reversed(tuple(urls)):
                error = self.aborted_urls.get(url) or self.proxy_errors_by_authority.get(
                    self._authority(url)
                )
                if error is not None:
                    return error
        return None

    def record_proxy_protocol_refusal(
        self, *, hostname: str, reason: str, url: str | None = None
    ) -> None:
        dns_failure = reason in {
            "resolution_unavailable",
            "resolution_timeout",
            "resolution_failed",
        }
        exc = m.CoreError(
            m.ErrorCode.NETWORK_TIMEOUT if dns_failure else m.ErrorCode.SCOPE_DENIED,
            "Facebook DNS resolution is temporarily unavailable"
            if dns_failure
            else "Proxy refused request",
            next_action="Check DNS resolution and resolver capacity, then retry"
            if dns_failure
            else None,
        )
        with self._lock:
            self.last_proxy_error = exc  # Compatibility diagnostic; never navigation attribution.
            if dns_failure and url is not None:
                authority = self._authority(url)
                if authority is not None:
                    self.proxy_errors_by_authority[authority] = exc
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
            if self._fixture_url_checker is None and not is_connect:
                raise m.CoreError(
                    m.ErrorCode.SCOPE_DENIED,
                    "Plain HTTP is refused by the Facebook browser egress proxy",
                    next_action="Use HTTPS for all Facebook requests",
                )
            checked = self._checked_destination(url, purpose)
        except m.CoreError as exc:
            with self._lock:
                self.last_proxy_error = exc
                self.aborted_urls[url] = exc
                if is_connect and (authority := self._authority(url)) is not None:
                    self.proxy_errors_by_authority[authority] = exc
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
                        if re.fullmatch(r"/share/p/[A-Za-z0-9]{1,64}/", urlsplit(url).path):
                            parse_post_permalink(url)
                        else:
                            parse_resolved_permalink(url)
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
            self.proxy_errors_by_authority.pop((checked.hostname, checked.port), None)
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
            checked = self._checked_destination(url, purpose)
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
            if reason not in {code.value for code in m.ErrorCode} | {
                "max_bytes_exceeded",
                "websocket_blocked",
                "blocked_subframe",
                "popup_blocked",
            }:
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


WEBSOCKET_BLOCK_SCRIPT = """(() => {
  // Ordinary WebSocket is intercepted by the pinned Playwright context route.
  for (const name of ['WebSocketStream']) {
    const original = window[name];
    if (typeof original !== 'function') continue;
    const blocked = function() {
      console.error('__METAMUSE_WS_BLOCKED__');
      throw new DOMException('WebSockets are disabled', 'SecurityError');
    };
    // Playwright's shim escape remains covered by the Chromium network rules.
    if (original._apiPassThrough) blocked._apiPassThrough = original._apiPassThrough.bind(original);
    Object.defineProperty(window, name, {value: blocked, configurable: true, writable: true});
  }
})()"""


def _verify_popup_control(browser: Any, context: Any, page: Any) -> None:
    """Use pristine blank realms, before init scripts and before opening the proxy."""
    try:
        refused = page.evaluate("""() => {
          const direct = window.open('');
          if (direct) direct.close();
          const frame = document.createElement('iframe'); document.body.append(frame);
          const fresh = frame.contentWindow.open('');
          if (fresh) fresh.close();
          frame.remove();
          return direct === null && fresh === null;
        }""")
        cdp = browser.new_browser_cdp_session()
        targets = cdp.send("Target.getTargets")["targetInfos"]
        pages = [target for target in targets if target["type"] == "page"]
        if refused and len(context.pages) == 1 and len(pages) == 1:
            return
    except Exception:
        pass  # Any inconclusive verification refuses the entire browser below.
    raise m.CoreError(
        m.ErrorCode.DEPENDENCY_MISSING,
        "Facebook browser popup creation control is unavailable",
        next_action="Install a Chromium headless build with working new-web-contents blocking",
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
        if proxy is not None:
            egress._route_url_checker = proxy.check_url_bounded
        self._timeout_ms = timeout_ms
        self._test_origin = _test_origin.rstrip("/") if _test_origin else None
        self.popups_blocked = 0
        self.downloads_blocked = 0
        self.workers_blocked = 0
        self._transport_error: m.CoreError | None = None
        self._peer_error: m.CoreError | None = None
        self._navigation_urls: list[str] = []
        self._navigation_responded: set[str] = set()
        self._document_status = 200
        self._expansion_response_status: int | None = None
        self._active_control: str | None = None
        self._pending_expansion_urls: list[str] = []
        self._expansion_requests: set[Any] = set()
        self._closing_targets: set[str] = set()

        def _on_route(route: Any) -> None:
            req = route.request
            main_navigation = req.is_navigation_request() and req.frame == self._page.main_frame
            if req.is_navigation_request() and not main_navigation:
                self._egress.record_browser_refusal("blocked_subframe")
                self._finish_route(route, abort=True)
                return
            redirected_from = req.redirected_from.url if req.redirected_from else None
            if main_navigation:
                self._navigation_urls.append(req.url)
            try:
                self._egress.check_browser_route(
                    req.url,
                    resource_type=req.resource_type,
                    is_navigation=main_navigation,
                    redirected_from=redirected_from,
                )
            except m.CoreError:
                self._finish_route(route, abort=True)
                return
            self._finish_route(route, abort=False)

        def note_navigation(request: Any) -> None:
            if request.is_navigation_request() and request.frame == self._page.main_frame:
                self._navigation_urls.append(request.url)

        self._page.on("request", note_navigation)

        def note_expansion(source: Any, payload: Any) -> None:
            if (
                source.get("frame") == self._page.main_frame
                and isinstance(payload, dict)
                and self._active_control is not None
                and payload.get("control") == self._active_control
                and isinstance(payload.get("url"), str)
                and len(self._pending_expansion_urls) < 256
            ):
                self._pending_expansion_urls.append(payload["url"])

        def request_started(request: Any) -> None:
            if request.url in self._pending_expansion_urls:
                self._pending_expansion_urls.remove(request.url)
                self._expansion_requests.add(request)
            elif request.redirected_from in self._expansion_requests:
                self._expansion_requests.add(request)

        self._context.expose_binding("__metamuseExpansionRequest", note_expansion)
        self._context.add_init_script(WEBSOCKET_BLOCK_SCRIPT)
        self._context.add_init_script("""(() => {
          const note = url => {
            const event = window.event;
            const control = event?.target?.closest?.('[data-expand-id]');
            if (event?.isTrusted && event.type === 'click' && control) {
              try { window.__metamuseExpansionRequest({control: control.dataset.expandId,
                url: new URL(url, document.baseURI).href}); } catch (_) {}
            }
          };
          const originalFetch = window.fetch;
          window.fetch = function(input, ...args) {
            note(typeof input === 'string' || input instanceof URL ? input : input.url);
            return originalFetch.call(this, input, ...args);
          };
          const urls = new WeakMap(), open = XMLHttpRequest.prototype.open,
            send = XMLHttpRequest.prototype.send;
          XMLHttpRequest.prototype.open = function(method, url, ...args) {
            urls.set(this, url); return open.call(this, method, url, ...args);
          };
          XMLHttpRequest.prototype.send = function(...args) {
            note(urls.get(this)); return send.apply(this, args);
          };
        })()""")
        self._page.on("request", request_started)

        def _on_response(response: Any) -> None:
            req = response.request
            main_navigation = req.is_navigation_request() and req.frame == self._page.main_frame
            if main_navigation:
                self._document_status = response.status
            with self._egress._lock:
                aborted_err = self._egress.aborted_urls.get(response.url)
            aborted_by_proxy = (
                response.headers.get("x-metamuse-proxy-aborted") == "1" or aborted_err is not None
            )
            if aborted_by_proxy:
                if main_navigation and self._egress.navigation_error is None:
                    self._egress.navigation_error = (
                        aborted_err
                        or self._egress.navigation_proxy_error([req.url])
                        or m.CoreError(
                            m.ErrorCode.SCOPE_DENIED,
                            "Outbound navigation blocked by egress proxy",
                        )
                    )
                return
            if main_navigation:
                self._navigation_responded.add(req.url)
            if (
                req in self._expansion_requests
                and req.resource_type in ("xhr", "fetch")
                and response.status >= 400
            ):
                self._expansion_response_status = response.status
            purpose: safety.URLPurpose = (
                "request"
                if main_navigation
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
                if main_navigation and self._peer_error is None:
                    self._peer_error = exc

        def _on_frame_navigated(frame: Any) -> None:
            if frame == self._page.main_frame and self._egress.navigation_error is None:
                try:
                    self._egress.check_browser_route(frame.url, is_navigation=True)
                except m.CoreError as exc:
                    self._egress.navigation_error = exc

        async def _on_web_socket(route: Any) -> None:
            self._egress.record_browser_refusal("websocket_blocked")
            # Playwright 1.63 invokes WebSocket handlers directly on its asyncio loop.
            # Await the pinned implementation; a nested sync close deadlocks that loop.
            try:
                await route._impl_obj.close(code=1008, reason="WebSockets are disabled")
            except Exception:
                self._guard_failed()  # A lost close reply cannot prove the socket is gone.

        self._context.on(
            "console",
            lambda message: (
                self._egress.record_browser_refusal("websocket_blocked")
                if message.text == "__METAMUSE_WS_BLOCKED__"
                else None
            ),
        )
        self._context.on("page", self._on_popup)
        self._page.on("popup", self._on_popup)
        self._page.on("download", self._on_download)
        self._context.route("**/*", _on_route)
        self._context.route_web_socket("**/*", _on_web_socket)
        self._page.on("response", _on_response)
        self._page.on("framenavigated", _on_frame_navigated)
        # Playwright WebSocket routes do not cover worker sockets in the pinned build.
        # Pause every related worker before its script starts, then terminate it.
        self._worker_cdp = browser.new_browser_cdp_session()
        page_cdp = context.new_cdp_session(page)
        self._network_cdp = page_cdp
        page_cdp.send("Network.enable")
        page_cdp.send("Network.setBlockedURLs", {"urls": ["ws://*", "wss://*"]})
        rules = [
            {
                "urlPattern": scheme + "://*:*/*",
                "offline": True,
                "latency": 0,
                "downloadThroughput": -1,
                "uploadThroughput": -1,
            }
            for scheme in ("ws", "wss")
        ]
        page_cdp.send("Network.emulateNetworkConditionsByRule", {"matchedNetworkConditions": rules})
        page_cdp.send("Page.enable")
        page_cdp.on("Page.windowOpen", lambda _: self._note_popup_attempt())
        page_cdp.on("Network.webSocketCreated", self._unexpected_websocket)

        page_cdp.on("Network.webSocketHandshakeResponseReceived", self._unexpected_websocket)
        page_target = page_cdp.send("Target.getTargetInfo")["targetInfo"]["targetId"]

        self._worker_cdp.on("Target.attachedToTarget", self._on_worker_attached)
        # Shared workers have no page opener relation. Watch browser-level targets too.
        self._shared_worker_cdp = browser.new_browser_cdp_session()
        self._shared_worker_cdp.on("Target.attachedToTarget", self._on_worker_attached)
        self._shared_worker_cdp.send(
            "Target.setAutoAttach",
            {
                "autoAttach": True,
                "waitForDebuggerOnStart": True,
                "flatten": True,
                "filter": [{"type": "shared_worker"}, {"exclude": True}],
            },
        )
        self._worker_cdp.send(
            "Target.autoAttachRelated",
            {
                "targetId": page_target,
                "waitForDebuggerOnStart": True,
                "filter": [{"type": kind} for kind in ("worker", "shared_worker", "service_worker")]
                + [{"exclude": True}],
            },
        )

        # Browser-wide page attachment pauses foreign targets before their first
        # script. The headless switch normally prevents their creation entirely.
        self._page_target_cdp = browser.new_browser_cdp_session()

        self._target_network_channels: list[Any] = []

        def page_attached(event: dict[str, Any]) -> None:
            info = event["targetInfo"]
            try:
                channel = browser.new_browser_cdp_session()
                self._target_network_channels.append(channel)
                attached = channel.send(
                    "Target.attachToTarget",
                    {
                        "targetId": info["targetId"],
                        "flatten": False,
                    },
                )
                channel.on("Target.receivedMessageFromTarget", target_message)
                channel.send(
                    "Target.sendMessageToTarget",
                    {
                        "sessionId": attached["sessionId"],
                        "message": json.dumps({"id": 1, "method": "Network.enable"}),
                    },
                )
            except Exception:
                if info["targetId"] == page_target:
                    self._guard_failed()
                else:
                    self._closing_targets.add(info["targetId"])
            if info["targetId"] != page_target:
                if info["type"] == "page":
                    self._note_popup_attempt()
                else:
                    self._egress.record_browser_refusal("blocked_subframe")
                if event.get("waitingForDebugger") is not True:
                    self._guard_failed()
                self._close_target(info["targetId"])

        def target_message(event: dict[str, Any]) -> None:
            try:
                message = json.loads(event["message"])
                if "error" in message:
                    self._guard_failed()
                method = message.get("method")
            except (ValueError, KeyError, TypeError):
                self._guard_failed()
                return
            if method in ("Network.webSocketCreated", "Network.webSocketHandshakeResponseReceived"):
                self._unexpected_websocket(event)
            elif method == "Page.windowOpen":
                self._note_popup_attempt()

        self._page_target_cdp.on("Target.attachedToTarget", page_attached)
        self._page_target_cdp.on("Target.receivedMessageFromTarget", target_message)
        self._page_target_cdp.send(
            "Target.setAutoAttach",
            {
                "autoAttach": True,
                "waitForDebuggerOnStart": True,
                "flatten": True,
                "filter": [{"type": "page"}, {"type": "iframe"}, {"exclude": True}],
            },
        )

        # The sync Playwright API pumps this pinned implementation loop during
        # goto/click/evaluate. Proxy and browser guard latches must interrupt the
        # page while navigation is awaiting its full production read timeout.
        self._byte_watch = page._impl_obj._loop.create_task(self._watch_byte_limit())

    async def _watch_byte_limit(self) -> None:
        while not self._page.is_closed():
            if (
                self._transport_error is not None
                or self._egress.byte_limit_exceeded
                or (self._proxy is not None and self._proxy.byte_limit_exceeded)
            ):
                try:
                    await self._page._impl_obj.close()
                except Exception:
                    try:
                        gone = self._browser.is_connected() and self._page.is_closed()
                    except Exception:
                        gone = False
                    if not gone:
                        self._guard_failed()
                return
            await asyncio.sleep(0.05)

    def _note_popup_attempt(self) -> None:
        self.popups_blocked += 1
        self._egress.record_browser_refusal("popup_blocked")

    def _unexpected_websocket(self, _: Any) -> None:
        self._egress.record_browser_refusal("websocket_blocked")
        self._transport_error = m.CoreError(
            m.ErrorCode.PROTOCOL_MISMATCH,
            "Facebook WebSocket handshake bypassed browser blocking",
            next_action="Stop collection and inspect the installed Chromium network controls",
        )

    def _finish_route(self, route: Any, *, abort: bool) -> None:
        try:
            if abort:
                route.abort("blockedbyclient")
            else:
                route.continue_()
        except Exception:
            try:
                gone = route.request.failure is not None or (
                    self._browser.is_connected() and self._page.is_closed()
                )
            except Exception:
                gone = False
            if not gone:
                self._guard_failed()

    def _guard_failed(self) -> None:
        if self._transport_error is None:
            self._transport_error = m.CoreError(
                m.ErrorCode.PROTOCOL_MISMATCH,
                "Facebook browser guard could not verify target shutdown",
                next_action="Stop collection and inspect the installed Chromium target controls",
            )

    def _target_gone(self, target_id: str) -> bool:
        try:
            if not self._browser.is_connected():
                return False
            targets = self._worker_cdp.send("Target.getTargets")["targetInfos"]
            return not any(target["targetId"] == target_id for target in targets)
        except Exception:
            return False

    def _close_target(self, target_id: str) -> None:
        # Both a positive ACK and a refusal may precede asynchronous destruction.
        # Never resume source while waiting; the worker remains debugger-paused.
        self._closing_targets.add(target_id)
        try:
            if not self._browser.is_connected():
                self._guard_failed()
                return
            self._worker_cdp.send("Target.closeTarget", {"targetId": target_id})
        except Exception:
            # A lost reply is inconclusive, not proof of a live or dead target.
            # The common bounded verifier must establish disappearance.
            if not self._browser.is_connected():
                self._guard_failed()

    def _verify_target_shutdown(self) -> None:
        # One deadline covers the original targets and attachments delivered by CDP
        # while polling. Source remains debugger-paused; this path never resumes it.
        deadline = time.monotonic() + 5
        try:
            while self._closing_targets:
                if not self._browser.is_connected():
                    self._guard_failed()
                    return
                for target in list(self._closing_targets):
                    if self._target_gone(target):
                        self._closing_targets.discard(target)
                if not self._closing_targets:
                    return
                if time.monotonic() >= deadline:
                    self._guard_failed()
                    return
                if self._page.is_closed():
                    self._worker_cdp._sync(asyncio.sleep(0.05))
                else:
                    self._page.wait_for_timeout(50)
        except Exception:
            self._guard_failed()

    def _on_worker_attached(self, event: dict[str, Any]) -> None:
        info = event["targetInfo"]
        if info["type"] not in ("worker", "shared_worker", "service_worker"):
            return
        self.workers_blocked += 1
        self._egress.record_proxy_protocol_refusal(hostname="proxy", reason="worker")
        if event.get("waitingForDebugger") is not True:
            self._guard_failed()
        # Target.closeTarget alone leaves dedicated workers alive in the pinned build.
        # Evaluate only our fixed self.close() in the debugger-paused realm; never
        # resume the worker's source. Shared workers support closeTarget directly.
        if info["type"] == "worker":
            try:
                channel = self._browser.new_browser_cdp_session()
                attached = channel.send(
                    "Target.attachToTarget",
                    {
                        "targetId": info["targetId"],
                        "flatten": False,
                    },
                )
                channel.send(
                    "Target.sendMessageToTarget",
                    {
                        "sessionId": attached["sessionId"],
                        "message": json.dumps(
                            {
                                "id": 1,
                                "method": "Runtime.evaluate",
                                "params": {"expression": "self.close()"},
                            }
                        ),
                    },
                )
            except Exception:
                # It may have disappeared while attaching/evaluating. The common
                # close path verifies that asynchronously, without any resume.
                self._closing_targets.add(info["targetId"])
        self._close_target(info["targetId"])

    def _on_popup(self, popup: Any) -> None:
        self._note_popup_attempt()
        target_id = None
        try:
            target_id = self._context.new_cdp_session(popup).send("Target.getTargetInfo")[
                "targetInfo"
            ]["targetId"]
        except Exception:
            pass  # Closed pages can no longer be attached; liveness is checked below.
        try:
            popup.close()
        except Exception:
            try:
                gone = self._browser.is_connected() and popup.is_closed()
            except Exception:
                gone = False
            if not gone:
                if target_id is not None:
                    self._closing_targets.add(target_id)
                else:
                    self._guard_failed()

    def _on_download(self, download: Any) -> None:
        self.downloads_blocked += 1
        try:
            download.cancel()
        except Exception:
            # A disconnected CDP transport does not prove a download is gone.
            # Download.failure() can itself wait for the production read timeout.
            self._guard_failed()

    def _raise_if_fatal_stop(self) -> None:
        self._verify_target_shutdown()
        if self._transport_error is not None:
            raise self._transport_error

        # More specific fatal outcomes outrank a socket/wait interrupted by cutoff.
        def priority(error: m.CoreError) -> int:
            code = error.detail.code
            if code == m.ErrorCode.SCOPE_DENIED:
                return 0
            if code in {
                m.ErrorCode.AUTH_REQUIRED,
                m.ErrorCode.ACCESS_DENIED,
                m.ErrorCode.CHECKPOINT_REQUIRED,
                m.ErrorCode.RATE_LIMITED,
            }:
                return (
                    2 if error.detail.message == "Facebook navigation left the approved post" else 1
                )
            if code == m.ErrorCode.REFERENCE_INVALID:
                return 2
            return 3

        proxy_error = self._egress.navigation_proxy_error(
            url for url in self._navigation_urls if url not in self._navigation_responded
        )
        status_error = None
        try:
            _raise_observation_barrier(self._document_status, None)
        except m.CoreError as exc:
            status_error = exc
        errors = [
            error
            for error in (
                self._egress.navigation_error,
                self._peer_error,
                proxy_error,
                status_error,
            )
            if error is not None and priority(error) < 3
        ]
        if status_error is not None and status_error.detail.code == m.ErrorCode.PROTOCOL_MISMATCH:
            errors.append(status_error)  # An observed HTTP failure is not a cutoff interruption.
        if errors:
            raise min(errors, key=priority)

    def _raise_if_byte_limit(self) -> None:
        self._raise_if_fatal_stop()
        if self._egress.byte_limit_exceeded or (
            self._proxy is not None and self._proxy.byte_limit_exceeded
        ):
            raise m.CoreError(
                m.ErrorCode.LIMIT_REACHED,
                "Facebook page response exceeded max_bytes budget",
                next_action="Increase limits.max_bytes or narrow the requested resources",
            )

    def _snapshot_html(self) -> str:
        return self._page.evaluate("""() => {
          const copy = document.documentElement.cloneNode(true);
          const source = [document.documentElement,
            ...document.documentElement.querySelectorAll('*')];
          const clones = [copy, ...copy.querySelectorAll('*')];
          source.forEach((node, index) => {
            const clone = clones[index];
            for (const attr of [...clone.attributes]) {
              if (attr.name.toLowerCase().startsWith('data-metamuse-')) {
                clone.removeAttribute(attr.name);
              }
            }
            const style = getComputedStyle(node);
            if (style.display === 'none') {
              clone.setAttribute('data-metamuse-display-none', '');
            }
            clone.setAttribute('data-metamuse-visibility', style.visibility);
          });
          return copy.outerHTML;
        }""")

    def goto_post(self, url: str) -> PageObservation:
        try:
            self._raise_if_byte_limit()
            return self._goto_post(url)
        except m.CoreError:
            self._raise_if_fatal_stop()
            raise
        except Exception:
            self._raise_if_byte_limit()
            raise

    def _goto_post(self, url: str) -> PageObservation:
        target_url = url
        if self._test_origin is not None:
            parsed = urlsplit(url)
            suffix = f"?{parsed.query}" if parsed.query else ""
            target_url = f"{self._test_origin}{parsed.path}{suffix}"
        self._egress.navigation_error = None
        self._egress.last_proxy_error = None
        self._navigation_urls = []
        self._navigation_responded.clear()
        with self._egress._lock:
            self._egress.aborted_urls.clear()
            self._egress.proxy_errors_by_authority.clear()
        self._peer_error = None
        try:
            response = self._page.goto(target_url, wait_until="load", timeout=self._timeout_ms)
        except Exception as exc:
            self._raise_if_byte_limit()
            if self._egress.navigation_error is not None:
                raise self._egress.navigation_error from None
            if self._peer_error is not None:
                raise self._peer_error from None
            navigation_error = self._egress.navigation_proxy_error(
                url
                for url in (self._navigation_urls or [target_url])
                if url not in self._navigation_responded
            )
            if navigation_error is not None:
                raise navigation_error from None
            raise m.CoreError(
                m.ErrorCode.NETWORK_TIMEOUT,
                "Browser navigation to Facebook post permalink failed",
                next_action="Verify connectivity and the post permalink before retrying",
            ) from exc
        # Dispatch immediate script/meta navigations before the first DOM snapshot.
        self._page.wait_for_timeout(50)
        resolution_deadline = time.monotonic() + self._timeout_ms / 1000
        resolving_share = (
            re.fullmatch(r"/share/p/[A-Za-z0-9]{1,64}/", urlsplit(url).path) is not None
        )
        while (
            resolving_share
            and re.fullmatch(r"/share/p/[A-Za-z0-9]{1,64}/", urlsplit(self._page.url).path)
            is not None
            and self._egress.navigation_error is None
            and time.monotonic() < resolution_deadline
        ):
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
            html=self._snapshot_html(),
            snapshot_visibility=True,
            server_addr=response.server_addr(),
        )

    def expand_next(self, control_id: str) -> PageObservation:
        try:
            self._raise_if_byte_limit()
            return self._expand_next(control_id)
        except m.CoreError:
            self._raise_if_fatal_stop()
            raise
        except Exception:
            self._raise_if_byte_limit()
            raise

    def _expand_next(self, control_id: str) -> PageObservation:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", control_id):
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid expand control identifier")
        self._egress.navigation_error = None
        self._egress.last_proxy_error = None
        self._peer_error = None
        self._expansion_response_status = None
        self._active_control = control_id
        self._pending_expansion_urls.clear()
        self._expansion_requests.clear()
        signature = """() => Array.from(document.querySelectorAll(
          '[data-fb-role="comment"], [data-fb-role="reply"], [data-comment-id]'))
          .map(node => JSON.stringify([node.getAttribute('data-comment-id'), node.textContent]))"""
        before = set(self._page.evaluate(signature))
        locator = self._page.locator(f'[data-expand-id="{control_id}"]').first
        try:
            locator.click(timeout=self._timeout_ms)
        except Exception:
            if self._egress.navigation_error is not None:
                raise self._egress.navigation_error from None
            self._active_control = None
            return PageObservation(
                url=self._page.url,
                status_code=self._document_status,
                html=self._snapshot_html(),
                snapshot_visibility=True,
                progress_confirmed=False,
                expansion_failed=True,
            )
        deadline = time.monotonic() + self._timeout_ms / 1000
        progress = False
        while time.monotonic() < deadline:
            self._page.wait_for_timeout(50)
            self._raise_if_byte_limit()
            if self._egress.navigation_error is not None:
                raise self._egress.navigation_error
            if self._peer_error is not None:
                raise self._peer_error
            self._egress.check_browser_route(self._page.url, is_navigation=True)
            if self._expansion_response_status is not None:
                progress = True  # Classified failure response, never an empty-data inference.
                break
            if set(self._page.evaluate(signature)) - before:
                progress = True
                break
        self._active_control = None
        return PageObservation(
            url=self._page.url,
            status_code=self._document_status,
            html=self._snapshot_html(),
            snapshot_visibility=True,
            server_addr=None,
            progress_confirmed=progress,
            expansion_failed=self._expansion_response_status is not None,
        )

    def close(self) -> None:
        # Verify before tearing down CDP, even if cutoff already closed the page.
        try:
            self._verify_target_shutdown()
        except Exception:
            self._guard_failed()
        self._byte_watch.cancel()
        for obj in (self._page, self._context, self._browser):
            try:
                obj.close()
            except Exception:
                try:
                    gone = (
                        obj is self._page
                        and self._browser.is_connected()
                        and self._page.is_closed()
                    )
                except Exception:
                    gone = False
                if not gone:
                    self._guard_failed()
        try:
            self._pw.stop()
        except Exception:
            self._guard_failed()
        if self._proxy is not None:
            try:
                self._proxy.close()
            except Exception:
                self._guard_failed()
        if self._transport_error is not None:
            raise self._transport_error


class PlaywrightBrowserDriver:
    """Thin Playwright Chromium implementation behind BrowserDriver."""

    def __init__(
        self, *, _test_origin: str | None = None, _test_executable: Path | None = None
    ) -> None:
        self._test_origin = _test_origin
        self._test_executable = _test_executable

    def is_available(self) -> bool:
        return find_playwright_browser() is not None

    def sandbox_available(self) -> bool:
        executable = self._test_executable or find_playwright_browser()
        if executable is None:
            return False
        try:
            return _sandbox_launch_check(
                str(executable), executable.stat().st_mtime_ns, int(time.monotonic() // 60)
            )
        except OSError:
            return False

    def open_session(
        self,
        storage_state: SanitizedStorageState,
        egress: EgressGuard,
        *,
        timeout_ms: int,
        max_bytes: int | None = None,
        allow_unsandboxed_browser: bool = False,
    ) -> BrowserSession:
        refuse_proxies()
        executable = self._test_executable or find_playwright_browser()
        if executable is None:
            ensure_browser_available()
        from playwright.sync_api import sync_playwright

        idle_timeout_s = max(1.0, float(timeout_ms) / 1000.0)
        proxy = BrowserEgressProxy(
            egress,
            max_bytes=max_bytes,
            idle_timeout_s=idle_timeout_s,
            start_closed=True,
        )
        clean_env = {k: v for k, v in os.environ.items() if k not in PROXY_ENV_VARS}
        pw: Any = None
        browser: Any = None
        stage = "startup"
        try:
            pw = sync_playwright().start()
            stage = "launch"
            browser = pw.chromium.launch(
                executable_path=str(executable) if executable else None,
                headless=True,
                chromium_sandbox=not allow_unsandboxed_browser,
                timeout=min(timeout_ms, 10_000),
                env=clean_env,
                args=build_chromium_launch_args(proxy.port),
            )
            stage = "context"
            context = browser.new_context(
                storage_state=storage_state.as_playwright_dict(),  # type: ignore[arg-type]
                service_workers="block",
                accept_downloads=False,
                java_script_enabled=True,
            )
            stage = "page"
            page = context.new_page()
            stage = "verification"
            mode = _observed_sandbox_mode(browser)
            if mode != "enabled" and not allow_unsandboxed_browser:
                raise m.CoreError(
                    m.ErrorCode.DEPENDENCY_MISSING,
                    "Facebook browser sandbox could not be verified",
                    next_action=(
                        "Check Chromium command-line and Linux renderer Seccomp state "
                        "before retrying"
                    ),
                )
            _verify_popup_control(browser, context, page)
            stage = "controls"
            session = _PlaywrightSession(
                pw,
                browser,
                context,
                page,
                egress,
                proxy=proxy,
                timeout_ms=timeout_ms,
                _test_origin=self._test_origin,
            )
            session._raise_if_byte_limit()
            session.browser_sandbox = mode
            proxy.start()
            return session
        except Exception as exc:
            proxy.close()
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass
            if isinstance(exc, m.CoreError):
                raise exc from None
            if stage == "context":
                raise m.CoreError(
                    m.ErrorCode.AUTH_REQUIRED,
                    "Facebook browser context rejected authentication state",
                    next_action="Re-import a valid Playwright storage_state JSON file",
                ) from None
            raise m.CoreError(
                m.ErrorCode.DEPENDENCY_MISSING,
                "Facebook browser could not start with the required browser controls",
                next_action=SANDBOX_NEXT_ACTION
                if stage == "launch" and not allow_unsandboxed_browser
                else (
                    "Check the installed Playwright/Chromium versions and browser controls "
                    "before retrying"
                ),
            ) from None


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

    def sandbox_available(self) -> bool:
        return self.available  # Synthetic driver; no launch or LIVE verification.

    def open_session(
        self,
        storage_state: SanitizedStorageState,
        egress: EgressGuard,
        *,
        timeout_ms: int,
        max_bytes: int | None = None,
        allow_unsandboxed_browser: bool = False,
    ) -> BrowserSession:
        del timeout_ms, max_bytes
        refuse_proxies()
        if not self.available:
            ensure_browser_available()
        self.last_cookie_hosts = storage_state.cookie_hosts
        session = _FixtureSession(self, egress)
        session.browser_sandbox = "disabled_by_operator" if allow_unsandboxed_browser else "enabled"
        return session


class _FixtureSession:
    def __init__(self, driver: FixtureBrowserDriver, egress: EgressGuard) -> None:
        self._driver = driver
        self._egress = egress
        self._seq_index = 0

    def _run_observation(self, obs: PageObservation, *, request_url: str) -> PageObservation:
        obs = replace(obs, snapshot_visibility=False)
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


def _inline_visibility_property(style: str, name: str) -> str | None:
    # Fixtures cover inline display/visibility, with CSS declaration order and
    # !important precedence. Chromium snapshots supply computed values instead.
    values = (
        "visible|hidden|collapse|inherit|initial|unset"
        if name == "visibility"
        else "none|block|inline|inline-block|flex|inline-flex|grid|inline-grid|contents|"
        "flow-root|list-item|table|table-row|table-cell|inherit|initial|unset"
    )
    declarations = re.findall(
        rf"(?:^|;)\s*{name}\s*:\s*({values})\s*(!important)?\s*(?=;|$)", style
    )
    important = [entry for entry in declarations if entry[1]]
    return (important or declarations)[-1][0] if declarations else None


class _DOMExtractor(HTMLParser):
    """Lightweight stdlib HTML parser for synthetic and Facebook post permalink DOMs."""

    _VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

    def __init__(self, *, snapshot_visibility: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self._snapshot_visibility = snapshot_visibility
        self._visibility_stack: list[bool] = []
        self.meta_state: str | None = None
        self.json_scripts: list[str] = []
        self._in_json_script = False
        self._hidden_depth = 0
        self._json_buf: list[str] = []
        self.post_attrs: dict[str, str] | None = None
        self.post_text_parts: list[str] = []
        self.post_body_present = False
        self._in_post_body = 0
        self.attachments: list[dict[str, str]] = []
        self.comments_section_present = False
        self.comments_exhausted = False
        self.count_gaps: set[str] = set()
        self.comments: list[dict[str, Any]] = []
        self._current_comment: dict[str, Any] | None = None
        self._in_comment_body = 0
        self.expand_controls: list[ExpandControl] = []
        self._stack: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        # Stop evidence is independent of rendering, including head/void elements.
        states = [attr_map.get("data-fb-state"), attr_map.get("data-page-state")]
        if tag == "form" and (
            attr_map.get("id") == "login_form" or attr_map.get("data-testid") == "royal_login_form"
        ):
            states.append("login_wall")
        if attr_map.get("data-checkpoint") == "true" or attr_map.get("id") == "checkpoint_Form":
            states.append("checkpoint")
        # Match classification priority; later benign state cannot erase a stop.
        priority = {
            "rate_limited": 0,
            "checkpoint": 1,
            "challenge": 1,
            "login_wall": 2,
            "access_denied": 3,
        }
        for raw_state in states:
            state = (raw_state or "").strip().lower()
            if priority.get(state, 99) < priority.get(self.meta_state, 99):
                self.meta_state = state
        # Structural JSON evidence is independent of rendered-body visibility.
        if (
            tag == "script"
            and attr_map.get("type", "").lower() == "application/json"
            and "data-fb-post-payload" in attr_map
        ):
            self._in_json_script = True
            self._json_buf = []
            return
        if self._hidden_depth:
            if tag not in self._VOID_TAGS:
                self._hidden_depth += 1
            return
        style = attr_map.get("style", "").lower()
        hidden = (
            "hidden" in attr_map
            or (self._snapshot_visibility and "data-metamuse-display-none" in attr_map)
            or tag in {"head", "meta"}
            or (tag == "input" and attr_map.get("type", "").lower() == "hidden")
            or attr_map.get("aria-hidden", "").lower() == "true"
            or (
                not self._snapshot_visibility
                and _inline_visibility_property(style, "display") == "none"
            )
        )
        if hidden or tag in {"script", "style", "template"}:
            if tag not in self._VOID_TAGS:
                self._hidden_depth = 1
            return
        # CSS visibility inherits but can be overridden by a descendant. Unlike
        # display/hidden/aria-hidden, it must never suppress traversal of a subtree.
        visibility = self._visibility_stack[-1] if self._visibility_stack else True
        if self._snapshot_visibility:
            computed = attr_map.get("data-metamuse-visibility")
            if computed is not None:
                visibility = computed == "visible"
        else:
            value = _inline_visibility_property(style, "visibility")
            if value is not None and value not in {"inherit", "unset"}:
                visibility = value in {"visible", "initial"}
        if tag not in self._VOID_TAGS:
            self._visibility_stack.append(visibility)
        # Void elements still carry barrier and source metadata; only body depth
        # and stack tracking must exclude them.
        if visibility and attr_map.get("data-comments-exhausted") == "true":
            self.comments_exhausted = True
        if (
            visibility
            and attr_map.get("data-fb-role", "").lower() == "attachment"
            and "data-attachment-url" in attr_map
        ):
            self.attachments.append(
                {
                    "url": attr_map["data-attachment-url"],
                    "kind": attr_map.get("data-attachment-kind", "image"),
                }
            )
        if tag in self._VOID_TAGS:
            if not visibility:
                return
            if tag == "img" and "alt" in attr_map:
                if self._in_post_body:
                    self.post_body_present = True
                    self.post_text_parts.append(attr_map["alt"])
                if self._in_comment_body and self._current_comment is not None:
                    self._current_comment["visible"] = True
                    self._current_comment["body_present"] = True
                    self._current_comment["text_parts"].append(attr_map["alt"])
            if tag == "br":
                if self._in_post_body:
                    self.post_text_parts.append("\n")
                if self._in_comment_body and self._current_comment is not None:
                    self._current_comment["text_parts"].append("\n")
            return
        if visibility and tag in {
            "div",
            "p",
            "section",
            "article",
            "li",
            "blockquote",
            "pre",
            "h1",
            "h2",
            "h3",
        }:
            self._block_boundary()
        self._stack.append((tag.lower(), attr_map))

        role = attr_map.get("role", "").lower()
        fb_role = attr_map.get("data-fb-role", "").lower()

        if fb_role == "post" or (role == "article" and "data-post-id" in attr_map):
            if self.post_attrs is None:
                self.post_attrs = dict(attr_map)
        if fb_role == "post-body" or attr_map.get("data-ad-preview") == "message":
            self.post_body_present |= visibility
            self._in_post_body += 1
        elif self._in_post_body > 0:
            self._in_post_body += 1

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
                "native_id": _native_token(attr_map.get("data-comment-id")),
                "parent_native_id": _native_token(attr_map.get("data-parent-comment-id")),
                "author_display": attr_map.get("data-author") or None,
                "created_at_raw": attr_map.get("data-created-at") or None,
                "permalink": attr_map.get("data-permalink") or None,
                "mention_tag": attr_map.get("data-mention") or None,
                "text_parts": [],
                "body_present": False,
                "visible": visibility,
            }
            self.comments.append(self._current_comment)

        if self._current_comment is not None and (
            fb_role == "comment-body" or attr_map.get("data-comment-body") is not None
        ):
            self._current_comment["visible"] |= visibility
            self._current_comment["body_present"] |= visibility
            self._in_comment_body += 1
        elif self._in_comment_body > 0:
            self._in_comment_body += 1

        expand_kind = attr_map.get("data-expand-kind", "").lower()
        if visibility and expand_kind in ("more_comments", "more_replies"):
            self.comments_section_present = True
            control_id = attr_map.get("data-expand-id") or f"expand_{len(self.expand_controls) + 1}"
            hint_raw = attr_map.get("data-remaining-count")
            hint = _opt_int(hint_raw)
            if hint_raw is not None and hint is None:
                self.count_gaps.add("remaining_count_unparsed")
            self.expand_controls.append(
                ExpandControl(
                    control_id=control_id,
                    kind="more_replies" if expand_kind == "more_replies" else "more_comments",
                    parent_comment_id=_native_token(attr_map.get("data-parent-comment-id")),
                    remaining_hint=hint,
                )
            )

    def handle_endtag(self, tag: str) -> None:
        if self._in_json_script and tag.lower() == "script":
            self._in_json_script = False
            self.json_scripts.append("".join(self._json_buf))
            self._json_buf = []
            return
        if self._hidden_depth:
            if tag.lower() not in self._VOID_TAGS:
                self._hidden_depth -= 1
            return
        visibility = self._visibility_stack.pop() if self._visibility_stack else True
        if visibility and tag.lower() in {
            "div",
            "p",
            "section",
            "article",
            "li",
            "blockquote",
            "pre",
            "h1",
            "h2",
            "h3",
        }:
            self._block_boundary(nested_only=True)
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

    def _block_boundary(self, *, nested_only: bool = False) -> None:
        if self._in_post_body > (1 if nested_only else 0):
            if self.post_text_parts and not self.post_text_parts[-1].endswith("\n"):
                self.post_text_parts.append("\n")
        if self._in_comment_body > (1 if nested_only else 0) and self._current_comment is not None:
            parts = self._current_comment["text_parts"]
            if parts and not parts[-1].endswith("\n"):
                parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self._VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._in_json_script:
            self._json_buf.append(data)
            return
        if self._hidden_depth or (self._visibility_stack and not self._visibility_stack[-1]):
            return
        if self._in_post_body > 0:
            self.post_body_present = True
            self.post_text_parts.append(data)
        if self._in_comment_body > 0 and self._current_comment is not None:
            self._current_comment["visible"] = True
            self._current_comment["body_present"] = True
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


def _native_token(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value).strip()
    return text if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", text) else None


def _opt_int(val: Any) -> int | None:
    # Page-controlled counts are bounded ASCII decimals, never Unicode isdigit().
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val if 0 <= val < 10**18 else None
    if isinstance(val, str) and re.fullmatch(r"[0-9]{1,18}", val.strip()):
        return int(val.strip())
    return None


def _count_gaps(values: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(f"{name}_unparsed" for name, value in values.items() if _opt_int(value) is None)


def _validate_extracted_identity(post_id: str, actor: Any, target: PostPermalinkTarget) -> None:
    if (actor, post_id) != (target.actor, target.post_id):
        raise m.CoreError(
            m.ErrorCode.REFERENCE_INVALID,
            "Extracted Facebook post does not match the authorized identity",
            next_action="Review the approved permalink and its exact actor and post token",
        )


def _raise_observation_barrier(status_code: int, meta_state: str | None) -> None:
    # 1. Explicit status code and barrier checks (AT-041)
    if status_code == 429 or meta_state == "rate_limited":
        raise m.CoreError(
            m.ErrorCode.RATE_LIMITED,
            "Facebook rate-limited the browser session (HTTP 429 or rate-limit barrier)",
            next_action=(
                "Stop collection and wait before retrying; do not rotate accounts or proxies"
            ),
        )
    if meta_state in ("checkpoint", "challenge"):
        raise m.CoreError(
            m.ErrorCode.CHECKPOINT_REQUIRED,
            "Facebook presented an account checkpoint or challenge",
            next_action=(
                "Complete the challenge manually outside MetaMuse and re-import storage_state"
            ),
        )
    if status_code == 401 or meta_state == "login_wall":
        raise m.CoreError(
            m.ErrorCode.AUTH_REQUIRED,
            "Facebook required authentication (login wall encountered)",
            next_action=(
                "Log in outside MetaMuse, export a fresh storage_state, and run "
                "metamuse auth import"
            ),
        )
    if status_code == 403 or meta_state == "access_denied":
        raise m.CoreError(
            m.ErrorCode.ACCESS_DENIED,
            "Facebook denied access to this post permalink (HTTP 403 or permission barrier)",
            next_action="Verify that the authenticated account has permission to view this post",
        )
    if status_code >= 400:
        raise m.CoreError(
            m.ErrorCode.PROTOCOL_MISMATCH,
            f"Unexpected HTTP status {status_code} on Facebook post permalink",
            next_action="Inspect the target permalink and try again later",
        )


def classify_and_parse_observation(
    obs: PageObservation,
    *,
    permalink: PostPermalinkTarget,
    require_comments_section: bool,
) -> ParsedPostPage:
    """Classify failure states (AT-041) or parse the post permalink DOM/JSON."""
    barrier = EgressGuard._classify_navigation_barrier(obs.url)
    if barrier is not None:
        raise barrier
    extractor = _DOMExtractor(snapshot_visibility=obs.snapshot_visibility)
    try:
        extractor.feed(obs.html)
    except Exception as exc:
        raise m.CoreError(
            m.ErrorCode.SELECTOR_DRIFT,
            "Facebook response HTML could not be parsed",
            next_action="Inspect the page markup against the approved post_permalink parser",
        ) from exc

    _raise_observation_barrier(obs.status_code, extractor.meta_state)

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

    post_text = (
        "".join(extractor.post_text_parts)
        if extractor.post_body_present
        else attrs.get("data-post-text")
    )
    raw_time = attrs.get("data-created-at") or None

    parsed_comments: list[ParsedComment] = []
    for c in extractor.comments:
        if not c["visible"]:
            continue
        c_text = "".join(c["text_parts"]) if c["body_present"] else None
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
        comments_exhausted=extractor.comments_exhausted,
        missing_fields=tuple(
            sorted(
                set(
                    _count_gaps(
                        {
                            name: attrs["data-" + name.replace("_", "-")]
                            for name in ("reaction_count", "comment_count", "share_count")
                            if "data-" + name.replace("_", "-") in attrs
                        }
                    )
                )
                | extractor.count_gaps
            )
        ),
    )


def _parse_json_payload(
    raw_json: str,
    *,
    permalink: PostPermalinkTarget,
    require_comments_section: bool,
) -> ParsedPostPage:
    try:
        # Keep oversized decimal tokens lexical for per-field validation.
        # This avoids dropping a valid post at Python’s integer conversion bound.
        payload = json.loads(
            raw_json, parse_int=lambda token: int(token) if len(token) <= 128 else token
        )
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
        native_id = _native_token(entry.get("id"))
        parent_id = _native_token(entry.get("parent_id"))
        if parent_id is None and c_kind == "comment":
            parent_id = post_id
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
    control_gaps: set[str] = set()
    for ctrl in payload.get("expand_controls", []) or []:
        if not isinstance(ctrl, Mapping):
            continue
        cid = str(ctrl.get("id") or f"expand_{len(controls) + 1}")
        ckind: Literal["more_comments", "more_replies"] = (
            "more_replies" if ctrl.get("kind") == "more_replies" else "more_comments"
        )
        if "remaining" in ctrl and _opt_int(ctrl["remaining"]) is None:
            control_gaps.add("remaining_count_unparsed")
        controls.append(
            ExpandControl(
                control_id=cid,
                kind=ckind,
                parent_comment_id=(_native_token(ctrl.get("parent_comment_id"))),
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
        comments_exhausted=payload.get("comments_exhausted") is True,
        missing_fields=tuple(
            sorted(
                set(
                    _count_gaps(
                        {
                            name: post_obj[name]
                            for name in ("reaction_count", "comment_count", "share_count")
                            if name in post_obj
                        }
                    )
                )
                | control_gaps
            )
        ),
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
            "browser_sandbox": "unverified",
            "browser_sandbox_requested": "operator_exception"
            if fb_cfg.allow_unsandboxed_browser
            else "required",
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
                source_url=permalink.canonical_url,
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
                    source_url=permalink.canonical_url,
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
    requested_locator: str,
) -> None:
    """Insert Source rows with full provenance before book.flush runs (no post-insert UPDATE)."""
    for item in pending:
        if not item.source_new:
            continue
        use_basis = {
            "permit_id": permit_id,
            "account": account,
            "page_type": PAGE_TYPE_POST_PERMALINK,
            "input_sha256": (item.revisions[0].evidence if item.revisions else item.evidence).get(
                "input_sha256", page_sha
            ),
            "requested_locator": requested_locator,
            "resolved_permalink": canonical_url,
        }
        writer.insert(
            m.Source(
                id=item.source_id,
                project_id=project_id,
                platform=item.platform,
                native_scope=item.scope if item.native_id else (item.scope or None),
                native_id=item.native_id,
                original_url=original_url,
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
    requested_locator = permalink.canonical_url

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
    saved = dict(step.checkpoint or {})
    is_resume = int(saved.get("accepted", 0)) > 0
    expected = (
        parse_post_permalink(saved["resolved_permalink"])
        if saved.get("resolved_permalink")
        else None
    )
    egress.authorize_permalink(permalink, expected if permalink.is_share else None)
    browser_sandbox = "unverified"
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
    expansions_used = int(saved.get("expansions_used", 0))
    previous_bytes = int(saved.get("bytes_used", 0))
    bytes_used = previous_bytes
    observation_bytes = 0
    accepted = int(saved.get("accepted", 0))
    accepted_identities = set(saved.get("accepted_identities", []))
    halt: str | None = None
    obs: PageObservation | None = None
    parsed: ParsedPostPage | None = None
    byte_limit_error: m.ErrorDetail | None = None
    accumulated_records: list[collect.Record] = []
    record_snapshots: dict[int, str] = {}
    observed_identities: set[str] = set()
    observed_states: dict[str, str] = {}
    observation_gaps: set[str] = set()
    pages_seen = 0

    def accumulate_page(page: ParsedPostPage, html: str) -> None:
        nonlocal pages_seen
        observation_gaps.update(page.missing_fields)
        snapshot_sha = hashlib.sha256(html.encode("utf-8")).hexdigest()
        records, _ = _records_from_page(page, permalink=permalink, resources=resources)
        for record in records:
            if record.native_id is None:
                if pages_seen:
                    observation_gaps.add("weak_identity_expansion")
                    continue
                identity = m.new_id("observation")  # No text/position identity is inferred.
            else:
                identity = m.payload_digest([record.platform, record.scope, record.native_id])
            state = m.payload_digest([record.kind, record.text, record.parent_native_id])
            if observed_states.get(identity) == state:
                continue
            if identity not in observed_identities and len(observed_identities) >= max_items + 1:
                observation_gaps.add("uncollected_items_budget")
                break
            observed_identities.add(identity)
            observed_states[identity] = state
            accumulated_records.append(record)
            record_snapshots[id(record)] = snapshot_sha
        pages_seen += 1

    def checkpoint_usage() -> None:
        step.commit_checkpoint(
            {
                **saved,
                "browser_sandbox": browser_sandbox,
                "expansions_used": expansions_used,
                "bytes_used": bytes_used,
                "accepted": accepted,
                "accepted_identities": sorted(accepted_identities),
            }
        )

    session: BrowserSession | None = None
    checkpoint_usage()
    try:
        step.guard()
        if previous_bytes >= max_bytes:
            raise m.CoreError(m.ErrorCode.LIMIT_REACHED, "Facebook byte budget is exhausted")
        session = driver.open_session(
            storage_state,
            egress,
            timeout_ms=timeout_ms,
            max_bytes=max_bytes - previous_bytes,
            allow_unsandboxed_browser=fb_cfg.allow_unsandboxed_browser,
        )
        browser_sandbox = getattr(session, "browser_sandbox", "unverified")
        checkpoint_usage()
        obs = session.goto_post(permalink.fetch_url)
        egress.check_browser_route(obs.url, is_navigation=True)
        if permalink.is_share:
            resolved = egress.authorized_permalink
            if resolved is None or resolved.is_share:
                raise _unsupported_resolution()
            permalink = replace(resolved, original_url=url_input.url)
            saved["resolved_permalink"] = permalink.canonical_url
        observation_bytes += len(obs.html.encode("utf-8"))
        bytes_used = previous_bytes + max(egress.bytes_relayed, observation_bytes)
        checkpoint_usage()
        parsed = classify_and_parse_observation(
            obs,
            permalink=permalink,
            require_comments_section=require_comments,
        )
        if egress.byte_limit_exceeded or bytes_used > max_bytes:
            raise m.CoreError(
                m.ErrorCode.LIMIT_REACHED,
                "Facebook page response exceeded max_bytes budget",
                next_action="Increase limits.max_bytes or narrow the requested resources",
            )

        accumulate_page(parsed, obs.html)
        while require_comments and parsed.expand_controls:
            step.guard()
            elapsed = time.monotonic() - started
            if elapsed >= wall_clock_s:
                halt = "limit_reached"
                break
            if expansions_used >= max_expansions:
                halt = "expand_budget_exhausted"
                break
            projected_items = accepted + len(observed_identities - accepted_identities)
            if projected_items >= max_items:
                halt = "limit_reached"
                break
            if pacing_ms > 0:
                step.executor.sleep(pacing_ms / 1000.0)
            ctrl = parsed.expand_controls[0]
            expansions_used += 1
            checkpoint_usage()  # Charge before the click, including a failed/crashed click.
            obs = session.expand_next(ctrl.control_id)
            egress.check_browser_route(obs.url, is_navigation=True)
            observation_bytes += len(obs.html.encode("utf-8"))
            bytes_used = previous_bytes + max(egress.bytes_relayed, observation_bytes)
            checkpoint_usage()
            parsed = classify_and_parse_observation(
                obs,
                permalink=permalink,
                require_comments_section=require_comments,
            )
            if egress.byte_limit_exceeded or bytes_used > max_bytes:
                raise m.CoreError(
                    m.ErrorCode.LIMIT_REACHED,
                    "Facebook page response exceeded max_bytes budget",
                    next_action="Increase limits.max_bytes or narrow the requested resources",
                )
            accumulate_page(parsed, obs.html)
            if obs.expansion_failed:
                halt = "expansion_failed"
                break
            if not obs.progress_confirmed:
                halt = "expansion_timeout"
                break
    except m.CoreError as exc:
        # Scrub any secret token from error messages before surfacing
        cleaned_msg = storage_state.redact(exc.detail.message)
        if exc.detail.code == m.ErrorCode.LIMIT_REACHED:
            halt = "limit_reached"
            bytes_used = max(bytes_used, previous_bytes + egress.bytes_relayed)
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
        try:
            if session is not None:
                session.close()
        finally:
            # Settle bytes even if late WebSocket detection stops close/import.
            bytes_used = previous_bytes + max(egress.bytes_relayed, observation_bytes)
            checkpoint_usage()

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
        records = accumulated_records
        post_media = parsed.attachments or None
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

    if any(record.kind == "reply" and record.parent_native_id is None for record in records):
        observation_gaps.add("parent_comment")
    comment_identities = {
        r.native_id for r in records if r.kind in ("comment", "reply") and r.native_id
    }
    comments_observed = len(comment_identities) + sum(
        r.kind in ("comment", "reply") and r.native_id is None for r in records
    )
    if require_comments and parsed is not None:
        if parsed.comment_count is not None and parsed.comment_count != comments_observed:
            observation_gaps.add("rendered_count_conflict")
            if parsed.comment_count > comments_observed and not parsed.expand_controls:
                observation_gaps.add("missing_comments_controls")
        if not parsed.comments_exhausted:
            observation_gaps.add("comments_scope_unverified")

    with step.store.connection() as conn:
        book = collect._Book(request.project_id)
        book.load(conn)

    # Identity-based resume (AT-013, FR-010): pass every record from the fresh observation
    # through book.apply; items committed in an earlier batch before a crash are already in
    # book.by_key and become "unchanged" without index slicing or storing HTML in checkpoints.
    rows: list[dict[str, Any]] = []
    missing: set[str] = (
        set(saved.get("missing_fields", [])) | set(unexpanded_gaps) | observation_gaps
    )
    if byte_limit_error is not None:
        missing.add("byte_budget_exceeded")
    if halt == "expansion_timeout":
        missing.add("expansion_progress_timeout")
    if halt == "expansion_failed":
        missing.add("expansion_failed")
    batch_count = 0

    def _flush_checkpoint(processed_count: int) -> None:
        nonlocal batch_count
        batch_count = 0
        cp_payload = {
            "browser_sandbox": browser_sandbox,
            "resolved_permalink": permalink.canonical_url
            if not permalink.is_share
            else saved.get("resolved_permalink"),
            "expansions_used": expansions_used,
            "bytes_used": bytes_used,
            "halt": halt,
            "missing_fields": sorted(missing),
            "accepted": accepted,
            "accepted_identities": sorted(accepted_identities),
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
                requested_locator=requested_locator,
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
        if is_resume and rec.native_id is None:
            missing.add("weak_identity_resume")
            rows.append(
                {
                    "input": 0,
                    "row": rec.row,
                    "status": "skipped",
                    "code": None,
                    "field": "identity",
                    "message": None,
                }
            )
            continue
        identity = (
            m.payload_digest([rec.platform, rec.scope, rec.native_id]) if rec.native_id else None
        )
        already_accepted = identity is not None and identity in accepted_identities
        if accepted >= max_items and not already_accepted:
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
                sha=record_snapshots[id(rec)],
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
            if item.revision_new:
                item.evidence.update(
                    {
                        "requested_locator": requested_locator,
                        "resolved_permalink": permalink.canonical_url,
                        "permit_id": permit_id,
                        "account": account,
                    }
                )
            if rec.kind == "reply" and (rec.parent_native_id is None or item.unresolved_parent):
                missing.add("parent_comment")
                if item.revision_new:
                    item.missing_fields = sorted(set(item.missing_fields) | {"parent_comment"})
                    item.complete = False
            if item.kind == "post" and item.revision_new:
                merged_missing = list(item.missing_fields)
                for gap in sorted(missing):
                    if gap not in merged_missing:
                        merged_missing.append(gap)
                item.missing_fields = merged_missing
                item.complete = not merged_missing
                item.evidence.update(post_extra_evidence)
            if not already_accepted:
                accepted += 1
                if identity is not None:
                    accepted_identities.add(identity)
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

    if batch_count:
        _flush_checkpoint(len(records))

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
    has_gaps = bool(missing)
    comments_empty = (
        parsed is not None
        and require_comments
        and comments_observed == 0
        and parsed.comment_count == 0
        and parsed.comments_exhausted
        and len(parsed.expand_controls) == 0
        and halt is None
        and not observation_gaps
    )

    if has_gaps or rejected:
        coverage: m.CoverageStatus = "partial"
        if halt in (
            "limit_reached",
            "expand_budget_exhausted",
            "expansion_timeout",
            "expansion_failed",
        ):
            stop_reason = halt
        elif unexpanded_gaps:
            stop_reason = "unexpanded_controls"
        elif rejected:
            stop_reason = "row_errors"
        else:
            stop_reason = "coverage_gaps"
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
        "requested_locator": requested_locator,
        "resolved_permalink": permalink.canonical_url,
        "committed": sum(r.get("status") == "committed" for r in rows),
        "unchanged": sum(r.get("status") == "unchanged" for r in rows),
        "rejected": sum(r.get("status") == "rejected" for r in rows),
        "comments_observed": comments_observed,
        "comments_empty": comments_empty,
        "expansions_used": expansions_used,
        "unexpanded_controls": len(parsed.expand_controls) if parsed is not None else 0,
        "bytes_used": bytes_used,
        "accepted": accepted,
    }
    result_data: dict[str, Any] = {
        "rows": rows,
        "egress": egress_summary,
        "requested_locator": requested_locator,
        "resolved_permalink": permalink.canonical_url,
        "browser_sandbox": browser_sandbox,
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
