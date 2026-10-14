# AT-042 offline journey

This runs real installed CLI processes with synthetic comments and replies, an explicit
`OfflineMuseGateway`, host-written text and five local export formats. Every Muse-derived
draft and export is labelled `backend=fixture`, `mock=true`, `research_complete=false`.
No credentials, live binding, network request, publication or upload is involved. AT-043
remains blocked pending separately authorized LIVE evidence.

Run from the repository root after `UV_OFFLINE=1 uv sync --python 3.12`. The operator
commands below create fixture-only grants; grant creation is a human CLI operation and
is never available to MCP or inferred from Muse text. The preview helpers only calculate
the exact target/payload digest; they perform no submission or grant creation.

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

`packet prepare` returns the exact pre-share preview, digest, source methods and
redaction notice. Review it before the following fixture grant. This backend retains
the normal permission and queue boundaries even though it opens no sockets.

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
```

The first open/send envelopes are `accepted`, `queued`, `worker_available=false`.
Job lookup shows zero attempts until `worker run --once`. Sending does not imply a
reply or research completion. Reading without refresh only reads the local store.
Refresh requires its own access grant and a separate worker step.

```bash
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
# The operator/host supplies this text. MetaMuse makes no generation call.
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
# After inspecting the artifacts, remove only this generated synthetic workspace:
rm -rf -- "$SLICE_DIR"
```

The deterministic fixture reply is progress (`raw_status=running`). The host settles
`needs_more_data`; `research_complete` remains false. Its immutable observation ID has
revision 1. Drafts may cite it and other drafts transitively. Backend/mock labels come
from stored observations, including the exact observation digest. Packets accept only
item/transcript revisions; citing Muse never re-shares the reply or creates permission.

Bundle members contain redacted JSONL and Markdown with source/provenance metadata;
opaque media is excluded. Each manifest lists exact member hashes and byte counts,
text policy and redaction notice. JSONL's `text` is faithful, CSV prefixes formula cells,
and derived text files add a Muse notice before the original body. Private formats use
`original_private` and do not redact source text: review the selected inputs. The automated
journey keeps its synthetic cookie challenge out of private export selections and proves
that packet previews, all exports, logs and envelopes contain neither marker nor secret.

The integration test repeats Muse → draft → all five exports through actual MCP stdio:

```bash
pytest-disktmp uv run pytest -q tests/test_at042_slice.py tests/test_at042_evidence.py
uv run metamuse --workspace /path/to/operator-offline-workspace --non-interactive mcp
```

The test starts that installed entry point using `mcp.client.stdio.stdio_client` and
`ClientSession`, initializes the protocol, and calls the existing `metamuse_muse_open`,
`metamuse_muse_send`, `metamuse_muse_read`, `metamuse_muse_close_exchange`,
`metamuse_draft_save`, `metamuse_draft_preview` and `metamuse_export` tools. Fixture
operator grants and worker execution remain CLI operations. CLI replays MCP exports
and compares read/preview results, excluding only per-request IDs. A negative test
removes a derived bundle label and repairs its hashes; attribution verification still fails.

B16 should measure per-step and total latency, 30-run p95 and peak RSS, import/search/
resolver/export scaling, serialized bytes and disk growth, worker queue/lock wait, and
backup/restore cost. B16 should separately exercise restart/cancel/uncertain-submit
boundaries, malformed or late history, orphan export cleanup, and privacy scans. This
slice adds neither fault injection nor performance measurements and claims no MEASURE
or LIVE evidence.
