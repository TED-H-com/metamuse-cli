"""B08 credential and checked-address adapters. No discovery or implicit network."""

import json
import re
import socket
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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
    binding: m.MuseBinding
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
            if len(raw) > COOKIE_MAX_BYTES:
                raise ValueError
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
            if (
                set(document) != {"format", "cookie_header"}
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
        except Exception:
            failed = True
        if failed:
            raise m.CoreError(
                m.ErrorCode.AUTH_REQUIRED,
                "Muse cookie-header binding is unavailable",
                next_action="Import cookie-header JSON and configure provider/account/auth_ref",
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
        if purpose not in ("auth.check", "session", "hatch.token", "wss.noise"):
            raise m.CoreError(m.ErrorCode.SCOPE_DENIED, "Unknown Muse URL purpose")
        parsed = urlsplit(url)
        is_wss = parsed.scheme == "wss"
        if is_wss and (parsed.hostname != "hatch.metaaivm.com" or parsed.path != "/v1/noise"):
            raise m.CoreError(m.ErrorCode.SCOPE_DENIED, "Unknown Muse gateway")
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
