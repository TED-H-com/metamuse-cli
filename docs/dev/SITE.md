# Project page

The public page is `site/page.json`, rendered with the external page kit (`teddashh/teddashh.github.io` at commit `d6d067412279ab61d73151e836722799662edb11`, MIT). Screenshots use only the shipped CLI (`metamuse`) and MCP (`metamuse mcp`) entrypoints on `main`.

## Render and validate

Set `PAGE_KIT_DIR` to the `kit/` directory of a checkout of `teddashh/teddashh.github.io` at commit `d6d067412279ab61d73151e836722799662edb11`:

```sh
python3 "$PAGE_KIT_DIR/render.py" .
PAGE_KIT_DIR="$PAGE_KIT_DIR" python3 scripts/check_site.py
```

`check_site.py` requires `PAGE_KIT_DIR` (there is no default local path). It runs `render.py --check`, renders again, and fails if a generated file changes. It also checks local links, transcript links in captions, image and transcript SHA-256 hashes, crop rectangles, bilingual alt text, the row-by-row README status mirror, transcript privacy, and `.github/workflows/pages.yml`.

The kit writes `pages.yml` only when the file is absent. After the first render, add the validate step if it is missing:

```sh
python3 -c 'from pathlib import Path; import sys; sys.path.insert(0, "scripts"); from site_lib import apply_pages_validate_step; apply_pages_validate_step(Path(".github/workflows/pages.yml"))'
```

## Capture

Screenshots are real Alacritty windows on a free Xvfb display, rendered at 2x pixel density (`WINIT_X11_SCALE_FACTOR=2`) and cropped from the top of the window to one empty row below the last non-empty row while keeping the full window width. The installed console script is `metamuse`. The demo directory is `/tmp/metamuse-demo` with `HOME` inside it.

```sh
UV_OFFLINE=1 uv sync --python 3.12
uv run --script scripts/capture_site.py
```

`capture_site.py` pins Pillow in its PEP 723 header. It refuses any transcript that contains `$USER`, the machine hostname, a home-directory path, or a line from `$SITE_PRIVATE_DENYLIST`. It records both `window_rect` and `crop_rect` per image in `site/images/manifest.json` and verifies that lossless PNG compression preserves exact decoded pixels.

Showcase frames use 98 columns and at most 40 content rows. The dedicated hero
uses 60 columns and an 18-row packet preview. Commands retain shell variables
set visibly in earlier frames and take no more than two terminal rows. The
transcripts remove ANSI colour and cursor-control sequences only.

`manifest.json` records the source commit/tree, product base, capture script hash,
synthetic input hashes and the explicit shortening of the first synthetic URL
for the hero. Per-image fields include commands, AT IDs, columns, window and
content rows, font size, cell size, top padding, display-coordinate window/crop
rectangles, byte count, file and transcript hashes, decoded RGB hash and the
lossless comparison result. The checker verifies the full file set, PNG header
and dimensions, crop geometry, row budget, hashes and matching transcript links.

The scene list follows the README's tested offline walkthrough: packet preview,
grant-free approval review, human-only `permit approve`, fixture questions and a
follow-up in the same discussion, cached reads, host draft and local export.
The terminal operator input is scripted for this synthetic demo and recorded as
such in the manifest. The open approval frame shows the real stderr review,
including expiry and one use. Later send/read reviews go to demo `out/` files to
keep the two Muse frames compact. Product operations use shipped commands only;
there is no direct application/database access or model-generated grant.

The official MCP stdio client starts the server with its default attached worker,
lists 15 tools, imports a local synthetic file and polls `metamuse_job_get` to a
terminal state. It never invokes a manual worker. The worker belongs to the MCP
server process; closing the server does not promise background continuation.

## Keep status in sync

The status columns contain exactly the README's rows and four-state vocabulary,
preserving README order within each column. Traditional Chinese translates the
capability and note while retaining the same state. Public copy describes the
product and its limits, without branch names or planned capture rounds.

When a README status row changes:

1. Update the matching row in `site/page.json` in both languages.
2. Re-render with `python3 "$PAGE_KIT_DIR/render.py" .`.
3. Run `PAGE_KIT_DIR="$PAGE_KIT_DIR" python3 scripts/check_site.py`.

`tests/test_site_check.py::test_real_site_status_transcripts_and_manifest` reads
the real README, page source, transcripts and manifest without the kit, display
tools or network. It runs in the required CI `check` through the normal pytest
suite. A README state change without its matching page update fails that test.

## Visual review

Run the kit's `shoot.py` against the rendered directory, then capture the entire
page at 1440 px and 390 px in English and Traditional Chinese. The kit's fixed
window can stop before the footer and omits English mobile. Review every chunk,
including the hero, every frame, status and footer. Keep review captures
outside the repository. Browser traffic must remain on loopback for this task.
