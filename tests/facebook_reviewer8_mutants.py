# ruff: noqa: E501
"""Reviewer-provided faults, ported to the accessor/composed-capture APIs.

Synthetic review-b13r8 evidence supplied with the task; no /tmp import.
"""

import json
import os

import pytest
from facebook_round8_mutants import rewrite

from metamuse import _facebook_capture as capture
from metamuse import facebook


def apply_reviewer_mutant(monkeypatch, name: str) -> None:
    if name == "REV_M1_MAIN_PROP":
        # Capture one property (checkVisibility) from the main world (read-only) while WALK runs in isolated world.
        orig_capture = capture.capture
        orig_walk = capture.WALK

        def main_prop_capture(cdp):
            main_vis = (
                cdp.send(
                    "Runtime.evaluate",
                    {
                        "expression": "Array.from(document.querySelectorAll('*'), e => typeof e.checkVisibility === 'function' ? !!e.checkVisibility() : true)",
                        "returnByValue": True,
                    },
                )
                .get("result", {})
                .get("value", [])
            )
            monkeypatch.setattr(
                capture,
                "WALK",
                orig_walk.replace(
                    "return D.checkVisibility(n_node);",
                    "{const mv="
                    + json.dumps(main_vis)
                    + ";const idx=Array.from(document.querySelectorAll('*')).indexOf(n_node);return idx>=0?mv[idx]:D.checkVisibility(n_node);}",
                ),
            )
            return orig_capture(cdp)

        monkeypatch.setattr(capture, "capture", main_prop_capture)
    elif name == "REV_M1B_MAIN_CONTENT_TYPE":
        # Read document.contentType in the main world instead of the isolated world.
        orig_capture = capture.capture

        def main_ct_capture(cdp):
            res = cdp.send(
                "Runtime.evaluate",
                {"expression": "document.contentType", "returnByValue": True},
            )
            if res.get("result", {}).get("value") != "text/html":
                raise capture.failure()
            return orig_capture(cdp)

        monkeypatch.setattr(capture, "capture", main_ct_capture)
        monkeypatch.setattr(
            capture,
            "WALK",
            capture.WALK.replace(
                "if (D.contentType(document) !== 'text/html') return {error:'document'};",
                "",
            ),
        )
    elif name == "REV_M2_CLOSED_ROOT":
        # Skip closed shadow root resolution in capture().
        rewrite(
            monkeypatch,
            capture,
            "capture",
            [('if root.get("shadowRootType") != "closed":', "if True:")],
            module=capture,
        )
    elif name == "REV_M2B_SECOND_CLOSED_ROOT":
        # Skip resolving the second closed shadow root in capture().
        rewrite(
            monkeypatch,
            capture,
            "capture",
            [
                ("gaps: set[str] = set()", "gaps: set[str] = set()\n        closed_seen = 0"),
                (
                    'if root.get("shadowRootType") != "closed":\n                    continue',
                    'if root.get("shadowRootType") != "closed":\n                    continue\n                closed_seen += 1\n                if closed_seen > 1:\n                    continue',
                ),
            ],
            module=capture,
        )
    elif name == "REV_M3_CV_HIDDEN":
        helper = """function underCV(n_node){while(n_node){if(D.nodeType(n_node)===1 && style(n_node,'content-visibility')==='hidden')return true;n_node=parent(n_node);}return false;}
        function box(n_node) {"""
        walk = (
            capture.WALK.replace("function box(n_node) {", helper)
            .replace(
                "style(n_node,'content-visibility')==='hidden' &&",
                "false &&",
            )
            .replace(
                "return D.checkVisibility(n_node);",
                "return D.checkVisibility(n_node)||underCV(n_node);",
            )
            .replace(
                "D.rectsLength(D.getClientRects(n_range))>0 || v_autoSkipped",
                "D.rectsLength(D.getClientRects(n_range))>0 || v_autoSkipped || underCV(n_p)",
            )
        )
        monkeypatch.setattr(capture, "WALK", walk)
        rewrite(
            monkeypatch,
            capture.FixtureTree,
            "finish",
            [('css(style, "content-visibility") == "hidden"', "False")],
            module=capture,
        )
    elif name == "REV_M4_CONTENTS_LOOKTHROUGH":
        # display:contents look-through removed.
        monkeypatch.setattr(
            capture,
            "WALK",
            capture.WALK.replace(
                "if(style(n_node,'display')!=='contents')",
                "if(true)",
            ).replace(
                "while(n_ancestor && (D.nodeType(n_ancestor)!==1 || style(n_ancestor,'display')==='contents'))",
                "while(false)",
            ),
        )
        rewrite(
            monkeypatch,
            capture.FixtureTree,
            "finish",
            [('or display == "none"', 'or display in ("none", "contents")')],
            module=capture,
        )
    elif name == "REV_M5_BARRIER_SKIP":
        old = "if(D.namespaceURI(n_node)===html && D.localName(n_node)==='template')v_children.push(D.content(n_node));"
        assert old in capture.WALK
        monkeypatch.setattr(capture, "WALK", capture.WALK.replace(old, ""))
        original = capture.fixture_capture

        def no_template(markup):
            nodes = original(markup)
            pending = [j for n in nodes if n["tag"] == "template" for j in n["children"]]
            while pending:
                node = nodes[pending.pop()]
                node["attrs"] = {}
                pending.extend(node["children"])
            return nodes

        monkeypatch.setattr(capture, "fixture_capture", no_template)
    elif name == "REV_M6_NODE_BOUND_OFF_BY_ONE":
        # Node bound off by one (65,537 allowed instead of 65,536).
        monkeypatch.setattr(capture, "MAX_NODES", 65_537)
        monkeypatch.setattr(
            capture,
            "WALK",
            capture.WALK.replace(
                "if(v_nodes.length>=65536) return {error:'bound'};",
                "if(v_nodes.length>65536) return {error:'bound'};",
            ),
        )
    elif name == "REV_M7_CAPTURE_ERR_CODE":
        # Capture failure mapped to E_PROTOCOL_MISMATCH.
        rewrite(
            monkeypatch,
            capture,
            "failure",
            [("m.ErrorCode.SELECTOR_DRIFT", "m.ErrorCode.PROTOCOL_MISMATCH")],
            module=capture,
        )
    elif name == "REV_M8_CLICK_HIT_TEST":
        # Click hit test removed.
        rewrite(
            monkeypatch,
            facebook._PlaywrightSession,
            "_expand_next",
            [('if same.get("result", {}).get("value") is not True:', "if False:")],
        )
    elif name == "REV_M9_DEADLINE_RESET":
        # Verifier deadline reset for each new arrival.
        rewrite(
            monkeypatch,
            facebook._PlaywrightSession,
            "_verify_target_shutdown",
            [
                (
                    "deadline = time.monotonic() + 5",
                    "deadline = time.monotonic() + 5\n    seen = set(self._closing_targets)",
                ),
                (
                    "for target in list(self._closing_targets):",
                    (
                        "if self._closing_targets - seen:\n"
                        "                deadline = time.monotonic() + 5\n"
                        "                seen |= set(self._closing_targets)\n"
                        "            for target in list(self._closing_targets):"
                    ),
                ),
            ],
        )
    elif name == "REV_M10_CUTOFF_FILTER":
        # Cutoff filter accepting one more error class (REFERENCE_INVALID).
        rewrite(
            monkeypatch,
            facebook._PlaywrightSession,
            "_raise_if_fatal_stop",
            [
                (
                    "error.detail.code != m.ErrorCode.LIMIT_REACHED",
                    "error.detail.code not in {m.ErrorCode.LIMIT_REACHED, m.ErrorCode.REFERENCE_INVALID}",
                )
            ],
        )
    elif name == "REV_M10B_CUTOFF_SELECTOR_DRIFT":
        # Cutoff filter accepting SELECTOR_DRIFT.
        rewrite(
            monkeypatch,
            facebook._PlaywrightSession,
            "_raise_if_fatal_stop",
            [
                (
                    "error.detail.code != m.ErrorCode.LIMIT_REACHED",
                    "error.detail.code not in {m.ErrorCode.LIMIT_REACHED, m.ErrorCode.SELECTOR_DRIFT}",
                )
            ],
        )
    else:
        raise ValueError(f"Unknown reviewer mutant: {name}")


@pytest.fixture(autouse=True)
def _inject_reviewer_r8_mutant(monkeypatch):
    name = os.environ.get("METAMUSE_REVIEWER_R8_MUTANT")
    if name:
        apply_reviewer_mutant(monkeypatch, name)
