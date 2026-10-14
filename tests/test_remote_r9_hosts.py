"""OFFLINE: canonical host mapping refuses IDNA ambiguities before review."""

import pytest
from test_remote_media import configure, fixture_extractor, remote_request  # noqa: F401

from metamuse import app
from metamuse import models as m


@pytest.mark.parametrize("host", ["faß.fixture.test", "ς.fixture.test"])
def test_r9_idna_deviation_host_is_unreviewable(ctx, project, host):
    configure(ctx, [("MetamuseFixture", [host])])
    result = app.submit_collect(
        ctx, remote_request(project, f"https://{host}/clip", ["media_metadata"])
    )
    assert result.error.code == m.ErrorCode.INPUT_INVALID
    assert result.error.message == "Remote URL cannot be reviewed safely"
    assert result.approval is None
    assert app.list_jobs(ctx, m.JobListQuery(project_id=project)).data["jobs"] == []


@pytest.mark.parametrize("dot", [".", "。", "．", "｡"])
def test_r9_idna_mapped_trailing_dot_is_unreviewable(ctx, project, dot):
    configure(ctx, [("MetamuseFixture", ["media.fixture.test"])])
    result = app.submit_collect(
        ctx, remote_request(project, f"https://media.fixture.test{dot}/clip", ["media_metadata"])
    )
    assert result.error.code == m.ErrorCode.INPUT_INVALID
    assert result.error.message == "Remote URL cannot be reviewed safely"
    assert result.approval is None
