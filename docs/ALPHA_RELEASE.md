# First alpha: 0.1.0a1

This document describes the first alpha and its publication procedure. The
installation and bundled example are in [Getting started](GETTING_STARTED.md).
Operator approval to publish does not itself establish that an upload succeeded.

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
received equivalent end-to-end acceptance. The design's full reference-environment
performance target (AT-036) remains a declared release deviation; measurements
below are not a performance SLA. Alpha does not change existing workspace schemas
or permission rules.

## Performance observations, 2026-10-17

The unchanged installed PyPI alpha was measured on Linux/Python 3.12.13, on a shared
host with 24 visible logical CPUs and about 61 GiB RAM. This differs from the
specified dedicated 4-vCPU/8-GiB SSD environment. All data was synthetic and offline.
Each CLI measurement includes process startup; p95 is the nearest-rank 29th value
of 30 samples.

| Workload | Observed result |
|---|---|
| 30 serial demos, each in a fresh workspace, under syscall tracing | 30 passed; p95 3.534 s, maximum 3.635 s. Export file hashes and source citations checked. |
| Submit a queued local import, with 10,000 records already stored; 30 fresh request keys | p95 0.623 s. Jobs remained queued without a worker; this measures submission, not execution. |
| Search the first 50 matches in those 10,000 records; 30 requests | p95 0.195 s, with 50 results checked each time. |
| Import exactly 20 MiB of JSONL containing those 10,000 records | 10,000 committed, none rejected; 31.450 s wall time, peak individual-process RSS 125,372 KiB (about 122 MiB). No browser or codec ran. |

The import used an explicit `limits.max_items = 10000`; the normal 500-item default
remains unchanged. Demo tracing separates child commands: init p95 0.609 s, import
with its worker p95 0.715 s, and the longest individual lock syscall below 0.5 ms in
these successful samples. It does not separately measure worker startup.

A separate installed initialization took 46.516 s and was observed waiting for a
filesystem journal commit. This is direct evidence of storage waiting in that run,
not proof of the earlier release demo timeouts' cause. Those timeouts remain open;
no timeout, durability setting or retry rule was changed. The measurements also do
not establish cold-cache installation performance or aggregate process-tree RSS.

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

## Configure the publisher

The release workflow is `.github/workflows/publish.yml`. It runs manually from
`main`, requires a successful GitHub Actions `check` for that exact commit, builds
the distributions, and passes those immutable artifacts to a separate publishing
job. Only that job receives `id-token: write`, in the `pypi` environment. It uses
PyPI Trusted Publishing; no stored PyPI token is needed.

For a first release through this workflow, the PyPI account owner must add a
[pending GitHub publisher](https://pypi.org/manage/account/publishing/) with:

| Field | Value |
|---|---|
| PyPI project name | `metamuse-cli` |
| GitHub owner | `TED-H-com` |
| Repository | `metamuse-cli` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

This account step can create the project on first publication. It does not reserve
the package name. See [PyPI's pending publisher documentation](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

## Publish after operator approval

Publishing to PyPI is a separate account action. Use the operator's configured
Trusted Publisher or a PyPI API token supplied through the environment; never put
credentials in a command, file in this repository, or release notes. The publishing
command names only the two reviewed artifacts. Before building those final
artifacts, prepare the README, getting-started guide and site installation commands
on the release branch. The README is the package's PyPI long description. Keep
these changes off `main` and the deployed site until the index upload and a fresh
installation have been verified.

The normal path is to dispatch the reviewed workflow after CI succeeds:

```sh
gh workflow run publish.yml --ref main -f version=0.1.0a1
```

The build artifact includes `SHA256SUMS`; retain it together with the workflow's
source commit. The publishing job checks the downloaded hashes before uploading
and then checks PyPI's reported file hashes. If upload fails or its result is
uncertain, inspect the index before rerunning; never rebuild different bytes under
the same published filename.

Alternatively, a reviewed release branch that has passed normal CI can be
published locally using an API token. Store it through the hidden prompt:

```sh
uv auth login https://upload.pypi.org/legacy/ --username __token__
```

uv stores this credential outside the repository. Read it directly into the
publishing process environment, without printing it or putting it in an argument.
Use `UV_PUBLISH_TOKEN` and the exact reviewed artifact paths:

```sh
uv publish dist/metamuse_cli-0.1.0a1-py3-none-any.whl dist/metamuse_cli-0.1.0a1.tar.gz
```

After confirming PyPI has the exact version and matching file hashes, verify a fresh
installation from the index using the prepared instructions:

```sh
uv tool install --python 3.12.13 'metamuse-cli==0.1.0a1'
metamuse demo
```

Then merge the release branch and deploy the rendered site. Keep the real-account
limitations visible. Version and alpha classification follow the
[Python packaging versioning guide](https://packaging.python.org/en/latest/discussions/versioning/);
build and publication commands follow the
[uv package guide](https://docs.astral.sh/uv/guides/package/).
