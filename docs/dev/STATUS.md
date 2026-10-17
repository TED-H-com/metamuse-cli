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
| Muse standard-VM transport | offline tests and a bounded synthetic LIVE journey | An independently approved conversation completed two real Muse turns, restart/local read-back, and a host-written draft with source and both reply citations exported as bundle and Markdown. The run crossed candidate builds while compatibility defects were fixed; it is not a fresh-install acceptance of one unchanged release. An earlier create remains delivery-uncertain and was never replayed. The published `0.1.0a1` lacks these fixes; CVM is unsupported. See [diagnostics and scope](MUSE_SPIKE.md#11-authorized-live-diagnostics-2026-10-17). |
| MCP stdio | implemented with offline tests | Nineteen tools (fifteen P0 tools and four experimental publishing tools). Stdout is the protocol; logs go to stderr. |
| MCP attached worker | implemented with offline tests | `metamuse mcp` starts one worker; `--no-worker` opts out. The workspace OS lock allows one executor. |
| Approval previews and `metamuse permit approve` | implemented with offline tests | Human-only approval of the exact action; no MCP approval tool. |
| Account setup | implemented with offline tests | Human-only `metamuse connect muse|facebook|publishing` validates a named private file and atomically binds account settings, preserving TOML comments. Network opt-in is explicit; setup performs no remote IO and issues no permits. |
| Remote media (B12) | implemented with offline tests | Exact-action approval preview and human `permit approve`. OFFLINE and FIXTURE evidence; AT-040 LIVE has not been run. |
| Facebook posts (B13) | implemented with offline tests | One operator-approved post per job, rendered in sandboxed Chromium through a policy-checked local proxy. CLI/MCP provide the shared approval preview and human `permit approve` flow; manual `permit create` remains available. Requires the `facebook` extra and Playwright Chromium. No real-account run. |
| Publishing | fixture tests and partial LIVE; exact acceptance incomplete | One authorized Page text test produced a real receipt and published post with the requested content/link. Facebook removed a terminal newline, so exact read-back failed and the CLI keeps `needs_reconcile`; the post was not resent. This does not pass the complete AT-049 acceptance. No personal profiles, groups, replies, assets or cross-platform publishing. See [publishing](PUBLISHING.md). |

`tests/test_connected_workflow.py` exercises one workspace from a synthetic Facebook
post/comments through packet, two Muse turns, cited Facebook draft, approved Page send,
read-back and restart. It uses shipped CLI/MCP entries and fixture browser/Muse/Page
boundaries; it is INTEGRATION/FIXTURE evidence, not LIVE acceptance.

## Development checks

GitHub Actions `check` runs the offline gates, sandboxed loopback Chromium browser tests (browser skips fail CI), and wheel smoke on every PR and `main` push; required status check on `main` since 2026-10-14.

The fixture journey is exercised by `tests/test_at042_slice.py` and
`tests/test_at042_evidence.py`. No real-account integration is claimed.
