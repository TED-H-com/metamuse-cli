"""Reviewer round-9 runtime mutation plugin (11 mutants covering Section 5)."""

# ruff: noqa: E501

from __future__ import annotations

import os
from typing import Any

import facebook_parity

from metamuse import _facebook_capture as capture
from metamuse import _facebook_dom as dom
from metamuse import facebook

MUTANT_ENV = "METAMUSE_REVIEWER_R9_MUTANT"


def _replace_once(source: str, old: str, new: str, mutant: str) -> str:
    if old not in source:
        raise RuntimeError(f"{mutant} target not found: {old!r}")
    return source.replace(old, new, 1)


def pytest_configure(config: Any) -> None:
    mutant = os.environ.get(MUTANT_ENV)
    if not mutant:
        return

    if mutant == "REV9_M1_ACCESSOR_DIRECT_CALL":
        # Evaluated function calling a DOM method directly instead of through D.
        capture.WALK = _replace_once(
            capture.WALK,
            "D.getAttribute(n_node,'hidden')",
            "n_node.getAttribute('hidden')",
            mutant,
        )

    elif mutant == "REV9_M2_OMIT_CLOSED_ROOTS":
        # WALK reading globalThis.__metamuseClosed without defining it first / omitting closed roots.
        orig_capture = capture.capture

        def _capture_no_closed(cdp: Any):
            orig_send = cdp.send

            def _send(method: str, params: dict[str, Any] | None = None):
                if (
                    method == "Runtime.evaluate"
                    and params
                    and "__metamuseClosed=new WeakMap()" in params.get("expression", "")
                ):
                    return {"result": {"type": "undefined"}}
                if (
                    method == "Runtime.callFunctionOn"
                    and params
                    and "__metamuseClosed.set" in params.get("functionDeclaration", "")
                ):
                    return {"result": {"type": "undefined"}}
                return orig_send(method, params)

            cdp.send = _send
            try:
                return orig_capture(cdp)
            finally:
                cdp.send = orig_send

        capture.WALK = _replace_once(
            capture.WALK,
            "globalThis.__metamuseClosed.get(n_node)",
            "(globalThis.__metamuseClosed&&globalThis.__metamuseClosed.get?globalThis.__metamuseClosed.get(n_node):null)",
            mutant,
        )
        capture.capture = _capture_no_closed

    elif mutant == "REV9_M3_FOREIGN_NOSCRIPT_IFRAME":
        # WALK treating <svg><noscript> or <math><iframe> as the HTML element.
        capture.WALK = _replace_once(
            capture.WALK,
            "if(v_el && r_node.ns===html && ['noscript','template','script','style','head'].includes(r_node.tag))v_blocked=true;",
            "if(v_el && ['noscript','template','script','style','head'].includes(r_node.tag))v_blocked=true;",
            mutant,
        )
        capture.WALK = _replace_once(
            capture.WALK,
            "else if(r_node.ns===html && ['template','noscript','iframe'].includes(r_node.tag))v_children=[];",
            "else if(['template','noscript','iframe'].includes(r_node.tag))v_children=[];",
            mutant,
        )

    elif mutant == "REV9_M4_PARITY_DROP_ATTRS_TEXT":
        # facebook_parity.normalise dropping attributes or text values before comparing.
        orig_normalise = facebook_parity.normalise

        def _normalise_dropped(nodes: list[dict[str, Any]]) -> dict[str, Any]:
            out = orig_normalise(nodes)
            out["nodes"] = [
                (kind, tag, ns, [], "", rendered, parent, container)
                for (kind, tag, ns, _attrs, _text, rendered, parent, container) in out["nodes"]
            ]
            return out

        facebook_parity.normalise = _normalise_dropped

    elif mutant == "REV9_M5_FIXTURE_ACCEPT_OUTSIDE_SUBSET":
        # FixtureTree accepting a construct outside its modelled subset instead of calling failure()
        # (accepting nested <button>/<form>/<a> or <h1><h2>).
        orig_starttag = capture.FixtureTree.handle_starttag

        def _permissive_starttag(self, tag, attrs):
            saved_tags = self.open_tags
            self.open_tags = capture.Counter(
                {
                    k: v
                    for k, v in saved_tags.items()
                    if k not in {"form", "a", "button", "h1", "h2", "h3", "h4", "h5", "h6"}
                }
            )
            try:
                return orig_starttag(self, tag, attrs)
            finally:
                for k in ("form", "a", "button", "h1", "h2", "h3", "h4", "h5", "h6"):
                    if k in saved_tags:
                        self.open_tags[k] = saved_tags[k]

        capture.FixtureTree.handle_starttag = _permissive_starttag

    elif mutant == "REV9_M6_NOBOX_BUTTON_FIELDSET_LEGEND_SVG":
        # noBox returning true for button, fieldset, legend or svg with display:contents.
        capture.WALK = _replace_once(
            capture.WALK,
            "const v_replaced=new Set('img input textarea select video audio canvas iframe embed object br wbr meter progress frame frameset'.split(' '));",
            "const v_replaced=new Set('img input textarea select video audio canvas iframe embed object br wbr meter progress frame frameset button fieldset legend svg'.split(' '));",
            mutant,
        )
        capture.CONTENTS_NONE = capture.CONTENTS_NONE | frozenset(
            {"button", "fieldset", "legend", "svg"}
        )

    elif mutant == "REV9_M7_UNRENDERED_CONTAINER_POLLUTION":
        # _DOMExtractor updating container state on an unrendered start tag (unrendered comments-section).
        orig_handle_starttag = facebook._DOMExtractor.handle_starttag

        def _polluting_starttag(
            self,
            tag: str,
            attrs: list[tuple[str, str | None]],
            *,
            visible: bool = True,
            void: bool | None = None,
        ) -> None:
            attr_map = {k.lower(): (v or "") for k, v in attrs if k}
            if (
                attr_map.get("data-fb-role", "").strip().lower() == "comments-section"
                and not visible
            ):
                self.comments_section_present = True
            return orig_handle_starttag(self, tag, attrs, visible=visible, void=void)

        facebook._DOMExtractor.handle_starttag = _polluting_starttag

    elif mutant == "REV9_M8_CLICK_STALE_OR_SKIP_HIT_TEST":
        # _expand_next skipping the composed hit test.
        dom.HIT_TEST = _replace_once(
            dom.HIT_TEST,
            "return false;",
            "return true;",
            mutant,
        )

    elif mutant == "REV9_M11_REPLACED_HOST_SKIP_SHADOW":
        # WALK skipping shadowRoot / closed shadow roots on a host element (e.g. div/span host).
        capture.WALK = _replace_once(
            capture.WALK,
            "const n_shadow=shadow(n_node);if(n_shadow)v_children.push(n_shadow);",
            "const n_shadow=(D.localName(n_node)==='span'?null:shadow(n_node));if(n_shadow)v_children.push(n_shadow);",
            mutant,
        )
        capture.WALK = _replace_once(
            capture.WALK,
            "const n_shadow=shadow(n_node);\n      if(n_shadow)v_children=children(n_shadow);",
            "const n_shadow=(r_node.tag==='span'?null:shadow(n_node));\n      if(n_shadow)v_children=children(n_shadow);",
            mutant,
        )

    else:
        raise RuntimeError(f"Unknown reviewer round-9 mutant: {mutant}")
