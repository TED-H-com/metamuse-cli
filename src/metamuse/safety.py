"""Local safety boundaries. Source and model content never issue permissions."""

from __future__ import annotations

import base64
import binascii
import errno
import ipaddress
import json
import os
import re
import shlex
import socket
import sqlite3
import stat
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
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
            except m.CoreError:
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
                    corrupt.append(
                        {
                            "auth_ref": name[:-5],
                            "reason": "Secret record is unavailable or invalid",
                            "next_action": "metamuse auth revoke --auth-ref " + name[:-5],
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
        """Unlink exactly one owned regular record without opening its contents."""
        directory = None
        try:
            if not isinstance(auth_ref, str) or not _AUTH_REF.fullmatch(auth_ref):
                _secret_error()
            directory = self._directory(create=False)
            name = auth_ref + ".json"
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                _secret_error()
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)
            return
        except (m.CoreError, OSError, ValueError, TypeError):
            pass
        finally:
            if directory is not None:
                os.close(directory)
        _secret_error()


def list_auth(ctx: AppContext) -> m.Envelope:
    ctx.require_interactive()
    binding = ctx.policy.muse.binding
    corrupt: list[dict[str, str]] = []
    records = (ctx.secret_store or SecretStore()).list(corrupt=corrupt)
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


class AccessScope(m.Model):
    operations: list[Literal["muse.open", "muse.read", "collect.remote"]] = Field(
        min_length=1, max_length=3
    )
    target_digests: list[m.Digest] = Field(min_length=1, max_length=200)


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

    @model_validator(mode="after")
    def require_selection(self) -> ShareScope:
        if bool(self.packet_digests) == (self.snapshot is not None):
            raise ValueError("Select exact packets or one immutable snapshot")
        return self


class PermitUse(m.Model):
    kind: Literal["access", "share"]
    project_id: m.ID
    provider: str
    account: str
    operation: OperationName
    digest: m.Digest
    packet_id: m.ID | None = None


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
    if isinstance(scope, AccessScope):
        if use.operation not in scope.operations or use.digest not in scope.target_digests:
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
    fingerprint = m.payload_digest({"permit_id": permit_id, "use": use.model_dump(mode="json")})

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


# --- Redaction (B07/B11) ---


@lru_cache(maxsize=2048)
def redaction_sensitive_key(key: str) -> bool:
    """ASCII credential names only; normalize percent encoding and -/_ spelling."""
    import re
    from urllib.parse import unquote_plus

    decoded = key
    for _ in range(3):
        decoded = unquote_plus(decoded)
    if re.fullmatch(r"[A-Za-z0-9_.-]+", decoded, flags=re.ASCII) is None:
        return False
    name = decoded.lower().replace("-", "").replace("_", "")
    if name in {"nextpagetoken", "eostoken", "bostoken", "padtoken", "unktoken", "maxtoken"}:
        return False
    return (
        name
        in {
            "headers",
            "authorization",
            "cookie",
            "cookieheader",
            "setcookie",
            "accesstoken",
            "authtoken",
            "apikey",
            "token",
            "refreshtoken",
            "idtoken",
            "clientsecret",
            "secret",
            "password",
            "privatekey",
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
        or name.endswith(("sess", "sessid", "session", "token", "secret", "cookie", "password"))
        or decoded.lower().startswith(("x-amz-", "x-goog-", "__secure-", "__host-"))
    )


def redaction_sensitive_value(key: str, value: Any) -> bool:
    """A credential name needs a value, rather than vocabulary or code prose."""
    if not redaction_sensitive_key(key):
        return False
    if not isinstance(value, str):
        return key.lower() in {"headers", "authorization", "cookie", "cookie_header"}
    if len(value) <= 4096 and "%" in value:
        for _ in range(3):
            value = unquote(value)
    if "\\" in value:
        escaped = value.strip()
        if len(escaped) >= 2 and escaped[0] in {'"', "'"} and escaped[-1] == escaped[0]:
            escaped = escaped[1:-1]
        try:
            value = json.loads('"' + escaped + '"')
        except ValueError:
            pass
    value = value.strip().strip("\"'")
    if not value or value == "[REDACTED]" or value.endswith("()"):
        return False
    if value.startswith("[REDACTED]"):
        value = value[len("[REDACTED]") :]
    if value.lower() in {"none", "null", "true", "false"}:
        return False
    if re.fullmatch(
        r"[+-]?(?:[0-9]++(?:\.[0-9]*+)?|\.[0-9]++)(?:[eE][+-]?[0-9]++)?", value, re.ASCII
    ):
        normalized = key
        for _ in range(3):
            normalized = unquote(normalized)
        return not normalized.lower().replace("_", "").replace("-", "").endswith("session")
    if re.fullmatch(r"[A-Za-z0-9._~+/%=@:|?\-]+", value, re.ASCII):
        return True
    return (
        re.search(
            r"(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]{1,128}+=[^\s;]++|[A-Z0-9_]{8,}+",
            value,
            re.ASCII,
        )
        is not None
    )


def _cookie_values(text: str) -> bool:
    attributes = {"path", "domain", "expires", "max-age", "samesite"}
    header = text.lower().startswith(("cookie:", "set-cookie:"))
    for match in re.finditer(
        r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]{1,128}+)=([^;\r\n]++)", text, re.ASCII
    ):
        key, value = match.groups()
        if key.lower() not in attributes and (
            header
            or redaction_sensitive_value(key, value)
            or (not value.strip().isdecimal() and (len(value.strip()) >= 8 or " " in value))
        ):
            return True
    return False


def _encoded_separator(char: str, *, literal: bool = True) -> str:
    encoded = rf"%(?:25){{0,2}}{ord(char):02x}"
    return "(?:" + (re.escape(char) + "|" if literal else "") + encoded + ")"


def _url_redaction_spans(text: str) -> list[tuple[int, int]]:
    """Locate bounded names and separators in original bytes, without a dense map."""
    parameters = re.compile(
        r"(?:[?&#]|%(?:25){0,2}(?:3f|26|23))(?P<key>[A-Za-z0-9_.%\-]{1,384}?)"
        + _encoded_separator("="),
        re.ASCII | re.IGNORECASE,
    )
    ending = re.compile(r"%(?:25){0,2}(?:3f|26|23)|[\s&#\"'<>{}\]]", re.ASCII | re.IGNORECASE)
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
    spans = []
    position = 0
    while match := parameters.search(text, position):
        position = match.end()
        name = match["key"]
        decoded_name = name
        for _ in range(3):
            decoded_name = unquote(decoded_name)
        if not (redaction_sensitive_key(name) or decoded_name.lower() in signed):
            continue
        end_match = ending.search(text, match.end())
        if (
            end_match
            and text.startswith("[REDACTED]", match.end())
            and end_match.start() == match.end() + len("[REDACTED]") - 1
        ):
            end_match = ending.search(text, end_match.end())
        end = end_match.start() if end_match else len(text)
        value = text[match.end() : end]
        if value and redaction_sensitive_value(
            "token" if decoded_name.lower() in signed else name, value
        ):
            spans.append((match.end(), end))
            position = end
    return spans


def redact_text(text: str) -> tuple[str, list[dict[str, str | int]]]:
    """Collect intervals on original text, merge overlaps, and join exactly once."""
    spans: list[tuple[int, int, str]] = []

    def add(start: int, end: int, kind: str) -> None:
        if start < end and not (
            end - start == len("[REDACTED]") and text.startswith("[REDACTED]", start)
        ):
            spans.append((start, end, kind))

    quoted = r"""(?:"[^"\r\n]*+"|'[^'\r\n]*+')"""
    if '"' in text or "'" in text:
        fields = re.compile(
            r"""(?<!\\)(?P<q>["'])(?P<key>(?:[A-Za-z0-9_.%\-]|\\u[0-9a-fA-F]{4}|\\[^u\r\n]){1,128}+)"""
            r"""(?P=q)\s*:\s*(?P<vq>["'])(?P<value>(?:\\[^\r\n]|(?!(?P=vq))[^\\\r\n])*+)(?P=vq)""",
            re.ASCII,
        )
        for match in fields.finditer(text):
            key = match["key"]
            if "\\" in key:
                try:
                    key = json.loads('"' + key + '"')
                except ValueError:
                    continue
            if redaction_sensitive_value(key, match["value"]):
                kind = (
                    "local_path"
                    if key.lower().replace("_", "").replace("-", "") == "localpath"
                    else "sensitive_field"
                )
                add(*match.span("value"), kind)

    space = r"(?:[ \t]|%(?:25){0,2}(?:20|09))"
    if "%" in text:
        encoded_fields = re.compile(
            r"(?P<q>%(?:25){0,2}22)(?P<key>[A-Za-z0-9_.%\-]{1,384}?)(?P=q)"
            + space
            + "*+"
            + _encoded_separator(":")
            + space
            + "*+"
            + r"(?P<vq>%(?:25){0,2}22)(?P<value>(?:(?!(?P=vq))[^\r\n])*+)(?P=vq)",
            re.ASCII | re.IGNORECASE,
        )
        for match in encoded_fields.finditer(text):
            if redaction_sensitive_value(match["key"], match["value"]):
                add(*match.span("value"), "sensitive_field")

    bearer = re.compile(
        r"(?i)(?<![A-Za-z0-9_.-])bearer" + space + r"++(?P<token>[A-Za-z0-9._~+/%=\-]{8,}+)",
        re.ASCII,
    )
    for match in bearer.finditer(text):
        if match["token"].lower() not in {"preferred", "token", "tokens", "header"}:
            add(*match.span("token"), "authorization")

    userinfo = re.compile(
        r"https?"
        + _encoded_separator(":")
        + _encoded_separator("/") * 2
        + r"[A-Za-z0-9._~%+\-]{1,384}?"
        + _encoded_separator(":"),
        re.ASCII | re.IGNORECASE,
    )
    authority_end = re.compile(r"[\s/\"'<>{}]", re.ASCII)
    at = re.compile(_encoded_separator("@"), re.ASCII | re.IGNORECASE)
    position = 0
    while match := userinfo.search(text, position):
        boundary = authority_end.search(text, match.end())
        finish = boundary.start() if boundary else len(text)
        separator = at.search(text, match.end(), finish)
        position = separator.end() if separator else finish
        if separator and redaction_sensitive_value(
            "password", text[match.end() : separator.start()]
        ):
            add(match.end(), separator.start(), "url_token")

    # Atomic bodies preserve quoted cookie pairs and JSON closing delimiters.
    header_body = (
        r"(?:(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]{1,128}+=" + quoted + r"""|[^\r\n"'}\]])*+"""
    )
    headers = re.compile(
        r"(?im)(?<![A-Za-z0-9_.-])(?P<key>set-cookie|cookie|authorization)[ \t]*:[ \t]*"
        + header_body
        + r"(?:\r?\n[ \t]+"
        + header_body
        + r")*+",
        re.ASCII,
    )
    # Avoid scanning large plain text for absent header labels.
    if ":" in text:
        for match in headers.finditer(text):
            key = match["key"].lower()
            sensitive = (
                _cookie_values(match.group())
                if key != "authorization"
                else redaction_sensitive_value(key, match.group().split(":", 1)[1])
            )
            if sensitive:
                add(*match.span(), "authorization" if key == "authorization" else "cookie")

    value = quoted + r"""|(?:\[REDACTED\]|[^\s;,"'{}<>\[\]()`|])++"""
    # '=' cannot restart a chain. Failed a=a=a... scans its value only once.
    chain = re.compile(
        r"(?<![A-Za-z0-9_.%=/:-])[A-Za-z0-9_.-]{1,128}+=(?:" + value + r")"
        r"(?:;[ \t]*[A-Za-z0-9_.-]{1,128}+=(?:" + value + r"))++",
        re.ASCII,
    )
    if ";" in text:
        for match in chain.finditer(text):
            if _cookie_values(match.group()):
                add(*match.span(), "cookie")

    for start, end in _url_redaction_spans(text):
        add(start, end, "url_token")

    assignments = re.compile(
        r"(?i)(?<![A-Za-z0-9_.%?&#-])(?P<key>[A-Za-z_][A-Za-z0-9_.%\-]{0,383}?)"
        + space
        + "*+"
        + r"(?P<sep>"
        + _encoded_separator("=")
        + "|"
        + _encoded_separator(":")
        + ")"
        + space
        + "*+",
        re.ASCII,
    )
    values = re.compile("(?:" + value + ")", re.ASCII)
    # Credential spans already cover their contents. Scan assignments only outside
    # them, while still examining local-path spans for embedded credentials.
    ranges = []
    position = 0
    for start, end, kind in sorted(spans):
        if kind == "local_path":
            continue
        if start > position:
            ranges.append((position, start))
        position = max(position, end)
    if position < len(text):
        ranges.append((position, len(text)))
    if "=" in text or ":" in text or "%" in text:
        for begin, finish in ranges:
            for match in assignments.finditer(text, begin, finish):
                key = match["key"]
                if not redaction_sensitive_key(key):
                    continue
                if match["sep"] != "=" and key.lower() in {"authorization", "cookie", "set-cookie"}:
                    continue
                found = values.match(text, match.end(), finish)
                if (
                    found
                    and text[found.end() : found.end() + 1] != "("
                    and redaction_sensitive_value(key, found.group())
                ):
                    add(
                        match.start(),
                        found.end(),
                        "local_path"
                        if key.lower().replace("_", "") == "localpath"
                        else "session_cookie",
                    )

    if "sk-" in text:
        for match in re.finditer(
            r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{8,}+(?![A-Za-z0-9_-])", text, re.ASCII
        ):
            add(*match.span(), "api_key")

    roots = (
        r"/(?:tmp|var|etc|home|Users|opt|usr|proc|sys|dev|run|srv|mnt|media|root|private|"
        r"Library|Volumes|app|workspace)(?:/|\b)"
    )
    plausible = r"(?:" + roots + r"|/[A-Za-z0-9._-]++/|[A-Za-z]:[\\/]|\\\\)"
    if "/" in text or "\\" in text:
        public_paths = {"/docs/plan.md", "/etc/hosts"}
        if '"' in text or "'" in text:
            paths = re.compile(
                r"""(?P<q>["'])(?P<path>(?:/[^"'\r\n/]*+/|"""
                + plausible
                + r""")[^"'\r\n]*+)(?P=q)""",
                re.ASCII,
            )
            for match in paths.finditer(text):
                if match["path"] not in public_paths:
                    add(*match.span("path"), "local_path")
        paths = re.compile(
            r"(?<![A-Za-z0-9:/])" + plausible + r"""[^\s<>"'`,;{}\]\[)]*+""", re.ASCII
        )
        for match in paths.finditer(text):
            if match.group() not in public_paths:
                add(*match.span(), "local_path")

    if not spans:
        return text, []
    counts: dict[str, int] = {}
    merged: list[tuple[int, int]] = []
    covered_kind = None
    for start, end, kind in sorted(spans, key=lambda span: (span[0], -span[1])):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1] or (covered_kind == "local_path" and kind != "local_path"):
                counts[kind] = counts.get(kind, 0) + 1
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            if kind != "local_path":
                covered_kind = kind
        else:
            merged.append((start, end))
            covered_kind = kind
            counts[kind] = counts.get(kind, 0) + 1
    parts = []
    position = 0
    for start, end in merged:
        parts.extend((text[position:start], "[REDACTED]"))
        position = end
    parts.append(text[position:])
    return "".join(parts), [{"kind": kind, "count": n} for kind, n in sorted(counts.items())]
