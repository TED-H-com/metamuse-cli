"""The fixture accepts only inputs whose capture agrees with Chromium."""

import json
import os
from pathlib import Path

from browser_deadline import browser_deadline
from facebook_parity import as_document, literal_corpus, normalise, seeded_corpus
from test_facebook import _synthetic_post_html
from test_facebook_review import URL, browser_fixture
from test_facebook_round5 import POST_PATH
from test_facebook_round6 import reset_seams  # noqa: F401

from metamuse import _facebook_capture as capture
from metamuse import facebook
from metamuse import models as m


@browser_deadline
def test_r9_fixture_differential_corpus(tmp_path, monkeypatch):
    recorded = {
        json.loads(line)
        for line in (Path(os.environ["METAMUSE_FACEBOOK_PARITY_INPUTS"]) / "inputs.jsonl")
        .read_text()
        .splitlines()
    }
    inputs = sorted(literal_corpus() | set(seeded_corpus()) | recorded)
    accepted = refused = 0
    mismatches = []
    with browser_fixture(
        tmp_path,
        monkeypatch,
        {POST_PATH: _synthetic_post_html()},
        unsandboxed=False,
        read_timeout=300,
    ) as (_, _, _, _, _, guard, driver):
        state = facebook.SanitizedStorageState(
            cookies=[], origins=[], secret_tokens=(), cookie_hosts=(), dropped_cookie_domains=()
        )
        session = driver.open_session(state, guard, timeout_ms=300000, max_bytes=100000000)
        try:
            session.goto_post(URL)
            for markup in inputs:
                if len(markup.encode()) > capture.MAX_TEXT_BYTES:
                    continue
                try:
                    fixture = capture.fixture_capture(markup)
                except m.CoreError as exc:
                    assert (
                        exc.detail.message == "Facebook structured capture could not be completed"
                    )
                    refused += 1
                    continue
                session._page.set_content(as_document(markup), wait_until="domcontentloaded")
                session._network_cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
                _, browser, gaps = session._capture()
                assert not gaps, (markup, gaps)
                if normalise(fixture) != normalise(browser):
                    mismatches.append((markup, normalise(fixture), normalise(browser)))
                accepted += 1
        finally:
            session.close()
    print(
        json.dumps(
            dict(inputs=len(inputs), accepted=accepted, refused=refused, mismatches=len(mismatches))
        )
    )
    assert not mismatches, mismatches
    assert accepted > 40
