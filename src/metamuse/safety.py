"""Local safety boundaries. Source and model content never issue permissions."""

from __future__ import annotations

import base64
import binascii
import errno
import html
import ipaddress
import json
import os
import re
import shlex
import socket
import sqlite3
import stat
import unicodedata
from array import array
from bisect import bisect_left
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache, wraps
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Literal
from urllib.parse import unquote, urlsplit, urlunsplit

from platformdirs import user_config_path
from pydantic import Field, ValidationError, model_validator

from metamuse import models as m
from metamuse.paths import WorkspacePaths

if TYPE_CHECKING:
    from metamuse.app import AppContext
    from metamuse.jobs import Operation, StepContext
    from metamuse.store import Store


# Auth/permit commands are human-only and are deliberately absent from MCP capabilities.
IMPLEMENTED_TOOLS: frozenset[str] = frozenset()


def register_operations(registry: dict[str, Operation] | None = None) -> None:
    """No asynchronous operations are needed by this module."""


# Approval callbacks prepare actions only. They must never issue grants or perform IO abroad.
_APPROVAL_PREVIEWS: dict[str, tuple[type[m.Model], Callable]] = {}


def register_approval_preview(
    operation: str, fn: Callable, *, request_model: type[m.Model]
) -> None:
    """Register a fixed feature callback at startup; registration is idempotent."""
    entry = (request_model, fn)
    if operation in _APPROVAL_PREVIEWS and _APPROVAL_PREVIEWS[operation] != entry:
        raise ValueError("Approval operation already registered")
    _APPROVAL_PREVIEWS[operation] = entry


def approval_preview(ctx: AppContext, operation: str, request: Any) -> m.ApprovalPreview:
    from metamuse.app import register_builtin_operations

    register_builtin_operations()
    if operation not in _APPROVAL_PREVIEWS:
        raise m.CoreError(m.ErrorCode.UNSUPPORTED, "Operation has no approval preview")
    model, fn = _APPROVAL_PREVIEWS[operation]
    validated = model.model_validate(request, context={"limits": ctx.limits})
    preview = fn(ctx, validated)
    if preview.operation != operation:
        raise m.CoreError(m.ErrorCode.STORAGE, "Approval callback returned another operation")
    _label(preview.provider)
    _label(preview.account)
    return preview


def approval_failure(ctx: AppContext, request: m.Model, error: m.CoreError) -> m.Envelope:
    """Shared CLI/MCP hook; keep unrelated policy/auth errors without a preview."""
    if error.detail.code != m.ErrorCode.SCOPE_REQUIRED:
        return m.failure(error)
    for operation, (model, _) in _APPROVAL_PREVIEWS.items():
        if isinstance(request, model):
            try:
                preview = approval_preview(ctx, operation, request.model_dump(mode="python"))
            except m.CoreError as refusal:
                if refusal.detail.code == m.ErrorCode.UNSUPPORTED:
                    return m.failure(error)
                return m.failure(refusal)
            except Exception as exc:
                ctx.logger.warning("Approval preview callback failed (%s)", type(exc).__name__)
                return m.failure(error)
            return m.Envelope(
                status="error",
                error=error.detail.model_copy(update={"next_action": preview.next_action}),
                approval=preview,
            )
    return m.failure(error)


def operator_command(ctx: AppContext, *args: str) -> str:
    return shlex.join(["metamuse", "--workspace", str(ctx.paths.root), "--json", *args])


def approval_expiry(ctx: AppContext, expires_at: str | None, request_key: str) -> str:
    """Resolve once for human review and storage, preserving the expiry on a replay."""
    if expires_at is None:
        with ctx.store.connection() as conn:
            receipt = conn.execute(
                "SELECT response FROM request_receipts "
                "WHERE operation='permit.create' AND request_key=?",
                (request_key,),
            ).fetchone()
        if receipt:
            from metamuse.store import _json_loads

            try:
                expires_at = _json_loads(receipt["response"])["expires_at"]
            except (ValueError, TypeError, KeyError):
                raise m.CoreError(m.ErrorCode.STORAGE, "Stored receipt is invalid") from None
        else:
            expires_at = (_timestamp(ctx.clock()) + timedelta(hours=24)).isoformat()
    return _timestamp(expires_at).isoformat()


def approve_permit(
    ctx: AppContext,
    preview: m.ApprovalPreview,
    *,
    expires_at: str | None,
    request_key: str,
    max_uses: int = 1,
) -> m.Envelope:
    """Called by the human CLI only, after confirmation of a freshly recomputed preview."""
    ctx.require_interactive()
    expires_at = approval_expiry(ctx, expires_at, request_key)
    scope = (
        {"packet_digests": [preview.packet_digest]}
        if preview.kind == "share"
        else {"operations": [preview.permit_operation], "target_digests": [preview.action_digest]}
    )
    return create_permit(
        ctx,
        m.PermitCreateRequest(
            project_id=preview.project_id,
            kind=preview.kind,
            scope=scope,
            provider=preview.provider,
            account=preview.account,
            issuer="local_operator",
            expires_at=expires_at,
            max_uses=max_uses,
            exact_digest=preview.action_digest,
            request_key=request_key,
        ),
    )


# --- Local input files (B05/B06) ---


@dataclass
class InputFile:
    """Opened regular file. Byte accounting uses reads, never st_size alone."""

    path: Path
    max_bytes: int
    _handle: BinaryIO
    bytes_read: int = field(default=0, init=False)

    def read(self, size: int = -1) -> bytes:
        chunk = self._handle.read(size)
        self.bytes_read += len(chunk)
        if self.bytes_read > self.max_bytes:
            raise m.CoreError(
                m.ErrorCode.LIMIT_REACHED,
                "Input exceeds the byte limit",
                next_action="Choose a smaller file or raise the workspace byte limit",
            )
        return chunk


def _reject_path_text(path: str) -> None:
    if not isinstance(path, str) or path.strip() == "":
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Input path is empty")
    if "\x00" in path or "\n" in path or "\r" in path:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Input path contains a forbidden character")


def _prepare_roots(roots: list[str | Path]) -> list[Path]:
    prepared: list[Path] = []
    for root in roots:
        text = os.fspath(root)
        _reject_path_text(text)
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        if candidate.is_symlink() or not candidate.is_dir():
            raise m.CoreError(
                m.ErrorCode.INPUT_INVALID,
                "Import root does not exist or is not a real directory",
                next_action="Set app.import_roots to existing directories, not symbolic links",
            )
        prepared.append(candidate.resolve())
    return prepared


def _lexical_absolute(path: str) -> Path:
    """Join a relative path to the cwd without collapsing ``..``.

    ``..`` is applied later, one component at a time, and only after that
    directory was opened and was not a symlink. Lexical normalization would
    erase a symlink and read a different file.
    """

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate


def _reject_option_components(path: Path) -> None:
    if os.fspath(path).startswith("-") or any(part.startswith("-") for part in path.parts):
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID,
            "Input path has an option-like component",
            next_action="Rename the file so no path component starts with '-'",
        )


def _walk_without_symlinks(path: Path) -> Path:
    probe = Path(path.anchor)
    for part in path.parts[1:]:
        if part in ("", "."):
            continue
        if part == "..":
            probe = probe.parent
            continue
        probe = probe / part
        try:
            if probe.is_symlink():
                raise m.CoreError(
                    m.ErrorCode.INPUT_INVALID,
                    "Input path contains a symbolic link",
                    next_action="Pass the regular file itself, not a symbolic link",
                )
        except OSError as exc:
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Input path cannot be read") from exc
    return probe


def _inside(child: Path, root: Path) -> bool:
    try:
        child.relative_to(root)
    except ValueError:
        return False
    return child != root


def _open_error(exc: OSError) -> m.CoreError:
    code = exc.errno
    if code in (errno.EACCES, errno.EPERM):
        return m.CoreError(m.ErrorCode.ACCESS_DENIED, "Input file is not readable")
    if code == errno.ELOOP:
        return m.CoreError(
            m.ErrorCode.INPUT_INVALID,
            "Input path is a symbolic link",
            next_action="Pass the regular file itself, not a symbolic link",
        )
    if code == errno.ENOENT:
        return m.CoreError(m.ErrorCode.INPUT_INVALID, "Input file does not exist")
    return m.CoreError(m.ErrorCode.INPUT_INVALID, "Input file cannot be opened")


@contextmanager
def open_input_file(
    path: str, *, max_bytes: int, roots: list[str | Path] | None = None
) -> Iterator[InputFile]:
    """Open one regular file inside the configured roots.

    NUL bytes, option-like names, symbolic links, and non-regular files are
    rejected. The byte limit is enforced by counting reads.
    """

    _reject_path_text(path)
    if max_bytes < 0:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Byte limit is negative")
    if not roots:
        raise m.CoreError(
            m.ErrorCode.SCOPE_REQUIRED,
            "No import root is configured",
            next_action=(
                "Set app.import_roots in the workspace metamuse.toml "
                "to the directories local imports may read"
            ),
        )
    allowed = _prepare_roots(roots)
    lexical = _lexical_absolute(path)
    _reject_option_components(lexical)
    walked = _walk_without_symlinks(lexical)
    if not any(_inside(walked, root) for root in allowed):
        raise m.CoreError(
            m.ErrorCode.SCOPE_DENIED,
            "Input path is outside the configured import roots",
            next_action=(
                "Move the file under an import root or add its directory to app.import_roots"
            ),
        )
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        fd = os.open(walked, flags)
    except OSError as exc:
        raise _open_error(exc) from exc
    handle: BinaryIO | None = None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Input path is not a regular file")
        real = Path(os.path.realpath(walked))
        if not any(_inside(real, root) for root in allowed):
            raise m.CoreError(
                m.ErrorCode.SCOPE_DENIED,
                "Input path resolves outside the configured import roots",
                next_action=(
                    "Move the file under an import root or add its directory to app.import_roots"
                ),
            )
        compared = os.stat(real)
        if (info.st_dev, info.st_ino) != (compared.st_dev, compared.st_ino):
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Input path changed while it was opened")
        handle = os.fdopen(fd, "rb")
        fd = -1
        yield InputFile(path=real, max_bytes=max_bytes, _handle=handle)
    except m.CoreError:
        raise
    except OSError as exc:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Input file cannot be opened") from exc
    finally:
        if handle is not None:
            handle.close()
        elif fd >= 0:
            os.close(fd)


def import_auth(ctx: AppContext, request: m.AuthImportRequest) -> m.Envelope:
    """Import only the named file. Never discover browser profiles or credentials."""
    ctx.require_interactive()
    _label(request.provider, optional=True)
    _label(request.account, optional=True)
    secret = _read_secret_file(Path(request.path).expanduser(), SECRET_MAX_BYTES)
    metadata = SecretStore().put(
        secret, provider=request.provider, account=request.account, created_at=ctx.clock()
    )
    return m.Envelope(status="ok", data=metadata)


def create_permit(ctx: AppContext, request: m.PermitCreateRequest) -> m.Envelope:
    """Issue local operator intent; never call this from source/model/MCP handlers."""
    ctx.require_interactive()
    scope = _parse_scope(request.kind, request.scope)
    _label(request.provider)
    _label(request.account)
    _label(request.issuer)
    if request.max_uses > PERMIT_MAX_USES:
        _scope_error("Permit use limit is too large")
    expires = _timestamp(request.expires_at)
    research_preflight = None
    if scope.research is not None:
        from metamuse._muse_research import prepare_creation

        research_preflight = prepare_creation(ctx, scope.research, request)
    with ctx.store.transaction() as conn:
        replayed = (
            conn.execute(
                "SELECT 1 FROM request_receipts WHERE operation='permit.create' AND request_key=?",
                (request.request_key,),
            ).fetchone()
            is not None
        )

        def issue() -> dict[str, Any]:
            now = _timestamp(ctx.clock())
            if not now < expires <= now + PERMIT_MAX_LIFETIME:
                _scope_error("Permit expiry must be in the next 30 days")
            ctx.store.get(m.Project, request.project_id, conn=conn)
            if scope.research is not None:
                from metamuse._muse_research import validate_creation

                validate_creation(ctx, conn, scope.research, request, preflight=research_preflight)
            if isinstance(scope, ShareScope) and scope.snapshot is not None:
                ctx.store.validate_evidence(
                    request.project_id, scope.snapshot.selection, conn=conn, source_only=True
                )
            permit = m.Permit(
                id=m.new_id("pmt"),
                project_id=request.project_id,
                kind=request.kind,
                scope=scope.model_dump(mode="json", exclude_none=True),
                provider=request.provider,
                account=request.account,
                issuer=request.issuer,
                issued_at=now.isoformat(),
                expires_at=expires.isoformat(),
                max_uses=request.max_uses,
                exact_digest=request.exact_digest,
            )
            ctx.store.insert(permit, conn=conn)
            ctx.store.event(conn, "permit.created", object_id=permit.id)
            return permit.model_dump(mode="json")

        data = ctx.store.mutate_once(conn, "permit.create", request.request_key, request, issue)
        # Receipt replay identifies the grant; mutable consumption/revocation state is always live.
        data = ctx.store.get(m.Permit, data["id"], conn=conn).model_dump(mode="json")
        data["replayed"] = replayed
    return m.Envelope(status="ok", data=data)


# --- URL policy (B08) ---

URL_MAX_BYTES = 8192
URLPurpose = Literal["seed", "request", "redirect", "subresource", "media"]
Resolver = Callable[[str], Iterable[str]]
_IPv4 = ipaddress.IPv4Address
_IPv6 = ipaddress.IPv6Address
_IP = _IPv4 | _IPv6
_V4_DENY = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.88.99.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
    )
)


@dataclass(frozen=True)
class CheckedURL:
    # Paths/queries can contain signed credentials. Do not expose them through repr.
    url: str = field(repr=False)
    hostname: str
    port: int
    addresses: tuple[str, ...]
    purpose: URLPurpose


def _url_error() -> None:
    raise m.CoreError(
        m.ErrorCode.SCOPE_DENIED,
        "Outbound URL is outside the network policy",
        next_action="Use an approved public HTTP(S) destination and a controlled backend",
    ) from None


def _literal_ip(host: str) -> _IP | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    # inet_aton is a local parser (no DNS); it recognizes historical browser IPv4 forms.
    try:
        return _IPv4(socket.inet_aton(host))
    except OSError:
        # Numeric-looking hosts must not fall through to a different DNS/browser parser.
        if re.fullmatch(r"(?:[0-9]+|0[xX][0-9a-fA-F]*)", host.rsplit(".", 1)[-1]):
            _url_error()
        return None


def _hostname(value: str) -> str:
    if not value or value.endswith("..") or any(c in value for c in "%\\/@[]"):
        _url_error()
    host = value.rstrip(".").lower()
    if ":" in host:
        try:
            return str(_IPv6(host))
        except ValueError:
            _url_error()
    try:
        host = host.encode("idna").decode("ascii").lower().rstrip(".")
        # Validate existing A-labels as well as Unicode inputs; roundtrip for normalization.
        host = host.encode("ascii").decode("idna").encode("idna").decode("ascii").lower()
    except UnicodeError:
        _url_error()
    if len(host) > 253 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in host.split(".")
    ):
        _url_error()
    literal = _literal_ip(host)
    return str(literal) if literal is not None else host


def normalize_hostname(value: str) -> str:
    """IDNA A-label. One trailing dot is stripped. A second trailing dot is refused."""

    return _hostname(value)


def _public_ip(address: _IP, *, fixture_loopback: bool = False) -> bool:
    if isinstance(address, _IPv6) and address.ipv4_mapped is not None:
        return _public_ip(address.ipv4_mapped, fixture_loopback=fixture_loopback)
    if fixture_loopback and address.is_loopback:
        return True
    if isinstance(address, _IPv4):
        return address.is_global and not any(address in net for net in _V4_DENY)
    # Exclude compatible IPv4, NAT64, 6to4 and Teredo: translators can reach denied IPv4.
    # Only ordinary public IPv6 unicast is supported by this policy.
    return (
        address in ipaddress.ip_network("2000::/3")
        and address not in ipaddress.ip_network("2001::/23")
        and address not in ipaddress.ip_network("2002::/16")
        and address not in ipaddress.ip_network("3fff::/20")
        and address.is_global
        and not address.is_multicast
        and not address.is_reserved
    )


def _check_url(
    url: str,
    *,
    purpose: URLPurpose,
    resolver: Resolver | None,
    allowed_hosts: Iterable[str],
    fixture_loopback: bool = False,
) -> CheckedURL:
    try:
        if (
            purpose not in ("seed", "request", "redirect", "subresource", "media")
            or not isinstance(url, str)
            or len(url.encode("utf-8")) > URL_MAX_BYTES
            or "\\" in url
            or any(c.isspace() or unicodedata.category(c) in ("Cc", "Cf") for c in url)
            or any(unicodedata.category(c) in ("Cc", "Cf") for c in unquote(url))
        ):
            _url_error()
        parsed = urlsplit(url)
        if (
            parsed.scheme.lower() not in ("http", "https")
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or "@" in parsed.netloc
            or not parsed.hostname
        ):
            _url_error()
        if parsed.netloc.startswith("[") and not re.fullmatch(
            r"\[[^\]]+\](?::[0-9]+)?", parsed.netloc
        ):
            _url_error()
        host = _hostname(parsed.hostname)
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        if parsed.port == 0 or parsed.netloc.endswith(":"):
            _url_error()
        allowed = {_hostname(name) for name in allowed_hosts}
        if allowed and host not in allowed:
            _url_error()
        literal = _literal_ip(host)
        if literal is not None:
            addresses = (literal,)
        else:
            if resolver is None:
                _url_error()  # No implicit DNS/network request, ever.
            try:
                resolved = resolver(host)
                if isinstance(resolved, (str, bytes)):
                    _url_error()
                addresses = tuple(ipaddress.ip_address(value) for value in islice(resolved, 65))
            except Exception:
                _url_error()
            if len(addresses) > 64:
                _url_error()
        if not addresses or any(
            not _public_ip(address, fixture_loopback=fixture_loopback) for address in addresses
        ):
            _url_error()
        if port not in (80, 443) and not (
            fixture_loopback
            and all(
                (
                    address.ipv4_mapped
                    if isinstance(address, _IPv6) and address.ipv4_mapped
                    else address
                ).is_loopback
                for address in addresses
            )
        ):
            _url_error()
        authority = f"[{host}]" if ":" in host else host
        if parsed.port is not None:
            authority += f":{parsed.port}"
        return CheckedURL(
            urlunsplit(
                (parsed.scheme.lower(), authority, parsed.path, parsed.query, parsed.fragment)
            ),
            host,
            port,
            tuple(sorted({str(address) for address in addresses})),
            purpose,
        )
    except m.CoreError:
        raise
    except (ValueError, TypeError, UnicodeError, OSError):
        _url_error()


def check_url(
    url: str,
    *,
    purpose: URLPurpose,
    resolver: Resolver | None = None,
    allowed_hosts: Iterable[str] = (),
) -> CheckedURL:
    """Check all supplied DNS addresses. This function never resolves DNS itself."""
    return _check_url(url, purpose=purpose, resolver=resolver, allowed_hosts=allowed_hosts)


def check_fixture_url(
    url: str,
    *,
    purpose: URLPurpose,
    resolver: Resolver | None = None,
    allowed_hosts: Iterable[str] = (),
) -> CheckedURL:
    """Explicit synthetic-server mode: also allow loopback, never other private networks."""
    return _check_url(
        url, purpose=purpose, resolver=resolver, allowed_hosts=allowed_hosts, fixture_loopback=True
    )


@dataclass(frozen=True)
class OutboundURLPolicy:
    """Backend hook for EVERY actual request, redirect and browser/media subresource.

    Connect to one of the returned addresses (with original hostname TLS verification),
    validate the peer, and disable library redirects/proxies that bypass this hook.
    Use process-level egress controls when dependencies cannot be intercepted.
    """

    resolver: Resolver
    allowed_hosts: tuple[str, ...] = ()

    def before_request(
        self, url: str, *, purpose: URLPurpose = "request", peer_address: str | None = None
    ) -> CheckedURL:
        result = check_url(
            url, purpose=purpose, resolver=self.resolver, allowed_hosts=self.allowed_hosts
        )
        if peer_address is not None:
            try:
                peer = str(ipaddress.ip_address(peer_address))
            except ValueError:
                _url_error()
            if peer not in result.addresses:
                _url_error()
        return result


# --- Secret references (B08) ---

SECRET_MAX_BYTES = 1024 * 1024
_AUTH_REF = re.compile(r"auth_[0-9a-f]{32}")


def _secret_error() -> None:
    raise m.CoreError(
        m.ErrorCode.AUTH_REQUIRED,
        "Secret file or reference is unavailable or unsafe",
        next_action="Import an owned regular file with mode 0600 using metamuse auth import",
    ) from None


def _open_directory(path: Path, *, create: bool = False) -> int:
    """Walk dirfds with NOFOLLOW, including parents, instead of racing realpath/open."""
    if ".." in path.parts:
        _secret_error()
    absolute = path.expanduser()
    if not absolute.is_absolute():
        absolute = Path.cwd() / absolute
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_secret_file(path: Path, max_bytes: int) -> bytes:
    if ".." in path.parts:
        _secret_error()
    try:
        parent = _open_directory(path.parent)
        try:
            # NONBLOCK prevents a named pipe/device from hanging before fstat rejects it.
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        finally:
            os.close(parent)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_mode & 0o077
                or not 0 < before.st_size <= max_bytes
            ):
                _secret_error()
            secret = stream.read(max_bytes + 1)
            after = os.fstat(stream.fileno())
            if (
                not 0 < len(secret) <= max_bytes
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or len(secret) != after.st_size
            ):
                _secret_error()
            return secret
    except (OSError, ValueError):
        _secret_error()


_AUTH_MANUAL_CLEANUP = "Manually remove the non-regular entry from the private auth directory"


class SecretStore:
    """POSIX private-file fallback. No keyring dependency and no encryption claim."""

    def __init__(self, root: Path | None = None):
        self.root = (
            root if root is not None else user_config_path("metamuse", appauthor=False) / "auth"
        )

    def _directory(self, *, create: bool = True) -> int:
        if ".." in self.root.parts:
            _secret_error()
        try:
            # Config overrides must not put secrets in a checkout.
            WorkspacePaths.resolve(self.root)
        except m.CoreError:
            _secret_error()
        fd = _open_directory(self.root, create=create)
        try:
            if os.fstat(fd).st_uid != os.getuid():
                _secret_error()
            os.fchmod(fd, 0o700)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def put(
        self, secret: bytes, *, provider: str | None, account: str | None, created_at: str
    ) -> dict[str, Any]:
        _label(provider, optional=True)
        _label(account, optional=True)
        if not isinstance(secret, bytes) or not 0 < len(secret) <= SECRET_MAX_BYTES:
            _secret_error()
        metadata = {
            "auth_ref": m.new_id("auth"),
            "provider": provider,
            "account": account,
            "storage_kind": "private_file",
            "created_at": _timestamp(created_at).isoformat(),
        }
        temporary = m.new_id("staging")
        directory = None
        try:
            directory = self._directory()
            fd = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(
                    m.canonical_json(
                        {
                            "metadata": metadata,
                            "secret_b64": base64.b64encode(secret).decode("ascii"),
                        }
                    )
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(
                temporary,
                metadata["auth_ref"] + ".json",
                src_dir_fd=directory,
                dst_dir_fd=directory,
            )
            os.fsync(directory)
            return metadata
        except (OSError, ValueError, TypeError):
            _secret_error()
        finally:
            if directory is not None:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
                finally:
                    os.close(directory)

    def resolve(self, auth_ref: str, *, provider: str, account: str) -> bytes:
        try:
            _label(provider)
            _label(account)
            if not isinstance(auth_ref, str) or not _AUTH_REF.fullmatch(auth_ref):
                _secret_error()
            value = json.loads(
                _read_secret_file(self.root / (auth_ref + ".json"), SECRET_MAX_BYTES * 2)
            )
            metadata = value["metadata"]
            if (
                metadata["auth_ref"] != auth_ref
                or metadata["storage_kind"] != "private_file"
                or provider != metadata["provider"]
                or account != metadata["account"]
            ):
                _secret_error()
            secret = base64.b64decode(value["secret_b64"], validate=True)
            if not 0 < len(secret) <= SECRET_MAX_BYTES:
                _secret_error()
            return secret
        except (
            m.CoreError,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            RecursionError,
            binascii.Error,
        ):
            pass
        # Suppressing display with `from None` inside the handler still retains
        # JSONDecodeError.doc through __context__. Leave the handler before raising.
        _secret_error()

    def list(self, *, corrupt: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
        """Return validated metadata only, never secret values or filesystem paths."""
        directory = None
        try:
            try:
                directory = self._directory(create=False)
            except FileNotFoundError:
                return []
            records = []
            for name in sorted(os.listdir(directory)):
                if not name.endswith(".json") or not _AUTH_REF.fullmatch(name[:-5]):
                    continue
                record = None
                try:
                    value = json.loads(_read_secret_file(self.root / name, SECRET_MAX_BYTES * 2))
                    metadata = value["metadata"]
                    if (
                        metadata["auth_ref"] != name[:-5]
                        or metadata["storage_kind"] != "private_file"
                    ):
                        _secret_error()
                    _label(metadata["provider"], optional=True)
                    _label(metadata["account"], optional=True)
                    created_at = _timestamp(metadata["created_at"]).isoformat()
                    if (
                        not 0
                        < len(base64.b64decode(value["secret_b64"], validate=True))
                        <= SECRET_MAX_BYTES
                    ):
                        _secret_error()
                    records.append(
                        {
                            "provider": metadata["provider"],
                            "account": metadata["account"],
                            "auth_ref": name[:-5],
                            "created_at": created_at,
                            "storage_kind": "private_file",
                        }
                    )
                    record = records[-1]
                except (m.CoreError, OSError, ValueError, KeyError, TypeError, RecursionError):
                    pass
                if record is None:
                    if corrupt is None:
                        _secret_error()
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    removable = info.st_uid == os.getuid() and (
                        stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    )
                    corrupt.append(
                        {
                            "auth_ref": name[:-5],
                            "reason": "Secret record is unavailable or invalid",
                            "next_action": "metamuse auth revoke --auth-ref " + name[:-5]
                            if removable
                            else (
                                "Manually remove the non-regular entry "
                                "from the private auth directory"
                            ),
                        }
                    )

            return records
        except (m.CoreError, OSError, ValueError, KeyError, TypeError, RecursionError):
            pass
        finally:
            if directory is not None:
                os.close(directory)
        _secret_error()

    def revoke(self, auth_ref: str) -> None:
        """Unlink the record itself, including a symlink, without opening its target."""
        directory = None
        manual = False
        try:
            if not isinstance(auth_ref, str) or not _AUTH_REF.fullmatch(auth_ref):
                _secret_error()
            directory = self._directory(create=False)
            name = auth_ref + ".json"
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (
                not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode))
                or info.st_uid != os.getuid()
            ):
                manual = True
                _secret_error()
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)
            return
        except (m.CoreError, OSError, ValueError, TypeError):
            pass
        finally:
            if directory is not None:
                os.close(directory)
        if manual:
            raise m.CoreError(
                m.ErrorCode.AUTH_REQUIRED,
                "Secret file or reference is unavailable or unsafe",
                next_action=_AUTH_MANUAL_CLEANUP,
            )
        _secret_error()


def auth_context(workspace: str | Path | None):
    """Read config only: listing/revoking auth must not initialize a workspace/Store."""
    from metamuse.app import AppContext
    from metamuse.config import load_config

    paths = WorkspacePaths.resolve(workspace)
    # These commands only use policy, secret_store and require_interactive.
    # No Store constructor or AppContext.open is invoked.
    return AppContext(paths, None, policy=load_config(paths.root))


def list_auth(ctx: AppContext, *, workspace: str | Path | None = None) -> m.Envelope:
    ctx.require_interactive()
    binding = ctx.policy.muse.binding
    corrupt: list[dict[str, str]] = []
    records = (ctx.secret_store or SecretStore()).list(corrupt=corrupt)
    if workspace is not None:
        for record in corrupt:
            if record["next_action"].startswith("metamuse auth revoke "):
                record["next_action"] = operator_command(
                    ctx, "auth", "revoke", "--auth-ref", record["auth_ref"]
                )
    for record in records:
        record["config_bound"] = binding is not None and binding.auth_ref == record["auth_ref"]
    return m.Envelope(
        status="partial" if corrupt else "ok",
        data={
            "references": records,
            "unavailable": corrupt,
            "next_action": "; ".join(record["next_action"] for record in corrupt)
            if corrupt
            else None,
        },
    )


def revoke_auth(ctx: AppContext, auth_ref: str) -> m.Envelope:
    ctx.require_interactive()
    (ctx.secret_store or SecretStore()).revoke(auth_ref)
    binding = ctx.policy.muse.binding
    bound = binding is not None and binding.auth_ref == auth_ref
    return m.Envelope(
        status="ok",
        data={
            "auth_ref": auth_ref,
            "revoked": True,
            "config_bound": bound,
            "next_action": "Import credentials and rebind the current config to the new auth_ref"
            if bound
            else None,
        },
    )


def resolve_secret(
    auth_ref: str,
    *,
    provider: str,
    account: str,
    secret_store: SecretStore | None = None,
) -> bytes:
    """Backend-only API. Never put its result into an envelope, event or diagnostic."""
    return (secret_store or SecretStore()).resolve(auth_ref, provider=provider, account=account)


# --- Permits (B08) ---

PERMIT_MAX_LIFETIME = timedelta(days=30)
PERMIT_MAX_USES = 1000
OperationName = Literal["muse.open", "muse.read", "collect.remote", "muse.send"]


def _scope_error(
    message: str = "Permit does not cover this action", *, missing: bool = False
) -> None:
    raise m.CoreError(
        m.ErrorCode.SCOPE_REQUIRED if missing else m.ErrorCode.SCOPE_DENIED,
        message,
        next_action="Review the exact scope and create a new permit with metamuse permit create",
    ) from None


def _grant_error(message: str = "Permit does not cover this action", *, missing=False) -> None:
    raise m.CoreError(
        m.ErrorCode.SCOPE_REQUIRED if missing else m.ErrorCode.SCOPE_DENIED,
        message,
        next_action="Repeat the call without a grant to get a fresh approval preview",
    ) from None


def _label(value: str | None, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:@-]{0,127}", value):
        _scope_error("Provider, account and issuer must be explicit non-secret labels")


def _timestamp(value: str | None) -> datetime:
    try:
        if value is None:
            _scope_error("Permit expiry is required", missing=True)
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            _scope_error("A timezone-aware timestamp is required")
        return parsed.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        _scope_error("A timezone-aware timestamp is required")


class ResearchScope(m.Model):
    """Human consent for question variation; identities and budgets are enforceable."""

    discussion_id: m.ID
    topic: str = Field(min_length=1, max_length=8192)
    open_request_key: m.RequestKey
    open_digest: m.Digest
    backend: Literal["fixture", "live"]
    packet_id: m.ID
    packet_digest: m.Digest
    selection_digest: m.Digest
    rounds: int = Field(ge=1, le=20, strict=True)
    max_reads: int = Field(ge=1, le=200, strict=True)


class AccessScope(m.Model):
    operations: list[Literal["muse.open", "muse.read", "collect.remote"]] = Field(
        min_length=1, max_length=3
    )
    target_digests: list[m.Digest] = Field(min_length=1, max_length=200)
    research: ResearchScope | None = None

    @model_validator(mode="after")
    def research_bounds(self) -> AccessScope:
        if self.research is not None and (
            self.operations != ["muse.open", "muse.read"]
            or self.target_digests != [self.research.open_digest]
        ):
            raise ValueError("Research access permits only the bound open and discussion reads")
        return self


class SnapshotScope(m.Model):
    """A displayed immutable evidence selection, not an untrusted claimed snapshot ID."""

    digest: m.Digest
    selection: list[m.EvidenceRef] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def check_digest(self) -> SnapshotScope:
        if self.digest != snapshot_digest(self.selection):
            raise ValueError("Snapshot digest mismatch")
        return self


class ShareScope(m.Model):
    packet_digests: list[m.Digest] = Field(default_factory=list, max_length=200)
    snapshot: SnapshotScope | None = None
    research: ResearchScope | None = None

    @model_validator(mode="after")
    def require_selection(self) -> ShareScope:
        if bool(self.packet_digests) == (self.snapshot is not None):
            raise ValueError("Select exact packets or one immutable snapshot")
        if self.research is not None and (
            self.snapshot is not None or self.packet_digests != [self.research.packet_digest]
        ):
            raise ValueError("Research share requires exactly the fixed packet")
        return self


class PermitUse(m.Model):
    kind: Literal["access", "share"]
    project_id: m.ID
    provider: str
    account: str
    operation: OperationName
    digest: m.Digest
    packet_id: m.ID | None = None
    discussion_id: m.ID | None = None


def snapshot_digest(selection: list[m.EvidenceRef]) -> str:
    """Order-independent digest of exact refs; scope never follows later revisions."""
    refs = sorted({m.canonical_json(ref.model_dump(mode="json")) for ref in selection})
    return m.payload_digest([json.loads(ref) for ref in refs])


def _parse_scope(kind: str, scope: dict[str, Any]) -> AccessScope | ShareScope:
    try:
        if kind == "access":
            return AccessScope.model_validate(scope)
        if kind == "share":
            return ShareScope.model_validate(scope)
    except (ValidationError, ValueError, TypeError):
        _scope_error("Invalid scope for this permit kind")
    _scope_error("Permit kind must be access or share")


def _verify_permit(
    store: Store,
    permit_id: str | None,
    use: PermitUse,
    *,
    conn: sqlite3.Connection,
    check_remaining: bool = True,
) -> m.Permit:
    if not permit_id:
        _grant_error("A permit is required", missing=True)
    row = conn.execute("SELECT * FROM permits WHERE id=?", (permit_id,)).fetchone()
    if row is None:
        _grant_error("Permit does not exist", missing=True)
    permit = store.decode(m.Permit, row)
    if (
        permit.kind != use.kind
        or permit.project_id != use.project_id
        or permit.provider != use.provider
        or permit.account != use.account
        or permit.revoked
        or _timestamp(store.clock()) >= _timestamp(permit.expires_at)
        or (check_remaining and permit.used >= permit.max_uses)
        or (permit.exact_digest is not None and permit.exact_digest != use.digest)
    ):
        _grant_error()
    scope = _parse_scope(permit.kind, permit.scope)
    if scope.research is not None:
        from metamuse._muse_research import verify_research_use

        verify_research_use(store, conn, permit, scope.research, use, check_remaining)
    if isinstance(scope, AccessScope):
        if use.operation not in scope.operations or (
            scope.research is None and use.digest not in scope.target_digests
        ):
            _grant_error()
    else:
        if use.operation != "muse.send" or use.packet_id is None:
            _grant_error()
        try:
            packet = store.get(m.Packet, use.packet_id, conn=conn)
        except m.CoreError:
            _grant_error()
        if packet.stale:
            raise m.CoreError(
                m.ErrorCode.REFERENCE_INVALID, "Packet is stale; prepare a new packet"
            )
        try:
            if packet.project_id != use.project_id:
                _grant_error()
            store.validate_evidence(use.project_id, packet.selection, conn=conn, source_only=True)
            if scope.snapshot is None:
                if packet.digest not in scope.packet_digests:
                    _grant_error()
            else:
                approved = {
                    m.canonical_json(ref.model_dump(mode="json"))
                    for ref in scope.snapshot.selection
                }
                if not packet.selection or any(
                    m.canonical_json(ref.model_dump(mode="json")) not in approved
                    for ref in packet.selection
                ):
                    _grant_error()
        except m.CoreError:
            _grant_error()
    return permit


def verify_permit(
    store: Store,
    permit_id: str | None,
    use: PermitUse,
    *,
    conn: sqlite3.Connection,
    reserve: bool = False,
) -> m.Permit:
    """Check or reserve in the caller's transaction; all changes roll back with it.

    For external actions use external_action_verifier instead: it records a durable
    reservation identity and refuses reuse of an uncertain submission.
    """
    if reserve and not conn.in_transaction:
        _grant_error("Permit reservation requires the caller's transaction")
    permit = _verify_permit(store, permit_id, use, conn=conn)
    if reserve:
        try:
            changed = conn.execute(
                "UPDATE permits SET used=used+1 WHERE id=? AND used=? AND revoked=0 "
                "AND used<max_uses AND julianday(expires_at)>julianday(metamuse_now())",
                (permit.id, permit.used),
            ).rowcount
        except sqlite3.IntegrityError:
            # Core's trigger rechecks time at the update itself; a boundary race is a denial.
            _grant_error("Permit cannot be consumed")
        if changed != 1:
            _grant_error()
        permit = permit.model_copy(update={"used": permit.used + 1})
    return permit


def revoke_permit(ctx: AppContext, permit_id: str, *, request_key: str) -> m.Envelope:
    ctx.require_interactive()
    with ctx.store.transaction() as conn:

        def revoke() -> dict[str, Any]:
            ctx.store.get(m.Permit, permit_id, conn=conn)
            conn.execute("UPDATE permits SET revoked=1 WHERE id=?", (permit_id,))
            ctx.store.event(conn, "permit.revoked", object_id=permit_id)
            return {"permit_id": permit_id, "revoked": True}

        data = ctx.store.mutate_once(
            conn, "permit.revoke", request_key, {"permit_id": permit_id}, revoke
        )
    return m.Envelope(status="ok", data=data)


def list_permits(ctx: AppContext, *, project_id: str) -> m.Envelope:
    ctx.require_interactive()
    with ctx.store.connection() as conn:
        ctx.store.get(m.Project, project_id, conn=conn)
        rows = conn.execute(
            "SELECT * FROM permits WHERE project_id=? ORDER BY issued_at,id LIMIT 201",
            (project_id,),
        ).fetchall()
        if len(rows) > 200:
            raise m.CoreError(
                m.ErrorCode.LIMIT_REACHED,
                "Permit list exceeds 200 entries",
                next_action="Use the backend verification API for a specific permit",
            )
        permits = [ctx.store.decode(m.Permit, row).model_dump(mode="json") for row in rows]
    return m.Envelope(status="ok", data={"permits": permits})


# --- External-action verifier (B08) ---


def action_digest(target: dict[str, Any], payload: Any) -> str:
    """Hash destination and actual outbound payload, excluding only the local intent ID.

    Local request keys, permit IDs and approval flags do not belong in an outbound target.
    An intent ID identifies the durable discussion/exchange, not a transport retry.
    """
    return m.payload_digest(
        {
            "target": {key: value for key, value in target.items() if key != "intent_id"},
            "payload": payload,
        }
    )


def external_action_verifier(
    step: StepContext,
    *,
    permit_id: str,
    use: PermitUse,
    load_payload: Callable[[sqlite3.Connection, dict[str, Any]], Any],
) -> Callable[[sqlite3.Connection, dict[str, Any]], str]:
    """Database-only verifier for StepContext.before_external_action.

    load_payload must read/rebuild the ACTUAL outbound payload and current destination
    from persisted domain state, never take a claimed digest/approval from model output.
    Send exactly that verified payload/target. No external I/O inside this callback.
    """
    use = PermitUse.model_validate(use.model_dump(mode="json"))
    # Keep the legacy shape, INCLUDING packet_id=None, for old durable reservations.
    # Discussion context is persisted for research; exact grants retain their old fingerprint.
    fingerprint_use = use.model_dump(mode="json", exclude={"discussion_id"})

    def verify(conn: sqlite3.Connection, target: dict[str, Any]) -> str:
        if not conn.in_transaction:
            _scope_error("External verification requires the caller's transaction")
        row = step.store.get(m.JobStep, step.step.id, conn=conn)
        job = step.store.get(m.Job, row.job_id, conn=conn)
        expected_class = (
            "external_submit" if use.operation in ("muse.open", "muse.send") else "safe_read"
        )
        if row.side_effect_class != expected_class:
            _scope_error("External operation has the wrong side effect class")
        if row.state in ("submitting", "send_uncertain", "open_uncertain", "needs_reconcile"):
            raise m.CoreError(
                m.ErrorCode.EXTERNAL_STATE_UNKNOWN,
                "Submission requires reconciliation",
                next_action="Reconcile the original submission before submitting again",
            )
        bindings = {
            "project_id": use.project_id,
            "provider": use.provider,
            "account": use.account,
            "operation": use.operation,
        }
        if use.packet_id is not None:
            bindings["packet_id"] = use.packet_id
        if (
            job.project_id != use.project_id
            or row.target != target
            or any(target.get(key) != value for key, value in bindings.items())
            or action_digest(target, load_payload(conn, target)) != use.digest
        ):
            _scope_error()
        intent_digest = None
        if row.side_effect_class == "external_submit":
            intent = target.get("intent_id")
            if not isinstance(intent, str) or not 1 <= len(intent) <= 128:
                _scope_error("A durable external intent identity is required")
            intent_digest = m.payload_digest(
                {
                    "project_id": use.project_id,
                    "provider": use.provider,
                    "account": use.account,
                    "operation": use.operation,
                    "intent_id": intent,
                }
            )
            uncertain = conn.execute(
                "SELECT 1 FROM events e JOIN job_steps s ON s.id=e.object_id "
                "WHERE e.kind='permit.use_reserved' AND e.project_id=? "
                "AND s.side_effect_class='external_submit' "
                "AND s.state IN ('submitting','send_uncertain','open_uncertain','needs_reconcile') "
                "AND (json_extract(e.detail,'$.action_digest')=? "
                "OR json_extract(e.detail,'$.intent_digest')=?) LIMIT 1",
                (use.project_id, use.digest, intent_digest),
            ).fetchone()
            if uncertain:
                raise m.CoreError(
                    m.ErrorCode.EXTERNAL_STATE_UNKNOWN,
                    "Submission requires reconciliation",
                    next_action="Reconcile the original submission before submitting again",
                )
        permit_row = conn.execute("SELECT * FROM permits WHERE id=?", (permit_id,)).fetchone()
        if permit_row is None:
            _grant_error("Permit does not exist", missing=True)
        permit = step.store.decode(m.Permit, permit_row)
        current_fingerprint_use = dict(fingerprint_use)
        if permit.scope.get("research") is not None:
            current_fingerprint_use["discussion_id"] = use.discussion_id
        fingerprint = m.payload_digest({"permit_id": permit_id, "use": current_fingerprint_use})
        reservation = conn.execute(
            "SELECT detail FROM events WHERE kind='permit.use_reserved' AND object_id=?",
            (row.id,),
        ).fetchone()
        if reservation is not None:
            if json.loads(reservation["detail"])["fingerprint"] != fingerprint:
                _scope_error("This step already reserved a different permit use")
            _verify_permit(step.store, permit_id, use, conn=conn, check_remaining=False)
            return use.digest
        verify_permit(step.store, permit_id, use, conn=conn, reserve=True)
        step.store.event(
            conn,
            "permit.use_reserved",
            job=job,
            object_id=row.id,
            detail={
                "permit_id": permit_id,
                "fingerprint": fingerprint,
                "action_digest": use.digest,
                "intent_digest": intent_digest,
            },
        )
        return use.digest

    return verify


# --- Redaction (B07/B11/B16a) ---

_REDACTED = "[REDACTED]"
_NAME = r"[A-Za-z_][A-Za-z0-9_.-]{0,127}+"


def _short_name_cache(function):
    """Retain bounded names while classifying longer names in full."""
    cached = lru_cache(maxsize=2048)(function)

    @wraps(function)
    def classify(key):
        return cached(key) if len(key) <= 256 else function(key)

    classify.cache_info = cached.cache_info
    classify.cache_clear = cached.cache_clear
    classify.cache_parameters = cached.cache_parameters
    return classify


@_short_name_cache
def _credential_name(key: str) -> str:
    for _ in range(3):
        key = unquote(key)
    return key.lower().replace("_", "").replace("-", "")


@_short_name_cache
def redaction_sensitive_key(key: str) -> bool:
    """Credential names, with closed pagination/tokenizer exclusions by name."""
    name = _credential_name(key)
    if re.fullmatch(r"[a-z0-9.]+", name, re.ASCII) is None:
        return False
    leaf = name.rsplit(".", 1)[-1]
    if (
        leaf == "cursor"
        or ("page" in leaf and leaf.endswith("token"))
        or (leaf.startswith(("next", "continuation", "sync")) and leaf.endswith("token"))
        or leaf
        in {
            "padtoken",
            "eostoken",
            "bostoken",
            "unktoken",
            "septoken",
            "clstoken",
            "masktoken",
            "maxtokens",
            "maxnewtokens",
            "numtokens",
        }
        or leaf.endswith("tokenid")
    ):
        return False
    return (
        leaf
        in {
            "headers",
            "authorization",
            "proxyauthorization",
            "auth",
            "credentials",
            "cookie",
            "cookieheader",
            "setcookie",
            "accesstoken",
            "authtoken",
            "apikey",
            "xapikey",
            "token",
            "refreshtoken",
            "idtoken",
            "clientsecret",
            "secret",
            "password",
            "passwd",
            "pwd",
            "pass",
            "privatekey",
            "passphrase",
            "session",
            "sessionid",
            "sessioncookie",
            "sessiontoken",
            "sid",
            "phpsessid",
            "jsessionid",
            "asp.netsessionid",
            "connect.sid",
            "csrftoken",
            "nextauth.sessiontoken",
            "xs",
            "cuser",
            "datr",
            "localpath",
            "signature",
            "sig",
            "policy",
            "keypairid",
            "awsaccesskeyid",
            "googleaccessid",
        }
        or name in {"connect.sid", "asp.netsessionid", "nextauth.sessiontoken"}
        or leaf.endswith(
            (
                "sess",
                "sessid",
                "session",
                "token",
                "secret",
                "cookie",
                "password",
                "passwd",
                "pwd",
                "pass",
                "apikey",
                "secretkey",
                "accesskey",
                "privatekey",
            )
        )
        or unquote(unquote(unquote(key)))
        .lower()
        .startswith(("x-amz-", "x-goog-", "__secure-", "__host-"))
    )


_TYPES = frozenset({"str", "string", "bytes", "int", "bool", "Any", "None", "SecretStr"})


def _type_reference(value: str) -> bool:
    # Bounded depth, closed vocabulary; no arbitrary function calls.
    if len(value) > 256:
        return False
    while value.startswith("Optional[") and value.endswith("]"):
        value = value[9:-1].strip()
    return value in _TYPES or (
        "|" in value
        and all(_type_reference(part.strip()) for part in value.split("|"))
        and "None" in (part.strip() for part in value.split("|"))
    )


def redaction_sensitive_value(key: str, value: Any) -> bool:
    """After a sensitive name, every value is private except the closed exemptions."""
    if not redaction_sensitive_key(key):
        return False
    return not _credential_value_exempt(key, value)


def _complete_lookup(value):
    """Validate one complete fixed environment lookup, respecting quoted arguments."""
    match = re.match(r"os\.(?:getenv\(|environ\[)", value)
    if match is None:
        return False
    stack = [value[match.end() - 1]]
    quote = None
    escaped = False
    for index in range(match.end(), len(value)):
        char = value[index]
        if char in "\r\n":
            return False
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack or char != {"(": ")", "[": "]", "{": "}"}[stack.pop()]:
                return False
            if not stack:
                return index == len(value) - 1
    return False


def _shell_reference(value):
    """One forward walk over reference-only parameter operands, at any depth."""
    start, end = 0, len(value)
    name = re.compile(r"[A-Za-z_][A-Za-z0-9_]*+|[0-9]++|[?#!@$*_-]")
    operator = re.compile(r":?[-+?=]|#{1,2}|%{1,2}|/{1,2}|\^{1,2}|,{1,2}|:[0-9]++")
    while value.startswith("${", start) and end > start + 2 and value[end - 1] == "}":
        if value.startswith("${{", start) and value[end - 2 : end] == "}}":
            return True
        end -= 1
        param = name.match(value, start + 2, end)
        if param is None:
            return False
        if param.end() == end:
            return True
        op = operator.match(value, param.end(), end)
        if op is None:
            return False
        start = op.end()
        if op[0] in {"?", ":?"} or start == end:
            return True
        if value.startswith("${", start):
            continue
        return re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*+").fullmatch(value, start, end) is not None
    return False


def _credential_value_exempt(key: str, value: Any, *, depths=None) -> bool:
    if value is None or isinstance(value, bool):
        return True
    leaf = _credential_name(key).rsplit(".", 1)[-1]
    if not isinstance(value, str):
        return isinstance(value, (int, float)) and leaf.endswith("session")
    if depths is not None and value:
        first = len(value) - len(value.lstrip())
        last = len(value.rstrip()) - 1
        if (
            first <= last
            and value.startswith(
                ("<", "{", "${", "%", "os.getenv(", "os.environ[", "Optional["), first
            )
            and depths[first] != depths[last]
        ):
            return False
    value = value.strip()
    if not value or value.lower() in {"null", "none", "nil", "true", "false"}:
        return True
    if value.startswith("${"):
        return _shell_reference(value)
    if value in {_REDACTED, "REDACTED", "...", "…", "%s", "{}"}:
        return True
    if re.fullmatch(
        r"(?:\*{3,}+|[xX]{3,}+|<[^<>]*+>|\$\{[^{}]*+\}|\$[A-Za-z_][A-Za-z0-9_]*+|%[A-Za-z_][A-Za-z0-9_]*+%|\{\{[^{}]*+\}\}|\{[A-Za-z_][A-Za-z0-9_]*+\})",
        value,
    ):
        return True
    reference = value.removesuffix(";")
    if _type_reference(reference):
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*+", reference):
        if reference.rsplit(".", 1)[-1].lower().replace("_", "") == leaf or reference.startswith(
            "process.env."
        ):
            return True
    if _complete_lookup(value):
        return True
    if leaf.endswith("session") and re.fullmatch(
        r"[+-]?(?:[0-9]++(?:\.[0-9]*+)?|\.[0-9]++)(?:[eE][+-]?[0-9]++)?%?", value, re.ASCII
    ):
        return True
    return False


@dataclass(frozen=True, slots=True)
class _TextEdit:
    start: int
    end: int
    original_start: int
    original_end: int


def _decode_view(
    text: str, *, json_escapes: bool = False, form: bool = False, ranges=None, form_depths=None
):
    """One canonicalization pass; allocate offsets only where bytes actually change."""
    pattern = re.compile(
        r"\+|\\{1,8}u002[bB]"
        if form
        else r'\\(?:u[0-9a-fA-F]{4}|["\\/bfnrt])'
        if json_escapes
        else r"(?:%[0-9a-fA-F]{2})++"
    )
    edits = _EditIndex()
    edits.encoding = "form" if form else "json" if json_escapes else "percent"
    parts = []
    position = offset = 0
    ranges = iter(ranges or ())
    active = next(ranges, None)
    for match in pattern.finditer(text):
        if form:
            while active and active[1] <= match.start():
                active = next(ranges, None)
            if active is None or not active[0] <= match.start() < active[1]:
                continue
            if form_depths is not None and form_depths[match.start()] > active[2]:
                continue
        raw = match.group()
        if form:
            units = [(" ", len(raw))]
        elif json_escapes:
            try:
                decoded = json.loads('"' + raw + '"')
            except ValueError:
                continue
            if 0xD800 <= ord(decoded) <= 0xDFFF:
                continue
            units = [(decoded, len(raw))]
        else:
            data = bytes.fromhex(raw.replace("%", ""))
            units = [(data.decode("ascii"), len(raw))] if data.isascii() else []
            index = len(data) if units else 0
            while index < len(data):
                width = 1
                byte = data[index]
                if 0xC2 <= byte <= 0xF4:
                    width = 2 if byte < 0xE0 else 3 if byte < 0xF0 else 4
                try:
                    char = data[index : index + width].decode("utf-8")
                except UnicodeDecodeError:
                    width, char = 1, raw[index * 3 : index * 3 + 3]
                units.append((char, width * 3))
                index += width
        parts.append(text[position : match.start()])
        cursor = match.start()
        for char, width in units:
            if char != text[cursor : cursor + width]:
                start = cursor - offset
                edit = _TextEdit(start, start + len(char), cursor, cursor + width)
                if (
                    edits
                    and edits[-1].end == edit.start
                    and edits[-1].original_end == edit.original_start
                    and (edits[-1].original_end - edits[-1].original_start)
                    * (edit.end - edit.start)
                    == (edit.original_end - edit.original_start) * (edits[-1].end - edits[-1].start)
                ):
                    previous = edits[-1]
                    edits[-1] = _TextEdit(
                        previous.start, edit.end, previous.original_start, edit.original_end
                    )
                else:
                    edits.append(edit)
                offset += width - len(char)
            parts.append(char)
            cursor += width
        position = match.end()
    if not edits:
        return text, []
    parts.append(text[position:])
    return "".join(parts), edits


def _original_spans(spans, edits):
    """Map an ordered, disjoint stream backwards in one walk over sparse edits."""
    index = quote_index = 0
    shift = quote_shift = 0
    for start, end, kind, replacement in spans:
        if isinstance(replacement, _JsonReplacement):
            # A key quote anchors the structure's layer even when an empty
            # container or numeric value contains no escapes of its own.
            a, b = replacement.anchor
            while quote_index < len(edits) and edits[quote_index].end <= a:
                quote_shift = edits[quote_index].original_end - edits[quote_index].end
                quote_index += 1
            first, last = a + quote_shift, b + quote_shift
            escaped = False
            while quote_index < len(edits) and edits[quote_index].start < b:
                edit = edits[quote_index]
                scale = (edit.original_end - edit.original_start) // (edit.end - edit.start)
                if edit.start <= a:
                    first = edit.original_start + (a - edit.start) * scale
                last = (
                    edit.original_start + (b - edit.start) * scale
                    if b <= edit.end
                    else b + edit.original_end - edit.end
                )
                escaped = True
                if b < edit.end:
                    break
                quote_shift = edit.original_end - edit.end
                quote_index += 1
            value = str(replacement)
            if escaped and edits.encoding == "json":
                value = json.dumps(value, ensure_ascii=False)[1:-1]
            elif escaped and edits.encoding == "percent":
                # Keep the sentinel literal, as in existing encoded masks;
                # only its structural wrapper needs source-layer escaping.
                value = value.replace("%", "%25").replace("\\", "%5C").replace('"', "%22")
            replacement = _JsonReplacement(value, first, last)
        while index < len(edits) and edits[index].end <= start:
            shift = edits[index].original_end - edits[index].end
            index += 1
        original_start = start + shift
        if index < len(edits) and edits[index].start <= start < edits[index].end:
            edit = edits[index]
            scale = (edit.original_end - edit.original_start) // (edit.end - edit.start)
            original_start = edit.original_start + (start - edit.start) * scale
        original_end = end + shift
        while index < len(edits) and edits[index].start < end:
            edit = edits[index]
            scale = (edit.original_end - edit.original_start) // (edit.end - edit.start)
            original_end = (
                edit.original_start + (end - edit.start) * scale
                if end <= edit.end
                else end + edit.original_end - edit.end
            )
            if end < edit.end:
                break
            shift = edit.original_end - edit.end
            index += 1
        yield original_start, original_end, kind, replacement


def _decoding_depths(length, layers):
    """One byte per decoded character; only allocate when a view changed.

    The edit maps remain sparse. Each edit contributes a layer to its output,
    including a form '+' or JSON escape, rather than erasing its provenance.
    """
    if not layers:
        return None
    depths = None
    for edits in layers:
        view_length = length + edits[-1].end - edits[-1].original_end
        result = bytearray(view_length)
        previous = memoryview(depths) if depths is not None else None
        position = cursor = 0
        for edit in edits:
            if previous is not None:
                result[cursor : edit.start] = previous[position : edit.original_start]
            level = (
                max(previous[edit.original_start : edit.original_end], default=0)
                if previous is not None
                else 0
            )
            result[edit.start : edit.end] = bytes([level + 1]) * (edit.end - edit.start)
            position, cursor = edit.original_end, edit.end
        if previous is not None:
            result[cursor:] = previous[position:]
        depths, length = result, view_length

    return depths


class _PositionIndex:
    """One lazily allocated signed offset per character, shared by index views.

    Positive entries are exclusive ends; negative entries identify the opening
    delimiter of a close/separator. No Python int/dict is retained per position.
    """

    def __init__(self, text, mode="ends", parent=None):
        self.text, self.mode = text, mode
        self.parent = parent
        self.data = array("i") if parent is None else None

    def get(self, position, default=None):
        data = self.parent.data if self.parent is not None else self.data
        if not data or not 0 <= position < len(data):
            return default
        value = data[position]
        if self.mode == "ends":
            return value if value > 0 else default
        allowed = {"closings": ")]}", "quotes": "\"'`", "separators": ",;"}[self.mode]
        if value < 0 and self.text[position] in allowed:
            opening = -value - 1
            return (self.text[opening], opening) if self.mode == "separators" else opening
        return default

    def __contains__(self, position):
        return self.get(position) is not None

    def __setitem__(self, position, value):
        parent = self.parent if self.parent is not None else self
        if not parent.data:
            parent.data = array("i", [0]) * len(self.text)
        if self.mode != "ends":
            value = -(value[1] if self.mode == "separators" else value) - 1
        parent.data[position] = value


class _QuoteRanges:
    """Opening-order intervals in two compact unsigned arrays."""

    def __init__(self, pairs=()):
        self.starts, self.ends = array("I"), array("I")
        for pair in pairs:
            self.append(pair)

    def append(self, pair):
        self.starts.append(pair[0])
        self.ends.append(pair[1])

    def __len__(self):
        return len(self.starts)

    def __getitem__(self, index):
        return self.starts[index], self.ends[index]

    def __setitem__(self, index, pair):
        self.starts[index], self.ends[index] = pair


class _EditIndex:
    """Sparse numeric edits; keep the coalescing tail mutable without rebuilding it."""

    def __init__(self):
        self.data = array("I")
        self.last = None

    def __len__(self):
        return len(self.data) // 4 + int(self.last is not None)

    def __getitem__(self, index):
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        if index == len(self) - 1:
            return self.last
        return _TextEdit(*self.data[index * 4 : index * 4 + 4])

    def append(self, edit):
        if self.last is not None:
            old = self.last
            self.data.extend((old.start, old.end, old.original_start, old.original_end))
        self.last = edit

    def __setitem__(self, index, edit):
        if index < 0:
            index += len(self)
        if index == len(self) - 1:
            self.last = edit
        else:
            self.data[index * 4 : index * 4 + 4] = array(
                "I", (edit.start, edit.end, edit.original_start, edit.original_end)
            )


class _Syntax:
    """Compute quote and balanced-value terminators once, never from every key."""

    def __init__(self, text, depths=None, proven=(), barriers=(), json_lines=()):
        self.barriers = barriers
        self.attribute_quotes = None
        self.json_lines = bytearray(len(text)) if json_lines else None
        for start, end in json_lines:
            self.json_lines[start:end] = b"\1" * (end - start)
        self.json_content = bytearray(len(text)) if proven else None
        self.depths = depths
        self.max_depth = max(depths, default=0) if depths is not None else 0
        self.form = bool(
            "&" in text and re.fullmatch(_NAME + r"=[^&\s]*+(?:&" + _NAME + r"=[^&\s]*+)*+", text)
        )
        self.ends = _PositionIndex(text)
        self.closings = _PositionIndex(text, "closings", self.ends)
        self.quote_closings = _PositionIndex(text, "quotes", self.ends)
        self.separator_context = _PositionIndex(text, "separators", self.ends)
        self.quotes = _QuoteRanges()
        self.escaped_closings = _PositionIndex(text)
        self.escaped_ends = _PositionIndex(text)
        escaped_quotes = {}
        escaped_last = {}
        if depths is None:
            for escaped in re.finditer(r"(?P<slashes>\\++)(?P<quote>[\"']?)", text):
                layer = len(escaped["slashes"]).bit_length()
                if layer > 3 or not escaped["quote"]:
                    continue
                channel = (escaped["quote"], layer)
                prior = escaped_last.get(channel)
                if prior is not None:
                    self.escaped_ends[prior] = escaped.end()
                escaped_last[channel] = escaped.start()
                opening = escaped_quotes.pop(channel, None)
                if opening is None:
                    escaped_quotes[channel] = escaped.start()
                else:
                    self.escaped_closings[escaped.start()] = opening + 1
        self.line_ends = array("I", (m.start() for m in re.finditer(r"[\r\n]", text)))
        # Local quote successors and proven pairs are separate facts. A stray
        # quote can never change the polarity of a credential's opening quote.
        self.quote_next = _PositionIndex(text)
        pending, successors = {}, {}
        stack = array("I")
        stack_context = array("i")
        quote_context = -1
        for match in re.finditer(r"\\[\s\S]|\"{3}|'{3}|`{3}|[\"'`()\[\]{},;\r\n]", text):
            char, pos = match[0], match.start()
            if char.startswith("\\"):
                continue
            level = self.depth(pos)
            if char in {"\r", "\n"}:
                # A deeper decoded newline is data for a shallower string.
                for channel in list(pending):
                    if len(channel[0]) == 1 and channel[1] >= level:
                        opening, index = pending.pop(channel)
                        self.ends[opening] = pos
                        self.quotes[index] = (opening, pos)
                for channel in list(successors):
                    if len(channel[0]) == 1 and channel[1] >= level:
                        del successors[channel]
                quote_context = max((v[0] for v in pending.values()), default=-1)
                continue
            if char[0] in "\"'`":
                if (
                    char == "'"
                    and pos
                    and pos + 1 < len(text)
                    and text[pos - 1].isalnum()
                    and text[pos + 1].isalnum()
                ):
                    continue
                channel = char, level
                prior = successors.get(channel)
                if prior is not None:
                    self.quote_next[prior] = match.end()
                successors[channel] = pos
                previous = pending.pop(channel, None)
                if previous is None:
                    pending[channel] = (pos, len(self.quotes))
                    self.quotes.append((pos, len(text)))
                else:
                    opening, index = previous
                    self.ends[opening] = match.end()
                    self.quotes[index] = (opening, pos)
                    self.quote_closings[pos] = opening
                    for inner_channel, (inner, inner_index) in list(pending.items()):
                        if inner > opening and inner_channel[1] >= level:
                            self.ends[inner] = pos
                            self.quotes[inner_index] = (inner, pos)
                            del pending[inner_channel]
                    while stack and stack[-1] > opening:
                        begin = stack.pop()
                        stack_context.pop()
                        self.ends[begin] = pos
                quote_context = max((value[0] for value in pending.values()), default=-1)
            elif char in ",;":
                if stack and stack_context[-1] == quote_context:
                    self.separator_context[pos] = (text[stack[-1]], stack[-1])
            elif char in "([{":
                stack.append(pos)
                stack_context.append(quote_context)
            elif (
                stack
                and char == {"(": ")", "[": "]", "{": "}"}[text[stack[-1]]]
                and stack_context[-1] == quote_context
                and self.depth(pos) == self.depth(stack[-1])
            ):
                begin = stack.pop()
                stack_context.pop()
                self.ends[begin] = pos + 1
                self.closings[pos] = begin
        for opening, _ in pending.values():
            self.ends[opening] = len(text)
        for begin in stack:
            self.ends[begin] = len(text)
        self.proven_closings = _PositionIndex(text)
        for opening, ending in proven:
            if (
                ending <= opening + 1
                or text[opening] != '"'
                or text[ending - 1] != '"'
                or self.depth(opening) != self.depth(ending - 1)
            ):
                continue
            self.json_content[opening + 1 : ending - 1] = bytes([self.depth(opening) + 1]) * max(
                0, ending - opening - 2
            )
            self.ends[opening] = ending
            self.quote_next[opening] = ending
            self.quote_closings[ending - 1] = opening
            self.proven_closings[ending - 1] = opening + 1

        def protected_quotes():
            data = self.proven_closings.data
            return (
                iter((p, value - 1) for p, value in enumerate(data) if value) if data else iter(())
            )

        protected = protected_quotes()
        protected_close = next(protected, None)
        placeholders = []
        cursor = 0
        placeholder_tokens = re.compile(r"(?<!\\)\\++[\"']|\$\{|\{\{|[{}\"'`]")
        while token := placeholder_tokens.search(text, cursor):
            char, pos = token[0], token.start()
            cursor = token.end()
            while protected_close is not None and protected_close[0] < pos:
                protected_close = next(protected, None)
            if char.startswith("\\") and placeholders:
                cursor = self.escaped_ends.get(pos, cursor)
                continue
            if char in {"${", "{{"}:
                if char == "{{":
                    placeholders.append(pos)
                placeholders.append(token.end() - 1)
            elif placeholders:
                enclosing = self.proven_closings.get(pos)
                if enclosing is not None and enclosing - 1 < placeholders[0]:
                    for opening in placeholders:
                        self.ends[opening] = pos
                    placeholders.clear()
                elif char in "\"'`":
                    close = self.quote_next.get(pos)
                    if close is not None and self.proven_closings.get(pos) is None:
                        cursor = close
                        if protected_close is not None and protected_close[1] < placeholders[0]:
                            cursor = min(cursor, protected_close[0])
                    elif self.quote_closings.get(pos, pos) < placeholders[0]:
                        for opening in placeholders:
                            self.ends[opening] = pos
                        placeholders.clear()
                elif char == "{":
                    placeholders.append(pos)
                elif char == "}" and self.depth(pos) == self.depth(placeholders[-1]):
                    opening = placeholders.pop()
                    self.ends[opening] = cursor
                    self.closings[pos] = opening
        # Lookup calls have local quoted arguments. Unrelated prose quotes
        # cannot close their parentheses, and punctuation in arguments is data.
        call_tokens = re.compile(r"os\.(?:getenv\(|environ\[)|(?<!\\)\\++[\"']|[()\[\]{}\"'\r\n]")
        calls = []
        protected = protected_quotes()
        protected_close = next(protected, None)
        cursor = 0
        while token := call_tokens.search(text, cursor):
            char, pos = token[0], token.start()
            cursor = token.end()
            while protected_close is not None and protected_close[0] < pos:
                protected_close = next(protected, None)
            if char.startswith("os."):
                calls.append((token.end() - 1, char[-1], True))
            elif calls:
                if char.startswith("\\"):
                    cursor = self.escaped_ends.get(pos, cursor)
                    continue
                if char in "\"'":
                    closing = self.quote_next.get(pos)
                    if (
                        self.proven_closings.get(pos) is not None
                        and self.proven_closings.get(pos) - 1 < calls[0][0]
                    ):
                        calls.clear()
                    elif closing is not None and self.proven_closings.get(pos) is None:
                        cursor = closing
                        if protected_close is not None and protected_close[1] < calls[0][0]:
                            cursor = min(cursor, protected_close[0])
                    elif closing is None:
                        calls.clear()
                elif char in "\r\n":
                    calls.clear()
                elif char in "([{":
                    calls.append((pos, char, False))
                elif char == {"(": ")", "[": "]", "{": "}"}[calls[-1][1]]:
                    opening, _, lookup = calls.pop()
                    if lookup:
                        self.ends[opening] = token.end()
                        self.closings[pos] = opening
        self._owned_quotes(text)
        # The temporary proof-close index is no longer needed after local call
        # indexing. Reuse its allocation for constant-time containing bounds.
        self.proven_bounds = self.proven_closings
        if proven:
            self.proven_bounds.data = array("i", [0]) * len(text)
            for begin, finish in proven:
                if (
                    finish > begin + 2
                    and text[begin] == '"'
                    and text[finish - 1] == '"'
                    and self.depth(begin) == self.depth(finish - 1)
                ):
                    self.proven_bounds.data[begin + 1 : finish] = array("i", [finish - 1]) * (
                        finish - begin - 1
                    )
        # Indented YAML containers/scalars end at the next dedent. Close every
        # pending block once, using the previous content end computed once/line.
        self.yaml_ends = _PositionIndex(text)
        self.yaml_scalars = _PositionIndex(text)
        blocks = []
        content_end = 0
        for line in re.finditer(r"[^\r\n]++|[\r\n]", text):
            body = line[0]
            stripped = body.lstrip()
            if not stripped:
                continue
            indent = len(body) - len(stripped)
            while blocks and indent <= blocks[-1][0]:
                _, colon = blocks.pop()
                self.yaml_ends[colon] = content_end
            scalar = re.match(r"(?P<key>" + _NAME + r")\s*+:[^\S\r\n]*+", stripped)
            if scalar and stripped[scalar.end() : scalar.end() + 1] not in {'"', "'", "[", "{"}:
                colon = line.start() + body.index(":")
                # Plain YAML scalars extend through the line, except comments.
                comment = re.search(r"\s#", stripped[scalar.end() :])
                finish = scalar.end() + comment.start() if comment else len(stripped.rstrip())
                self.yaml_scalars[colon] = line.start() + indent + finish
            # Each key remains eligible inside an earlier header's comment.
            # Do not scan the comment suffix once per key: '#' qualifies in O(1).
            for block_key in re.finditer(
                r"[\"']?" + _NAME + r"[\"']?[^\S\r\n]*+(?P<colon>:)[^\S\r\n]*+", stripped
            ):
                tail = block_key.end()
                indicator = re.compile(r"[|>](?:[1-9]?[-+]?|[-+]?[1-9]?)[^\S\r\n]*+(?=#|$)").match(
                    stripped, tail
                )
                if tail == len(stripped) or stripped[tail : tail + 1] == "#" or indicator:
                    # A pasted block can have malformed indentation. Ambiguity
                    # retains the following indented run rather than exposing it.
                    blocks.append((0, line.start() + indent + block_key.start("colon")))
            content_end = line.start() + len(body.rstrip())
        for _, colon in blocks:
            self.yaml_ends[colon] = content_end

    def depth(self, position):
        return (
            self.depths[position] if self.depths is not None and position < len(self.depths) else 0
        )

    def layer(self, start, end):
        return max(self.depths[start:end], default=0) if self.depths is not None else 0

    def complete(self, opening, end):
        return (
            end > opening
            and self.ends.get(opening) == end
            and self.closings.get(end - 1) == opening
            and self.depth(end - 1) == self.depth(opening)
        )

    def placeholder_complete(self, text, start, end):
        header = re.compile(
            r"\$\{(?:[A-Za-z_][A-Za-z0-9_]*+|[0-9]++|[?#!@$*_-])(?::?[-+?=]|%{1,2}|#{1,2}|}|\[)"
        )
        return self.complete(start + 1, end) and (
            (text.startswith("${{", start) and self.complete(start + 2, end - 1))
            or header.match(text, start, end) is not None
        )

    def shell_operator(self, text, position):
        # A key immediately after '${' is the parameter name, not an assignment.
        return text[max(0, position - 2) : position] == "${"

    def _owned_quotes(self, text):
        """Successors, never line-wide polarity, prove a value's own closer.

        Each quote and closer run is visited once. Escaped, doubled and shell
        concatenation quotes are data. A value-start empty pair is handled by
        quoted_end rather than treating an internal doubled pair as a close.
        """
        self.owned_quote_ends = self.quote_next
        self.owned_quote_ends.data = array("i")
        self.owned_brackets = self.escaped_closings
        self.owned_brackets.data = array("i")
        valid = bytearray()
        for run in re.finditer(r"(?:\\{0,8}[\"'`”’)\]}>])++", text):
            if self._follower(text, run.end()):
                if not valid:
                    valid = bytearray(len(text))
                if self.depths is None or run.end() == len(text):
                    valid[run.start() : run.end()] = b"\1" * (run.end() - run.start())
                else:
                    follower_depth = self.depth(run.end())
                    for position in range(run.start(), run.end()):
                        valid[position] = (
                            follower_depth <= self.depth(position)
                            or self.json_content is not None
                            and self.json_content[run.end()]
                            and self.json_lines is not None
                            and self.json_lines[run.end()]
                            and text[run.end()] in "\r\n"
                        )
        self.valid_closers = valid
        tokens = array("I")
        for token in re.finditer(
            r"'\"'\"'|'\\''|\\[\s\S]|`[\"']|\"{3}|'{3}|[\"'`“”‘’＂＇()\[\]{}<>\r\n]", text
        ):
            if (
                len(token[0]) == 3
                and token[0] in {'"' * 3, "'" * 3}
                and any(
                    self.depth(i) != self.depth(token.start())
                    for i in range(token.start() + 1, token.end())
                )
            ):
                for i in range(token.start(), token.end()):
                    tokens.extend((i, i + 1))
            else:
                tokens.extend((token.start(), token.end()))
        next_close = {}
        for index in range(len(tokens) - 2, -1, -2):
            pos, token_end = tokens[index], tokens[index + 1]
            char = text[pos:token_end]
            level = self.depth(pos)
            if char in {"\r", "\n"}:
                for channel in list(next_close):
                    if len(channel[0]) == 1 and channel[1] >= level:
                        del next_close[channel]
                continue
            if char.startswith("\\") or char in {"'\"'\"'", "'\\''", '`"', "`'"}:
                continue
            if char in "([{<":
                closer = {"(": ")", "[": "]", "{": "}", "<": ">"}[char]
                endpoint = next_close.get((closer, level))
                if endpoint is not None:
                    self.owned_brackets[pos] = endpoint
                continue
            if char in ")]}>" and valid and valid[pos]:
                next_close[(char, level)] = token_end
                continue
            close = {"“": "”", "‘": "’"}.get(char, char)
            if (
                char == "’"
                and pos
                and pos + 1 < len(text)
                and text[pos - 1].isalnum()
                and text[pos + 1].isalnum()
            ):
                continue
            endpoint = next_close.get((close, level))
            if endpoint is not None:
                self.owned_quote_ends[pos] = endpoint
            doubled = (
                len(char) == 1
                and char in "\"'"
                and (
                    (text[pos - 1 : pos] == char and self.depth(pos - 1) == level)
                    or (text[pos + 1 : pos + 2] == char and self.depth(pos + 1) == level)
                )
            )
            if valid and valid[pos] and not doubled:
                next_close[(char, level)] = token_end
        del tokens

    def _follower(self, text, after):
        if after >= len(text) or text[after].isspace() or text[after] in ",;&":
            return True
        if (
            self.json_content is not None
            and self.json_content[after]
            and re.compile(r"\\++(?:[nrt]|u00(?:09|0[aAdD]|20))").match(text, after)
        ):
            return True
        if text.startswith("/>", after):
            return True
        if text[after] in ".!?:" and (after + 1 == len(text) or text[after + 1].isspace()):
            return True
        return (
            text[after : after + 1] == "."
            and re.compile(r"\.[A-Za-z_][A-Za-z0-9_]*+\(").match(text, after) is not None
        )

    def boundary(self, text, position):
        return position < len(self.valid_closers) and bool(self.valid_closers[position])

    @staticmethod
    def quote_token(text, start):
        for token in ('"""', "'''", "$'", '"', "'", "`", "“", "‘", "＂", "＇"):
            if text.startswith(token, start):
                return token
        return None

    def line_end(self, text, start, level):
        if not hasattr(self, "line_stops"):
            self.line_stops = {}
        stops = self.line_stops.get(level)
        if stops is None:
            stops = self.line_stops[level] = array("I", [len(text)]) * (len(text) // 64 + 2)
            following = len(text)
            cursor = len(self.line_ends) - 1
            for block in range(len(stops) - 1, -1, -1):
                edge = block * 64
                while cursor >= 0 and self.line_ends[cursor] >= edge:
                    position = self.line_ends[cursor]
                    if self.depth(position) <= level:
                        following = position
                    cursor -= 1
                stops[block] = following
        edge = min(len(text), (start // 64 + 1) * 64)
        for position in range(start, edge):
            if text[position] in "\r\n" and self.depth(position) <= level:
                return position
        return stops[start // 64 + 1]

    def proven_end(self, start):
        return self.proven_bounds.get(start)

    def quoted_end(self, text, start):
        token = self.quote_token(text, start)
        if token is None:
            return None
        width = len(token)
        quote = "'" if token == "$'" else token
        first = start + int(token == "$'")
        if text.startswith(quote, start + width) and self.boundary(text, start + width):
            return start + width + len(quote)
        return self.owned_quote_ends.get(first)

    def attribute_end(self, text, start):
        if self.attribute_quotes is None:
            self.attribute_quotes = self.owned_brackets
            tokens = array("I")
            for token in re.finditer(r"\\[\s\S]|[\"'`]", text):
                if token[0].startswith("\\"):
                    continue
                tokens.append(token.start())
            following = {}
            for position in reversed(tokens):
                char = text[position]
                channel = char, self.depth(position)
                if channel in following:
                    self.attribute_quotes[position] = following[channel]
                doubled = char in "\"'" and (
                    (
                        text[position - 1 : position] == char
                        and self.depth(position - 1) == channel[1]
                    )
                    or (
                        text[position + 1 : position + 2] == char
                        and self.depth(position + 1) == channel[1]
                    )
                )
                if (
                    self._follower(text, position + 1)
                    or (
                        text[position + 1 : position + 2] == ">"
                        or text.startswith("/>", position + 1)
                    )
                ) and not doubled:
                    following[channel] = position + 1
        if text[start : start + 1] == text[start + 1 : start + 2] and (
            self._follower(text, start + 2)
            or (text[start + 2 : start + 3] == ">" or text.startswith("/>", start + 2))
        ):
            return start + 2
        return self.attribute_quotes.get(start)

    def owned_end(self, text, construct, start):
        """Only adjacent openers and proven JSON strings belong to this construct."""
        limit = self.proven_end(start)
        cursor = construct - 1
        pairs = {"(": ")", "[": "]", "{": "}", "<": ">"}
        while cursor >= 0:
            char = text[cursor]
            if self.depth(cursor) != self.depth(construct):
                break
            if char in "\"'`“‘":
                escaped = cursor
                while escaped and text[escaped - 1] == "\\":
                    escaped -= 1
                if escaped < cursor:
                    finish = self.escaped_ends.get(escaped)
                    if finish is not None:
                        closing = finish - (cursor - escaped) - 1
                        if closing >= start and self.boundary(text, closing):
                            limit = min(limit, closing) if limit is not None else closing
                    cursor = escaped - 1
                    continue
                finish = self.quoted_end(text, cursor)
                if finish is not None and finish - 1 >= start:
                    limit = min(limit, finish - 1) if limit is not None else finish - 1
            elif char in pairs:
                finish = self.owned_brackets.get(cursor)
                if finish is not None and finish - 1 >= start:
                    limit = min(limit, finish - 1) if limit is not None else finish - 1
            else:
                break
            cursor -= 1
        return limit

    def trim(self, text, start, end):
        if self.quote_token(text, start) is None:
            call = re.compile(r"os\.(?:getenv\(|environ\[)|Optional\[").match(text, start, end)
            while end > start and text[end - 1] in "\"'`”’)]}>.,!?:;":
                # A complete placeholder, fixed call or sentinel owns its closer.
                if call and self.complete(call.end() - 1, end):
                    break
                if (
                    (
                        end - start >= len(_REDACTED)
                        and text.startswith(_REDACTED, end - len(_REDACTED))
                    )
                    or text[end - 1] in "}>"
                    and self.complete(start + int(text.startswith("${", start)), end)
                ):
                    break
                if (
                    text.startswith("<", start)
                    and text[end - 1] == ">"
                    and self.depth(start) == self.depth(end - 1)
                ):
                    break
                end -= 1
        return end

    def private(self, text, key, start, end):
        if not redaction_sensitive_key(key):
            return False
        first, last = start, end
        while first < last and text[first].isspace():
            first += 1
        while last > first and text[last - 1].isspace():
            last -= 1
        escaped_end = self.escaped_ends.get(first)
        if escaped_end == last:
            value = text[first:last]
            for _ in range(3):
                value, edits = _decode_view(value, json_escapes=True)
                if not edits:
                    break
            try:
                value = json.loads(value)
            except (ValueError, RecursionError):
                return True
            return not _credential_value_exempt(key, value)
        token = self.quote_token(text, first)
        if token and self.quoted_end(text, first) == last:
            first += len(token)
            last -= 1 if token == "$'" else len(token)
        call = re.compile(r"os\.(?:getenv\(|environ\[)|Optional\[").match(text, first, last)
        if call:
            return not self.complete(call.end() - 1, last)
        if text.startswith("${", first) and not self.placeholder_complete(text, first, last):
            return True
        if text.startswith("{{", first) and not (
            self.complete(first, last) and self.complete(first + 1, last - 1)
        ):
            return True
        value = text[first:last]
        if value.startswith(("${", "{{", "os.getenv(", "os.environ[", "Optional[", "<", "{", "%")):
            # Delimiters have one provenance. Decoding is a caller's view, never
            # an exemption's attempt to complete a mixed-layer raw slice.
            if first < last and self.depth(first) != self.depth(last - 1):
                return True
            if value.startswith("${"):
                return not (
                    self.placeholder_complete(text, first, last) and _shell_reference(value)
                )
            if value.startswith("{{"):
                return not (self.complete(first, last) and self.complete(first + 1, last - 1))
            call = re.compile(r"os\.(?:getenv\(|environ\[)|Optional\[").match(text, first)
            if call:
                return not self.complete(call.end() - 1, last)
            if value.startswith("<"):
                return not (value.endswith(">") and "<" not in value[1:] and ">" not in value[:-1])
            if value.startswith("{"):
                return not (
                    self.complete(first, last)
                    and re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_]*+\}|\{\}", value)
                )
        return not _credential_value_exempt(key, value)

    def value(
        self, text, start, *, list_separators=False, operator_depth=None, construct=None, trim=True
    ):
        if start >= len(text):
            return start
        level = self.depth(start) if operator_depth is None else operator_depth
        construct = start if construct is None else construct
        limit = self.owned_end(text, construct, start)
        limit = len(text) if limit is None else limit
        escaped_end = self.escaped_ends.get(start)
        if escaped_end is not None and (escaped_end <= limit or limit <= start):
            return escaped_end
        token = self.quote_token(text, start)
        if token and limit == start:
            if self.quoted_end(text, start) is None:
                return start
            limit = len(text)
        prefix = re.compile(r"(?i)(?:br|rb|fr|rf|[brf])(?=[\"'])").match(text, start)
        if prefix:
            start = prefix.end()
            token = self.quote_token(text, start)
        if token:
            # A value's own quote is innermost. Adjacent outer brackets cannot
            # close it; only a proven containing JSON string can bound it.
            enclosing = self.proven_end(start)
            if enclosing is not None and enclosing <= start:
                enclosing = None
            end = self.quoted_end(text, start)
            if end is not None and (enclosing is None or end <= enclosing + 1):
                return end
            return min(enclosing or len(text), self.line_end(text, start, level))
        if text.startswith("<", start):
            finish = self.owned_brackets.get(start)
            if (
                finish is not None
                and text.find("<", start + 1, finish - 1) < 0
                and text.find(">", start + 1, finish - 1) < 0
            ):
                return min(finish, limit)
            end = min(limit, self.line_end(text, start, level))
            return self.trim(text, start, end) if trim else end
        call = re.compile(r"os\.(?:getenv\(|environ\[)|Optional\[").match(text, start)
        opening = call.end() - 1 if call else start + int(text.startswith("${", start))
        if call or text.startswith(("${", "{{"), start):
            finish = self.ends.get(opening)
            if finish is None or not self.complete(opening, finish):
                end = min(limit, self.line_end(text, start, level))
                return self.trim(text, start, end) if trim else end
        if text[start : start + 1] in "[{" and not text.startswith("{{", start):
            finish = self.ends.get(opening)
            if finish is None or not self.complete(opening, finish):
                return limit
        if call or text[start : start + 1] in "[{" or text.startswith("${", start):
            finish = self.ends.get(opening)
            if (
                finish is not None
                and self.complete(opening, finish)
                and finish <= limit
                and (
                    self._follower(text, finish)
                    or self.boundary(text, finish)
                    or (
                        self.proven_end(start) is not None
                        and re.compile(r"\\++[nr]").match(text, finish)
                    )
                )
            ):
                return finish
        end = start
        ambiguous = False
        pattern = re.compile(r"[\s,;&\"'`\[\]{}()]")
        while found := pattern.search(text, end, limit):
            pos = found.start()
            if self.depth(pos) > level:
                end = found.end()
                continue
            char = found[0]
            if char in "\"'`[]{}()":
                ambiguous |= char in "\"'`"
                end = found.end()
                continue
            if (
                char in "\r\n"
                or (char.isspace() and not ambiguous)
                or (
                    char in ",;&"
                    and (list_separators or (char in ";&" and self.form and level > 0))
                )
            ):
                return self.trim(text, start, pos) if trim else pos
            end = found.end()
        return self.trim(text, start, limit) if trim else limit


class _JsonReplacement(str):
    """Structured mask with a key-quote anchor in the current sparse view.

    Only scalar/container JSON masks carry this bounded per-span metadata.
    Ordinary masks and empty private additions allocate none of it.
    """

    __slots__ = ("anchor",)

    def __new__(cls, value, start, end):
        result = super().__new__(cls, value)
        result.anchor = (start, end)
        return result

    def shifted(self, offset):
        return _JsonReplacement(str(self), self.anchor[0] + offset, self.anchor[1] + offset)


def _anchored_spans(spans, anchors):
    """Rehydrate one transient structured mask from sparse numeric anchors."""
    offset = 0
    for index, (start, end, kind, replacement) in enumerate(spans):
        if anchors is not None and offset < len(anchors) and anchors[offset] == index:
            replacement = _JsonReplacement(replacement, anchors[offset + 1], anchors[offset + 2])
            offset += 3
        yield start, end, kind, replacement


class _SpanIndex:
    """Compact spans; retain no Python tuple/int object per input match."""

    def __init__(self, spans):
        self.data = array("I")
        self.kinds = []
        self.replacements = []
        self.anchors = None
        kinds, replacements = {}, {}
        for start, end, kind, replacement in spans:
            if isinstance(replacement, _JsonReplacement):
                if self.anchors is None:
                    self.anchors = array("I")
                self.anchors.extend((len(self.data) // 4, *replacement.anchor))
                replacement = str(replacement)
            if kind not in kinds:
                kinds[kind] = len(self.kinds)
                self.kinds.append(kind)
            if replacement not in replacements:
                replacements[replacement] = len(self.replacements)
                self.replacements.append(replacement)
            self.data.extend((start, end, kinds[kind], replacements[replacement]))

    def __len__(self):
        return len(self.data) // 4

    def __iter__(self):
        anchor = 0
        for offset in range(0, len(self.data), 4):
            replacement = self.replacements[self.data[offset + 3]]
            if (
                self.anchors is not None
                and anchor < len(self.anchors)
                and self.anchors[anchor] == offset // 4
            ):
                replacement = _JsonReplacement(
                    replacement, self.anchors[anchor + 1], self.anchors[anchor + 2]
                )
                anchor += 3
            yield (
                self.data[offset],
                self.data[offset + 1],
                self.kinds[self.data[offset + 2]],
                replacement,
            )


def _merge_spans(streams):
    """Fixed number of ordered rule streams: O(n), including overlapping rules."""
    from heapq import merge

    current = None
    for start, end, kind, replacement in merge(*streams):
        if start >= end:
            continue
        if current and start <= current[1]:
            begin, finish, old_kind, old_replacement = current
            # Credential kinds dominate display-only paths; outer structured values
            # retain their JSON replacement when an inner rule overlaps them.
            chosen = old_kind if old_kind != "local_path" else kind
            # A shorter additive span at the same start sorts first, but
            # cannot take the covering core scalar/container's quoted mask.
            if (
                start == begin
                and end >= finish
                and isinstance(replacement, _JsonReplacement)
                and not isinstance(old_replacement, _JsonReplacement)
            ):
                old_replacement = replacement
            current = (begin, max(finish, end), chosen, old_replacement)
        else:
            if current:
                yield current
            current = (start, end, kind, replacement)
    if current:
        yield current


def _assignment_spans(text, syntax, *, include_exempt=False, skip_paths=False):
    pattern = re.compile(
        r"(?:(?P<q>[\"'])(?P<quotedkey>" + _NAME + r")(?P=q)(?:\])?|"
        r"(?<![A-Za-z0-9_.])(?P<flag>--|-D|-)?(?P<key>" + _NAME + r"))"
        r"(?:[\s\u200b]*+(?P<sep>=>|=|:(?!=)|[：＝])\s*+|(?P<flagspace>\s++))",
    )
    trailing_assignment = re.compile(
        r"(?<![A-Za-z0-9_.-])(?P<flag>--|-D|-)?(?P<name>" + _NAME + r")(?P<op>=>|[=:：＝])?$"
    )
    position = line_index = container_index = 0
    while match := pattern.search(text, position):
        position = match.end()
        if not match["q"] and syntax.shell_operator(text, match.start()):
            continue
        key = match["key"] or match["quotedkey"]
        if (
            not match["q"]
            and match["sep"] in {"=", "=>"}
            and text[match.start() - 1 : match.start()] in {"?", "&", "#"}
        ):
            continue  # Query '=' values retain their query terminators and class.
        # Space alone is assignment syntax only on a command-line flag.
        if match["flagspace"] and not match["flag"]:
            continue
        if not redaction_sensitive_key(key):
            continue
        if skip_paths and _credential_name(key) == "localpath":
            continue
        start = match.end()
        container_end = None
        if match["q"] and text[start : start + 1] in "[{0123456789-ntf" and start < len(text):
            # The existing JSON lexer proves scalar/container ends.
            # Prose terminators must not consume a minified public sibling
            # or strip an actual JSON close.
            if not hasattr(syntax, "json_containers"):
                from heapq import merge

                source_bounds = ()
                source_layers = getattr(syntax, "source_layers", None)
                if source_layers is not None:
                    source, layers = source_layers
                    source_bounds = _QuoteRanges(
                        (a, b) for a, b, _, _ in _har_spans(source, _quotes="containers")
                    )
                    for edits in layers:
                        source_bounds = _decoded_proofs(source_bounds, edits)
                syntax.json_containers = _QuoteRanges(
                    merge(
                        ((a, b) for a, b, _, _ in _har_json_spans(text, _bounds=True)),
                        source_bounds,
                    )
                )
            bounds = syntax.json_containers
            while container_index < len(bounds) and bounds[container_index][0] < start:
                container_index += 1
            if container_index < len(bounds) and bounds[container_index][0] == start:
                container_end = bounds[container_index][1]
                while container_index + 1 < len(bounds) and bounds[container_index + 1][0] == start:
                    container_index += 1
                    container_end = max(container_end, bounds[container_index][1])
        operator_depth = (
            syntax.depth(match.start("sep")) if match["sep"] else syntax.depth(match.start())
        )
        if match["sep"]:
            start = match.end("sep")
            while (
                start < len(text)
                and text[start].isspace()
                and syntax.depth(start) <= operator_depth
            ):
                start += 1
        if (
            match["sep"] == "="
            and text[start : start + 1] == "="
            and text[match.start("sep") - 1 : match.start("sep")].isspace()
        ):
            continue  # A spaced equality comparison is not an assignment.
        annotation = False
        if match["sep"] == ":" and not match["q"]:
            equals = text.find("=", start, min(len(text), start + 256))
            if equals >= 0 and _type_reference(text[start:equals].strip().strip("\"'")):
                annotation = True
                start = equals + 1
                while start < len(text) and text[start].isspace():
                    start += 1
        if (
            not match["q"]
            and not match["flag"]
            and _credential_name(key)
            in {
                "authorization",
                "proxyauthorization",
                "cookie",
                "setcookie",
            }
        ):
            continue  # Header pass consumes its complete line, including punctuation.
        yaml_end = (
            syntax.yaml_ends.get(match.start("sep"))
            if match["sep"] == ":" and not annotation
            else None
        )
        if match["q"] and yaml_end is not None and yaml_end <= start:
            yaml_end = None  # A JSON-style key may introduce a raw newline scalar.
        scalar_end = (
            syntax.yaml_scalars.get(match.start("sep"))
            if match["sep"] == ":" and not annotation
            else None
        )
        if scalar_end is not None and _type_reference(text[start:scalar_end].strip()):
            scalar_end = None
        if scalar_end is not None and _type_reference(
            text[start:scalar_end].split(";", 1)[0].strip()
        ):
            scalar_end = None
        for boundary_name, boundary in (("yaml", yaml_end), ("scalar", scalar_end)):
            if boundary is not None and syntax.depth(boundary) > operator_depth:
                while line_index < len(syntax.line_ends) and (
                    syntax.line_ends[line_index] < boundary
                    or syntax.depth(syntax.line_ends[line_index]) > operator_depth
                ):
                    line_index += 1
                boundary = (
                    syntax.line_ends[line_index]
                    if line_index < len(syntax.line_ends)
                    else len(text)
                )
                if boundary_name == "yaml":
                    yaml_end = boundary
                else:
                    scalar_end = boundary
        end = (
            container_end
            if container_end is not None
            else max(start, yaml_end)
            if yaml_end is not None
            else max(start, scalar_end)
            if scalar_end is not None
            else syntax.value(text, start, operator_depth=operator_depth, construct=match.start())
        )
        if (
            match["q"]
            and syntax.quote_token(text, start)
            and syntax.quoted_end(text, start) is None
        ):
            end = len(text)
        quoted = yaml_end is None and syntax.quote_token(text, start) is not None
        if yaml_end is not None and text[start : start + 1] in {"|", ">"} and end <= start + 2:
            end = len(text)  # A malformed block cannot expose its following body.
        nested = (
            trailing_assignment.search(text, max(start, end - 134), end) if not quoted else None
        )
        continuation = re.compile(r"\s*+(?:=>|[=:：＝])").match(text, end)
        if not quoted and end < len(text) and text[end : end + 1] == ":":
            continuation = re.compile(r"[=:：＝]\s*+").match(text, end)
        if (
            nested
            and (nested["flag"] or nested["op"] or continuation)
            and redaction_sensitive_key(nested["name"])
            and end < len(text)
            and (text[end].isspace() or continuation is not None)
        ):
            if continuation:
                end = continuation.end()
            while end < len(text) and text[end].isspace():
                end += 1
            while line_index < len(syntax.line_ends) and syntax.line_ends[line_index] < end:
                line_index += 1
            end = syntax.line_ends[line_index] if line_index < len(syntax.line_ends) else len(text)
        if (
            match["flag"]
            and not quoted
            and _credential_name(key)
            in {
                "authorization",
                "proxyauthorization",
                "cookie",
                "setcookie",
            }
        ):
            while line_index < len(syntax.line_ends) and syntax.line_ends[line_index] < start:
                line_index += 1
            end = syntax.line_ends[line_index] if line_index < len(syntax.line_ends) else len(text)
        if quoted and end > start + 1 and text[end - 1 : end] == text[start : start + 1]:
            tail = trailing_assignment.search(text, max(start + 1, end - 135), end - 1)
            following = re.compile(r"\s*+(?:=>|[=:：＝])\s*+").match(text, end)
            if tail and following and redaction_sensitive_key(tail["name"]):
                end = syntax.line_end(text, following.end(), operator_depth)
        closed_quote = quoted and end - start > 1 and text[end - 1] == text[start]
        if include_exempt or syntax.private(text, key, start, end):
            position = max(position, end - int(closed_quote))
            if (
                closed_quote
                and continuation
                and end - start <= 130
                and redaction_sensitive_key(text[start + 1 : end - 1])
            ):
                # A quoted argument can also be the key of an overlapping
                # assignment. Its value starts beyond this argument's end.
                position = match.end()
            kind = (
                "local_path"
                if _credential_name(key) == "localpath"
                else "sensitive_field"
                if match["q"]
                else "session_cookie"
            )
            replacement = _REDACTED
            if quoted:
                closed = closed_quote
                width = 3 if text.startswith(('"""', "'''"), start) else 1
                start += width
                end -= width if closed else 0
            elif match["q"]:
                replacement = _JsonReplacement(
                    '"' + _REDACTED + '"', match.start("q"), match.start("q") + 1
                )
            yield start, end, kind, replacement
            if kind == "local_path" and not include_exempt:
                # A local_path label must not hide credential syntax in its value.
                value = text[match.end() : end]
                nested = _assignment_spans(value, _Syntax(value), skip_paths=True)
                offset = match.end()
                for inner_start, inner_end, inner_kind, inner_replacement in nested:
                    yield offset + inner_start, offset + inner_end, inner_kind, inner_replacement


def _header_spans(text, syntax, proven=()):
    pattern = re.compile(
        r"(?i)(?<![A-Za-z0-9_.-])(?P<key>" + _NAME + r")\s*(?P<sep>=|[:：＝])[^\S\r\n]*+"
    )

    def proof_cursor():
        index, active = 0, []

        def at(position):
            nonlocal index
            while index < len(proven) and proven[index][0] <= position:
                opening, ending = proven[index]
                index += 1
                while active and active[-1][0] <= opening:
                    active.pop()
                if ending > position:
                    escaped = re.compile(r'\\{1,7}(?=")').match(text, opening)
                    width = len(escaped[0]) if escaped else 0
                    level = syntax.depth(opening) + width.bit_length() + 1
                    active.append((ending, level, ending - width - 1))
            while active and active[-1][0] <= position:
                active.pop()
            return active[-1][1:] if active else (0, None)

        return at

    line_proof, value_proof = proof_cursor(), proof_cursor()
    # Unindented lines begin new groups. Compute a continuation group's end once,
    # then reuse it even when an exempt header re-enters the group's contents.
    lines = array("I", [0])
    line_ends = array("I")
    line_levels = bytearray()
    # Only a run's first backslash can start its escaped newline. Otherwise an
    # absent r/n retries the remaining possessive run at every backslash.
    for line in re.finditer(r"\r\n|\r|\n|(?<!\\)\\++(?:r(?:\\++n)?|n)", text):
        escaped = text[line.start()] == "\\"
        content_level = syntax.json_content[line.start()] if syntax.json_content is not None else 0
        content_level = max(content_level, line_proof(line.start())[0])
        if escaped and not content_level:
            continue
        lines.append(line.end())
        line_ends.append(line.start())
        line_levels.append(content_level if escaped else syntax.depth(line.start()))
    line_ends.append(len(text))
    folded = array("I", [0]) * len(lines)
    following = len(text)
    continuation = False
    for index in range(len(lines) - 1, -1, -1):
        start = lines[index]
        end = line_ends[index]
        folded[index] = following if continuation else end
        continuation = text[start : start + 1] in {" ", "\t"}
        following = folded[index]
    line_indices = {}
    position = 0
    while match := pattern.search(text, position):
        position = match.end()
        if syntax.shell_operator(text, match.start()):
            continue
        name = _credential_name(match["key"])
        if name not in {
            "authorization",
            "proxyauthorization",
            "cookieheader",
            "headers",
        } and not name.endswith("cookie"):
            continue
        start = match.end()
        if (
            match["sep"] == "="
            and text[start : start + 1] == "="
            and text[match.start("sep") - 1 : match.start("sep")].isspace()
        ):
            continue
        level = syntax.depth(match.start("sep"))
        content_level, proven_end = value_proof(start)
        level = max(level, content_level)
        if syntax.json_content is not None:
            level = max(level, syntax.json_content[start - 1])
        index = line_indices.get(level, 0)
        while index < len(line_levels) and (line_ends[index] < start or line_levels[index] > level):
            index += 1
        line_indices[level] = index
        end = line_ends[index]
        fold_end = None
        # Indentation belongs to this header even when it resembles k=v or k:v.
        if index < len(line_levels):
            after = lines[index + 1]
            if after >= len(text) or text[after : after + 1] not in " \t":
                after = None
            if after is not None:
                end = folded[index + 1]
                fold_end = end
        if syntax.quote_token(text, start):
            end = syntax.value(text, start, operator_depth=level, construct=match.start())
        if fold_end is not None:
            end = max(end, fold_end)
        limit = syntax.owned_end(text, match.start(), start)
        if proven_end is not None:
            limit = min(limit, proven_end) if limit is not None else proven_end
        if limit is not None:
            end = min(end, limit)
        if not syntax.quote_token(text, start):
            end = syntax.trim(text, start, end)
        cookie = name not in {"authorization", "proxyauthorization"}
        value = text[start:end].strip().lower() if not cookie and end - start <= 128 else None
        authorized = cookie or value not in {
            "bearer",
            "basic",
            "digest",
            "token",
            "negotiate",
            "ntlm",
            "aws4-hmac-sha256",
        }
        if authorized and syntax.private(text, match["key"], start, end):
            position = max(position, end)
            yield start, end, "cookie" if cookie else "authorization", _REDACTED


class _EncodedPositions:
    """Monotone origin lookups through a fixed number of sparse decoding layers."""

    def __init__(self, text, layers, *, form=False):
        self.text = text
        self.layers = list(reversed(layers))
        self.indices = [0] * len(layers)
        self.shifts = [0] * len(layers)
        self.form = form

    def copy(self):
        return _EncodedPositions(self.text, list(reversed(self.layers)), form=self.form)

    def contains(self, position):
        changed = False
        for level, edits in enumerate(self.layers):
            index = self.indices[level]
            while index < len(edits) and edits[index].end <= position:
                self.shifts[level] = edits[index].original_end - edits[index].end
                index += 1
            self.indices[level] = index
            if index < len(edits) and edits[index].start <= position < edits[index].end:
                changed = True
                edit = edits[index]
                scale = (edit.original_end - edit.original_start) // (edit.end - edit.start)
                position = edit.original_start + (position - edit.start) * scale
            else:
                position += self.shifts[level]
        return (
            changed
            or self.text[position : position + 1] == "%"
            or (
                self.form
                and (
                    self.text[position : position + 1] == "+"
                    or re.match(r"\\{1,8}u002b", self.text[position : position + 13], re.I)
                )
            )
        )


def _query_spans(text, syntax, encoded_positions):
    pattern = re.compile(
        r"[?&#]" + r"(?P<key>" + _NAME + r")[^\S\r\n]*+=[^\S\r\n]*+",
    )
    ending = re.compile(r"%(?:25){0,2}(?:3f|26|23)|[\s&#\"'`\[\]{}()>]|\?", re.I)
    signed = {
        "oh",
        "oe",
        "sp",
        "sv",
        "sr",
        "se",
        "st",
        "skoid",
        "sktid",
        "skt",
        "ske",
        "sks",
        "skv",
    }
    urls = list(
        re.finditer(r"(?i)(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*+://|(?<![:/])//", text)
    )
    url_index = -1
    spaces = iter(re.finditer(r"\s", text))
    space = next(spaces, None)
    last_space = -1
    position = 0
    while match := pattern.search(text, position):
        position = match.end()
        key = match["key"]
        if not (redaction_sensitive_key(key) or key.lower() in signed):
            continue
        start = match.end()
        operator_depth = syntax.depth(match.start("key") + len(key))
        owner = match.start()
        # URL candidates preceding a query are indexed once for this scanner.
        while url_index + 1 < len(urls) and urls[url_index + 1].start() <= match.start():
            url_index += 1
        while space is not None and space.start() < match.start():
            last_space = space.start()
            space = next(spaces, None)
        if url_index >= 0 and last_space < urls[url_index].end():
            owner = urls[url_index].start()
        limit = syntax.owned_end(text, owner, start)
        limit = len(text) if limit is None else limit
        if text[owner - 1 : owner] == "<":
            autolink = text.find(">", start, limit)
            if autolink >= 0 and syntax.boundary(text, autolink):
                limit = autolink
        if syntax.proven_end(start) == start:
            continue  # The proven URL string closes before this empty value.
        if syntax.quote_token(text, start):
            end = syntax.value(text, start, operator_depth=operator_depth, construct=owner)
            if syntax.private(text, "token" if key.lower() in signed else key, start, end):
                position = max(position, end)
                yield start, end, "url_token", _REDACTED
            continue
        cursor = start + len(_REDACTED) if text.startswith(_REDACTED, start) else start
        end = limit
        while boundary := ending.search(text, cursor, limit):
            encoded = encoded_positions.contains(boundary.start())
            char = boundary[0]
            if char in "& #".replace(" ", "") and syntax.depth(boundary.start()) <= operator_depth:
                end = boundary.start()
                break
            if char.isspace() and not encoded and syntax.depth(boundary.start()) <= operator_depth:
                end = boundary.start()
                break
            if (
                char == "?"
                and encoded
                and syntax.depth(boundary.start()) > operator_depth
                and not encoded_positions.form
            ) or char.startswith("%"):
                end = boundary.start()
                break
            cursor = boundary.end()
        end = syntax.trim(text, start, end)
        if syntax.private(text, "token" if key.lower() in signed else key, start, end):
            position = max(position, end)
            yield start, end, "url_token", _REDACTED


def _userinfo_spans(text, syntax):
    scheme = re.compile(r"(?i)(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*+://|(?<![:/])//")

    class Positions:
        def __init__(self, values):
            self.values = iter(values)
            self.current = next(self.values, len(text))

        def first(self, position):
            while self.current < position:
                self.current = next(self.values, len(text))
            return self.current

    def boundaries(level, special):
        for boundary in re.finditer(r"[/\?#\\\s]", text):
            pos = boundary.start()
            if syntax.depth(pos) <= level and (boundary[0] != "\\" or special):
                yield pos

    def candidate_boundaries(level):
        # Once userinfo has an operator, a deeper scheme is password data.
        # Each bounded layer has one monotone cursor; never restart per URL.
        return (
            candidate.start()
            for candidate in scheme.finditer(text)
            if syntax.depth(candidate.end() - 1) <= level
        )

    ends = {}
    successors = {}
    raw_ends = Positions(end for _, end in syntax.barriers)
    ats = Positions(m.start() for m in re.finditer("@", text))
    candidates = iter(scheme.finditer(text))
    match = next(candidates, None)
    while match is not None:
        following = next(candidates, None)
        position = match.end()
        level = syntax.depth(position - 1)
        special = match[0].startswith("//") or match[0].split(":", 1)[0].lower() in {
            "http",
            "https",
            "ftp",
            "file",
            "ws",
            "wss",
        }
        key = (level, special)
        if key not in ends:
            ends[key] = Positions(boundaries(level, special))
        if level not in successors:
            successors[level] = Positions(candidate_boundaries(level))
        next_boundary = successors[level].first(position)
        if following is not None and text.find(":", position, following.start()) < 0:
            # A root URL cannot claim an independent encoded URL's operator.
            next_boundary = min(next_boundary, following.start())
        end = ends[key].first(position)
        if special:
            end = min(end, raw_ends.first(position))
        end = min(end, next_boundary)
        first_at = ats.first(position)
        adjacent_bound = syntax.owned_end(text, match.start(), position)
        colon = text.find(
            ":",
            position,
            min(first_at, end, adjacent_bound if adjacent_bound is not None else len(text)),
        )
        if colon >= 0 and syntax.quote_token(text, colon + 1):
            # The password's quote is innermost; an adjacent URL bracket cannot
            # close it. Natural authority terminators still bound the URL.
            owned = syntax.proven_end(colon + 1)
            own_close = syntax.quoted_end(text, colon + 1)
            if own_close is not None:
                owned = min(owned, own_close - 1) if owned is not None else own_close - 1
        else:
            owned = syntax.owned_end(text, match.start(), position)
        if owned is not None:
            end = min(end, owned)
        if text[match.start() - 1 : match.start()] == "<" and not (
            colon >= 0 and syntax.quote_token(text, colon + 1)
        ):
            autolink = text.find(">", position, end)
            if autolink >= 0 and syntax.boundary(text, autolink):
                end = autolink
        first_at = ats.first(position)
        if first_at < end:
            colon = text.find(":", position, first_at)
            at = first_at
            while ats.first(at + 1) < end:
                at = ats.current
            if colon >= 0 and syntax.private(text, "password", colon + 1, at):
                yield colon + 1, at, "url_token", _REDACTED
        match = following


def _cookie_spans(text, syntax):
    def chains(pair):
        position = 0
        while match := pair.search(text, position):
            begin = match.start()
            end = syntax.value(
                text,
                match.end(),
                list_separators=True,
                operator_depth=syntax.depth(match.end() - 1),
            )
            sensitive = syntax.private(text, match["key"], match.end(), end)
            generic = syntax.private(text, "password", match.end(), end)
            members = 1
            position = max(end, match.end())
            while separator := re.compile(r";[^\S\r\n]*+(?=" + _NAME + r"=)").match(text, position):
                next_pair = re.compile(r"(?P<key>" + _NAME + r")=[^\S\r\n]*+").match(
                    text, separator.end()
                )
                if not next_pair:
                    break
                finish = syntax.value(
                    text,
                    next_pair.end(),
                    list_separators=True,
                    operator_depth=syntax.depth(next_pair.end() - 1),
                )
                sensitive |= syntax.private(text, next_pair["key"], next_pair.end(), finish)
                generic |= syntax.private(text, "password", next_pair.end(), finish)
                end = position = max(finish, next_pair.end())
                members += 1
            if members > 1 and (sensitive or generic):
                yield begin, end, "cookie", _REDACTED
            elif not sensitive and not generic:
                position = match.end()

    # A form's outer name=value must not consume a later URL's path/query
    # candidate. Each disjoint start class has its own monotone cursor; merge
    # their ordered spans without rescanning an outer value per inner pair.
    ordinary = re.compile(r"(?<![A-Za-z0-9_.%=/?:&#-])(?P<key>" + _NAME + r")=[^\S\r\n]*+")
    url = re.compile(r"(?<=[/?])(?P<key>" + _NAME + r")=[^\S\r\n]*+")
    yield from _merge_spans([chains(ordinary), chains(url)])


def _jar_spans(text, syntax):
    pattern = re.compile(
        r"(?m)(?<!\S)(?:#HttpOnly_)?[^\s]++\t(?:TRUE|FALSE)\t[^\t\r\n]*+\t(?:TRUE|FALSE)\t[0-9]++\t[^\t\r\n]++\t(?P<value>[^\r\n]*+)"
    )
    for match in pattern.finditer(text):
        if syntax.private(text, "cookie", *match.span("value")):
            yield *match.span("value"), "cookie", _REDACTED


def _bearer_spans(text):
    pattern = re.compile(
        r"(?i)bearer[\s+\u00a0]++(?P<token>(?:\[REDACTED\])?[A-Za-z0-9._~+/-]++=*+)"
    )
    for match in pattern.finditer(text):
        token = match["token"]
        if (
            token.startswith(_REDACTED)
            and len(token) > len(_REDACTED)
            or (
                len(token) >= 16
                and (
                    any(c.isdigit() for c in token)
                    or (any(c.isupper() for c in token) and any(c.islower() for c in token))
                )
            )
        ):
            yield *match.span("token"), "bearer_token", _REDACTED


# Independently written prefix rules. No third-party scanner patterns are copied.
_PREFIXES = re.compile(
    r"(?<![A-Za-z0-9_-])(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9]{22}_[A-Za-z0-9]{59}|"
    r"glpat-[A-Za-z0-9_-]{20}|xox[bpa]-(?:[0-9]{10,13}-){1,3}[A-Za-z0-9]{24,64}|xoxa-2-(?:[0-9]{10,13}-){1,3}[A-Za-z0-9]{24,64}|(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"AIza[A-Za-z0-9_-]{35}|(?:sk|rk)_live_[A-Za-z0-9]{24,200}+|npm_[A-Za-z0-9]{36}|"
    r"hf_[A-Za-z0-9]{34}|sk-(?:ant-)?[A-Za-z0-9_-]{8,}+)(?![A-Za-z0-9_-])",
    re.ASCII,
)


def _prefix_spans(text):
    for match in _PREFIXES.finditer(text):
        yield *match.span(), "api_key", _REDACTED


def _jwt_spans(text):
    for match in re.finditer(
        r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]++\.[A-Za-z0-9_-]++\.[A-Za-z0-9_-]*+(?![A-Za-z0-9_-])",
        text,
        re.ASCII,
    ):
        yield *match.span(), "api_key", _REDACTED


def _pem_spans(text, syntax):
    label_pattern = r"(?P<label>(?:[A-Z0-9]+[ +]){0,6}PRIVATE[ +]KEY(?:[ +]BLOCK)?)"
    begin = re.compile(r"-----BEGIN[ +]" + label_pattern + r"-----", re.I)
    end_lines = {}
    for match in re.finditer(r"-----END[ +]" + label_pattern + r"-----", text, re.I):
        end_lines.setdefault(match["label"].replace("+", " ").upper(), []).append(match)
    cursors = {}
    position = 0
    while match := begin.search(text, position):
        level = syntax.layer(match.start(), match.end())
        label = match["label"].replace("+", " ").upper()
        ends = end_lines.get(label, [])
        index = cursors.get(label, 0)
        while index < len(ends) and (
            ends[index].start() < match.end()
            or syntax.layer(ends[index].start(), ends[index].end()) > level
        ):
            index += 1
        end = (
            ends[index].end()
            if index < len(ends)
            else syntax.proven_end(match.start()) or len(text)
        )
        cursors[label] = index + 1
        yield match.start(), end, "private_key", _REDACTED
        position = end


def _markup_spans(text, syntax):
    """One bounded tag pass, with each quoted attribute/comment entered once."""
    if hasattr(syntax, "markup"):
        return syntax.markup
    tokens = re.compile(r"<!--|</?(?P<name>[A-Za-z][A-Za-z0-9_:.-]*+)(?=[\s/>])")
    attributes = re.compile(r"(?P<name>[A-Za-z_:][A-Za-z0-9_:.-]*+)\s*+=\s*+")
    closings = {}
    for match in re.finditer(r"</(?P<key>[A-Za-z][A-Za-z0-9_:.-]*+)[^\S\r\n]*+>", text):
        closings.setdefault(match["key"].casefold(), []).append(match)
    next_tag_ends = array("I", [len(text)]) * (len(text) // 64 + 2)
    following = len(text)
    for block in range(len(next_tag_ends) - 1, -1, -1):
        edge = block * 64
        position = text.find(">", edge, min(len(text), edge + 64))
        if position >= 0:
            following = position
        next_tag_ends[block] = following

    def next_tag_end(start, upper):
        edge = min(upper, (start // 64 + 1) * 64)
        position = text.find(">", start, edge)
        return min(upper, position if position >= 0 else next_tag_ends[start // 64 + 1])

    contexts = _QuoteRanges([(0, len(text))])
    context_index = 0
    output = _PositionIndex(text)
    indices = {}
    while context_index < len(contexts):
        lower, upper = contexts[context_index]
        context_index += 1
        if output.get(lower, 0) >= upper:
            continue  # A private attribute already masks this complete context.
        position = lower
        while tag := tokens.search(text, position, upper):
            position = tag.end()
            if tag[0] == "<!--":
                stop = text.find("-->", position, upper)
                stop = upper if stop < 0 else stop
                contexts.append((position, stop))
                position = min(upper, stop + 3)
                continue
            if tag[0].startswith("</"):
                continue
            if text[tag.start() - 1 : tag.start()] == "<" and re.compile(
                r"[^\S\r\n]*+(?:\r?\n|\r|$)"
            ).match(text, tag.end(), upper):
                continue  # A shell heredoc delimiter line is not a start tag.

            name = tag["name"].casefold()
            cursor = tag.end()
            private = password_type = False
            values = []
            closed = False
            while cursor < upper:
                char = text[cursor]
                if char == "<":
                    break
                if char == ">":
                    cursor += 1
                    closed = True
                    break
                attr = attributes.match(text, cursor, upper)
                if attr is None:
                    cursor += 1
                    continue
                start = attr.end()
                quote = text[start : start + 1]
                if quote in {'"', "'", "`"}:
                    finish = syntax.attribute_end(text, start)
                    if finish is not None and finish <= upper:
                        begin, end = start + 1, finish - 1
                        cursor = finish
                    else:
                        # Malformed quotes cannot consume a following tag.
                        end = next_tag_end(start, upper)
                        begin, cursor = start + 1, end
                else:
                    unquoted = re.compile(r"(?:[^\s<>/]|/(?!>))*+").match(text, start, upper)
                    begin, end = start, unquoted.end()
                    cursor = max(end, start)
                if text.find("<", begin, end) >= 0:
                    contexts.append((begin, end))
                key = attr["name"].casefold().rsplit(":", 1)[-1]
                if key in {"name", "type"}:
                    if end - begin > 1024:
                        private = True
                        continue
                    value = html.unescape(text[begin:end]).strip(" \t\r\n\f").casefold()
                    if key == "name":
                        private |= redaction_sensitive_key(value)
                    else:
                        password_type |= value == "password"
                elif key in {"value", "content"}:
                    values.append((begin, end, key))
            position = max(position, cursor)
            for begin, end, key in values:
                if not closed and text[end : end + 1] == "<" and (private or password_type):
                    # A malformed private attribute cannot expose the token
                    # that interrupted its tag. Include that bounded token.
                    stop = next_tag_end(end, upper)
                    end = min(upper, stop + 1)
                if (private or password_type and key == "value") and syntax.private(
                    text, "password", begin, end
                ):
                    output[begin] = max(output.get(begin, 0), end)
            if not closed or text[cursor - 2 : cursor] == "/>":
                continue
            leaf = name.rsplit(":", 1)[-1]
            if not redaction_sensitive_key(leaf):
                continue
            ends = closings.get(name, [])
            index = indices.get(name, 0)
            level = syntax.layer(tag.start(), tag.end())
            while index < len(ends) and (
                ends[index].start() < cursor or syntax.depth(ends[index].start()) > level
            ):
                index += 1
            end = min(upper, ends[index].start()) if index < len(ends) else upper
            indices[name] = index
            begin, finish = cursor, end
            if text.startswith("<![CDATA[", begin) and text[end - 3 : end] == "]]>":
                begin += 9
                finish -= 3
            if syntax.private(text, leaf, begin, finish):
                output[cursor] = max(output.get(cursor, 0), end)
                position = (
                    ends[index].end() if index < len(ends) and ends[index].end() <= upper else end
                )
    syntax.markup = _SpanIndex(
        (start, end, "sensitive_field", _REDACTED) for start, end in enumerate(output.data) if end
    )
    return syntax.markup


def _xml_spans(text, syntax):
    yield from _markup_spans(text, syntax)


def _html_spans(text, syntax):
    # The shared lexer covers both local-name bodies and name/value attributes.
    return iter(())


class _CurlScope:
    """Forward-only separator ownership after a lexical curl/long-flag anchor.

    This is local argument state, not quote pairing over surrounding prose. The
    same cursor also visits consumed private values, so skipping a value cannot
    hide a real command boundary or invent one at an escaped separator.
    """

    def __init__(self, text, syntax):
        self.text, self.syntax = text, syntax
        self.position = 0
        self.curl = self.tracking = False
        self.level = 0
        self.quote = None
        self.argument = True
        self.option = False
        self.parameter = 0
        self.parameter_level = 0
        self.closer_end = 0
        self.closer_valid = False
        self.protected_end = 0

    def anchor(self, position, level, *, curl):
        if not self.tracking:
            self.position = position
            self.level = level
            self.quote = None
            self.argument = not curl
            self.option = False
            self.parameter = 0
            self.tracking = True
        self.curl |= curl

    def advance(self, end, *, stop_at_boundary=False):
        text, syntax = self.text, self.syntax
        while self.tracking and self.position < end:
            pos = self.position
            char, level = text[pos], syntax.depth(pos)
            self.position += 1
            if char == "\\" and pos + 1 < len(text) and syntax.depth(pos + 1) == level:
                self.position += 1
                if text[pos + 1 : pos + 3] == "\r\n":
                    self.position += 1
                continue
            if self.quote is not None:
                quote, quote_level = self.quote
                if char == "`" and quote != "`" and text[pos + 1 : pos + 2] in {"'", '"'}:
                    self.position += 1
                    continue
                if char == quote and level == quote_level:
                    # The shell's two single-quote escape idioms and doubled
                    # quotes are data. Do not close then reopen across them.
                    idiom = next((x for x in ("'\"'\"'", "'\\''") if text.startswith(x, pos)), None)
                    if idiom:
                        self.position += len(idiom) - 1
                        continue
                    if text[pos + 1 : pos + 2] == quote:
                        self.position += 1
                        continue
                    if pos >= self.closer_end:
                        self.closer_end = pos + 1
                        while (
                            self.closer_end < len(text)
                            and text[self.closer_end] in "'\"`”’)]}>"
                            and syntax.depth(self.closer_end) == level
                        ):
                            self.closer_end += 1
                        self.closer_valid = (
                            syntax._follower(text, self.closer_end)
                            or text[self.closer_end : self.closer_end + 1] == "|"
                        )
                    if self.closer_valid:
                        self.quote = None
                continue
            if char == "$" and text[pos + 1 : pos + 2] == "{":
                if not self.parameter:
                    self.parameter_level = level
                self.parameter += 1
                self.position += 1
                self.argument = True
            elif self.parameter and level == self.parameter_level and char in "{}":
                self.parameter += 1 if char == "{" else -1
            elif char == "$" and self.argument and text[pos + 1 : pos + 2] == "'":
                self.quote = ("'", syntax.depth(pos + 1))
                self.position += 1
                self.argument = self.option = False
            elif char in "'\"`“‘" and (self.argument or self.option):
                self.quote = ({"“": "”", "‘": "’"}.get(char, char), level)
                self.argument = self.option = False
            elif (
                char in ";|&\r\n"
                and level <= self.level
                and not self.parameter
                and pos >= self.protected_end
            ):
                self.curl = self.tracking = False
                if stop_at_boundary:
                    return pos
            elif char.isspace() or char == "=":
                self.argument, self.option = True, False
            else:
                if self.argument:
                    self.option = char == "-"
                self.argument = False
        self.position = max(self.position, end)
        return None


def _curl_spans(text, syntax, *, source_escapes=False):
    if source_escapes and syntax.depths is not None:
        # Percent/form views still contain source JSON quote escapes. Their
        # successors occupy otherwise unused backslash slots in the existing
        # quote index; no second per-character table is retained.
        previous = {}
        for escaped in re.finditer(r"(?P<slashes>\\++)(?P<quote>[\"']?)", text):
            width = len(escaped["slashes"])
            if width > 7 or not escaped["quote"]:
                continue
            channel = (escaped["quote"], width, syntax.depth(escaped.start()))
            if syntax.depth(escaped.end() - 1) != channel[2]:
                continue
            prior = previous.get(channel)
            if prior is not None:
                syntax.owned_quote_ends[prior] = escaped.end()
            previous[channel] = escaped.start()
    pattern = re.compile(
        r"(?P<curl>(?<![A-Za-z0-9_])curl\b)|"
        r"(?P<boundary>[;|&\r\n])|"
        r"(?<![A-Za-z0-9_])(?:(?P<long>--user|--cookie)(?:=|[^\S\r\n]++|(?=(?:\\{1,7})?[\"'`]))|(?P<short>-[ub])[^\S\r\n]*+)"
    )
    option_quote = re.compile(r"\\{0,7}[\"'`]")
    position = 0
    scope = _CurlScope(text, syntax)
    while match := pattern.search(text, position):
        scope.advance(match.start())
        position = match.end()
        if match["curl"]:
            scope.anchor(
                match.end(),
                max(syntax.depth(match.start()), syntax.depth(match.end())),
                curl=True,
            )
            continue
        if match["boundary"]:
            scope.advance(match.end())
            continue
        if match["short"] and not scope.curl:
            continue
        scope.anchor(match.end(), syntax.depth(match.start()), curl=False)
        start = match.end()
        option_start = match.start("long") if match["long"] else match.start("short")
        closing = option_quote.match(text, start)
        if (
            closing is not None
            and option_start >= len(closing[0])
            and text[option_start - len(closing[0]) : option_start] == closing[0]
            and syntax.depth(option_start - len(closing[0])) == syntax.depth(start)
            and (
                text[closing.end() : closing.end() + 1].isspace()
                or text[closing.end() : closing.end() + 1] == "="
            )
        ):
            # The option owns this complete delimiter, not the argument. Keep
            # its source escape and start the value at its independent opener.
            start = closing.end() + int(text[closing.end() : closing.end() + 1] == "=")
            while start < len(text) and text[start].isspace():
                start += 1
        end = syntax.value(
            text,
            start,
            operator_depth=syntax.depth(match.start()),
            construct=match.start(),
            trim=False,
        )
        token = syntax.quote_token(text, start)
        closed = token and syntax.quoted_end(text, start) == end
        if token is None:
            escaped_end = (
                syntax.owned_quote_ends.get(start)
                if source_escapes and syntax.depths is not None
                else syntax.escaped_ends.get(start)
            )
            escaped = re.compile(r"\\{1,7}[\"']").match(text, start)
            if (
                escaped is not None
                and escaped_end is not None
                and text.endswith(escaped[0], escaped.end(), escaped_end)
            ):
                # Keep both whole source delimiters. Removing the closing
                # backslash damages JSON and widens the confirming pass.
                token, closed, end = escaped[0], True, escaped_end
        begin = start + (len(token) if token else 0)
        finish = end - ((1 if token == "$'" else len(token)) if closed else 0)
        key = "cookie" if (match["long"] or match["short"]) in {"-b", "--cookie"} else "password"
        if key == "password":
            colon = text.find(":", begin, finish)
            if colon < 0:
                continue
            begin = colon + 1
        if not token:
            finish = syntax.trim(text, begin, finish)
        if syntax.private(text, key, begin, finish):
            kind = "cookie" if key == "cookie" else "sensitive_field"
            boundary = scope.advance(end, stop_at_boundary=not token)
            if boundary is not None:
                # Keep the separator so the next application retains the
                # scope reset. Ambiguity may not reveal any of the selected
                # value: mask its tail too, and still visit later anchors.
                position = max(position, boundary)
                if begin < min(finish, boundary):
                    yield begin, min(finish, boundary), kind, _REDACTED
                if max(begin, boundary + 1) < finish:
                    yield max(begin, boundary + 1), finish, kind, _REDACTED
            else:
                position = max(position, end)
                yield begin, finish, kind, _REDACTED
        else:
            # A complete exempt value is a bounded argument context. Its
            # internal punctuation cannot reset the containing curl scope,
            # but the regex still enters it to find nested private flags.
            scope.protected_end = max(scope.protected_end, finish)


def _curl_source_spans(text):
    """Keep shell backslashes at their source layer, not a later JSON layer."""
    original = _SpanIndex(_curl_json_spans(text))
    view, layers = text, []
    ranges = list(_form_ranges(text))
    decoded, edits = _decode_view(view, form=True, ranges=ranges)
    if edits:
        view = decoded
        ranges = _decoded_ranges(ranges, edits)
        layers.append(edits)
    for _ in range(3):
        decoded, edits = _decode_view(view)
        if not edits:
            break
        view = decoded
        ranges = _decoded_ranges(ranges, edits)
        layers.append(edits)
    depths = _decoding_depths(len(text), layers) if layers else None
    syntax = _Syntax(view, depths)
    spans = _SpanIndex(_curl_spans(view, syntax, source_escapes=True))
    del syntax, depths
    if layers:
        spans = _SpanIndex(_merge_spans([spans, _curl_json_spans(view)]))
    for edits in reversed(layers):
        spans = _SpanIndex(_original_spans(spans, edits))
    yield from _merge_spans([original, spans])


def _curl_json_spans(text):
    """Scan proven JSON string contents without erasing shell escape parity.

    General canonicalization deliberately reveals several JSON layers. Shell
    backslashes instead belong to the string's own layer. Reuse the JSON
    lexer's disjoint proven string ranges, unwrap each complete string, and
    map only its curl spans back. At most three encoding layers are visited;
    each child syntax object is released before the next one is constructed.
    """
    forms = iter(_form_ranges(text))
    form = next(forms, None)
    for start, end, _, _ in _har_spans(text, _quotes=True):
        while form is not None and form[1] <= start:
            form = next(forms, None)
        view, layers = text[start:end], []
        if form is not None and form[0] <= start < form[1]:
            view, edits = _decode_view(view, form=True, ranges=[(0, len(view))])
            if edits:
                layers.append(edits)
        for _ in range(4):
            try:
                value = json.loads(view)
            except (ValueError, RecursionError):
                value = None
            if isinstance(value, str):
                break
            # Unwrap the lexer's proven encoded delimiters as whole layers,
            # stopping as soon as the string itself is valid JSON.
            decoded, edits = _decode_view(view, json_escapes=view.startswith("\\"))
            if not edits:
                break
            view = decoded
            layers.append(edits)
        else:
            value = None
        if not isinstance(value, str) or not re.search(r"curl|--(?:user|cookie)", value):
            continue
        inner, edits = _decode_view(view[1:-1], json_escapes=True)
        syntax = _Syntax(inner)
        spans = _SpanIndex(_curl_spans(inner, syntax))
        del syntax
        if edits:
            spans = _SpanIndex(_original_spans(spans, edits))
        spans = _SpanIndex((a + 1, b + 1, k, r) for a, b, k, r in spans)
        for edits in reversed(layers):
            spans = _SpanIndex(_original_spans(spans, edits))
        for a, b, kind, replacement in spans:
            yield start + a, start + b, kind, replacement


def _path_spans(text):
    roots = (
        r"/(?:tmp|var|etc|home|Users|opt|usr|proc|sys|dev|run|srv|mnt|media|root|private|"
        r"Library|Volumes|app|workspace)(?:/|\b)"
    )
    plausible = r"(?:" + roots + r"|/[A-Za-z0-9._-]++/|[A-Za-z]:[\\/]|\\(?:\\)?[A-Za-z])"
    public_paths = {"/docs/plan.md", "/etc/hosts"}
    quoted = re.compile(
        r"""(?P<q>["'])(?P<path>(?:/[^"'\r\n/]*+/|""" + plausible + r""")[^"'\r\n]*+)(?P=q)"""
    )
    bare = re.compile(r"(?<![A-Za-z0-9:/\]])" + plausible + r"""[^\s<>"'`,;{}\]\[)]*+""")

    def matches(pattern, group):
        for match in pattern.finditer(text):
            value = match[group] if group else match.group()
            if (
                value not in public_paths
                and not value.startswith("//")
                and _REDACTED not in value
                and not any(char in value for char in "?&=")
                and not text.startswith(_REDACTED, match.end())
            ):
                yield *(match.span(group) if group else match.span()), "local_path", _REDACTED

    yield from _merge_spans([matches(quoted, "path"), matches(bare, None)])


class _JsonFrame:
    def __init__(self, stack, index):
        self.stack, self.index = stack, index

    def __getitem__(self, key):
        offset = self.index * 4
        flags = self.stack.data[offset]
        if key == "kind":
            return "[" if flags & 1 else "{"
        if key == "state":
            return self.stack.states[(flags >> 1) & 7]
        if key == "key":
            return self.stack.keys[(flags >> 5) & 3]
        if key == "private":
            return bool(flags & 16)
        if key == "start":
            return self.stack.data[offset + 1]
        column = 2 if key == "value" else 3
        return self.stack.data[offset + column] - 1

    def get(self, key, default=None):
        result = self[key]
        return default if key in {"value", "slot"} and result < 0 else result

    def __setitem__(self, key, value):
        offset = self.index * 4
        if key == "state":
            flags = self.stack.data[offset]
            self.stack.data[offset] = (flags & ~14) | (self.stack.states.index(value) << 1)
        elif key == "key":
            flags = self.stack.data[offset]
            index = self.stack.keys.index(value) if value in self.stack.keys else 0
            self.stack.data[offset] = (flags & ~96) | (index << 5)
        elif key == "private":
            flags = self.stack.data[offset]
            self.stack.data[offset] = (flags | 16) if value else (flags & ~16)
        else:
            self.stack.data[offset + (2 if key == "value" else 3)] = value + 1


class _JsonFrames:
    """Bounded numeric lexer state, including deeply nested benign JSON."""

    states = (None, "first_key", "key", "colon", "value", "comma", "array_comma", "first_value")
    keys = (None, "name", "value", "cookies")

    def __init__(self):
        self.data = array("I")
        self.length = 0

    def __len__(self):
        return self.length

    def __getitem__(self, index):
        if index < 0:
            index += self.length
        if not 0 <= index < self.length:
            raise IndexError(index)
        return _JsonFrame(self, index)

    def append(self, frame):
        flags = int(frame["kind"] == "[") | (self.states.index(frame["state"]) << 1)
        row = array(
            "I",
            (
                flags,
                frame["start"],
                0,
                (frame.get("slot") or 0) + 1 if frame.get("slot") is not None else 0,
            ),
        )
        if len(self.data) == self.length * 4:
            self.data.extend(row)
        else:
            self.data[self.length * 4 : self.length * 4 + 4] = row
        self.length += 1

    def pop(self):
        self.length -= 1


def _har_json_spans(
    text, *, _depth=0, _quotes=False, _bounds=False, _percent_depth=0, _form_depth=0, _levels=None
):
    """Memoized strict JSON candidates, including candidates swallowed by an error.

    Lexical string ends are indexed once. Every container is parsed once, even
    when a failed outer candidate initially treated its opening brace as data.
    No parser resynchronizes *inside* a damaged object.
    """
    _bounds = _bounds or _quotes == "containers"
    quote_ends = _PositionIndex(text)
    bad = array("I")
    previous = None
    for token in re.finditer(r'\\(?:u[0-9a-fA-F]{4}|["\\/bfnrt])|\\[\s\S]?|["\x00-\x1f]', text):
        if token[0] == '"':
            if previous is not None:
                quote_ends[previous] = token.end()
            previous = token.start()
        elif (
            token[0][0] != "\\"
            or re.fullmatch(r'\\(?:u[0-9a-fA-F]{4}|["\\/bfnrt])', token[0]) is None
        ):
            bad.append(token.start())
    scalar = re.compile(r"-?(?:0|[1-9][0-9]*+)(?:\.[0-9]++)?(?:[eE][+-]?[0-9]++)?|true|false|null")
    whitespace = re.compile(r"[ \t\r\n]*+")
    memo = _PositionIndex(text)
    slots, selected, nested = [], [], []
    slot_anchors, nested_anchors = array("I"), array("I")
    seen_strings = set()
    frames = _JsonFrames()
    key_start = 0
    quote_slots = array("I")
    proven = _PositionIndex(text)
    quote_begins = array("I")

    def token_at(position):
        char = text[position]
        if _levels is not None and char in '{}[]:,"' and _levels[position] != _levels[root]:
            return None  # A decoded data delimiter is not structure at this JSON layer.
        if char == '"':
            end = quote_ends.get(position, len(text))
            invalid = bisect_left(bad, position + 1)
            if invalid < len(bad) and bad[invalid] < end:
                return None
            return char, end
        if char in "{}[]:,":
            return char, position + 1
        match = scalar.match(text, position)
        return (char, match.end()) if match else None

    def close_record(frame):
        if frame.get("private"):
            slot = frame.get("value")
            while slot is not None:
                selected[slot] = True
                slot = slots[slot][4]

    def register(frame, kind, start, end):
        if _quotes or _bounds:
            return None
        key = frame.get("key")
        if key not in {"value", "cookies"}:
            return None
        quoted = kind == '"'
        first, stop = start + int(quoted), end - int(quoted and text[end - 1 : end] == '"')
        opaque = stop - first <= len(_REDACTED) and text[first:stop] in {_REDACTED, "REDACTED", ""}
        if quoted:
            raw = text[first:stop]
            value, edits = _decode_view(raw, json_escapes=True)
            depths = _decoding_depths(len(raw), [edits]) if edits else None
            opaque = _credential_value_exempt("password", value, depths=depths)
        slot = len(slots)
        previous = frame.get("value") if key == "value" and not opaque else None
        if not quoted:
            slot_anchors.extend((slot, key_start, key_start + 1))
        slots.append(
            [
                first,
                stop,
                "cookie" if key == "cookies" else "sensitive_field",
                _REDACTED if quoted else '"' + _REDACTED + '"',
                previous,
            ]
        )
        selected.append(key == "cookies" and not opaque and text[start:end] != "[]")
        if key == "value" and not opaque:
            frame["value"] = slot
        return slot

    def string_data(start, end):
        if (_bounds and not _quotes) or start in seen_strings or _depth >= 3 or end - start <= 2:
            return
        seen_strings.add(start)
        raw = text[start + 1 : end - 1]
        if "\\" in raw:
            inner, edits = _decode_view(raw, json_escapes=True)
            if not edits:
                return
            spans = _original_spans(
                _har_spans(
                    inner,
                    _depth=_depth + 1,
                    _quotes=_quotes,
                    _percent_depth=_percent_depth,
                    _form_depth=_form_depth,
                ),
                edits,
            )
        elif "%" in raw and _percent_depth < 3:
            # A proven JSON string can hold a whole percent-encoded JSON
            # layer. Its own lexer, not the global canonical view, decides
            # structure. Mixed-depth punctuation stays string data.
            inner, layers = raw, []
            for _ in range(3 - _percent_depth):
                inner, edits = _decode_view(inner)
                if not edits:
                    return
                layers.append(edits)
                try:
                    document = json.loads(inner)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(document, (dict, list)):
                    return
                break
            else:
                return
            levels = _decoding_depths(len(raw), layers)
            spans = _har_json_spans(
                inner,
                _depth=_depth + 1,
                _quotes=_quotes,
                _percent_depth=_percent_depth + len(layers),
                _form_depth=_form_depth,
                _levels=levels,
            )
            for edits in reversed(layers):
                spans = _original_spans(spans, edits)
        else:
            return
        for a, b, k, r in spans:
            if _quotes:
                proven[a + start + 1] = b + start + 1
            else:
                if isinstance(r, _JsonReplacement):
                    nested_anchors.extend(
                        (len(nested), r.anchor[0] + start + 1, r.anchor[1] + start + 1)
                    )
                nested.append((a + start + 1, b + start + 1, k, str(r)))

    candidates = re.finditer(r"[\[{]", text)
    covered = 0
    for candidate in candidates:
        root = candidate.start()
        if root < covered or (memo.data and memo.data[root] != 0):
            continue
        position = root
        while position < len(text):
            position = whitespace.match(text, position).end()
            if position == len(text):
                break
            token = token_at(position)
            if token is None:
                break
            kind, end = token
            start = position
            frame = frames[-1] if frames else None
            state = frame["state"] if frame else "value"
            if state in {"key", "first_key"}:
                if kind == "}" and state == "first_key":
                    pass
                elif kind != '"' or text[end - 1 : end] != '"':
                    break
                else:
                    key_start = start
                    try:
                        frame["key"] = json.loads(text[start:end]) if end - start <= 128 else None
                    except ValueError:
                        break
                    if _quotes is True:
                        quote_slots.extend((start, end))
                    frame["state"] = "colon"
                    position = end
                    continue
            elif state == "colon":
                if kind != ":":
                    break
                frame["state"] = "value"
                position = end
                continue
            elif state in {"comma", "array_comma"}:
                if kind == ",":
                    frame["state"] = "key" if frame["kind"] == "{" else "value"
                    position = end
                    continue
                if kind != ("}" if frame["kind"] == "{" else "]"):
                    break
            if kind in "}]":
                if (
                    frame is None
                    or kind != ("}" if frame["kind"] == "{" else "]")
                    or state not in {"comma", "array_comma", "first_key", "first_value"}
                ):
                    break
                close_record(frame)
                memo[frame["start"]] = end
                frames.pop()
                quote_begin = quote_begins.pop()
                for q in range(quote_begin, len(quote_slots), 2):
                    proven[quote_slots[q]] = quote_slots[q + 1]
                del quote_slots[quote_begin:]
                if frame.get("slot") is not None:
                    slot = slots[frame["slot"]]
                    slot[1] = end
                    if end - frame["start"] == 2 and text[frame["start"] : end] == "[]":
                        selected[frame["slot"]] = False
                position = end
                if not frames:
                    covered = end
                    break
                continue
            if state not in {"value", "first_value"} or kind in ":,":
                break
            slot = register(frame, kind, start, end) if frame else None
            if frame:
                frame["state"] = "comma" if frame["kind"] == "{" else "array_comma"
                if (
                    frame.get("key") == "name"
                    and kind == '"'
                    and text[end - 1 : end] == '"'
                    and end - start <= 128
                ):
                    try:
                        frame["private"] = frame.get("private", False) or redaction_sensitive_key(
                            json.loads(text[start:end])
                        )
                    except ValueError:
                        break
            if kind in "{[":
                cached = memo.data[start] if memo.data else 0
                if cached:
                    # A previously failed container cannot be reparsed from its
                    # parent. A successful one already contributed its slots.
                    if cached < 0:
                        break
                    position = cached
                    if slot is not None:
                        slots[slot][1] = cached
                    continue
                quote_begins.append(len(quote_slots))
                frames.append(
                    {
                        "kind": kind,
                        "state": "first_key" if kind == "{" else "first_value",
                        "start": start,
                        "slot": slot,
                    }
                )
            elif kind == '"':
                if text[end - 1 : end] != '"' or end - start == 1:
                    position = end
                    break
                if _quotes is True:
                    quote_slots.extend((start, end))
                string_data(start, end)
            elif _bounds and frame is not None:
                memo[start] = end
            position = end
        # Retain partial records before their own first error. Mark every
        # outstanding container failed, so late failures remain linear.
        for frame in frames:
            close_record(frame)
            memo[frame["start"]] = max(1, position)
            memo.data[frame["start"]] = -max(1, position)
            if frame.get("slot") is not None:
                slots[frame["slot"]][1] = len(text)
        frames.length = 0
        del quote_slots[:]
        del quote_begins[:]
    if _bounds:

        def bounds():
            if not memo.data:
                return
            enclosing = array("I")
            for start, end in enumerate(memo.data):
                if end > 0:
                    while enclosing and enclosing[-1] <= start:
                        enclosing.pop()
                    # A standalone candidate cannot prove a preceding key's
                    # boundary. Its enclosing JSON object must also close.
                    if enclosing:
                        yield start, end, "proven_container", ""
                    if text[start] == "{":
                        enclosing.append(end)

        from heapq import merge

        yield from merge(
            bounds(),
            ((a, b, "proven_container", "") for a, b in enumerate(proven.data) if b)
            if proven.data
            else (),
        )
        return
    if _quotes:
        if proven.data:
            for start, end in enumerate(proven.data):
                if end:
                    yield start, end, "proven_string", ""
        return
    yield from _merge_spans(
        [
            (
                span
                for span, use in zip(
                    _anchored_spans(((a, b, k, r) for a, b, k, r, _ in slots), slot_anchors),
                    selected,
                    strict=True,
                )
                if use
            ),
            _anchored_spans(nested, nested_anchors),
        ]
    )


def _har_spans(text, syntax=None, *, _depth=0, _form_depth=0, _quotes=False, _percent_depth=0):
    # Recovery and wrapper views may overlap. Order spans with a linear numeric
    # index rather than sorting or assuming recovery visits them in source order.
    ordered = _PositionIndex(text)
    structured = array("I")
    replacements, replacement_ids = [], {}
    for start, end, kind, replacement in _har_candidates(
        text,
        syntax,
        _depth=_depth,
        _form_depth=_form_depth,
        _quotes=_quotes,
        _percent_depth=_percent_depth,
    ):
        if end <= start:
            continue
        flags = int(kind == "cookie") | (int(replacement.startswith('"')) << 1)
        encoded = (end << 2) | flags
        previous = ordered.data[start] if ordered.data else 0
        previous = structured[-previous - 1] if previous < 0 else previous
        if encoded > previous:
            if isinstance(replacement, _JsonReplacement):
                value = str(replacement)
                if value not in replacement_ids:
                    replacement_ids[value] = len(replacements)
                    replacements.append(value)
                ordered[start] = -len(structured) - 1
                structured.extend((encoded, *replacement.anchor, replacement_ids[value]))
            else:
                ordered[start] = encoded
    if ordered.data:
        for start, encoded in enumerate(ordered.data):
            if not encoded:
                continue
            replacement = None
            if encoded < 0:
                slot = -encoded - 1
                encoded = structured[slot]
                replacement = _JsonReplacement(
                    replacements[structured[slot + 3]],
                    structured[slot + 1],
                    structured[slot + 2],
                )
            yield (
                start,
                encoded >> 2,
                "proven_string" if _quotes else "cookie" if encoded & 1 else "sensitive_field",
                replacement
                if replacement is not None
                else '"' + _REDACTED + '"'
                if encoded & 2
                else _REDACTED,
            )


def _har_candidates(text, syntax=None, *, _depth=0, _form_depth=0, _quotes=False, _percent_depth=0):
    """Find JSON-layer HAR records at every offset, without decoded structure."""
    if _depth > 3:
        return
    # Decode wrappers first only when structure itself is escaped. The lexer
    # below never interprets escaped characters inside a value as structure.
    if re.search(r'[{\[]\s*+\\+(?:"|u0022)', text):
        yield from _har_json_spans(
            text,
            _depth=_depth,
            _quotes=_quotes,
            _percent_depth=_percent_depth,
            _form_depth=_form_depth,
        )
        decoded, edits = _decode_view(text, json_escapes=True)
        if edits:
            yield from _original_spans(
                _har_spans(
                    decoded,
                    _depth=_depth + 1,
                    _quotes=_quotes,
                    _percent_depth=_percent_depth,
                    _form_depth=_form_depth,
                ),
                edits,
            )
    elif '"' in text and (
        "name" in text
        or "cookies" in text
        or "\\" in text
        or (_quotes == "containers" and "%" in text)
    ):
        yield from _har_json_spans(
            text,
            _depth=_depth,
            _quotes=_quotes,
            _percent_depth=_percent_depth,
            _form_depth=_form_depth,
        )
    # A whole JSON string is itself a wrapper, including prose around its HAR.
    stripped = text.strip()
    if stripped.startswith('"'):
        try:
            value = json.loads(stripped)
        except (ValueError, RecursionError):
            value = None
        if isinstance(value, str) and _depth < 3:
            inner, edits = _decode_view(stripped[1:-1], json_escapes=True)
            offset = len(text) - len(text.lstrip()) + 1
            spans = _har_spans(
                inner,
                _depth=_depth + 1,
                _quotes=_quotes,
                _percent_depth=_percent_depth,
                _form_depth=_form_depth,
            )
            if edits:
                spans = _original_spans(spans, edits)
            for a, b, k, r in spans:
                yield (
                    a + offset,
                    b + offset,
                    k,
                    r.shifted(offset) if isinstance(r, _JsonReplacement) else r,
                )
    form = re.compile(r"(?<![A-Za-z0-9_%])" + _NAME + r"=(?P<value>[^&\s\"'`<>]*+)")
    if _form_depth < 3 and form.search(text):
        for field in form.finditer(text):
            raw = field["value"]
            view, form_edits = _decode_view(raw, form=True, ranges=[(0, len(raw))])
            # One form layer is unquote-plus: decode literal pluses before
            # percent escapes, without spending the independent percent budget.
            # Keep both sparse maps so structural replacements retain their
            # source quoting and exact boundaries.
            view, edits = _decode_view(view) if "%" in view else (view, ())
            spans = _har_spans(
                view,
                _depth=_depth,
                _form_depth=_form_depth + 1,
                _quotes=_quotes,
                _percent_depth=_percent_depth,
            )
            if edits:
                spans = _original_spans(spans, edits)
            if form_edits:
                spans = _original_spans(spans, form_edits)
            for a, b, k, r in spans:
                yield (
                    field.start("value") + a,
                    field.start("value") + b,
                    k,
                    r.shifted(field.start("value")) if isinstance(r, _JsonReplacement) else r,
                )
    if "%" in text and _percent_depth < 3:
        try:
            json.loads(text)
        except (ValueError, RecursionError):
            pass
        else:
            return
        view, edits = _decode_view(text)
        if edits:
            yield from _original_spans(
                _har_spans(
                    view,
                    _depth=_depth,
                    _form_depth=_form_depth,
                    _quotes=_quotes,
                    _percent_depth=_percent_depth + 1,
                ),
                edits,
            )


def _putty_spans(text, syntax):
    headers = re.compile(r"(?i)(?<![A-Za-z0-9_])PuTTY-User-Key-File-[0-9]++:")
    mac = re.compile(r"(?im)(?<![A-Za-z0-9_])Private-(?:MAC|Hash):[^\r\n]*+")
    position = 0
    while match := headers.search(text, position):
        limit = syntax.proven_end(match.start()) or len(text)
        if limit < match.end():
            limit = len(text)
        end_match = mac.search(text, match.end(), limit)
        end = end_match.end() if end_match else limit
        yield match.start(), end, "private_key", _REDACTED
        position = max(end, match.end())


def _command_spans(text, syntax):
    pattern = re.compile(
        r"(?i)(?P<mysql>\b(?:mysql|mariadb|mysqldump)\b)|"
        r"(?P<attached>\s[\"']?-p(?=\S))|(?P<boundary>[\r\n;&|])|"
        r"(?P<sql>\b(?:IDENTIFIED\s++BY|WITH\s++PASSWORD)\s++)|"
        r"(?P<sshpass>\bsshpass\s++[\"']?-p)[^\S\r\n]*+"
    )
    # Statement tokens are consumed once, even when many CREATE words have
    # no principal. A lazy wildcard restarted at each CREATE is quadratic.
    sql_ranges = []
    begin = None
    principal = None
    for word in re.finditer(r"(?i)\b(?:CREATE|ALTER|GRANT|SET|USER|ROLE|LOGIN)\b|;", text):
        token = word[0].upper()
        if token == ";":
            if principal is not None:
                sql_ranges.append((principal, word.start()))
            begin, principal = None, None
        elif begin is None and token in {"CREATE", "ALTER", "GRANT", "SET"}:
            begin = word.start()
        elif begin is not None and principal is None and token in {"USER", "ROLE", "LOGIN"}:
            principal = word.start()
    if principal is not None:
        sql_ranges.append((principal, len(text)))
    sql_ranges = iter(sql_ranges)
    sql_range = next(sql_ranges, None)
    position = 0
    mysql = False
    mysql_depth = 0
    while match := pattern.search(text, position):
        position = match.end()
        if match["boundary"]:
            if syntax.depth(match.start()) <= mysql_depth:
                mysql = False
            continue
        if match["mysql"]:
            mysql = True
            mysql_depth = syntax.depth(match.start())
            continue
        if match["attached"] and not mysql:
            continue
        if match["sql"]:
            while sql_range is not None and sql_range[1] <= match.start():
                sql_range = next(sql_ranges, None)
            if sql_range is None or not sql_range[0] <= match.start() < sql_range[1]:
                continue
        start = match.end()
        if (
            text[start : start + 1] in {'"', "'", "`"}
            and text[start : start + 1] in match[0][:-1]
            and text[start + 1 : start + 2].isspace()
        ):
            start += 1
            while start < len(text) and text[start].isspace():
                start += 1
        end = syntax.value(
            text, start, operator_depth=syntax.depth(match.start()), construct=match.start()
        )
        if syntax.private(text, "password", start, end):
            position = max(match.end(), end)
            quoted = text[start : start + 1] in {'"', "'", "`"}
            yield (
                start + int(quoted),
                end - int(quoted and text[end - 1 : end] == text[start]),
                "sensitive_field",
                _REDACTED,
            )


def _view_spans(text, encoded_positions, syntax, header_proofs=()):
    return _merge_spans(
        [
            _assignment_spans(text, syntax),
            _header_spans(text, syntax, header_proofs),
            _query_spans(text, syntax, encoded_positions),
            _userinfo_spans(text, syntax),
            _cookie_spans(text, syntax),
            _jar_spans(text, syntax),
            _bearer_spans(text),
            _prefix_spans(text),
            _jwt_spans(text),
            _pem_spans(text, syntax),
            _putty_spans(text, syntax),
            _xml_spans(text, syntax),
            _html_spans(text, syntax),
            _curl_spans(text, syntax),
            _command_spans(text, syntax),
            _path_spans(text),
        ]
    )


def _close_assignment_spans(text, spans, syntax):
    contexts = iter(_assignment_spans(text, syntax, include_exempt=True))
    context = next(contexts, None)
    for start, end, kind, replacement in spans:
        while context and context[1] <= start:
            context = next(contexts, None)
        while context and context[0] < end:
            start = min(start, context[0])
            if context[1] > end:
                end = context[1]
                replacement = context[3]
            context = next(contexts, None)
        yield start, end, kind, replacement


def _close_bearer_spans(text, spans):
    """A mask inside a bearer value covers its whole context, never creates a suffix."""
    contexts = iter(
        re.finditer(r"(?i)bearer[\s+\u00a0]++(?P<value>(?:\[REDACTED\])?[^\s\"'<>{}\[\]]++)", text)
    )
    context = next(contexts, None)
    for start, end, kind, replacement in spans:
        while context and context.end("value") <= start:
            context = next(contexts, None)
        if context and context.start("value") < end:
            start = min(start, context.start("value"))
            end = max(end, context.end("value"))
        yield start, end, kind, replacement


def _form_ranges(text, depths=None):
    """Only a complete form body or an actual URL query gives '+' space syntax.

    Packet records and pasted captures contain independent form-body lines. A
    prose line with whitespace is never a form body, regardless of earlier '?'.
    """
    form = re.compile(_NAME + r"=[^&\s]*+(?:&" + _NAME + r"=[^&\s]*+)*+")
    encoded_form = re.compile(_NAME + r"(?:\+|\\{1,8}u002[bB])*+%(?:25){0,2}3[dD][^\s]*+")

    def bodies():
        if re.fullmatch(
            r"(?i)(?:curl|sshpass|mysql|mariadb|mysqldump|%3cinput|%3cmeta)[^\s]*+", text
        ):
            yield 0, len(text), "form", ""
        for line in re.finditer(r"[^\r\n]++", text):
            label = re.match(r"(?i)body:[^\S\r\n]*+", line[0])
            begin = label.end() if label else 0
            if form.fullmatch(line[0][begin:]) or encoded_form.fullmatch(line[0][begin:]):
                yield line.start() + begin, line.end(), "form", ""

    url_pattern = re.compile(r"(?i)(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*+://[^\s\"'<>]++")

    def embedded_bodies():
        for captured in re.finditer(
            r"(?i)(?<![A-Za-z0-9_%])(?:curl|sshpass|mysql|mariadb|mysqldump|%3cinput|%3cmeta)\+[^\s]++",
            text,
        ):
            yield captured.start(), captured.end(), "form", ""
        # A captured form is independent of surrounding prose. A field inside
        # an actual URL belongs only to its bounded query, never its fragment.
        urls = iter(url_pattern.finditer(text))
        url = next(urls, None)
        for form_field in re.finditer(r"(?<![A-Za-z0-9_%])" + _NAME + r"=[^\s]++", text):
            while url is not None and url.end() <= form_field.start():
                url = next(urls, None)
            if url is not None and url.start() <= form_field.start() < url.end():
                continue
            yield form_field.start(), form_field.end(), "form", ""

    def quoted_bodies():
        opening = {}
        for match in re.finditer(r"\\[\s\S]|[\"'`]", text):
            if match[0].startswith("\\"):
                continue
            level = depths[match.start()] if depths is not None else 0
            channel = match[0], level
            prior = opening.pop(channel, None)
            if prior is None:
                opening[channel] = match.start()
            else:
                start = prior + 1
                value = text[start : match.start()]
                if form.fullmatch(value) or encoded_form.fullmatch(value):
                    yield start, match.start(), "form", ""
                elif depths is not None and re.match(_NAME + "=", value):
                    # Whitespace revealed below the operator's layer is data.
                    if all(
                        not char.isspace() or depths[start + i] > level
                        for i, char in enumerate(value)
                    ):
                        yield start, match.start(), "form", ""

    def queries():
        for match in url_pattern.finditer(text):
            question = text.find("?", match.start(), match.end())
            fragment = text.find("#", match.start(), match.end())
            if question >= 0 and (fragment < 0 or question < fragment):
                yield question + 1, fragment if fragment >= 0 else match.end(), "form", ""

    for start, end, _, _ in _merge_spans([bodies(), embedded_bodies(), quoted_bodies(), queries()]):
        yield start, end


def _decoded_ranges(ranges, edits):
    """Carry known form boundaries forward without losing decoded value data."""
    if not ranges or not edits:
        return ranges
    values = iter(edits)
    edit = next(values, None)
    shift = 0
    result = []
    for start, end in ranges:
        bounds = []
        for position, ending in ((start, False), (end, True)):
            while edit is not None and edit.original_end <= position:
                shift = edit.original_end - edit.end
                edit = next(values, None)
            if edit is not None and edit.original_start < position:
                # Coalesced escapes have a uniform scale. Round only a
                # contributing escape, never the rest of the coalesced run.
                width = edit.original_end - edit.original_start
                numerator = (position - edit.original_start) * (edit.end - edit.start)
                mapped = (numerator + width - 1) // width if ending else numerator // width
                bounds.append(edit.start + mapped)
            else:
                bounds.append(position - shift)
        result.append(tuple(bounds))
    return result


def _decoded_proofs(ranges, edits):
    """Map opening-ordered proofs with at most two sparse-edit traversals."""
    if not edits or not ranges:
        return ranges

    previous_end = 0
    for start, end in ranges:
        if start < previous_end:
            break
        previous_end = end
    else:
        # Preserve the usual disjoint-string path, including its storage bound.
        return _QuoteRanges(_decoded_ranges(ranges, edits))

    mapped = _QuoteRanges(ranges)
    order = array("I", range(len(mapped)))
    scratch = array("I", [0]) * len(mapped)

    def order_by(coordinates):
        nonlocal order, scratch
        # Existing proof coordinates are unsigned 32-bit offsets. Four stable
        # byte passes give linear work, even for crossing or equal-start proofs;
        # no comparison sort or stack per JSON nesting level is needed.
        for shift in (0, 8, 16, 24):
            counts = array("I", [0]) * 256
            for index in order:
                counts[(coordinates[index] >> shift) & 255] += 1
            total = 0
            for bucket, count in enumerate(counts):
                counts[bucket] = total
                total += count
            for index in order:
                bucket = (coordinates[index] >> shift) & 255
                scratch[counts[bucket]] = index
                counts[bucket] += 1
            order, scratch = scratch, order

    def carry(coordinates, indexes, ending):
        values = iter(edits)
        edit = next(values, None)
        shift = 0
        for index in indexes:
            position = coordinates[index]
            while edit is not None and edit.original_end <= position:
                shift = edit.original_end - edit.end
                edit = next(values, None)
            if edit is not None and edit.original_start < position:
                width = edit.original_end - edit.original_start
                numerator = (position - edit.original_start) * (edit.end - edit.start)
                offset = (numerator + width - 1) // width if ending else numerator // width
                coordinates[index] = edit.start + offset
            else:
                coordinates[index] = position - shift

    order_by(mapped.ends)
    carry(mapped.starts, range(len(mapped)), False)
    carry(mapped.ends, order, True)
    # Ends remain ordered under outward rounding. Stable start ordering restores
    # tuple order when distinct source starts collapse into the same escape.
    order_by(mapped.starts)
    return _QuoteRanges((mapped.starts[index], mapped.ends[index]) for index in order)


def _layered_form_ranges(text, depths, known):
    """Nested form prefixes get their own layer, bounded by the parent field."""
    discovered = list(_form_ranges(text, depths))
    scopes = _merge_spans(
        [
            [(a, b, "form", "") for a, b in known],
            [(a, b, "form", "") for a, b in discovered],
        ]
    )
    prefix = re.compile(_NAME + "=")
    for start, end, _, _ in scopes:
        first = prefix.match(text, start, end)
        level = max(depths[start - 1] if start else 0, depths[first.end() - 1] if first else 0)
        field_start = start
        # Each parent field is visited once. Deeper '&' is data for this parent.
        boundaries = (
            m.start()
            for m in re.finditer("&", text[start:end])
            if depths[start + m.start()] <= level
        )
        for boundary in boundaries:
            field_end = start + boundary
            yield from _form_prefix_ranges(text, depths, prefix, field_start, field_end, level)
            yield field_end, field_end + 1, level
            field_start = field_end + 1
        yield from _form_prefix_ranges(text, depths, prefix, field_start, end, level)


def _form_prefix_ranges(text, depths, prefix, start, end, level):
    cursor = start
    while match := prefix.match(text, cursor, end):
        nested_level = depths[match.end() - 1]
        if nested_level > level:
            yield start, cursor, level
            start, level = cursor, nested_level
        cursor = match.end()
    yield start, end, level


def _redact_once(
    text: str,
    *,
    private_spans: Callable[..., Iterable[tuple[int, int]]] | None = None,
    private_placeholder: str = _REDACTED,
) -> tuple[str, list[dict[str, str | int]]]:
    """Canonical decoded views, forward scans, sparse offsets, one output join."""
    if type(private_placeholder) is not str or (
        private_placeholder != _REDACTED and private_placeholder != "redacted"
    ):
        raise ValueError("Invalid private placeholder")
    if private_spans is not None:

        def additions(value, *, original):
            result = []
            previous = 0
            valid = True
            try:
                for start, end in private_spans(value, original=original):
                    if (
                        type(start) is not int
                        or type(end) is not int
                        or not previous <= start < end <= len(value)
                    ):
                        valid = False
                        break
                    result.append((start, end, "sensitive_field", private_placeholder))
                    previous = end
            except Exception:
                valid = False
            if not valid:
                raise ValueError("Invalid additive private spans")
            return result

        raw_additions = additions(text, original=True)
    layers = []
    view = text
    barriers = []
    json_lines = []
    proofs = (
        _QuoteRanges((a, b) for a, b, _, _ in _har_json_spans(text, _quotes=True))
        if '{"' in text or '{ "' in text
        else []
    )
    source_header_proofs = header_proofs = (
        _QuoteRanges((a, b) for a, b, _, _ in _har_spans(text, _quotes=True))
        if "\\" in text and re.search(r'[{\[]\s*+\\+(?:"|u0022)', text)
        else ()
    )
    ranges = list(_form_ranges(text))
    form = bool(ranges) and ranges[0] == (0, len(text))
    if "+" in text or re.search(r"\\{1,8}u002b", text, re.I):
        # Decode before percent escapes: an encoded %2B remains a literal plus.
        view, edits = _decode_view(text, form=True, ranges=ranges)
        if edits:
            ranges = _decoded_ranges(ranges, edits)
            proofs = _decoded_proofs(proofs, edits)
            header_proofs = _decoded_proofs(header_proofs, edits)
            layers.append(edits)
    # Escaped documents can reveal percent escapes, and conversely. Each
    # successful layer consumes its own budget; failed probes do not consume it.
    budgets = {False: 3, True: 3}
    while True:
        changed = False
        for decoding in (False, True):
            if not budgets[decoding]:
                continue
            if decoding:
                from heapq import merge

                new_lines = [
                    (m.start(), m.end())
                    for m in re.finditer(r'\\(?:u[0-9a-fA-F]{4}|["\\/bfnrt])', view)
                    if m[0] in {r"\n", r"\r"}
                ]
                json_lines = list(merge(json_lines, new_lines))
                barriers.extend(
                    (m.start(), m.start("slash"))
                    for m in re.finditer(
                        r"(?i)(?<![A-Za-z0-9+.-])(?:https?|ftp|file|wss?)://[^\s/?#\\]*+(?P<slash>\\[nr])",
                        view,
                    )
                )
            decoded, edits = _decode_view(view, json_escapes=decoding)
            if edits:
                budgets[decoding] -= 1
                view = decoded
                ranges = _decoded_ranges(ranges, edits)
                proofs = _decoded_proofs(proofs, edits)
                header_proofs = _decoded_proofs(header_proofs, edits)
                barriers = _decoded_ranges(barriers, edits)
                json_lines = _decoded_ranges(json_lines, edits)
                layers.append(edits)
                changed = True
        if not changed:
            break
    depths = _decoding_depths(len(text), layers)
    if layers and "+" in view:
        scoped = list(_layered_form_ranges(view, depths, ranges))
        decoded, edits = _decode_view(view, form=True, ranges=scoped, form_depths=depths)
        if edits:
            view = decoded
            proofs = _decoded_proofs(proofs, edits)
            header_proofs = _decoded_proofs(header_proofs, edits)
            barriers = _decoded_ranges(barriers, edits)
            json_lines = _decoded_ranges(json_lines, edits)
            layers.append(edits)
            depths = _decoding_depths(len(text), layers)
    encoded = _EncodedPositions(text, layers, form=form)
    # Views may expose an independently valid inner JSON document as well.
    revealed = (
        _QuoteRanges((a, b) for a, b, _, _ in _har_json_spans(view, _quotes=True))
        if '{"' in view or '{ "' in view
        else []
    )
    from heapq import merge

    syntax = _Syntax(view, depths, _QuoteRanges(merge(proofs, revealed)), barriers, json_lines)
    if layers:
        # Consult original-layer container proofs lazily, only when a quoted
        # sensitive key actually has a structured value. No view is copied.
        syntax.source_layers = (text, layers)
    spans = _SpanIndex(
        _merge_spans(
            [
                _close_bearer_spans(
                    view,
                    _close_assignment_spans(
                        view, _view_spans(view, encoded, syntax, header_proofs), syntax
                    ),
                )
            ]
        )
    )
    if private_spans is not None and view != text:
        decoded_additions = additions(view, original=False)
        if decoded_additions:
            spans = _SpanIndex(_merge_spans([spans, decoded_additions]))
    for edits in reversed(layers):
        spans = _SpanIndex(_original_spans(spans, edits))
    # Original quoted/structured values have their original quote terminators,
    # even when JSON unescaping reveals quotes inside the credential itself.
    del syntax, depths
    original_proofs = (
        _QuoteRanges((a, b) for a, b, _, _ in _har_json_spans(text, _quotes=True))
        if '{"' in text or '{ "' in text
        else []
    )
    original_syntax = _Syntax(text, proven=original_proofs) if layers else None
    original_fields = _assignment_spans(text, original_syntax) if layers else ()
    original_headers = _header_spans(text, original_syntax, source_header_proofs) if layers else ()
    original_markup = _xml_spans(text, original_syntax) if layers else ()
    spans = _SpanIndex(
        _merge_spans(
            [
                spans,
                original_fields,
                original_headers,
                original_markup,
                _bearer_spans(text),
                _har_spans(text),
            ]
        )
    )
    # A shell escape's parity must also survive JSON-string decoding. Do not
    # retain the original syntax tables while constructing a child string's.
    del original_syntax
    if any(word in source for source in (text, view) for word in ("curl", "--user", "--cookie")):
        spans = _SpanIndex(_merge_spans([spans, _curl_source_spans(text)]))
    if private_spans is not None and raw_additions:
        spans = _SpanIndex(_merge_spans([spans, raw_additions]))
    if not spans:
        return text, []
    parts = []
    counts = {}
    position = 0
    for start, end, kind, replacement in spans:
        if text[start:end] == _REDACTED:
            continue
        parts.extend((text[position:start], replacement))
        position = end
        counts[kind] = counts.get(kind, 0) + 1
    parts.append(text[position:])
    return "".join(parts), [
        {"kind": kind, "count": count} for kind, count in sorted(counts.items())
    ]


def redact_text(
    text: str,
    *,
    private_spans: Callable[..., Iterable[tuple[int, int]]] | None = None,
    private_placeholder: str = _REDACTED,
) -> tuple[str, list[dict[str, str | int]]]:
    """Bounded fixpoint; optional additive spans apply only to the initial input."""
    if type(private_placeholder) is not str or (
        private_placeholder != _REDACTED and private_placeholder != "redacted"
    ):
        raise ValueError("Invalid private placeholder")
    counts = {}
    for attempt in range(4):
        if attempt == 0 and private_spans is not None:
            if private_placeholder == _REDACTED:
                output, findings = _redact_once(text, private_spans=private_spans)
            else:
                output, findings = _redact_once(
                    text, private_spans=private_spans, private_placeholder=private_placeholder
                )
        else:
            output, findings = _redact_once(text)
        for finding in findings:
            kind = finding["kind"]
            counts[kind] = counts.get(kind, 0) + finding["count"]
        if output == text:
            break
        text = output
    else:
        counts["redaction_unstable"] = 1
        text = _REDACTED  # Consumers that cannot refuse must not retain a partial mask.
    return text, [{"kind": kind, "count": count} for kind, count in sorted(counts.items())]


def redact_display(text: str) -> str:
    """A nonrefusable display/log value must fail closed as one complete mask."""
    output, findings = redact_text(text)
    return _REDACTED if any(row["kind"] == "redaction_unstable" for row in findings) else output
