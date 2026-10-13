"""Safety feature seam. Local file reads live in the marked section below."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from metamuse import models as m

if TYPE_CHECKING:
    from metamuse.app import AppContext
    from metamuse.jobs import Operation


IMPLEMENTED_TOOLS: frozenset[str] = frozenset()


def register_operations(registry: dict[str, Operation] | None = None) -> None:
    """Register this module's operations idempotently; none are implemented yet."""


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
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return Path(os.path.normpath(os.fspath(candidate)))


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
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")


def create_permit(ctx: AppContext, request: m.PermitCreateRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")
