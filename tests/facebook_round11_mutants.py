"""Focused, opt-in repair sensitivity checks; absent from ordinary runs."""

import os

import pytest
from facebook_round8_mutants import rewrite

from metamuse import _facebook_capture as capture
from metamuse import facebook


@pytest.fixture(autouse=True)
def inject_round11_fault(monkeypatch):
    name = os.environ.get("METAMUSE_FACEBOOK_ROUND11_MUTANT")
    if not name:
        return
    if name == "PROMOTE_REJECTED_PAGE":
        rewrite(
            monkeypatch,
            facebook,
            "run_facebook_post",
            [
                (
                    "            if egress.byte_limit_exceeded or bytes_used > max_bytes:",
                    "            parsed = candidate\n"
                    "            if egress.byte_limit_exceeded or bytes_used > max_bytes:",
                )
            ],
        )
    elif name in {"DROP_EXPECTED_CLOSE", "SUPPRESS_UNEXPECTED", "SUPPRESS_ACTIVE_PEER"}:
        edits = {
            "DROP_EXPECTED_CLOSE": [("except TargetClosedError:", "except KeyError:")],
            "SUPPRESS_UNEXPECTED": [("except TargetClosedError:", "except Exception:")],
            "SUPPRESS_ACTIVE_PEER": [
                ("self._peer_error = _keep_stop(self._peer_error, exc)", "pass")
            ],
        }[name]
        rewrite(monkeypatch, facebook._PlaywrightSession, "__init__", edits)
    elif name.startswith("OMIT_DISPLAY_"):
        tag = name.removeprefix("OMIT_DISPLAY_").lower()
        assert tag in {"html", "body", "legend"}
        monkeypatch.delitem(capture.DEFAULT_DISPLAY, tag)
    else:
        raise ValueError(name)
