"""Whole-module scheduling and conservation of a captured collection partition."""

import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ci_cohorts as ci  # noqa: E402

MEASUREMENTS = (
    "tests/test_b16a_round3_complexity.py",
    "tests/test_b16a_round4_complexity.py",
    "tests/test_b16a_round5_complexity.py",
    "tests/test_b16a_round5_memory.py",
    "tests/test_b16a_round6_complexity.py",
    "tests/test_b16a_round7_complexity.py",
    "tests/test_b16a_round8_complexity.py",
)


def test_all_existing_measurement_modules_share_privacy():
    assert all((ROOT / module).is_file() for module in MEASUREMENTS)
    assert {ci.cohort_for(module) for module in MEASUREMENTS} == {"privacy"}


def test_future_nested_measurement_modules_follow_the_category():
    for suffix in ("complexity", "memory"):
        assert ci.cohort_for(f"nested/test_b16a_future_scope_{suffix}.py") == "privacy"


def test_existing_feature_precedence_wins_over_measurement_category():
    examples = {
        "tests/facebook/test_b16a_future_complexity.py": "facebook",
        "tests/test_b16a_facebook_muse_memory.py": "facebook",
        "tests/test_b16a_muse_remote_complexity.py": "muse",
        "tests/test_b16a_remote_memory.py": "media",
        "tests/test_b16a_media_complexity.py": "media",
    }
    assert {path: ci.cohort_for(path) for path in examples} == examples


def test_near_misses_and_unknown_patterns_remain_in_core():
    examples = (
        "tests/test_b16_round9_complexity.py",
        "tests/test_B16a_round9_memory.py",
        "tests/test_b16a_round9_complexity_extra.py",
        "tests/test_b16a_round9_memory_helpers.py",
        "tests/b16a_round9_memory_test.py",
        "nested/widget_test.py",
        "future/new_pattern.py",
    )
    assert {ci.cohort_for(path) for path in examples} == {"core"}


def test_partition_moves_only_complete_measurement_modules_without_loss():
    # A caller can supply captured normal IDs and their original selections.
    # Normal CI still exercises the same conservation rules on a tiny fixture.
    captured = os.environ.get("CI_COHORT_BALANCE_BASELINE")
    if captured:
        data = json.loads(Path(captured).read_text())
    else:
        before = {
            "core": ["nested/widget_test.py::test_future[1]"]
            + [module + "::test_measurement" for module in MEASUREMENTS],
            "muse": ["tests/test_muse.py::test_ok"],
            "media": ["tests/test_remote_media.py::test_ok"],
            "facebook": ["tests/test_facebook.py::test_ok"],
            "privacy": ["tests/test_privacy.py::test_ok"],
        }
        data = {
            "full": [before["core"][0], before["privacy"][0], *before["core"][1:]]
            + before["facebook"]
            + before["muse"]
            + before["media"],
            "selected": before,
        }
    full, before = data["full"], data["selected"]
    assert full and len(full) == len(set(full))
    assert set(before) == set(ci.COHORTS)
    assert Counter(n for nodes in before.values() for n in nodes) == Counter(full)
    moved = {n for n in full if n.split("::", 1)[0] in MEASUREMENTS}
    assert {n.split("::", 1)[0] for n in moved} == set(MEASUREMENTS)
    assert moved <= set(before["core"])
    after = {group: [] for group in ci.COHORTS}
    for node in full:
        after[ci.cohort_for(node.split("::", 1)[0])].append(node)
    expected = {group: set(nodes) for group, nodes in before.items()}
    expected["core"] -= moved
    expected["privacy"] |= moved
    for group in ci.COHORTS:
        assert after[group]  # No cohort silently disappears.
        assert after[group] == [node for node in full if node in expected[group]]
    assert Counter(n for nodes in after.values() for n in nodes) == Counter(full)
    audit = os.environ.get("CI_COHORT_BALANCE_AUDIT")
    if audit:
        Path(audit).write_text(
            json.dumps(
                {
                    "full_count": len(full),
                    "full_hash": hashlib.sha256(
                        json.dumps(sorted(full), ensure_ascii=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                    "moved": sorted(moved),
                    "counts": {group: len(nodes) for group, nodes in after.items()},
                    "selected": after,
                    "scope": "Collection partition only; no product test bodies executed",
                },
                indent=2,
            )
            + "\n"
        )
