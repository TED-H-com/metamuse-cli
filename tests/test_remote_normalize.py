"""OFFLINE: every canonical provenance candidate needs an approved host."""

import json

import pytest

from metamuse import remote_media


@pytest.mark.parametrize("webpage", ["absent", "refused"])
def test_normalize_unapproved_extract_host_falls_back_to_reviewed(webpage):
    refused_host = "unapproved.fixture.test"
    extract_url = f"https://{refused_host}/extract/%41"
    info = {"id": "clip", "title": "Synthetic clip"}
    if webpage == "refused":
        info["webpage_url"] = "https://media.fixture.test/clip?sig=SYNTHETIC_REFUSAL"

    record = remote_media._normalize(
        info,
        extract_url,
        "MetamuseFixture",
        10,
        approved_hosts=["media.fixture.test"],
        reviewed_url="https://MEDIA.fixture.test:443/reviewed/%41?x=%2f",
    )

    assert record is not None
    assert record["canonical_url"] == "https://media.fixture.test/reviewed/A?x=%2F"
    assert record["canonical_basis"] == "reviewed"
    assert refused_host not in json.dumps(record)
    assert "SYNTHETIC_REFUSAL" not in json.dumps(record)


def test_normalize_unapproved_webpage_host_uses_extract_display():
    refused_host = "unapproved.fixture.test"
    record = remote_media._normalize(
        {
            "id": "clip",
            "title": "Synthetic clip",
            "webpage_url": f"https://{refused_host}/webpage",
        },
        "https://MEDIA.fixture.test:443/extract/%41?x=%2f",
        "MetamuseFixture",
        10,
        approved_hosts=["media.fixture.test"],
        reviewed_url="https://media.fixture.test/reviewed",
    )

    assert record is not None
    assert record["canonical_url"] == "https://media.fixture.test/extract/A?x=%2F"
    assert record["canonical_basis"] == "extract_url"
    assert refused_host not in json.dumps(record)
