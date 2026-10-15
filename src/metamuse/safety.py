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
            except m.CoreError as refusal:
                return m.failure(refusal)
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


# --- Redaction (B07/B11/B16a) ---

_REDACTED = "[REDACTED]"
_NAME = r"[A-Za-z_][A-Za-z0-9_.-]{0,127}+"


@lru_cache(maxsize=2048)
def _credential_name(key: str) -> str:
    for _ in range(3):
        key = unquote(key)
    return key.lower().replace("_", "").replace("-", "")


@lru_cache(maxsize=2048)
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


def _credential_value_exempt(key: str, value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return True
    leaf = _credential_name(key).rsplit(".", 1)[-1]
    if not isinstance(value, str):
        return isinstance(value, (int, float)) and leaf.endswith("session")
    for _ in range(3):
        value = unquote(value)
    value = value.strip()
    if value.startswith(('"""', "'''")) and value.endswith(value[:3]) and len(value) >= 6:
        value = value[3:-3].strip()
    elif len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        value = value[1:-1].strip()
    if not value or value.lower() in {"null", "none", "nil", "true", "false"}:
        return True
    if value in {_REDACTED, "REDACTED", "...", "…", "%s", "{}"}:
        return True
    if re.fullmatch(
        r"(?:\*{3,}+|[xX]{3,}+|<[^<>]*+>|\$\{[^{}]*+\}|\$[A-Za-z_][A-Za-z0-9_]*+|%[A-Za-z_][A-Za-z0-9_]*+%|\{\{[^{}]*+\}\}|\{[A-Za-z_][A-Za-z0-9_]*+\})",
        value,
    ):
        return True
    if value.startswith("${") and value.endswith("}"):
        balance = 0
        for index, char in enumerate(value[1:], 1):
            if char == "{":
                balance += 1
            elif char == "}":
                balance -= 1
                if balance == 0:
                    return index == len(value) - 1
        return False
    reference = value.removesuffix(";")
    if _type_reference(reference):
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*+", reference):
        if reference.rsplit(".", 1)[-1].lower().replace("_", "") == leaf or reference.startswith(
            "process.env."
        ):
            return True
    if re.fullmatch(r"os\.(?:environ\[[^\r\n\]]*+\]|getenv\([^\r\n)]*+\))", value):
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


def _decode_view(text: str, *, json_escapes: bool = False, form: bool = False, ranges=None):
    """One canonicalization pass; allocate offsets only where bytes actually change."""
    pattern = re.compile(
        r"\+|\\{1,8}u002[bB]"
        if form
        else r'\\(?:u[0-9a-fA-F]{4}|["\\/bfnrt])'
        if json_escapes
        else r"(?:%[0-9a-fA-F]{2})++"
    )
    edits = []
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
    index = 0
    shift = 0
    for start, end, kind, replacement in spans:
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


class _Syntax:
    """Compute quote and balanced-value terminators once, never from every key."""

    def __init__(self, text, depths=None):
        self.depths = depths
        self.max_depth = max(depths, default=0) if depths is not None else 0
        self.form = bool(
            "&" in text and re.fullmatch(_NAME + r"=[^&\s]*+(?:&" + _NAME + r"=[^&\s]*+)*+", text)
        )
        self.ends = {}
        self.closings = {}
        self.quote_closings = {}
        self.separator_context = {}
        self.quotes = []
        self.line_ends = [m.start() for m in re.finditer(r"[\r\n]", text)]
        quote_ends = {}
        previous_quotes = {}
        stack = []
        quote = None
        opening = None
        for match in re.finditer(r"\\[\s\S]|[\"'()\[\]{},;]", text):
            char = match.group()
            pos = match.start()
            if char.startswith("\\"):
                continue
            if char in "\"'":
                level = self.depth(pos)
                for pending in range(level, self.max_depth + 1):
                    previous = previous_quotes.pop((char, pending), None)
                    if previous is not None:
                        quote_ends[previous] = pos + 1
                previous_quotes[char, level] = pos
            if quote:
                if char == quote and self.depth(pos) <= self.depth(opening):
                    self.ends[opening] = pos + 1
                    self.quotes.append((opening, pos))
                    self.quote_closings[pos] = opening
                    quote = opening = None
                continue
            if char in "\"'":
                quote, opening = char, pos
            elif char in ",;":
                if stack:
                    self.separator_context[pos] = stack[-1]
            elif char in "([{":
                stack.append((char, pos))
            elif (
                stack
                and char == {"(": ")", "[": "]", "{": "}"}[stack[-1][0]]
                and self.depth(pos) <= self.depth(stack[-1][1])
            ):
                _, begin = stack.pop()
                self.ends[begin] = pos + 1
                self.closings[pos] = begin
        if opening is not None:
            self.ends[opening] = len(text)
            self.quotes.append((opening, len(text)))
        for _, begin in stack:
            self.ends[begin] = len(text)
        for previous in previous_quotes.values():
            quote_ends[previous] = len(text)
        self.ends.update(quote_ends)
        triple = None
        for match in re.finditer(r"\\[\s\S]|" + '"' * 3 + "|" + "'" * 3, text):
            delimiter = match[0]
            if delimiter.startswith("\\"):
                continue
            if triple is None:
                triple = (delimiter, match.start())
            elif delimiter == triple[0] and self.depth(match.start()) <= self.depth(triple[1]):
                self.ends[triple[1]] = match.end()
                triple = None
        if triple is not None:
            self.ends[triple[1]] = len(text)
        # Indented YAML containers/scalars end at the next dedent. Close every
        # pending block once, using the previous content end computed once/line.
        self.yaml_ends = {}
        self.yaml_scalars = {}
        blocks = []
        content_end = 0
        header = re.compile(r"(?:[\"']?" + _NAME + r"[\"']?)\s*+:[^\S\r\n]*+(?:[|>][-+]?)?")
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
            block_header = re.search(
                _NAME + r"\s*+:[^\S\r\n]*+[|>][-+]?[^\S\r\n]*+$", stripped
            ) or header.fullmatch(stripped)
            if block_header and block_header.end() == len(stripped):
                colon = line.start() + indent + stripped.rfind(":", block_header.start())
                blocks.append((indent, colon))
            content_end = line.start() + len(body.rstrip())
        for _, colon in blocks:
            self.yaml_ends[colon] = content_end

    def depth(self, position):
        return (
            self.depths[position] if self.depths is not None and position < len(self.depths) else 0
        )

    def layer(self, start, end):
        return max(self.depths[start:end], default=0) if self.depths is not None else 0

    def value(self, text, start, *, list_separators=False, operator_depth=None):
        if start >= len(text):
            return start
        level = self.depth(start) if operator_depth is None else operator_depth
        quoted_end = (
            self.ends.get(start, len(text))
            if text[start] in "\"'" and self.depth(start) <= level
            else start
        )
        if quoted_end > start and (
            quoted_end == len(text) or text[quoted_end].isspace() or text[quoted_end] in ",;&)]}"
        ):
            return quoted_end
        end = (
            start + len(_REDACTED)
            if text.startswith(_REDACTED, start)
            else self.ends.get(start, len(text))
            if text[start] in "[{"
            else quoted_end
        )
        chunk = re.compile(r"[^\s,;&\"'{}\[\]()]++")
        separator = re.compile(r"[^\S\r\n]*+" + _NAME + r"[^\S\r\n]*+=")
        while end < len(text):
            found = chunk.match(text, end)
            if found:
                end = found.end()
            if end < len(text) and self.depth(end) > level:
                end += 1
                continue  # A deeper decoded delimiter is value data.
            if end >= len(text) or text[end].isspace():
                break
            if text[end] in "\"'" and self.quote_closings.get(end, end) < start:
                break
            if text[end] in ",;&":
                context = self.separator_context.get(end)
                if (
                    list_separators
                    or (text[end] == "," and context and context[0] in "({[" and context[1] < start)
                    or (text[end] in ";&" and self.form and level > 0)
                    or (text[end] == ";" and separator.match(text, end + 1))
                ):
                    break
                end += 1
            elif text[end] in "([{":
                end = self.ends.get(end, len(text))
            elif self.closings.get(end, end) < start:
                break
            else:
                end += 1
        return end


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
        r"(?<![A-Za-z0-9_.%?&#-])(?P<flag>--|-D|-)?(?P<key>" + _NAME + r"))"
        r"(?:\s*+(?P<sep>=>|=|:(?!=)|[：＝])\s*+|(?P<flagspace>\s++))",
    )
    position = line_index = 0
    while match := pattern.search(text, position):
        position = match.end()
        key = match["key"] or match["quotedkey"]
        # Space alone is assignment syntax only on a command-line flag.
        if match["flagspace"] and not match["flag"]:
            continue
        if not redaction_sensitive_key(key):
            continue
        if skip_paths and _credential_name(key) == "localpath":
            continue
        start = match.end()
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
            max(start, yaml_end)
            if yaml_end is not None
            else max(start, scalar_end)
            if scalar_end is not None
            else syntax.value(text, start, operator_depth=operator_depth)
        )
        quoted = (
            yaml_end is None
            and text[start : start + 1] in {'"', "'"}
            and syntax.depth(start) <= operator_depth
        )
        if not quoted and any(c in text[start:end] for c in "\"'"):
            while line_index < len(syntax.line_ends) and syntax.line_ends[line_index] < start:
                line_index += 1
            end = max(
                end,
                syntax.line_ends[line_index] if line_index < len(syntax.line_ends) else len(text),
            )
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
        value = text[start:end]
        closed_quote = quoted and len(value) > 1 and value[-1] == value[0]
        position = max(position, end - int(closed_quote))
        if include_exempt or redaction_sensitive_value(key, value):
            kind = (
                "local_path"
                if _credential_name(key) == "localpath"
                else "sensitive_field"
                if match["q"]
                else "session_cookie"
            )
            replacement = _REDACTED
            if quoted:
                closed = len(value) > 1 and value[-1] == value[0]
                width = 3 if value.startswith(('"""', "'''")) else 1
                start += width
                end -= width if closed else 0
            elif match["q"]:
                replacement = '"' + _REDACTED + '"'
            yield start, end, kind, replacement
            if kind == "local_path" and not include_exempt:
                # A local_path label must not hide credential syntax in its value.
                nested = _assignment_spans(value, _Syntax(value), skip_paths=True)
                offset = match.end()
                for inner_start, inner_end, inner_kind, inner_replacement in nested:
                    yield offset + inner_start, offset + inner_end, inner_kind, inner_replacement


def _header_spans(text, syntax):
    pattern = re.compile(r"(?i)(?<![A-Za-z0-9_.-])(?P<key>" + _NAME + r")\s*(?P<sep>=|[:：＝])\s*+")
    line_end = re.compile(r"[\r\n]")
    fold = re.compile(r"\r?\n\s+")
    position = quote_index = 0
    while match := pattern.search(text, position):
        position = match.end()
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
        operator_depth = syntax.depth(match.start("sep"))
        while quote_index < len(syntax.quotes) and syntax.quotes[quote_index][1] < match.start():
            quote_index += 1
        limit = len(text)
        if quote_index < len(syntax.quotes):
            begin, finish = syntax.quotes[quote_index]
            if begin < match.start() < finish:
                limit = finish

        def boundary_after(start, limit=limit, operator_depth=operator_depth):
            boundary = line_end.search(text, start, limit)
            while boundary and syntax.depth(boundary.start()) > operator_depth:
                boundary = line_end.search(text, boundary.end(), limit)
            return boundary

        boundary = boundary_after(start)
        end = boundary.start() if boundary else limit
        while folded := fold.match(text, end, limit):
            boundary = boundary_after(folded.end())
            end = boundary.start() if boundary else limit
        if text.startswith(_REDACTED, start) and text[
            start + len(_REDACTED) : start + len(_REDACTED) + 1
        ] in {'"', "'"}:
            end = start + len(_REDACTED)
        position = max(end, match.end())
        value = text[start:end].strip()
        cookie = name not in {"authorization", "proxyauthorization"}
        authorized = cookie or value.lower() not in {
            "bearer",
            "basic",
            "digest",
            "token",
            "negotiate",
            "ntlm",
            "aws4-hmac-sha256",
        }
        if authorized and redaction_sensitive_value(match["key"], value):
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
    ending = re.compile(r"%(?:25){0,2}(?:3f|26|23)|[\s&#\"'\[\]{}()]|\?", re.I)
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
    position = 0
    while match := pattern.search(text, position):
        position = match.end()
        key = match["key"]
        if not (redaction_sensitive_key(key) or key.lower() in signed):
            continue
        start = match.end()
        operator_depth = syntax.depth(match.start("key") + len(key))
        cursor = start + len(_REDACTED) if text.startswith(_REDACTED, start) else start
        end = len(text)
        while boundary := ending.search(text, cursor):
            encoded = encoded_positions.contains(boundary.start())
            if (
                (boundary.group() == "?" and not encoded)
                or (boundary.group() == "?" and encoded_positions.form)
                or (boundary.group() not in {"?", "&", "#"} and encoded)
                or (
                    syntax.depth(boundary.start()) > operator_depth
                    and boundary.group() in {"&", "#"}
                )
                or (
                    boundary.group() in "\"'[]{}()"
                    and syntax.closings.get(
                        boundary.start(),
                        syntax.quote_closings.get(boundary.start(), boundary.start()),
                    )
                    >= match.start()
                )
            ):
                cursor = boundary.end()
                continue
            end = boundary.start()
            break
        position = max(position, end)
        if redaction_sensitive_value("token" if key.lower() in signed else key, text[start:end]):
            yield start, end, "url_token", _REDACTED


def _userinfo_spans(text, syntax):
    scheme = re.compile(r"(?i)(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*+://")

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
            if syntax.depth(boundary.start()) <= level and (boundary[0] != "\\" or special):
                yield boundary.start()

    ends = {}
    ats = Positions(m.start() for m in re.finditer("@", text))
    position = 0
    while match := scheme.search(text, position):
        position = match.end()
        level = syntax.depth(match.end() - 1)
        special = match[0].split(":", 1)[0].lower() in {"http", "https", "ftp", "file", "ws", "wss"}
        key = (level, special)
        if key not in ends:
            ends[key] = Positions(boundaries(level, special))
        end = ends[key].first(position)
        first_at = ats.first(position)
        if first_at >= end:
            position = end
            continue
        colon = text.find(":", position, first_at)
        if colon < 0:
            position = end
            continue
        at = first_at
        while ats.first(at + 1) < end:
            at = ats.current
        position = end
        if redaction_sensitive_value("password", text[colon + 1 : at]):
            yield colon + 1, at, "url_token", _REDACTED


def _cookie_spans(text, syntax):
    pair = re.compile(r"(?<![A-Za-z0-9_.%=/?:&#-])(?P<key>" + _NAME + r")=[^\S\r\n]*+")
    position = 0
    while match := pair.search(text, position):
        begin = match.start()
        end = syntax.value(
            text, match.end(), list_separators=True, operator_depth=syntax.depth(match.end() - 1)
        )
        sensitive = redaction_sensitive_value(match["key"], text[match.end() : end])
        generic = not _credential_value_exempt(match["key"], text[match.end() : end]) and bool(
            text[match.end() : end].strip("\"'")
        )
        members = 1
        position = max(end, match.end())
        while separator := re.compile(r";[^\S\r\n]*+(?=" + _NAME + r"=)").match(text, position):
            context = syntax.separator_context.get(position)
            if context and context[0] == "(" and context[1] < begin:
                break  # Call arguments and signatures never form cookie chains.
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
            sensitive |= redaction_sensitive_value(next_pair["key"], text[next_pair.end() : finish])
            generic |= not _credential_value_exempt(
                next_pair["key"], text[next_pair.end() : finish]
            ) and bool(text[next_pair.end() : finish].strip("\"'"))
            end = position = max(finish, next_pair.end())
            members += 1
        if members > 1 and (sensitive or generic):
            yield begin, end, "cookie", _REDACTED


def _jar_spans(text):
    pattern = re.compile(
        r"(?m)^(?:#HttpOnly_)?[^\t\r\n]++\t(?:TRUE|FALSE)\t[^\t\r\n]*+\t(?:TRUE|FALSE)\t[0-9]++\t[^\t\r\n]++\t(?P<value>[^\r\n]*+)$"
    )
    for match in pattern.finditer(text):
        if redaction_sensitive_value("cookie", match["value"]):
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
    label_pattern = r"(?P<label>(?:[A-Z0-9]+ ){0,6}PRIVATE KEY(?: BLOCK)?)"
    begin = re.compile(r"-----BEGIN " + label_pattern + r"-----", re.I)
    end_lines = {}
    for match in re.finditer(r"-----END " + label_pattern + r"-----", text, re.I):
        end_lines.setdefault(match["label"].upper(), []).append(match)
    cursors = {}
    position = 0
    while match := begin.search(text, position):
        level = syntax.layer(match.start(), match.end())
        label = match["label"].upper()
        ends = end_lines.get(label, [])
        index = cursors.get(label, 0)
        while index < len(ends) and (
            ends[index].start() < match.end()
            or syntax.layer(ends[index].start(), ends[index].end()) > level
        ):
            index += 1
        end = ends[index].end() if index < len(ends) else len(text)
        cursors[label] = index + 1
        yield match.start(), end, "private_key", _REDACTED
        position = end


def _xml_spans(text, syntax):
    # Index closing tags once; repeated unterminated starts cannot rescan a suffix.
    closings = {}
    for match in re.finditer(r"</(?P<key>(?:" + _NAME + r":)?" + _NAME + r")[^\S\r\n]*+>", text):
        closings.setdefault(match["key"].lower(), []).append(match)
    cursors = {}
    position = 0
    pattern = re.compile(r"<(?P<key>(?:" + _NAME + r":)?" + _NAME + r")(?=[\s>])")
    while match := pattern.search(text, position):
        position = match.end()
        key = match["key"]
        if not redaction_sensitive_key(key.rsplit(":", 1)[-1]):
            continue
        value_start = match.end()
        while value_start < len(text) and text[value_start] != ">":
            if text[value_start] in {'"', "'"}:
                value_start = syntax.ends.get(value_start, len(text))
            else:
                value_start += 1
        if value_start == len(text):
            position = value_start
            continue
        if text[value_start - 1 : value_start] == "/":
            position = value_start + 1
            continue
        value_start += 1
        level = max(syntax.layer(match.start(), match.end()), syntax.depth(value_start - 1))
        ends = closings.get(key.lower(), [])
        index = cursors.get(key.lower(), 0)
        while index < len(ends) and (
            ends[index].start() < match.end()
            or syntax.layer(ends[index].start(), ends[index].end()) > level
        ):
            index += 1
        end = ends[index].start() if index < len(ends) else len(text)
        cursors[key.lower()] = index + 1
        position = ends[index].end() if index < len(ends) else end
        value = text[value_start:end]
        if value.startswith("<![CDATA[") and value.endswith("]]>"):
            value = value[9:-3]
        if redaction_sensitive_value(key.rsplit(":", 1)[-1], value):
            yield value_start, end, "sensitive_field", _REDACTED


def _curl_spans(text, syntax):
    position = 0
    pattern = re.compile(
        r"(?<![A-Za-z0-9_-])(?:(?P<long>--user|--cookie)(?:=|[^\S\r\n]++)|"
        r"(?P<short>-[ub])[^\S\r\n]*+)"
    )
    while match := pattern.search(text, position):
        start = match.end()
        if syntax.quote_closings.get(start, start) < match.start():
            start += 1  # A quoted flag ends before its separate argument.
            if start >= len(text) or not text[start].isspace():
                position = start
                continue
            while start < len(text) and text[start].isspace():
                start += 1
        end = syntax.value(text, start, operator_depth=syntax.depth(match.start()))
        position = max(end, match.end())
        if (match["long"] or match["short"]) in {"-b", "--cookie"}:
            if redaction_sensitive_value("cookie", text[start:end]):
                quoted = text[start : start + 1] in {'"', "'"}
                yield (
                    start + int(quoted),
                    end - int(quoted and text[end - 1 : end] == text[start]),
                    "cookie",
                    _REDACTED,
                )
            continue
        colon = text.find(":", start, end)
        if colon >= 0 and redaction_sensitive_value("password", text[colon + 1 : end]):
            if text[start : start + 1] in "\"'" and text[end - 1 : end] == text[start]:
                end -= 1
            yield colon + 1, end, "sensitive_field", _REDACTED


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
                and _REDACTED not in value
                and not any(char in value for char in "?&=")
                and not text.startswith(_REDACTED, match.end())
            ):
                yield *(match.span(group) if group else match.span()), "local_path", _REDACTED

    yield from _merge_spans([matches(quoted, "path"), matches(bare, None)])


def _har_spans(text, syntax):
    """Index HAR fields once in object order, including arbitrary cookie names."""
    objects = []
    stack = []
    token = re.compile(r'"(?:\\[^\r\n]|[^"\\])*+"|[{}\[\]]')
    colon = re.compile(r"\s*+:\s*+")
    for match in token.finditer(text):
        char = match[0]
        if char == "{":
            record = {"private": False, "value": None}
            objects.append(record)
            stack.append(record)
        elif char == "[":
            stack.append(None)
        elif char in {"}", "]"}:
            if stack:
                stack.pop()
        elif stack and stack[-1] is not None and char in {'"name"', '"value"', '"cookies"'}:
            separator = colon.match(text, match.end())
            if not separator:
                continue
            start = separator.end()
            end = syntax.value(text, start)
            record = stack[-1]
            if char == '"name"':
                name = text[start:end].strip('"')
                record["private"] = redaction_sensitive_key(name)
            elif char == '"value"':
                record["value"] = (start, end)
            elif text[start : start + 1] == "[" and text[start:end].strip() != "[]":
                record["cookies"] = (start, end)
    # Fields can occur in any order, including before their parent's cookies.
    # Revisit the token stream once to emit spans in order, without sorting or
    # restarting inside any value.
    selected = {}
    for record in objects:
        if record.get("cookies"):
            start, end = record["cookies"]
            selected[start] = (start, end, "cookie", '"' + _REDACTED + '"')
        if record["private"] and record["value"]:
            start, end = record["value"]
            if redaction_sensitive_value("password", text[start:end]):
                quoted = text[start : start + 1] == '"'
                selected[start] = (
                    start + int(quoted),
                    end - int(quoted and text[end - 1 : end] == '"'),
                    "sensitive_field",
                    _REDACTED if quoted else '"' + _REDACTED + '"',
                )
    for match in token.finditer(text):
        if match.start() in selected:
            yield selected[match.start()]


def _command_spans(text, syntax):
    pattern = re.compile(
        r"(?i)(?P<mysql>\b(?:mysql|mariadb|mysqldump)\b)|"
        r"(?P<attached>\s-p(?=\S))|(?P<boundary>[\r\n;&|])|"
        r"\b(?:IDENTIFIED\s++BY|WITH\s++PASSWORD|sshpass\s++-p)\s++|"
        r"(?P<putty>(?m:^PuTTY-User-Key-File-[0-9]++:))"
    )
    position = 0
    mysql = False
    mysql_depth = 0
    while match := pattern.search(text, position):
        position = match.end()
        if match["putty"]:
            yield match.start(), len(text), "private_key", _REDACTED
            return
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
        start = match.end()
        end = syntax.value(text, start, operator_depth=syntax.depth(match.start()))
        position = max(match.end(), end)
        if redaction_sensitive_value("password", text[start:end]):
            quoted = text[start : start + 1] in {'"', "'"}
            yield (
                start + int(quoted),
                end - int(quoted and text[end - 1 : end] == text[start]),
                "sensitive_field",
                _REDACTED,
            )


def _view_spans(text, encoded_positions, syntax):
    return _merge_spans(
        [
            _assignment_spans(text, syntax),
            _header_spans(text, syntax),
            _query_spans(text, syntax, encoded_positions),
            _userinfo_spans(text, syntax),
            _cookie_spans(text, syntax),
            _jar_spans(text),
            _bearer_spans(text),
            _prefix_spans(text),
            _jwt_spans(text),
            _pem_spans(text, syntax),
            _xml_spans(text, syntax),
            _curl_spans(text, syntax),
            _har_spans(text, syntax),
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


def _form_ranges(text):
    """Only a complete form body or an actual URL query gives '+' space syntax.

    Packet records and pasted captures contain independent form-body lines. A
    prose line with whitespace is never a form body, regardless of earlier '?'.
    """
    form = re.compile(_NAME + r"=[^&\s]*+(?:&" + _NAME + r"=[^&\s]*+)*+")
    encoded_form = re.compile(_NAME + r"(?:\+|\\{1,8}u002[bB])*+%(?:25){0,2}3[dD][^\s]*+")

    def bodies():
        for line in re.finditer(r"[^\r\n]++", text):
            label = re.match(r"(?i)body:[^\S\r\n]*+", line[0])
            begin = label.end() if label else 0
            if form.fullmatch(line[0][begin:]) or encoded_form.fullmatch(line[0][begin:]):
                yield line.start() + begin, line.end(), "form", ""

    def quoted_bodies():
        opening = None
        for match in re.finditer(r"\\[\s\S]|[\"']", text):
            if match[0].startswith("\\"):
                continue
            if opening is None:
                opening = (match[0], match.start())
            elif match[0] == opening[0]:
                start = opening[1] + 1
                value = text[start : match.start()]
                if form.fullmatch(value) or encoded_form.fullmatch(value):
                    yield start, match.start(), "form", ""
                opening = None

    def queries():
        for match in re.finditer(
            r"(?i)(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*+://[^\s\"'<>]++", text
        ):
            question = text.find("?", match.start(), match.end())
            fragment = text.find("#", match.start(), match.end())
            if question >= 0 and (fragment < 0 or question < fragment):
                yield question + 1, fragment if fragment >= 0 else match.end(), "form", ""

    for start, end, _, _ in _merge_spans([bodies(), quoted_bodies(), queries()]):
        yield start, end


def _redact_once(text: str) -> tuple[str, list[dict[str, str | int]]]:
    """Canonical decoded views, forward scans, sparse offsets, one output join."""
    layers = []
    view = text
    ranges = list(_form_ranges(text))
    form = bool(ranges) and ranges[0] == (0, len(text))
    if "+" in text or re.search(r"\\{1,8}u002b", text, re.I):
        # Decode before percent escapes: an encoded %2B remains a literal plus.
        view, edits = _decode_view(text, form=True, ranges=ranges)
        if edits:
            layers.append(edits)
    # Escaped documents can reveal percent escapes, and conversely. Each
    # successful layer consumes its own budget; failed probes do not consume it.
    budgets = {False: 3, True: 3}
    while True:
        changed = False
        for decoding in (False, True):
            if not budgets[decoding]:
                continue
            decoded, edits = _decode_view(view, json_escapes=decoding)
            if edits:
                budgets[decoding] -= 1
                view = decoded
                layers.append(edits)
                changed = True
        if not changed:
            break
    encoded = _EncodedPositions(text, layers, form=form)
    syntax = _Syntax(view, _decoding_depths(len(text), layers))
    spans = list(
        _merge_spans(
            [
                _close_bearer_spans(
                    view, _close_assignment_spans(view, _view_spans(view, encoded, syntax), syntax)
                )
            ]
        )
    )
    for edits in reversed(layers):
        spans = list(_original_spans(spans, edits))
    # Original quoted/structured values have their original quote terminators,
    # even when JSON unescaping reveals quotes inside the credential itself.
    original_syntax = _Syntax(text) if layers else None
    original_fields = _assignment_spans(text, original_syntax) if layers else ()
    original_headers = _header_spans(text, original_syntax) if layers else ()
    spans = list(_merge_spans([spans, original_fields, original_headers, _bearer_spans(text)]))
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


def redact_text(text: str) -> tuple[str, list[dict[str, str | int]]]:
    """Three changing applications and one confirming application, at most."""
    counts = {}
    for _ in range(4):
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
