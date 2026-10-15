"""Read-only CDP capture and static fixture trees. No HTML visibility channel."""

# ruff: noqa: E501

from __future__ import annotations

import json
import re
from collections import Counter
from html.entities import html5 as HTML_ENTITIES
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
CONTENTS_NONE = frozenset(
    "img input textarea select video audio canvas iframe embed object br wbr meter progress frame frameset".split()
)
VOID = frozenset(
    (
        "area base basefont bgsound br col embed frame hr img input keygen "
        "link meta param source track wbr"
    ).split()
)
LEGACY_ENTITIES = frozenset(key for key in HTML_ENTITIES if not key.endswith(";"))


def fixture_attributes_safe(raw: str) -> bool:
    # HTMLParser unescapes legacy prefixes in attributes where HTML5 keeps the
    # spelling literal (e.g. &notit;). Accept exact semicolon-terminated names;
    # refuse ambiguous legacy spellings rather than approximating the tokenizer.
    for match in re.finditer(r"&([A-Za-z][A-Za-z0-9]*)(;?)", raw):
        name, semicolon = match.groups()
        if semicolon and name + ";" in HTML_ENTITIES:
            continue
        if any(name.startswith(prefix) for prefix in LEGACY_ENTITIES):
            return False
    return True


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


def controls(nodes: list[dict[str, Any]]):
    """One identity assignment in composed order, shared by capture and extraction."""
    pending, seen, result = [0], set(), []
    while pending:
        node = nodes[pending.pop()]
        attrs = node["attrs"]
        if node["rendered"] and attrs.get("data-expand-kind", "").lower() in {
            "more_comments",
            "more_replies",
        }:
            identity = attrs.get("data-expand-id") or f"expand_{len(result) + 1}"
            if identity not in seen:
                seen.add(identity)
                result.append((identity, node))
        pending.extend(reversed(node["composed"]))
    return result


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
            if node["rendered"] and node["attrs"].get("data-expand-kind", "").lower() in {
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
    """Balanced, inline-CSS-only test subset; unsupported tree construction refuses."""

    CDATA_CONTENT_ELEMENTS = ("script", "style", "noscript")
    REFUSED = frozenset(
        "title xmp iframe noembed noframes plaintext textarea dialog select option optgroup frameset frame embed".split()
    )
    HTML_TAGS = VOID | frozenset(
        (
            "html head body div span p article section form button a ul ol li table tbody "
            "thead tfoot tr td th caption colgroup template script style noscript details "
            "summary b i u strong em s small mark code pre blockquote h1 h2 h3 h4 h5 h6 "
            "time label header footer main nav aside figure figcaption address fieldset "
            "legend audio video canvas object meter progress"
        ).split()
    )
    FOREIGN = {
        SVG: frozenset(
            "svg g text rect circle line path ellipse polyline polygon tspan defs use symbol".split()
        ),
        MATH: frozenset(
            "math mrow mi mn mo mtext ms mfrac msqrt msup msub mover munder mtable mtr mtd".split()
        ),
    }
    BLOCK = frozenset(
        "address article aside blockquote details div dl fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr main menu nav ol p pre section summary table ul".split()
    )
    TABLE = {
        "table": {"caption", "colgroup", "thead", "tbody", "tfoot"},
        "colgroup": {"col"},
        "thead": {"tr"},
        "tbody": {"tr"},
        "tfoot": {"tr"},
        "tr": {"td", "th"},
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes: list[dict[str, Any]] = [
            dict(kind=9, tag="", ns="", attrs={}, text="", rendered=False, children=[], composed=[])
        ]
        self.stack = [0]
        self.open_tags: Counter[str] = Counter()
        self.wrappers: set[str] = set()

    def feed(self, data):
        if "\0" in data:
            raise failure()  # HTML5 removes/replaces NULs by tokenizer state.
        super().feed(data)

    def handle_starttag(self, tag, attrs):
        if not fixture_attributes_safe(self.get_starttag_text()):
            raise failure()
        parent = self.nodes[self.stack[-1]]
        tags = self.open_tags
        ns = parent["ns"] or HTML
        if ns == MATH and parent["tag"] in {"mi", "mo", "mn", "ms", "mtext"}:
            raise failure()  # MathML text integration points can switch to HTML.
        if tag == "svg" and ns == HTML:
            ns = SVG
        elif tag == "math" and ns == HTML:
            ns = MATH
        pairs = [(k.lower(), v or "") for k, v in attrs]
        attrs = dict(pairs)
        if (
            len(attrs) != len(pairs)
            or "popover" in attrs
            or tag in self.REFUSED
            or any(name.startswith("on") for name in attrs)
        ):
            raise failure()
        if (
            (tag == "script" and "src" in attrs)
            or (tag == "link" and "href" in attrs)
            or (tag == "meta" and attrs.get("http-equiv", "").lower() == "refresh")
            or (tag == "object" and "data" in attrs)
        ):
            raise failure()
        if (
            ns == HTML
            and tag not in self.HTML_TAGS
            and not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+", tag)
        ):
            raise failure()
        style = attrs.get("style", "")
        if css(style, "display") == "inherit":
            raise failure()  # Requires resolved parent/UA display, not the inline token.
        if (
            ns == HTML
            and tag in {"meta", "base", "link", "param", "source", "track"}
            and css(style, "display") is not None
        ):
            raise failure()  # Metadata display overrides are outside the static subset.
        if (
            "\\" in style
            or "/*" in style
            or re.search(r"(?:^|;)\s*(?:all|animation[^:]*|transition[^:]*)\s*:", style, re.I)
        ):
            raise failure()
        for declaration in style.split(";"):
            if ":" not in declaration:
                continue
            name, value = declaration.split(":", 1)
            if name.strip().lower() in {"display", "visibility", "content-visibility"}:
                value = re.sub(r"!\s*important\s*$", "", value.strip(), flags=re.I).strip()
                if not re.fullmatch(r"[a-zA-Z-]+", value):
                    raise failure()
        if (
            tag in {"audio", "video", "canvas", "meter", "progress"}
            and css(style, "display") != "contents"
        ):
            raise failure()
        if ns != HTML and tag not in self.FOREIGN[ns]:
            raise failure()  # No HTML breakout or foreign integration points.
        if tag == "template" and ({"shadowrootmode", "shadowroot"} & attrs.keys()):
            raise failure()
        if (
            parent["ns"] == HTML
            and parent["tag"] in self.TABLE
            and (ns != HTML or tag not in self.TABLE[parent["tag"]])
        ):
            raise failure()  # Includes foreign-content foster parenting.
        if ns == HTML:
            if tag in {"html", "head", "body"}:
                if tag in self.wrappers or (tag == "head" and "body" in self.wrappers):
                    raise failure()
                self.wrappers.add(tag)
            if (
                (tag in self.BLOCK and "p" in tags)
                or (tag in {"form", "a", "button"} and tag in tags)
                or (
                    tag in {"h1", "h2", "h3", "h4", "h5", "h6"}
                    and any(t in tags for t in {"h1", "h2", "h3", "h4", "h5", "h6"})
                )
            ):
                raise failure()
            if tag == "li" and parent["tag"] not in {"ul", "ol"}:
                raise failure()
            if parent["tag"] in self.TABLE and tag not in self.TABLE[parent["tag"]]:
                raise failure()
            table_parts = {t for values in self.TABLE.values() for t in values}
            if tag in table_parts and tag not in self.TABLE.get(parent["tag"], set()):
                raise failure()
            if parent["tag"] == "head" and tag not in {
                "meta",
                "link",
                "base",
                "script",
                "style",
                "noscript",
            }:
                raise failure()
            if tag == "html" and self.stack != [0]:
                raise failure()
            if tag in {"head", "body"} and (parent["tag"] not in {"", "html"} or tag in tags):
                raise failure()
        node = dict(
            kind=1, tag=tag, ns=ns, attrs=attrs, text="", rendered=False, children=[], composed=[]
        )
        index = len(self.nodes)
        self.nodes.append(node)
        parent["children"].append(index)
        if ns != HTML or tag not in VOID:
            self.open_tags[tag] += 1
        if ns == HTML and tag == "template":
            # Template children live in a DocumentFragment, not on the element.
            self.stack.append(index)
            fragment = len(self.nodes)
            self.nodes.append(
                dict(
                    kind=11,
                    tag="",
                    ns="",
                    attrs={},
                    text="",
                    rendered=False,
                    children=[],
                    composed=[],
                )
            )
            node["children"].append(fragment)
            self.stack.append(fragment)
        elif ns != HTML or tag not in VOID:
            self.stack.append(index)
        if len(self.nodes) > MAX_NODES:
            raise failure()

    def handle_endtag(self, tag):
        if self.nodes[self.stack[-1]]["kind"] == 11:
            if tag != "template":
                raise failure()
            self.stack.pop()
        current = self.nodes[self.stack[-1]]
        if current["ns"] in {"", HTML} and tag in VOID:
            if tag == "br":
                self.handle_starttag("br", [])  # HTML5 treats </br> as <br>.
            return
        if len(self.stack) == 1 or current["tag"] != tag:
            raise failure()
        self.stack.pop()
        self.open_tags[tag] -= 1
        if not self.open_tags[tag]:
            del self.open_tags[tag]

    def handle_startendtag(self, tag, attrs):
        index = len(self.nodes)
        self.handle_starttag(tag, attrs)
        current = self.nodes[index]
        if current["ns"] != HTML:
            self.handle_endtag(tag)
        elif tag not in VOID:
            raise failure()

    def handle_data(self, data):
        parent = self.nodes[self.stack[-1]]
        if (
            parent["ns"] not in {"", HTML}
            and data.strip()
            and parent["tag"] not in {"text", "tspan", "mi", "mn", "mo", "mtext", "ms"}
        ):
            raise failure()
        if parent["tag"] == "pre" and not parent["children"] and data.startswith("\n"):
            data = data[1:]
        if not data:
            return
        if parent["tag"] == "head" and data.strip():
            raise failure()
        if parent["tag"] in self.TABLE and data.strip():
            raise failure()  # HTML5 foster parenting is outside the subset.
        if parent["tag"] == "style" and data.strip():
            raise failure()  # Stylesheets require Chromium's CSS cascade.
        if (
            parent["tag"] == "script"
            and parent["attrs"].get("type", "").lower()
            not in {"application/json", "application/ld+json"}
            and data.strip()
        ):
            raise failure()  # Static fixtures never simulate executable scripts.
        if parent["children"] and self.nodes[parent["children"][-1]]["kind"] == 3:
            self.nodes[parent["children"][-1]]["text"] += data
        else:
            parent["children"].append(len(self.nodes))
            self.nodes.append(
                dict(
                    kind=3,
                    tag="",
                    ns="",
                    attrs={},
                    text=data,
                    rendered=False,
                    children=[],
                    composed=[],
                )
            )
        if len(self.nodes) > MAX_NODES:
            raise failure()

    def handle_comment(self, data):
        self.nodes[self.stack[-1]]["children"].append(len(self.nodes))
        self.nodes.append(
            dict(kind=8, tag="", ns="", attrs={}, text="", rendered=False, children=[], composed=[])
        )

    def handle_decl(self, decl):
        if decl.lower() != "doctype html" or self.stack != [0]:
            raise failure()
        self.nodes[0]["children"].append(len(self.nodes))
        self.nodes.append(
            dict(
                kind=10, tag="", ns="", attrs={}, text="", rendered=False, children=[], composed=[]
            )
        )

    def unknown_decl(self, data):
        raise failure()  # Foreign CDATA and conditional declarations need HTML5.

    def handle_pi(self, data):
        raise failure()  # HTML turns processing instructions into comment nodes.

    def finish(self):
        if self.stack != [0]:
            raise failure()
        self.complete_document()
        validate(self.nodes)
        pending = [(0, False, True)]
        while pending:
            i, blocked, visibility = pending.pop()
            node = self.nodes[i]
            attrs, tag = node["attrs"], node["tag"]
            style = attrs.get("style", "")
            display = css(style, "display")
            vis = css(style, "visibility")
            if vis not in (None, "inherit", "unset", "revert", "revert-layer"):
                visibility = vis in {"visible", "initial"}
            hidden = attrs.get("hidden", "").lower()
            blocked = (
                blocked
                or (
                    node["ns"] == HTML
                    and tag
                    in {
                        "head",
                        "meta",
                        "title",
                        "script",
                        "style",
                        "noscript",
                        "template",
                        "base",
                        "link",
                        "param",
                        "source",
                        "track",
                    }
                )
                or display == "none"
                or (display == "contents" and tag in CONTENTS_NONE)
                or (
                    node["ns"] == HTML
                    and "hidden" in attrs
                    and display in (None, "none", "revert-layer")
                )
                or hidden == "until-found"
                or (tag == "input" and attrs.get("type", "").lower() == "hidden")
            )
            node["rendered"] = node["kind"] in {1, 3} and not blocked and visibility
            if node["rendered"] and node["kind"] == 3 and node["text"] and not node["text"].strip():
                raise failure()  # A layout engine is required for whitespace-only boxes.
            node["composed"] = (
                []
                if node["ns"] == HTML and tag in {"template", "noscript"}
                else list(node["children"])
            )
            summary = next(
                (
                    j
                    for j in node["children"]
                    if self.nodes[j]["ns"] == HTML and self.nodes[j]["tag"] == "summary"
                ),
                None,
            )
            for child in reversed(node["children"]):
                pending.append(
                    (
                        child,
                        blocked
                        or css(style, "content-visibility") == "hidden"
                        or (tag == "details" and "open" not in attrs and child != summary),
                        visibility,
                    )
                )
        return self.nodes

    def complete_document(self):
        """Insert the HTML5 document scaffold, including its bounded node cost.

        The subset refuses misplaced explicit wrappers instead of modelling the
        HTML5 insertion-mode repair machinery. Ordinary fragments use body context.
        """
        nodes = self.nodes

        def shell(tag):
            index = len(nodes)
            nodes.append(
                dict(
                    kind=1,
                    tag=tag,
                    ns=HTML,
                    attrs={},
                    text="",
                    rendered=False,
                    children=[],
                    composed=[],
                )
            )
            return index

        root = nodes[0]["children"]
        htmls = [i for i in root if nodes[i]["tag"] == "html"]
        if htmls:
            html = htmls[0]
            if any(i != html and nodes[i]["kind"] not in {8, 10} for i in root):
                raise failure()
            children = nodes[html]["children"]
            if any(
                nodes[i]["tag"] not in {"head", "body"} and nodes[i]["kind"] != 8 for i in children
            ):
                raise failure()
        else:
            html = shell("html")
            children = [i for i in root if nodes[i]["kind"] != 10]
            nodes[0]["children"] = [i for i in root if nodes[i]["kind"] == 10] + [html]
        heads = [i for i in children if nodes[i]["tag"] == "head"]
        bodies = [i for i in children if nodes[i]["tag"] == "body"]
        head = heads[0] if heads else shell("head")
        body = bodies[0] if bodies else shell("body")
        others = [i for i in children if i not in {head, body}]
        if bodies and any(nodes[i]["kind"] != 8 for i in others):
            raise failure()
        if heads and any(nodes[i]["kind"] not in {8, 10} for i in children[: children.index(head)]):
            raise failure()
        if not bodies:
            nodes[body]["children"] = others
        nodes[html]["children"] = (
            (children if heads else [head] + children) if bodies else [head, body]
        )
        # Structural pre-order is also the flat capture order. This is linear,
        # iterative, and does not discard any evidence or evade the node bound.
        order, pending = [], [0]
        while pending:
            i = pending.pop()
            order.append(i)
            pending.extend(reversed(nodes[i]["children"]))
        ids = {i: j for j, i in enumerate(order)}
        self.nodes = [
            dict(nodes[i], children=[ids[j] for j in nodes[i]["children"]]) for i in order
        ]


def fixture_capture(markup: str) -> list[dict[str, Any]]:
    try:
        if len(markup.encode()) > MAX_TEXT_BYTES:
            raise failure()
        parser = FixtureTree()
        parser.feed(markup.replace("\r\n", "\n").replace("\r", "\n"))
        if parser.rawdata:
            raise failure()  # Incomplete tags/comments/rawtext are outside the subset.
        parser.close()
        return parser.finish()
    except m.CoreError:
        raise
    except Exception:
        raise failure() from None
