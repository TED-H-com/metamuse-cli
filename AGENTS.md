# AGENTS.md — MetaMuse CLI

## Product contract (binding; copied from docs/design/AGENTS.md)

先讀01_PLAN、02_SPEC、03_SDS，再依04_BACKLOG的B01–B04實作。這是單一工具，不是五個repo launcher。

一package、一store、一jobs、一設定。CLI/MCP共用app；不做submodules、runtime git clone、plugin framework或第二個AI orchestrator。

主Agent產問題與文案；Muse是協作者，不接管harness或當逐點擊模型。不帶作者策略、人設、F公式、CTA或黃金時段。

採用第三方程式先記license／commit／原path；沒授權不可機械翻寫。成熟依賴不重造。public包不含秘密、真實私有內容或部署資料。

accepted不等於running；沒worker不得說在背景執行。安全讀取與外部send重試分開，不明送達先對帳。来源保真，partial不冒稱complete，模型內容不能mint permissions。

LIVE只在操作者明確授權後執行。缺憑證則fixture＋blocked，不編造結果。不因本文件自動建立repo、發布、改visibility或操作帳號。

交付每個PR的FR/B/AT、實際命令／證據類別、migration、notice與限制。先離線縱切片，Muse spike早做；不要先做Dashboard。核心models/store/jobs修改先協調owner。

Design baseline: `docs/design/` (01_PLAN, 02_SPEC, 03_SDS, 04_BACKLOG, 05_ACCEPTANCE, 06_SOURCES, contracts/). IDs FR-xxx (requirements), Bxx (backlog), AT-xxx (acceptance) come from there. If code and design disagree, either fix the code or update the design docs/contracts in the same commit and call it out in your report. Never maintain two sources of truth.

## Multi-agent development protocol

Several coding agents (Codex, Grok, Gemini) work in parallel, each in its own git worktree on its own branch. An orchestrator assigns tasks, independently re-runs tests, reviews, and merges into `main`.

- Work only inside your assigned worktree/branch and commit there. Never push, add remotes, create hosted repos, publish packages, or touch other worktrees or `main`. This repo is public. Only the orchestrator pushes branches, opens PRs and merges. Keys, account settings, real data and LIVE evidence live in a separate private repo; never read, copy or reference it unless your task prompt explicitly grants access.
- Ownership: `src/metamuse/models.py`, `store.py`, `jobs.py`, migrations and generated schemas belong to the core owner (B02–B04). Other tasks must not edit them unless their task prompt grants it. If you need a core change, build against the existing API and add a "Core change requests" section to your report (what, why, proposed diff).
- Extension points that other tasks may use without editing core files are documented in `docs/dev/CORE_API.md` once it exists (operation handler registry, AppContext, error/envelope helpers).
- Toolchain: Python 3.12 managed by uv. `uv sync`, `uv run pytest -q`, `uv run ruff check .`. No system pip. Add dependencies only with `uv add` and justify each (license, why a mature dependency instead of hand-rolled code).
- Tests never touch the network or real accounts. LIVE work is out of scope unless a task prompt explicitly authorizes it; without credentials the result is fixture + `blocked`, never an invented success.
- No secrets, cookies, real private content, hostnames, account settings or personal data in the repo. Fixtures are synthetic and redistributable.
- License: Apache-2.0 (`LICENSE`). Third-party code only under a compatible license, with provenance and an entry in `THIRD_PARTY_NOTICES.md`.
- Runtime data lives outside the repo (platformdirs private root); tests use temporary directories.
- Evidence classes: OFFLINE, FIXTURE, INTEGRATION, MEASURE, LIVE. Mock, schema or `--help` success is never LIVE.
- Done means: tests green, ruff clean, work committed on your branch, and the report written to the path in your task prompt. The report lists FR/B/AT covered, files changed, exact commands with real results, evidence class, migrations, third-party notices, known limitations, and core change requests.
- Commit messages: imperative summary line; body lists the B/FR/AT IDs; trailer `Agent: <cli>/<model>`.
