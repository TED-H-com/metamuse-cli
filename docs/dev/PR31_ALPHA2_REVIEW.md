# PR #31 review and second-alpha candidate, 2026-10-18

This section records the review as of 2026-10-18; see the dated follow-up below.
PR #31 has no blocking finding from this review. Its final hosted CI is green,
and its tested merge tree is identical to the PR head. A local `0.1.0a2` candidate
has passed installed-wheel verification. It has not been pushed or published;
PR #31 remains open. This report does not grant merge, publication or LIVE approval.

## Scope and source identity

- Reviewed PR: [#31](https://github.com/TED-H-com/metamuse-cli/pull/31), head
  `5afa6cca3a3c6ba09ec164488515ca7cd9a7df68`.
- Hosted [CI run 39110788812](https://github.com/TED-H-com/metamuse-cli/actions/runs/39110788812)
  tested merge commit `1158c822b6bb10911f2d0b58de2d09f04a00a1ee`, whose parents
  are `365c25e69e757837c5c6564e689b373e73940239` and the PR head. Both the merge
  and PR head have tree `491c798784e1f63d416ccb7119e190772c6f0efb`.
- Candidate commit: `a460969dea4084b099aa468d0eb7538b2cfe1680`, on local branch
  `review/pr31-alpha-followup`, based on the reviewed PR head. The subsequent
  report commit only adds this document; the artifacts below were built from
  the clean candidate commit, before this report was added.
- Candidate scope: B17; FR-018/FR-021. Review/regression scope additionally covers
  B09/B10/B16/B19 and FR-013/FR-014/FR-019/FR-027/FR-028.
- Acceptance coverage: AT-019/020/021/022 (Muse delivery and association),
  AT-025/026/035 (exports, MCP and package verification), AT-042 fixture journey,
  and AT-047/048 (publication approval and uncertain-delivery recovery).
  PR #31's bounded AT-038/043/049 LIVE observations are inherited evidence only;
  this review ran no LIVE operation. AT-035's private-string scan is limited as
  described below.

## Findings and changes

The Page comparator keeps the approved/sent/stored message intact. It permits
exact equality or omission of one terminal LF, rejects terminal CRLF removal and
other whitespace/text changes, and still verifies receipt ID, Page identity and
published state. It records both message hashes. The recovery path settles the
original submission rather than sending a second post.

The Muse changes use bounded native request/response shapes and preserve explicit
nested session identity. Exact user content takes precedence over presentation
text. Unsupported credentials and response wrappers stop; uncertain actions and
historical frozen mutations are not rewritten or replayed. The server endorsement
shape check is not represented as local signature verification.

The release procedure previously required `main` for publication but also said to
merge the release branch only after uploading. It now distinguishes the normal
main-first workflow from the separately approved local upload alternative. The
exact main commit must pass CI before the normal publication workflow can run.

Files changed after the reviewed PR head:

| File | Change |
| --- | --- |
| `pyproject.toml` | Candidate version `0.1.0a2`; dependencies unchanged. |
| `uv.lock` | Only the root package version changes. |
| `src/metamuse/__init__.py` | Runtime version matches the distribution. |
| `.github/workflows/publish.yml` | Manual dispatch defaults to the candidate version; gates unchanged. |
| `docs/ALPHA_RELEASE.md` | Candidate notes, recovery and evidence limits, consistent release sequence. |
| `docs/GETTING_STARTED.md` | Replace obsolete blanket Page-verification wording with the bounded result. |
| `docs/dev/STATUS.md` | Distinguish fixture evidence from separately recorded bounded LIVE results. |
| `docs/dev/PR31_ALPHA2_REVIEW.md` | This review and reproducible verification record. |

## Commands and actual results

Commands below ran from the isolated candidate checkout. `EVIDENCE` denotes its
evidence directory outside the repository; `RUNNER_TEMP` and `TMPDIR` were set to
disk scratch for installed checks. These paths contain synthetic test data only.

| Command | Result and evidence class |
| --- | --- |
| `gh pr view 31 --repo TED-H-com/metamuse-cli --json headRefOid,state,isDraft,mergeStateStatus,reviewDecision,statusCheckRollup` | Head `5afa6cc`, open/ready, merge state CLEAN, all 13 checks SUCCESS; no submitted reviews. Hosted CI status, not LIVE product evidence. |
| `gh run download 39110788812 --repo TED-H-com/metamuse-cli --pattern 'ci-*' --dir "$EVIDENCE/hosted-ci"` | Downloaded all five cohort artifacts; copied their JSON manifests into `hosted-manifests` for the existing verifier. |
| `python3 scripts/ci_cohorts.py verify --manifest-dir "$EVIDENCE/hosted-manifests" --source-sha 1158c822b6bb10911f2d0b58de2d09f04a00a1ee` | Verified exact, nonoverlapping full-suite execution: 8,346 nodes. Logs show core 4,173, Muse 1,929, media 654, Facebook 724, privacy 866 passed; zero failures/skips. OFFLINE/FIXTURE/INTEGRATION, independently checked hosted evidence. |
| `uv sync --locked --python 3.12.13 --group dev --extra remote --extra facebook` | Passed with managed Python 3.12.13 and the existing lock. Dependency setup downloads are separate from offline tests. |
| `uv run --locked pytest -q tests/test_publishing.py tests/test_connected_workflow.py tests/test_muse_daemon_wire.py tests/test_muse_server_endorsement.py tests/test_muse_transport.py tests/test_muse_associate.py tests/test_muse_lifecycle.py tests/test_muse_research_replay.py tests/test_muse_review3.py tests/test_site_check.py` | 315 passed in 97.29 seconds on the reviewed PR head. OFFLINE/FIXTURE/INTEGRATION; no real accounts. |
| `uv lock --offline` | Failed because registry metadata for the Python >=3.14 resolution partition was absent from the cache. No dependency constraint was changed. |
| `uv lock` | Passed; only `metamuse-cli` changed from `0.1.0a1` to `0.1.0a2`. |
| `uv run --locked ruff check .` | Passed. OFFLINE. |
| `uv run --locked ruff format --check .` | Passed. OFFLINE. |
| `uv run --locked python docs/design/validate_design_pack.py` | Passed: 32 requirements, 22 backlog tasks, 54 specified acceptance cases, 19 tool designs and 21 local links. Executes zero product acceptance/LIVE tests. |
| `uv run --locked python tests/fixtures/corpus/check_corpus.py` | All seven integrity checks passed; all 34 registered fixture files match. OFFLINE. |
| `TMPDIR="$RUNNER_TEMP" scripts/smoke_wheel.sh` | Passed on `a460969`: isolated wheel import, packaged schemas/migrations/Skill/notices, init/doctor, synthetic demo/export hashes, offline account setup and 19-tool MCP stdio. OFFLINE/INTEGRATION. |
| `RUNNER_TEMP="$RUNNER_TEMP" scripts/check_installed_journeys.sh a460969dea4084b099aa468d0eb7538b2cfe1680 "$EVIDENCE/installed"` | Two installed CLI/MCP journeys passed in 32.53 seconds, zero failures/skips. Provenance `verified=true`, exit 0. Fixed and bounded fixture-Muse approval paths include follow-up, restart and cited exports. OFFLINE/FIXTURE/INTEGRATION. |
| `uv build --no-sources --out-dir "$EVIDENCE/dist"` | Built the wheel through the source distribution from the clean candidate commit. OFFLINE build inputs after dependency setup. |
| `python3 "$EVIDENCE/verify_artifacts.py" . "$EVIDENCE"` | All 99 wheel payloads equal source; both distribution versions and README bytes match; license/notices present; no symlink/hardlink in sdist or Git/venv/cache payloads. Local audit script and JSON result retained outside Git. |
| `git diff --check` | Passed. OFFLINE. |

The first ad hoc archive audit used the email parser's default text decoding for
the UTF-8 README and failed its comparison. Comparing the raw metadata body bytes
confirmed exact equality in both archives; the retained audit uses byte comparison.
This was an audit-script defect, not a package-content change.

## Candidate artifacts

These hashes identify the clean `a460969` build, not any future main/workflow build.
Both files, `SHA256SUMS`, `artifact-verification.json`, the audit script, installed
provenance/JUnit output and hosted CI artifacts remain outside the repository.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `metamuse_cli-0.1.0a2-py3-none-any.whl` | 431116 | `aaaa2d2749d47b80a57aaa7beeb5078521a1a7d3c64c87426bff3710c37b3477` |
| `metamuse_cli-0.1.0a2.tar.gz` | 4787421 | `17e38b240d5cc5bde69d73a1b97eb2835412469473cc97c90f7c5747489bb018` |

## Migrations, notices and core change requests

Migrations: none. The existing schema and stored approvals/intents remain unchanged.
No modifications to `models.py`, `store.py`, `jobs.py`, migrations or generated schemas.
No core change requests.

Dependencies and third-party notices: unchanged. No third-party source was added
or copied. Existing pinned dependencies and notices are reused and ship in the wheel.

## Limits and next authorized step

- The new candidate/report commits have no hosted CI yet. Full-suite hosted
  results above belong to the PR #31 tree; the candidate has local static,
  packaging and installed-journey checks. CI must run on the eventual integration
  and publication commits.
- No private runtime/evidence repository or real credential was read for this
  MetaMuse review. `METAMUSE_PRIVATE_DENYLIST` was unset, so the design validator's
  optional private-string scan was explicitly skipped. Package identity/license
  checks do not establish an exhaustive secret scan or new LIVE evidence.
- Prior Muse and Page LIVE journeys crossed candidate builds. Real Facebook
  collection and remote media remain unrun; the AT-036 reference-host deviation
  and unresolved historical demo-timeout cause remain. No timeout or durability
  setting was increased.
- Public README/site install commands still identify the available `0.1.0a1`.
  The candidate's packaged README matches that source. Promoting those guides and
  re-rendering the site is a post-publication step in `ALPHA_RELEASE.md`; this
  local candidate is not proof that `0.1.0a2` can be installed from PyPI.
- The orchestrator must integrate the reviewed PR and candidate, wait for the
  exact main commit's CI, obtain/confirm publication approval, and dispatch the
  existing workflow. Verify the index hashes and a fresh index installation before
  promoting public installation instructions. Do not resend any earlier Muse
  question or Page post as a release check.

## Orchestrator follow-up, 2026-10-19

The operator authorized this session to act as orchestrator and proceed with the
proposed push, merge and release sequence. PR #31 was merged at
`d82ae371b224b369819498964c5cc7aa635edb71`; its main CI is
[run 39204214535](https://github.com/TED-H-com/metamuse-cli/actions/runs/39204214535).
The earlier candidate hashes above remain historical, not final release hashes.

Migration inventory inspection found no tracked-change patches or saved stashes
for this workspace. All 29 recorded worktrees have zero tracked modifications;
28 detached review heads were preserved under local archival refs from the bundle.
The sole untracked archive contains 4,000 files, all under `runs/`, with no absolute
or parent-traversal paths. These are retained review outputs, not product changes;
the archive was not applied or extracted into the public checkout. Historical
review heads were not cherry-picked over the newer accepted implementation.
No credential or private-runtime file was opened for this inventory check.

Publication preparation now updates the README before building, because it is the
immutable PyPI long description. README/getting-started/connected instructions
target `0.1.0a2` and link to the index for availability; descriptions identify which
version contains the fixes without asserting that an upload has happened. The
homepage install command and displayed version remain `0.1.0a1` until verified
publication. This replaces the earlier plan to update all installation text only
after uploading, which would have left the wheel's PyPI description on `0.1.0a1`.

README and bilingual site availability now require `0.1.0a2` and account setup for
Muse/Page operations. Existing bounded LIVE limits remain. Changes are confined
to documentation and the site's availability vocabulary; no runtime, dependency,
schema, migration or third-party notice change was added.

Additional verification: `uv run --locked pytest -q tests/test_site_check.py`
passed all 42 cases in 0.12 seconds. The pinned page-kit `render.py` and
`PAGE_KIT_DIR=... uv run --locked python scripts/check_site.py` passed, as did Ruff
check/format and `git diff --check`. These are OFFLINE checks, not new LIVE evidence.

`uv run --locked python -m playwright install chromium` installed the locked
browser for a loopback-only visual check. Launching it with
`chromium_sandbox=True` failed because this host has no usable Chromium sandbox.
No screenshots were produced; sandboxing and host policy were not weakened.
The static render/parity checks passed, but this follow-up has no new visual captures.
