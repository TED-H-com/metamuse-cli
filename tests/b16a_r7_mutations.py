# ruff: noqa: E501
"""Round-7 semantic restorations and delimiter-ownership mutation operators."""

import b16a_r6_mutations as prior
from b16a_r3_mutations import LIFE, SAFE, add, within

base = prior.base
STRUCT = "tests/test_b16a_round7_structure.py"
CPU = "tests/test_b16a_round7_complexity.py"
CTX = "tests/test_b16a_round7_context.py"


def new(name, edit, tests=STRUCT, selection="", file=SAFE):
    add(name, file, edit, tests, selection)


def adapt(name, edit, tests=None, selection=None):
    file, _, oldtests, oldselection, equivalent = base.MUTANTS[name]
    add(
        name,
        file,
        edit,
        tests or oldtests,
        oldselection if selection is None else selection,
        equivalent,
    )


new(
    "r7_d1_unowned_closer",
    within(
        "value",
        """            if char in "\\\"'`[]{}()":
""",
        """            if char in "\\\"'`[]{}()":
                return pos
""",
    ),
)
new(
    "r7_d1_adjacency_space",
    within(
        "owned_end",
        """            else:
                break
""",
        """            elif char.isspace():
                cursor -= 1
                continue
            else:
                break
""",
    ),
)
new(
    "r7_d2_dash_follower",
    within(
        "_follower",
        "if after >= len(text) or",
        'if text[after:after+1] == "-" or after >= len(text) or',
    ),
    selection="d2_follower",
)
new(
    "r7_d2_plain_dot_follower",
    within(
        "_follower",
        "if after >= len(text) or",
        'if text[after:after+1] == "." or after >= len(text) or',
    ),
    selection="d2_follower",
)
new(
    "r7_d3_disabled",
    within(
        "trim", """        if self.quote_token(text, start) is None:""", """        if False:"""
    ),
    selection="d3_exemption",
)
new(
    "r7_d3_strip_letters",
    within(
        "trim",
        """in "\\\"'`”’)]}>.,!?:;":""",
        """in "\\\"'`”’)]}>.,!?:;ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_":""",
    ),
)
new(
    "r7_d4_double_closes",
    within(
        "_owned_quotes",
        """            if valid and valid[pos] and not doubled:""",
        """            if valid and valid[pos]:""",
    ),
    selection="d4_quote",
)
new(
    "r7_d4_escaped_closes",
    within(
        "_owned_quotes",
        """            if char.startswith("\\\\") or""",
        """            if char.startswith("\\\\"):
                char = char[-1]
                pos = token_end - 1
            if char.startswith("\\\\") or""",
    ),
    selection="d4_quote",
)
new(
    "r7_d4_curly_unpaired",
    within("_owned_quotes", """close = {"“": "”", "‘": "’"}.get(char, char)""", """close = char"""),
    selection="d4_quote or typographic_quote",
)
new(
    "r7_d5_blind_unquote",
    within(
        "private",
        """        first, last = start, end""",
        """        from urllib.parse import unquote
        raw = text[start:end]
        for _ in range(3):
            raw = unquote(raw)
        return redaction_sensitive_value(key, raw)
        first, last = start, end""",
    ),
    selection="mixed_layer",
)
new(
    "r7_d5_userinfo_raw_exemption",
    within(
        "_userinfo_spans",
        """syntax.private(text, "password", colon + 1, at)""",
        """redaction_sensitive_value("password", text[colon+1:at])""",
    ),
    selection="mixed_layer",
)
new(
    "r7_d5_curl_username_exemption",
    within(
        "_curl_spans",
        """syntax.private(text, key, begin, finish)""",
        """syntax.private(text, key, start, end)""",
    ),
    selection="curl_exemption",
)
new(
    "r7_d6_literal_default_exempt",
    within(
        "_shell_reference",
        """        return re.compile(r"\\$[A-Za-z_][A-Za-z0-9_]*+").fullmatch(value, start, end) is not None""",
        """        return True""",
    ),
    selection="shell_literal",
)
new(
    "r7_d6_parameter_name_is_key",
    within(
        "shell_operator",
        '''return text[max(0, position - 2) : position] == "${"''',
        """return False""",
    ),
    selection="shell_reference",
)
new(
    "r7_d7_type_not_stripped",
    within(
        "_markup_spans",
        """html.unescape(text[begin:end]).strip(" \\t\\r\\n\\f").casefold()""",
        """html.unescape(text[begin:end]).casefold()""",
    ),
    selection="review_probes",
)
new(
    "r7_d7_backtick_not_quote",
    within("_markup_spans", """if quote in {'"', "'", "`"}:""", """if quote in {'"', "'"}:"""),
    selection="review_probes",
)
new(
    "r7_d7_malformed_ends_scan",
    within(
        "_markup_spans",
        """            if not closed or text[cursor - 2 : cursor] == "/>":
                continue""",
        """            if not closed:
                position = len(text)
                continue
            if text[cursor - 2 : cursor] == "/>":
                continue""",
    ),
    selection="review_probes or unquoted_nested_tag",
)
new(
    "r7_d7_comment_not_unit",
    within(
        "_markup_spans",
        """                position = min(upper, stop + 3)""",
        """                position = tag.end()""",
    ),
    selection="review_probes or comment_context",
)
new(
    "r7_d8_putty_closes_bracket",
    within(
        "_putty_spans",
        """        end = end_match.end() if end_match else limit""",
        """        end = end_match.end() if end_match else limit
        cut = text.find("]", match.end(), end)
        if cut >= 0:
            end = cut""",
    ),
    selection="putty",
)
new(
    "r7_d10_unanchored_short_curl",
    within("_curl_spans", """        if match["short"] and not curl:""", """        if False:"""),
    selection="benign_anchors",
)
new(
    "r7_d10_unanchored_sql",
    within(
        "_command_spans",
        """            if sql_range is None or not sql_range[0] <= match.start() < sql_range[1]:
                continue""",
        """            if False:
                continue""",
    ),
    selection="benign_anchors",
)
new(
    "r7_lock_sanitize_in_verify",
    within(
        "_associate",
        """user_echo(view, [observed.raw_event])""",
        """user_echo(_exchange_view(ctx, current, context=False), [observed.raw_event])""",
    ),
    tests="tests/test_b16a_round7_lock.py",
    selection="send_timeout",
    file=LIFE,
)
new(
    "r7_context_drops_family",
    within(
        "cases",
        """    pool = list(rows())""",
        """    pool = [r for r in rows() if r.family != "matrix:json:pct2"]""",
    ),
    tests=CTX,
    file="b16a_r7_context.py",
)


# Restorations target the current scanner, not obsolete line-pairing code.
def rewrite_all(function, old, replacement):
    def edit(source):
        import ast

        tree = ast.parse(source)
        node = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == function
        )
        lines = source.splitlines(True)
        body = "".join(lines[node.lineno - 1 : node.end_lineno])
        assert old in body, (function, old)
        lines[node.lineno - 1 : node.end_lineno] = [body.replace(old, replacement)]
        return "".join(lines)

    return edit


adapt("ending_drops_encoded_qmark", rewrite_all("_query_spans", 'char == "?"', "False"))
adapt(
    "ending_readds_literal_qmark",
    rewrite_all("_query_spans", 'char == "?"\n                and encoded', 'char == "?"'),
)
adapt(
    "value_not_possessive",
    rewrite_all("_Syntax", "(?<!\\\\)\\\\++[\\\"\\']", "(?<!\\\\)\\\\+[\\\"\\']"),
)
adapt(
    "chain_after_assignments",
    rewrite_all(
        "_view_spans",
        "            _cookie_spans(text, syntax),\n            _jar_spans(text, syntax),",
        "            _jar_spans(text, syntax),\n            _cookie_spans(text, syntax),",
    ),
)
adapt("jar_removed", rewrite_all("_view_spans", "            _jar_spans(text, syntax),\n", ""))
adapt(
    "drop_environment_exemption",
    rewrite_all("private", "return not self.complete(call.end() - 1, last)", "return True"),
)
adapt(
    "bare_bearer_removed",
    rewrite_all("_redact_once", "                _bearer_spans(text),", "                (),"),
)
adapt(
    "unicode_assignment_spacing_ignored",
    rewrite_all("_assignment_spans", "[\\s\\u200b]*+(?P<sep>", "[^\\S\\r\\n]*+(?P<sep>"),
)
adapt("xml_attributes_ignored", rewrite_all("_markup_spans", "(?=[\\s/>])", "(?=>)"))
adapt(
    "self_closing_xml_consumes_tail",
    rewrite_all(
        "_markup_spans", 'if not closed or text[cursor - 2 : cursor] == "/>":', "if not closed:"
    ),
)
adapt(
    "drop_nested_dollar_brace_exemption",
    rewrite_all(
        "private",
        "self.placeholder_complete(text, first, last) and _shell_reference(value)",
        "False",
    ),
)
adapt(
    "restart_inside_value_header",
    rewrite_all("_header_spans", "position = max(position, end)", "position = match.end()"),
)
adapt(
    "decoded_terminator_ends_raw_value",
    rewrite_all("value", "if self.depth(pos) > level:", "if False:"),
)
adapt(
    "bearer_decoded_view_only",
    rewrite_all("_redact_once", "                _bearer_spans(text),", "                (),"),
)
adapt(
    "quote_ends_unquoted_value",
    rewrite_all(
        "value",
        'if char in "\\"\'`[]{}()":',
        'if char in "\\"\'`[]{}()":\n                return pos',
    ),
)
adapt(
    "prefix_placeholder_exemption",
    rewrite_all(
        "private",
        "self.placeholder_complete(text, first, last) and _shell_reference(value)",
        'value.startswith("${")',
    ),
)
adapt(
    "header_newline_depth_ignored",
    rewrite_all("line_end", "if self.depth(position) <= level:", "if True:"),
)
adapt(
    "inner_quotes_skipped",
    rewrite_all(
        "_owned_quotes",
        "            level = self.depth(pos)\n",
        "            level = self.depth(pos)\n            if self.proven_end(pos) is not None:\n                continue\n",
    ),
)
adapt(
    "deeper_quoted_argument_refused",
    rewrite_all(
        "quoted_end",
        "        return self.owned_quote_ends.get(first)",
        "        return None if self.depth(start) else self.owned_quote_ends.get(first)",
    ),
)
adapt(
    "r6_value_opener_is_closer",
    rewrite_all(
        "quoted_end",
        "        return self.owned_quote_ends.get(first)",
        "        return None if self.quote_closings.get(start) is not None else self.owned_quote_ends.get(first)",
    ),
)
adapt(
    "r6_xml_skips_exempt",
    rewrite_all(
        "_markup_spans",
        "            if syntax.private(text, leaf, begin, finish):",
        "            position = end\n            if syntax.private(text, leaf, begin, finish):",
    ),
)
adapt("r6_autolink_end_removed", rewrite_all("_userinfo_spans", "end = autolink", "end = end"))
adapt(
    "r6_lookup_suffix_scan_restored",
    rewrite_all(
        "value",
        "            finish = self.ends.get(opening)\n            if finish is None or not self.complete(opening, finish):",
        '            self.private(text, "token", start, len(text))\n            finish = self.ends.get(opening)\n            if finish is None or not self.complete(opening, finish):',
    ),
)
adapt(
    "r6_html_name_rule_disabled",
    rewrite_all("_markup_spans", "private |= redaction_sensitive_key(value)", "private |= False"),
)
adapt(
    "r6_html_type_rule_disabled",
    rewrite_all("_markup_spans", 'password_type |= value == "password"', "password_type |= False"),
)
adapt(
    "r5_new_2a_deeper_close_quote_ends_shallower_value",
    rewrite_all(
        "quoted_end",
        "        return self.owned_quote_ends.get(first)",
        "        return self.quote_next.get(first)",
    ),
)
adapt(
    "r5_new_5a_putty_closing_boundary_narrowed",
    rewrite_all(
        "_putty_spans",
        "        end = end_match.end() if end_match else limit",
        '        end = end_match.end() if end_match else limit\n        close = re.search(r"[\\"\'\\]})]", text[match.end():end])\n        if close:\n            end = match.end()+close.start()',
    ),
)
adapt(
    "r5_new_6a_exempt_scan_stops_at_dollar_brace",
    rewrite_all(
        "_assignment_spans",
        "        position = match.end()",
        '        position = match.end()\n        if text.startswith("${", match.end()):\n            position = syntax.value(text, match.end())',
    ),
)
adapt(
    "r6_whole_url_question_layer_ignored",
    rewrite_all("_query_spans", "and syntax.depth(boundary.start()) > operator_depth", "and True"),
)
adapt(
    "r6_encoded_query_whitespace_terminates",
    rewrite_all(
        "_query_spans",
        "char.isspace() and not encoded and syntax.depth(boundary.start()) <= operator_depth",
        "char.isspace()",
    ),
)

adapt("value_not_possessive", rewrite_all("_Syntax", r"(?<!\\)\\++", r"(?<!\\)\\+"))
adapt(
    "har_lexed_on_canonical_data",
    rewrite_all("_redact_once", "_har_spans(text)", "_har_spans(view)"),
)
adapt(
    "r6_mask_prefix_quote_is_unproven_close",
    rewrite_all("value", "if token and limit == start:", "if token:"),
)
new(
    "mut_r6_01_pair_across_newline",
    rewrite_all("_owned_quotes", "if len(channel[0]) == 1 and channel[1] >= level:", "if False:"),
    tests=[STRUCT, "tests/test_b16a_round6_structure.py"],
)
new(
    "mut_r6_02_drop_contraction_rule",
    rewrite_all("_owned_quotes", 'char == "’"', "False"),
    tests=[STRUCT, "tests/test_b16a_round6_structure.py"],
)
new(
    "mut_r6_03_forced_close_as_match",
    rewrite_all("complete", "            and self.closings.get(end - 1) == opening\n", ""),
    tests=[STRUCT, "tests/test_b16a_round6_structure.py"],
)
new(
    "mut_r6_04a_scanner_header_skip_exempt",
    rewrite_all(
        "_header_spans",
        '        if authorized and syntax.private(text, match["key"], start, end):',
        '        position = max(position, end)\n        if authorized and syntax.private(text, match["key"], start, end):',
    ),
    tests=[STRUCT, "tests/test_b16a_round6_structure.py"],
)
new(
    "mut_r6_04b_scanner_query_skip_exempt",
    rewrite_all(
        "_query_spans",
        "        end = syntax.trim(text, start, end)",
        "        position = max(position, end)\n        end = syntax.trim(text, start, end)",
    ),
    tests=[STRUCT, "tests/test_b16a_round6_structure.py"],
)
new(
    "mut_r6_04c_scanner_curl_skip_exempt",
    rewrite_all(
        "_curl_spans",
        "        if syntax.private(text, key, begin, finish):",
        "        position = max(position, end)\n        if syntax.private(text, key, begin, finish):",
    ),
    tests=[STRUCT, "tests/test_b16a_round6_structure.py"],
)
new(
    "mut_r6_04d_scanner_command_skip_exempt",
    rewrite_all(
        "_command_spans",
        '        if syntax.private(text, "password", start, end):',
        '        position = max(position, end)\n        if syntax.private(text, "password", start, end):',
    ),
    tests=[STRUCT, "tests/test_b16a_round6_structure.py"],
)
new(
    "mut_r6_05_drop_triple_quote",
    rewrite_all("quote_token", "('\\\"\\\"\\\"', \"'''\",", "("),
    tests=[STRUCT, "tests/test_b16a_round6_structure.py"],
)
new(
    "mut_r6_06_har_repeated_name_overwritten",
    rewrite_all(
        "_har_json_spans",
        'frame.get("private", False) or redaction_sensitive_key(',
        "redaction_sensitive_key(",
    ),
    tests=[STRUCT, "tests/test_b16a_round6_structure.py"],
)
new(
    "mut_r6_07a_html_order_name_before_value",
    rewrite_all(
        "_markup_spans",
        'elif key in {"value", "content"}:',
        'elif key in {"value", "content"} and (private or password_type):',
    ),
    tests=[STRUCT, "tests/test_b16a_round6_structure.py"],
)
new(
    "mut_r6_07b_html_order_value_before_name",
    rewrite_all(
        "_markup_spans",
        'elif key in {"value", "content"}:',
        'elif key in {"value", "content"} and not (private or password_type):',
    ),
    tests=[STRUCT, "tests/test_b16a_round6_structure.py"],
)
new(
    "mut_r6_08_drop_protocol_relative_url",
    rewrite_all("_userinfo_spans", "|(?<![:/])//", ""),
    tests=[STRUCT, "tests/test_b16a_round6_structure.py"],
)
new(
    "mut_r6_09_drop_autolink_gt",
    rewrite_all("_userinfo_spans", "end = autolink", "end = end"),
    tests=[STRUCT, "tests/test_b16a_round6_structure.py"],
)
new(
    "mut_r6_10_special_scheme_backslash_loses",
    rewrite_all("_userinfo_spans", "end = min(end, raw_ends.first(position))", "end = end"),
    tests=[STRUCT, "tests/test_b16a_round6_structure.py"],
)
new(
    "mut_r6_11_context_drop_161_of_168_families",
    rewrite_all(
        "cases",
        "    pool = list(rows())",
        "    pool = [r for r in rows() if r.family.startswith('matrix:json:')]",
    ),
    tests=CTX,
    file="b16a_r7_context.py",
)
new(
    "r7_d1_line_pairing_extent",
    rewrite_all(
        "owned_end",
        "        limit = self.proven_end(start)",
        "        limit = self.enclosing.get(start)",
    ),
)
new(
    "r7_d3_trim_own_quote",
    rewrite_all("trim", "if self.quote_token(text, start) is None:", "if True:"),
)
new(
    "r7_d6_operand_not_token",
    rewrite_all(
        "_curl_spans",
        '        if match["short"] and not curl:',
        '        if match["short"] and (not curl or text[max(0, match.start()-2):match.start()] in {":-", "-"}):',
    ),
)
new(
    "r7_d7_unquoted_includes_slash",
    rewrite_all("_markup_spans", "(?:[^\\s<>/]|/(?!>))*+", "[^\\s<>]*+"),
)
new("r7_d8_yaml_digits_rejected", rewrite_all("_Syntax", "(?:[1-9]?[-+]?|[-+]?[1-9]?)", "[-+]?"))
new("r7_d8_yaml_comment_rejected", rewrite_all("_Syntax", "(?=#|$)", "(?=$)"))
new(
    "r7_d9_quote_ends_form",
    rewrite_all("_form_ranges", '_NAME + r"=[^\\s]++"', '_NAME + r"=[^\\s\\"\\\']++"'),
)
new(
    "r7_d11_xml_suffix_restart",
    rewrite_all(
        "_markup_spans",
        "            cursor = tag.end()",
        '            text.find(">", tag.end(), upper)\n            cursor = tag.end()',
    ),
    tests=CPU,
    selection="xml_unclosed_before_exempt and linear",
)
new(
    "r7_d11_attribute_suffix_restart",
    rewrite_all(
        "attribute_end",
        "        return self.attribute_quotes.get(start)",
        '        text.find("\\0", start)\n        return self.attribute_quotes.get(start)',
    ),
    tests=CPU,
    selection="html_quoted_subtags and linear",
)
new(
    "r7_d11_fold_suffix_restart",
    rewrite_all(
        "_header_spans",
        "        start = match.end()",
        '        text.find("\\0", match.end())\n        start = match.end()',
    ),
    tests=CPU,
    selection="folded_header and linear",
)

adapt("mut_r6_05_drop_triple_quote", rewrite_all("quote_token", "'\"\"\"', \"'''\", ", ""))
adapt(
    "mut_r6_02_drop_contraction_rule",
    rewrite_all("_Syntax", '                    char == "\'"\n', "                    False\n"),
)
adapt(
    "r7_d1_line_pairing_extent",
    rewrite_all(
        "owned_end",
        "        limit = self.proven_end(start)",
        "        limit = min((b for a,b in self.quotes if a < start < b), default=self.proven_end(start))",
    ),
)
adapt(
    "r5_new_2a_deeper_close_quote_ends_shallower_value",
    rewrite_all(
        "quoted_end",
        "        return self.owned_quote_ends.get(first)",
        "        deeper = text.find(quote, start + width)\n        if deeper >= 0 and self.depth(deeper) > self.depth(start):\n            return deeper + len(quote)\n        return self.owned_quote_ends.get(first)",
    ),
)

adapt(
    "ending_readds_literal_qmark",
    rewrite_all(
        "_query_spans",
        'char == "?"\n                and encoded\n                and syntax.depth(boundary.start()) > operator_depth',
        'char == "?"',
    ),
)
adapt(
    "r7_d11_xml_suffix_restart",
    rewrite_all(
        "_markup_spans",
        "            cursor = tag.end()",
        '            probe = tag.end()\n            while probe < upper and text[probe] != ">":\n                probe += 1\n            cursor = tag.end()',
    ),
)
adapt(
    "r7_d11_attribute_suffix_restart",
    rewrite_all(
        "_markup_spans",
        "            cursor = tag.end()",
        '            probe = tag.end()\n            while probe < len(text) and text[probe] != ">":\n                probe += 1\n            cursor = tag.end()',
    ),
)
adapt(
    "r7_d11_fold_suffix_restart",
    rewrite_all(
        "_header_spans",
        "        start = match.end()",
        '        probe = match.end()\n        while probe < len(text):\n            stop = text.find("\\n", probe)\n            if stop < 0 or text[stop+1:stop+2] not in {" ", "\\t"}:\n                break\n            probe = stop + 1\n        start = match.end()',
    ),
)


adapt(
    "bare_bearer_removed",
    base.MUTANTS["bare_bearer_removed"][1],
    tests="tests/test_b16a_round4_form.py",
    selection="bearer_question_mark_seeded_rates",
)
adapt(
    "prefix_placeholder_exemption",
    base.MUTANTS["prefix_placeholder_exemption"][1],
    tests=STRUCT,
    selection="shell_literal",
)
adapt(
    "sentinel_not_opaque_bearer",
    base.MUTANTS["sentinel_not_opaque_bearer"][1],
    tests=STRUCT,
    selection="sentinel_prefix",
)
adapt(
    "r6_mask_prefix_quote_is_unproven_close",
    base.MUTANTS["r6_mask_prefix_quote_is_unproven_close"][1],
    tests=STRUCT,
    selection="unclosed_value_quote",
)
adapt(
    "r6_lookup_suffix_scan_restored",
    rewrite_all(
        "value",
        "            finish = self.ends.get(opening)\n            if finish is None or not self.complete(opening, finish):",
        '            _credential_value_exempt("token", text[start:])\n            finish = self.ends.get(opening)\n            if finish is None or not self.complete(opening, finish):',
    ),
)


adapt(
    "fallback_uppercase_removed",
    base.MUTANTS["fallback_uppercase_removed"][1],
    tests="tests/test_b16a_round3.py",
    selection="safe_value",
)
adapt(
    "r7_d4_escaped_closes",
    base.MUTANTS["r7_d4_escaped_closes"][1],
    tests=STRUCT,
    selection="backslash_quote_is_data",
)
adapt(
    "header_newline_depth_ignored",
    base.MUTANTS["header_newline_depth_ignored"][1],
    tests=STRUCT,
    selection="deeper_newline",
)
adapt(
    "r5_new_2a_deeper_close_quote_ends_shallower_value",
    base.MUTANTS["r5_new_2a_deeper_close_quote_ends_shallower_value"][1],
    tests=STRUCT,
    selection="deeper_quote",
)
# These guards are implied by the ownership index or conservative tail closure.
# The runner still requires an identical 100,372-input differential digest.
for equivalent_name in (
    "bare_bearer_removed",
    "container_closer_depth_ignored",
    "r6_autolink_end_removed",
    "r6_quoted_assignment_overlap_ignored",
):
    file, edit, tests, selection, _ = base.MUTANTS[equivalent_name]
    add(equivalent_name, file, edit, tests, selection, True)


adapt(
    "header_newline_depth_ignored",
    rewrite_all("line_end", "self.depth(position) <= level", "True"),
    tests=STRUCT,
    selection="deeper_newline",
)
adapt(
    "r7_d3_trim_own_quote",
    within(
        "value",
        "                return end\n",
        "                return self.trim(text, start+len(token), end)\n",
    ),
    tests=STRUCT,
    selection="d3_exemption",
)
adapt(
    "r7_d6_operand_not_token",
    within("_assignment_spans", r"(?<![A-Za-z0-9_.])", r"(?<![A-Za-z0-9_.-])"),
    tests=STRUCT,
    selection="shell_operands",
)
new(
    "r7_d4_shell_idiom_closes",
    within(
        "_owned_quotes",
        '            if char.startswith("\\\\") or',
        '            if char in {"\'\\"\'\\"\'", "\'\\\\\'\'"}:\n                char = "\'"\n                token_end = pos + 1\n            if char.startswith("\\\\") or',
    ),
    selection="shell_idiom_space",
)
new(
    "r7_d5_mixed_layer_placeholder_exempt",
    within(
        "private",
        "if first < last and self.depth(first) != self.depth(last - 1):\n                return True",
        "if first < last and self.depth(first) != self.depth(last - 1):\n                return False",
    ),
    selection="mixed_layer",
)
new(
    "r7_d11_private_context_reentered",
    within("_markup_spans", "        if output.get(lower, 0) >= upper:", "        if False:"),
    tests=CPU,
    selection="markup_nested_private and linear",
)

if __name__ == "__main__":
    from b16a_r6_mutation_run import main

    main()
