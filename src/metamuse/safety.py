"""Local safety boundaries. Source and model content never issue permissions."""

from __future__ import annotations

import base64
import binascii
import errno
import ipaddress
import json
import os
import re
import socket
import sqlite3
import stat
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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

    def _directory(self) -> int:
        if ".." in self.root.parts:
            _secret_error()
        try:
            # Config overrides must not put secrets in a checkout.
            WorkspacePaths.resolve(self.root)
        except m.CoreError:
            _secret_error()
        fd = _open_directory(self.root, create=True)
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
        except m.CoreError:
            _secret_error()
        if not isinstance(auth_ref, str) or not _AUTH_REF.fullmatch(auth_ref):
            _secret_error()
        try:
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
        except (ValueError, KeyError, TypeError, binascii.Error):
            _secret_error()


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
        _scope_error("A permit is required", missing=True)
    row = conn.execute("SELECT * FROM permits WHERE id=?", (permit_id,)).fetchone()
    if row is None:
        _scope_error("Permit does not exist", missing=True)
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
        _scope_error()
    scope = _parse_scope(permit.kind, permit.scope)
    if isinstance(scope, AccessScope):
        if use.operation not in scope.operations or use.digest not in scope.target_digests:
            _scope_error()
    else:
        if use.operation != "muse.send" or use.packet_id is None:
            _scope_error()
        try:
            packet = store.get(m.Packet, use.packet_id, conn=conn)
            if packet.project_id != use.project_id or packet.stale:
                _scope_error()
            store.validate_evidence(use.project_id, packet.selection, conn=conn, source_only=True)
            if scope.snapshot is None:
                if packet.digest not in scope.packet_digests:
                    _scope_error()
            else:
                approved = {
                    m.canonical_json(ref.model_dump(mode="json"))
                    for ref in scope.snapshot.selection
                }
                if not packet.selection or any(
                    m.canonical_json(ref.model_dump(mode="json")) not in approved
                    for ref in packet.selection
                ):
                    _scope_error()
        except m.CoreError:
            _scope_error()
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
        _scope_error("Permit reservation requires the caller's transaction")
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
            _scope_error("Permit cannot be consumed")
        if changed != 1:
            _scope_error()
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


def redaction_sensitive_key(key: str) -> bool:
    """ASCII credential names only; normalize percent encoding and -/_ spelling."""
    import re
    from urllib.parse import unquote_plus

    decoded = unquote_plus(key)
    if re.fullmatch(r"[A-Za-z0-9_.-]+", decoded, flags=re.ASCII) is None:
        return False
    name = decoded.lower().replace("-", "").replace("_", "")
    return name in {
        "headers",
        "authorization",
        "cookie",
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
    } or decoded.lower().startswith(("x-amz-", "x-goog-", "__secure-", "__host-"))


def redact_text(text: str) -> tuple[str, list[dict[str, str | int]]]:
    """Syntax-aware best-effort guard, not a guarantee. Audits contain no values."""
    import re
    from urllib.parse import unquote_plus

    counts: dict[str, int] = {}

    def count(kind: str) -> None:
        counts[kind] = counts.get(kind, 0) + 1

    def replace(pattern: str, kind: str, replacement=None) -> None:
        nonlocal text

        def substitute(match):
            result = replacement(match) if replacement else "[REDACTED]"
            if result != match.group(0):
                count(kind)
            return result

        text = re.sub(pattern, substitute, text, flags=re.ASCII)

    # Replace JSON/string-literal values, preserving delimiters and surrounding syntax.
    def json_value(match):
        if redaction_sensitive_key(match["key"]):
            return (
                match["q"]
                + match["key"]
                + match["q"]
                + match["gap"]
                + match["vq"]
                + "[REDACTED]"
                + match["vq"]
            )
        return match.group(0)

    replace(
        r"""(?P<q>["'])(?P<key>[A-Za-z0-9_.%\-]+)(?P=q)(?P<gap>\s*:\s*)"""
        r"""(?P<vq>["'])(?P<value>(?:\\[^\r\n]|(?!(?P=vq))[^\\\r\n])*?)(?P=vq)""",
        "sensitive_field",
        json_value,
    )
    # Headers, including label prefixes, quoted cookie values and folded continuations.
    quoted = r"""(?:"[^"\r\n]*"|'[^'\r\n]*')"""
    header_body = r"(?:(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]+=" + quoted + r"""|[^\r\n"'}\]])*"""
    replace(
        r"(?im)(?<![A-Za-z0-9_.-])(?:set-cookie|cookie)[ \t]*:[ \t]*"
        + header_body
        + r"(?:\r?\n[ \t]+"
        + header_body
        + r")*",
        "cookie",
    )
    replace(
        r"(?i)(?<![A-Za-z0-9_.-])authorization[ \t]*:[ \t]*"
        r"(?:bearer|basic|token|digest|aws4-hmac-sha256)[ \t]+"
        + header_body
        + r"(?:\r?\n[ \t]+"
        + header_body
        + r")*",
        "authorization",
    )

    # Locate separators without swallowing outer redirect values, so encoded nested
    # query strings are examined too. Only secret values are replaced.
    parameters = re.compile(
        r"(?:[?&#]|%3f|%26|%23)"
        r"(?P<key>(?:(?!%3d|%26|%3f|%23)(?:[A-Za-z0-9_.-]|%[0-9a-f]{2}))+)"
        r"(?:=|%3d)",
        re.IGNORECASE | re.ASCII,
    )
    ending = re.compile(r"""[\s&#"'<>{}\]]|%26|%23|%3f""", re.IGNORECASE | re.ASCII)
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
    for match in parameters.finditer(text):
        if text.startswith("[REDACTED]", match.end()):
            continue
        name = unquote_plus(match["key"]).lower()
        if redaction_sensitive_key(name) or name in signed:
            end_match = ending.search(text, match.end())
            end = end_match.start() if end_match else len(text)
            spans.append((match.end(), end))
    for start, end in reversed(spans):
        if text[start:end] != "[REDACTED]":
            text = text[:start] + "[REDACTED]" + text[end:]
            count("url_token")

    value = quoted + r"""|[^\s;,"'{}\]]+"""

    def session(match):
        if redaction_sensitive_key(match["key"]):
            return "[REDACTED]"
        return match.group(0)

    replace(
        r"(?i)(?<![A-Za-z0-9_.%?&#-])(?P<key>[A-Za-z_][A-Za-z0-9_.%\-]*)[ \t]*=[ \t]*(?:"
        + value
        + ")",
        "session_cookie",
        session,
    )
    replace(
        r"(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]+=(?:" + value + ")"
        r"(?:;[ \t]*[A-Za-z0-9_.-]+=(?:" + value + "))+",
        "cookie",
    )
    replace(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])", "api_key")

    roots = (
        r"/(?:tmp|var|etc|home|Users|opt|usr|proc|sys|dev|run|srv|mnt|media|root|private|"
        r"Library|Volumes|app|workspace)(?:/|\b)"
    )
    plausible = r"(?:" + roots + r"|/[A-Za-z0-9._-]+/[A-Za-z0-9._/-]+|[A-Za-z]:[\\/]|\\\\)"
    replace(
        r"""(?P<q>["'])(?P<path>(?:/[^"'\r\n]+/[^"'\r\n]+|"""
        + plausible
        + r"""[^"'\r\n]*))(?P=q)""",
        "local_path",
        lambda match: match["q"] + "[REDACTED]" + match["q"],
    )
    replace(
        r"(?<![A-Za-z0-9:/])" + plausible + r"""[^\s<>"'`,;{}\]\[)]+""",
        "local_path",
    )
    return text, [{"kind": kind, "count": n} for kind, n in sorted(counts.items())]
