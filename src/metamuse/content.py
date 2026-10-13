"""Content feature seam; implementations belong to this feature owner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from metamuse import models as m

if TYPE_CHECKING:
    from metamuse.app import AppContext
    from metamuse.jobs import Operation


IMPLEMENTED_TOOLS: frozenset[str] = frozenset()


def register_operations(registry: dict[str, Operation] | None = None) -> None:
    """Register this module's operations idempotently; none are implemented yet."""


def search_evidence(ctx: AppContext, request: m.SearchRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")


def prepare_packet(ctx: AppContext, request: m.PacketPrepareRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")


def save_draft(ctx: AppContext, request: m.DraftSaveRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")


def preview_draft(ctx: AppContext, request: m.DraftPreviewRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")


def export_objects(ctx: AppContext, request: m.ExportRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")
