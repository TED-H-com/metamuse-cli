"""A narrow JavaScript postfix-chain checker for the observation dialect.

Strings and comments are tokenised, then every dotted/computed postfix chain is
checked. Platform handles are n_* (including accessor results); plain r_* records
come only from the v_nodes array populated with object literals. v_* values are
program-created arrays, maps, sets, strings, numbers and booleans. Unknown dotted
receivers are refused. This deliberately accepts less than general JavaScript.
"""

import re

TOKEN = re.compile(
    r"""\s+|//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[A-Za-z_$][\w$]*|\d+|\?\.|=>|[^\s]""",
    re.S,
)
PLAIN_MEMBERS = {
    "includes",
    "length",
    "push",
    "pop",
    "map",
    "find",
    "get",
    "set",
    "has",
    "add",
    "encode",
    "toLowerCase",
    "split",
    "from",
    "fromEntries",
    "getOwnPropertyDescriptor",
    "value",
}
RECORD_MEMBERS = {"kind", "tag", "ns", "attrs", "text", "rendered", "children", "composed"}


def check(source, table, accessors):
    assert table in source
    source = source.replace(table, "")
    tokens = [
        m.group()
        for m in TOKEN.finditer(source)
        if not m.group().isspace() and not m.group().startswith(("//", "/*"))
    ]
    # Any property/index lookup on a platform handle is an error, including
    # optional chaining and computed properties. No exception for familiar names.
    for i, token in enumerate(tokens[:-1]):
        if token.startswith("n_") or token in {"document", "this"}:
            assert tokens[i + 1] not in {".", "?.", "["}, tokens[max(0, i - 2) : i + 5]
        # These arrays contain platform handles, not structured records. Their
        # elements cannot acquire the plain array/primitive postfix exemption.
        if token in {"v_raw", "v_pending", "v_children", "v_assigned"}:
            j = i + 1
            if tokens[j] == "[":
                depth, j = 1, j + 1
                while depth:
                    depth += (tokens[j] == "[") - (tokens[j] == "]")
                    j += 1
                assert j == len(tokens) or tokens[j] not in {".", "?.", "["}
            elif tokens[j : j + 4] == [".", "pop", "(", ")"]:
                assert j + 4 == len(tokens) or tokens[j + 4] not in {".", "?.", "["}
    for i, token in enumerate(tokens):
        if token not in {".", "?."}:
            continue
        receiver, member = tokens[i - 1], tokens[i + 1]
        if receiver == "D":
            assert member in accessors
        elif receiver == "r_node":
            assert member in RECORD_MEMBERS
        elif receiver == "__metamuseClosed":
            assert member in {"get", "set"}
        elif receiver == "globalThis":
            assert member in {"__metamuseNodes", "__metamuseClosed"}
        elif receiver.startswith("v_") or receiver in {"Array", "Object"}:
            assert member in PLAIN_MEMBERS, (receiver, member)
        elif receiver in {")", "]", "STRING"} or receiver.startswith(('"', "'")):
            # Calls can expose only a primitive's methods, or the length of a
            # freshly encoded byte array. DOM-valued accessor chaining is banned
            # separately below; records use the sole v_nodes[index] constructor.
            assert member in PLAIN_MEMBERS | {"children"}, (
                receiver,
                member,
            )
        else:
            raise AssertionError((receiver, member))
    dom_results = {
        "createRange",
        "createElement",
        "body",
        "appendChild",
        "removeChild",
        "frameWindow",
        "childNodes",
        "parentNode",
        "parentElement",
        "getRootNode",
        "attributes",
        "shadowRoot",
        "assignedSlot",
        "textSlot",
        "content",
        "host",
        "listItem",
        "attrsItem",
        "getClientRects",
    }
    # A balanced postfix call scanner catches D.childNodes(node).length and
    # D.content(node)['children'], not just direct n_handle.property syntax.
    for i in range(len(tokens) - 3):
        if (
            tokens[i : i + 2] != ["D", "."]
            or tokens[i + 2] not in dom_results
            or tokens[i + 3] != "("
        ):
            continue
        depth, j = 1, i + 4
        while depth:
            depth += (tokens[j] == "(") - (tokens[j] == ")")
            j += 1
        assert j == len(tokens) or tokens[j] not in {".", "?.", "["}, tokens[i : j + 2]
    # No platform result may be disguised as a plain-data local.
    for match in re.finditer(
        r"\b(?:const|let)\s+([\w$]+)\s*=\s*(?:D\.([\w$]+)\s*\("
        r"|(document|this|n_[\w$]+)\b(?=\s*[,;)]|$))",
        source,
    ):
        variable, accessor, alias = match.groups()
        if accessor in dom_results or alias:
            assert variable.startswith("n_"), (variable, accessor, alias)
    for match in re.finditer(
        r"\b(?:const|let)\s+([\w$]+)\s*=\s*"
        r"(?:v_(?:raw|pending|children|assigned)\s*(?:\[|\.pop\s*\()|(?:parent|shadow)\s*\()",
        source,
    ):
        assert match[1].startswith("n_"), match[0]
    # DOM-returning helper calls have the same postfix restriction as accessors.
    for i in range(len(tokens) - 1):
        dom_call = tokens[i] in {"parent", "shadow"} or (
            tokens[i] == "apply" and tokens[i + 2] in {"computedStyle", "v_open"}
        )
        if not dom_call or tokens[i + 1] != "(":
            continue
        if i and tokens[i - 1] == "function":
            continue
        depth, j = 1, i + 2
        while depth:
            depth += (tokens[j] == "(") - (tokens[j] == ")")
            j += 1
        assert j == len(tokens) or tokens[j] not in {".", "?.", "["}
    for match in re.finditer(
        r"\b(?:const|let)\s+([\w$]+)\s*=\s*apply\s*\(\s*(?:computedStyle|v_open)\b", source
    ):
        assert match[1].startswith("n_"), match[0]
