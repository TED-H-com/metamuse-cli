"""Private workspace paths; no runtime files belong in a checkout."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

from metamuse.models import CoreError, ErrorCode


def private_dir(path: Path) -> Path:
    if not path.parent.exists():
        private_dir(path.parent)
    path.mkdir(exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def private_file(path: Path) -> None:
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    os.close(fd)
    path.chmod(0o600)


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path

    @classmethod
    def resolve(cls, workspace: str | Path | None = None) -> WorkspacePaths:
        root = (
            Path(workspace).expanduser()
            if workspace
            else user_data_path("metamuse", appauthor=False) / "default"
        ).resolve()
        if any(
            (parent / ".git").exists() and (parent / "src/metamuse").is_dir()
            for parent in (root, *root.parents)
        ):
            raise CoreError(ErrorCode.INPUT_INVALID, "Workspace must be outside a git checkout")
        return cls(root)

    @property
    def database(self) -> Path:
        return self.root / "state.sqlite3"

    @property
    def objects(self) -> Path:
        return self.root / "objects" / "sha256"

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    @property
    def worker_lock(self) -> Path:
        return self.runtime / "worker.lock"

    def initialize(self) -> None:
        private_dir(self.root)
        for name in (
            "objects",
            "objects/sha256",
            "staging",
            "exports",
            "logs",
            "backups",
            "runtime",
        ):
            private_dir(self.root / name)
        private_file(self.worker_lock)
        private_file(self.runtime / "maintenance.lock")

    def object_path(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise CoreError(ErrorCode.INPUT_INVALID, "Invalid SHA-256")
        return self.objects / sha256[:2] / sha256
