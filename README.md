# MetaMuse CLI

Pre-alpha. Nothing is released yet.

A local-first tool for organising permitted social posts, comments, media and subtitles into sourced evidence packets, discussing them with Muse, and saving drafts and exports. One package (`metamuse`), one shared core for the CLI, MCP server and Skill.

This is an unofficial project. It is not affiliated with Meta, Facebook or Muse, and it does not claim official API access, terms-of-service compliance, or protection against account restrictions.

Credentials, account settings, real collected data and live-test evidence are never stored in this repository; the code reads them from operator-provided locations at runtime.

Design baseline: [docs/design/](docs/design/README.md). Collection behaviour is in [COLLECT.md](docs/dev/COLLECT.md). Muse binding, grants and recovery are in [MUSE.md](docs/dev/MUSE.md). The copyable offline path below is the same procedure as [OFFLINE_SLICE.md](docs/dev/OFFLINE_SLICE.md).

## Status

Each row uses one of four states: implemented with offline tests; implemented but not independently accepted; not done; needs a real account (LIVE).

<!-- The project page mirrors this status table; CI checks that they stay in sync. -->

| Capability | State | Note |
|---|---|---|
| Local import | implemented with offline tests | Local files and text. |
| Local media and subtitles | implemented with offline tests | `ffprobe` is optional. Tests that need the binary skip when it is absent. |
| Search | implemented with offline tests | Project-scoped search over imported evidence. |
| Packets | implemented with offline tests | Selection file to a pre-share preview. |
| Drafts | implemented with offline tests | Host-supplied text. MetaMuse makes no generation call. |
| Exports | implemented with offline tests | `bundle`, `jsonl`, `markdown`, `text`, and `csv`. No publish or upload. |
| Muse lifecycle with the fixture backend | implemented with offline tests | Open, send, read, and close through `OfflineMuseGateway`. Derived records stay `backend=fixture`, `mock=true`, `research_complete=false`. |
| Muse standard-VM transport | implemented with offline tests | Strict Noise key/nonce verification, durable trust and bounded wake/redial. AT-038 and AT-043 LIVE have not run; CVM is unsupported. |
| MCP stdio | implemented with offline tests | Fifteen tools. Stdout is the protocol; logs go to stderr. |
| MCP attached worker | implemented with offline tests | `metamuse mcp` starts one worker; `--no-worker` opts out. The workspace OS lock allows one executor. |
| Approval previews and `metamuse permit approve` | implemented with offline tests | Human-only approval of the exact action; no MCP approval tool. |
| Remote media (B12) | implemented with offline tests | Exact-action approval preview and human `permit approve`. OFFLINE and FIXTURE evidence; AT-040 LIVE has not been run. |
| Facebook posts (B13) | implemented with offline tests | One operator-approved post per job, rendered in sandboxed Chromium through a policy-checked local proxy. Tested only on synthetic loopback pages; requires the `facebook` extra and Playwright Chromium. Human approval uses `metamuse permit create`; Facebook approval preview is not wired yet. No real-account run. |
| Publishing | not done | Not started. |
| CI | implemented with offline tests | GitHub Actions `check` runs the offline gates, sandboxed loopback Chromium browser tests (browser skips fail CI), and wheel smoke on every PR and `main` push; required status check on `main` since 2026-10-14. |

Automated coverage of the fixture journey is `tests/test_at042_slice.py` and `tests/test_at042_evidence.py`. No real account integration is claimed.

```sh
uv sync --python 3.12
uv run metamuse --workspace /path/to/private/workspace --json init
uv run metamuse --workspace /path/to/private/workspace --json doctor
pytest-disktmp uv run pytest -q
uv run ruff check .
```

Runtime workspaces belong outside the checkout. Operation registration, handler
boundaries and database APIs are documented in [CORE_API.md](docs/dev/CORE_API.md).
Dependency notices are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Local offline walkthrough

Run from the repository root after `uv sync --python 3.12`. This synthetic walkthrough collects data, previews a packet, opens a fixture Muse discussion, asks a question and a follow-up in the same discussion, saves host-written text and exports five formats. The host shows the human each `E_SCOPE_REQUIRED` approval preview. Only the human runs `operator permit approve`, reviews its stderr (including expiry and uses), and repeats the unchanged call with the returned grant. The agent never approves. Evidence is OFFLINE/FIXTURE, with no network or accounts.

These Bash blocks run in order. Python only edits configuration and copies public JSON fields; every product operation uses the shipped CLI. `tests/test_readme_walkthrough.py` executes these exact blocks in a temporary workspace.

```bash
export UV_OFFLINE=1
mkdir -p "$HOME/agent-work/tmp"
export SLICE_DIR="${SLICE_DIR:-$(mktemp -d "$HOME/agent-work/tmp/metamuse-at042-XXXXXX")}"
export SLICE_WS="$SLICE_DIR/workspace"
mkdir "$SLICE_DIR/inputs"
mm() { uv run metamuse --workspace "$SLICE_WS" --json --non-interactive "$@"; }
operator() { uv run metamuse --workspace "$SLICE_WS" --json "$@"; }
field() { uv run python -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"][sys.argv[2]])' "$1" "$2"; }
approval_field() { uv run python -c 'import json,sys; print(json.load(open(sys.argv[1]))["approval"][sys.argv[2]])' "$1" "$2"; }
preview() {
  local output="$1"; shift
  local result=0
  mm "$@" > "$output" || result=$?
  test "$result" -eq 3
  uv run python -c 'import json,sys; e=json.load(open(sys.argv[1])); assert e["error"]["code"] == "E_SCOPE_REQUIRED" and e["approval"]; print(e["approval"]["summary"]); print(e["approval"]["next_action"])' "$output"
}
save_request() {
  uv run python -c 'import json,sys; a=json.load(open(sys.argv[1]))["approval"]; json.dump(a["request"],open(a["request_file"],"w"),ensure_ascii=False)' "$1"
}

mm init > "$SLICE_DIR/init.json"
uv run python - <<'PYTHON'
import json, os
from pathlib import Path
root = Path(os.environ['SLICE_DIR'])
config = Path(os.environ['SLICE_WS']) / 'metamuse.toml'
text = config.read_text().replace('[app]\n', '[app]\nimport_roots = ' + json.dumps([str(root / 'inputs')]) + '\n')
text = text.replace('[muse]\nenabled = false', '[muse]\nenabled = true\nbackend = "fixture"')
config.write_text(text)
corpus = Path('tests/fixtures/corpus/import/comments_tree.jsonl')
(root / 'inputs/comments.jsonl').write_text('\n'.join(corpus.read_text().splitlines()[:3]) + '\n')
PYTHON
mm doctor > "$SLICE_DIR/doctor.json"
mm project create --name 'Synthetic slice' --request-key project > "$SLICE_DIR/project.json"
export SLICE_PROJECT=$(field "$SLICE_DIR/project.json" id)
mm add --project "$SLICE_PROJECT" --file "$SLICE_DIR/inputs/comments.jsonl" --kind jsonl --request-key import --run > "$SLICE_DIR/import.json"
mm search --project "$SLICE_PROJECT" --query '這是' > "$SLICE_DIR/search.json"
uv run python - <<'PYTHON'
import json, os
from pathlib import Path
root = Path(os.environ['SLICE_DIR'])
hits = json.loads((root / 'search.json').read_text())['data']['results']
(root / 'inputs/selection.json').write_text(json.dumps([hit['ref'] for hit in hits]))
PYTHON
mm packet prepare --project "$SLICE_PROJECT" --selection-file "$SLICE_DIR/inputs/selection.json" --request-key packet > "$SLICE_DIR/packet.json"
export SLICE_PACKET=$(field "$SLICE_DIR/packet.json" id)
mm packet preview "$SLICE_PACKET" > "$SLICE_DIR/packet-preview.json"
cat "$SLICE_DIR/packet-preview.json"
```

Call without a grant, show the review and its human command, save the public request, then approve the exact digest. Credential-like values are redacted separately in every text field. A redacted or edited request fails the original digest; it must be reviewed again. Expired, revoked, exhausted or mismatched grants return `E_SCOPE_DENIED` without a preview. Repeat without a grant to get a fresh review.

```bash
preview "$SLICE_DIR/open-preview.json" muse open --project "$SLICE_PROJECT" --topic 'Synthetic activity review' --request-key open
save_request "$SLICE_DIR/open-preview.json"
operator permit approve --operation muse.open --request-file "$(approval_field "$SLICE_DIR/open-preview.json" request_file)" --exact-digest "$(approval_field "$SLICE_DIR/open-preview.json" action_digest)" --request-key open-grant > "$SLICE_DIR/open-grant.json"
mm muse open --project "$SLICE_PROJECT" --topic 'Synthetic activity review' --access-grant "$(field "$SLICE_DIR/open-grant.json" id)" --request-key open > "$SLICE_DIR/open.json"
export SLICE_DISCUSSION=$(field "$SLICE_DIR/open.json" discussion_id)
mm jobs get "$(field "$SLICE_DIR/open.json" job_id)"
mm worker run --once
mm jobs get "$(field "$SLICE_DIR/open.json" job_id)"

printf '%s' 'List evidence and gaps.' > "$SLICE_DIR/inputs/question-1.txt"
printf '%s' 'Which missing replies would change those conclusions?' > "$SLICE_DIR/inputs/question-2.txt"
for exchange in 1 2; do
  preview "$SLICE_DIR/send-preview-$exchange.json" muse send --discussion "$SLICE_DISCUSSION" --packet "$SLICE_PACKET" --question-file "$SLICE_DIR/inputs/question-$exchange.txt" --request-key "send-$exchange"
  mm packet preview "$SLICE_PACKET"
  save_request "$SLICE_DIR/send-preview-$exchange.json"
  operator permit approve --operation muse.send --request-file "$(approval_field "$SLICE_DIR/send-preview-$exchange.json" request_file)" --exact-digest "$(approval_field "$SLICE_DIR/send-preview-$exchange.json" action_digest)" --request-key "share-grant-$exchange" > "$SLICE_DIR/share-grant-$exchange.json"
  mm muse send --discussion "$SLICE_DISCUSSION" --packet "$SLICE_PACKET" --question-file "$SLICE_DIR/inputs/question-$exchange.txt" --share-grant "$(field "$SLICE_DIR/share-grant-$exchange.json" id)" --request-key "send-$exchange" > "$SLICE_DIR/send-$exchange.json"
  export SLICE_EXCHANGE=$(field "$SLICE_DIR/send-$exchange.json" exchange_id)
  mm jobs get "$(field "$SLICE_DIR/send-$exchange.json" job_id)"
  mm worker run --once
  mm jobs get "$(field "$SLICE_DIR/send-$exchange.json" job_id)"
  preview "$SLICE_DIR/read-preview-$exchange.json" muse read --exchange "$SLICE_EXCHANGE" --refresh
  save_request "$SLICE_DIR/read-preview-$exchange.json"
  operator permit approve --operation muse.read --request-file "$(approval_field "$SLICE_DIR/read-preview-$exchange.json" request_file)" --exact-digest "$(approval_field "$SLICE_DIR/read-preview-$exchange.json" action_digest)" --request-key "read-grant-$exchange" > "$SLICE_DIR/read-grant-$exchange.json"
  mm muse read --exchange "$SLICE_EXCHANGE" --refresh --access-grant "$(field "$SLICE_DIR/read-grant-$exchange.json" id)" > "$SLICE_DIR/refresh-$exchange.json"
  mm worker run --once
  mm muse read --exchange "$SLICE_EXCHANGE" > "$SLICE_DIR/read-$exchange.json"
  mm muse close-exchange "$SLICE_EXCHANGE" --outcome needs_more_data --request-key "close-$exchange" > "$SLICE_DIR/close-$exchange.json"
done
```

The fixture replies are progress, so the operator closes each exchange as `needs_more_data`. Draft text comes from the host and cites observations from both exchanges. CLI invocations have already restarted the application between steps; the separate MCP acceptance test also restarts a real stdio server and checks that no send repeats.

```bash
uv run python - <<'PYTHON'
import json, os
from pathlib import Path
root = Path(os.environ['SLICE_DIR'])
facts = json.loads((root / 'inputs/selection.json').read_text())
citations = []
for exchange in (1, 2):
    read = json.loads((root / f'read-{exchange}.json').read_text())['data']
    assistant_ids = {message['id'] for message in read['messages'] if message['role'] == 'assistant'}
    observed = next(o for o in read['observations'] if o['message_id'] in assistant_ids and o['raw_event'].get('payload',{}).get('role') == 'assistant')
    assert read['mock'] and read['research_complete'] is False
    facts.append(observed['evidence_ref'])
    citations.append(observed['id'])
(root / 'inputs/facts.json').write_text(json.dumps(facts))
host_text = '=活動回顧（host 撰寫）\r\n首輪列出缺口；追問指出仍需要留言與子回覆。😀 e\u0301\n'
(root / 'inputs/draft.txt').write_bytes((host_text + '\n引用：' + ', '.join(citations) + '\n').encode())
PYTHON
mm draft save --project "$SLICE_PROJECT" --text-file "$SLICE_DIR/inputs/draft.txt" --platform generic --facts-file "$SLICE_DIR/inputs/facts.json" --request-key draft > "$SLICE_DIR/draft.json"
export SLICE_DRAFT=$(field "$SLICE_DIR/draft.json" draft_id)
mm draft preview "$SLICE_DRAFT" --revision 1 > "$SLICE_DIR/preview.json"
uv run python - <<'PYTHON'
import json, os
from pathlib import Path
(Path(os.environ['SLICE_DIR']) / 'inputs/export-refs.json').write_text(json.dumps([{'kind':'draft_revision','id':os.environ['SLICE_DRAFT'],'revision':1}]))
PYTHON
for format in bundle jsonl markdown text csv; do
  mm export --project "$SLICE_PROJECT" --refs-file "$SLICE_DIR/inputs/export-refs.json" --format "$format" --request-key "export-$format" > "$SLICE_DIR/export-$format.json"
done
cat "$SLICE_DIR/preview.json" "$SLICE_DIR/export-bundle.json"
if [ "${SLICE_KEEP:-0}" != 1 ]; then rm -rf -- "$SLICE_DIR"; fi
```

## Skill

The canonical Skill tree is [skills/metamuse/](skills/metamuse/SKILL.md). The wheel ships the same files, and `metamuse skill export --dest DIR` writes them to a directory the operator names. It is not an MCP tool. Installation steps are in [SKILL.md](docs/dev/SKILL.md). The Skill requires an installed, reachable MetaMuse backend (CLI or MCP server) to execute operations; when the backend is unavailable, operations cannot be simulated.

License: [Apache-2.0](LICENSE).
