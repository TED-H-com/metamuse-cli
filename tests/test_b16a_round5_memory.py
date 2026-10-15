"""RSS regression at one tenth of the raw packet cap, plus interpreter headroom."""

import pytest
from b16a_r5_memory import measure


@pytest.mark.parametrize(
    "family",
    [
        "review_xml_attr_quote_run",
        "escaped_quote_run",
        "json_escaped_doc",
        "review_nested_json_prose",
    ],
)
def test_r47_fresh_process_memory_budget(family):
    row = measure(family, 2 * 1024 * 1024)
    # 1 GiB at 20 MiB scales to 102.4 MiB, plus 48 MiB interpreter/import headroom.
    assert row["rss_kib"] <= (1024 * 1024 // 10 + 48 * 1024), row


def test_r47_decoded_depth_table_has_byte_storage_rss():
    row = measure("encoded_key_long", 2 * 1024 * 1024)
    # Sparse decoding changes one escape; a dense Python pointer list alone adds
    # 16 MiB at this size, on top of the decoded text and numeric syntax tables.
    assert row["rss_kib"] - row["baseline_kib"] <= 24 * 1024, row
