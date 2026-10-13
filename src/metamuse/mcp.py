"""MCP server lifecycle seam; the MCP owner supplies the SDK transport."""

from __future__ import annotations

from typing import TYPE_CHECKING

from metamuse import models as m

if TYPE_CHECKING:
    from metamuse.app import AppContext


def run(ctx: AppContext) -> None:
    """Own protocol stdout; register builtins before tools or an attached worker start."""
    raise m.CoreError(m.ErrorCode.UNSUPPORTED, "not implemented in this build")
