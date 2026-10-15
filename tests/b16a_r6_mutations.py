# ruff: noqa: E501
"""Round-6 semantic adaptations of every retained mutation, plus structural defects."""

import b16a_r5_mutations as prior
from b16a_r3_mutations import LIFE, SAFE, add, exact, within

base = prior.base
STRUCT = "tests/test_b16a_round6_structure.py"
CTX = "tests/test_b16a_round6_context.py"
CPU = "tests/test_b16a_round6_complexity.py"
HAR = "tests/test_b16a_round5_har.py"


def adapt(name, edit, tests=None, selection=None, equivalent=None):
    file, _, oldtests, oldselection, oldeq = base.MUTANTS[name]
    add(
        name,
        file,
        edit,
        tests or oldtests,
        oldselection if selection is None else selection,
        oldeq if equivalent is None else equivalent,
    )


adapt(
    "drop_environment_exemption",
    lambda s: exact("    if _complete_lookup(value):", "    if False and _complete_lookup(value):")(
        s
    ),
    STRUCT,
    "complete_exemption",
)
adapt("userinfo_https_only", within("_userinfo_spans", "[A-Za-z][A-Za-z0-9+.-]*+://", "https://"))
adapt("toml_triple_quote_ends_removed", exact("\"{3}|'{3}|`{3}|", ""), STRUCT, "multiline_tokens")
adapt(
    "container_closer_depth_ignored",
    exact("                and self.depth(pos) == self.depth(stack[-1])\n", ""),
)
adapt(
    "numeric_generic_cookie_exemption",
    lambda s: within(
        "_cookie_spans",
        '        generic = syntax.private(text, "password", match.end(), end)',
        '        generic = not text[match.end():end].isnumeric() and syntax.private(text, "password", match.end(), end)',
    )(
        within(
            "_cookie_spans",
            '            generic |= syntax.private(text, "password", next_pair.end(), finish)',
            '            generic |= not text[next_pair.end():finish].isnumeric() and syntax.private(text, "password", next_pair.end(), finish)',
        )(s)
    ),
)
adapt(
    "deeper_quoted_argument_refused",
    within(
        "quoted_end",
        "return self.quote_next.get(start) if",
        "return self.quote_next.get(start) if self.depth(start) == 0 and",
    ),
)
adapt(
    "har_resynchronizes_after_error",
    within(
        "_har_json_spans",
        "            if token is None:\n                break",
        "            if token is None:\n                position += 1\n                continue",
    ),
    HAR,
    "no_resynchronization",
)
adapt(
    "exempt_value_skip_restored",
    within(
        "_assignment_spans",
        "        if include_exempt or syntax.private(text, key, start, end):\n            position = max(position, end - int(closed_quote))",
        "        position = max(position, end - int(closed_quote))\n        if include_exempt or syntax.private(text, key, start, end):",
    ),
    STRUCT,
    "every_scanner",
)
adapt(
    "har_form_scope_ignored",
    within("_har_candidates", "if _form_depth < 3 and form.search(text):", "if False:"),
)
adapt(
    "har_non_json_whitespace_allowed",
    within(
        "_har_json_spans",
        'whitespace = re.compile(r"[ \\t\\r\\n]*+")',
        'whitespace = re.compile(r"\\s*+")',
    ),
)
adapt(
    "har_percent_layer_consumes_form_budget",
    within(
        "_har_candidates",
        "_depth=_depth, _form_depth=_form_depth + 1, _quotes=_quotes",
        "_depth=_depth + 1, _form_depth=_form_depth + 1, _quotes=_quotes",
    ),
)


def new(name, edit, tests=STRUCT, selection=""):
    add(name, SAFE, edit, tests, selection)


new(
    "r6_pair_across_line",
    lambda s: s.replace("if len(channel[0]) == 1 and channel[1] >= level:", "if False:"),
    selection="pairing",
)
new(
    "r6_apostrophe_delimiter",
    exact('                    char == "\'"\n', "                    False\n"),
    selection="pairing",
)
new("r6_triple_is_three_quotes", exact("\"{3}|'{3}|`{3}|", ""), selection="multiline_tokens")
new(
    "r6_value_opener_is_closer",
    within(
        "quoted_end",
        "return self.quote_next.get(start) if",
        "return self.quote_next.get(start) if start not in self.quote_closings and",
    ),
    selection="value_start",
)
new(
    "r6_forced_close_is_genuine",
    within("complete", "            and self.closings.get(end - 1) == opening\n", ""),
    selection="unclosed_exemption",
)
new(
    "r6_quoted_call_paren_closes",
    exact(
        "                        cursor = closing\n", "                        cursor = pos + 1\n"
    ),
    selection="complete_exemption",
)
new(
    "r6_header_skips_exempt",
    within(
        "_header_spans",
        '        if authorized and syntax.private(text, match["key"], start, end):',
        '        position = max(end, match.end())\n        if authorized and syntax.private(text, match["key"], start, end):',
    ),
    selection="every_scanner",
)
new(
    "r6_xml_skips_exempt",
    within(
        "_xml_spans",
        '        if syntax.private(text, key.rsplit(":", 1)[-1], private_start, private_end):',
        '        position = ends[index].end() if index < len(ends) else end\n        if syntax.private(text, key.rsplit(":", 1)[-1], private_start, private_end):',
    ),
    selection="every_scanner",
)
new(
    "r6_har_document_start_only",
    within(
        "_har_json_spans",
        '    candidates = re.finditer(r"[\\[{]", text)',
        '    candidates = re.finditer(r"^[\\[{]", text)',
    ),
    selection="har_anywhere",
)
new(
    "r6_har_repeated_name_overwrites",
    exact('frame.get("private", False) or redaction_sensitive_key(', "redaction_sensitive_key("),
    selection="har_anywhere",
)
new(
    "r6_har_swallowed_candidate_lost",
    within(
        "_har_json_spans",
        "        for frame in frames:",
        "        covered = max(covered, position)\n        for frame in frames:",
    ),
    selection="har_anywhere",
)
new(
    "r6_relative_urls_disabled",
    within("_userinfo_spans", "|(?<![:/])//", ""),
    selection="protocol_relative",
)
new(
    "r6_autolink_end_removed",
    within("_userinfo_spans", '                or boundary[0] == ">"', "                or False"),
    selection="url_benign",
)
new(
    "r6_raw_special_barrier_ignored",
    within(
        "_userinfo_spans",
        "            end = min(end, raw_ends.first(position))",
        "            end = end",
    ),
    selection="url_benign",
)
new("r6_block_plus_rejected", lambda s: s.replace("[ +]", " "), selection="key_space")
new(
    "r6_lookup_suffix_scan_restored",
    within(
        "value",
        "            finish = self.ends.get(call.end() - 1, len(text))",
        "            _complete_lookup(text[start:])\n            finish = self.ends.get(call.end() - 1, len(text))",
    ),
    CPU,
    "unclosed_getenv",
)
new(
    "r6_html_name_rule_disabled",
    within(
        "_html_spans",
        "private |= end - begin > 1024 or redaction_sensitive_key(html.unescape(text[begin:end]))",
        "private |= False",
    ),
    selection="html_private",
)
new(
    "r6_html_type_rule_disabled",
    within(
        "_html_spans",
        'password_type |= end - begin > 1024 or html.unescape(text[begin:end]).lower() == "password"',
        "password_type |= False",
    ),
    selection="html_private",
)
add(
    "r6_context_family_dropped",
    "b16a_r6_context.py",
    exact('    ("contraction", "Here\'s what I ran: {x}"),\n', ""),
    CTX,
    "subset_covers",
)


# Ported from the supplied reviewer evidence; edits are adapted below.
R5_ALL_UNIT = [
    "tests/test_b16a_round5_urls.py",
    "tests/test_b16a_round5_quotes.py",
    "tests/test_b16a_round5_har.py",
    "tests/test_b16a_round5_blocks.py",
    "tests/test_b16a_round5_exempt.py",
    "tests/test_b16a_round5_corpus.py",
]
review_specs = [
    (
        "r5_new_1_scheme_stream_skips_one",
        SAFE,
        within(
            "_userinfo_spans", "        match = following", "        match = next(candidates, None)"
        ),
        R5_ALL_UNIT,
        "",
    ),
    (
        "r5_new_2a_deeper_close_quote_ends_shallower_value",
        SAFE,
        within(
            "value",
            'if end < len(text) and text[end] in "\\"\'" and self.quote_closings.get(end, end) < start:',
            'if end < len(text) and text[end] in "\\"\'" and end in self.quote_closings:',
        ),
        R5_ALL_UNIT,
        "",
    ),
    (
        "r5_new_2b_deeper_close_quote_closes_shallower_pending",
        SAFE,
        exact(
            "                        if inner > opening and channel[1] >= level:",
            "                        if inner > opening:",
        ),
        R5_ALL_UNIT,
        "",
    ),
    (
        "r5_new_3a_har_lexer_resync_on_token_error",
        SAFE,
        within(
            "_har_spans",
            "        if token is None:\n            break  # Never search for a later plausible object after an error.",
            "        if token is None:\n            position += 1\n            continue",
        ),
        R5_ALL_UNIT,
        "",
    ),
    (
        "r5_new_3b_har_lexer_resync_on_state_error",
        SAFE,
        within(
            "_har_spans",
            '        if state not in {"value", "first_value"} or kind in ":,"'
            + ":\n            break",
            '        if state not in {"value", "first_value"} or kind in ":,"'
            + ":\n            position = end\n            continue",
        ),
        R5_ALL_UNIT,
        "",
    ),
    (
        "r5_new_4a_topic_helper_bypassed_in_check",
        LIFE,
        within(
            "_check_reconciliation_topic",
            "not _reconciliation_topic_matches(\n        discussion, request\n    )",
            "discussion.topic != request.topic",
        ),
        "tests/test_b16a_round5_reconcile.py",
        "app and credential",
    ),
    (
        "r5_new_4b_topic_helper_bypassed_in_receipt",
        LIFE,
        within(
            "_receipt",
            "or not _reconciliation_topic_matches(discussion, request)",
            "or discussion.topic != request.topic",
        ),
        "tests/test_b16a_round5_reconcile.py",
        "app and credential",
    ),
    (
        "r5_new_5a_putty_closing_boundary_narrowed",
        SAFE,
        within(
            "_putty_spans",
            '    closings = iter(re.finditer(r"[\\"\'\\]})]", text))',
            "    closings = iter(())",
        ),
        R5_ALL_UNIT,
        "",
    ),
    (
        "r5_new_5b_putty_version_narrowed_to_v2",
        SAFE,
        within(
            "_putty_spans",
            'headers = re.compile(r"(?i)(?<![A-Za-z0-9_])PuTTY-User-Key-File-[0-9]++:")',
            'headers = re.compile(r"(?i)(?<![A-Za-z0-9_])PuTTY-User-Key-File-2:")',
        ),
        R5_ALL_UNIT,
        "",
    ),
    (
        "r5_new_6a_exempt_scan_stops_at_dollar_brace",
        SAFE,
        within(
            "_assignment_spans",
            "        ):\n            continue\n        nested = (",
            "        ):\n            position = max(position, end)\n            continue\n        nested = (",
        ),
        R5_ALL_UNIT,
        "",
    ),
    (
        "r5_new_6b_exempt_scan_stops_at_other_exempt",
        SAFE,
        within(
            "_assignment_spans",
            "        if include_exempt or redaction_sensitive_value(key, value):\n            position = max(position, end - int(closed_quote))",
            "        position = max(position, end - int(closed_quote))\n        if include_exempt or redaction_sensitive_value(key, value):",
        ),
        R5_ALL_UNIT,
        "",
    ),
    (
        "r5_new_7_depth_storage_widened_array_Q",
        SAFE,
        within("_decoding_depths", "    return depths", '    return array("Q", depths)'),
        "tests/test_b16a_round5_memory.py",
        "byte_storage_rss or review_xml_attr_quote_run",
    ),
    (
        "r5_new_8a_seeded_subset_drops_form_body_dimension",
        "b16a_r5_corpus.py",
        within(
            "seeded_subset",
            "        if rng.randrange(40) == 0:\n            yield row",
            '        if rng.randrange(40) == 0 and row.shape != "form_body":\n            yield row',
        ),
        "tests/test_b16a_round5_corpus.py",
        "",
    ),
    (
        "r5_new_8b_seeded_subset_drops_neighbours",
        "b16a_r5_corpus.py",
        within("seeded_subset", "    yield from neighbours()", "    return"),
        "tests/test_b16a_round5_corpus.py",
        "",
    ),
]

for name, file, edit, tests, selection in review_specs:
    add(name, file, edit, tests, selection)


def replace_body(function, old, new):
    def apply(text):
        start = text.index("def " + function + "(")
        end = text.find("\ndef ", start + 1)
        end = len(text) if end < 0 else end
        assert old in text[start:end]
        return text[:start] + text[start:end].replace(old, new) + text[end:]

    return apply


adapt(
    "r5_new_2a_deeper_close_quote_ends_shallower_value",
    replace_body(
        "value", "self.quote_closings.get(end, end) < start", "end in self.quote_closings"
    ),
    STRUCT,
    "full_sweep_counterexamples",
)
adapt(
    "r5_new_2b_deeper_close_quote_closes_shallower_pending",
    exact("if inner > opening and inner_channel[1] >= level:", "if inner > opening:"),
    STRUCT,
    "full_sweep_counterexamples or value_start",
)
adapt(
    "r5_new_3a_har_lexer_resync_on_token_error",
    within(
        "_har_json_spans",
        "            if token is None:\n                break",
        "            if token is None:\n                position += 1\n                continue",
    ),
    HAR,
    "no_resynchronization",
)
adapt(
    "r5_new_3b_har_lexer_resync_on_state_error",
    within(
        "_har_json_spans",
        '            if state not in {"value", "first_value"} or kind in ":,":\n                break',
        '            if state not in {"value", "first_value"} or kind in ":,":\n                position = end\n                continue',
    ),
    HAR,
    "no_resynchronization",
)
adapt(
    "r5_new_6a_exempt_scan_stops_at_dollar_brace",
    within(
        "_assignment_spans",
        "and syntax.placeholder_complete(text, inner_start, inner_end)\n        ):\n            continue",
        "and syntax.placeholder_complete(text, inner_start, inner_end)\n        ):\n            position = end\n            continue",
    ),
    STRUCT,
    "every_scanner",
)
adapt(
    "r5_new_6b_exempt_scan_stops_at_other_exempt",
    base.MUTANTS["exempt_value_skip_restored"][1],
    STRUCT,
    "every_scanner",
)
adapt(
    "r6_quoted_call_paren_closes",
    exact(
        "                        cursor = closing\n", "                        cursor = pos + 1\n"
    ),
    STRUCT,
    "complete_exemption",
)
adapt(
    "r6_html_name_rule_disabled",
    replace_body(
        "_html_spans",
        "private |= end - begin > 1024 or redaction_sensitive_key(\n                    html.unescape(text[begin:end])\n                )",
        "private |= False",
    ),
    STRUCT,
    "html_private",
)
adapt(
    "r6_html_type_rule_disabled",
    replace_body(
        "_html_spans",
        'password_type |= (\n                    end - begin > 1024 or html.unescape(text[begin:end]).lower() == "password"\n                )',
        "password_type |= False",
    ),
    STRUCT,
    "html_private",
)


adapt(
    "drop_nested_dollar_brace_exemption",
    lambda s: within(
        "_assignment_spans",
        "and syntax.placeholder_complete(text, inner_start, inner_end)",
        "and False",
    )(
        within("private", "return not self.placeholder_complete(text, first, last)", "return True")(
            s
        )
    ),
    STRUCT,
    "complete_exemption",
)
adapt(
    "prefix_placeholder_exemption",
    within(
        "private",
        "return not self.placeholder_complete(text, first, last)",
        'return not text.endswith("}", first, last)',
    ),
    "tests/test_b16a_round4_placeholders.py",
    "lookalike",
)
adapt("assignment_closure_removed", base.MUTANTS["assignment_closure_removed"][1], equivalent=True)
adapt(
    "r5_new_2a_deeper_close_quote_ends_shallower_value",
    base.MUTANTS["r5_new_2a_deeper_close_quote_ends_shallower_value"][1],
    STRUCT,
    "decoded_quote_cannot",
)
adapt(
    "r5_new_2b_deeper_close_quote_closes_shallower_pending",
    base.MUTANTS["r5_new_2b_deeper_close_quote_closes_shallower_pending"][1],
    STRUCT,
    "deeper_close_does",
)
adapt(
    "r5_new_3b_har_lexer_resync_on_state_error",
    within(
        "_har_json_spans",
        "                elif kind != '\"' or text[end - 1 : end] != '\"':\n                    break",
        "                elif kind != '\"' or text[end - 1 : end] != '\"':\n                    position = end\n                    continue",
    ),
    STRUCT,
    "har_does_not_resynchronize",
)
new(
    "r5_new_2c_deeper_close_quote_pairs_shallower_open",
    exact(
        "previous = pending.pop(channel, None)",
        "previous = pending.pop(channel, None) or next((pending.pop(k) for k in list(pending) if k[0] == char and k[1] < level), None)",
    ),
    "tests/test_b16a_round5_quotes.py",
    "quoted_arguments",
)


adapt(
    "drop_environment_exemption",
    lambda s: within(
        "_assignment_spans",
        "if not include_exempt and call and syntax.complete(call.end() - 1, inner_end):",
        "if False:",
    )(within("private", "return not self.complete(call.end() - 1, last)", "return True")(s)),
    STRUCT,
    "complete_exemption",
)
adapt(
    "r6_value_opener_is_closer",
    base.MUTANTS["r6_value_opener_is_closer"][1],
    STRUCT,
    "opener_preserves",
)
adapt(
    "r6_lookup_suffix_scan_restored",
    base.MUTANTS["r6_lookup_suffix_scan_restored"][1],
    CPU,
    "balanced_lookup",
)
adapt(
    "r5_new_5b_putty_version_narrowed_to_v2",
    base.MUTANTS["r5_new_5b_putty_version_narrowed_to_v2"][1],
    STRUCT,
    "putty_version_three",
)
adapt(
    "r5_new_8a_seeded_subset_drops_form_body_dimension",
    within(
        "seeded_subset",
        "        if rng.randrange(40) == 0:\n            yield row",
        '        if rng.randrange(40) == 0 and not row.shape.startswith("form_first:"):\n            yield row',
    ),
)
adapt(
    "har_resynchronizes_after_error",
    base.MUTANTS["har_resynchronizes_after_error"][1],
    STRUCT,
    "lexer_keeps",
)
adapt(
    "har_non_json_whitespace_allowed",
    base.MUTANTS["har_non_json_whitespace_allowed"][1],
    STRUCT,
    "lexer_keeps",
)
adapt(
    "r5_new_3a_har_lexer_resync_on_token_error",
    base.MUTANTS["r5_new_3a_har_lexer_resync_on_token_error"][1],
    STRUCT,
    "lexer_keeps",
)

adapt(
    "exempt_value_skip_restored",
    base.MUTANTS["exempt_value_skip_restored"][1],
    "tests/test_b16a_round5_exempt.py",
    "outer_value",
)
adapt(
    "r5_new_6b_exempt_scan_stops_at_other_exempt",
    base.MUTANTS["r5_new_6b_exempt_scan_stops_at_other_exempt"][1],
    "tests/test_b16a_round5_exempt.py",
    "outer_value",
)
adapt(
    "r5_new_6a_exempt_scan_stops_at_dollar_brace",
    base.MUTANTS["r5_new_6a_exempt_scan_stops_at_dollar_brace"][1],
    "tests/test_b16a_round5_exempt.py",
    "outer_value",
)

adapt(
    "r5_new_2c_deeper_close_quote_pairs_shallower_open",
    base.MUTANTS["r5_new_2c_deeper_close_quote_pairs_shallower_open"][1],
    STRUCT,
    "deeper_quote_never",
)
adapt(
    "enclosing_quote_context_ignored",
    base.MUTANTS["enclosing_quote_context_ignored"][1],
    STRUCT,
    "punctuation_does_not_close",
)
adapt(
    "r6_har_swallowed_candidate_lost",
    base.MUTANTS["r6_har_swallowed_candidate_lost"][1],
    STRUCT,
    "recovers_candidate_swallowed",
)
adapt(
    "nested_form_scope_removed",
    base.MUTANTS["nested_form_scope_removed"][1],
    STRUCT,
    "nested_form_prefix",
)

adapt(
    "ending_drops_encoded_qmark",
    within(
        "_query_spans", "(not encoded or syntax.depth(boundary.start()) <= operator_depth)", "True"
    ),
)
adapt(
    "ending_readds_literal_qmark",
    within(
        "_query_spans", "(not encoded or syntax.depth(boundary.start()) <= operator_depth)", "False"
    ),
)
new(
    "r6_whole_url_question_layer_ignored",
    within(
        "_query_spans",
        "(not encoded or syntax.depth(boundary.start()) <= operator_depth)",
        "not encoded",
    ),
    selection="encoding_the_whole_url",
)
new(
    "r6_quoted_assignment_overlap_ignored",
    within(
        "_assignment_spans",
        "                closed_quote\n                and continuation",
        "                False\n                and continuation",
    ),
    selection="quoted_argument_can_overlap",
)
new(
    "r6_embedded_form_consumes_url_fragment",
    within(
        "_form_ranges",
        "            if url is not None and url.start() <= form_field.start() < url.end():",
        "            if False:",
    ),
    "tests/test_b16a_round4_form.py",
    "fragment_never",
)

new(
    "r6_escaped_candidate_blocks_percent_har",
    within(
        "_har_candidates", "    elif '\"' in text and", "        return\n    elif '\"' in text and"
    ),
    selection="full_sweep_counterexamples",
)
new(
    "r6_encoded_query_whitespace_terminates",
    within(
        "_query_spans",
        "boundary[0].isspace() or syntax.depth(boundary.start()) > operator_depth",
        "syntax.depth(boundary.start()) > operator_depth",
    ),
    "tests/test_b16a_privacy.py",
    "credential_shapes_and_encoded_spans",
)

adapt(
    "har_percent_layer_consumes_form_budget",
    base.MUTANTS["har_percent_layer_consumes_form_budget"][1],
    STRUCT,
    "form_har_does_not_spend",
)
adapt(
    "r6_escaped_candidate_blocks_percent_har",
    base.MUTANTS["r6_escaped_candidate_blocks_percent_har"][1],
    STRUCT,
    "percent_har_is_independent",
)

if __name__ == "__main__":
    from b16a_r6_mutation_run import main

    main()
