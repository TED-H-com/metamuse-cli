"""B08 credential and checked-address adapters. No discovery or implicit network."""

import json
import re
import socket
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from metamuse import models as m
from metamuse.safety import CheckedURL, OutboundURLPolicy, resolve_secret

COOKIE_MAX_BYTES = 8192


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


@dataclass(frozen=True)
class BoundMuseCredentials:
    binding: m.MuseBinding = field(repr=False)
    secret_store: Any = field(default=None, repr=False)

    def cookies(self, auth_ref: str) -> str:
        failed = False
        value = None
        try:
            if auth_ref != self.binding.auth_ref:
                raise ValueError
            raw = resolve_secret(
                auth_ref,
                provider=self.binding.provider,
                account=self.binding.account,
                secret_store=self.secret_store,
            )
            value = parse_cookie_header(raw)
        except Exception:
            failed = True
        if failed:
            raise m.CoreError(
                m.ErrorCode.AUTH_REQUIRED,
                "Muse cookie-header binding is unavailable",
                next_action="Import cookie-header JSON and configure provider/account/auth_ref",
            )
        return value


def parse_cookie_header(raw: bytes) -> str:
    """Validate the supported private-file format without accessing an account."""
    value = None
    failed = False
    try:
        if len(raw) > COOKIE_MAX_BYTES:
            raise ValueError
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if (
            not isinstance(document, dict)
            or set(document) != {"format", "cookie_header"}
            or document["format"] != "cookie-header"
        ):
            raise ValueError
        value = document["cookie_header"]
        if (
            not isinstance(value, str)
            or not value
            or any(ord(c) < 32 or ord(c) >= 127 for c in value)
        ):
            raise ValueError
        for cookie in value.split(";"):
            name, sep, content = cookie.strip().partition("=")
            if not sep or not re.fullmatch(r"[A-Za-z0-9_!#$%&'*+.^`|~-]+", name) or not content:
                raise ValueError
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        failed = True
    if failed:
        raise m.CoreError(
            m.ErrorCode.AUTH_REQUIRED,
            "Invalid Muse cookie-header JSON",
            next_action="Use a JSON object with format=cookie-header and cookie_header",
        )
    return value


def resolve_all(host: str) -> tuple[str, ...]:
    return tuple(
        sorted({row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    )


@dataclass(frozen=True)
class MuseURLGuard:
    resolver: Any = resolve_all

    def check(self, url: str, *, purpose: str) -> CheckedURL:
        if purpose not in ("auth.check", "session", "hatch.token", "hatch.wake", "wss.noise"):
            raise m.CoreError(m.ErrorCode.SCOPE_DENIED, "Unknown Muse URL purpose")
        parsed = urlsplit(url)
        is_wss = parsed.scheme == "wss"
        if is_wss and (parsed.hostname != "hatch.metaaivm.com" or parsed.path != "/v1/noise"):
            raise m.CoreError(m.ErrorCode.SCOPE_DENIED, "Unknown Muse gateway")
        paths = {
            "auth.check": "/api/auth/check",
            "session": "/api/session",
            "hatch.token": "/api/hatch/token",
            "hatch.wake": "/api/hatch/vm/wake",
            "wss.noise": "/v1/noise",
        }
        query = parse_qsl(parsed.query, keep_blank_values=True)
        keys = [key for key, _value in query]
        valid = (
            parsed.path == paths[purpose]
            and not parsed.fragment
            and not parsed.username
            and not parsed.password
            and (parsed.port or 443) == 443
        )
        if is_wss:
            valid = (
                valid
                and purpose == "wss.noise"
                and set(keys)
                in (
                    {"vm_id", "auth_token", "app_id", "request_id"},
                    {"vm_id", "auth_token", "notary_token", "app_id", "request_id"},
                )
                and len(keys) == len(set(keys))
                and dict(query).get("app_id") == "hatch-web"
            )
        else:
            valid = valid and parsed.scheme == "https" and parsed.hostname == "muse.ai"
            valid = valid and (
                query == [("vm_state", "live")] if purpose == "session" else not query
            )
        if not valid:
            raise m.CoreError(m.ErrorCode.SCOPE_REQUIRED, "Muse URL purpose was refused")
        mapped = urlunsplit(
            (
                "https" if is_wss else parsed.scheme,
                parsed.netloc,
                parsed.path,
                parsed.query,
                parsed.fragment,
            )
        )
        policy = OutboundURLPolicy(self.resolver, ("muse.ai", "hatch.metaaivm.com"))
        checked = policy.before_request(mapped, purpose="request")
        return replace(checked, url="wss:" + checked.url[6:]) if is_wss else checked

    def verify_peer(self, checked: CheckedURL, peer_address: str) -> None:
        mapped = "https:" + checked.url[4:] if checked.url.startswith("wss:") else checked.url
        policy = OutboundURLPolicy(
            lambda host: checked.addresses if host == checked.hostname else (), (checked.hostname,)
        )
        policy.before_request(mapped, purpose="request", peer_address=peer_address)
