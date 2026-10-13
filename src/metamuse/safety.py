"""Safety feature seam; implementations belong to this feature owner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from metamuse import models as m

if TYPE_CHECKING:
    from metamuse.app import AppContext
    from metamuse.jobs import Operation


IMPLEMENTED_TOOLS: frozenset[str] = frozenset()


def register_operations(registry: dict[str, Operation] | None = None) -> None:
    """Register this module's operations idempotently; none are implemented yet."""


def import_auth(ctx: AppContext, request: m.AuthImportRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")


def create_permit(ctx: AppContext, request: m.PermitCreateRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")
