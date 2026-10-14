"""Log a failure without exception text, arguments, locals, or source lines."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

_OPERATION = re.compile(r"[A-Za-z0-9_.-]{1,64}(?: [A-Za-z0-9_.-]{1,64})?\Z")
_CODE = re.compile(r"E_[A-Z0-9_]{1,40}\Z")
_REQUEST_ID = re.compile(r"req_[0-9a-f]{32}\Z")
_JOB_ID = re.compile(r"job_[0-9a-f]{32}\Z")
_CLASS = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,80}\Z")
_FILE = re.compile(r"[A-Za-z0-9_.-]{1,80}\Z")
_FUNC = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,80}\Z")
_MAX_CHAIN = 8
_MAX_FRAMES = 16


def known_job_id(arguments: Mapping[str, Any] | None) -> str | None:
    """Return a job id only when the argument is already in the public id shape."""
    if not isinstance(arguments, Mapping):
        return None
    value = arguments.get("job_id")
    if isinstance(value, str) and _JOB_ID.fullmatch(value):
        return value
    return None


def log_safe_exception(
    logger: logging.Logger,
    exc: BaseException,
    *,
    operation: str,
    code: str,
    request_id: str | None = None,
    job_id: str | None = None,
) -> None:
    """Record class names and ``file:line:function`` frames. Never ``exc_info``."""
    logger.error(
        "%s",
        _message(exc, operation=operation, code=code, request_id=request_id, job_id=job_id),
        exc_info=False,
    )


def _message(
    exc: BaseException,
    *,
    operation: str,
    code: str,
    request_id: str | None,
    job_id: str | None,
) -> str:
    parts = [
        f"operation={_token(operation, _OPERATION, 'unknown')}",
        f"code={_token(code, _CODE, 'E_STORAGE')}",
    ]
    if isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id):
        parts.append(f"request_id={request_id}")
    if isinstance(job_id, str) and _JOB_ID.fullmatch(job_id):
        parts.append(f"job_id={job_id}")
    parts.append("classes=" + ",".join(_classes(exc)))
    frames = _frames(exc)
    parts.append("frames=" + (",".join(frames) if frames else "-"))
    return " ".join(parts)


def _token(value: str, pattern: re.Pattern[str], fallback: str) -> str:
    return value if isinstance(value, str) and pattern.fullmatch(value) else fallback


def _classes(exc: BaseException) -> list[str]:
    names: list[str] = []
    for item in _chain(exc):
        name = type(item).__name__
        names.append(name if isinstance(name, str) and _CLASS.fullmatch(name) else "Exception")
    return names or ["Exception"]


def _chain(exc: BaseException) -> list[BaseException]:
    ordered: list[BaseException] = []
    seen: set[int] = set()
    pending: list[BaseException] = [exc]
    while pending and len(ordered) < _MAX_CHAIN:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        ordered.append(current)
        cause = current.__cause__ if isinstance(current.__cause__, BaseException) else None
        context = current.__context__ if isinstance(current.__context__, BaseException) else None
        if cause is not None and id(cause) not in seen:
            pending.append(cause)
        if context is not None and context is not cause and id(context) not in seen:
            pending.append(context)
    return ordered


def _frames(exc: BaseException) -> list[str]:
    frames: list[str] = []
    for item in _chain(exc):
        tb = item.__traceback__
        while tb is not None and len(frames) < _MAX_FRAMES:
            frames.append(_frame(tb))
            tb = tb.tb_next
    return frames


def _frame(tb: Any) -> str:
    try:
        code = tb.tb_frame.f_code
        raw_name = str(code.co_filename).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        func = str(code.co_name)
        line = tb.tb_lineno
    except Exception:
        return "file:0:function"
    file_name = raw_name if _FILE.fullmatch(raw_name) else "file"
    function = func if _FUNC.fullmatch(func) else "function"
    number = line if isinstance(line, int) and line >= 0 else 0
    return f"{file_name}:{number}:{function}"
