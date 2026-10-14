"""Round-4 review regressions. Synthetic data; browsers connect only to loopback."""

import json
from pathlib import Path

import pytest
from test_facebook import _write_facebook_config

from metamuse import app, config, facebook, jobs
from metamuse import models as m

URL = "https://www.facebook.com/synth.lab/posts/pfbid0101synth"


@pytest.fixture(autouse=True)
def reset_seams():
    yield
    facebook.set_driver_factory(None)
    facebook.set_egress_factory(None)
    facebook.set_secret_store_factory(None)
    facebook.set_resolver_factory(None)
    facebook.set_batch_hook(None)


def observation(markup, url=URL):
    return facebook.PageObservation(url=url, status_code=200, html=markup)


def submit(ctx, project, permit, *, url=URL, max_items=100, max_bytes=1_000_000):
    result = app.submit_collect(
        ctx,
        {
            "project_id": project,
            "inputs": [{"kind": "url", "url": url}],
            "resources": ["post", "comments"],
            "limits": {
                "max_urls": 1,
                "max_items": max_items,
                "max_bytes": max_bytes,
                "wall_clock_seconds": 60,
            },
            "access_grant_id": permit,
            "request_key": "review-job",
        },
    )
    assert result.status == "accepted", result
    return result.data["job_id"]


def finish(ctx, job_id):
    with jobs.Executor(ctx.store) as executor:
        executor.run(once=True)
    return app.get_job(ctx, {"job_id": job_id}).data["job"]


@pytest.mark.parametrize(
    "hosts",
    [
        [],
        ["*.facebook.com"],
        ["*.fbcdn.net"],
        ["127.0.0.1"],
        ["localhost"],
        ["probe.local"],
        [""],
        ["bad host"],
        ["www.facebook.com", "bad/host"],
    ],
)
def test_finding_01_invalid_allowlist_is_fixed_config_error(tmp_path: Path, hosts):
    tmp_path.mkdir(exist_ok=True)
    _write_facebook_config(tmp_path, extra_facebook_lines=[f"allowed_hosts = {json.dumps(hosts)}"])
    with pytest.raises(m.CoreError) as error:
        config.load_config(tmp_path)
    assert error.value.detail.code == m.ErrorCode.INPUT_INVALID
    assert error.value.detail.message == "Invalid Facebook host allowlist"
    with pytest.raises(m.CoreError):
        facebook.EgressGuard(allowed_hosts=hosts, resolver=lambda _: ["93.184.216.34"])


def test_finding_01_defaults_keep_exact_host_restriction():
    guard = facebook.EgressGuard(
        allowed_hosts=config.DEFAULT_FACEBOOK_ALLOWED_HOSTS, resolver=lambda _: ["93.184.216.34"]
    )
    with pytest.raises(m.CoreError) as error:
        guard.check_proxy_target("https://unapproved.example/", method="CONNECT", is_connect=True)
    assert error.value.detail.code == m.ErrorCode.SCOPE_DENIED
