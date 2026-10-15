"""Read-only CDP capture and static fixture trees. No HTML visibility channel."""

# ruff: noqa: E501

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any

from metamuse import models as m
from metamuse._facebook_dom import ACCESSOR_TABLE, HELPERS

# At most 8 MiB of text/attributes and 65,536 nodes, independent of job byte limits.
MAX_NODES = 65_536
MAX_TEXT_BYTES = 8 * 1024 * 1024
HTML = "http://www.w3.org/1999/xhtml"
SVG = "http://www.w3.org/2000/svg"
MATH = "http://www.w3.org/1998/Math/MathML"
VOID = frozenset(
    (
        "area base basefont bgsound br col embed frame hr img input keygen "
        "link meta param source track wbr"
    ).split()
)


def failure() -> m.CoreError:
    return m.CoreError(
        m.ErrorCode.SELECTOR_DRIFT,
        "Facebook structured capture could not be completed",
        next_action="Inspect the supported Facebook post DOM before retrying",
    )


def css(style: str, name: str) -> str | None:
    declarations = re.findall(
        r"(?:^|;)\s*" + name + r"\s*:\s*([a-z-]+)\s*(!\s*important)?\s*(?=;|$)", style.lower()
    )
    globals_ = {"inherit", "initial", "unset", "revert", "revert-layer"}
    allowed = {
        "visibility": {"visible", "hidden", "collapse"} | globals_,
        "content-visibility": {"visible", "hidden", "auto"} | globals_,
        "display": set(
            (
                "none block inline inline-block flex inline-flex grid inline-grid "
                "contents flow-root list-item table inline-table table-row-group "
                "table-header-group table-footer-group table-row table-column-group "
                "table-column table-cell table-caption ruby ruby-base ruby-text "
                "ruby-base-container ruby-text-container"
            ).split()
        )
        | globals_,
    }[name]
    declarations = [entry for entry in declarations if entry[0] in allowed]
    important = [entry for entry in declarations if entry[1]]
    return (important or declarations)[-1][0] if declarations else None


def encode(nodes: list[dict[str, Any]]) -> str:
    return json.dumps(nodes, ensure_ascii=False, separators=(",", ":"))


# This code executes only in a CDP isolated world. It neither creates nor clones
# elements and performs no DOM writes. Closed roots are supplied by CDP handles.
WALK = (
    "() => {"
    + ACCESSOR_TABLE
    + HELPERS
    + r"""
  if (D.contentType(document) !== 'text/html') return {error:'document'};
  const v_nodes=[], v_ids=new Map(), v_raw=[], v_pending=[document]; let v_bytes=0;
  const v_utf8=new TextEncoder();
  function shadow(n_node) {return D.shadowRoot(n_node)||globalThis.__metamuseClosed.get(n_node);}
  function allChildren(n_node) {
    const v_children=children(n_node);
    if(D.nodeType(n_node)===1) {
      if(D.namespaceURI(n_node)===html && D.localName(n_node)==='template')v_children.push(D.content(n_node));
      const n_shadow=shadow(n_node);if(n_shadow)v_children.push(n_shadow);
    }
    return v_children;
  }
  while(v_pending.length) {
    const n_node=v_pending.pop(); if(v_ids.has(n_node))continue;
    if(v_nodes.length>=65536) return {error:'bound'};
    const v_kind=D.nodeType(n_node), v_tag=v_kind===1?D.localName(n_node).toLowerCase():'';
    const v_attrs=[];
    if(v_kind===1) {
      const n_attrs=D.attributes(n_node);
      for(let v_i=0;v_i<D.attrsLength(n_attrs);v_i++) {
        const n_attr=D.attrsItem(n_attrs,v_i);
        v_attrs.push([D.attrName(n_attr).toLowerCase(),D.attrValue(n_attr)]);
      }
    }
    const v_text=v_kind===3?D.nodeValue(n_node):'';
    v_bytes+=v_utf8.encode(v_text).length;
    for(const v_a of v_attrs) v_bytes+=v_utf8.encode(v_a[0]+v_a[1]).length;
    if(v_bytes>8388608)return {error:'bound'};
    v_ids.set(n_node,v_nodes.length);v_raw.push(n_node);
    v_nodes.push({kind:v_kind,tag:v_tag,ns:v_kind===1?D.namespaceURI(n_node):'',attrs:Object.fromEntries(v_attrs),text:v_text,rendered:false,children:[],composed:[]});
    const v_children=allChildren(n_node);
    for(let v_i=v_children.length-1;v_i>=0;v_i--)v_pending.push(v_children[v_i]);
  }
  for(let v_i=0;v_i<v_raw.length;v_i++)v_nodes[v_i].children=allChildren(v_raw[v_i]).map(n_child=>v_ids.get(n_child));
  const v_replaced=new Set('img input textarea select video audio canvas iframe embed object br wbr meter progress frame frameset'.split(' '));
  function noBox(n_node) {
    const v_display=style(n_node,'display');
    return v_display==='none' || (v_display==='contents' && v_replaced.has(D.localName(n_node)));
  }
  function box(n_node) {
    while(n_node) {
      if(D.nodeType(n_node)===1) {
        if(noBox(n_node))return false;
        if(style(n_node,'display')!=='contents')return D.checkVisibility(n_node);
      }
      n_node=parent(n_node);
    }
    return false;
  }
  const v_stack=[[document,null,false]], v_seen=new Set();
  while(v_stack.length) {
    const [n_node,n_parent,v_excluded]=v_stack.pop(); if(v_seen.has(n_node))continue;v_seen.add(n_node);
    const v_i=v_ids.get(n_node);if(v_i===undefined)return {error:'tree'};
    const r_node=v_nodes[v_i], v_el=D.nodeType(n_node)===1;
    let v_blocked=v_excluded;
    if(v_el && r_node.ns===html && ['noscript','template','script','style','head'].includes(r_node.tag))v_blocked=true;
    if(v_el && (D.getAttribute(n_node,'hidden')?.toLowerCase()==='until-found'||noBox(n_node)))v_blocked=true;
    if(v_el)r_node.rendered=!v_blocked && style(n_node,'visibility')==='visible' && box(n_node);
    if(D.nodeType(n_node)===3) {
      const n_p=n_parent;
      const n_range=D.createRange(document);D.selectNode(n_range,n_node);
      let n_ancestor=n_p;
      while(n_ancestor && (D.nodeType(n_ancestor)!==1 || style(n_ancestor,'display')==='contents'))n_ancestor=parent(n_ancestor);
      const v_autoSkipped=n_ancestor && box(n_ancestor) && !D.checkVisibility(n_ancestor,{contentVisibilityAuto:true});
      const v_textBox=D.rectsLength(D.getClientRects(n_range))>0 || v_autoSkipped;
      r_node.rendered=!v_blocked && !!n_p && style(n_p,'visibility')==='visible' && box(n_p) && v_textBox;
    }
    let v_children=children(n_node);
    if(v_el) {
      const n_shadow=shadow(n_node);
      if(n_shadow)v_children=children(n_shadow);
      else if(r_node.ns===html && r_node.tag==='slot') {
        const v_assigned=D.assignedNodes(n_node);if(v_assigned.length)v_children=Array.from(v_assigned);
      }
      else if(r_node.ns===html && ['template','noscript','iframe'].includes(r_node.tag))v_children=[];
    }
    r_node.composed=v_children.map(n_child=>v_ids.get(n_child));
    const v_closedDetails=v_el && r_node.ns===html && r_node.tag==='details' && !D.hasAttribute(n_node,'open');
    const n_summary=v_closedDetails?v_children.find(n_c=>D.nodeType(n_c)===1 && D.namespaceURI(n_c)===html && D.localName(n_c)==='summary'):null;
    const v_hiddenContent=v_el && style(n_node,'content-visibility')==='hidden';
    for(let v_j=v_children.length-1;v_j>=0;v_j--)v_stack.push([v_children[v_j],v_el?n_node:n_parent,v_blocked || v_hiddenContent || (v_closedDetails && v_children[v_j]!==n_summary)]);
  }
  globalThis.__metamuseNodes=v_raw;
  return {nodes:v_nodes};
}"""
)


def capture(cdp: Any) -> tuple[list[dict[str, Any]], int, tuple[str, ...]]:
    """One isolated world for the current main-frame document; no iframe evidence."""
    changed = []
    # Text/attribute edits cannot introduce an undiscovered closed root. An
    # inserted subtree or new shadow root can, so preserve that coverage gap.
    events = ("DOM.childNodeInserted", "DOM.shadowRootPushed")

    def mutation(_event):
        if not changed:
            changed.append(True)

    try:
        for event in events:
            cdp.on(event, mutation)
        frame = cdp.send("Page.getFrameTree")["frameTree"]["frame"]
        world = cdp.send(
            "Page.createIsolatedWorld", {"frameId": frame["id"], "worldName": "metamuse-read-only"}
        )["executionContextId"]
        cdp.send(
            "Runtime.evaluate",
            {"expression": "globalThis.__metamuseClosed=new WeakMap()", "contextId": world},
        )
        document = cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})["root"]
        pending = [document]
        gaps: set[str] = set()
        count = 0
        while pending:
            node = pending.pop()
            count += 1
            if count > MAX_NODES:
                raise failure()
            pending.extend(node.get("children", ()))
            if node.get("templateContent"):
                pending.append(node["templateContent"])
            for root in node.get("shadowRoots", ()):
                pending.append(root)
                if root.get("shadowRootType") != "closed":
                    continue
                try:
                    host = cdp.send(
                        "DOM.resolveNode",
                        {"backendNodeId": node["backendNodeId"], "executionContextId": world},
                    )["object"]["objectId"]
                    shadow = cdp.send(
                        "DOM.resolveNode",
                        {"backendNodeId": root["backendNodeId"], "executionContextId": world},
                    )["object"]["objectId"]
                    result = cdp.send(
                        "Runtime.callFunctionOn",
                        {
                            "objectId": host,
                            "functionDeclaration": (
                                "function(root){globalThis.__metamuseClosed.set(this,root)}"
                            ),
                            "arguments": [{"objectId": shadow}],
                        },
                    )
                    if "exceptionDetails" in result:
                        raise failure()
                except Exception:
                    gaps.add("closed_shadow_capture_unavailable")
        result = cdp.send(
            "Runtime.evaluate",
            {"expression": f"({WALK})()", "contextId": world, "returnByValue": True},
        )
        data = result.get("result", {}).get("value", {})
        if "exceptionDetails" in result or "nodes" not in data:
            raise failure()
        nodes = data["nodes"]
        validate(nodes)
        # Only controls need protocol identities. Main-world CSS selectors are
        # never used to substitute a duplicate node for the observed control.
        for i, node in enumerate(nodes):
            if node["rendered"] and node["attrs"].get("data-expand-kind") in {
                "more_comments",
                "more_replies",
            }:
                obj = cdp.send(
                    "Runtime.evaluate",
                    {"expression": f"globalThis.__metamuseNodes[{i}]", "contextId": world},
                )["result"]["objectId"]
                node["backend"] = cdp.send("DOM.describeNode", {"objectId": obj})["node"][
                    "backendNodeId"
                ]
        if changed:
            gaps.add("capture_changed_during_observation")
        return nodes, world, tuple(sorted(gaps))
    except m.CoreError:
        raise
    except Exception:
        raise failure() from None
    finally:
        try:
            for event in events:
                cdp.remove_listener(event, mutation)
        except Exception:
            raise failure() from None


def validate(nodes: list[dict[str, Any]]) -> None:
    if (
        len(nodes) > MAX_NODES
        or sum(
            len(n["text"].encode()) + sum(len((k + v).encode()) for k, v in n["attrs"].items())
            for n in nodes
        )
        > MAX_TEXT_BYTES
    ):
        raise failure()


class FixtureTree(HTMLParser):
    # Scripting-on noscript is raw text, just like Chromium's HTML tokenizer.
    CDATA_CONTENT_ELEMENTS = ("script", "style", "noscript")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes: list[dict[str, Any]] = [
            dict(kind=9, tag="", ns="", attrs={}, text="", rendered=False, children=[], composed=[])
        ]
        self.stack = [0]

    def handle_starttag(self, tag, attrs):
        parent = self.nodes[self.stack[-1]]
        ns = parent["ns"] or HTML
        if parent["tag"] in {"foreignobject", "annotation-xml"}:
            ns = HTML
        if tag == "svg":
            ns = SVG
        elif tag == "math":
            ns = MATH
        attrs = {k.lower(): v or "" for k, v in attrs}
        if tag == "template" and "shadowrootmode" in attrs:
            raise failure()  # Static fixtures deliberately refuse declarative roots.
        node = dict(
            kind=1,
            tag=tag.lower(),
            ns=ns,
            attrs=attrs,
            text="",
            rendered=False,
            children=[],
            composed=[],
        )
        index = len(self.nodes)
        self.nodes.append(node)
        parent["children"].append(index)
        if ns != HTML or tag not in VOID:
            self.stack.append(index)
        if len(self.nodes) > MAX_NODES:
            raise failure()

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.nodes[self.stack[index]]["tag"] == tag.lower():
                del self.stack[index:]
                return

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.nodes[self.stack[-1]]["ns"] != HTML:
            self.handle_endtag(tag)

    def handle_data(self, data):
        node = dict(
            kind=3, tag="", ns="", attrs={}, text=data, rendered=False, children=[], composed=[]
        )
        self.nodes[self.stack[-1]]["children"].append(len(self.nodes))
        self.nodes.append(node)
        if len(self.nodes) > MAX_NODES:
            raise failure()

    def finish(self):
        validate(self.nodes)
        pending = [(0, False, True)]
        while pending:
            i, blocked, visibility = pending.pop()
            node = self.nodes[i]
            attrs = node["attrs"]
            tag = node["tag"]
            style = attrs.get("style", "")
            display = css(style, "display")
            vis = css(style, "visibility")
            if vis not in (None, "inherit", "unset", "revert", "revert-layer"):
                visibility = vis in {"visible", "initial"}
            hidden = attrs.get("hidden", "").lower()
            blocked = (
                blocked
                or tag in {"head", "meta", "title", "script", "style", "noscript", "template"}
                or display == "none"
                or ("hidden" in attrs and display in (None, "none", "revert-layer"))
                or hidden == "until-found"
                or (tag == "input" and attrs.get("type", "").lower() == "hidden")
            )
            node["rendered"] = not blocked and visibility
            node["composed"] = list(node["children"])
            summary = next((j for j in node["children"] if self.nodes[j]["tag"] == "summary"), None)
            for child in reversed(node["children"]):
                child_blocked = (
                    blocked
                    or css(style, "content-visibility") == "hidden"
                    or (tag == "details" and "open" not in attrs and child != summary)
                )
                pending.append((child, child_blocked, visibility))
        return self.nodes


def fixture_capture(markup: str) -> list[dict[str, Any]]:
    try:
        if len(markup.encode()) > MAX_TEXT_BYTES:
            raise failure()
        parser = FixtureTree()
        parser.feed(markup)
        parser.close()
        return parser.finish()
    except m.CoreError:
        raise
    except Exception:
        raise failure() from None
