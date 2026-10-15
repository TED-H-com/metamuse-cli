# ruff: noqa: E501
"""Opt-in removals of each round-9 class control, never ordinary test imports."""

import os

import pytest
from facebook_round8_mutants import rewrite

from metamuse import _facebook_capture as cap
from metamuse import _facebook_dom as dom
from metamuse import facebook

DIRECT = {
    "DOCUMENT": ("D.contentType(document)", "document.contentType"),
    "NODE": ("D.nodeType(n_node)", "n_node.nodeType"),
    "ELEMENT": ("D.localName(n_node)", "n_node.localName"),
    "ATTRIBUTE": ("D.attrName(n_attr)", "n_attr.name"),
    "TEMPLATE": ("D.content(n_node)", "n_node.content"),
    "SLOT": ("D.assignedNodes(n_node)", "n_node.assignedNodes()"),
    "RANGE": ("D.getClientRects(n_range)", "n_range.getClientRects()"),
    "STYLE": (
        "D.css(apply(computedStyle,globalThis,[n_node]),v_property)",
        "apply(computedStyle,globalThis,[n_node])[v_property]",
    ),
    "COLLECTION": ("D.listLength(n_list)", "n_list.length"),
    "SHADOW": ("D.host(n_node)", "n_node.host"),
    "IFRAME": ("D.frameWindow(n_frame)", "n_frame.contentWindow"),
    "TEXT": ("D.textSlot(n_node)", "n_node.assignedSlot"),
    "WINDOW": ("apply(computedStyle,globalThis,[n_node])", "globalThis.getComputedStyle(n_node)"),
    "ATTRS_COLLECTION": ("D.attrsLength(n_attrs)", "n_attrs.length"),
    "RECTS_COLLECTION": (
        "D.rectsLength(D.getClientRects(n_range))",
        "D.getClientRects(n_range).length",
    ),
}


def apply(monkeypatch, name):
    if name.startswith("R9_DIRECT_"):
        old, new = DIRECT[name.removeprefix("R9_DIRECT_")]
        owner, field = (dom, "POPUP_CHECK") if name.endswith("_IFRAME") else (cap, "WALK")
        source = getattr(owner, field)
        assert old in source
        monkeypatch.setattr(owner, field, source.replace(old, new))
    elif name == "R9_NAMESPACE":
        monkeypatch.setattr(
            cap,
            "WALK",
            cap.WALK.replace("D.namespaceURI(n_node)===html && ", "").replace(
                "r_node.ns===html && ", ""
            ),
        )
    elif name == "R9_REWRAP":
        rewrite(
            monkeypatch,
            facebook,
            "classify_and_parse_observation",
            [("    except m.CoreError:\n        raise\n", "")],
        )
    elif name == "R9_MARKER_VOCABULARY":
        import facebook_parity

        monkeypatch.delitem(facebook_parity.MARKER_VALUES, "data-fb-state")
    elif name == "R9_ACCEPT_TEMPLATE_WRAPPER":
        rewrite(
            monkeypatch,
            cap.FixtureTree,
            "handle_starttag",
            [
                (
                    'if tag in {"html", "head", "body"}:',
                    'if tag in {"html", "head", "body"} and parent["kind"] != 11:',
                ),
                (
                    '(parent["kind"] != 9 and parent["tag"] != "html")',
                    'parent["tag"] not in {"", "html"}',
                ),
            ],
            module=cap,
        )
    elif name == "R9_NATIVE_VOID":
        monkeypatch.setattr(cap, "FIXTURE_HIDDEN_HTML", cap.FIXTURE_HIDDEN_HTML - {"area"})
    elif name == "R9_SVG_OPAQUE":
        monkeypatch.setattr(
            cap.FixtureTree,
            "FOREIGN",
            {
                **cap.FixtureTree.FOREIGN,
                cap.SVG: cap.FixtureTree.FOREIGN[cap.SVG] | {"use", "defs", "symbol"},
            },
        )
    elif name in {"R9_SVG_CHILDREN", "R9_SVG_TSPAN", "R9_FOREIGN_DISPLAY", "R9_COLUMN_DISPLAY"}:
        old, new = {
            "R9_SVG_CHILDREN": (
                'if parent["ns"] == SVG and parent["tag"] not in {"svg", "g", "text", "tspan"}:',
                "if False:",
            ),
            "R9_SVG_TSPAN": (
                'if tag == "tspan" and parent["tag"] not in {"text", "tspan"}:',
                "if False:",
            ),
            "R9_FOREIGN_DISPLAY": (
                'if ns != HTML and css(style, "display") not in {None, "none"}:',
                "if False:",
            ),
            "R9_COLUMN_DISPLAY": (
                'if css(style, "display") in {"inherit", "table-column", "table-column-group"}:',
                'if css(style, "display") == "inherit":',
            ),
        }[name]
        rewrite(monkeypatch, cap.FixtureTree, "handle_starttag", [(old, new)], module=cap)
    elif name.startswith("R9_ACCEPT_"):
        construct = name.removeprefix("R9_ACCEPT_").lower()
        if construct in cap.FixtureTree.REFUSED:
            monkeypatch.setattr(cap.FixtureTree, "REFUSED", cap.FixtureTree.REFUSED - {construct})
            monkeypatch.setattr(
                cap.FixtureTree, "HTML_TAGS", cap.FixtureTree.HTML_TAGS | {construct}
            )
            if construct == "plaintext":
                # Python's HTMLParser itself stays in PLAINTEXT through EOF.
                # Force acceptance too; removing only REFUSED leaves the strict
                # balanced-stack check as a second refusal and is not this fault.
                finish = cap.FixtureTree.finish

                def accepting_plaintext(self):
                    if self.nodes[self.stack[-1]]["tag"] == "plaintext":
                        self.stack = [0]
                    return finish(self)

                monkeypatch.setattr(cap.FixtureTree, "finish", accepting_plaintext)
        elif construct == "popover":
            rewrite(
                monkeypatch,
                cap.FixtureTree,
                "handle_starttag",
                [('or "popover" in attrs', "")],
                module=cap,
            )
        elif construct == "global_display":
            rewrite(
                monkeypatch,
                cap.FixtureTree,
                "handle_starttag",
                [
                    (
                        'if css(style, "display") in {"inherit", "table-column", "table-column-group"}:',
                        "if False:",
                    )
                ],
                module=cap,
            )
        elif construct == "metadata":
            rewrite(
                monkeypatch,
                cap.FixtureTree,
                "handle_starttag",
                [
                    (
                        'and tag in {"meta", "base", "link", "param", "source", "track", "area", "basefont"}',
                        "and False",
                    )
                ],
                module=cap,
            )
        elif construct == "whitespace":
            rewrite(
                monkeypatch,
                cap.FixtureTree,
                "finish",
                [('and not node["text"].strip()', "and False")],
                module=cap,
            )
        elif construct == "integration":
            rewrite(
                monkeypatch,
                cap.FixtureTree,
                "handle_starttag",
                [('if ns == MATH and parent["tag"]', 'if False and parent["tag"]')],
                module=cap,
            )
        elif construct == "entities":
            monkeypatch.setattr(cap, "fixture_attributes_safe", lambda raw: True)
        elif construct == "null":
            monkeypatch.setattr(cap.FixtureTree, "feed", cap.HTMLParser.feed)
        elif construct == "cdata":
            monkeypatch.setattr(cap.FixtureTree, "unknown_decl", lambda self, data: None)
        elif construct == "pi":
            monkeypatch.setattr(cap.FixtureTree, "handle_pi", lambda self, data: None)
        elif construct == "foreign":
            rewrite(
                monkeypatch,
                cap.FixtureTree,
                "handle_starttag",
                [("if ns != HTML and tag not in self.FOREIGN[ns]:", "if False:")],
                module=cap,
            )
        elif construct == "unbalanced":

            def pop_any(self, tag):
                for index in range(len(self.stack) - 1, 0, -1):
                    if self.nodes[self.stack[index]]["tag"] == tag:
                        for node in self.stack[index:]:
                            current = self.nodes[node]["tag"]
                            self.open_tags[current] -= 1
                            if not self.open_tags[current]:
                                del self.open_tags[current]
                        del self.stack[index:]
                        return

            monkeypatch.setattr(cap.FixtureTree, "handle_endtag", pop_any)
        elif construct == "dsd":
            rewrite(
                monkeypatch,
                cap.FixtureTree,
                "handle_starttag",
                [
                    (
                        'if tag == "template" and ({"shadowrootmode", "shadowroot"} & attrs.keys()):',
                        "if False:",
                    )
                ],
                module=cap,
            )
        elif construct == "p":
            monkeypatch.setattr(cap.FixtureTree, "BLOCK", frozenset())
        elif construct == "table":
            monkeypatch.setattr(cap.FixtureTree, "TABLE", {})
        elif construct in {"script", "style"}:
            source = __import__("inspect").getsource(cap.FixtureTree.handle_data)
            # Remove only the selected fixed refusal, leaving parsing intact.
            old = (
                'if parent["tag"] == "style" and data.strip():'
                if construct == "style"
                else 'parent["tag"] == "script"'
            )
            new = "if False:" if construct == "style" else "False"
            assert old in source
            rewrite(monkeypatch, cap.FixtureTree, "handle_data", [(old, new)], module=cap)
        else:
            raise ValueError(construct)
    elif name == "R9_COMPOSED_PARENT":
        old = "if(n_slot)return n_slot;"
        assert old in cap.WALK and old in dom.HIT_TEST
        monkeypatch.setattr(cap, "WALK", cap.WALK.replace(old, ""))
        monkeypatch.setattr(dom, "HIT_TEST", dom.HIT_TEST.replace(old, ""))
    elif name == "R9_IMG_LOOKTHROUGH":
        old = " || (v_display==='contents' && v_replaced.has(D.localName(n_node)))"
        assert old in cap.WALK
        monkeypatch.setattr(cap, "WALK", cap.WALK.replace(old, ""))
        monkeypatch.setattr(cap, "CONTENTS_NONE", frozenset())
    elif name == "R9_HIDDEN_STATE":
        rewrite(
            monkeypatch,
            facebook._DOMExtractor,
            "handle_starttag",
            [('if visibility and (fb_role == "post"', 'if (fb_role == "post"')],
        )
    elif name == "R9_CONTROL_KEY":
        rewrite(
            monkeypatch,
            facebook._PlaywrightSession,
            "_capture",
            [('identity: node["backend"]', 'node["attrs"].get("data-expand-id"): node["backend"]')],
        )
    elif name == "R9_DUPLICATE_LAST":
        rewrite(
            monkeypatch,
            cap,
            "controls",
            [
                ("if identity not in seen:", "if True:"),
                ("return result", "return list(dict(result).items())"),
            ],
            module=cap,
        )
    elif name == "R9_CONTAINS":
        monkeypatch.setattr(
            dom, "HIT_TEST", "function(n_node){return this===n_node || this.contains(n_node)}"
        )
    elif name == "R9_REVALIDATE":
        rewrite(
            monkeypatch,
            facebook._PlaywrightSession,
            "_expand_next",
            [("if self._capture_controls.get(control_id) != backend:", "if False:")],
        )
    else:
        raise ValueError(name)


@pytest.fixture(autouse=True)
def inject(monkeypatch):
    name = os.environ.get("METAMUSE_FACEBOOK_ROUND9_MUTANT")
    if name:
        apply(monkeypatch, name)
