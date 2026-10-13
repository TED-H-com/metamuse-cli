"""Muse feature seam; implementations belong to this feature owner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from metamuse import models as m

if TYPE_CHECKING:
    from metamuse.app import AppContext
    from metamuse.jobs import Operation


IMPLEMENTED_TOOLS: frozenset[str] = frozenset()


def register_operations(registry: dict[str, Operation] | None = None) -> None:
    """Register this module's operations idempotently; none are implemented yet."""


def open_muse_discussion(ctx: AppContext, request: m.MuseOpenRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")


def submit_muse_exchange(ctx: AppContext, request: m.MuseSendRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")


def read_muse_exchange(ctx: AppContext, request: m.MuseReadRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")


def close_muse_exchange(ctx: AppContext, request: m.MuseCloseExchangeRequest) -> m.Envelope:
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")
