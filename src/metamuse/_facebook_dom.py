"""Isolated-world DOM access. Only this generated table touches platform members."""

# (prototype, member, getter). This is also the clobbering corpus vocabulary.
ACCESSORS = {
    "contentType": ("Document", "contentType", True),
    "createRange": ("Document", "createRange", False),
    "nodeType": ("Node", "nodeType", True),
    "nodeValue": ("Node", "nodeValue", True),
    "childNodes": ("Node", "childNodes", True),
    "parentNode": ("Node", "parentNode", True),
    "parentElement": ("Node", "parentElement", True),
    "getRootNode": ("Node", "getRootNode", False),
    "localName": ("Element", "localName", True),
    "namespaceURI": ("Element", "namespaceURI", True),
    "attributes": ("Element", "attributes", True),
    "shadowRoot": ("Element", "shadowRoot", True),
    "assignedSlot": ("Element", "assignedSlot", True),
    "checkVisibility": ("Element", "checkVisibility", False),
    "getAttribute": ("Element", "getAttribute", False),
    "hasAttribute": ("Element", "hasAttribute", False),
    "attrName": ("Attr", "name", True),
    "attrValue": ("Attr", "value", True),
    "content": ("HTMLTemplateElement", "content", True),
    "assignedNodes": ("HTMLSlotElement", "assignedNodes", False),
    "host": ("ShadowRoot", "host", True),
    "selectNode": ("Range", "selectNode", False),
    "getClientRects": ("Range", "getClientRects", False),
    "listLength": ("NodeList", "length", True),
    "listItem": ("NodeList", "item", False),
    "attrsLength": ("NamedNodeMap", "length", True),
    "attrsItem": ("NamedNodeMap", "item", False),
    "rectsLength": ("DOMRectList", "length", True),
    "css": ("CSSStyleDeclaration", "getPropertyValue", False),
}

# Capture functions before examining any document or node. Reflect.apply avoids
# a named platform property lookup even on HTMLFormElement/HTMLDocument.
ACCESSOR_TABLE = (
    "const apply = Reflect.apply; const D = Object.freeze({\n"
    + ",\n".join(
        f"{name}: (() => {{const f = "
        + (
            f"Object.getOwnPropertyDescriptor({proto}.prototype, '{member}').get"
            if getter
            else f"{proto}.prototype.{member}"
        )
        + "; return (n, ...args) => apply(f, n, args);})()"
        for name, (proto, member, getter) in ACCESSORS.items()
    )
    + "\n}); const computedStyle = globalThis.getComputedStyle;\n"
)

HELPERS = r"""
const html = 'http://www.w3.org/1999/xhtml';
function children(n_node) {
  const n_list = D.childNodes(n_node), v_result = [];
  for(let v_i=0;v_i<D.listLength(n_list);v_i++)v_result.push(D.listItem(n_list,v_i));
  return v_result;
}
function parent(n_node) {
  if(D.nodeType(n_node)===1) {
    const n_slot=D.assignedSlot(n_node);if(n_slot)return n_slot;
  }
  const n_parent=D.parentNode(n_node);if(n_parent)return n_parent;
  return D.nodeType(n_node)===11?D.host(n_node):null;
}
function style(n_node,v_property) {
  return D.css(apply(computedStyle,globalThis,[n_node]),v_property);
}
"""

HIT_TEST = (
    "function(n_node){"
    + ACCESSOR_TABLE
    + HELPERS
    + r"""
  const v_seen=new Set();
  while(n_node && !v_seen.has(n_node)) {
    if(n_node===this)return true;
    v_seen.add(n_node);n_node=parent(n_node);
  }
  return false;
}"""
)
