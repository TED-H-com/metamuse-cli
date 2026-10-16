"""Developer documentation and any public schema claims follow the Store."""

import json
from pathlib import Path

from metamuse.store import SCHEMA_VERSION


def test_r5_9_page_schema_matches_store():
    root = Path(__file__).resolve().parents[1]
    # The user-facing page no longer needs to advertise storage internals. Keep
    # checking the version where developers actually find the storage contract.
    header = (root / "docs/dev/CORE_API.md").read_text().splitlines()[0]
    assert header.endswith(f", schema {SCHEMA_VERSION})")

    page = json.loads((root / "site/page.json").read_text())
    for section in ("stats", "facts"):
        for row in page.get(section, []):
            if row["label"]["en"] in ("SQLite schema after init", "SQLite schema"):
                assert row["value"] == str(SCHEMA_VERSION)

    # Traverse the page's map without coupling to its section layout.
    def strings(node):
        if isinstance(node, dict):
            if node.get("en", "").startswith("Schema "):
                yield node
            for child in node.values():
                yield from strings(child)
        elif isinstance(node, list):
            for child in node:
                yield from strings(child)

    claims = list(strings(page))
    for claim in claims:
        assert claim == {"en": f"Schema {SCHEMA_VERSION}", "zh": f"綱要 {SCHEMA_VERSION}"}
