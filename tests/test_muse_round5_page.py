"""All three bilingual schema claims follow the authoritative Store version."""

import json
from pathlib import Path

from metamuse.store import SCHEMA_VERSION


def test_r5_9_page_schema_matches_store():
    page = json.loads((Path(__file__).resolve().parents[1] / "site/page.json").read_text())
    stat = next(s for s in page["stats"] if s["label"]["en"] == "SQLite schema after init")
    fact = next(s for s in page["facts"] if s["label"]["en"] == "SQLite schema")
    assert stat["value"] == fact["value"] == str(SCHEMA_VERSION)

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
    assert claims == [{"en": f"Schema {SCHEMA_VERSION}", "zh": f"綱要 {SCHEMA_VERSION}"}]
