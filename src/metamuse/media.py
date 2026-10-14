"""Local media bytes and provided subtitle cues.

Object bytes are staged, sniffed, and validated here. The collect job inserts
the ready asset row and its project link in one database transaction.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from metamuse import models as m
from metamuse.paths import private_dir

if TYPE_CHECKING:
    from metamuse.jobs import Operation
    from metamuse.store import Store


IMPLEMENTED_TOOLS: frozenset[str] = frozenset()

_HEADER = 64
_CHUNK = 1024 * 1024
_HTML = (b"<!doctype", b"<html", b"<head", b"<body")
_TS = re.compile(r"^(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{1,3})$")
_RANGE = re.compile(
    r"^\s*(?P<start>(?:\d+:)?\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*"
    r"(?P<end>(?:\d+:)?\d{2}:\d{2}[.,]\d{1,3})(?:\s+\S.*)?\s*$"
)
FFPROBE_ARGS = (
    "-hide_banner",
    "-loglevel",
    "error",
    "-show_entries",
    "format=duration:stream=codec_type",
    "-of",
    "json",
    "-protocol_whitelist",
    "file",
)


def register_operations(registry: dict[str, Operation] | None = None) -> None:
    """Media work runs inside collect's import operation; this module adds none."""


@dataclass(frozen=True)
class MediaInfo:
    sha256: str
    mime: str
    byte_size: int
    duration_ms: int | None
    probe: str


@dataclass
class Cue:
    cue_index: int
    start_ms: int
    end_ms: int
    text: str
    overlap: bool = False
    row: int = 0


@dataclass
class CueError:
    row: int
    field: str
    code: str
    message: str


@dataclass
class SubtitleFile:
    cues: list[Cue] = field(default_factory=list)
    errors: list[CueError] = field(default_factory=list)
    language: str | None = None


def sniff_mime(header: bytes) -> str | None:
    """Return a MIME type from magic bytes, or text/html for an HTML document."""

    sample = header.lstrip(b"\xef\xbb\xbf \t\r\n")
    if sample[:32].lower().startswith(_HTML):
        return "text/html"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "audio/wav"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"AVI ":
        return "video/x-msvideo"
    if header.startswith(b"OggS"):
        return "audio/ogg"
    if header.startswith(b"fLaC"):
        return "audio/flac"
    if header.startswith(b"ID3") or (
        len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
    ):
        return "audio/mpeg"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        if header[8:12] == b"qt  ":
            return "video/quicktime"
        return "video/mp4"
    return None


def walk_isobmff(path: Path) -> None:
    """Reject a truncated or incoherent MP4/QuickTime box stream."""

    size = path.stat().st_size
    with path.open("rb") as handle:
        offset = 0
        while offset + 8 <= size:
            handle.seek(offset)
            header = handle.read(8)
            if len(header) < 8:
                raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Media container is truncated")
            box_size = int.from_bytes(header[:4], "big")
            box_type = header[4:8]
            if any(byte < 32 or byte > 126 for byte in box_type):
                raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Media container is truncated")
            header_len = 8
            if box_size == 1:
                large = handle.read(8)
                if len(large) < 8:
                    raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Media container is truncated")
                box_size = int.from_bytes(large, "big")
                header_len = 16
            elif box_size == 0:
                box_size = size - offset
            if box_size < header_len or offset + box_size > size:
                raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Media container is truncated")
            offset += box_size
        if offset != size:
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Media container is truncated")


def _hash_file(path: Path, *, max_bytes: int) -> tuple[str, int, bytes]:
    digest = hashlib.sha256()
    size = 0
    header = b""
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            size += len(chunk)
            if size > max_bytes:
                raise m.CoreError(
                    m.ErrorCode.LIMIT_REACHED,
                    "Media exceeds the byte limit",
                    next_action="Choose a smaller file or raise the workspace media byte limit",
                )
            digest.update(chunk)
            if len(header) < _HEADER:
                header += chunk[: _HEADER - len(header)]
    return digest.hexdigest(), size, header


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ffprobe_command(binary: str, path: Path) -> list[str]:
    """Fixed argv. The file URL is one argument and is never passed through a shell."""

    return [binary, *FFPROBE_ARGS, f"file:{path.resolve()}"]


def _duration_ms(payload: dict) -> int | None:
    raw = (payload.get("format") or {}).get("duration")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return int(seconds * 1000)


def probe_media(path: Path, *, timeout: int) -> tuple[str, int | None]:
    """Run ffprobe when it is installed. Absence is a probe status, not a failure."""

    binary = shutil.which("ffprobe")
    if binary is None:
        return "not_installed", None
    if not path.is_file():
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID, "Media probe reported success but the file is missing"
        )
    try:
        completed = subprocess.run(
            ffprobe_command(binary, path),
            shell=False,
            timeout=timeout,
            capture_output=True,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise m.CoreError(m.ErrorCode.NETWORK_TIMEOUT, "Media probe timed out") from exc
    if not path.is_file():
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID, "Media probe reported success but the file is missing"
        )
    if completed.returncode != 0:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Media probe rejected the file")
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID, "Media probe returned unreadable output"
        ) from exc
    if not isinstance(payload, dict):
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Media probe returned unreadable output")
    return "probed", _duration_ms(payload)


def analyze_file(path: Path, *, max_bytes: int, read_timeout_seconds: int) -> MediaInfo:
    """Hash, sniff, and structurally validate one staged file. Nothing is marked ready."""

    if not path.is_file():
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID, "Media probe reported success but the file is missing"
        )
    sha, size, header = _hash_file(path, max_bytes=max_bytes)
    if size == 0:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Media file is empty")
    if path.stat().st_size != size or not path.is_file():
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID, "Media probe reported success but the file is missing"
        )
    mime = sniff_mime(header)
    if mime == "text/html":
        raise m.CoreError(
            m.ErrorCode.UNSUPPORTED,
            "Media file is an HTML page",
            next_action="Pass the media file itself, not an HTML page saved with a media extension",
        )
    if mime is None:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Media type is not recognized")
    if mime in ("video/mp4", "video/quicktime"):
        walk_isobmff(path)
    probe, duration = probe_media(path, timeout=read_timeout_seconds)
    if not path.is_file() or path.stat().st_size != size:
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID, "Media probe reported success but the file is missing"
        )
    return MediaInfo(sha256=sha, mime=mime, byte_size=size, duration_ms=duration, probe=probe)


def place_object(store: Store, staged: Path, info: MediaInfo) -> Path:
    """Move staged bytes onto the content-addressed path. Does not insert a row."""

    if not staged.is_file() or staged.stat().st_size != info.byte_size:
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID, "Media probe reported success but the file is missing"
        )
    target = store.paths.object_path(info.sha256)
    if target.exists():
        if (
            target.stat().st_size != info.byte_size
            or _hash_file(target, max_bytes=info.byte_size)[0] != info.sha256
        ):
            raise m.CoreError(m.ErrorCode.STORAGE, "Existing object hash mismatch")
        staged.unlink()
    else:
        private_dir(target.parent)
        os.replace(staged, target)
        if not target.is_file():
            raise m.CoreError(
                m.ErrorCode.INPUT_INVALID, "Media probe reported success but the file is missing"
            )
        target.chmod(0o600)
        _fsync_dir(target.parent)
    if not target.is_file() or target.stat().st_size != info.byte_size:
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID, "Media probe reported success but the file is missing"
        )
    return target


def require_compatible_mime(store: Store, info: MediaInfo) -> None:
    """One verified MIME per byte string. A disagreement is an input error."""

    with store.connection() as conn:
        row = conn.execute("SELECT mime FROM assets WHERE sha256=?", (info.sha256,)).fetchone()
    if row is not None and row["mime"] != info.mime:
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID,
            "Object MIME disagrees with its existing classification",
        )


def ensure_asset(store: Store, writer: Any, info: MediaInfo) -> m.Asset:
    """Insert a ready asset or reuse the row when the MIME matches.

    The first writer's duration stays. A different MIME raises E_INPUT_INVALID.
    """

    row = writer.execute("SELECT * FROM assets WHERE sha256=?", (info.sha256,)).fetchone()
    if row is not None:
        asset = store.decode(m.Asset, row)
        if asset.mime != info.mime:
            raise m.CoreError(
                m.ErrorCode.INPUT_INVALID,
                "Object MIME disagrees with its existing classification",
            )
        store.verify_asset(asset)
        return asset
    path = store.paths.object_path(info.sha256)
    asset = m.Asset(
        id=m.new_id("ast"),
        sha256=info.sha256,
        mime=info.mime,
        actual_bytes=info.byte_size,
        duration_ms=info.duration_ms,
        object_path=str(path.relative_to(store.paths.root)),
        state="ready",
    )
    return writer.insert(asset)


def _timestamp_ms(token: str) -> int | None:
    match = _TS.match(token.strip())
    if match is None:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    fraction = match.group(4)
    if minutes > 59 or seconds > 59:
        return None
    millis = int(fraction) * (10 ** (3 - len(fraction)))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def _mark_overlaps(cues: list[Cue]) -> None:
    for index, left in enumerate(cues):
        for right in cues[index + 1 :]:
            if left.start_ms < right.end_ms and right.start_ms < left.end_ms:
                left.overlap = True
                right.overlap = True


def _html_page(text: str) -> bool:
    sample = text.lstrip("\ufeff \t\r\n")[:64].lower()
    return sample.startswith(("<!doctype", "<html", "<head", "<body"))


def _blocks(lines: list[str]) -> list[tuple[int, list[str]]]:
    blocks: list[tuple[int, list[str]]] = []
    index = 0
    number = 0
    while index < len(lines):
        while index < len(lines) and lines[index].strip() == "":
            index += 1
        if index >= len(lines):
            break
        block: list[str] = []
        while index < len(lines) and lines[index].strip() != "":
            block.append(lines[index])
            index += 1
        number += 1
        blocks.append((number, block))
    return blocks


def _cue_from_block(number: int, block: list[str], *, skip_notes: bool) -> Cue | CueError | None:
    first = block[0].strip()
    if skip_notes and (
        first == "NOTE"
        or first.startswith(("NOTE ", "NOTE\t", "STYLE", "REGION"))
        or first in ("STYLE", "REGION")
    ):
        return None
    timestamp_at = next((index for index, line in enumerate(block[:2]) if "-->" in line), None)
    if timestamp_at is None:
        return CueError(
            number,
            "timestamp",
            m.ErrorCode.INPUT_INVALID.value,
            "Cue timestamp is malformed",
        )
    match = _RANGE.match(block[timestamp_at])
    if match is None:
        return CueError(
            number,
            "timestamp",
            m.ErrorCode.INPUT_INVALID.value,
            "Cue timestamp is malformed",
        )
    start = _timestamp_ms(match.group("start"))
    end = _timestamp_ms(match.group("end"))
    if start is None or end is None:
        return CueError(
            number,
            "timestamp",
            m.ErrorCode.INPUT_INVALID.value,
            "Cue timestamp is malformed",
        )
    if start < 0 or end < start:
        return CueError(
            number,
            "end_ms",
            m.ErrorCode.INPUT_INVALID.value,
            "Cue end is before its start",
        )
    return Cue(
        cue_index=0,
        start_ms=start,
        end_ms=end,
        text="\n".join(block[timestamp_at + 1 :]),
        row=number,
    )


def parse_webvtt(text: str) -> SubtitleFile:
    """Parse a WebVTT document. HTML pages and a missing header fail the file."""

    if text.startswith("\ufeff"):
        text = text[1:]
    if _html_page(text):
        raise m.CoreError(
            m.ErrorCode.UNSUPPORTED,
            "Subtitle file is an HTML page, not a caption track",
            next_action=(
                "Pass a WebVTT or SRT file, not an HTML page renamed to a caption extension"
            ),
        )
    lines = text.splitlines()
    if not lines or not lines[0].startswith("WEBVTT"):
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "WebVTT file is missing the WEBVTT header")
    language = None
    index = 1
    while index < len(lines) and lines[index].strip() != "":
        if lines[index].lower().startswith("language:"):
            value = lines[index].split(":", 1)[1].strip()
            language = value or None
        index += 1
    parsed = SubtitleFile(language=language)
    for number, block in _blocks(lines[index:]):
        cue = _cue_from_block(number, block, skip_notes=True)
        if isinstance(cue, Cue):
            parsed.cues.append(cue)
        elif isinstance(cue, CueError):
            parsed.errors.append(cue)
    for cue_index, cue in enumerate(parsed.cues):
        cue.cue_index = cue_index
    _mark_overlaps(parsed.cues)
    return parsed


def parse_srt(text: str) -> SubtitleFile:
    """Parse an SRT document. An HTML page fails the file; a blank file has zero cues."""

    if text.startswith("\ufeff"):
        text = text[1:]
    if _html_page(text):
        raise m.CoreError(
            m.ErrorCode.UNSUPPORTED,
            "Subtitle file is an HTML page, not a caption track",
            next_action=(
                "Pass a WebVTT or SRT file, not an HTML page renamed to a caption extension"
            ),
        )
    parsed = SubtitleFile()
    if text.strip() == "":
        return parsed
    for number, block in _blocks(text.splitlines()):
        body = block[1:] if block and block[0].strip().isdigit() else block
        if not body:
            parsed.errors.append(
                CueError(
                    number,
                    "timestamp",
                    m.ErrorCode.INPUT_INVALID.value,
                    "Cue timestamp is malformed",
                )
            )
            continue
        cue = _cue_from_block(number, body, skip_notes=False)
        if isinstance(cue, Cue):
            parsed.cues.append(cue)
        elif isinstance(cue, CueError):
            parsed.errors.append(cue)
    for cue_index, cue in enumerate(parsed.cues):
        cue.cue_index = cue_index
    _mark_overlaps(parsed.cues)
    return parsed


def parse_subtitles(text: str, fmt: str) -> SubtitleFile:
    if fmt == "vtt":
        return parse_webvtt(text)
    if fmt == "srt":
        return parse_srt(text)
    raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Subtitle format is not supported")
