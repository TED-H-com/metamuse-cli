"""Handoff test failures cannot leave non-daemon threads pinning pytest exit."""

from pathlib import Path

import pytest
import test_muse_trust as trust_tests


@pytest.mark.parametrize(
    "name",
    [
        "test_final_handoff_serializes_selection_change_across_store_instances",
        "test_final_handoff_allows_workspace_maintenance_after_commit",
    ],
)
def test_r4_6_handoff_threads_cannot_pin_process(ctx, monkeypatch, name):
    original = trust_tests.threading.Thread
    seen = []

    def thread(*args, **kwargs):
        assert kwargs.get("daemon") is True
        result = original(*args, **kwargs)
        seen.append(result)
        return result

    monkeypatch.setattr(trust_tests.threading, "Thread", thread)
    getattr(trust_tests, name)(ctx)
    assert len(seen) == 2 and all(not item.is_alive() for item in seen)


def test_r4_6_ci_has_finite_check_deadline():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/ci.yml").read_text()
    for job in ("static-wheel", "cohorts", "installed-journeys", "privacy-load", "check"):
        assert f"  {job}:\n    timeout-minutes: 30\n" in workflow
    assert (
        "needs: [static-wheel, cohorts, installed-journeys, privacy-load]\n    if: always()"
        in workflow
    )
