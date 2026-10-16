# Project page

The public page is `site/page.json`, rendered with the external page kit (`teddashh/teddashh.github.io` at commit `5e51fb954acef56e9f75b4cfd2b57dc76afcdfd8`, MIT). Screenshots use only the shipped CLI (`metamuse`) and MCP (`metamuse mcp`) entrypoints on `main`.

## Render and validate

Set `PAGE_KIT_DIR` to the `kit/` directory of a checkout of `teddashh/teddashh.github.io` at commit `5e51fb954acef56e9f75b4cfd2b57dc76afcdfd8`:

```sh
python3 "$PAGE_KIT_DIR/render.py" .
PAGE_KIT_DIR="$PAGE_KIT_DIR" python3 scripts/check_site.py
```

`check_site.py` requires `PAGE_KIT_DIR` (there is no default local path). It runs `render.py --check`, renders again, and fails if a generated file changes. It also checks local links, transcript links in captions, image and transcript SHA-256 hashes, crop rectangles, bilingual alt text, the README user-task and availability mirror, transcript privacy, and `.github/workflows/pages.yml`.

The page address follows the lowercased owner in `page.json` `repo` and its `slug`.
After a repository move, update `repo`, repository links and caption transcript URLs,
then re-render. The transcript checker requires `TRANSCRIPT_URL_PREFIX` in
`scripts/site_lib.py` to use the same page address; its link regex derives from that
prefix. See the kit README's “When a repository moves” procedure for forwarding at
the old address, which belongs to the kit repository.

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

Every scene command must exit 0 by default. Any unexpected exit, including 5
(partial result or wait timeout), stops the scene before its completion marker,
image or published transcript is written; the capture run fails without writing
a new manifest. The demo transcript names the scene, command position, actual
exit code and whether exit 5 was expected, without expanding command variables.

A scene can set `expected_partial_commands=("exact command from commands",)`.
Each entry must match exactly one command in that scene; duplicate declarations
and ambiguous repeated commands are rejected. The summary must explicitly include
the word `partial`, and the transcript must show that partial result. Validation
runs when the `Scene` is constructed, including at module load. A declared command
must exit exactly 5: exit 0 also fails because it contradicts the declared result.
Use exact strings so inserting or reordering commands cannot move the exception.

The current `02-queue` transcript shows a partial **stored job** (7 committed,
7 rejected). Its `worker run --once` and `jobs get` commands both exit 0 in the
current CLI, so it has no expected-partial exit declaration. The other current
scenes likewise declare none. A stored partial state alone does not establish
that the command exits 5.

Captures first write PNGs and transcripts into a temporary directory under the
demo's `tmp/`, outside `site/images`. Pixel comparisons, sizes and hashes use
those staged files. Any failure before publication, including `SystemExit`, an
exception or `KeyboardInterrupt`, removes staging and leaves the existing
`site/images` file names and bytes untouched. Publication starts only after all
scenes, including the hero, succeed and the capture processes stop.

`_commit_outputs` copies the new frame set into a temporary sibling of
`site/images`, verifies every copied PNG and transcript SHA-256 against its
record, and then writes the new manifest and README there. It renames the old
directory to a unique `.images-backup-*` sibling, then renames the complete new
directory to `images`. This replaces the entire set, including obsolete frames.
Before the renames, the prepared directory gets the old `images` directory's
permission bits. On initial publication, a plain `mkdir` probe determines the
mode under the current umask; the capture process does not change its umask.
A caught failure between renames restores the backup; if restoration also fails,
the error names the retained backup for manual recovery. No mixed directory or
manifest pointing at missing frames is published. After successful publication,
the old backup is deleted; a cleanup failure is reported with the new complete
set still in place.
An interruption just after the second rename is reported as committed and names
the retained backup; an initial publication reports that there is no backup.

The two renames are individually atomic, but their pair is not: readers can
briefly see `images` absent. An uncatchable interruption (such as SIGKILL or power
loss) in that gap can leave `images` absent, the complete old set in
`.images-backup-*`, and the prepared new set in `.images-stage-*`. Restore the
backup to `images` before retrying. After the second rename, interruption can
leave the complete new `images` and an old backup. Uncatchable interruption can
also leave demo staging behind. There is no filesystem durability guarantee
across power loss (no fsync), and captures must run with a single writer.

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
lists 19 tools, imports a local synthetic file and polls `metamuse_job_get` to a
terminal state. It never invokes a manual worker. The worker belongs to the MCP
server process; closing the server does not promise background continuation.

## Keep user-facing availability in sync

The homepage describes user tasks and setup requirements, such as searching
saved comments or exporting an editable draft. It does not display the developer
verification inventory, CI checks, protocol details or acceptance test IDs.
That inventory lives in [STATUS.md](STATUS.md).

The `What can I use today?` table in the README is the source for the homepage's
ordered task names and availability. The page's bilingual `availability` metadata
uses these four setup states:

- Ready with local files.
- Connect your assistant first.
- Needs extra setup; real-account use is unverified.
- Not available.

The first two belong in `works`; the others belong in `limits`. This metadata
is checked without displaying status codes in the public copy. Each task's
visible text explains the action, result and any setup it needs in plain language.
The introduction states that Muse and remote collection have not been verified
with real accounts.

When a user-task row changes, update its matching row and availability metadata
in `site/page.json` in both languages, re-render, and run `scripts/check_site.py`
with `PAGE_KIT_DIR` set. The technical inventory is maintained separately as
implementation evidence, not as a list of product features.

`tests/test_site_check.py::test_real_site_status_transcripts_and_manifest` checks
the real README/page task names, availability and column placement, along with
transcripts and image hashes. Unit checks reject changed states, reordered or
duplicate tasks, wrong columns, translation mismatches and false LIVE claims.

## Visual review

Run the kit's `shoot.py` against the rendered directory, then capture the entire
page at 1440 px and 390 px in English and Traditional Chinese. The kit's fixed
window can stop before the footer and omits English mobile. Review every chunk,
including the hero, every frame, status and footer. Keep review captures
outside the repository. Browser traffic must remain on loopback for this task.
