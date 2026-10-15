"""curl_cffi adapter for the Muse HTTPS auth chain and the Noise WebSocket.

``curl_cffi`` is imported inside :func:`load_curl`. Importing this module does
not import it, so offline commands keep working when the package is absent.

``curl_cffi`` 0.16.3 stores ``trust_env`` and never reads it, and
``Session.request`` does not accept ``curl_options`` (the session constructor
does). ``WebSocket.connect`` does accept ``curl_options`` and defaults
``verify`` to ``None``. This adapter pins both calls to the certifi CA bundle,
``impersonate="chrome"``, hostname verification, an empty ``CURLOPT_PROXY``,
and ``CURLOPT_RESOLVE`` entries for the checked hostname. It does not accept
an explicit proxy. Redirects are not followed. Auth bodies stop at
``AUTH_RESPONSE_MAX_BYTES`` decompressed bytes. WebSocket reads stop at one
ciphertext, a fragment budget, and the caller's monotonic deadline.
"""

from __future__ import annotations

import ipaddress
import select
import socket
import threading
import time
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from metamuse._muse_link import ByteLink, SendUncertain
from metamuse.models import CoreError, ErrorCode
from metamuse.safety import CheckedURL

# Auth JSON is a token plus a vm id. 64 KiB is the decompressed cap, including
# a body that arrives without Content-Length. Larger responses are not parsed.
AUTH_RESPONSE_MAX_BYTES = 64 * 1024
SESSION_RESPONSE_MAX_BYTES = 1024 * 1024
_WS_MESSAGE_MAX_BYTES = 65535
_WS_PIECE_MAX = 64


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    peer: str


class HttpClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        checked: CheckedURL,
        timeout: float,
    ) -> HttpResponse: ...


class Connector(Protocol):
    def connect(
        self,
        url: str,
        *,
        checked: CheckedURL,
        timeout: float,
    ) -> ByteLink: ...


class AuthBodyBuffer:
    """Accumulates one HTTPS body and stops before the cap is exceeded."""

    def __init__(self, limit: int = AUTH_RESPONSE_MAX_BYTES) -> None:
        self.limit = limit
        self.data = bytearray()
        self.overflow = False

    def __call__(self, chunk: bytes) -> int:
        piece = bytes(chunk)
        if len(self.data) + len(piece) > self.limit:
            self.overflow = True
            raise RuntimeError("auth body cap")
        self.data.extend(piece)
        return len(piece)


def load_curl() -> tuple[Any, Any, Any]:
    """Import curl_cffi. A missing install is ``E_DEPENDENCY_MISSING``."""
    try:
        from curl_cffi.const import CurlOpt
        from curl_cffi.requests import Session, WebSocket
    except ImportError:
        failure = CoreError(
            ErrorCode.DEPENDENCY_MISSING,
            "curl_cffi is not installed",
            next_action="Install the pinned curl_cffi package. Do not add websockets.",
        )
    else:
        failure = None
    if failure is not None:
        raise failure
    return Session, WebSocket, CurlOpt


def _ca_bundle() -> str:
    try:
        import certifi
    except ImportError:
        failure = CoreError(
            ErrorCode.DEPENDENCY_MISSING,
            "certifi is not installed",
            next_action="Install the pinned certifi bundle. Do not trust an environment CA path.",
        )
    else:
        failure = None
        path = certifi.where()
    if failure is not None:
        raise failure
    if not isinstance(path, str) or not path:
        raise CoreError(
            ErrorCode.DEPENDENCY_MISSING,
            "certifi did not provide a CA bundle",
            next_action="Install the pinned certifi bundle.",
        )
    return path


def _hostname(host: str) -> str:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise CoreError(
        ErrorCode.SCOPE_REQUIRED,
        "Outbound host must stay a hostname",
        next_action="Pin the canonical hostname. Do not put an IP in Host or SNI.",
    )


def resolve_entries(checked: CheckedURL) -> list[str]:
    """curl ``RESOLVE`` lines. The host is the pinned hostname, never an IP."""
    host = _hostname(checked.hostname)
    if not checked.addresses or checked.port <= 0:
        raise CoreError(
            ErrorCode.SCOPE_REQUIRED,
            "Pinned addresses were empty",
            next_action="Return at least one address from the outbound guard",
        )
    addresses: list[str] = []
    for address in checked.addresses:
        if not isinstance(address, str) or not address or "/" in address:
            raise CoreError(
                ErrorCode.SCOPE_REQUIRED,
                "Pinned addresses were empty",
                next_action="Return IP addresses from the outbound guard",
            )
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            failure = CoreError(ErrorCode.SCOPE_REQUIRED, "Pinned addresses were empty")
        else:
            failure = None
        _raise_if(failure)
        addresses.append(f"[{address}]" if parsed.version == 6 else address)
    return [f"{host}:{checked.port}:{','.join(addresses)}"]


def _options(curl_opt: Any, checked: CheckedURL) -> dict[Any, Any]:
    if not hasattr(curl_opt, "RESOLVE") or not hasattr(curl_opt, "CAINFO"):
        raise CoreError(
            ErrorCode.DEPENDENCY_MISSING,
            "curl_cffi API is not the pinned shape",
            next_action="Install curl_cffi==0.16.3. Do not retry this process.",
        )
    return {
        curl_opt.PROXY: b"",
        curl_opt.CAINFO: _ca_bundle(),
        curl_opt.SSL_VERIFYPEER: 1,
        curl_opt.SSL_VERIFYHOST: 2,
        curl_opt.RESOLVE: resolve_entries(checked),
    }


def _api_mismatch() -> CoreError:
    return CoreError(
        ErrorCode.DEPENDENCY_MISSING,
        "curl_cffi API is not the pinned shape",
        next_action="Install curl_cffi==0.16.3. Do not retry this process.",
    )


def _https_failed() -> CoreError:
    return CoreError(
        ErrorCode.NETWORK_TIMEOUT,
        "Muse HTTPS request failed",
        next_action="Retry the auth request. No gateway frame was handed off.",
    )


class UpgradeFailure(CoreError):
    """Only an integer status escapes curl; no URL, body, query or raw exception."""

    def __init__(self, status: int):
        self.status = status
        if status in (401, 403):
            code, message, action = (
                ErrorCode.AUTH_REQUIRED,
                "Muse upgrade authentication failed",
                "Refresh the operator credentials",
            )
        elif status == 429:
            code, message, action = (
                ErrorCode.RATE_LIMITED,
                "Muse upgrade rate limited",
                "Wait before reconnecting; do not resend a handed-off chat",
            )
        elif 500 <= status <= 599:
            code, message, action = (
                ErrorCode.RESOURCE_UNAVAILABLE,
                "Muse VM is not running yet",
                "The VM is not running yet; retry later",
            )
        else:
            code, message, action = (
                ErrorCode.PROTOCOL_MISMATCH,
                "Muse upgrade was refused",
                "Stop for protocol review",
            )
        super().__init__(code, message, next_action=action)


def _upgrade_failed(ws: Any) -> CoreError:
    from curl_cffi.const import CurlInfo

    try:
        status = int(ws.curl.getinfo(CurlInfo.RESPONSE_CODE))
    except Exception:
        status = 0
    if status >= 300:
        return UpgradeFailure(status)
    return CoreError(
        ErrorCode.NETWORK_TIMEOUT,
        "Muse connection failed",
        next_action="Retry the connection; no application frame was sent",
    )


def _auth_too_large(session: bool = False) -> CoreError:
    return CoreError(
        ErrorCode.LIMIT_REACHED,
        "Muse session response exceeded the size cap"
        if session
        else "Muse auth response exceeded the size cap",
        next_action="Stop. Do not parse an oversized session response."
        if session
        else "Stop. Do not parse an oversized auth response.",
    )


def _raise_if(error: CoreError | None) -> None:
    if error is not None:
        raise error


def _send_uncertain() -> None:
    raise SendUncertain("gateway send did not finish")


def _peer_text(value: Any) -> str:
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    if not isinstance(value, str):
        return ""
    return value.strip()


class CurlHttp:
    """One-shot HTTPS calls. No session cookie jar is retained."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        checked: CheckedURL,
        timeout: float,
    ) -> HttpResponse:
        session_type, _websocket, curl_opt = load_curl()
        options = _options(curl_opt, checked)
        bundle = _ca_bundle()
        # A string verify skips Session.__init__'s REQUESTS_CA_BUNDLE override.
        try:
            session = session_type(
                impersonate="chrome",
                verify=bundle,
                trust_env=False,
                allow_redirects=False,
                curl_options=options,
            )
        except (TypeError, AttributeError):
            failure = _api_mismatch()
            session = None
        else:
            failure = None
        _raise_if(failure)
        is_session = urlsplit(url).path == "/api/session"
        writer = AuthBodyBuffer(
            SESSION_RESPONSE_MAX_BYTES if is_session else AUTH_RESPONSE_MAX_BYTES
        )
        response = None
        try:
            kwargs: dict[str, Any] = {
                "headers": dict(headers),
                "impersonate": "chrome",
                "verify": bundle,
                "allow_redirects": False,
                "timeout": timeout,
                "content_callback": writer,
            }
            if body is not None:
                kwargs["data"] = body
            try:
                response = session.request(method, url, **kwargs)
            except (TypeError, AttributeError):
                failure = _api_mismatch()
            except Exception:
                failure = _auth_too_large(is_session) if writer.overflow else _https_failed()
        finally:
            if session is not None:
                closer = getattr(session, "close", None)
                if closer is not None:
                    try:
                        closer()
                    except Exception:
                        pass
        _raise_if(failure)
        if writer.overflow or len(writer.data) > writer.limit:
            raise _auth_too_large(is_session)
        peer = _peer_text(getattr(response, "primary_ip", ""))
        raw_headers = getattr(response, "headers", {}) or {}
        copied = {str(key): str(value) for key, value in dict(raw_headers).items()}
        return HttpResponse(int(response.status_code), bytes(writer.data), copied, peer)


class CurlSocket:
    """Serial curl ownership with a separately owned socket cancellation handle."""

    def __init__(self, ws: Any, peer_address: str) -> None:
        self._ws = ws
        self.peer_address = peer_address
        self._closed = False
        self._cleaned = False
        self._active = False
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._interrupt: socket.socket | None = None
        # Cache on the connector/owner thread, before this pipe can be notified.
        # A duplicate pins the socket identity even if curl releases its own fd.
        # Never getinfo, close or otherwise use curl concurrently with an operation:
        # https://curl.se/libcurl/c/threadsafe.html (Handles).
        curl = getattr(ws, "curl", None)
        if curl is not None:
            from curl_cffi.const import CurlInfo
            from curl_cffi.requests.websockets import CURL_SOCKET_BAD

            try:
                fd = curl.getinfo(CurlInfo.ACTIVESOCKET)
                if type(fd) is not int or fd < 0 or fd == CURL_SOCKET_BAD:
                    raise ValueError("no socket")
                # Use the native descriptor duplicator, not socket.socket.dup():
                # that method calls settimeout and changes curl's shared O_NONBLOCK.
                duplicate = socket.dup(fd)
                try:
                    self._interrupt = socket.socket(fileno=duplicate)
                except BaseException:
                    socket.close(duplicate)
                    raise
            except (TypeError, AttributeError):
                failure = _api_mismatch()
            except Exception:
                failure = CoreError(
                    ErrorCode.NETWORK_TIMEOUT,
                    "Muse socket has no file descriptor",
                    next_action="Retry the connection.",
                )
            else:
                failure = None
            if failure is not None:
                self.close()
                raise failure
            # A peer close is handled below by the owner. Avoid the backend's
            # ordinary close-frame send/cleanup path during cancellation.
            ws.autoclose = False

    @contextmanager
    def _operation(self):
        with self._operation_lock:
            with self._state_lock:
                self._active = True
            try:
                yield
            finally:
                with self._state_lock:
                    self._active = False
                    cleanup = self._closed
                if cleanup:
                    self._cleanup()

    def _cleanup(self) -> None:
        # No native operation is active. Claim exactly once before touching curl.
        with self._state_lock:
            if self._cleaned:
                return
            self._cleaned = True
            interrupt, self._interrupt = self._interrupt, None
        try:
            # close() sends a WebSocket close frame; cancellation must not send.
            # terminate() only cleans up. Descriptor-less test doubles may expose
            # only close(), which is safe here after their operation has unwound.
            closer = None
            if not getattr(self._ws, "closed", False):
                closer = getattr(self._ws, "terminate", None) or getattr(self._ws, "close", None)
            if closer is not None:
                closer()
        except Exception:
            pass
        finally:
            if interrupt is not None:
                interrupt.close()

    def send(self, data: bytes) -> None:
        failed = False
        with self._operation():
            if self._closed:
                failed = True
            else:
                try:
                    self._ws.send_bytes(data)
                except Exception:
                    failed = True
        if failed:
            _send_uncertain()

    def recv(self, timeout: float) -> bytes | None:
        with self._operation():
            return self._recv(timeout)

    def _recv(self, timeout: float) -> bytes | None:
        from curl_cffi.const import CurlECode, CurlWsFlag
        from curl_cffi.curl import CurlError

        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise CoreError(
                ErrorCode.PROTOCOL_MISMATCH,
                "Muse read timeout is not finite",
                next_action="Stop. Do not raise the collect deadline.",
            )
        if timeout < 0 or timeout != timeout or timeout == float("inf"):
            raise CoreError(
                ErrorCode.PROTOCOL_MISMATCH,
                "Muse read timeout is not finite",
                next_action="Stop. Do not raise the collect deadline.",
            )
        deadline = time.monotonic() + timeout
        chunks: list[bytes] = []
        total = 0
        pieces = 0
        while True:
            if self._closed:
                return None
            if time.monotonic() >= deadline:
                return None
            if pieces >= _WS_PIECE_MAX:
                raise CoreError(
                    ErrorCode.PROTOCOL_MISMATCH,
                    "Muse websocket fragment budget was exhausted",
                    next_action="Stop. Do not assemble an unbounded websocket message.",
                )
            try:
                chunk, frame = self._ws.recv_fragment()
            except CurlError as exc:
                again = getattr(exc, "code", None) == CurlECode.AGAIN
                if not again:
                    failure = CoreError(
                        ErrorCode.NETWORK_TIMEOUT,
                        "Muse websocket read failed",
                        next_action="Retry the read if no frame was handed off.",
                    )
                else:
                    failure = None
            except (TypeError, AttributeError):
                failure = _api_mismatch()
                again = False
            except Exception:
                failure = CoreError(
                    ErrorCode.NETWORK_TIMEOUT,
                    "Muse websocket read failed",
                    next_action="Retry the read if no frame was handed off.",
                )
                again = False
            else:
                failure = None
                again = False
            if failure is not None:
                raise failure
            if self._closed:
                return None
            if again:
                if time.monotonic() >= deadline:
                    return None
                remaining = deadline - time.monotonic()
                if self._interrupt is None:
                    # Descriptor-less fakes work until they request socket I/O.
                    raise _api_mismatch()
                select.select([self._interrupt], [], [], min(remaining, 0.5))
                if time.monotonic() >= deadline:
                    return None
                continue
            pieces += 1
            if time.monotonic() >= deadline:
                return None
            if frame.flags & (CurlWsFlag.PING | CurlWsFlag.PONG):
                continue
            if frame.flags & CurlWsFlag.CLOSE:
                self.close()
                return None
            piece = bytes(chunk)
            if total + len(piece) > _WS_MESSAGE_MAX_BYTES:
                raise CoreError(
                    ErrorCode.PROTOCOL_MISMATCH,
                    "Muse websocket message exceeds the ciphertext cap",
                    next_action="Stop. Do not assemble an oversized websocket message.",
                )
            if piece:
                chunks.append(piece)
                total += len(piece)
            if frame.bytesleft == 0 and frame.flags & CurlWsFlag.CONT == 0:
                if time.monotonic() >= deadline:
                    return None
                return b"".join(chunks)

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            if self._interrupt is not None:
                try:
                    self._interrupt.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # Already disconnected; the owner still cleans up.
            cleanup = not self._active
        # Never wait for recv/send or a transport lock in a notification. An
        # active operation cleans up in its finally; an idle handle can safely
        # transfer to this thread without simultaneous libcurl access.
        if cleanup:
            self._cleanup()


class CurlConnector:
    """Open ``wss://`` with certificate verification left on."""

    def connect(
        self,
        url: str,
        *,
        checked: CheckedURL,
        timeout: float,
    ) -> ByteLink:
        from urllib.parse import urlparse

        _session, websocket_type, curl_opt = load_curl()
        parsed = urlparse(url)
        if parsed.scheme != "wss" or not parsed.hostname:
            raise CoreError(
                ErrorCode.SCOPE_REQUIRED,
                "Muse gateway URL is not a wss URL",
                next_action="Use the hatch WebSocket endpoint.",
            )
        if parsed.hostname != checked.hostname or (parsed.port or 443) != checked.port:
            raise CoreError(
                ErrorCode.SCOPE_REQUIRED,
                "Outbound pin does not match the requested URL",
                next_action="Pin the canonical hatch hostname before connecting.",
            )
        options = _options(curl_opt, checked)
        bundle = _ca_bundle()
        try:
            ws = websocket_type()
        except (TypeError, AttributeError):
            failure = _api_mismatch()
            ws = None
        else:
            failure = None
        _raise_if(failure)
        try:
            ws.connect(
                url,
                impersonate="chrome",
                verify=bundle,
                allow_redirects=False,
                timeout=timeout,
                quote=False,
                headers={"Origin": "https://muse.ai"},
                curl_options=options,
            )
        except (TypeError, AttributeError):
            failure = _api_mismatch()
        except CoreError:
            failure = _upgrade_failed(ws)
        except Exception:
            failure = _upgrade_failed(ws)
        else:
            failure = None
        if failure is not None:
            closer = getattr(ws, "close", None)
            if closer is not None:
                try:
                    closer()
                except Exception:
                    pass
            raise failure
        try:
            from curl_cffi.const import CurlInfo

            peer = _peer_text(ws.curl.getinfo(CurlInfo.PRIMARY_IP))
        except (TypeError, AttributeError):
            peer_failure = _api_mismatch()
            peer = ""
        except Exception:
            peer_failure = CoreError(
                ErrorCode.SCOPE_REQUIRED,
                "Outbound peer was not available",
                next_action="Do not use a connection whose peer address was not read.",
            )
            peer = ""
        else:
            peer_failure = None
        if peer_failure is not None or not peer:
            closer = getattr(ws, "close", None)
            if closer is not None:
                try:
                    closer()
                except Exception:
                    pass
            if peer_failure is None:
                peer_failure = CoreError(
                    ErrorCode.SCOPE_REQUIRED,
                    "Outbound peer was not available",
                    next_action="Do not use a connection whose peer address was not read.",
                )
            raise peer_failure
        return CurlSocket(ws, peer)
