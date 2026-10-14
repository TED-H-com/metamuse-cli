#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Check the generated project page, image manifest, and transcripts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from site_lib import (  # noqa: E402
    GENERATED_SITE_FILES,
    TRANSCRIPT_URL_PREFIX,
    caption_transcript_problems,
    manifest_problems,
    pages_workflow_problems,
    sha256_file,
    status_problems,
)

REPO = Path(__file__).resolve().parents[1]
HREF = re.compile(r"""(?:href|src)="([^"]+)""")
IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
ALT = re.compile(r"""\balt="([^"]*)""")
SRC = re.compile(r"""\bsrc="([^"]+)""")


def _fail(problems: list[str]) -> int:
    for problem in problems:
        print(problem)
    return 1 if problems else 0


def _kit() -> Path:
    raw = os.environ.get("PAGE_KIT_DIR", "").strip()
    if not raw:
        raise SystemExit("PAGE_KIT_DIR environment variable is required")
    kit = Path(raw)
    render = kit / "render.py"
    if not render.is_file():
        raise SystemExit(f"page kit render.py not found at {render}")
    return kit


def _render(repo: Path, kit: Path, *extra: str) -> None:
    subprocess.run(
        [sys.executable, str(kit / "render.py"), str(repo), *extra],
        check=True,
    )


def _generated_hashes(site: Path) -> dict[str, str]:
    found = {}
    for name in GENERATED_SITE_FILES:
        path = site / name
        if path.is_file():
            found[name] = sha256_file(path)
    return found


def _local_target(page_dir: Path, url: str) -> Path | None:
    if url.startswith(TRANSCRIPT_URL_PREFIX):
        rel = url[len(TRANSCRIPT_URL_PREFIX) :].split("#", 1)[0].split("?", 1)[0]
        return (page_dir / "images" / rel).resolve()
    if url.startswith(("http://", "https://", "mailto:", "#", "data:")):
        return None
    if url.startswith("/"):
        return None
    path = url.split("#", 1)[0].split("?", 1)[0]
    if not path:
        return None
    return (page_dir / path).resolve()


def _link_problems(site: Path) -> list[str]:
    problems = []
    for html in (site / "index.html", site / "404.html"):
        text = html.read_text(encoding="utf-8")
        for match in HREF.finditer(text):
            target = _local_target(html.parent, match.group(1))
            if target is not None and not target.is_file():
                problems.append(f"{html.name} missing local target {match.group(1)}")
        for tag in IMG.finditer(text):
            src = SRC.search(tag.group(0))
            alt = ALT.search(tag.group(0))
            if src is None:
                continue
            url = src.group(1)
            if "logo.svg" in url or "favicon.svg" in url:
                continue
            if alt is None or not alt.group(1).strip():
                problems.append(f"{html.name} image {url} has empty alt text")
            target = _local_target(html.parent, url)
            if target is not None and not target.is_file():
                problems.append(f"{html.name} missing image {url}")
    return problems


def _bilingual(value: object, path: str, problems: list[str]) -> None:
    if not isinstance(value, dict) or not str(value.get("en", "")).strip():
        problems.append(f"{path} needs non-empty English alt text")
        return
    chinese = str(value.get("zh", "")).strip()
    if not chinese:
        problems.append(f"{path} needs non-empty Traditional Chinese alt text")
    elif not re.search(r"[一-鿿]", chinese):
        problems.append(f"{path} Chinese alt text has no Chinese characters")


def _alt_problems(page: dict) -> list[str]:
    problems: list[str] = []
    visual = page.get("hero", {}).get("visual", {})
    if visual.get("type") == "image":
        _bilingual(visual.get("alt"), "hero.visual.alt", problems)
        if not (REPO / str(visual.get("src", ""))).is_file():
            problems.append(f"hero image missing: {visual.get('src')}")
    for index, shot in enumerate((page.get("showcase") or {}).get("shots") or []):
        _bilingual(shot.get("alt"), f"showcase.shots[{index}].alt", problems)
        if not (REPO / str(shot.get("src", ""))).is_file():
            problems.append(f"showcase image missing: {shot.get('src')}")
    return problems


def main() -> int:
    kit = _kit()
    site = REPO / "site"
    page_path = site / "page.json"
    page = json.loads(page_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    _render(REPO, kit, "--check")
    before = _generated_hashes(site)
    _render(REPO, kit)
    after = _generated_hashes(site)
    if before != after:
        changed = sorted(
            set(before) ^ set(after) | {k for k in before if before.get(k) != after.get(k)}
        )
        problems.append("re-render changed generated files: " + ", ".join(changed))
    problems.extend(_link_problems(site))
    problems.extend(_alt_problems(page))
    problems.extend(caption_transcript_problems(site, page))
    problems.extend(manifest_problems(site))
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    problems.extend(status_problems(readme, page))
    workflow = REPO / ".github" / "workflows" / "pages.yml"
    if not workflow.is_file():
        problems.append("missing .github/workflows/pages.yml")
    else:
        problems.extend(pages_workflow_problems(workflow.read_text(encoding="utf-8")))
    return _fail(problems)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
