"""Shared checks for the project page. Standard library only."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import socket
import struct
import zlib
from pathlib import Path

FOUR_STATES = (
    "implemented with offline tests",
    "implemented but not independently accepted",
    "not done",
    "needs a real account (LIVE)",
)

WORKS_STATES = {"implemented with offline tests"}
GENERATED_SITE_FILES = (
    "index.html",
    "404.html",
    "styles.css",
    "lang.js",
    "app.js",
    "flow.js",
    "flow.css",
    "logo.svg",
    "favicon.svg",
    "robots.txt",
    "sitemap.xml",
    ".nojekyll",
)

KIT_COMMIT = "5e51fb954acef56e9f75b4cfd2b57dc76afcdfd8"
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"  # actions/checkout v7.0.1
CONFIGURE_PAGES_SHA = "45bfe0192ca1faeb007ade9deae92b16b8254a0d"  # v6.0.0
UPLOAD_PAGES_SHA = "fc324d3547104276b827a68afc52ff2a11cc49c9"  # v5.0.0
DEPLOY_PAGES_SHA = "368f82528645a54fb793d4d04e342629a3f51346"  # v5.0.1

TRANSCRIPT_URL_PREFIX = "https://ted-h-com.github.io/metamuse-cli/images/"
_TRANSCRIPT_LINK_RE = re.compile(
    rf'href="({re.escape(TRANSCRIPT_URL_PREFIX)}([a-zA-Z0-9_-]+\.txt))"'
)
_TAG_RE = re.compile(r"<[^>]+>")
_HOME_MARKER = "/" + "home/"
MAX_IMAGE_BYTES = 500_000

VALIDATE_STEP = f"""      - name: Check out page kit
        uses: actions/checkout@{CHECKOUT_SHA} # v7.0.1
        with:
          repository: teddashh/teddashh.github.io
          ref: {KIT_COMMIT}
          path: .page-kit
          persist-credentials: false

      - name: Validate site
        env:
          PAGE_KIT_DIR: ${{{{ github.workspace }}}}/.page-kit/kit
        run: python3 scripts/check_site.py
"""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def privacy_failures(text: str, *, denylist: Path | None = None) -> list[str]:
    """Return reasons a transcript must not be published."""
    failures: list[str] = []
    user = os.environ.get("USER", "")
    if user and user in text:
        failures.append(f"contains USER {user!r}")
    host = socket.gethostname()
    if host and host.lower() in text.lower():
        failures.append(f"contains hostname {host!r}")
    if _HOME_MARKER in text:
        failures.append(f"contains {_HOME_MARKER}")
    path = denylist
    if path is None:
        raw = os.environ.get("SITE_PRIVATE_DENYLIST", "")
        path = Path(raw) if raw else None
    if path is not None and path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            term = line.strip()
            if not term or term.startswith("#"):
                continue
            if term in text:
                failures.append(f"contains denylist term {term!r}")
    return failures


def _split_row(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def readme_status_rows(readme: str) -> list[tuple[str, str]] | None:
    """Return (capability, state) rows from a README status table, if one exists.

    A status table is a GitHub-flavored table whose header contains a cell
    `State`, `Status`, or `狀態`. The capability is the first cell. The state is
    the cell under that header.
    """
    lines = readme.splitlines()
    rows: list[tuple[str, str]] | None = None
    index = 0
    while index < len(lines) - 1:
        header = _split_row(lines[index])
        separator = _split_row(lines[index + 1]) if index + 1 < len(lines) else []
        if "|" not in lines[index] or not _is_separator(separator):
            index += 1
            continue
        status_at = None
        for cell_index, cell in enumerate(header):
            if cell.lower() in {"state", "status"} or cell == "狀態":
                status_at = cell_index
                break
        block: list[tuple[str, str]] = []
        cursor = index + 2
        while cursor < len(lines) and "|" in lines[cursor] and lines[cursor].strip():
            cells = _split_row(lines[cursor])
            if status_at is not None and cells and cells[0] and status_at < len(cells):
                capability = re.sub(r"`([^`]+)`", r"\1", cells[0])
                block.append((capability, cells[status_at]))
            cursor += 1
        if status_at is not None:
            rows = block
        index = max(cursor, index + 1)
    return rows


def _column_text(items: list) -> str:
    return "\n".join(
        html.unescape(_TAG_RE.sub("", str(item.get(lang, ""))))
        for item in items
        if isinstance(item, dict)
        for lang in ("en", "zh")
    )


def status_problems(readme: str, page: dict) -> list[str]:
    """Compare ordered rows and exact states; prose substrings cannot stand in for a row."""
    rows = readme_status_rows(readme)
    if not rows:
        return ["README has no status table"]
    problems: list[str] = []
    status = page.get("status") or {}
    for cap, state in rows:
        if state not in FOUR_STATES:
            problems.append(f"README row {cap!r} has unknown state {state!r}")
    for column in ("works", "limits"):
        expected = [
            (cap, state) for cap, state in rows if (state in WORKS_STATES) == (column == "works")
        ]
        items = status.get(column) or []
        actual = []
        for index, item in enumerate(items):
            en = str(item.get("en", "")) if isinstance(item, dict) else ""
            cap = re.search(r"<strong>([^<]+)</strong>", en)
            state = re.search(r"<code>([^<]+)</code>", en)
            actual.append((html.unescape(cap[1]) if cap else "", state[1] if state else ""))
            if state:
                zh = str(item.get("zh", ""))
                if f"<code>{state[1]}</code>" not in zh:
                    problems.append(f"{column}[{index}] Chinese state disagrees with English")
                plain = html.unescape(_TAG_RE.sub("", en))
                for other in FOUR_STATES:
                    if other != state[1] and other in plain:
                        problems.append(f"{column}[{index}] has conflicting state {other!r}")
        if actual != expected:
            problems.append(
                f"{column} rows disagree with README: expected {expected!r}, got {actual!r}"
            )
    works = _column_text(status.get("works") or [])
    if "live_verified" in works or "LIVE is verified" in works:
        problems.append("works column claims LIVE verification")
    intro = str((status.get("intro") or {}).get("en", ""))
    if "Muse LIVE is not verified" not in intro:
        problems.append("status intro must state that Muse LIVE is not verified")
    return problems


def caption_transcript_problems(site: Path, page: dict) -> list[str]:
    """Every frame caption must link its plain-text transcript under site/images/."""
    problems: list[str] = []
    owner = page["repo"].split("/")[0].lower()
    expected = f"https://{owner}.github.io/{page['slug']}/"
    actual = TRANSCRIPT_URL_PREFIX.removesuffix("images/")
    if actual != expected:
        problems.append(
            f"transcript site address disagrees with page.json: "
            f"expected {expected!r}, got {actual!r}"
        )
    targets: list[tuple[str, dict]] = []
    visual = page.get("hero", {}).get("visual", {})
    if visual.get("type") == "image":
        targets.append(("hero.visual", visual))
    for index, shot in enumerate((page.get("showcase") or {}).get("shots") or []):
        targets.append((f"showcase.shots[{index}]", shot))

    for label, item in targets:
        caption = item.get("caption")
        if not isinstance(caption, dict):
            problems.append(f"{label}.caption must be a bilingual object")
            continue
        for lang in ("en", "zh"):
            text = str(caption.get(lang, ""))
            matches = _TRANSCRIPT_LINK_RE.findall(text)
            if not matches:
                problems.append(
                    f"{label}.caption.{lang} must link a transcript under {TRANSCRIPT_URL_PREFIX}"
                )
                continue
            expected = Path(str(item.get("src", ""))).stem + ".txt"
            if len(matches) != 1 or matches[0][1] != expected:
                problems.append(f"{label}.caption.{lang} must link its own transcript {expected}")
            for _url, filename in matches:
                transcript = site / "images" / filename
                if not transcript.is_file() or transcript.stat().st_size == 0:
                    problems.append(
                        f"{label}.caption.{lang} links missing transcript images/{filename}"
                    )
    return problems


def _rect_problems(rect: object, label: str) -> list[str]:
    if not isinstance(rect, dict):
        return [f"{label} must be an object"]
    problems: list[str] = []
    for key in ("x", "y", "width", "height"):
        val = rect.get(key)
        if not isinstance(val, int) or isinstance(val, bool):
            problems.append(f"{label}.{key} must be an int")
    if not problems:
        if rect["width"] <= 0 or rect["height"] <= 0:
            problems.append(f"{label} width and height must be positive")
    return problems


def _png_size(path: Path) -> tuple[int, int] | None:
    """Read and verify the PNG IHDR without an imaging dependency in CI."""
    header = path.read_bytes()[:33]
    if len(header) != 33 or header[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR":
        return None
    if zlib.crc32(header[12:29]) != struct.unpack(">I", header[29:33])[0]:
        return None
    return struct.unpack(">II", header[16:24])


def manifest_problems(site: Path) -> list[str]:
    path = site / "images" / "manifest.json"
    if not path.is_file():
        return ["site/images/manifest.json is missing"]
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return ["image manifest is not readable JSON"]
    if not isinstance(manifest, dict):
        return ["image manifest must be an object"]
    problems: list[str] = []
    for key, expected in (
        ("synthetic_data", True),
        ("live_or_real_account", False),
        ("image_edited", False),
    ):
        if manifest.get(key) is not expected:
            problems.append(f"manifest {key} must be {expected}")
    if manifest.get("scale_factor") != 2:
        problems.append("manifest scale_factor must be 2")
    for key in ("density_method", "crop_note"):
        if not str(manifest.get(key, "")).strip():
            problems.append(f"manifest {key} is empty")
    for key in ("source_commit", "source_tree"):
        if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get(key, ""))):
            problems.append(f"manifest {key} must be a full git hash")
    images = manifest.get("images")
    if not isinstance(images, list) or not images:
        return [*problems, "manifest lists no images"]
    readme = site / "images" / "README.md"
    readme_text = readme.read_text(encoding="utf-8") if readme.is_file() else ""
    if not readme.is_file():
        problems.append("site/images/README.md is missing")
    listed: set[str] = set()
    for record in images:
        if not isinstance(record, dict):
            problems.append("manifest image record must be an object")
            continue
        name = record.get("png", "")
        text_name = record.get("transcript", "")
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+\.png", name):
            problems.append("manifest PNG must be a filename within images")
            continue
        if text_name != Path(name).stem + ".txt":
            problems.append(f"{name} must name its corresponding transcript")
            continue
        for filename in (name, text_name):
            if filename in listed:
                problems.append(f"duplicate manifest entry: {filename}")
            listed.add(filename)
        png = site / "images" / name
        transcript = site / "images" / text_name
        win_rect, crop_rect = record.get("window_rect"), record.get("crop_rect")
        rect_errors = [
            *_rect_problems(win_rect, f"{name} window_rect"),
            *_rect_problems(crop_rect, f"{name} crop_rect"),
        ]
        problems.extend(rect_errors)
        if not rect_errors:
            for key in ("x", "y", "width"):
                if crop_rect[key] != win_rect[key]:
                    problems.append(f"{name} crop_rect.{key} must equal window_rect.{key}")
            if not 0 < crop_rect["height"] <= win_rect["height"]:
                problems.append(f"{name} crop height must be within the window")
            cell = record.get("cell_px", {}).get("height")
            rows = record.get("content_rows")
            padding = record.get("padding_top_px")
            if not all(type(value) is int and value > 0 for value in (cell, rows, padding)):
                problems.append(f"{name} needs positive cell height, content rows and padding")
            else:
                if crop_rect["height"] != padding + (rows + 1) * cell:
                    problems.append(f"{name} crop must include exactly one empty row below content")
                if rows > (22 if name == "00-hero.png" else 40):
                    problems.append(f"{name} exceeds the content row budget")
        if record.get("lossless_verified") is not True:
            problems.append(f"{name} lossless_verified must be True")
        if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("decoded_rgb_sha256", ""))):
            problems.append(f"{name} needs a decoded RGB hash")
        if not png.is_file():
            problems.append(f"missing {name}")
            continue
        if png.stat().st_size > MAX_IMAGE_BYTES:
            problems.append(f"{name} exceeds {MAX_IMAGE_BYTES} bytes")
        if png.stat().st_size != record.get("bytes"):
            problems.append(f"{name} byte count mismatch")
        if sha256_file(png) != record.get("sha256"):
            problems.append(f"sha256 mismatch for {name}")
        dimensions = _png_size(png)
        if dimensions is None:
            problems.append(f"{name} has an invalid PNG header")
        elif not rect_errors and dimensions != (crop_rect["width"], crop_rect["height"]):
            problems.append(f"{name} PNG dimensions disagree with crop_rect")
        if not transcript.is_file() or transcript.stat().st_size == 0:
            problems.append(f"missing transcript for {name}")
            continue
        if sha256_file(transcript) != record.get("transcript_sha256"):
            problems.append(f"transcript sha256 mismatch for {text_name}")
        if name not in readme_text:
            problems.append(f"images README does not mention {name}")
        problems.extend(
            f"{text_name}: {failure}" for failure in privacy_failures(transcript.read_text())
        )
    present = {p.name for p in (site / "images").iterdir() if p.suffix in (".png", ".txt")}
    if present != listed:
        problems.append(
            f"manifest file set disagrees: unlisted={sorted(present - listed)}, "
            f"missing={sorted(listed - present)}"
        )
    return problems


def apply_pages_validate_step(path: Path) -> None:
    """Insert the site check into the kit workflow without dropping its pinned actions."""
    text = path.read_text(encoding="utf-8")
    if "python3 scripts/check_site.py" in text and f"ref: {KIT_COMMIT}" in text:
        return
    needle = "          persist-credentials: false\n"
    if needle not in text:
        raise SystemExit(f"{path} has no persist-credentials: false checkout to extend")
    updated = text.replace(needle, needle + "\n" + VALIDATE_STEP, 1)
    path.write_text(updated, encoding="utf-8")


def pages_workflow_problems(text: str) -> list[str]:
    problems: list[str] = []
    expected = {
        "actions/checkout": CHECKOUT_SHA,
        "actions/configure-pages": CONFIGURE_PAGES_SHA,
        "actions/upload-pages-artifact": UPLOAD_PAGES_SHA,
        "actions/deploy-pages": DEPLOY_PAGES_SHA,
    }
    found: set[str] = set()
    kit_found = False
    # Examine each step, so a pin or credential setting in an unrelated step
    # cannot satisfy a checkout. The generated workflow has six-space step indent.
    steps = re.split(r"(?m)^      - ", text)[1:]
    for step in steps:
        use = re.search(r"(?:^uses:|^        uses:)\s*(\S+)", step, re.MULTILINE)
        if not use:
            continue
        action, _, ref = use[1].partition("@")
        found.add(action)
        if action in expected and ref != expected[action]:
            problems.append(f"pages.yml {action} action is not pinned to the expected SHA")
        elif action not in expected and not re.fullmatch(r"[0-9a-f]{40}", ref):
            problems.append(f"pages.yml {action} action is not pinned")
        if action == "actions/checkout":
            if not re.search(r"^          persist-credentials: false\s*$", step, re.MULTILINE):
                problems.append("pages.yml must set persist-credentials: false on every checkout")
            if "repository: teddashh/teddashh.github.io" in step:
                kit_found = bool(
                    re.search(rf"^          ref: {KIT_COMMIT}\s*$", step, re.MULTILINE)
                )
    for action in expected.keys() - found:
        problems.append(f"pages.yml is missing pinned {action}")
    if not kit_found:
        problems.append("pages.yml does not pin the page kit checkout")
    if "python3 scripts/check_site.py" not in text or "PAGE_KIT_DIR:" not in text:
        problems.append("pages.yml does not run scripts/check_site.py with PAGE_KIT_DIR")
    if "permissions:" not in text or "contents: read" not in text:
        problems.append("pages.yml must keep contents: read")
    return problems
