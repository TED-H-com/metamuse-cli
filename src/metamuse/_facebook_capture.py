"""Read-only CDP capture and static fixture trees. No HTML visibility channel."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any

from metamuse import models as m

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
WALK = r"""() => {
  if (document.contentType !== 'text/html') return {error:'document'};
  const nodes=[], ids=new Map(), raw=[], pending=[document]; let bytes=0;
  const utf8=new TextEncoder();
  while(pending.length) {
    const n=pending.pop(); if(ids.has(n))continue;
    if(nodes.length>=65536) return {error:'bound'};
    const kind=n.nodeType, tag=kind===1?n.localName.toLowerCase():'';
    const attrs=kind===1?Array.from(n.attributes,a=>[a.name.toLowerCase(),a.value]):[];
    const text=kind===3?n.data:'';
    bytes+=utf8.encode(text).length;
    for(const a of attrs) bytes+=utf8.encode(a[0]+a[1]).length;
    if(bytes>8388608)return {error:'bound'};
    ids.set(n,nodes.length);raw.push(n);
    nodes.push({kind,tag,ns:n.namespaceURI||'',attrs:Object.fromEntries(attrs),text,rendered:false,children:[],composed:[]});
    const children=Array.from(n.childNodes);
    if(kind===1 && tag==='template')children.push(n.content);
    const shadow=kind===1?(n.shadowRoot||globalThis.__metamuseClosed.get(n)):null;
    if(shadow)children.push(shadow);
    for(let i=children.length-1;i>=0;i--)pending.push(children[i]);
  }
  for(let i=0;i<raw.length;i++) {
    const n=raw[i], children=Array.from(n.childNodes);
    if(n.nodeType===1&&n.localName==='template')children.push(n.content);
    const shadow=n.nodeType===1?(n.shadowRoot||globalThis.__metamuseClosed.get(n)):null;
    if(shadow)children.push(shadow);
    nodes[i].children=children.map(n=>ids.get(n));
  }
  function box(el) {
    while(el && getComputedStyle(el).display==='contents') {
      el=el.parentElement||el.getRootNode().host;
    }
    return !!el && el.checkVisibility();
  }
  const stack=[[document,null,false]], seen=new Set();
  while(stack.length) {
    const [n,parent,excluded]=stack.pop(); if(seen.has(n))continue;seen.add(n);
    const i=ids.get(n);if(i===undefined)return {error:'tree'};
    const rec=nodes[i], el=n.nodeType===1;
    let blocked=excluded;
    if(el && ['noscript','template','script','style','head'].includes(rec.tag))blocked=true;
    if(el && n.getAttribute('hidden')?.toLowerCase()==='until-found')blocked=true;
    if(el)rec.rendered=!blocked && getComputedStyle(n).visibility==='visible' && box(n);
    if(n.nodeType===3) {
      // Computed visibility inherits through the composed parent; display:contents
      // has no box of its own and must be looked through.
      const p=n.parentElement?.localName==='slot'?n.parentElement:parent;
      const range=document.createRange();range.selectNode(n);
      let ancestor=p;
      while(ancestor && getComputedStyle(ancestor).display==='contents') {
        ancestor=ancestor.parentElement||ancestor.getRootNode().host;
      }
      const autoSkipped=ancestor && ancestor.checkVisibility() &&
        !ancestor.checkVisibility({contentVisibilityAuto:true});
      const textBox=range.getClientRects().length>0 || autoSkipped;
      rec.rendered=!blocked && !!p && getComputedStyle(p).visibility==='visible' &&
        box(p) && textBox;
    }
    let children=Array.from(n.childNodes);
    if(el) {
      const shadow=n.shadowRoot||globalThis.__metamuseClosed.get(n);
      if(shadow)children=Array.from(shadow.childNodes);
      else if(rec.tag==='slot') {
        const assigned=n.assignedNodes();if(assigned.length)children=Array.from(assigned);
      }
      else if(['template','noscript','iframe'].includes(rec.tag))children=[];
    }
    rec.composed=children.map(n=>ids.get(n));
    const closedDetails=el && rec.tag==='details' && !n.hasAttribute('open');
    const summary=closedDetails?children.find(c=>c.nodeType===1 && c.localName==='summary'):null;
    const hiddenContent=el && getComputedStyle(n).contentVisibility==='hidden';
    for(let j=children.length-1;j>=0;j--) {
      const childBlocked=blocked || hiddenContent || (closedDetails && children[j]!==summary);
      stack.push([children[j],el?n:parent,childBlocked]);
    }
  }
  globalThis.__metamuseNodes=raw;
  return {nodes};
}"""


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
