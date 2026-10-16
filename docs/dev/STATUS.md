# Implementation and verification status

This is the technical verification inventory for developers. User tasks and
setup requirements are described in the [README](../../README.md#what-can-i-use-today).
Implementation test coverage does not establish real-account success.

Each row uses one of four states: implemented with offline tests; implemented but
not independently accepted; not done; needs a real account (LIVE).

| Capability | State | Note |
|---|---|---|
| Local import | implemented with offline tests | Local files and text. The local import path rejects URLs before a job exists; approved remote inputs use their own collector. |
| Local media and subtitles | implemented with offline tests | `ffprobe` is optional. Tests that need the binary skip when it is absent. |
| Search | implemented with offline tests | Project-scoped search over imported evidence. |
| Packets | implemented with offline tests | Selection file to a pre-share preview. |
| Drafts | implemented with offline tests | Host-supplied text. MetaMuse makes no generation call. |
| Exports | implemented with offline tests | `bundle`, `jsonl`, `markdown`, `text`, and `csv`. No publish or upload. |
| Muse lifecycle with the fixture backend | implemented with offline tests | Open, send, read, and close through `OfflineMuseGateway`. Derived records stay `backend=fixture`, `mock=true`, `research_complete=false`. |
| Muse standard-VM transport | implemented with offline tests | Strict Noise key/nonce verification, durable trust and bounded wake/redial. AT-038 and AT-043 LIVE have not run; CVM is unsupported. |
| MCP stdio | implemented with offline tests | Nineteen tools (fifteen P0 tools and four experimental publishing tools). Stdout is the protocol; logs go to stderr. |
| MCP attached worker | implemented with offline tests | `metamuse mcp` starts one worker; `--no-worker` opts out. The workspace OS lock allows one executor. |
| Approval previews and `metamuse permit approve` | implemented with offline tests | Human-only approval of the exact action; no MCP approval tool. |
| Remote media (B12) | implemented with offline tests | Exact-action approval preview and human `permit approve`. OFFLINE and FIXTURE evidence; AT-040 LIVE has not been run. |
| Facebook posts (B13) | implemented with offline tests | One operator-approved post per job, rendered in sandboxed Chromium through a policy-checked local proxy. Tested only on synthetic loopback pages; requires the `facebook` extra and Playwright Chromium. Human approval uses `metamuse permit create`; Facebook approval preview is not wired yet. No real-account run. |
| Publishing | implemented with fixture tests; LIVE unverified | Experimental Facebook Page text posts, exact human approval, one durable send attempt, saved receipt/read-back and read-only reconciliation. No personal profiles, groups, replies, assets or cross-platform publishing. See [publishing](PUBLISHING.md). |

## Development checks

GitHub Actions `check` runs the offline gates, sandboxed loopback Chromium browser tests (browser skips fail CI), and wheel smoke on every PR and `main` push; required status check on `main` since 2026-10-14.

The fixture journey is exercised by `tests/test_at042_slice.py` and
`tests/test_at042_evidence.py`. No real-account integration is claimed.
