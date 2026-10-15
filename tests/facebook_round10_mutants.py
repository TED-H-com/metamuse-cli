"""Opt-in round-10 faults. Each restores a class defect, never weakens its test."""

# ruff: noqa: E501

import os
from dataclasses import replace

import pytest
from facebook_round8_mutants import rewrite

from metamuse import _facebook_capture as cap
from metamuse import _facebook_dom as dom
from metamuse import facebook


def apply(monkeypatch, name):
    if name == "R10_DEBUGGER":
        original = facebook._PlaywrightSession.__init__

        def initialize(self, *args, **kwargs):
            original(self, *args, **kwargs)
            self._network_cdp.send("Debugger.enable")

        monkeypatch.setattr(facebook._PlaywrightSession, "__init__", initialize)
    elif name in {"R10_STATUS_FAILURE", "R10_STATUS_PROGRESS", "R10_FLOOD_GUARD"}:
        original = facebook._PlaywrightSession._expand_next

        def expansion(self, control_id):
            statuses, requests = [], []

            def response(event):
                statuses.append(event["response"]["status"])

            def request(event):
                requests.append(event["requestId"])
                if name == "R10_FLOOD_GUARD" and len(requests) > 256:
                    self._guard_failed()

            self._network_cdp.on("Network.responseReceived", response)
            self._network_cdp.on("Network.requestWillBeSent", request)
            try:
                observed = original(self, control_id)
                if name == "R10_STATUS_FAILURE" and any(s >= 400 for s in statuses):
                    return replace(observed, expansion_failed=True, progress_confirmed=True)
                if name == "R10_STATUS_PROGRESS" and statuses:
                    return replace(observed, progress_confirmed=True)
                return observed
            finally:
                self._network_cdp.remove_listener("Network.responseReceived", response)
                self._network_cdp.remove_listener("Network.requestWillBeSent", request)

        monkeypatch.setattr(facebook._PlaywrightSession, "_expand_next", expansion)
    elif name == "R10_NAMESPACE":
        monkeypatch.setattr(cap, "html_tag", lambda node: node["tag"])
    elif name == "R10_NORMALISE_CONTAINER":
        import inspect

        import facebook_parity

        original = facebook_parity.normalise
        source = inspect.getsource(original)
        assert "                container," in source
        namespace = {}
        exec(
            compile(source.replace("                container,\n", ""), "<drop-container>", "exec"),
            facebook_parity.__dict__,
            namespace,
        )
        # Mutate the existing function, so aliases imported during collection
        # cannot accidentally turn this fault into an inactive wrapper.
        monkeypatch.setattr(original, "__code__", namespace["normalise"].__code__)
    elif name == "R10_SCRIPT_ESCAPE":
        rewrite(
            monkeypatch,
            cap.FixtureTree,
            "handle_data",
            [('if parent["tag"] == "script" and "<!--" in data.lower():', "if False:")],
            module=cap,
        )
    elif name == "R10_AFTER_HEAD":
        # Accept and relocate post-head meta/script/template into body, reproducing
        # the old insertion-mode defect instead of an inert missing refusal.
        original = cap.fixture_capture

        def permissive(markup):
            import re

            markup = re.sub(r"</head>(.+?)<body>", r"</head><body>\1", markup)
            return original(markup)

        monkeypatch.setattr(cap, "fixture_capture", permissive)
    elif name in {"R10_WALK_SLOT_MAP", "R10_HIT_SLOT_MAP"}:
        owner, field = (cap, "WALK") if name == "R10_WALK_SLOT_MAP" else (dom, "HIT_TEST")
        source = getattr(owner, field)
        assert " || v_slots.get(n_node)" in source
        monkeypatch.setattr(owner, field, source.replace(" || v_slots.get(n_node)", ""))
    elif name == "R10_CV_INLINE":
        old = "['block','inline-block','flow-root','flex','inline-flex','grid','inline-grid','list-item','table-cell']"
        assert old in cap.WALK
        monkeypatch.setattr(cap, "WALK", cap.WALK.replace(old, old[:-1] + ",'inline']"))
        monkeypatch.setattr(cap, "CV_SKIPS", cap.CV_SKIPS | {"inline"})
    elif name == "R10_UNRENDERED_OUTER":
        rewrite(
            monkeypatch,
            facebook._DOMExtractor,
            "handle_starttag",
            [("    if identity:", "    if identity and visibility:")],
        )
    elif name == "R10_MOVE_AFTER_HIT":
        line = (
            '        cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})\n'
        )
        rewrite(
            monkeypatch,
            facebook._PlaywrightSession,
            "_expand_next",
            [
                (line, ""),
                (
                    '        if same.get("result", {}).get("value") is not True:',
                    line + '        if same.get("result", {}).get("value") is not True:',
                ),
            ],
        )
    else:
        raise ValueError(name)


@pytest.fixture(autouse=True)
def inject_round10_mutant(monkeypatch):
    name = os.environ.get("METAMUSE_FACEBOOK_ROUND10_MUTANT")
    if name:
        apply(monkeypatch, name)
