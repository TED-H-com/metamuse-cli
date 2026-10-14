"""Process-local HTTP/CONNECT egress proxy for Chromium browser sessions (B13, AT-030, FR-019).

Playwright's ``context.route`` handler is invoked only for the initial URL of a navigation or
subresource request; Chromium follows subsequent 3xx redirect hops internally using its own
DNS resolution and socket connections without consulting ``context.route``. Furthermore,
``Response.server_addr()`` is only observable after a connection has already been made and the
request has been sent.

To satisfy B08's backend obligations (``docs/dev/SAFETY_API.md``), all Chromium network traffic
is routed through ``BrowserEgressProxy`` bound to ``127.0.0.1:0`` for the lifetime of a single
browser session, while Chromium is launched with:
- ``--proxy-server=http://127.0.0.1:<port>``
- ``--proxy-bypass-list=<-loopback>``
- ``--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1``
- ``--disable-quic``
- ``--force-webrtc-ip-handling-policy=disable_non_proxied_udp``

``BrowserEgressProxy`` is therefore the true network egress boundary:
1. Every ``CONNECT host:port`` tunnel and every absolute-form plain-HTTP request (including
   every 3xx redirect hop) is validated against B08 ``OutboundURLPolicy.before_request`` (or
   the test-only fixture checker) before any socket connection is attempted.
2. The proxy dials only the first validated IP literal from ``checked.addresses`` via
   ``socket.create_connection((checked.addresses[0], checked.port), ...)`` and never
   re-resolves DNS.
3. The connected socket peer (``getpeername()``) is verified against ``checked.addresses`` and
   ``checked.port`` before answering ``200`` or forwarding bytes.
4. All bytes relayed in both directions are counted against the job's ``max_bytes`` budget;
   exceeding the budget immediately terminates the tunnel.
"""

from __future__ import annotations

import ipaddress
import queue
import re
import select
import socket
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from metamuse import models as m
from metamuse import safety

if TYPE_CHECKING:
    from metamuse.facebook import EgressGuard

MAX_HEADER_BYTES = 16_384
MAX_HEADER_LINES = 64
MAX_CONCURRENT_TUNNELS = 16
RELAY_CHUNK_BYTES = 16_384
_RESOLUTION_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_TUNNELS)
ALLOWED_PLAIN_HTTP_METHODS = frozenset({"GET", "HEAD"})

_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_BRACKET_AUTHORITY_RE = re.compile(r"^\[([^\]]+)\]:([0-9]+)$")
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "proxy-connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


def build_chromium_launch_args(proxy_port: int) -> list[str]:
    """Return the pinned Chromium launch flags routing all traffic through the local proxy."""
    if (
        isinstance(proxy_port, bool)
        or not isinstance(proxy_port, int)
        or not (1 <= proxy_port <= 65535)
    ):
        raise ValueError("proxy_port must be an integer in 1..65535")
    return [
        f"--proxy-server=http://127.0.0.1:{proxy_port}",
        "--proxy-bypass-list=<-loopback>",
        "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1",
        "--disable-quic",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-sync",
        "--disable-translate",
        "--mute-audio",
        "--no-first-run",
    ]


class ProxyRequestError(Exception):
    """Raised when a client request to the local egress proxy is malformed or disallowed."""

    def __init__(self, reason: str, message: str = "Proxy request refused") -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class ParsedProxyRequest:
    method: str
    target: str
    version: str
    is_connect: bool
    check_url: str
    hostname: str
    port: int
    origin_path: str
    headers: tuple[tuple[str, str], ...]
    purpose: safety.URLPurpose
    is_navigation: bool


def _has_forbidden_controls(text: str) -> bool:
    for ch in text:
        code = ord(ch)
        if code == 0 or (code < 32 and ch not in ("\r", "\n", "\t")) or code == 127:
            return True
    return False


def _parse_port_str(port_str: str) -> int:
    if not port_str or len(port_str) > 5 or not port_str.isascii() or not port_str.isdigit():
        raise ProxyRequestError("bad_port", "Invalid port in proxy target")
    if port_str.startswith("0"):
        raise ProxyRequestError("bad_port", "Leading zero or zero port is not allowed")
    try:
        port = int(port_str)
    except ValueError:
        raise ProxyRequestError("bad_port", "Invalid port in proxy target") from None
    if not (1 <= port <= 65535):
        raise ProxyRequestError("bad_port", "Port is out of range (1..65535)")
    return port


def _is_self_proxy_target(host: str, port: int, proxy_port: int | None) -> bool:
    if proxy_port is None or port != proxy_port:
        return False
    cleaned = host.strip().lower().rstrip(".")
    if cleaned in ("localhost", "localhost.localdomain") or cleaned.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(cleaned)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def parse_proxy_request(
    raw_head: bytes,
    *,
    proxy_port: int | None = None,
) -> ParsedProxyRequest:
    """Parse and validate a single HTTP/1.x proxy request header block."""
    if not isinstance(raw_head, (bytes, bytearray)) or len(raw_head) == 0:
        raise ProxyRequestError("empty_request", "Empty proxy request")
    if len(raw_head) > MAX_HEADER_BYTES:
        raise ProxyRequestError("oversize_headers", "Proxy request headers exceed maximum size")

    body = bytes(raw_head)
    if body.endswith(b"\r\n\r\n"):
        body = body[:-4]
    elif body.endswith(b"\r\n"):
        body = body[:-2]

    try:
        decoded = body.decode("iso-8859-1")
    except UnicodeDecodeError as exc:
        raise ProxyRequestError(
            "invalid_encoding", "Request headers are not valid Latin-1"
        ) from exc

    if _has_forbidden_controls(decoded):
        raise ProxyRequestError("control_chars", "Request headers contain control characters")

    lines = decoded.split("\r\n")
    if not lines or not lines[0]:
        raise ProxyRequestError("malformed_request_line", "Missing request line")
    if len(lines) > MAX_HEADER_LINES:
        raise ProxyRequestError("too_many_headers", "Too many HTTP header lines")
    for line in lines:
        if "\r" in line or "\n" in line:
            raise ProxyRequestError("bare_newline", "Bare CR or LF in request header")

    req_parts = lines[0].split(" ")
    if len(req_parts) != 3:
        raise ProxyRequestError("malformed_request_line", "Request line must have 3 tokens")
    method, target, version = req_parts
    if not method or not target or version not in ("HTTP/1.1", "HTTP/1.0"):
        raise ProxyRequestError("malformed_request_line", "Unsupported HTTP version or empty token")

    headers: list[tuple[str, str]] = []
    header_map: dict[str, str] = {}
    for hdr_line in lines[1:]:
        if not hdr_line:
            continue
        name, sep, value = hdr_line.partition(":")
        if not sep or not name or not _TOKEN_RE.fullmatch(name):
            raise ProxyRequestError("malformed_header", "Invalid HTTP header name")
        clean_val = value.strip(" \t")
        name_lower = name.lower()
        if name_lower == "upgrade":
            raise ProxyRequestError("websocket_upgrade_refused", "Protocol upgrades are refused")
        if name_lower in ("connection", "proxy-connection"):
            tokens = {tok.strip().lower() for tok in clean_val.split(",") if tok.strip()}
            if "upgrade" in tokens:
                raise ProxyRequestError(
                    "websocket_upgrade_refused", "Connection upgrade is refused"
                )
        if name_lower == "transfer-encoding":
            raise ProxyRequestError(
                "transfer_encoding_refused", "Chunked request bodies are refused"
            )
        if name_lower == "content-length" and clean_val not in ("", "0"):
            raise ProxyRequestError("request_body_refused", "Non-empty request bodies are refused")
        headers.append((name, clean_val))
        header_map[name_lower] = clean_val

    sec_mode = header_map.get("sec-fetch-mode", "").lower()
    sec_dest = header_map.get("sec-fetch-dest", "").lower()
    is_nav = sec_mode == "navigate" or sec_dest in ("document", "iframe")
    if is_nav:
        purpose: safety.URLPurpose = "request"
    elif sec_dest in ("image", "media", "video", "audio", "font"):
        purpose = "media"
    else:
        purpose = "subresource"

    if method == "CONNECT":
        if target.startswith(("/", "http://", "https://")) or any(
            ch in target for ch in "/?#@\\ \t"
        ):
            raise ProxyRequestError("invalid_connect_target", "Invalid CONNECT authority target")
        if target.startswith("["):
            match = _BRACKET_AUTHORITY_RE.fullmatch(target)
            if match is None:
                raise ProxyRequestError("invalid_connect_target", "Malformed IPv6 CONNECT target")
            raw_host, port_str = match.group(1), match.group(2)
        else:
            raw_host, sep, port_str = target.rpartition(":")
            if not sep or not raw_host or ":" in raw_host:
                raise ProxyRequestError("bad_port", "CONNECT target must be host:port")
        port = _parse_port_str(port_str)
        if _is_self_proxy_target(raw_host, port, proxy_port):
            raise ProxyRequestError("proxy_self_target", "Requests to the proxy itself are refused")
        bracket_host = f"[{raw_host}]" if ":" in raw_host else raw_host
        check_url = f"https://{bracket_host}:{port}/"
        return ParsedProxyRequest(
            method="CONNECT",
            target=target,
            version=version,
            is_connect=True,
            check_url=check_url,
            hostname=raw_host.lower(),
            port=port,
            origin_path="",
            headers=tuple(headers),
            purpose="request",
            is_navigation=False,
        )

    if method in ALLOWED_PLAIN_HTTP_METHODS:
        if target.startswith("/") or target == "*":
            raise ProxyRequestError(
                "origin_form_refused",
                "Origin-form and asterisk-form requests to the proxy are refused",
            )
        if "\\" in target or any(ch.isspace() for ch in target):
            raise ProxyRequestError("invalid_target_url", "Invalid characters in target URL")
        try:
            parsed = urlsplit(target)
        except ValueError as exc:
            raise ProxyRequestError("invalid_target_url", "Malformed target URL") from exc
        if (
            parsed.scheme.lower() != "http"
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "@" in parsed.netloc
        ):
            raise ProxyRequestError(
                "invalid_target_url",
                "Plain-HTTP proxy requests require an http:// URL without userinfo",
            )
        if parsed.netloc.startswith("["):
            m_v6 = re.fullmatch(r"\[([^\]]+)\](?::(.*))?", parsed.netloc)
            if m_v6 is None:
                raise ProxyRequestError("invalid_target_url", "Malformed IPv6 authority")
            port_part = m_v6.group(2)
        else:
            _, colon, port_part = parsed.netloc.partition(":")
            if not colon:
                port_part = None
        if port_part is not None:
            port = _parse_port_str(port_part)
        else:
            port = 80
        raw_host = parsed.hostname
        if _is_self_proxy_target(raw_host, port, proxy_port):
            raise ProxyRequestError("proxy_self_target", "Requests to the proxy itself are refused")
        origin_path = parsed.path if parsed.path else "/"
        if not origin_path.startswith("/"):
            raise ProxyRequestError("invalid_target_url", "URL path must start with '/'")
        if parsed.query:
            origin_path = f"{origin_path}?{parsed.query}"
        return ParsedProxyRequest(
            method=method,
            target=target,
            version=version,
            is_connect=False,
            check_url=target,
            hostname=raw_host.lower(),
            port=port,
            origin_path=origin_path,
            headers=tuple(headers),
            purpose=purpose,
            is_navigation=is_nav,
        )

    raise ProxyRequestError(f"unsupported_method:{method}", f"Method {method} is refused")


class BrowserEgressProxy:
    """Per-session local HTTP/CONNECT proxy enforcing B08 OutboundURLPolicy and pinned dialing."""

    def __init__(
        self,
        egress: EgressGuard,
        *,
        max_bytes: int | None = None,
        max_concurrent_tunnels: int = MAX_CONCURRENT_TUNNELS,
        idle_timeout_s: float = 30.0,
    ) -> None:
        self.egress = egress
        self.max_bytes = max_bytes
        self.max_concurrent_tunnels = max(1, int(max_concurrent_tunnels))
        self.idle_timeout_s = max(0.5, float(idle_timeout_s))
        self._lock = threading.Lock()
        self._dial_lock = threading.Lock()
        self._closed = threading.Event()
        self._active_tunnels = 0
        self._active_sockets: set[socket.socket] = set()
        self._worker_threads: list[threading.Thread] = []
        self.bytes_relayed = 0
        self.byte_limit_exceeded = False

        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind(("127.0.0.1", 0))
        self._server_sock.listen(64)
        self.port: int = int(self._server_sock.getsockname()[1])
        self.egress.proxy_port = self.port

        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name=f"metamuse-egress-proxy-{self.port}",
            daemon=True,
        )
        self._accept_thread.start()

    def _register_socket(self, sock: socket.socket) -> bool:
        with self._lock:
            if self._closed.is_set():
                sock.close()
                return False
            self._active_sockets.add(sock)
            return True

    def _unregister_socket(self, sock: socket.socket) -> None:
        with self._lock:
            self._active_sockets.discard(sock)

    def _close_socket(self, sock: socket.socket | None) -> None:
        if sock is None:
            return
        self._unregister_socket(sock)
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def _charge_bytes(self, count: int) -> bool:
        if count <= 0:
            return not self.byte_limit_exceeded
        with self._lock:
            self.bytes_relayed += count
            self.egress.bytes_relayed = self.bytes_relayed
            if self.max_bytes is not None and self.bytes_relayed > self.max_bytes:
                if not self.byte_limit_exceeded:
                    self.byte_limit_exceeded = True
                    self.egress.mark_byte_limit_exceeded()
                return False
            return True

    def _accept_loop(self) -> None:
        while not self._closed.is_set():
            try:
                client_sock, _ = self._server_sock.accept()
            except OSError:
                break
            if self._closed.is_set():
                self._close_socket(client_sock)
                break
            if not self._register_socket(client_sock):
                break
            t = threading.Thread(
                target=self._handle_client,
                args=(client_sock,),
                daemon=True,
            )
            with self._lock:
                self._worker_threads = [w for w in self._worker_threads if w.is_alive()]
                self._worker_threads.append(t)
            t.start()

    def _send_forbidden(self, client_sock: socket.socket) -> None:
        try:
            client_sock.sendall(
                b"HTTP/1.1 403 Forbidden\r\n"
                b"X-MetaMuse-Proxy-Aborted: 1\r\n"
                b"Content-Length: 0\r\n"
                b"Connection: close\r\n\r\n"
            )
        except OSError:
            pass

    def _read_request_head(self, client_sock: socket.socket) -> tuple[bytes, bytes] | None:
        buf = bytearray()
        while b"\r\n\r\n" not in buf:
            if len(buf) >= MAX_HEADER_BYTES:
                raise ProxyRequestError(
                    "oversize_headers", "Proxy request headers exceed maximum size"
                )
            try:
                chunk = client_sock.recv(min(4096, MAX_HEADER_BYTES - len(buf) + 4))
            except OSError as exc:
                if not buf:
                    return None
                raise ProxyRequestError("read_error", "Failed reading proxy request") from exc
            if not chunk:
                if not buf:
                    return None
                raise ProxyRequestError(
                    "incomplete_headers", "Client closed connection before header end"
                )
            buf.extend(chunk)
            idx = buf.find(b"\r\n\r\n")
            if idx != -1:
                head_end = idx + 4
                if head_end > MAX_HEADER_BYTES:
                    raise ProxyRequestError(
                        "oversize_headers", "Proxy request headers exceed maximum size"
                    )
                return bytes(buf[:head_end]), bytes(buf[head_end:])
        return None

    def _handle_client(self, client_sock: socket.socket) -> None:
        upstream_sock: socket.socket | None = None
        slot_acquired = False
        try:
            with self._lock:
                if self._active_tunnels >= self.max_concurrent_tunnels:
                    self.egress.record_proxy_protocol_refusal(
                        hostname="proxy",
                        reason="max_concurrent_tunnels",
                    )
                    self._send_forbidden(client_sock)
                    return
                self._active_tunnels += 1
                slot_acquired = True

            client_sock.settimeout(self.idle_timeout_s)
            read_res = self._read_request_head(client_sock)
            if read_res is None:
                return
            head_bytes, leftover = read_res
            parsed = parse_proxy_request(head_bytes, proxy_port=self.port)

            checked = self._check_target_bounded(parsed)

            dial_ip = checked.addresses[0]
            # Serialize the closed-state check and dial with shutdown completion.
            with self._dial_lock:
                if self._closed.is_set():
                    return
                upstream_sock = socket.create_connection(
                    (dial_ip, checked.port),
                    timeout=self.idle_timeout_s,
                )
                if not self._register_socket(upstream_sock):
                    return
            upstream_sock.settimeout(self.idle_timeout_s)

            peer_raw = upstream_sock.getpeername()
            peer_ip = str(ipaddress.ip_address(peer_raw[0]))
            peer_port = int(peer_raw[1])
            self.egress.verify_proxy_peer(
                checked,
                peer_ip=peer_ip,
                peer_port=peer_port,
            )

            if parsed.is_connect:
                client_sock.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if leftover:
                    if not self._charge_bytes(len(leftover)):
                        return
                    upstream_sock.sendall(leftover)
                self._splice_tunnel(client_sock, upstream_sock)
            else:
                if leftover:
                    raise ProxyRequestError(
                        "unexpected_body", "Unexpected body bytes on GET/HEAD request"
                    )
                self._forward_plain_http(client_sock, upstream_sock, parsed, checked)
        except ProxyRequestError as exc:
            self.egress.record_proxy_protocol_refusal(
                hostname="proxy",
                reason=exc.reason,
            )
            self._send_forbidden(client_sock)
        except m.CoreError:
            self._send_forbidden(client_sock)
        except (ValueError, UnicodeError, OverflowError):
            self.egress.record_proxy_protocol_refusal(hostname="proxy", reason="malformed_request")
            self._send_forbidden(client_sock)
        except OSError:
            pass
        finally:
            self._close_socket(upstream_sock)
            self._close_socket(client_sock)
            if slot_acquired:
                with self._lock:
                    self._active_tunnels = max(0, self._active_tunnels - 1)

    def _check_target_bounded(self, parsed: ParsedProxyRequest) -> safety.CheckedURL:
        if self._closed.is_set() or not _RESOLUTION_SLOTS.acquire(blocking=False):
            raise ProxyRequestError("resolution_unavailable")
        result: queue.Queue[object] = queue.Queue(maxsize=1)

        def resolve() -> None:
            try:
                result.put(
                    self.egress.check_proxy_target(
                        parsed.check_url,
                        method=parsed.method,
                        is_connect=parsed.is_connect,
                        purpose=parsed.purpose,
                        is_navigation=parsed.is_navigation,
                    )
                )
            except Exception as exc:
                result.put(exc)
            finally:
                _RESOLUTION_SLOTS.release()

        worker = threading.Thread(target=resolve, daemon=True, name="metamuse-egress-resolution")
        try:
            worker.start()
        except Exception:
            _RESOLUTION_SLOTS.release()
            raise ProxyRequestError("resolution_unavailable") from None
        deadline = time.monotonic() + self.idle_timeout_s
        while not self._closed.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProxyRequestError("resolution_timeout")
            try:
                checked = result.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue
            if self._closed.is_set():
                break
            if isinstance(checked, m.CoreError):
                raise checked
            if isinstance(checked, Exception):
                raise ProxyRequestError("resolution_failed")
            assert isinstance(checked, safety.CheckedURL)
            return checked
        raise ProxyRequestError("proxy_closed")

    def _forward_plain_http(
        self,
        client_sock: socket.socket,
        upstream_sock: socket.socket,
        parsed: ParsedProxyRequest,
        checked: safety.CheckedURL,
    ) -> None:
        out_lines = [f"{parsed.method} {parsed.origin_path} HTTP/1.1"]
        has_host = False
        for k, v in parsed.headers:
            kl = k.lower()
            if kl in _HOP_BY_HOP_HEADERS:
                continue
            if kl == "host":
                has_host = True
            out_lines.append(f"{k}: {v}")
        if not has_host:
            host_hdr = (
                checked.hostname
                if checked.port in (80, 443)
                else f"{checked.hostname}:{checked.port}"
            )
            out_lines.append(f"Host: {host_hdr}")
        out_lines.append("Connection: close")
        out_lines.append("\r\n")
        payload = "\r\n".join(out_lines).encode("iso-8859-1")

        if not self._charge_bytes(len(payload)):
            return
        upstream_sock.sendall(payload)

        while not self._closed.is_set():
            try:
                chunk = upstream_sock.recv(RELAY_CHUNK_BYTES)
            except OSError:
                break
            if not chunk:
                break
            if not self._charge_bytes(len(chunk)):
                break
            try:
                client_sock.sendall(chunk)
            except OSError:
                break

    def _splice_tunnel(self, client_sock: socket.socket, upstream_sock: socket.socket) -> None:
        sockets = [client_sock, upstream_sock]
        while not self._closed.is_set():
            try:
                readable, _, exceptional = select.select(sockets, [], sockets, self.idle_timeout_s)
            except (OSError, ValueError):
                break
            if exceptional or not readable:
                break
            for src in readable:
                try:
                    data = src.recv(RELAY_CHUNK_BYTES)
                except OSError:
                    return
                if not data:
                    return
                if not self._charge_bytes(len(data)):
                    return
                dst = upstream_sock if src is client_sock else client_sock
                try:
                    dst.sendall(data)
                except OSError:
                    return

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        with self._dial_lock:
            pass  # Any dial that entered before closure has finished and cannot recur.
        self._close_socket(self._server_sock)
        with self._lock:
            active = list(self._active_sockets)
            workers = list(self._worker_threads)
        for sock in active:
            self._close_socket(sock)
        self._accept_thread.join(timeout=2.0)
        for worker in workers:
            worker.join(timeout=1.0)
