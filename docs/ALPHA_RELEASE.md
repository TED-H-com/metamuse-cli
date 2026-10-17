# First alpha: 0.1.0a1

This version is a release candidate, not a published PyPI package. The current
source installation and bundled example are in [Getting started](GETTING_STARTED.md).

The alpha lets you import local comments and subtitles, search the original text,
keep fixed source references, save your own or your assistant's drafts, and export
Markdown, text, CSV, JSONL, or a bundle. `metamuse demo` includes synthetic comments
and a prewritten recap, so an installed wheel can demonstrate the workflow without
a checkout, account, or AI service. It keeps each run in a new private workspace.

Muse conversations, permitted remote collection, and Facebook Page text publishing
have implementation and synthetic coverage. They still require your own account
setup and separately scoped approval. Real-account acceptance remains unverified;
this alpha does not claim the beta or LIVE publishing gates have passed. See
[the connected workflow](CONNECTED_WORKFLOW.md) and the README capability table.

Python 3.12 on Linux is the reference environment. Other operating systems have not
received equivalent end-to-end acceptance. The design's full performance target
(AT-036: 30-run p95 and RSS) is a declared release deviation; no performance SLA is
claimed. Alpha does not change existing workspace schemas or permission rules.

## Prepare and verify

Build from a clean, reviewed commit after normal CI passes:

```sh
uv sync --locked --python 3.12.13 --group dev --extra remote --extra facebook
uv run ruff check .
uv run ruff format --check .
uv run python docs/design/validate_design_pack.py
scripts/smoke_wheel.sh
uv build --no-sources
```

The smoke check installs the wheel outside the checkout, checks packaged migrations,
schemas, Skill and notices, runs the synthetic demo and verifies its Markdown hashes,
and checks CLI setup and MCP stdio. CI also runs the full offline cohorts and
installed journeys. None of this establishes real-account success.

Review both `metamuse_cli-0.1.0a1-py3-none-any.whl` and
`metamuse_cli-0.1.0a1.tar.gz`. Record their SHA-256 hashes and source commit in the
release evidence. Both archives must contain the license and notices; the wheel
must contain the synthetic example. Keep credentials and private evidence outside
both archives and the public repository.

## Publish only after operator approval

Publishing to PyPI is a separate account action. Use the operator's configured
Trusted Publisher or a PyPI API token supplied through the environment; never put
credentials in a command, file in this repository, or release notes. The publishing
command names only the two reviewed artifacts:

```sh
uv publish dist/metamuse_cli-0.1.0a1-py3-none-any.whl dist/metamuse_cli-0.1.0a1.tar.gz
```

After confirming PyPI has the exact version and matching file hashes, verify a fresh
installation from the index. Then replace the source installation in the README,
getting-started guide, and `site/page.json` with:

```sh
uv tool install --python 3.12.13 'metamuse-cli==0.1.0a1'
metamuse demo
```

Render and deploy the site after updating those claims. Keep the real-account
limitations visible. Version and alpha classification follow the
[Python packaging versioning guide](https://packaging.python.org/en/latest/discussions/versioning/);
build and publication commands follow the
[uv package guide](https://docs.astral.sh/uv/guides/package/).
