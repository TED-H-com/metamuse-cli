# Third-party notices

Inspection date: 2026-10-13. Versions and commits are the ones recorded in `docs/dev/B01_SOURCES_COMPAT.md` and `docs/dev/MUSE_SPIKE.md`. Nothing under `src/` vendors this code. “Adopted in spikes/” means the offline spike imports or tightly restates it. “Planned” means B02 or a later backlog item should pin it. “Not adopted” means inspected and left out.

Full MIT permission text, where a row says MIT, is the license file at the pinned commit or the package metadata. A later copy of a substantial portion of an MIT work must keep that work’s copyright line and permission notice.

| Name | Pin / commit | License | Status |
|---|---|---|---|
| nikships/muse-cli | `ebcb6310432ad2d39927bcb59e686330b0538310` (2026-10-03) | MIT. `Copyright (c) 2026 Nik Ships`. File `LICENSE`. | Adopted in spikes/: framing restated from `docs/PROTOCOL.md` and the field numbers in `desc0.bin` / `desc1.bin`. `gateway.py` was not copied. The descriptor binaries are not in this repo. |
| tlyu0419/facebook_crawler | `82ec3ec46aa0a324252bbb6274b57cbf29c27e6e` (2025-02-16) | MIT. `Copyright (c) 2021 tlyu0419`. File `LICENSE`. | Not adopted. Patterns for post paging and post fields are recorded for B13. No source copied. |
| boo283/Facebook_comment_crawler | `7900f75e03345e918680296b3f703cfb531c19f1` (2024-10-30) | No LICENSE file found. | Not adopted. No copy and no mechanical translation. |
| Hao0321/claude-skill-social-post | `e0720ed234085d9d41074735b409f2c17aaa3888` (2026-10-03) | MIT. `Copyright (c) 2026 Hao0321 contributors`. File `LICENSE`. | Not adopted. Revision, explicit approval, and snapshot patterns are recorded. Voice, formulas, and governance files are out of scope. |
| castle_fb_crawl | not available | private | Not adopted. Experience only, nothing imported. |
| CPython | 3.12 baseline (uv has 3.12.13) | PSF-2.0 | Planned runtime. |
| pydantic | 2.14.1 | MIT | Planned. Not imported here. |
| platformdirs | 4.13.0 | MIT | Planned. Not imported here. |
| mcp (official Python SDK) | 2.3.0 | MIT | Planned. Not imported here. Speaks protocol revisions `2024-11-05` through `2026-07-28`. |
| pydantic, as required by mcp | `>=2.12` (satisfied by 2.14.1) | MIT | Planned, same pin as pydantic. |
| yt-dlp | 2026.8.19 | Unlicense | Planned. Not imported here. |
| playwright | 1.63.0 | Apache-2.0 | Planned. Browser binaries are an operator install, not part of this repo. |
| ffmpeg | external binary; this machine has 8.0.1-3ubuntu2, configured `--enable-gpl` and `--enable-libx264` | LGPL-2.1-or-later, or GPL-2.0-or-later when GPL parts are enabled (this binary) | Not bundled. Operator-installed executable only. |
| noiseprotocol | 0.3.1 | MIT | Adopted in spikes/ (`spikes/muse/handshake.py` via PEP 723). Not vendored. |
| cryptography | 50.0.2, resolved as the dependency of noiseprotocol on 2026-10-13 | Apache-2.0 OR BSD-3-Clause | Adopted in spikes/ only as that transitive dependency. |
| protobuf | 7.36.2 | BSD-3-Clause | Not adopted in the spike. Used on 2026-10-13 to read upstream descriptors and compare bytes. Planned if B09 loads the descriptors instead of the spike codec. |
| curl_cffi | 0.16.3 | MIT | Planned for the B09 live socket. Not used by the spike. |
| websockets | 17.2 looked up, not selected | BSD-3-Clause | Not adopted. The documented gateway client is `curl_cffi` with a Chrome TLS fingerprint. |

SQLite is used through the Python standard library when the store lands (B03). No separate notice pin.
