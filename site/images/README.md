# Terminal captures

Real Alacritty on a newly allocated Xvfb display; `WINIT_X11_SCALE_FACTOR=2`.
The shell prints each literal command and executes it from `/tmp/metamuse-demo`.
Variables set visibly in earlier scenes are retained between frames.

Each PNG keeps the full window width and its original top edge, ending one empty
terminal row below the last ink. Pixels within that rectangle are unedited.
Pillow's lossless PNG optimization is checked by decoding and comparing all RGB
bytes. The manifest records window/crop rectangles in display coordinates,
cell dimensions, content rows, commands, AT IDs, hashes, sizes and tool versions.

Only the repository's synthetic `comments_mixed_bad.jsonl` and `secrets.jsonl`
fixtures are used. In the copied secrets input, the first row's synthetic URL
is shortened from `https://example.org/api/v1/feed?` to `https://example.org/?`
and its label to `存取網址：` so the redaction fits together at 60 columns.
The synthetic token and source labels are unchanged; this is input data, not an
edit to CLI output. Muse is explicitly configured with `backend = "fixture"`.
No credentials, real account, external network, send or publication is involved.
The text file beside every image is its accessible transcript, linked from both
languages on the page. The hero is a separate 60-column capture of the same packet.

## Frames

### 01-setup.png (AT-001)

Offline init, explicit fixture config, and doctor capability rows.

```sh
cd /tmp/metamuse-demo
metamuse --workspace ws init
python3 configure_demo_workspace.py
sed -n '/^network_mode/p;/^backend/p' ws/metamuse.toml
metamuse --workspace ws doctor > out/doctor.json
jq -cC '{network_mode, worker_available}, (.capabilities[] | [.name, .verification, .effective])' out/doctor.json
```

### 02-queue.png (AT-003, AT-008)

Accepted and queued without a worker; run once, then partial with 7 committed and 7 rejected.

```sh
metamuse --workspace ws project create --name 'Synthetic demo' --request-key project > out/project.json
P=$(jq -r .id out/project.json)
metamuse --workspace ws --json add --project "$P" --file inputs/mixed.jsonl --kind jsonl --request-key import > out/add.json
jq -C '{status, state: .data.state, worker_available: .data.worker_available}' out/add.json
J=$(jq -r .data.job_id out/add.json)
metamuse --workspace ws worker run --once
metamuse --workspace ws jobs get "$J" > out/job.json
jq -C '.job | {state, coverage_status}' out/job.json
jq -cC '.steps[0].result.observed_scope | {committed, rejected}' out/job.json
jq -cC '.steps[0].result.data.rows[] | select(.status == "rejected") | {line, code}' out/job.json
```

### 03-search.png (AT-017)

One exact item revision, with file_import and synthetic_fixture source labels.

```sh
metamuse --workspace ws search --project "$P" --query 正常留言第一行 --limit 1 > out/search.json
jq -C '.results[0] | {ref, snippet, source}' out/search.json
```

### 04-packet.png (AT-015, AT-028)

Exact packet text, one url_token redaction, and its SHA-256. Nothing is sent.

```sh
metamuse --workspace ws add --project "$P" --file inputs/secrets.jsonl --kind jsonl --request-key secrets --run > out/secrets.json
metamuse --workspace ws search --project "$P" --query 存取網址 --limit 1 > out/secret-search.json
jq '[.results[0].ref]' out/secret-search.json > inputs/selection.json
metamuse --workspace ws packet prepare --project "$P" --selection-file inputs/selection.json --request-key packet > out/packet.json
jq -r .rendered_text out/packet.json
jq -C '{redactions, digest}' out/packet.json
```

### 05-draft.png (AT-023)

Host-supplied text stored as written, citing an exact item revision; no model call.

```sh
printf '%s\n' '活動紀錄（host 撰寫）' '本地留言仍在。壞列沒有被算成完整匯入。' > inputs/draft.txt
jq '[.results[0].ref]' out/search.json > inputs/facts.json
metamuse --workspace ws draft save --project "$P" --text-file inputs/draft.txt --platform generic --facts-file inputs/facts.json --request-key draft > out/draft.json
jq -C '{draft_id, revision, text, facts_refs}' out/draft.json
```

### 06-export.png (AT-025)

Local bundle manifest and the CLI's no-publish, no-upload, redaction-guard warnings.

```sh
D=$(jq -r .draft_id out/draft.json)
jq -n --arg id "$D" '[{kind:"draft_revision", id:$id, revision:1}]' > inputs/export-refs.json
metamuse --workspace ws export --project "$P" --refs-file inputs/export-refs.json --format bundle --request-key export > out/export.json
jq -C '.manifest | {format, text_policy, asset_policy, redaction_notice}' out/export.json
jq -cC '.manifest.files[]' out/export.json
```

### 07-mcp.png (AT-026)

Official mcp stdio client lists 15 tools and readOnly hints, then calls metamuse_search.

```sh
python3 mcp_demo.py
```

### 08-skill.png (AT-027)

Skill export writes SKILL.md and references to the named local destination.

```sh
metamuse skill export --dest host-skill
ls -R host-skill
```

### 00-hero.png (AT-015, AT-028)

The same packet's exact text, redaction count, and digest, captured for the hero.

```sh
jq -r .rendered_text out/packet.json
jq -cC '{redactions, digest}' out/packet.json
```

## Reproduce

With Alacritty, Xvfb, xdotool, jq and the already-synced project virtualenv:

```sh
UV_OFFLINE=1 uv run --script scripts/capture_site.py
```

The capture uses a pinned Pillow script dependency; it is not a product dependency.
The script rejects local home paths, the operator's username or hostname, and any
term from `SITE_PRIVATE_DENYLIST`. Commands over two rows and scenes over their row
budget fail. The script stops its own terminal and virtual display on exit.

Skill export demonstrates installation output, not full AT-027 missing-backend
acceptance. MCP shows discovery and one search call, not every host/tool pairing.
