# MetaMuse CLI

Pre-alpha. Nothing is released yet.

A local-first tool for organising permitted social posts, comments, media and subtitles into sourced evidence packets, discussing them with Muse, and saving drafts and exports. One package (`metamuse`), one shared core for the CLI, MCP server and Skill.

This is an unofficial project. It is not affiliated with Meta, Facebook or Muse, and it does not claim official API access, terms-of-service compliance, or protection against account restrictions.

Credentials, account settings, real collected data and live-test evidence are never stored in this repository; the code reads them from operator-provided locations at runtime.

Design baseline: [docs/design/](docs/design/README.md). Collection behaviour is in [COLLECT.md](docs/dev/COLLECT.md). Muse binding, grants and recovery are in [MUSE.md](docs/dev/MUSE.md). The copyable offline path below is the same procedure as [OFFLINE_SLICE.md](docs/dev/OFFLINE_SLICE.md).

## Status

Each row uses one of four states: implemented with offline tests; implemented but not independently accepted; not done; needs a real account (LIVE).

| Capability | State | Note |
|---|---|---|
| Local import | implemented with offline tests | Local files and text. A remote URL is rejected before a job exists. |
| Local media and subtitles | implemented with offline tests | `ffprobe` is optional. Tests that need the binary skip when it is absent. |
| Search | implemented with offline tests | Project-scoped search over imported evidence. |
| Packets | implemented with offline tests | Selection file to a pre-share preview. |
| Drafts | implemented with offline tests | Host-supplied text. MetaMuse makes no generation call. |
| Exports | implemented with offline tests | `bundle`, `jsonl`, `markdown`, `text`, and `csv`. No publish or upload. |
| Muse lifecycle with the fixture backend | implemented with offline tests | Open, send, read, and close through `OfflineMuseGateway`. Derived records stay `backend=fixture`, `mock=true`, `research_complete=false`. |
| Muse LIVE | needs a real account (LIVE) | Blocked by upstream protocol drift. The standard-VM path is in progress. |
| MCP stdio | implemented with offline tests | Fifteen tools. Stdout is the protocol; logs go to stderr. |
| MCP attached worker | not done | In progress on another branch. `metamuse mcp` does not start a worker. |
| Remote media (B12) and Facebook posts (B13) | not done | In progress elsewhere. Not in this tree. |
| Publishing | not done | Not started. |
| CI | implemented but not independently accepted | This branch adds `.github/workflows/ci.yml` and `scripts/smoke_wheel.sh`. No hosted run is claimed here. |

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

Run from the repository root after `uv sync --python 3.12`. The commands use synthetic comments, an explicit fixture Muse backend, host-written draft text, and five local export formats. Grant creation is a human CLI operation. The preview helpers only calculate a digest. They do not submit or create a grant. No credentials, live binding, network request, publication, or upload is involved.

```bash
export UV_OFFLINE=1
mkdir -p "$HOME/agent-work/tmp"
export SLICE_DIR=$(mktemp -d "$HOME/agent-work/tmp/metamuse-at042-XXXXXX")
export SLICE_WS="$SLICE_DIR/workspace"
mkdir "$SLICE_DIR/inputs"
mm() { uv run metamuse --workspace "$SLICE_WS" --json --non-interactive "$@"; }
operator() { uv run metamuse --workspace "$SLICE_WS" --json "$@"; }
field() { uv run python -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"][sys.argv[2]])' "$1" "$2"; }

mm init > "$SLICE_DIR/init.json"
uv run python - <<'PY'
import json, os
from pathlib import Path
root = Path(os.environ['SLICE_DIR'])
config = Path(os.environ['SLICE_WS']) / 'metamuse.toml'
text = config.read_text().replace('[app]\n', '[app]\nimport_roots = ' + json.dumps([str(root / 'inputs')]) + '\n')
text = text.replace('[muse]\nenabled = false', '[muse]\nenabled = true\nbackend = "fixture"')
config.write_text(text)
corpus = Path('tests/fixtures/corpus/import/comments_tree.jsonl')
(root / 'inputs/comments.jsonl').write_text('\n'.join(corpus.read_text().splitlines()[:3]) + '\n')
PY
mm doctor > "$SLICE_DIR/doctor.json"
mm project create --name 'Synthetic slice' --request-key project > "$SLICE_DIR/project.json"
export SLICE_PROJECT=$(field "$SLICE_DIR/project.json" id)
mm add --project "$SLICE_PROJECT" --file "$SLICE_DIR/inputs/comments.jsonl" --kind jsonl --request-key import --run > "$SLICE_DIR/import.json"
mm search --project "$SLICE_PROJECT" --query '這是' > "$SLICE_DIR/search.json"
uv run python - <<'PY'
import json, os
from pathlib import Path
root = Path(os.environ['SLICE_DIR'])
hits = json.loads((root / 'search.json').read_text())['data']['results']
(root / 'inputs/selection.json').write_text(json.dumps([hit['ref'] for hit in hits]))
PY
mm packet prepare --project "$SLICE_PROJECT" --selection-file "$SLICE_DIR/inputs/selection.json" --request-key packet > "$SLICE_DIR/packet.json"
export SLICE_PACKET=$(field "$SLICE_DIR/packet.json" id)
cat "$SLICE_DIR/packet.json"
```

Review the packet preview before the fixture grant.

```bash
uv run python - <<'PY'
import json, os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from metamuse import app, models as m, safety
from metamuse._muse_lifecycle import prepare_open
root = Path(os.environ['SLICE_DIR'])
ctx = app.AppContext.open(os.environ['SLICE_WS'])
request = m.MuseOpenRequest(project_id=os.environ['SLICE_PROJECT'], topic='Synthetic activity review', access_grant_id='pending', request_key='open')
preview = prepare_open(ctx, request)
digest = safety.action_digest(preview.target, preview.payload)
(root / 'inputs/open-scope.json').write_text(json.dumps({'operations':['muse.open'], 'target_digests':[digest]}))
(root / 'expiry.txt').write_text((datetime.now(UTC) + timedelta(days=1)).isoformat())
PY
operator permit create --project "$SLICE_PROJECT" --kind access --scope-file "$SLICE_DIR/inputs/open-scope.json" --provider fixture --account fixture --issuer fixture-operator --expires-at "$(cat "$SLICE_DIR/expiry.txt")" --request-key open-grant > "$SLICE_DIR/open-grant.json"
mm muse open --project "$SLICE_PROJECT" --topic 'Synthetic activity review' --access-grant "$(field "$SLICE_DIR/open-grant.json" id)" --request-key open > "$SLICE_DIR/open.json"
export SLICE_DISCUSSION=$(field "$SLICE_DIR/open.json" discussion_id)
mm jobs get "$(field "$SLICE_DIR/open.json" job_id)"
mm worker run --once
mm jobs get "$(field "$SLICE_DIR/open.json" job_id)"

uv run python - <<'PY'
import json, os
from pathlib import Path
from metamuse import app, models as m, safety
from metamuse._muse_lifecycle import prepare_send
root = Path(os.environ['SLICE_DIR'])
ctx = app.AppContext.open(os.environ['SLICE_WS'])
request = m.MuseSendRequest(discussion_id=os.environ['SLICE_DISCUSSION'], packet_id=os.environ['SLICE_PACKET'], question='List evidence and gaps.', share_grant_id='pending', request_key='send')
preview = prepare_send(ctx, request)
packet = ctx.store.get(m.Packet, request.packet_id)
(root / 'inputs/share-scope.json').write_text(json.dumps({'packet_digests':[packet.digest]}))
(root / 'send-digest.txt').write_text(safety.action_digest(preview.target, preview.payload))
(root / 'inputs/question.txt').write_text(request.question)
PY
operator permit create --project "$SLICE_PROJECT" --kind share --scope-file "$SLICE_DIR/inputs/share-scope.json" --provider fixture --account fixture --issuer fixture-operator --expires-at "$(cat "$SLICE_DIR/expiry.txt")" --exact-digest "$(cat "$SLICE_DIR/send-digest.txt")" --request-key share-grant > "$SLICE_DIR/share-grant.json"
mm muse send --discussion "$SLICE_DISCUSSION" --packet "$SLICE_PACKET" --question-file "$SLICE_DIR/inputs/question.txt" --share-grant "$(field "$SLICE_DIR/share-grant.json" id)" --request-key send > "$SLICE_DIR/send.json"
export SLICE_EXCHANGE=$(field "$SLICE_DIR/send.json" exchange_id)
mm jobs get "$(field "$SLICE_DIR/send.json" job_id)"
mm muse read --exchange "$SLICE_EXCHANGE"
mm worker run --once
mm jobs get "$(field "$SLICE_DIR/send.json" job_id)"

uv run python - <<'PY'
import json, os
from pathlib import Path
from metamuse import app, models as m, safety
from metamuse._muse_lifecycle import read_action
root = Path(os.environ['SLICE_DIR'])
ctx = app.AppContext.open(os.environ['SLICE_WS'])
target, payload = read_action(ctx.store.get(m.Discussion, os.environ['SLICE_DISCUSSION']))
(root / 'inputs/read-scope.json').write_text(json.dumps({'operations':['muse.read'], 'target_digests':[safety.action_digest(target,payload)]}))
PY
operator permit create --project "$SLICE_PROJECT" --kind access --scope-file "$SLICE_DIR/inputs/read-scope.json" --provider fixture --account fixture --issuer fixture-operator --expires-at "$(cat "$SLICE_DIR/expiry.txt")" --request-key read-grant > "$SLICE_DIR/read-grant.json"
mm muse read --exchange "$SLICE_EXCHANGE" --refresh --access-grant "$(field "$SLICE_DIR/read-grant.json" id)" > "$SLICE_DIR/refresh.json"
mm worker run --once
mm muse read --exchange "$SLICE_EXCHANGE" > "$SLICE_DIR/read.json"
mm muse close-exchange "$SLICE_EXCHANGE" --outcome needs_more_data --request-key close > "$SLICE_DIR/close.json"

uv run python - <<'PY'
import json, os
from pathlib import Path
root = Path(os.environ['SLICE_DIR'])
read = json.loads((root / 'read.json').read_text())['data']
observed = next(o for o in read['observations'] if o['raw_event'].get('payload',{}).get('role') == 'assistant')
assert read['mock'] and read['research_complete'] is False
facts = json.loads((root / 'inputs/selection.json').read_text()) + [observed['evidence_ref']]
(root / 'inputs/facts.json').write_text(json.dumps(facts))
(root / 'inputs/draft.txt').write_bytes('=活動回顧（host 撰寫）\r\n留言與子回覆仍有缺口。😀 e\u0301\n'.encode())
PY
mm draft save --project "$SLICE_PROJECT" --text-file "$SLICE_DIR/inputs/draft.txt" --platform generic --facts-file "$SLICE_DIR/inputs/facts.json" --request-key draft > "$SLICE_DIR/draft.json"
export SLICE_DRAFT=$(field "$SLICE_DIR/draft.json" draft_id)
mm draft preview "$SLICE_DRAFT" --revision 1 > "$SLICE_DIR/preview.json"
uv run python - <<'PY'
import json, os
from pathlib import Path
(Path(os.environ['SLICE_DIR']) / 'inputs/export-refs.json').write_text(json.dumps([{'kind':'draft_revision','id':os.environ['SLICE_DRAFT'],'revision':1}]))
PY
for format in bundle jsonl markdown text csv; do
  mm export --project "$SLICE_PROJECT" --refs-file "$SLICE_DIR/inputs/export-refs.json" --format "$format" --request-key "export-$format" > "$SLICE_DIR/export-$format.json"
done
cat "$SLICE_DIR/preview.json" "$SLICE_DIR/export-bundle.json"
rm -rf -- "$SLICE_DIR"
```

## Skill

The canonical Skill tree is [skills/metamuse/](skills/metamuse/SKILL.md). The wheel ships the same files, and `metamuse skill export --dest DIR` writes them to a directory the operator names. It is not an MCP tool. Installation steps are in [SKILL.md](docs/dev/SKILL.md). The Skill requires an installed, reachable MetaMuse backend (CLI or MCP server) to execute operations; when the backend is unavailable, operations cannot be simulated.

License: [Apache-2.0](LICENSE).
