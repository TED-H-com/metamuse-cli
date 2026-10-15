"""Serial offline mutation proof, adapting prior operators without weakening them."""

import b16a_r3_mutations as base
from b16a_r3_mutations import CONTENT, LIFE, R3, RUN, SAFE, add, exact, within

R4 = "tests/test_b16a_round4.py"
FORM = "tests/test_b16a_round4_form.py"
EXTENTS = "tests/test_b16a_round4_extents.py"
SYNTAX = "tests/test_b16a_round4_syntax.py"
COOKIE = "tests/test_b16a_round4_cookie.py"
KEYS = "tests/test_b16a_round4_keys.py"
RECONCILE = "tests/test_b16a_round4_reconcile.py"


def set_edit(name, file, edit, tests, selection="", equivalent=False):
    add(name, file, edit, tests, selection, equivalent)


def raw_bearer_backtracking(text):
    anchor = "    if not spans:\n        return text, []"
    addition = r"""    legacy = []
    for match in re.finditer(r"(?i)bearer(?:\s|%(?:25){0,2}20)+(?P<token>[A-Za-z0-9._~+/%=-]{8,}+)", text):  # noqa: E501 - exact mutation anchor
        legacy.append((*match.span("token"), "bearer_token", _REDACTED))
    spans = list(_merge_spans([spans, legacy]))
"""  # noqa: E501 - exact mutation anchor
    return within("_redact_once", anchor, addition + anchor)(text)


set_edit(
    "bearer_space_not_possessive", SAFE, raw_bearer_backtracking, R3, "bearer_separator_boundary"
)
set_edit(
    "xml_removed",
    SAFE,
    exact("            _xml_spans(text, syntax),\n", ""),
    R3,
    "encodings_and_syntax",
)
set_edit(
    "drop_code_reference_exemption",
    SAFE,
    within(
        "_credential_value_exempt",
        'if reference.rsplit(".", 1)[-1].lower().replace("_", "") == leaf or reference.startswith(',
        'if False and reference.rsplit(".", 1)[-1].lower().replace("_", "") == leaf or reference.startswith(',  # noqa: E501 - exact mutation anchor
    ),
    R3,
    "closed_exemptions",
)
set_edit(
    "drop_type_exemption",
    SAFE,
    within(
        "_credential_value_exempt",
        "    if _type_reference(reference):",
        "    if False and _type_reference(reference):",
    ),
    R3,
    "closed_exemptions or closed_type",
)
set_edit(
    "form_plus_ignored",
    SAFE,
    within(
        "_redact_once",
        "view, edits = _decode_view(text, form=True, ranges=ranges)",
        "view, edits = text, []",
    ),
    FORM,
    "whole_text_form",
)
set_edit(
    "packet_fixpoint_backstop_removed",
    CONTENT,
    within(
        "_require_stable_redaction",
        'if any(row["kind"] == "redaction_unstable" for row in findings):',
        "if False:",
    ),
    RUN,
    "packet_fixpoint_backstop",
)
set_edit(
    "detail_truncated_before_redacting",
    LIFE,
    exact(
        "safe_detail = redact_display(attempt.evidence.detail)[:512]",
        "safe_detail = redact_display(attempt.evidence.detail[:512])",
    ),
    RUN,
    "prefix_512",
)
set_edit(
    "detail_redacted_inside_batch",
    LIFE,
    exact(
        "    safe_detail = redact_display(attempt.evidence.detail)[:512]\n\n    def save(writer):",
        "    def save(writer):\n        safe_detail = redact_display(attempt.evidence.detail)[:512]",  # noqa: E501 - exact mutation anchor
    ),
    RUN,
    "remote_detail",
)
set_edit(
    "unicode_assignment_spacing_ignored",
    SAFE,
    within(
        "_assignment_spans",
        r"(?:\s*+(?P<sep>=>|=|:(?!=)|[：＝])\s*+|(?P<flagspace>\s++))",
        r"(?:[ \t]*+(?P<sep>=>|=|:(?!=)|[：＝])[ \t]*+|(?P<flagspace>[ \t]++))",
    ),
    R3,
    "differential",
)
set_edit(
    "userinfo_authority_boundary_removed",
    SAFE,
    within("_userinfo_spans", "        end = ends[key].first(position)", "        end = len(text)"),
    R3,
    "authority_ends",
)
set_edit(
    "annotation_default_skipped",
    SAFE,
    within(
        "_assignment_spans",
        'if equals >= 0 and _type_reference(text[start:equals].strip().strip("\\"\'")):',
        "if False:",
    ),
    R3,
    "annotated_assignment_default",
)
set_edit(
    "bare_bearer_removed",
    SAFE,
    lambda text: exact("_bearer_spans(text)]", "iter(())]")(
        exact("            _bearer_spans(text),\n", "")(text)
    ),
    R3,
    "handoff",
)


set_edit(
    "toml_triple_quote_ends_removed",
    SAFE,
    exact("                self.ends[triple[1]] = match.end()\n", ""),
    R4,
    "triple_quoted_value",
)

set_edit(
    "pem_removed",
    SAFE,
    exact("            _pem_spans(text, syntax),\n", ""),
    R3,
    "well_known_prefixes",
)

file, edit, *_ = base.MUTANTS["bearer_closure_removed"]
set_edit("bearer_closure_removed", file, edit, R4, "bearer_closure_covers")

# The reviewer operators, including the dead-branch equivalent removed at head.
set_edit(
    "drop_dollar_var_exemption",
    SAFE,
    exact(r"|\$[A-Za-z_][A-Za-z0-9_]*+|%[A-Za-z_]", r"|%[A-Za-z_]"),
    R3,
    "closed_exemptions",
)
set_edit(
    "drop_nested_dollar_brace_exemption",
    SAFE,
    within(
        "_credential_value_exempt",
        'if value.startswith("${") and value.endswith("}"):',
        "if False:",
    ),
    "tests/test_b16a_round4_placeholders.py",
    "whole_placeholder",
)
for name, gate in [
    ("value_shape_length_gate", "len(value) < 8"),
    ("value_shape_space_gate", '" " in value'),
]:
    set_edit(
        name,
        SAFE,
        within(
            "_credential_value_exempt",
            '    if value in {_REDACTED, "REDACTED", "...", "…", "%s", "{}"}:',
            "    if "
            + gate
            + ':\n        return True\n    if value in {_REDACTED, "REDACTED", "...", "…", "%s", "{}"}:',  # noqa: E501 - exact mutation anchor
        ),
        R4,
        "generated_password_rates or leak_matrix or short_values or prose_values",
    )
set_edit(
    "sentinel_not_opaque_query",
    SAFE,
    within(
        "_query_spans",
        "cursor = start + len(_REDACTED) if text.startswith(_REDACTED, start) else start",
        "cursor = start",
    ),
    R4,
    "leak_matrix or handoff or query_sentinel",
)
set_edit(
    "truncate_at_600_before_redacting",
    LIFE,
    exact(
        "safe_detail = redact_display(attempt.evidence.detail)[:512]",
        "safe_detail = redact_display(attempt.evidence.detail[:600])[:512]",
    ),
    RUN,
    "remote_detail",
)
for name, function, selection, tests in [
    ("restart_inside_value_cookie", "_cookie_spans", "defined_chains", COOKIE),
    ("restart_inside_value_header", "_header_spans", "header_consumes", KEYS),
]:
    set_edit(
        name,
        SAFE,
        within(
            function, "        position = max(end, match.end())", "        position = match.end()"
        ),
        tests,
        selection,
    )
for kind in ["percent", "json_escape"]:
    set_edit(
        kind + "_two_layers",
        SAFE,
        exact(
            "budgets = {False: 3, True: 3}",
            "budgets = {False: 2, True: 3}"
            if kind == "percent"
            else "budgets = {False: 3, True: 2}",
        ),
        KEYS if kind == "json_escape" else R3,
        "three_json" if kind == "json_escape" else "handoff",
    )
for name, pattern in [
    ("google", "AIza[A-Za-z0-9_-]{35}|"),
    ("slack_xoxa2", "|xoxa-2-(?:[0-9]{10,13}-){1,3}[A-Za-z0-9]{24,64}"),
    ("npm", "npm_[A-Za-z0-9]{36}|"),
]:
    set_edit(
        "drop_prefix_" + name,
        SAFE,
        exact(pattern, ""),
        KEYS if name == "slack_xoxa2" else R3,
        "slack_documented" if name == "slack_xoxa2" else "well_known_prefixes",
    )
for name, bound in [("backstop_two_passes", 2), ("backstop_nine_passes", 9)]:
    set_edit(
        name,
        SAFE,
        within("redact_text", "range(4)", f"range({bound})"),
        R4,
        "exact_internal_application_bound",
    )
set_edit(
    "generic_chain_removed",
    SAFE,
    within(
        "_cookie_spans",
        "if members > 1 and (sensitive or generic):",
        "if members > 1 and sensitive:",
    ),
    COOKIE,
    "defined_chains",
)

# New operators for every binding item 1–11.
set_edit(
    "decoded_terminator_ends_raw_value",
    SAFE,
    within("value", "if end < len(text) and self.depth(end) > level:", "if False:"),
    R4,
    "operator_layer_relative",
)
set_edit(
    "drop_raw_percent_spans",
    SAFE,
    within(
        "_redact_once",
        "original_fields = _assignment_spans(text, original_syntax) if layers else ()",
        'original_fields = (span for span in _assignment_spans(text, original_syntax) if "%" not in text[span[0]:span[1]]) if layers else ()',  # noqa: E501 - exact mutation anchor
    ),
    R4,
    "raw_spans_are_independent",
)
set_edit(
    "form_from_first_question_mark",
    SAFE,
    within(
        "_form_ranges",
        "    form = re.compile(",
        '    if "?" in text:\n        yield text.index("?") + 1, len(text)\n        return\n    form = re.compile(',  # noqa: E501 - exact mutation anchor
    ),
    FORM,
    "question_mark_seeded or question_mark_does_not_enable",
)
set_edit(
    "bearer_decoded_view_only",
    SAFE,
    exact("original_headers, _bearer_spans(text)", "original_headers"),
    R4,
    "raw_spans_are_independent",
)
set_edit(
    "three_internal_applications",
    SAFE,
    within("redact_text", "range(4)", "range(3)"),
    R4,
    "exact_internal_application_bound",
)
set_edit(
    "unstable_bundle_ignored",
    CONTENT,
    within("_safe_value", "        _require_stable_redaction(redactions)\n", ""),
    R4,
    "unstable_bundle",
)
set_edit(
    "quote_ends_unquoted_value",
    SAFE,
    within(
        "value",
        "if end >= len(text) or text[end].isspace():",
        'if end >= len(text) or text[end].isspace() or text[end] in "\\"\'":',
    ),
    EXTENTS,
    "syntax_and_extent",
)
set_edit(
    "userinfo_first_at_only",
    SAFE,
    within("_userinfo_spans", "while ats.first(at + 1) < end:", "while False:"),
    EXTENTS,
    "last_at",
)
set_edit(
    "drop_api_key_suffix",
    SAFE,
    exact('                "apikey",\n', ""),
    SYNTAX,
    "names_and_operators",
)
set_edit(
    "reconcile_raw_topic_only",
    LIFE,
    within("_check_reconciliation_topic", "        redact_display(discussion.topic),\n", ""),
    RECONCILE,
    "via_saved_request",
)
set_edit(
    "comma_chain_outside_context",
    SAFE,
    within("_cookie_spans", r'r";[^\S\r\n]*+(?="', r'r"[;,][^\S\r\n]*+(?="'),
    COOKIE,
    "benign_calls or generic_pairs",
)
set_edit(
    "prefix_placeholder_exemption",
    SAFE,
    within(
        "_credential_value_exempt",
        '    if value.startswith("${") and value.endswith("}"):',
        '    if value.startswith("${") and value.endswith("}"):\n        return True\n    if value.startswith("${") and value.endswith("}"):',  # noqa: E501 - exact mutation anchor
    ),
    "tests/test_b16a_round4_placeholders.py",
    "lookalike",
)
set_edit(
    "sk_capped_at_200",
    SAFE,
    exact("sk-(?:ant-)?[A-Za-z0-9_-]{8,}+", "sk-(?:ant-)?[A-Za-z0-9_-]{8,200}+"),
    KEYS,
    "unbounded_sk",
)
set_edit(
    "stale_send_generic_next_action",
    LIFE,
    within(
        "prepare_send",
        '            "Prepare a new packet and send request with a new exact share grant",\n',
        "",
    ),
    "tests/test_b16a_round4_guidance.py",
)
set_edit(
    "revoke_without_workspace",
    SAFE,
    within("list_auth", "    if workspace is not None:", "    if False:"),
    "tests/test_b16a_round4_auth.py",
)


set_edit(
    "sentinel_not_opaque_bearer",
    SAFE,
    within(
        "_bearer_spans",
        '        token = match["token"]\n',
        '        token = match["token"]\n        if token == _REDACTED:\n            continue\n',
    ),
    R3,
    "differential",
    True,
)


# The shared internal fixpoint can make former closure passes observationally
# redundant; compare full output, findings and second call before claiming so.
for name in ("assignment_closure_removed", "sentinel_not_opaque_query"):
    file, edit, tests, selection, _ = base.MUTANTS[name]
    set_edit(name, file, edit, tests, selection, True)

set_edit(
    "header_newline_depth_ignored",
    SAFE,
    within(
        "_header_spans",
        "while boundary and syntax.depth(boundary.start()) > operator_depth:",
        "while False:",
    ),
    R4,
    "nested_header_newline_layer",
)
set_edit(
    "container_closer_depth_ignored",
    SAFE,
    exact("                and self.depth(pos) <= self.depth(stack[-1][1])\n", ""),
    R4,
    "container_closer",
)
set_edit(
    "mysql_suppresses_command_stream",
    SAFE,
    within(
        "_command_spans",
        "            mysql = True\n            mysql_depth = syntax.depth(match.start())\n"
        "            continue",
        "            mysql = True\n            mysql_depth = syntax.depth(match.start())\n"
        "            position = len(text)\n            continue",
    ),
    SYNTAX,
    "command_stream",
)
set_edit(
    "url_fragment_enables_form_plus",
    SAFE,
    within(
        "queries",
        '            fragment = text.find("#", match.start(), match.end())',
        "            fragment = -1",
    ),
    FORM,
    "url_fragment",
)
set_edit(
    "numeric_generic_cookie_exemption",
    SAFE,
    lambda text: within(
        "_cookie_spans",
        "        generic = not _credential_value_exempt(",
        "        generic = not text[match.end():end].isnumeric() and not _credential_value_exempt(",
    )(
        within(
            "_cookie_spans",
            "            generic |= not _credential_value_exempt(",
            "            generic |= not text[next_pair.end():finish].isnumeric() and not "
            "_credential_value_exempt(",
        )(text)
    ),
    COOKIE,
    "numeric_generic_cookie",
)

set_edit(
    "canonical_form_scope_ignored",
    SAFE,
    within("_redact_once", 'if layers and "+" in view:', "if False:"),
    FORM,
    "form_scope_revealed",
)
set_edit(
    "consumed_nested_assignment_not_extended",
    SAFE,
    within("_assignment_spans", "            nested\n", "            False and nested\n"),
    R4,
    "consumed_assignment_covers_nested",
)

if __name__ == "__main__":
    base.main()
