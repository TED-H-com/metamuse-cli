# Third-party notices

Inspection date: 2026-10-13. Versions and commits are the ones recorded in `docs/dev/B01_SOURCES_COMPAT.md` and `docs/dev/MUSE_SPIKE.md`. Nothing under `src/` vendors this code. “Adopted in spikes/” means the offline spike imports or tightly restates it. “Planned” means B02 or a later backlog item should pin it. “Not adopted” means inspected and left out.

## muse-cli

Commit `ebcb6310432ad2d39927bcb59e686330b0538310` (2026-10-03). The MIT license at `LICENSE` in that tree requires the copyright line and this permission notice to travel with the portions restated below:

```text
MIT License

Copyright (c) 2026 Nik Ships

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

| File | Upstream paths at that commit | What changed |
|---|---|---|
| `spikes/muse/framing.py` | `docs/PROTOCOL.md`; field numbers in `src/muse_cli/desc0.bin` and `desc1.bin` | Historical codec. `gateway.py` was not copied. The descriptor binaries are not in this repo. Plaintext frames are capped at 65519 bytes. |
| `spikes/muse/associate.py` | `src/muse_cli/cli.py` `is_reply` and `fmt_event`; `src/muse_cli/chat.py` `Chat.new_replies`; `src/muse_cli/gateway.py` `call_json` (`ok is False`) | Historical association rules. Tied to this exchange’s user turn. A conflicting marker wins over identical text. Ambiguity is cumulative and clears only for a competing candidate. Only a calibrated status completes. `ok: false` and a non-2xx status are attempt evidence `app_error`; the exchange stays `send_uncertain` until this exchange’s user echo promotes it to `sent`. Nothing is resent. The truthy status check is not adopted. |
| `spikes/muse/loop.py` | `src/muse_cli/gateway.py` `_read_frame` and `Gateway.request` | Historical receive loop. Ownership is on the link. The deadline uses `time.monotonic`. A frame budget was added. |
| `spikes/muse/handshake.py` | `docs/PROTOCOL.md` Noise_XX pattern and empty payloads | Historical in-process handshake. Uses the `noiseprotocol` library. Does not copy the gateway handshake code. |
| `src/metamuse/_muse_codec.py` | Same descriptor field numbers as `spikes/muse/framing.py` | Production codec. Unknown field, multi-chunk, and oversize frames are `E_PROTOCOL_MISMATCH`. |
| `src/metamuse/_muse_associate.py` | Same muse-cli behaviors as `spikes/muse/associate.py` | Production pure functions. The spike rules are unchanged. |
| `src/metamuse/_muse_link.py` | `src/muse_cli/gateway.py` `_read_frame` and `Gateway.request` | Production receive loop. One owner. Injected monotonic deadline. 64-frame collect budget. |
| `src/metamuse/_muse_noise.py` | `docs/PROTOCOL.md` and `src/muse_cli/gateway.py` initiator, empty payloads, pattern name | Uses `noiseprotocol`. Does not copy the client handshake. |
| `src/metamuse/_muse_http.py` | `src/muse_cli/gateway.py` HTTPS helpers and `WebSocket.connect(..., verify=True)` | New `curl_cffi` adapter. `impersonate="chrome"`, certifi CA bundle, hostname verification, redirects off, empty `CURLOPT_PROXY`, `CURLOPT_RESOLVE` for the checked hostname. Not a copy of `gateway.py`. |
| `src/metamuse/muse.py` | `src/muse_cli/gateway.py` auth chain and the four routes in `routes.json` | Allowlisted transport and attempt evidence. No discussion lifecycle. `gateway.py` was not copied. |

## noiseprotocol

The spike imports `noiseprotocol` 0.3.1. It does not vendor the library. The package `LICENSE` read from that release:

```text
MIT License

Copyright (c) 2017 Piotr Lizończyk

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Inventory

| Name | Pin / commit | License | Status |
|---|---|---|---|
| nikships/muse-cli | `ebcb6310432ad2d39927bcb59e686330b0538310` (2026-10-03) | MIT. `Copyright (c) 2026 Nik Ships`. Full notice above. | Adopted in spikes/. See the file table above. |
| tlyu0419/facebook_crawler | `82ec3ec46aa0a324252bbb6274b57cbf29c27e6e` (2025-02-16) | MIT. `Copyright (c) 2021 tlyu0419`. File `LICENSE`. | Post field checklist (`paser.py` `_parse_edge`) referenced in `src/metamuse/facebook.py`. Full MIT notice included below. No crawler loop or source copied. |
| boo283/Facebook_comment_crawler | `7900f75e03345e918680296b3f703cfb531c19f1` (2024-10-30) | No LICENSE file found. | Not adopted. No copy and no mechanical translation. |
| Hao0321/claude-skill-social-post | `e0720ed234085d9d41074735b409f2c17aaa3888` (2026-10-03) | MIT. `Copyright (c) 2026 Hao0321 contributors`. File `LICENSE`. | Not adopted. Revision, explicit approval, and snapshot patterns are recorded. Voice, formulas, and governance files are out of scope. |
| castle_fb_crawl | not available | private | Not adopted. Experience only, nothing imported. |
| CPython | 3.12 baseline (uv has 3.12.13) | PSF-2.0 | Planned runtime. |
| pydantic | 2.14.1 | MIT | Adopted as a runtime dependency. See the Python package dependencies table below. |
| platformdirs | 4.13.0 | MIT | Adopted as a runtime dependency. See the Python package dependencies table below. |
| mcp (official Python SDK) | 2.3.0 | MIT | Adopted as a runtime dependency. See the Python package dependencies table below. Speaks protocol revisions `2024-11-05` through `2026-07-28`. |
| pydantic, as required by mcp | `>=2.12` (satisfied by 2.14.1) | MIT | Satisfied by the pydantic pin above. |
| yt-dlp | 2026.8.19 (imported version `2026.08.19`) | Unlicense | Adopted as the optional extra `remote` and a dev-group pin. Imported lazily by `src/metamuse/remote_media.py`. Not vendored. |
| playwright | 1.63.0 | Apache-2.0 | Adopted as optional extra (`facebook`) and dev dependency. Imported lazily by `src/metamuse/facebook.py`. Browser binaries are an operator install, not part of this repo. |
| ffmpeg | external binary; this machine has 8.0.1-3ubuntu2, configured `--enable-gpl` and `--enable-libx264` | LGPL-2.1-or-later, or GPL-2.0-or-later when GPL parts are enabled (this binary) | Not bundled. Operator-installed executable only. |
| noiseprotocol | 0.3.1 | MIT. `Copyright (c) 2017 Piotr Lizończyk`. Full notice above. | Adopted. Imported by `src/metamuse/_muse_noise.py` and `spikes/muse/handshake.py`. Not vendored. |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause. Wheel `LICENSE` offers either `LICENSE.APACHE` or `LICENSE.BSD`. | Runtime transitive of `noiseprotocol` and `curl_cffi`. The installed wheel reports `OpenSSL 4.0.3 29 Sep 2026` (OpenSSL 3+ is Apache-2.0). Not vendored. |
| cffi | 2.1.1 | MIT-0 (MIT No Attribution). Wheel `LICENSE`. | Runtime transitive of `cryptography` and `curl_cffi`. |
| pycparser | 3.11 | BSD-3-Clause. `Copyright (c) 2008-2027, Eli Bendersky`. Wheel `LICENSE`. | Runtime transitive of `cffi`. |
| certifi | 2026.7.22 | MPL-2.0. Wheel `LICENSE` (Mozilla CA bundle). | Runtime transitive of `curl_cffi`. |
| protobuf | 7.36.2 | BSD-3-Clause | Not adopted. Used on 2026-10-13 to read upstream descriptors. B09 kept the hand-written codec and does not depend on this package. |
| curl_cffi | 0.16.3 | MIT. `Copyright (c) 2018 multippt`. `Copyright (c) 2022 curl_cffi developers`. Wheel `LICENSE`. Home: https://github.com/lexiforest/curl_cffi | Adopted. Imported lazily by `src/metamuse/_muse_http.py`. Not vendored. |
| libcurl | 8.21.0-IMPERSONATE | curl license (MIT-style). `Curl().version()` prints `libcurl/8.21.0-IMPERSONATE`. | Statically linked in `curl_cffi/_wrapper.abi3.so`. Not a separate Python package. |
| BoringSSL | numeric version not exposed | Google BoringSSL (OpenSSL license and ISC). The version string names BoringSSL and does not print a number. | Statically linked in `curl_cffi/_wrapper.abi3.so`. |
| zlib | 1.3.1 | zlib license. `Copyright (C) 1995-2024 Jean-loup Gailly and Mark Adler`. `Curl().version()` prints `zlib/1.3.1`. | Statically linked in `curl_cffi/_wrapper.abi3.so`. |
| brotli | 1.2.0 | MIT. `Curl().version()` prints `brotli/1.2.0`. | Statically linked in `curl_cffi/_wrapper.abi3.so`. |
| zstd | 1.5.7 | BSD-3-Clause for the bundled build. `Curl().version()` prints `zstd/1.5.7`. | Statically linked in `curl_cffi/_wrapper.abi3.so`. |
| libidn2 | 2.3.7 | The installed C library is dual-licensed LGPLv3+ or GPLv2+. The rest of the libidn2 package is GPLv3+. `Copyright (C) 2011-2024 Simon Josefsson`. `Curl().version()` prints `libidn2/2.3.7`. | Statically linked in `curl_cffi/_wrapper.abi3.so`. `readelf -d` lists no external libidn2. Not a system library. |
| nghttp2 | 1.63.0 | MIT. `Curl().version()` prints `nghttp2/1.63.0`. | Statically linked in `curl_cffi/_wrapper.abi3.so`. |
| ngtcp2 | 1.20.0 | MIT. `Copyright (c) 2016 ngtcp2 contributors`. `Curl().version()` prints `ngtcp2/1.20.0`. | Statically linked in `curl_cffi/_wrapper.abi3.so`. |
| nghttp3 | 1.15.0 | MIT. `Curl().version()` prints `nghttp3/1.15.0`. | Statically linked in `curl_cffi/_wrapper.abi3.so`. |
| Unicode data / gnulib unistring | version string not exposed | Unicode, Inc. Copyright 1991-2016, Unicode Terms of Use (Unicode-DFS-2016), as in libidn2 `COPYING.unicode` at tag v2.3.7. The wheel contains gnulib unistring symbols (`u8_*`, `u32_*`, `uc_*`, `uninorm_nfc`, `uninorm_nfd`) from libidn2's in-tree unistring replacement. | Bundled inside libidn2 in the same `.so`. No separate libunistring version is exposed. This notice does not invent one. |
| websockets | 17.2 looked up, not selected | BSD-3-Clause | Not adopted. The gateway client is `curl_cffi` with a Chrome TLS fingerprint. |

SQLite is used through the Python standard library when the store lands (B03). No separate notice pin.

## Python package dependencies (`src/metamuse`)

The package uses normal installed dependencies. No source from the crawler, Muse client,
content skills or other third-party repositories was copied or mechanically rewritten.
There are no adopted source commits/original paths to list for this change. Exact
package versions, registry archive URLs and archive hashes are recorded in `uv.lock`;
upstream dependency license files remain included in their distributions.

Runtime dependencies:

| Dependency | Locked version | License | Reason |
|---|---|---|---|
| pydantic | 2.14.1 | MIT | Mature typed validation and JSON Schema generation; avoid hand-built validators. |
| platformdirs | 4.13.0 | MIT | Mature OS user-directory discovery; avoid hardcoded runtime roots. |
| tomlkit | 0.15.2 | MIT | Preserve operator comments and valid TOML while binding accounts; use the established parser/writer instead of patching TOML with regex. Upstream: https://github.com/python-poetry/tomlkit; installed dependency only, no copied source. |
| pydantic-core | 2.50.1 | MIT | Pydantic validation engine. |
| annotated-types | 0.8.0 | MIT | Pydantic annotation support. |
| typing-inspection | 0.4.4 | MIT | Pydantic typing support. |
| typing_extensions | 4.16.0 | PSF-2.0 | Pydantic typing compatibility. |
| anyio | 4.15.1 | MIT | Structured async concurrency and stream wrappers (`mcp` dependency). |
| attrs | 26.1.0 | MIT | Class and attribute helpers (`jsonschema` dependency). |
| certifi | 2026.7.22 | MPL-2.0 | CA bundle used by curl_cffi. |
| cffi | 2.1.1 | MIT-0 | C foreign function interface (`cryptography` and `curl_cffi` dependency). |
| click | 8.5.0 | BSD-3-Clause | Command-line interface toolkit (`uvicorn` dependency). |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | Cryptographic primitives (`pyjwt[crypto]` dependency required by `mcp`; also the cryptographic backend of noiseprotocol). OpenSSL 4.0.3 inside the wheel. |
| curl_cffi | 0.16.3 | MIT | HTTPS and WSS with `impersonate="chrome"`, the certifi CA bundle, and hostname verification. `curl_options` are set on the `Session` constructor. Imported only when the adapter runs. |
| h11 | 0.16.0 | MIT | HTTP/1.1 protocol state machine (`httpcore2` and `uvicorn` dependency). |
| httpcore2 | 2.13.1 | BSD-3-Clause | Low-level HTTP transport core (`httpx2` dependency). |
| httpx2 | 2.13.1 | BSD-3-Clause | HTTP client library (`mcp` dependency). |
| idna | 3.20 | BSD-3-Clause | Internationalized domain names in applications (`anyio` and `httpx2` dependency). |
| jsonschema | 4.26.0 | MIT | JSON Schema Draft 2020-12 validation (`mcp` dependency). |
| jsonschema-specifications | 2025.9.1 | MIT | JSON Schema meta-schemas and vocabularies (`jsonschema` dependency). |
| mcp | 2.3.0 | MIT | Official MCP Python SDK for stdio transport and JSON-RPC wire framing; avoid hand-rolled protocol machinery. |
| mcp-types | 2.3.0 | MIT | MCP protocol type models and version constants (`mcp` dependency). |
| noiseprotocol | 0.3.1 | MIT | Noise_XX_25519_AESGCM_SHA256. Imported only when a gateway handshake starts. |
| opentelemetry-api | 1.45.1 | Apache-2.0 | Telemetry tracing API (`mcp` dependency; no exporter enabled). |
| pycparser | 3.11 | BSD-3-Clause | C parser in Python (`cffi` dependency). |
| pyjwt | 2.15.1 | MIT | JSON Web Token implementation (`PyJWT`; `mcp` dependency). |
| python-multipart | 0.0.32 | Apache-2.0 | Streaming multipart parser (`mcp` dependency). |
| referencing | 0.37.0 | MIT | JSON Schema `$ref` resolution (`jsonschema` dependency). |
| rpds-py | 2026.9.1 | MIT | Persistent data structures (`jsonschema` and `referencing` dependency). |
| sse-starlette | 3.5.0 | BSD-3-Clause | Server-sent events for Starlette (`mcp` dependency). |
| starlette | 1.8.0 | BSD-3-Clause | ASGI framework (`mcp` and `sse-starlette` dependency). |
| truststore | 0.10.5 | MIT | OS trust store certificate verification (`httpcore2` and `httpx2` dependency). |
| uvicorn | 0.54.0 | BSD-3-Clause | ASGI server implementation (`mcp` dependency). |
| yt-dlp | 2026.8.19 | Unlicense | Optional extra `remote`. Python API only. Lazy import from `remote_media.py`. |
| playwright | 1.63.0 | Apache-2.0 | Optional `facebook` extra and dev dependency for bounded single-post Facebook browser collection (`src/metamuse/facebook.py`, imported lazily). Browser binaries are installed separately by the operator. |
| pyee | 13.0.1 | MIT | Event emitter library (`playwright` dependency). |
| greenlet | 3.5.6 | MIT AND PSF-2.0 | Lightweight in-process concurrent programming (`playwright` sync API dependency). |

## tlyu0419/facebook_crawler

Commit `82ec3ec46aa0a324252bbb6274b57cbf29c27e6e` (2025-02-16), upstream path `paser.py` (`_parse_edge`). No crawler loop or HTTP code was copied; `src/metamuse/facebook.py` references the post field checklist (`actor`, `creation_time`, message text, post id, permalink, reaction/comment/share counts, attachment URLs) under the MIT license:

```text
MIT License

Copyright (c) 2021 tlyu0419

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Development dependencies (not runtime requirements): pytest 9.1.1 (MIT), ruff 0.17.0
(MIT), jsonschema 4.26.0 (MIT), playwright 1.63.0 (Apache-2.0; also optional `facebook` extra). These provide the requested test runner, lint/format
checks, independent schema validation, and local synthetic browser integration tests. Transitive dev packages: attrs 26.1.0 (MIT),
iniconfig 2.3.1 (MIT), pluggy 1.6.0 (MIT), jsonschema-specifications 2025.9.1 (MIT),
referencing 0.37.0 (MIT), rpds-py 2026.9.1 (MIT), packaging 26.3 (Apache-2.0 OR
BSD-2-Clause), Pygments 2.21.0 (BSD-2-Clause), pyee 13.0.1 (MIT), greenlet 3.5.6 (MIT AND PSF-2.0).

Licenses/versions above were read from the installed distributions' primary METADATA,
not inferred from repository names. Hatchling 1.32.4 is the observed isolated build backend
(MIT, verified from its cached distribution METADATA), used by uv for the src-layout wheel; it is not a runtime dependency. No
project-wide license decision or permission to publish is made by this notice.

## Project page (B17)

The external renderer is `teddashh/teddashh.github.io` at full commit
`5e51fb954acef56e9f75b4cfd2b57dc76afcdfd8`, MIT,
`Copyright (c) 2026 Ted Huang`. Original paths: `kit/render.py`, `kit/base.css`,
`kit/lang.js`, `kit/app.js`, `kit/flow.js` and `kit/flow.css`. Generated assets live
under `site/`; the renderer remains external. The full MIT notice is retained in
[site/README.md](site/README.md). No kit code is mechanically rewritten here.
This commit derives the page address from the repository owner, so this page follows the move to TED-H-com.

The standalone capture script uses Pillow 12.1.1 (MIT-CMU), an existing pinned script
dependency, for X11 framebuffer capture and lossless PNG encoding. Pillow is not
vendored or added to the product's runtime or development dependency groups.
Its mature image functions avoid writing a separate capture or PNG encoder.

## yt-dlp Unlicense

The text below is copied from `yt_dlp-2026.8.19.dist-info/licenses/LICENSE` in the
installed wheel. It is the Unlicense dedication shipped with `yt-dlp==2026.8.19`.

```
This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or
distribute this software, either in source code form or as a compiled
binary, for any purpose, commercial or non-commercial, and by any
means.

In jurisdictions that recognize copyright laws, the author or authors
of this software dedicate any and all copyright interest in the
software to the public domain. We make this dedication for the benefit
of the public at large and to the detriment of our heirs and
successors. We intend this dedication to be an overt act of
relinquishment in perpetuity of all present and future rights to this
software under copyright law.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR
OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE,
ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.

For more information, please refer to <http://unlicense.org/>
```
