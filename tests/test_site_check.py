"""Site checks run in normal CI, without the page kit, a display or network."""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from site_lib import (  # noqa: E402
    CHECKOUT_SHA,
    CONFIGURE_PAGES_SHA,
    DEPLOY_PAGES_SHA,
    KIT_COMMIT,
    TRANSCRIPT_URL_PREFIX,
    UPLOAD_PAGES_SHA,
    apply_pages_validate_step,
    caption_transcript_problems,
    manifest_problems,
    pages_workflow_problems,
    privacy_failures,
    readme_status_rows,
    status_problems,
)

SAMPLE_README = """# MetaMuse CLI

## Status

| Capability | State | Note |
|---|---|---|
| Local import | implemented with offline tests | Local files and text. |
| Search | implemented with offline tests | Project-scoped search. |
| Muse LIVE | needs a real account (LIVE) | Blocked by upstream protocol drift. |
| MCP attached worker | not done | Unavailable in this synthetic sample. |
| Publishing | not done | Not started. |
| CI | implemented but not independently accepted | Offline CI workflow. |
"""


def _sample_page() -> dict:
    page = {
        "repo": "TED-H-com/metamuse-cli",
        "slug": "metamuse-cli",
        "hero": {"visual": {"type": "image", "src": "site/images/00-hero.png"}},
        "showcase": {"shots": [{"src": "site/images/01-setup.png"}]},
        "status": {
            "intro": {
                "en": "Muse LIVE is not verified; permissions require a human.",
                "zh": "Muse LIVE 尚未驗證；權限須由人類核准。",
            },
            "works": [],
            "limits": [],
        },
    }
    for cap, state in readme_status_rows(SAMPLE_README):
        column = "works" if state == "implemented with offline tests" else "limits"
        page["status"][column].append(
            {
                "en": f"<strong>{cap}</strong> (<code>{state}</code>): Test note.",
                "zh": f"測試項目（<code>{state}</code>）。",
            }
        )
    for item in (page["hero"]["visual"], *page["showcase"]["shots"]):
        filename = Path(item["src"]).stem + ".txt"
        item["caption"] = {
            "en": f'CLI output. <a href="{TRANSCRIPT_URL_PREFIX}{filename}">Transcript</a>.',
            "zh": f'CLI 輸出。<a href="{TRANSCRIPT_URL_PREFIX}{filename}">純文字紀錄</a>。',
        }
    return page


@pytest.fixture(autouse=True)
def synthetic_identity(monkeypatch) -> None:
    monkeypatch.setenv("USER", "operator-acct")
    monkeypatch.setattr("site_lib.socket.gethostname", lambda: "workstation-box")
    monkeypatch.delenv("SITE_PRIVATE_DENYLIST", raising=False)


def test_privacy_scan_catches_home_user_and_host() -> None:
    home_path = "/" + "home/operator-acct/note"
    assert privacy_failures("workspace ready") == []
    assert any("home" in item for item in privacy_failures(f"see {home_path}"))
    assert any("USER" in item for item in privacy_failures("user operator-acct ran it"))
    assert any("hostname" in item for item in privacy_failures("host WORKSTATION-BOX"))


def test_privacy_denylist(tmp_path: Path, monkeypatch) -> None:
    deny = tmp_path / "deny.txt"
    deny.write_text("# comment\n\nsecret-marker\n", encoding="utf-8")
    assert privacy_failures("clean text", denylist=deny) == []
    assert "denylist" in privacy_failures("leaked secret-marker", denylist=deny)[0]
    monkeypatch.setenv("SITE_PRIVATE_DENYLIST", str(deny))
    assert "denylist" in privacy_failures("leaked secret-marker")[0]


def test_readme_without_status_table_fails() -> None:
    readme = "# MetaMuse CLI\n\nPre-alpha. Nothing is released yet.\n"
    assert readme_status_rows(readme) is None
    assert status_problems(readme, _sample_page()) == ["README has no status table"]


def test_real_site_status_transcripts_and_manifest(monkeypatch) -> None:
    # Use the actual CI identity for this repository check, not the unit fixture.
    monkeypatch.undo()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    page = json.loads((ROOT / "site/page.json").read_text(encoding="utf-8"))
    assert status_problems(readme, page) == []
    assert caption_transcript_problems(ROOT / "site", page) == []
    assert manifest_problems(ROOT / "site") == []


def test_status_capability_inline_code_matches_visible_text() -> None:
    readme = SAMPLE_README.replace("| Search |", "| `Search` |")
    assert status_problems(readme, _sample_page()) == []


@pytest.mark.parametrize("header", ["State", "Status", "狀態"])
def test_status_table_parsing(header: str) -> None:
    rows = readme_status_rows(SAMPLE_README.replace("State", header))
    assert rows == [
        ("Local import", "implemented with offline tests"),
        ("Search", "implemented with offline tests"),
        ("Muse LIVE", "needs a real account (LIVE)"),
        ("MCP attached worker", "not done"),
        ("Publishing", "not done"),
        ("CI", "implemented but not independently accepted"),
    ]
    assert status_problems(SAMPLE_README, _sample_page()) == []


@pytest.mark.parametrize("change", ["state", "order", "duplicate", "column", "zh", "live"])
def test_status_mirror_catches_disagreements(change: str) -> None:
    page = _sample_page()
    works, limits = page["status"]["works"], page["status"]["limits"]
    if change == "state":
        limits[-1]["en"] = "<strong>CI</strong> (<code>implemented with offline tests</code>)."
    elif change == "order":
        works.reverse()
    elif change == "duplicate":
        works[1] = works[0].copy()
    elif change == "column":
        works.append(limits.pop())
    elif change == "zh":
        limits[0]["zh"] = "已完成（<code>implemented with offline tests</code>）。"
    elif change == "live":
        works[0]["en"] += " LIVE is verified."
    assert status_problems(SAMPLE_README, page)


def test_status_rejects_unknown_readme_state() -> None:
    readme = SAMPLE_README.replace("implemented but not independently accepted", "done")
    assert any("unknown state" in problem for problem in status_problems(readme, _sample_page()))


def _workflow() -> str:
    return f"""name: Deploy
permissions:
  contents: read
jobs:
  deploy:
    steps:
      - uses: actions/checkout@{CHECKOUT_SHA}
        with:
          persist-credentials: false
      - uses: actions/configure-pages@{CONFIGURE_PAGES_SHA}
      - uses: actions/upload-pages-artifact@{UPLOAD_PAGES_SHA}
      - uses: actions/deploy-pages@{DEPLOY_PAGES_SHA}
"""


def test_workflow_pins_and_validate_step(tmp_path: Path) -> None:
    workflow = tmp_path / "pages.yml"
    workflow.write_text(_workflow())
    assert pages_workflow_problems(workflow.read_text())
    apply_pages_validate_step(workflow)
    text = workflow.read_text()
    assert KIT_COMMIT in text
    assert pages_workflow_problems(text) == []
    apply_pages_validate_step(workflow)
    assert workflow.read_text() == text
    # A SHA in a comment must not satisfy an unpinned action.
    assert pages_workflow_problems(
        text.replace(f"@{DEPLOY_PAGES_SHA}", f"@v5 # {DEPLOY_PAGES_SHA}")
    )
    assert pages_workflow_problems(
        text.replace("persist-credentials: false", "persist-credentials: true", 1)
    )
    assert pages_workflow_problems(text.replace(f"ref: {KIT_COMMIT}", f"ref: main # {KIT_COMMIT}"))


def _png(width: int, height: int) -> bytes:
    """Tiny valid RGB PNG fixture; no Pillow dependency for these tests."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x14\x21\x1c" * width) * height))
        + chunk(b"IEND", b"")
    )


@pytest.fixture
def image_site(tmp_path: Path) -> tuple[Path, dict]:
    site = tmp_path / "site"
    images = site / "images"
    images.mkdir(parents=True)
    png_bytes = _png(8, 7)
    txt_bytes = b'$ metamuse --workspace ws init\n{"initialized": true}\n'
    manifest = {
        "source_commit": "a" * 40,
        "source_tree": "b" * 40,
        "scale_factor": 2,
        "density_method": "WINIT_X11_SCALE_FACTOR=2",
        "synthetic_data": True,
        "live_or_real_account": False,
        "image_edited": False,
        "crop_note": "Cropped to content with one empty row below.",
        "images": [],
    }
    for stem in ("00-hero", "01-setup"):
        (images / f"{stem}.png").write_bytes(png_bytes)
        (images / f"{stem}.txt").write_bytes(txt_bytes)
        manifest["images"].append(
            {
                "png": f"{stem}.png",
                "transcript": f"{stem}.txt",
                "window_rect": {"x": 0, "y": 0, "width": 8, "height": 9},
                "crop_rect": {"x": 0, "y": 0, "width": 8, "height": 7},
                "cell_px": {"height": 2},
                "content_rows": 2,
                "padding_top_px": 1,
                "lossless_verified": True,
                "decoded_rgb_sha256": "c" * 64,
                "bytes": len(png_bytes),
                "sha256": hashlib.sha256(png_bytes).hexdigest(),
                "transcript_sha256": hashlib.sha256(txt_bytes).hexdigest(),
            }
        )
    (images / "README.md").write_text("Frames: `00-hero.png` and `01-setup.png`.\n")
    (images / "manifest.json").write_text(json.dumps(manifest))
    return site, manifest


def test_manifest_and_caption_pass(image_site) -> None:
    site, _manifest = image_site
    assert manifest_problems(site) == []
    assert caption_transcript_problems(site, _sample_page()) == []


@pytest.mark.parametrize("owner", ["TED-H-com", "ted-h-com", "TeD-h-CoM"])
def test_caption_site_address_lowercases_owner(image_site, owner: str) -> None:
    site, _manifest = image_site
    page = _sample_page()
    page["repo"] = f"{owner}/metamuse-cli"
    assert caption_transcript_problems(site, page) == []


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("repo", "another-owner/metamuse-cli", "https://another-owner.github.io/metamuse-cli/"),
        ("slug", "another-slug", "https://ted-h-com.github.io/another-slug/"),
    ],
)
def test_caption_site_address_must_match_page(
    image_site, key: str, value: str, expected: str
) -> None:
    site, _manifest = image_site
    page = _sample_page()
    page[key] = value
    assert caption_transcript_problems(site, page) == [
        f"transcript site address disagrees with page.json: expected {expected!r}, "
        f"got {TRANSCRIPT_URL_PREFIX.removesuffix('images/')!r}"
    ]


def test_caption_rejects_old_page_address(image_site) -> None:
    site, _manifest = image_site
    page = _sample_page()
    old_owner = "teddashh"
    old_prefix = f"https://{old_owner}.github.io/metamuse-cli/images/"
    caption = page["hero"]["visual"]["caption"]
    caption["en"] = caption["en"].replace(TRANSCRIPT_URL_PREFIX, old_prefix)
    assert caption_transcript_problems(site, page) == [
        f"hero.visual.caption.en must link a transcript under {TRANSCRIPT_URL_PREFIX}"
    ]


@pytest.mark.parametrize(
    "key,value", [("width", 6), ("height", 10), ("x", 1), ("y", 1), ("width", True)]
)
def test_manifest_rejects_incorrect_crop(image_site, key: str, value) -> None:
    site, manifest = image_site
    manifest["images"][0]["crop_rect"][key] = value
    (site / "images/manifest.json").write_text(json.dumps(manifest))
    assert manifest_problems(site)


@pytest.mark.parametrize(
    "change", ["hash", "text", "orphan", "duplicate", "png", "path", "lossless", "rows", "json"]
)
def test_manifest_rejects_invalid_evidence(image_site, change: str) -> None:
    site, manifest = image_site
    images = site / "images"
    record = manifest["images"][0]
    if change == "hash":
        record["sha256"] = "d" * 64
    elif change == "text":
        (images / record["transcript"]).write_text("operator-acct leaked\n")
    elif change == "orphan":
        (images / "obsolete.png").write_bytes(_png(8, 7))
    elif change == "duplicate":
        manifest["images"].append(record.copy())
    elif change == "png":
        (images / record["png"]).write_bytes(b"fake PNG")
    elif change == "path":
        record["png"] = "../outside.png"
    elif change == "lossless":
        record["lossless_verified"] = False
    elif change == "rows":
        record["content_rows"] = 41
    (images / "manifest.json").write_text("invalid" if change == "json" else json.dumps(manifest))
    assert manifest_problems(site)


@pytest.mark.parametrize("change", ["absent", "missing", "wrong", "escape"])
def test_caption_requires_own_existing_transcript(image_site, change: str) -> None:
    site, _manifest = image_site
    page = _sample_page()
    caption = page["showcase"]["shots"][0]["caption"]
    if change == "absent":
        caption["en"] = "Caption without a transcript link."
    elif change == "missing":
        (site / "images/01-setup.txt").unlink()
    elif change == "wrong":
        caption["en"] = caption["en"].replace("01-setup.txt", "00-hero.txt")
    elif change == "escape":
        caption["en"] = caption["en"].replace("01-setup.txt", "../01-setup.txt")
    assert caption_transcript_problems(site, page)
