"""The fixture accepts only inputs whose capture agrees with Chromium."""

import json
import os
from pathlib import Path

from browser_deadline import browser_deadline
from facebook_parity import (
    as_document,
    capture_session,
    class_corpus,
    literal_corpus,
    normalise,
    seeded_corpus,
)
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import _facebook_capture as capture
from metamuse import models as m


@browser_deadline
def test_r9_fixture_differential_corpus(tmp_path, monkeypatch):
    recorded = {
        json.loads(line)
        for line in (Path(os.environ["METAMUSE_FACEBOOK_PARITY_INPUTS"]) / "inputs.jsonl")
        .read_text()
        .splitlines()
    }
    seed_size = int(os.environ.get("METAMUSE_FACEBOOK_PARITY_SIZE", "160"))
    inputs = sorted(
        literal_corpus() | set(seeded_corpus(seed_size)) | set(class_corpus()) | recorded
    )
    raw_overbound = sum(len(markup.encode()) > capture.MAX_TEXT_BYTES for markup in inputs)
    inputs = [markup for markup in inputs if len(markup.encode()) <= capture.MAX_TEXT_BYTES]
    accepted = refused = 0
    mismatches = []
    with capture_session(monkeypatch) as session:
        # These rules are checked even for constructs outside the fixture
        # subset (e.g. iframe, select, declarative shadow DOM).
        for markup in class_corpus():
            session._page.set_content(as_document(markup), wait_until="load")
            _, nodes, _ = session._capture()
            if "shadowrootmode=" not in markup:
                elements = [n for n in nodes if n["attrs"].get("data-expand-kind")]
                # Locked Chromium ignores display:contents on frameset/frame
                # and computes block. They remain outside the fixture subset.
                expected = elements and elements[0]["tag"] in {"frame", "frameset"}
                assert len(elements) == 1 and elements[0]["rendered"] == expected, markup
            else:
                rendered = "".join(n["text"] for n in nodes if n["rendered"])
                assert ("SLOTTED" in rendered) == ("display:none" not in markup), markup
        for markup in inputs:
            try:
                fixture = capture.fixture_capture(markup)
            except m.CoreError as exc:
                assert exc.detail.code == m.ErrorCode.SELECTOR_DRIFT
                assert exc.detail.message == "Facebook structured capture could not be completed"
                refused += 1
                continue
            session._page.set_content(as_document(markup), wait_until="load")
            session._network_cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
            _, browser, gaps = session._capture()
            assert not gaps, (markup, gaps)
            if normalise(fixture) != normalise(browser):
                mismatches.append((markup, normalise(fixture), normalise(browser)))
            accepted += 1
    result = dict(
        inputs=len(inputs),
        accepted=accepted,
        refused=refused,
        mismatches=len(mismatches),
        raw_overbound_excluded=raw_overbound,
    )
    print(json.dumps(result))
    if os.environ.get("METAMUSE_FACEBOOK_PARITY_REPORT"):
        samples = []
        for markup, fixture, browser in mismatches[:10]:
            delta = next(
                (
                    (a, b)
                    for a, b in zip(fixture["nodes"], browser["nodes"], strict=False)
                    if a != b
                ),
                None,
            )
            samples.append({"markup": markup[:2048], "first_difference": delta})
        Path(os.environ["METAMUSE_FACEBOOK_PARITY_REPORT"]).write_text(
            json.dumps(dict(result, mismatch_samples=samples)) + "\n"
        )
    assert not mismatches, mismatches
    assert accepted > 40
