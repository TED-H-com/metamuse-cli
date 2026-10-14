"""OFFLINE: the published URL guarantee distinguishes extractor input from derived requests."""

from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "name",
    [
        "docs/dev/COLLECT.md",
        "docs/dev/CORE_API.md",
        "docs/design/02_SPEC.md",
        "docs/design/03_SDS.md",
    ],
)
def test_r9_docs_describe_the_checked_wire_and_derived_requests(name):
    text = " ".join((Path(__file__).parents[1] / name).read_text().split())
    assert "extractor receives exactly the displayed URL" in text
    assert "transport sends exactly each URL the checker approved" in text
    assert "without requoting or dot-segment removal" in text
    assert "FacebookReel" in text and "API endpoints" in text
    assert "watch, video and story" in text


def test_r9_docs_requested_share_url_is_canonical_with_original_provenance():
    text = " ".join((Path(__file__).parents[1] / "docs/dev/COLLECT.md").read_text().split())
    assert "`requested_url` (the reviewed canonical share URL)" in text
    assert "`original_url` keeps the operator's original share spelling" in text
