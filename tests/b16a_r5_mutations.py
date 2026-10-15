"""Round-5 adaptations retain the complete author/reviewer operator semantics."""

import re

import b16a_r4_mutations as prior
from b16a_r3_mutations import LIFE, SAFE, add, exact, within

base = prior.base
R5 = "tests/test_b16a_round5_corpus.py"
URL = "tests/test_b16a_round5_urls.py"
QUOTES = "tests/test_b16a_round5_quotes.py"
HAR = "tests/test_b16a_round5_har.py"
BLOCK = "tests/test_b16a_round5_blocks.py"
EXEMPT = "tests/test_b16a_round5_exempt.py"
REPLAY = "tests/test_b16a_round5_reconcile.py"
MEMORY = "tests/test_b16a_round5_memory.py"


def chunk_backtracking(text):
    match = re.search(r"        chunk = re.compile\([^\n]+\)", text)
    assert match and "++" in match[0]
    return exact(match[0], match[0].replace("++", "+"))(text)


file, _, tests, selection, equivalent = base.MUTANTS["value_not_possessive"]
add("value_not_possessive", file, chunk_backtracking, tests, selection, equivalent)
add(
    "bare_bearer_removed",
    SAFE,
    lambda text: exact("original_headers, _bearer_spans(text),", "original_headers,")(
        exact("            _bearer_spans(text),\n", "")(text)
    ),
    prior.R3,
    "handoff",
)
add(
    "reconcile_raw_topic_only",
    LIFE,
    within(
        "_reconciliation_topic_matches",
        "return request.topic in {discussion.topic, redact_display(discussion.topic)}",
        "return request.topic == discussion.topic",
    ),
    REPLAY,
    "app and credential",
)
add(
    "container_closer_depth_ignored",
    SAFE,
    exact("                and self.depth(pos) <= self.depth(stack[-1])\n", ""),
    prior.R4,
    "container_closer",
)

# The exemption now has a constant-time indexed path; removing the rule removes
# both that path and its ordinary value-classifier implementation.
old = base.MUTANTS["drop_nested_dollar_brace_exemption"]
add(
    "drop_nested_dollar_brace_exemption",
    SAFE,
    lambda text: within(
        "_assignment_spans",
        'not include_exempt\n            and text.startswith("${", inner_start)',
        'False\n            and text.startswith("${", inner_start)',
    )(old[1](text)),
    old[2],
    old[3],
)

add(
    "next_scheme_not_authority_boundary",
    SAFE,
    within(
        "_userinfo_spans",
        "end = min(end, following.start() if following else len(text))",
        "end = end",
    ),
    URL,
    "each_url",
)
add(
    "scan_resumes_at_candidate_end",
    SAFE,
    within(
        "_userinfo_spans", "        match = following", "        match = scheme.search(text, end)"
    ),
    URL,
    "each_url",
    True,
)


def skip_inner_quotes(text):
    start = text.index("class _Syntax:")
    end = text.index("\ndef _merge_spans", start)
    body = exact(
        "            level = self.depth(pos)\n",
        "            level = self.depth(pos)\n            if pend"
        "ing and level > min(k[1] for k in pending):\n           "
        "     continue\n",
    )(text[start:end])
    return text[:start] + body + text[end:]


add("inner_quotes_skipped", SAFE, skip_inner_quotes, QUOTES, "quoted_arguments")

add(
    "deeper_quoted_argument_refused",
    SAFE,
    within(
        "value",
        'self.ends.get(start, len(text)) if text[start] in "\\"\'" else start',
        'self.ends.get(start, len(text)) if text[start] in "\\"\'"'
        " and self.depth(start) <= level else start",
    ),
    QUOTES,
    "quoted_arguments",
)


def har_decoded(text):
    text = exact(
        "            _command_spans(text, syntax),",
        "            _har_spans(text, syntax),\n            _command_spans(text, syntax),",
    )(text)
    return exact(", _har_spans(text)]", "]")(text)


add("har_lexed_on_canonical_data", SAFE, har_decoded, HAR, "json_values")
add(
    "har_resynchronizes_after_error",
    SAFE,
    within(
        "_har_spans",
        "        if token is None:\n            break  # Never se"
        "arch for a later plausible object after an error.",
        """        if token is None:
            later = text.find('{"name"', position)
            if later >= 0:
                for a,b,k,r in _har_spans(text[later:]):
                    yield later+a,later+b,k,r
            return""",
    ),
    HAR,
    "no_resynchronization",
)
add(
    "har_scalar_emission_removed",
    SAFE,
    within(
        "register",
        "        quoted = kind == '\"'\n",
        '        quoted = kind == \'"\'\n        if key == "value" '
        'and not quoted and kind not in "{[":\n            return'
        " None\n",
    ),
    HAR,
    "scalar_values",
)
add(
    "receipt_raw_topic_only",
    LIFE,
    within(
        "_receipt",
        "or not _reconciliation_topic_matches(discussion, request)",
        "or discussion.topic != request.topic",
    ),
    REPLAY,
    "app and credential",
)
add(
    "putty_line_anchor_restored",
    SAFE,
    within(
        "_putty_spans",
        'r"(?i)(?<![A-Za-z0-9_])PuTTY-User-Key-File-',
        'r"(?im)^PuTTY-User-Key-File-',
    ),
    BLOCK,
    "key_blocks",
)


def exempt_skip(text):
    text = within(
        "_assignment_spans",
        "        if include_exempt or redaction_sensitive_value("
        "key, value):\n            position = max(position, end -"
        " int(closed_quote))",
        "        position = max(position, end - int(closed_quote"
        "))\n        if include_exempt or redaction_sensitive_val"
        "ue(key, value):",
    )(text)
    return within(
        "_assignment_spans",
        "        ):\n            continue\n        nested = (",
        "        ):\n            position = max(position, end)\n"
        "            continue\n        nested = (",
    )(text)


add("exempt_value_skip_restored", SAFE, exempt_skip, EXEMPT, "outer_value")
add(
    "depths_python_pointer_list",
    SAFE,
    within("_decoding_depths", "    return depths", "    return list(depths)"),
    MEMORY,
    "byte_storage_rss",
)
add(
    "enclosing_quote_context_ignored",
    SAFE,
    exact("                and stack_context[-1] == quote_context\n", ""),
    QUOTES,
    "outer_structure",
)
add(
    "har_form_scope_ignored",
    SAFE,
    within(
        "_har_spans",
        'if _form_depth < 3 and form.match(text) and not re.search(r"\\s", text):',
        "if False:",
    ),
    HAR,
    "form_wrapped",
)

add(
    "har_unquoted_branch_removed",
    SAFE,
    within("register", "_REDACTED if quoted else '\"' + _REDACTED + '\"'", "_REDACTED"),
    HAR,
    "scalar_values",
)
add(
    "drop_passphrase_name",
    SAFE,
    exact('            "passphrase",\n', ""),
    prior.SYNTAX,
    "names_and_operators",
)
add("drop_sid_name", SAFE, exact('            "sid",\n', ""), prior.SYNTAX, "names_and_operators")

add(
    "restart_unmatched_escape_quote",
    SAFE,
    exact('r"(?P<slashes>\\\\++)(?P<quote>[\\"\']?)"', 'r"(?P<slashes>\\\\++)(?P<quote>[\\"\'])"'),
    "tests/test_b16a_round4_complexity.py",
    "review_backslash_run",
)

add(
    "har_non_json_whitespace_allowed",
    SAFE,
    within(
        "_har_spans",
        'whitespace = re.compile(r"[ \\t\\r\\n]*+")',
        'whitespace = re.compile(r"\\s*+")',
    ),
    HAR,
    "non_json_whitespace",
)

old = base.MUTANTS["assignment_closure_removed"]
add("assignment_closure_removed", SAFE, old[1], R5, "assignment_closure", False)
add(
    "nested_form_scope_removed",
    SAFE,
    within(
        "_layered_form_ranges",
        "        yield from _form_prefix_ranges(text, depths, prefix, field_start, end, level)",
        "        yield field_start, end, level",
    ),
    R5,
    "class_matrix",
)


if __name__ == "__main__":
    base.main()
