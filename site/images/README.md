# Terminal captures

Real Alacritty on a newly allocated Xvfb display; `WINIT_X11_SCALE_FACTOR=2`.
The shell prints each literal command and executes it from `/tmp/metamuse-demo`.
Variables set visibly in earlier scenes are retained between frames.

Each PNG keeps the full window width and its original top edge, ending one empty
terminal row below the last ink. Pixels within that rectangle are unedited.
Pillow's lossless PNG optimization is checked by decoding and comparing all RGB
bytes. The manifest records window/crop rectangles in display coordinates,
cell dimensions, content rows, commands, AT IDs, hashes, sizes and tool versions.

The CLI inputs use the repository's synthetic `comments_mixed_bad.jsonl` and
`secrets.jsonl` fixtures. In the copied secrets input, the first row's synthetic URL
is shortened from `https://example.org/api/v1/feed?` to `https://example.org/?`
and its label to `存取網址：` so the redaction fits together at 60 columns.
The synthetic token and source labels are unchanged; this is input data, not an
edit to CLI output. Muse is explicitly configured with `backend = "fixture"`.
No credentials, real account, external network or publication is involved.
Muse sends go only to the offline fixture backend. Operator approvals are scripted
terminal input in this reproducible synthetic demo; a host has no MCP approval tool.
Full send/read operator reviews are redirected to demo out/ files to keep their
frames compact; the open approval frame displays the real stderr review in full.
The MCP input is the first three rows of the synthetic comments_tree.jsonl fixture.
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
K=$(jq -r .id out/packet.json)
```

### 05-approval-preview.png (AT-016, AT-020)

A grant-free Muse open returns E_SCOPE_REQUIRED with the exact action preview.

```sh
metamuse --workspace ws --json --non-interactive muse open --project "$P" --topic "Synthetic review" --request-key open > out/open-review.json || test $? -eq 3
jq -C '{status, error: .error.code, approval: .approval | {operation, action_digest, destination, topic, request}}' out/open-review.json
read R A < <(jq -r ".approval | [.request_file, .action_digest] | @tsv" out/open-review.json)
jq .approval.request out/open-review.json > "$R"
```

### 06-human-approval.png (AT-015, AT-016, AT-020)

The operator previews the packet and approves the exact digest; Muse open is accepted.

```sh
metamuse --workspace ws packet preview "$K" > out/packet-preview.json
jq -r .rendered_text out/packet-preview.json
metamuse --workspace ws --json permit approve --operation muse.open --request-file "$R" --exact-digest "$A" --request-key open-grant > out/open-grant.json
G=$(jq -r .data.id out/open-grant.json)
metamuse --workspace ws --json muse open --project "$P" --topic "Synthetic review" --access-grant "$G" --request-key open > out/open.json
jq -C '{status, data: .data | {state, discussion_id, worker_available}}' out/open.json
T=$(jq -r .data.discussion_id out/open.json)
```

### 07-fixture-discussion.png (AT-016, AT-020, AT-021, AT-022, AT-024)

Approved fixture question, worker refresh, cached reply and explicit needs_more_data closure.

```sh
metamuse --workspace ws worker run --once > out/w-open.json
printf '%s' '這份資料有哪些資訊缺口？' > inputs/q1.txt
metamuse --workspace ws --json muse send --discussion "$T" --packet "$K" --question-file inputs/q1.txt --request-key send-1 > out/s1-review.json || test $? -eq 3
metamuse --workspace ws packet preview "$K" > out/p1.json
read R A < <(jq -r ".approval | [.request_file, .action_digest] | @tsv" out/s1-review.json)
jq .approval.request out/s1-review.json > "$R"
metamuse --workspace ws --json permit approve --operation muse.send --request-file "$R" --exact-digest "$A" --request-key sg1 > out/sg1.json 2> out/sg1.txt
G=$(jq -r .data.id out/sg1.json)
metamuse --workspace ws --json muse send --discussion "$T" --packet "$K" --question-file inputs/q1.txt --share-grant "$G" --request-key send-1 > out/s1.json
E=$(jq -r .data.exchange_id out/s1.json)
metamuse --workspace ws worker run --once > out/ws1.json
metamuse --workspace ws --json muse read --exchange "$E" --refresh > out/r1-review.json || test $? -eq 3
read R A < <(jq -r ".approval | [.request_file, .action_digest] | @tsv" out/r1-review.json)
jq .approval.request out/r1-review.json > "$R"
metamuse --workspace ws --json permit approve --operation muse.read --request-file "$R" --exact-digest "$A" --request-key rg1 > out/rg1.json 2> out/rg1.txt
G=$(jq -r .data.id out/rg1.json)
metamuse --workspace ws muse read --exchange "$E" --refresh --access-grant "$G" > out/rf1.json
metamuse --workspace ws worker run --once > out/wr1.json
metamuse --workspace ws --json muse read --exchange "$E" > out/r1.json
jq -cC '.data | {discussion_id, exchange_id, backend, mock, research_complete}' out/r1.json
jq -cC '.data.messages[] | select(.role == "assistant") | {role, text, association}' out/r1.json
metamuse --workspace ws muse close-exchange "$E" --outcome needs_more_data --request-key close-1 > out/c1.json
jq -cC '{outcome, research_complete}' out/c1.json
```

### 08-fixture-follow-up.png (AT-016, AT-020, AT-021, AT-022, AT-024)

Approved fixture question, worker refresh, cached reply and explicit needs_more_data closure.

```sh
printf '%s' '哪些缺口會影響這個判斷？' > inputs/q2.txt
metamuse --workspace ws --json muse send --discussion "$T" --packet "$K" --question-file inputs/q2.txt --request-key send-2 > out/s2-review.json || test $? -eq 3
metamuse --workspace ws packet preview "$K" > out/p2.json
read R A < <(jq -r ".approval | [.request_file, .action_digest] | @tsv" out/s2-review.json)
jq .approval.request out/s2-review.json > "$R"
metamuse --workspace ws --json permit approve --operation muse.send --request-file "$R" --exact-digest "$A" --request-key sg2 > out/sg2.json 2> out/sg2.txt
G=$(jq -r .data.id out/sg2.json)
metamuse --workspace ws --json muse send --discussion "$T" --packet "$K" --question-file inputs/q2.txt --share-grant "$G" --request-key send-2 > out/s2.json
E=$(jq -r .data.exchange_id out/s2.json)
metamuse --workspace ws worker run --once > out/ws2.json
metamuse --workspace ws --json muse read --exchange "$E" --refresh > out/r2-review.json || test $? -eq 3
read R A < <(jq -r ".approval | [.request_file, .action_digest] | @tsv" out/r2-review.json)
jq .approval.request out/r2-review.json > "$R"
metamuse --workspace ws --json permit approve --operation muse.read --request-file "$R" --exact-digest "$A" --request-key rg2 > out/rg2.json 2> out/rg2.txt
G=$(jq -r .data.id out/rg2.json)
metamuse --workspace ws muse read --exchange "$E" --refresh --access-grant "$G" > out/rf2.json
metamuse --workspace ws worker run --once > out/wr2.json
metamuse --workspace ws --json muse read --exchange "$E" > out/r2.json
jq -cC '.data | {discussion_id, exchange_id, backend, mock, research_complete}' out/r2.json
jq -cC '.data.messages[] | select(.role == "assistant") | {role, text, association}' out/r2.json
metamuse --workspace ws muse close-exchange "$E" --outcome needs_more_data --request-key close-2 > out/c2.json
jq -cC '{outcome, research_complete}' out/c2.json
```

### 09-host-draft.png (AT-023, AT-024)

Host text stored unchanged, citing the item revision and both fixture observations.

```sh
printf '%s\n' '活動紀錄（host 撰寫）' '首輪與追問仍有缺口，尚未完成研究。' > inputs/draft.txt
jq '[.results[0].ref] + [inputs | .data.observations[-1].evidence_ref]' out/search.json out/r1.json out/r2.json > inputs/facts.json
metamuse --workspace ws draft save --project "$P" --text-file inputs/draft.txt --platform generic --facts-file inputs/facts.json --request-key draft > out/draft.json
jq -C '{draft_id, revision, text}' out/draft.json
jq -cC '.facts_refs[]' out/draft.json
```

### 10-local-export.png (AT-025)

Local bundle manifest and the CLI's no-publish, no-upload, redaction-guard warnings.

```sh
D=$(jq -r .draft_id out/draft.json)
jq -n --arg id "$D" '[{kind:"draft_revision", id:$id, revision:1}]' > inputs/export-refs.json
metamuse --workspace ws export --project "$P" --refs-file inputs/export-refs.json --format bundle --request-key export > out/export.json
jq -C '.manifest | {format, backend, mock, research_complete, text_policy, asset_policy, redaction_notice}' out/export.json
jq -cC '.manifest.files[]' out/export.json
```

### 11-mcp-worker.png (AT-008, AT-026)

This captured demo discovers the original fifteen P0 tools, collects a synthetic file and polls the attached worker. The current server lists nineteen tools, including four experimental publication tools; this historical image is not publication evidence.

```sh
python3 mcp_demo.py
```

### 12-skill-export.png (AT-027)

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
acceptance. MCP shows discovery, local collection, attached-worker polling and one search,
not every host/tool pairing. Fixture replies are progress, not completed research.
