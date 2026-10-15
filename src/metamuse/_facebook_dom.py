"""Isolated-world DOM access. Only this generated table touches platform members."""

# (prototype, member, getter). This is also the clobbering corpus vocabulary.
ACCESSORS = {
    "contentType": ("Document", "contentType", True),
    "createRange": ("Document", "createRange", False),
    "createElement": ("Document", "createElement", False),
    "body": ("Document", "body", True),
    "appendChild": ("Node", "appendChild", False),
    "removeChild": ("Node", "removeChild", False),
    "frameWindow": ("HTMLIFrameElement", "contentWindow", True),
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
    "textSlot": ("Text", "assignedSlot", True),
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

# Window's [Global] methods have own descriptors rather than prototype methods.
GLOBAL_METHODS = {"computedStyle": "getComputedStyle", "v_open": "open", "v_close": "close"}

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
    + "\n});\n"
    + "\n".join(
        f"const {name}=Object.getOwnPropertyDescriptor(globalThis,'{member}').value;"
        for name, member in GLOBAL_METHODS.items()
    )
    + "\n"
)

HELPERS = r"""
const html = 'http://www.w3.org/1999/xhtml';
function children(n_node) {
  const n_list = D.childNodes(n_node), v_result = [];
  for(let v_i=0;v_i<D.listLength(n_list);v_i++)v_result.push(D.listItem(n_list,v_i));
  return v_result;
}
function shadow(n_node) {return D.shadowRoot(n_node)||globalThis.__metamuseClosed.get(n_node);}
function slotMap(n_root) {
  const v_map=new WeakMap(), v_pending=[n_root], v_seen=new Set();let v_count=0;
  while(v_pending.length) {
    const n_node=v_pending.pop();if(v_seen.has(n_node))continue;v_seen.add(n_node);
    if(++v_count>65536)throw new Error('bound');
    const v_children=children(n_node);
    if(D.nodeType(n_node)===1) {
      if(D.namespaceURI(n_node)===html && D.localName(n_node)==='slot') {
        for(const n_assigned of D.assignedNodes(n_node))v_map.set(n_assigned,n_node);
      }
      const n_shadow=shadow(n_node);if(n_shadow)v_children.push(n_shadow);
    }
    for(const n_child of v_children)v_pending.push(n_child);
  }
  return v_map;
}
function parent(n_node) {
  const n_slot=(D.nodeType(n_node)===1?D.assignedSlot(n_node):
    (D.nodeType(n_node)===3?D.textSlot(n_node):null)) || v_slots.get(n_node);
  if(n_slot)return n_slot;
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
  const v_slots=slotMap(document), v_seen=new Set();
  while(n_node && !v_seen.has(n_node)) {
    if(n_node===this)return true;
    v_seen.add(n_node);n_node=parent(n_node);
  }
  return false;
}"""
)


# The startup probe runs before page scripts, navigation, proxy opening or DNS.
# Window's [Global] methods are own properties, not prototype members; capture
# these descriptors alongside the WebIDL prototype accessors above.
POPUP_CHECK = (
    "() => {"
    + ACCESSOR_TABLE
    + r"""
  const n_direct=apply(v_open,globalThis,['']);
  if(n_direct)apply(v_close,n_direct,[]);
  const n_frame=D.createElement(document,'iframe'), n_body=D.body(document);
  D.appendChild(n_body,n_frame);
  const n_fresh=D.frameWindow(n_frame);
  const n_window=apply(v_open,n_fresh,['']);
  if(n_window)apply(v_close,n_window,[]);
  D.removeChild(n_body,n_frame);
  return n_direct===null && n_window===null;
}"""
)
