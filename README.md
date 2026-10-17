# MetaMuse CLI

Turn comments and transcripts into a recap you can check against the originals.

Have event feedback to review, or a video transcript to quote? MetaMuse keeps your permitted source material, finds matching passages, and saves drafts with their citations. You or your AI assistant writes the recap; MetaMuse keeps the originals and the export together on your computer.

Use it from a terminal, or connect it to an AI assistant that can run CLI or MCP tools. The tool runs on your machine.

[Try the example](#try-it) · [繁體中文上手指南](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/GETTING_STARTED.md) · [Connect accounts and publish](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/CONNECTED_WORKFLOW.md) · [Connect your assistant](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/dev/SKILL.md)

## What can I do with it?

- **Review event feedback:** find comments about a request, then have your assistant draft a recap with links to the original comments.
- **Find a passage in a transcript:** import local SRT or VTT subtitles and search for words you want to quote.
- **Keep a checkable draft:** save your text with source references and export Markdown, text, CSV, JSONL or a bundle.

Search currently matches literal text. Your assistant handles interpretation and writing; a saved citation still needs your review.

## Try it

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then install the [alpha package](https://pypi.org/project/metamuse-cli/0.1.0a1/) and run the bundled example. uv manages the Python version and creates an isolated environment for the command.

```sh
uv tool install --python 3.12.13 'metamuse-cli==0.1.0a1'
metamuse demo
```

Installation downloads Python and dependencies. The example itself runs locally with **synthetic comments and a prewritten recap**. It needs no account, Muse connection or AI service.

You will import three records, search for `字幕` (subtitles), and export a Markdown recap with the matching comment and its source reference. The terminal prints `Export directory:`; open the `.md` files there to see the result. This is a demonstration of the data workflow, not an AI-generated analysis.

Every run creates a separate workspace outside the checkout, under your system's user data directory (`metamuse/demos/`). It stays there so you can inspect it. Delete the workspace printed by the example when you no longer need it.

The [getting started guide (繁體中文)](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/GETTING_STARTED.md) shows the expected result and how to move on to your own files. The sample inputs are in [src/metamuse/data/examples/event-comments.jsonl](https://github.com/TED-H-com/metamuse-cli/blob/main/src/metamuse/data/examples/event-comments.jsonl).

## Use it with your AI assistant

Connect the installed CLI using the [Skill setup guide](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/dev/SKILL.md), or configure the [MCP server](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/dev/MCP.md). Loading the Skill supplies instructions; your assistant also needs access to the working CLI or MCP backend.

Once connected, try a request like this:

> Use MetaMuse to review the event comments I provide and have permission to use. Find feedback about subtitles, group the requests, and draft a recap with links to the original comments. Show me the sources and gaps before saving. Export Markdown and keep this work local.

Muse is an optional collaborator for further questions. Local import, search, drafts and exports work without a Muse account. A real Muse connection needs separate configuration and human approval. An unreleased correction has completed two real Muse turns using synthetic input, restart/read-back and cited draft exports; the [Muse guide](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/dev/MUSE.md) records that scope. The published `0.1.0a1` does not include those fixes.

Use `metamuse connect muse`, `connect facebook`, or `connect publishing` to validate your
private credential file and bind the account without editing TOML. Add `--enable-network`
to explicitly enable network use; each external action still needs its own approval.
The [connected workflow guide (中文)](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/CONNECTED_WORKFLOW.md) walks through setup,
collecting one post, Muse follow-ups, a cited draft and Page publication. This complete
sequence has synthetic integration coverage; real-account acceptance is pending.

## What can I use today?

Start with files on your computer. You can search, save drafts and export without a Muse account. Your assistant handles interpretation and writing once connected.

| What you want to do | Availability | What you get |
|---|---|---|
| Import saved comments and subtitles | Ready with local files | Bring together the saved comments, articles and subtitle files you already have, so you can find them later. |
| Find a comment or transcript passage | Ready with local files | Search for a word such as subtitles, then read the matching text and its source. |
| Keep a draft with its sources | Ready with local files | Save text you or your assistant wrote, together with the original material you cited. |
| Export a file you can edit | Ready with local files | Keep your draft and material as text documents or spreadsheet-friendly files for further editing. |
| Work with your AI assistant | Connect your assistant first | Connect your assistant to MetaMuse, then ask it to find material and draft a recap with citations. |
| Ask Muse follow-up questions | Needs setup and an unreleased compatibility fix | Select material to share, ask Muse a question, then follow up in the same conversation. A bounded synthetic-input test with a real account succeeded across corrected candidate builds; the PyPI alpha still needs those fixes. |
| Collect a Facebook post or remote video | Needs extra setup; real-account use is unverified | Additional setup and your approval are required. These collectors have not been verified with real accounts. |
| Publish a text post to a Facebook Page | Needs extra setup; real-account use is unverified | Preview a saved draft, approve its exact text and Page, then send and check the saved receipt. Text-only Page posts are experimental; [setup and limits](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/dev/PUBLISHING.md). |

See the [getting started guide](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/GETTING_STARTED.md) to try the local example,
or [connect your assistant](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/dev/SKILL.md) to use your own material.
Developers can find the [implementation and verification inventory](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/dev/STATUS.md)
in the development documentation.

<details>
<summary>Advanced: ask Muse follow-up questions with one bounded approval</summary>

The implemented [bounded research workflow](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/dev/MUSE.md#bounded-research-workflow-synthetic-evidence) lets a human approve a fixed packet, topic, expiry and question/read budget. Your assistant can then ask varied questions within that scope, keep the discussion across restarts, and save a draft citing sources and Muse observations. Changed data or scope needs new consent. The guide distinguishes fixture coverage from the bounded real-account test and the fixes still missing from the published alpha.

</details>

<details>
<summary>Advanced: full command-by-command offline example, including fixture Muse</summary>

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

</details>

## Skill

The canonical Skill tree is [skills/metamuse/](https://github.com/TED-H-com/metamuse-cli/blob/main/skills/metamuse/SKILL.md). The wheel ships the same files, and `metamuse skill export --dest DIR` writes them to a directory the operator names. It is not an MCP tool. Installation steps are in [SKILL.md](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/dev/SKILL.md). The Skill requires an installed, reachable MetaMuse backend (CLI or MCP server) to execute operations; when the backend is unavailable, operations cannot be simulated.

## Development and project details

One package (`metamuse`) and one shared application serve the CLI, MCP server and Skill. See the [design baseline](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/design/README.md), [core API](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/dev/CORE_API.md), [collection formats](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/dev/COLLECT.md), and [full offline walkthrough](https://github.com/TED-H-com/metamuse-cli/blob/main/docs/dev/OFFLINE_SLICE.md).

For development, use Python 3.12 managed by uv:

```sh
pytest-disktmp uv run pytest -q
uv run ruff check .
```

Runtime workspaces belong outside the checkout. Credentials, account settings, real collected data and LIVE evidence stay outside this repository and are supplied by the operator at runtime.

This is an unofficial project, unaffiliated with Meta, Facebook or Muse. It does not claim official API access, terms-of-service compliance or protection against account restrictions.

License: [Apache-2.0](https://github.com/TED-H-com/metamuse-cli/blob/main/LICENSE). Dependency notices: [THIRD_PARTY_NOTICES.md](https://github.com/TED-H-com/metamuse-cli/blob/main/THIRD_PARTY_NOTICES.md).
