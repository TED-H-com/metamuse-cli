# Alpha release: 0.1.0a2

`0.1.0a2` is available on [PyPI](https://pypi.org/project/metamuse-cli/0.1.0a2/).
It includes the Muse compatibility and Facebook Page reconciliation corrections
from [PR #31](https://github.com/TED-H-com/metamuse-cli/pull/31). The
[GitHub release](https://github.com/TED-H-com/metamuse-cli/releases/tag/v0.1.0a2)
records the source commit, publication workflow, matching distribution hashes and
fresh index-install verification. The README, [getting-started instructions](GETTING_STARTED.md)
and public site now install `0.1.0a2`.

The commands below document this release procedure. Future releases need a new
version; published distribution files cannot be replaced with different bytes.

## Changes in 0.1.0a2

- Muse standard-VM connections accept the bounded server-issued credential shape
  and use native session, text and history requests. Wrapped replies and nested
  session identities are checked. Unsupported VM tiers, malformed credentials
  and failed Noise checks still stop; local credential-signature verification is
  not claimed.
- Facebook Page read-back permits one omitted terminal LF while retaining the
  exact approved and sent text. Receipt, Page and published-state checks remain
  required. The result records comparison mode and both message hashes.
- Previously frozen Muse mutations are not rewritten or resent. A saved Page
  receipt can be reconciled with the original publication after upgrade; an
  uncertain action is never resolved by submitting another post or question.

There are no new dependencies, third-party code, migrations, permission changes
or core model/store/jobs changes. Existing workspace data remains in place;
normal startup and read-only reconciliation use the existing contracts.

The alpha lets you import local comments and subtitles, search the original text,
keep fixed source references, save your own or your assistant's drafts, and export
Markdown, text, CSV, JSONL, or a bundle. `metamuse demo` includes synthetic comments
and a prewritten recap, so an installed wheel can demonstrate the workflow without
a checkout, account, or AI service. It keeps each run in a new private workspace.

Muse conversations and Page publishing have bounded LIVE evidence in PR #31:
two Muse turns with restart/read-back and cited exports, and one Page post settled
by read-only reconciliation with one attempt preserved after restart. Both journeys
crossed candidate builds while defects were repaired; neither establishes a fresh
end-to-end run of this unchanged release. Remote-media and Facebook-collection LIVE
acceptance remain unrun. Account setup and separately scoped approval are still
required. This remains an alpha, with no broader beta or cross-platform acceptance
claim. See [the connected workflow](CONNECTED_WORKFLOW.md) and the README table.

Python 3.12 on Linux is the reference environment. Other operating systems have not
received equivalent end-to-end acceptance. The design's full reference-environment
performance target (AT-036) remains a declared release deviation; measurements
below are not a performance SLA. Alpha does not change existing workspace schemas
or permission rules.

## Performance observations, 2026-10-17

The unchanged installed PyPI `0.1.0a1` was measured on Linux/Python 3.12.13, on a shared
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

Review both `metamuse_cli-0.1.0a2-py3-none-any.whl` and
`metamuse_cli-0.1.0a2.tar.gz`. Record their SHA-256 hashes and source commit in the
release evidence. Both archives must contain the license and notices; the wheel
must contain the synthetic example. Keep credentials and private evidence outside
both archives and the public repository.

## Configure the publisher

The release workflow is `.github/workflows/publish.yml`. It runs manually from
`main`, requires a successful GitHub Actions `check` for that exact commit, builds
the distributions, and passes those immutable artifacts to a separate publishing
job. Only that job receives `id-token: write`, in the `pypi` environment. It uses
PyPI Trusted Publishing; no stored PyPI token is needed.

Use the existing Trusted Publisher for this update. Do not create a new project
or change account configuration as part of candidate verification. For an initial
setup only, the PyPI account owner can add a
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

Publishing to PyPI is a separate account action. Use the configured Trusted
Publisher or an explicitly supplied API token; never put credentials in a command,
repository file or release notes. Only the orchestrator pushes and merges under
the repository's [agent protocol](../AGENTS.md).

The normal workflow builds from `main`, so the orchestrator first merges the
reviewed candidate and waits for `check` to pass on that exact main commit.
PR CI on an earlier commit does not satisfy that gate. The README is also the
immutable PyPI long description, so prepare its `0.1.0a2` commands before building
the final artifacts. It describes the code in that version and points to the index
for availability, without claiming an upload succeeded. Keep the site's advertised
install version on `0.1.0a1` until the index is verified.

After operator approval, dispatch the reviewed workflow:

```sh
gh workflow run publish.yml --ref main -f version=0.1.0a2
```

The build artifact includes `SHA256SUMS`; retain it together with the workflow's
source commit. The publishing job checks the downloaded hashes before uploading
and then checks PyPI's reported file hashes. If upload fails or its result is
uncertain, inspect the index before rerunning; never rebuild different bytes under
the same published filename.

Alternatively, after separate approval, the orchestrator can publish the exact
reviewed artifacts from a release branch that passed CI, using an API token.
Store it through the hidden prompt:

```sh
uv auth login https://upload.pypi.org/legacy/ --username __token__
```

uv stores this credential outside the repository. Read it directly into the
publishing process environment, without printing it or putting it in an argument.
Use `UV_PUBLISH_TOKEN` and the exact reviewed artifact paths:

```sh
uv publish dist/metamuse_cli-0.1.0a2-py3-none-any.whl dist/metamuse_cli-0.1.0a2.tar.gz
```

After confirming PyPI has the exact version and matching file hashes, verify a fresh
installation from the index using the prepared instructions:

```sh
uv tool install --python 3.12.13 'metamuse-cli==0.1.0a2'
metamuse demo
```

Only after that succeeds, update the installation command and displayed version
in `site/page.json` to `0.1.0a2`, and record the publication in this document.
Retain the bounded LIVE evidence and alpha limits. Re-render with the pinned page
kit, validate README/site parity, and let the orchestrator merge and deploy that
documentation update. For the alternative local
upload path, the release branch also needs merging after the index is verified.

Version and alpha classification follow the
[Python packaging versioning guide](https://packaging.python.org/en/latest/discussions/versioning/);
build and publication commands follow the
[uv package guide](https://docs.astral.sh/uv/guides/package/).
