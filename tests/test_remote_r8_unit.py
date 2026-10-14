"""OFFLINE/FIXTURE/MEASURE boundary probes; no imports from review scratch."""

import json
import time
from pathlib import Path

import pytest
from test_remote_media import (  # noqa: F401
    MetamuseFixtureIE,
    configure,
    egress_guards,
    fixture_extractor,
    issue,
    netscape,
    no_dns,
    put_secret,
    remote_request,
    resolver,
    route_checks,
    url_for,
)
from test_remote_media import (
    origin as remote_origin,
)
from yt_dlp.extractor.common import InfoExtractor

from metamuse import app, jobs, remote_media, safety
from metamuse import models as m

origin = remote_origin


def test_r8_remote_docs_match_approval_and_identity_contract():
    root = Path(__file__).parents[1]
    collect_doc = (root / "docs/dev/COLLECT.md").read_text()
    outer = collect_doc.split("Outer, only when", 1)[1].split("\n\n", 1)[0]
    assert all(
        name not in outer for name in ("channel_id", "channel_url", "uploader_id", "uploader_url")
    )
    assert "`__rd_verify` (case-insensitive, with or without a preceding slash)" in collect_doc
    for name in (
        "docs/dev/COLLECT.md",
        "docs/dev/CORE_API.md",
        "docs/design/02_SPEC.md",
        "docs/design/03_SDS.md",
    ):
        text = (root / name).read_text()
        assert "ASCII-only canonical" in text
        assert "Fragments are not fetched" not in text
        assert "not fetched. The action digest continues to bind original bytes" not in text


def test_r8_canonical_extraction_preserves_original_url_provenance(ctx, project):
    seen = []

    class ReviewCanonicalIE(InfoExtractor):
        _VALID_URL = r"https://xn--bcher-kva\.fixture\.test/(?P<id>clip)"

        def _real_extract(self, url):
            seen.append(url)
            return {"id": "clip", "title": "Synthetic", "url": url, "ext": "wav"}

    remote_media.register_test_extractor(ReviewCanonicalIE)
    try:
        configure(ctx, [("ReviewCanonical", ["bücher.fixture.test"])])
        original = "https://BÜCHER.fixture.test:443/clip/中文?note=\u3164"
        expected = "https://xn--bcher-kva.fixture.test/clip/%E4%B8%AD%E6%96%87?note=%E3%85%A4"
        _req, _grant, job_id = accept(ctx, project, original)
        run_once(ctx)
        job = ctx.store.get(m.Job, job_id)
        assert job.state == "succeeded", job.error
        assert seen == [expected]
        assert job.payload["inputs"][0]["url"] == original
        with ctx.store.connection() as conn:
            source = conn.execute("SELECT original_url FROM sources").fetchone()
        assert source["original_url"] == original
    finally:
        remote_media.unregister_test_extractor(ReviewCanonicalIE)


def test_r8_expanded_canonical_url_must_fit_outbound_bound(ctx, project):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    url = "https://media.fixture.test/clip?note=" + "\u3164" * 1000
    assert len(url.encode()) < safety.URL_MAX_BYTES
    envelope = app.submit_collect(ctx, remote_request(project, url, ["media_metadata"]))
    assert envelope.error.code == m.ErrorCode.SCOPE_DENIED
    assert envelope.approval is None
    assert app.list_jobs(ctx, m.JobListQuery(project_id=project)).data["jobs"] == []


def accept(ctx, project, url):
    req = remote_request(project, url, ["media_metadata"])
    shown = remote_media.preview_remote_media(ctx, req)
    from datetime import timedelta

    from test_remote_media import ACCOUNT, PROVIDER, datetime_from

    grant = safety.create_permit(
        ctx,
        m.PermitCreateRequest(
            project_id=project,
            kind="access",
            provider=PROVIDER,
            account=ACCOUNT,
            scope={"operations": ["collect.remote"], "target_digests": [shown.action_digest]},
            issuer="fixture-operator",
            expires_at=(datetime_from(ctx) + timedelta(days=1)).isoformat(),
            max_uses=1,
            exact_digest=shown.action_digest,
            request_key=m.new_id("req"),
        ),
    ).data["id"]
    granted = req.model_copy(update={"access_grant_id": grant})
    env = app.submit_collect(ctx, granted)
    assert env.status == "accepted", env.error
    return granted, grant, env.data["job_id"]


def run_once(ctx, **kw):
    with jobs.Executor(ctx.store, sleep=lambda seconds: None, **kw) as executor:
        assert executor.run_once()


class ReviewOuterIE(InfoExtractor):
    _VALID_URL = r"https://outer\.fixture\.test/clip/(?P<id>\d+)"
    hook = None

    def _real_extract(self, url):
        if self.hook:
            self.hook()
        return {
            "_type": "url_transparent",
            "url": "https://inner.fixture.test/video/77",
            "ie_key": "ReviewInner",
        }


class ReviewInnerIE(InfoExtractor):
    _VALID_URL = r"https://inner\.fixture\.test/video/(?P<id>\d+)"

    def _real_extract(self, url):
        return self._download_json(url, self._match_id(url), note=False)


def test_r8_mid_run_approval_edit_never_enables_unbound_hop(ctx, project, monkeypatch):
    dials = []
    monkeypatch.setattr(
        remote_media,
        "_dial",
        lambda *args: (
            dials.append(args)
            or (
                200,
                {},
                b'{"id":"77","title":"Inner","url":"https://inner.fixture.test/m.wav","ext":"wav"}',
                "127.0.0.1",
                "OK",
            )
        ),
    )
    for cls in (ReviewOuterIE, ReviewInnerIE):
        remote_media.register_test_extractor(cls)
    try:
        configure(ctx, [("ReviewOuter", ["outer.fixture.test"])])
        _request, grant, job_id = accept(ctx, project, "https://outer.fixture.test/clip/1")
        monkeypatch.setattr(
            ReviewOuterIE,
            "hook",
            staticmethod(
                lambda: configure(
                    ctx,
                    [
                        ("ReviewOuter", ["outer.fixture.test"]),
                        ("ReviewInner", ["inner.fixture.test"]),
                    ],
                )
            ),
        )
        run_once(ctx)
        job = ctx.store.get(m.Job, job_id)
        assert job.state == "awaiting_user"
        assert job.error["message"] == "Extractor hop is not approved"
        assert dials == []
        assert ctx.store.get(m.Permit, grant).used == 1
    finally:
        for cls in (ReviewOuterIE, ReviewInnerIE):
            remote_media.unregister_test_extractor(cls)


def test_r8_pre_receipt_replay_with_different_payload_conflicts(ctx, project, origin):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    granted, _grant, job_id = accept(ctx, project, url_for(origin, "clip"))
    # Simulate a legacy B12 job accepted before receipt creation was implemented.
    with ctx.store.transaction() as conn:
        assert (
            conn.execute(
                "DELETE FROM request_receipts WHERE operation=? AND request_key=?",
                (remote_media.OPERATION, granted.request_key),
            ).rowcount
            == 1
        )
    changed = granted.model_copy(
        update={"inputs": [m.URLInput(kind="url", url=url_for(origin, "other"))]}
    )
    refused = app.submit_collect(ctx, changed)
    assert refused.error.code == m.ErrorCode.IDEMPOTENCY_CONFLICT
    assert refused.approval is None
    assert app.submit_collect(ctx, granted).data["job_id"] == job_id


def test_r8_retry_cap_preserves_original_error_and_never_dials_again(
    ctx, project, origin, monkeypatch
):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    path = ctx.paths.root / "metamuse.toml"
    path.write_text(path.read_text().replace("safe_read_retries = 2", "safe_read_retries = 0"))
    _req, _grant, job_id = accept(ctx, project, url_for(origin, "clip"))
    path.write_text(path.read_text().replace("safe_read_retries = 0", "safe_read_retries = 2"))
    calls = []

    def timeout(*args):
        calls.append(args)
        raise m.CoreError(m.ErrorCode.NETWORK_TIMEOUT, "Upstream read timed out")

    monkeypatch.setattr(remote_media, "_dial", timeout)
    run_once(ctx)
    run_once(ctx)
    job = ctx.store.get(m.Job, job_id)
    assert len(calls) == 1
    assert job.state == "failed"
    assert job.error["code"] == "E_NETWORK_TIMEOUT"
    assert job.error["message"] == "Upstream read timed out"
    assert "accepted retry cap was reached" in job.error["next_action"]
    with jobs.Executor(ctx.store) as executor:
        assert not executor.run_once()


def test_r8_offline_after_claim_reads_no_bound_secret(ctx, project, origin, tmp_path, monkeypatch):
    store, ref = put_secret(
        tmp_path,
        ctx,
        netscape("media.fixture.test", "FALSE", "/", "FALSE", "0", "sid", "SYNTHETIC_VALUE"),
    )
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"], ref)])
    _req, grant, job_id = accept(ctx, project, url_for(origin, "clip"))
    original = jobs.Executor.require_network
    checks, reads = [], []

    def require(self, **kw):
        checks.append(kw)
        original(self, **kw)
        if len(checks) == 1:
            path = ctx.paths.root / "metamuse.toml"
            path.write_text(
                path.read_text().replace('network_mode = "live"', 'network_mode = "offline"')
            )

    monkeypatch.setattr(jobs.Executor, "require_network", require)
    monkeypatch.setattr(
        safety, "resolve_secret", lambda *a, **kw: reads.append(a) or store.resolve(*a, **kw)
    )
    run_once(ctx, secret_store=store)
    assert ctx.store.get(m.Job, job_id).state == "awaiting_user"
    assert reads == []
    assert ctx.store.get(m.Permit, grant).used == 0
    assert origin.hits == []


def test_r8_persisted_payload_tamper_after_boundary_fails_closed(ctx, project, origin, monkeypatch):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    other = app.create_project(ctx, {"name": "Other", "request_key": "other-project"}).data["id"]
    _req, grant, job_id = accept(ctx, project, url_for(origin, "clip"))
    original = remote_media._load_bound_cookies

    def tamper(*args, **kw):
        with ctx.store.transaction() as conn:
            payload = json.loads(
                conn.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
            )
            payload["project_id"] = other
            conn.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(payload), job_id))
        return original(*args, **kw)

    monkeypatch.setattr(remote_media, "_load_bound_cookies", tamper)
    run_once(ctx)
    job = ctx.store.get(m.Job, job_id)
    assert job.state == "awaiting_user"
    assert job.error["message"] == "Permit does not cover this action"
    assert ctx.store.get(m.Permit, grant).used == 0
    assert origin.hits == []


@pytest.mark.parametrize(
    "html",
    [
        '<form action="http://[::1"></form><form action="/login/"></form>',
        '<base href="http://[::1"><form action="/login/"></form>',
        '<base href="http://[::1"><base href="/login/"><form action="./"></form>',
    ],
)
def test_r8_invalid_form_or_base_does_not_hide_later_login(html):
    assert remote_media._login_form(html.encode(), "https://www.facebook.com/share/r/Synthetic1/")


@pytest.mark.parametrize(
    "value", ["/__rd%5Fverify", r"/\u005f\u005frd_verify", r"/\x5f\x5frd_verify", "/＿＿rd_verify"]
)
@pytest.mark.parametrize("channel", ["body", "refresh", "location"])
def test_r8_encoded_underscore_challenge_is_stopped(value, channel):
    assert remote_media._challenge_marked(
        200,
        {} if channel == "body" else {channel: value},
        value.encode() if channel == "body" else b"",
        "OK",
    )


@pytest.mark.parametrize(
    "value", ["__rd%5Fverify", r"\u005f\u005frd_verify", r"\x5f\x5frd_verify", "＿＿rd_verify"]
)
def test_r8_encoded_challenge_spanning_response_blocks_is_stopped(value):
    payload = b"x" * 65533 + value.encode() + b"x" * 65536
    assert remote_media._challenge_marked(200, {}, payload, "OK")


def test_r8_challenge_scan_has_bounded_temporary_allocations():
    import tracemalloc

    payload = b"\xff" * (2 * 1024 * 1024)
    tracemalloc.start()
    try:
        assert not remote_media._challenge_marked(200, {}, payload, "OK")
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    print(f"MEASURE challenge scan of 2 MiB: {peak} temporary bytes")
    assert peak < 1024 * 1024, peak


@pytest.mark.parametrize("domain", ["127.0.0.1", ".1", "127.1", "0x7f000001", "[::1]"])
def test_r8_ip_cookie_domain_is_refused(domain):
    with pytest.raises(m.CoreError) as held:
        remote_media._parse_cookie_secret(f"{domain}\tTRUE\t/\tTRUE\t0\tsid\tsynthetic\n".encode())
    assert held.value.detail.message == "Cookie file line 1 is invalid"


@pytest.mark.parametrize("unit", ["a", "b=a=", "a=b="])
def test_r8_preview_cpu_at_url_cap_with_canonical_form(ctx, project, unit):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    prefix = "https://media.fixture.test/clip?q="
    remaining = safety.URL_MAX_BYTES - len(prefix)
    url = prefix + (unit * remaining)[:remaining]
    started = time.process_time()
    envelope = app.submit_collect(ctx, remote_request(project, url, ["media_metadata"]))
    elapsed = time.process_time() - started
    print(
        f"MEASURE canonical preview {unit!r} {len(url.encode())} bytes: {elapsed:.6f} CPU seconds"
    )
    assert elapsed < 2.0
    assert envelope.approval is not None
    assert envelope.approval.destination == url and url.isascii()
