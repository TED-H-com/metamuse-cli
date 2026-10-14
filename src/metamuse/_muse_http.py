"""curl_cffi adapter for the Muse HTTPS auth chain and the Noise WebSocket.

``curl_cffi`` is imported inside :func:`load_curl`. Importing this module does
not import it, so offline commands keep working when the package is absent.

The adapter always passes ``impersonate="chrome"`` and ``verify=True``.
``curl_cffi`` 0.16.3 stores ``trust_env`` and never reads it, and
``WebSocket.connect`` defaults ``verify`` to ``None`` (TLS verification off).
An empty ``CURLOPT_PROXY`` is what disables proxy variables from the
environment. An explicit proxy is used only when the caller passes one.
Redirects are not followed. ``WebSocket.recv`` waits forever, so the socket
here uses ``recv_fragment`` and stops at the caller's timeout.
"""

from __future__ import annotations

import select
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from metamuse._muse_codec import redact
from metamuse._muse_link import ByteLink, SendUncertain
from metamuse.models import CoreError, ErrorCode


@dataclass(frozen=True)
class AddressPin:
    """Addresses the outbound guard tells the adapter to use for one host."""

    host: str
    port: int
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


class HttpClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        pins: AddressPin | None,
        timeout: float,
        proxy: str | None,
    ) -> HttpResponse: ...


class Connector(Protocol):
    def connect(
        self,
        url: str,
        *,
        pins: AddressPin | None,
        timeout: float,
        proxy: str | None,
    ) -> ByteLink: ...


def load_curl() -> tuple[Any, Any, Any]:
    """Import curl_cffi. A missing install is ``E_DEPENDENCY_MISSING``."""
    try:
        from curl_cffi.const import CurlOpt
        from curl_cffi.requests import Session, WebSocket
    except ImportError as exc:
        raise CoreError(
            ErrorCode.DEPENDENCY_MISSING,
            "curl_cffi is not installed",
            next_action="Install the pinned curl_cffi package. Do not add websockets.",
        ) from exc
    return Session, WebSocket, CurlOpt


def resolve_entries(pin: AddressPin) -> list[str]:
    """curl ``RESOLVE`` lines. IPv6 addresses are wrapped in brackets."""
    if not pin.addresses:
        raise CoreError(
            ErrorCode.SCOPE_REQUIRED,
            "Pinned addresses were empty",
            next_action="Return at least one address from the outbound guard or allow the URL",
        )
    entries: list[str] = []
    for address in pin.addresses:
        if ":" in address and not address.startswith("["):
            rendered = f"[{address}]"
        else:
            rendered = address
        entries.append(f"{pin.host}:{pin.port}:{rendered}")
    return entries


def _options(curl_opt: Any, *, proxy: str | None, pins: AddressPin | None) -> dict[Any, Any]:
    options: dict[Any, Any] = {}
    if proxy is None:
        # Empty string disables libcurl's use of http_proxy / https_proxy.
        options[curl_opt.PROXY] = b""
    if pins is not None:
        if not hasattr(curl_opt, "RESOLVE"):
            raise CoreError(
                ErrorCode.SCOPE_REQUIRED,
                "Pinned addresses could not be applied",
                next_action="Use a curl_cffi build that supports CURLOPT_RESOLVE",
            )
        options[curl_opt.RESOLVE] = resolve_entries(pins)
    return options


def _transport_failure(exc: Exception) -> CoreError:
    if isinstance(exc, CoreError):
        return exc
    return CoreError(
        ErrorCode.NETWORK_TIMEOUT,
        redact(str(exc)),
        next_action="Retry the connection. No gateway frame was handed off by this failure.",
    )


class CurlHttp:
    """One-shot HTTPS calls. No session cookie jar is retained."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        pins: AddressPin | None,
        timeout: float,
        proxy: str | None,
    ) -> HttpResponse:
        session_type, _websocket, curl_opt = load_curl()
        options = _options(curl_opt, proxy=proxy, pins=pins)
        session = session_type(impersonate="chrome", verify=True, trust_env=False)
        try:
            kwargs: dict[str, Any] = {
                "headers": dict(headers),
                "impersonate": "chrome",
                "verify": True,
                "allow_redirects": False,
                "timeout": timeout,
                "curl_options": options,
            }
            if body is not None:
                kwargs["data"] = body
            if proxy is not None:
                kwargs["proxy"] = proxy
            try:
                response = session.request(method, url, **kwargs)
            except CoreError:
                raise
            except Exception as exc:
                raise _transport_failure(exc) from None
        finally:
            session.close()
        raw_headers = getattr(response, "headers", {}) or {}
        copied = {str(key): str(value) for key, value in dict(raw_headers).items()}
        content = getattr(response, "content", b"") or b""
        if isinstance(content, str):
            content = content.encode("utf-8", "replace")
        return HttpResponse(int(response.status_code), bytes(content), copied)


class CurlSocket:
    """Byte pipe over one curl WebSocket. ``recv`` stops at ``timeout``."""

    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._closed = False

    def send(self, data: bytes) -> None:
        try:
            self._ws.send_bytes(data)
        except Exception as exc:
            raise SendUncertain(redact(str(exc))) from None

    def recv(self, timeout: float) -> bytes | None:
        from curl_cffi.const import CurlECode, CurlInfo, CurlWsFlag
        from curl_cffi.curl import CurlError
        from curl_cffi.requests.websockets import CURL_SOCKET_BAD

        deadline = time.monotonic() + max(0.0, timeout)
        chunks: list[bytes] = []
        flags = 0
        while True:
            try:
                chunk, frame = self._ws.recv_fragment()
            except CurlError as exc:
                if exc.code != CurlECode.AGAIN:
                    raise CoreError(
                        ErrorCode.NETWORK_TIMEOUT,
                        redact(str(exc)),
                        next_action="Retry the read if no frame was handed off.",
                    ) from None
                if time.monotonic() >= deadline:
                    return None
                remaining = deadline - time.monotonic()
                sock = self._ws.curl.getinfo(CurlInfo.ACTIVESOCKET)
                if sock == CURL_SOCKET_BAD:
                    raise CoreError(
                        ErrorCode.NETWORK_TIMEOUT,
                        "Muse socket has no file descriptor",
                        next_action="Retry the connection.",
                    ) from None
                select.select([sock], [], [], min(remaining, 0.5))
                if time.monotonic() >= deadline:
                    return None
                continue
            except Exception as exc:
                raise CoreError(
                    ErrorCode.NETWORK_TIMEOUT,
                    redact(str(exc)),
                    next_action="Retry the read if no frame was handed off.",
                ) from None
            if frame.flags & (CurlWsFlag.PING | CurlWsFlag.PONG):
                continue
            if frame.flags & CurlWsFlag.CLOSE:
                return None
            chunks.append(chunk)
            flags = frame.flags
            if frame.bytesleft == 0 and flags & CurlWsFlag.CONT == 0:
                return b"".join(chunks)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        closer = getattr(self._ws, "close", None)
        if closer is not None:
            try:
                closer()
            except Exception:
                return


class CurlConnector:
    """Open ``wss://`` with certificate verification left on."""

    def connect(
        self,
        url: str,
        *,
        pins: AddressPin | None,
        timeout: float,
        proxy: str | None,
    ) -> ByteLink:
        _session, websocket_type, curl_opt = load_curl()
        parsed = urlparse(url)
        if parsed.scheme != "wss" or not parsed.hostname:
            raise CoreError(
                ErrorCode.SCOPE_REQUIRED,
                "Muse gateway URL is not a wss URL",
                next_action="Use the hatch WebSocket endpoint.",
            )
        options = _options(curl_opt, proxy=proxy, pins=pins)
        ws = websocket_type()
        kwargs: dict[str, Any] = {
            "impersonate": "chrome",
            "verify": True,
            "allow_redirects": False,
            "timeout": timeout,
            "quote": False,
            "curl_options": options,
        }
        if proxy is not None:
            kwargs["proxy"] = proxy
        try:
            ws.connect(url, **kwargs)
        except CoreError:
            raise
        except Exception as exc:
            raise _transport_failure(exc) from None
        return CurlSocket(ws)
