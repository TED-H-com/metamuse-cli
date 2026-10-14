"""Copy the packaged Skill into a directory the operator names."""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path
from typing import Any

from metamuse import models as m

_SKILL_PARTS = ("skills", "metamuse")


def packaged_skill_files() -> tuple[tuple[str, bytes], ...]:
    """Return the wheel's Skill files, or the canonical tree in a source checkout."""
    root = _skill_root()
    found: list[tuple[str, bytes]] = []
    _collect(root, "", found)
    if not found or not any(name == "SKILL.md" for name, _ in found):
        raise m.CoreError(m.ErrorCode.STORAGE, "Packaged Skill is not available")
    return tuple(sorted(found))


def export_skill(dest: str | Path, *, force: bool = False) -> m.Envelope:
    """Write the packaged Skill. Refuse to replace existing files unless forced."""
    payload = packaged_skill_files()
    target = _prepare_dest(Path(dest))
    relatives = [_relative(name) for name, _ in payload]
    planned = [target / relative for relative in relatives]
    if any(os.path.lexists(path) for path in planned) and not force:
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID,
            "Skill export would overwrite existing files",
            next_action="Choose an empty directory or pass --force",
        )
    for relative, data in zip(relatives, (item[1] for item in payload), strict=True):
        _write_file(target / relative, data, force=force)
    return m.Envelope(
        status="ok",
        data={"files": [relative.as_posix() for relative in relatives], "file_count": len(payload)},
    )


def _skill_root() -> Any:
    packaged = files("metamuse.data").joinpath(*_SKILL_PARTS)
    if packaged.joinpath("SKILL.md").is_file():
        return packaged
    from metamuse import __file__ as package_file

    root = Path(package_file).resolve().parents[2]
    candidate = root.joinpath(*_SKILL_PARTS)
    project = root / "pyproject.toml"
    if candidate.joinpath("SKILL.md").is_file() and project.is_file():
        text = project.read_text(encoding="utf-8")
        if 'name = "metamuse-cli"' in text:
            return candidate
    raise m.CoreError(m.ErrorCode.STORAGE, "Packaged Skill is not available")


def _collect(node: Any, prefix: str, found: list[tuple[str, bytes]]) -> None:
    children = list(node.iterdir())
    children.sort(key=lambda child: child.name)
    for child in children:
        name = child.name
        if name == "__pycache__" or name.endswith(".pyc"):
            continue
        relative = name if not prefix else f"{prefix}/{name}"
        if child.is_file():
            found.append((relative, child.read_bytes()))
        elif child.is_dir():
            _collect(child, relative, found)


def _prepare_dest(dest: Path) -> Path:
    if dest.is_symlink():
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID, "Skill export destination must not be a symbolic link"
        )
    try:
        dest.mkdir(mode=0o755, exist_ok=True)
    except OSError:
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID, "Skill export destination cannot be created"
        ) from None
    if dest.is_symlink() or not dest.is_dir():
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Skill export destination is not a directory")
    return dest


def _relative(name: str) -> Path:
    if not name or name.startswith(("/", "\\")) or "\\" in name or "\x00" in name:
        raise m.CoreError(m.ErrorCode.STORAGE, "Packaged Skill path is not relative")
    parts = Path(name).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise m.CoreError(m.ErrorCode.STORAGE, "Packaged Skill path is not relative")
    return Path(*parts)


def _storage_error() -> m.CoreError:
    return m.CoreError(m.ErrorCode.STORAGE, "Skill export could not write the destination")


def _write_file(path: Path, data: bytes, *, force: bool) -> None:
    parent = path.parent
    if parent.is_symlink():
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Skill export path contains a symbolic link")
    try:
        parent.mkdir(mode=0o755, exist_ok=True)
    except OSError:
        raise _storage_error() from None
    if path.is_symlink():
        if not force:
            raise m.CoreError(
                m.ErrorCode.INPUT_INVALID,
                "Skill export would overwrite existing files",
                next_action="Choose an empty directory or pass --force",
            )
        try:
            path.unlink()
        except OSError:
            raise _storage_error() from None
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    flags |= os.O_TRUNC if force else os.O_EXCL
    try:
        fd = os.open(path, flags, 0o644)
    except OSError:
        raise _storage_error() from None
    try:
        os.write(fd, data)
        os.fsync(fd)
    except OSError:
        raise _storage_error() from None
    finally:
        os.close(fd)
    try:
        path.chmod(0o644)
    except OSError:
        raise _storage_error() from None
