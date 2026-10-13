"""Media feature seam; implementations belong to this feature owner."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from metamuse.jobs import Operation


IMPLEMENTED_TOOLS: frozenset[str] = frozenset()


def register_operations(registry: dict[str, Operation] | None = None) -> None:
    """Register this module's operations idempotently; none are implemented yet."""
