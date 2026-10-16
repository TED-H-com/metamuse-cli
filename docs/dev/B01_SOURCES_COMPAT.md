# B01 — Upstream sources, licenses, and compatibility

Inspection date: 2026-10-13. This is an adoption plan with pinned commits and versions. It does not mean any upstream program has been copied into `src/`. The offline Muse spike is recorded in [MUSE_SPIKE.md](MUSE_SPIKE.md). Notices are in `THIRD_PARTY_NOTICES.md`.

Clones are shallow (`git clone --depth 1`) and live in a sibling directory outside the repo (for example `../../upstream/` relative to a worktree checkout). SHAs below are `git log -1` on those clones. Each SHA is the default-branch HEAD that GitHub served on the inspection date, not a review of older history.

License text below is what is in the tree at that SHA. Platform permission to read Facebook or to call Muse is a separate question and is not granted by these licenses. See `docs/design/06_SOURCES.md`.

## 1. Public upstreams (PLAN §3)

### nikships/muse-cli

| | |
|---|---|
| URL | https://github.com/nikships/muse-cli |
| Inspected commit | `ebcb6310432ad2d39927bcb59e686330b0538310` |
| Commit date | 2026-10-03T11:59:33-04:00 (`Merge pull request #3 from serrebidev/interactive-chat`) |
| Commit link | https://github.com/nikships/muse-cli/commit/ebcb6310432ad2d39927bcb59e686330b0538310 |
| Package version at that commit | `0.3.2` (`src/muse_cli/__init__.py`) |
| License | SPDX `MIT` |
| License file | `LICENSE` |
| Copyright line | `Copyright (c) 2026 Nik Ships` |

The `LICENSE` file is the standard MIT permission text. Any later copy of a substantial portion of this repo must keep that copyright line and the permission notice.

Relevant files at that commit:

| Path | What it is |
|---|---|
| `docs/PROTOCOL.md` | Transport, Noise XX, protobuf envelope names, history and send quirks. 82 lines. The file says it was derived from the web client bundle and checked live. |
| `src/muse_cli/gateway.py` | HTTPS auth, WebSocket connect, Noise handshake, frame encode/decode, unary `request`, subscriptions. |
| `src/muse_cli/cli.py` | `cmd_send`, `cmd_history`, `is_reply`, `cmd_session_start`, `cmd_raw`. |
| `src/muse_cli/chat.py` | Interactive send/history; same reply discriminator as `is_reply`. |
| `src/muse_cli/routes.json` | 258 `{method, http, path}` entries. Loaded and counted in this pass. |
| `src/muse_cli/desc0.bin` | File descriptor `noise_transport.proto`, package `ingress_rev_proxy`. sha256 `5479a20cd5a9bf817c2a7ba4eec56b354d8576f565d9096fd2a3a8223e820d00`. |
| `src/muse_cli/desc1.bin` | File descriptor `noise_envelope.proto`, package `hatch.noise`. sha256 `5ea3f209386a4d47d28ee06473a2defad35536725bf2d3d847d31b04ec1a8868`. |
| `src/muse_cli/update.py` | PyPI update check. Out of scope. |
| `pyproject.toml` | Depends on `curl_cffi>=0.11`, `noiseprotocol>=0.3`, `protobuf>=5.0`. |

May adopt, under MIT, with the copyright notice, the pinned commit, and a change summary:

- The minimal auth / session / send / history route set and the gateway framing described in `docs/PROTOCOL.md`.
- Field numbers read out of `desc0.bin` and `desc1.bin` (they are not written in `PROTOCOL.md`). The spike restates them in a new codec. The binaries are not vendored.
- The Noise pattern name `Noise_XX_25519_AESGCM_SHA256` with empty handshake payloads, implemented by `noiseprotocol`, not by new cryptography.

Do not adopt:

- The full CLI, `site/`, `skills/muse-cli/`, or `update.py`.
- `cmd_raw` and the other routes in `routes.json` (258 methods). PLAN §3: do not move the whole CLI, the updater, or the raw routes, and do not modify the Muse harness.
- Cookie export via Chrome remote debugging (`cli.py` `cmd_auth_export`). That stays an operator step.
- `curl_cffi` `impersonate="chrome"` as something to reimplement. B09 may depend on `curl_cffi` for the live socket. This spike does not open that socket.
- Any account, cookie, VM id, or token. None were present in the clone and none were used.

### tlyu0419/facebook_crawler

| | |
|---|---|
| URL | https://github.com/tlyu0419/facebook_crawler |
| Inspected commit | `82ec3ec46aa0a324252bbb6274b57cbf29c27e6e` |
| Commit date | 2025-02-16T12:50:33+08:00 (`Update`) |
| Commit link | https://github.com/tlyu0419/facebook_crawler/commit/82ec3ec46aa0a324252bbb6274b57cbf29c27e6e |
| License | SPDX `MIT` |
| License file | `LICENSE` |
| Copyright line | `Copyright (c) 2021 tlyu0419` |

Relevant functions (public post collection, not comment bodies):

| Path | Symbol | Role |
|---|---|---|
| `main.py` | `Crawl_PagePosts` | Page crawl from `until_date`, cursor in, dataframe out. |
| `main.py` | `Crawl_GroupPosts` | Calls `Crawl_PagePosts`. |
| `main.py` | `Crawl_PageInfo`, `Crawl_RelatedPages` | Page metadata and related-page walk. |
| `requester.py` | `_get_homepage`, `_get_posts` | HTTP. `_get_posts` sends a cursor to either the no-JS page-units URL or a GraphQL `doc_id` body. |
| `paser.py` | `_parse_entryPoint`, `_parse_identifier`, `_parse_docid` | Choose a parse path from the homepage HTML. |
| `paser.py` | `_parse_composite_graphql`, `_parse_composite_nojs` | Turn one response into rows plus the next cursor and a max date. |
| `paser.py` | `_parse_edge` | One GraphQL edge: actor name and id, `creation_time`, message text, post id, comment/reaction/share counts, attachment title and photo URIs, cursor, permalink. |
| `utils.py` | `_init_request_vars`, `_extract_id` | Cursor seed and id parsing. |

`README.md` at this commit says collection needs no login and works for public pages and groups. PLAN §3 says not to inherit “logged out and always available” or the old retry behavior. `Crawl_PagePosts` retries on a bare `except`, sleeps 20 seconds, refreshes headers, and after 15 failures tells the operator to change IP (`main.py`). Do not copy that control flow.

The FAQ in `README.md` says comment and reply collection is not in this package (it asks the reader to email the author). Counts of comments are parsed. Comment text is not.

May adopt, under MIT, with the copyright notice and this commit:

- The idea of paging newest-to-older with an opaque cursor and stopping at a date.
- The field list a post row needs: author, time, text, native post id, permalink, reaction/comment/share counts, attachment URLs.
- Small, reviewed parsing helpers after a human checks they still match a fixture. HTML and GraphQL shapes from 2025-02-16 are expected to be stale (E_SELECTOR_DRIFT), so B13 should treat them as a checklist, not as drop-in code.

Do not adopt: the retry-and-change-IP loop, the claim that logged-out access keeps working, sample notebooks and images, or `requirements.txt` pins (`requests==2.24.0` and the rest are far behind a Python 3.12 baseline).

### boo283/Facebook_comment_crawler

| | |
|---|---|
| URL | https://github.com/boo283/Facebook_comment_crawler |
| Inspected commit | `7900f75e03345e918680296b3f703cfb531c19f1` |
| Commit date | 2024-10-30T14:11:58+07:00 (`Update action.py`) |
| Commit link | https://github.com/boo283/Facebook_comment_crawler/commit/7900f75e03345e918680296b3f703cfb531c19f1 |
| License | **no LICENSE file found** |
| License file | absent at the repository root and one directory down |
| Copyright line | none in a license file |

`README.md` says “Feel free to adjust my code, practice makes perfect”. That sentence is not an SPDX license and is not permission to copy the program into a public package. PLAN §3 and `06_SOURCES.md`: do not copy this repo and do not mechanically translate it until the author grants a license.

Files inspected only to name the neutral requirements they imply:

| Path | Symbols | Requirement they illustrate |
|---|---|---|
| `crawl.py` | `crawl` | One post URL in, comments out. |
| `configuration/action.py` | `show_more_comments`, `show_all_replies` | Comments and replies are paged by expanding the page. |
| `configuration/action.py` | `get_comments` | The appended dict has `id`, `username`, `text`, `tag_name`, and `is_spam` only. It has no parent-post or parent-comment field. |
| `configuration/config.py` | `login_facebook`, `configure_driver` | Selenium login. Credentials do not belong in source. |
| `requirements.txt` | `selenium==4.19.0` | Browser automation, not an HTTP API. |

`filter_spam` in `action.py` matches a hard-coded keyword list. That list is not our classifier and must not be copied.

May adopt: nothing of the source. Parent linkage is a MetaMuse requirement, not an upstream row field: a reply must record which comment or post it belongs to. `show_all_replies` only clicks through the page until a threshold or a short idle timeout; it does not emit that link. Author display, text, an optional mention (`tag_name`), expansion until a limit, and an explicit partial result when expansion stops are the rest of the requirement, written in our own words. B13 implements those against an operator-supplied Playwright storage state (see below), for one approved page type.

### Hao0321/claude-skill-social-post

| | |
|---|---|
| URL | https://github.com/Hao0321/claude-skill-social-post |
| Inspected commit | `e0720ed234085d9d41074735b409f2c17aaa3888` |
| Commit date | 2026-10-03T13:59:25+08:00 (`feat: isolate social learning and lock reviewed author contracts`) |
| Commit link | https://github.com/Hao0321/claude-skill-social-post/commit/e0720ed234085d9d41074735b409f2c17aaa3888 |
| License | SPDX `MIT` |
| License file | `LICENSE` |
| Copyright line | `Copyright (c) 2026 Hao0321 contributors` |

May adopt as patterns, under MIT, with the copyright notice. Do not vendor the skill, the formula archive, or the governance docs. The functions below were read for their contracts:

| Path | Symbol | Pattern |
|---|---|---|
| `social-post/scripts/social_store.py` | `store_revision` | Revision is a hash of the store bytes. |
| `social-post/scripts/social_store.py` | `commit_records` | Write only when `expected_revision` still matches. Reject a stale read-modify-write. |
| `social-post/scripts/social_learning.py` | `review` | `owner_confirmed` is required. The decision is approve or reject of one `proposal_digest`. A digest or base-revision mismatch is refused. |
| `social-post/scripts/social_learning.py` | `activate` | A second explicit owner action. Activation checks the latest review. |
| `social-post/scripts/log_outcome.py` | `append_snapshot` | A snapshot id is unique. A new post id needs both the post and the snapshot. A later snapshot does not rewrite the post row. |
| `social-post/scripts/social_validation.py` | `validate_snapshots`, `validate_metric_qualifiers` | Objective checks on numbers, percents, and evidence fields. |

Our drafts already follow SPEC §7: host-supplied text, `expected_revision`, no persona. Metrics, when they exist (SDS P1 `metric_snapshots`), keep the original value, definition, time, surface, and null. The functions above are a concrete picture of digest-bound approval and append-only snapshots. They are not code to paste: `review` and `activate` are wired to that repo’s author-contract proposals (`social_writing_contract.py` `check_draft` takes a formula id and a writing revision).

Do not copy, quote into the product, or translate:

- Author voice and persona: `social-post/voice_quick.md`, `social-post/style_profile.example.md`, `social-post/references/learn_style.md`, `social-post/scripts/social_learning.py` as a style learner.
- Formulas, hooks, CTAs, “best time”, and viral patterns: `social-post/references/formulas/` (`F01.md`–`F05.md` and `formulas.md`), `social-post/scripts/workbench_formulas.py`.
- The private governance system: `GOVERNANCE.md`, `social-post/references/writing-governance.md`, `social-post/references/rules/`, CODEOWNERS and the contribution machinery. PLAN §7 says we do not clone that governance stack.
- `social-post/data/` and any private observations. Not inventoried as product input.

### castle_fb_crawl

Experience only, nothing imported. The wrapper is the user’s private code and was not in this workspace. No commit, no license file, and no path were inspected. PLAN §3 keeps the lesson (video URLs go through yt-dlp into the shared jobs and assets) and drops the old `links.txt` queue, the wipe-everything-on-failure behavior, and private deploy data.

## 2. Dependency pins

Versions are `info.version` from `https://pypi.org/pypi/<project>/json` fetched on 2026-10-13, unless a row says the value came from an installed wheel. This branch does not edit `pyproject.toml` or `uv.lock`. B02 should pin these. “Why” is the mature library SDS §1 already chose, checked against current releases.

| Pin | License | Python | Why this one | Incompatibility / limit |
|---|---|---|---|---|
| CPython **3.12** baseline. uv on this machine has CPython **3.12.13**. | PSF-2.0 | — | SDS §1 first reference runtime. | `python3` on this machine is **3.14.4**. Do not write 3.14-only syntax. The spike runs on 3.12. |
| `pydantic==2.14.1` | MIT (`license_expression`) | `>=3.10` | Models and JSON Schema for MCP tools. v2 only. | `mcp==2.3.0` requires `pydantic>=2.12.0`. A v1 pin will not load. |
| `platformdirs==4.13.0` | MIT | `>=3.11` | Private data/config/cache root (SDS §2). | Does not support the 3.10 line. Fine on 3.12. |
| `mcp==2.3.0` (uploaded 2026-10-02T22:06:52Z) | MIT | `>=3.10` | Official MCP Python SDK. Wire and protocol versions stay in the SDK (SDS §11). | Requires `httpx2>=2.10.0` and `pydantic>=2.12`. On Python 3.14 it requires `anyio>=4.10` and `starlette>=0.48`; on 3.12 the lower bounds in the wheel apply. |
| `yt-dlp==2026.8.19` (imported version string `2026.08.19`) | Unlicense | `>=3.10` | Remote media metadata and download. Do not shell out to the CLI. | Extractor breakage is upstream drift, not a pin bug. Plugins and config: see below. |
| `playwright==1.63.0` | Apache-2.0 | `>=3.10` | Authorized browser session for one Facebook page type. | The pip package does not include a browser. |
| `noiseprotocol==0.3.1` (uploaded 2020-03-03) | MIT. `Copyright (c) 2017 Piotr Lizończyk`, package `LICENSE`. | metadata says `~=3.5` | Noise_XX for the Muse gateway. Same library muse-cli declares (`noiseprotocol>=0.3`). | Last release is 2020. `noise.constants.MAX_MESSAGE_LEN` is 65535 and the check is on the ciphertext. Plaintext that still round-trips is 65519 bytes because the AES-GCM tag is 16 bytes. A Noise_XX handshake completed on CPython 3.12 during the spike tests. Re-check on the next bump. Do not replace it with hand-written Noise. |
| `cryptography==50.0.2` | Apache-2.0 OR BSD-3-Clause | `>=3.9`, excluding 3.9.0 and 3.9.1 | Transitive dependency of `noiseprotocol`. X25519, AES-GCM, and SHA-256 live here. | Pin only if B02 wants a direct lock. Resolved version observed via `uv run --python 3.12 --with noiseprotocol==0.3.1`. |
| `protobuf==7.36.2` (uploaded 2026-09-17) | BSD-3-Clause | `>=3.10` | Optional for B09 if it loads `desc0.bin` / `desc1.bin` instead of the spike codec. | The spike codec does not import it. Byte comparison on 2026-10-13 used this release. |
| `curl_cffi==0.16.3` (uploaded 2026-09-02) | MIT | `>=3.10` | B09 live transport. `PROTOCOL.md` says plain TLS to the gateway is reset and a Chrome fingerprint is required, with certificate verification left on. | Not used by the offline spike. No live call in this pass. |

`websockets` 17.2 (BSD-3-Clause) was looked up and **not** selected. The documented gateway path is `curl_cffi`’s WebSocket with `impersonate="chrome"` and `verify=True` (`gateway.py` in the pinned muse-cli commit). A standard-library or `websockets` client would not apply that fingerprint.

ffmpeg is an external binary, not a Python dependency and not bundled. On the inspection host, `ffmpeg -version` reported `ffmpeg version 8.0.1-3ubuntu2`, configured with `--enable-gpl` and `--enable-libx264` among other libraries. The project states that FFmpeg itself is LGPL-2.1-or-later, and that GPL-2.0-or-later applies to the whole build when GPL parts are used ([ffmpeg.org/legal.html](https://ffmpeg.org/legal.html), fetched 2026-10-13). That page names libx264 as a GPL library. This machine’s binary is a GPL build. MetaMuse invokes it as a separate process and does not vendor the binary or its libraries. Missing ffmpeg is `E_DEPENDENCY_MISSING` for media jobs and must not block offline import.

SQLite is the stdlib `sqlite3` module. WAL behavior is [sqlite.org/wal.html](https://sqlite.org/wal.html), already cited by `06_SOURCES.md`. No separate pin.

### yt-dlp from Python, without the shell

Read from the `yt-dlp==2026.8.19` wheel unpacked by uv (not executed against a URL):

- `YoutubeDL(params)` does not call `load_configs()`. Config files are loaded by the CLI parser in `yt_dlp/options.py` (`load_configs`, `--ignore-config` / `dest='ignoreconfig'`). Using the Python API therefore does not inherit `~/.config/yt-dlp` config. Do not invoke the `yt-dlp` executable.
- `YoutubeDL.__init__` calls `load_all_plugins()` when plugins are not loaded yet (`YoutubeDL.py` around the `all_plugins_loaded` check). `yt_dlp/plugins.py` skips that load when `YTDLP_NO_PLUGINS` is a non-empty environment variable or when `plugin_dirs.value` is empty.
- The CLI flag `--no-plugin-dirs` sets `plugin_dirs` to `[]` (`options.py`). The Python equivalent, before the first `YoutubeDL()`, is `yt_dlp.globals.plugin_dirs.value = []` or `os.environ["YTDLP_NO_PLUGINS"] = "1"`.
- Pass `ffmpeg_location` in the params dict to the operator-installed binary. `extract_info(url, download=False)` is the metadata call. Download only after the job’s byte budget says so (SPEC limits).

### Playwright storage state

[Playwright Python auth](https://playwright.dev/python/docs/auth), fetched 2026-10-13: `context.storage_state(path=...)` writes cookies, local storage, IndexedDB, and virtual WebAuthn credentials; `browser.new_context(storage_state=...)` restores them. The same page says the file can impersonate the account and must not be committed. SPEC §8: the operator points at a file. The tool does not read a default Chrome profile.

Browser install is a manual operator step (`playwright install` for the browsers that package documents). This pass did not download a browser. Doctor should report a missing browser as `E_DEPENDENCY_MISSING` and leave offline commands usable (SDS §1).

### MCP SDK 2.3.0

Sources: the `mcp==2.3.0` wheel imported with `uv run --with mcp==2.3.0` on 2026-10-13, and the tools page of protocol `2026-07-28` at <https://modelcontextprotocol.io/specification/2026-07-28/server/tools>.

`mcp_types.version` in that wheel:

| Constant | Value |
|---|---|
| `SUPPORTED_PROTOCOL_VERSIONS` | `2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25`, `2026-07-28` |
| `LATEST_PROTOCOL_VERSION` | `2026-07-28` |
| `HANDSHAKE_PROTOCOL_VERSIONS` / `LATEST_HANDSHAKE_VERSION` | through `2025-11-25` |
| `MODERN_PROTOCOL_VERSIONS` | `2026-07-28` only |

stdio: `mcp.server.stdio.stdio_server` is an async context manager. Its docstring says it serves MCP on the process stdin and stdout. While it serves, the implementation rebinds fd 0 and fd 1 so handler prints do not land on the protocol stream. B14 still has to prove our logs go to stderr. The SDK rebind is not a substitute for that test.

`structuredContent` / `outputSchema`:

- The 2026-07-28 spec: `structuredContent` may be any JSON value that matches `outputSchema`. A tool that returns structured content should also return the same JSON as text. `outputSchema` is optional. Clients should validate when it is present. Tool annotations are hints. Clients must not trust annotations from an untrusted server.
- The SDK’s `server/mcpserver/utilities/func_metadata.py` in the 2.3.0 wheel: a `BaseModel` or `TypedDict` return is the schema. Primitives and generic `list` / `dict` / `Union` returns are wrapped in a model whose only field is `result`. A comment in `_inline_root_ref` says `Tool.outputSchema` needs an object root on the wire through `2025-11-25`.
- B14 should return a typed object (our envelope), not a bare list or string, so older and newer hosts see the same top-level fields. Keep a text copy of the same JSON in `content`. `structuredContent` describes our tool result. It does not constrain Muse (SPEC §9).

Tool annotations on `mcp.types.ToolAnnotations`: `title`, `read_only_hint`, `destructive_hint`, `idempotent_hint`, `open_world_hint`. The class docstring says they are hints. `muse_send` and any refresh tool stay without `read_only_hint=True` (SPEC §9, SDS §11).

This pass did not construct an MCP server and did not attach one to a host.

## 3. MCP hosts on this machine

Commands were version queries only. No host config was read or written. No MCP server was added.

| Command | stdout |
|---|---|
| `claude --version` | `2.1.288 (Claude Code)` |
| `codex --version` | `codex-cli 0.160.0` |
| `grok --version` | `grok 1.0.46 (2765805b9442) [stable]` |
| `agy --version` | `1.3.4` |

`agy --help` lists an `mcp` subcommand (“Manage MCP servers”) and was not run further. `claude --help`, `codex --help`, and `grok --help` also mention MCP configuration commands. Those commands were not run.

These versions are not evidence that any host speaks `2026-07-28`, validates `outputSchema`, or keeps stdout clean. SPEC §9: do not claim a host supports a feature until B14 runs it.

## 4. What B14 must verify, per host

Use an ephemeral server invocation. Do not save a server entry into the host’s config as part of the test, and do not pass `--dangerously-skip-permissions` or any equivalent.

For Claude Code 2.1.288, codex-cli 0.160.0, grok 1.0.46, and agy 1.3.4, record the negotiated protocol version if the host exposes it:

1. **stdio cleanliness.** Spawn `metamuse mcp` with stdout and stderr captured separately. Every stdout line parses as one JSON-RPC message. Logs, warnings, download progress, and tracebacks are absent from stdout. A `tools/list` round trip completes.
2. **Tool listing.** `tools/list` includes the original fifteen P0 tools and four experimental publication tools in `docs/design/contracts/mcp-tools.json`, each with `inputSchema` and an object-rooted `outputSchema`. Names match the contract. `muse_send` and refresh are not advertised as read-only. Annotations are recorded and then ignored for authorization decisions.
3. **structuredContent.** Call one local read tool and one erroring tool. Record whether the host shows `structuredContent`, the text `content` block, or both. Compare a host that negotiated `2025-11-25` with one that negotiated `2026-07-28` when both occur. A host that drops `structuredContent` must still show the text copy. A host that validates `outputSchema` must not reject the envelope.
4. **Error surfacing.** An input error and a domain error (`E_SCOPE_REQUIRED`, `E_EXTERNAL_STATE_UNKNOWN`, or similar) reach the operator with `code`, `retry_class`, and `next_action`. An empty list is not used as a hidden failure. Note whether the host surfaces a JSON-RPC error, a tool result with `isError: true`, or both. SPEC’s envelope stays inside the tool result; it is not a second protocol.
5. **State handles.** A second call that omits the project id does not reuse a project chosen on an earlier call of the same stdio session (SPEC §9).

Stop the test if a host would need a config change to continue. Write the transcript paths and the negotiated version into the B14 report. A passing `--help` is not this test.

## 5. Limits of this pass

No Facebook page was fetched. No Muse account was used. No browser was installed. Host versions were not paired with a server. The pins are what PyPI and the local interpreters reported on 2026-10-13.
