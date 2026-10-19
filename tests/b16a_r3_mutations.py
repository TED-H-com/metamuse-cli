"""OFFLINE mutation proof; isolated source copies, serial disk pytest, import canary.

Run with ``UV_OFFLINE=1 uv run python tests/b16a_r3_mutations.py``. Evidence goes
under /tmp by default. Original review operators are adapted to the forward
scanner; removed design choices are restored as defects, rather than weakened.
"""

import argparse
import difflib
import json
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SAFE = "metamuse/safety.py"
LIFE = "metamuse/_muse_lifecycle.py"
CONTENT = "metamuse/content.py"
R2 = "tests/test_b16a_round2.py"
R3 = "tests/test_b16a_round3.py"
RUN = "tests/test_b16a_round3_runtime.py"
CPU = "tests/test_b16a_round3_complexity.py"


def exact(old, new):
    def apply(text):
        assert text.count(old) == 1, (old, text.count(old))
        return text.replace(old, new, 1)

    return apply


def within(function, old, new):
    def apply(text):
        start = text.index("def " + function + "(")
        end = text.find("\ndef ", start + 1)
        if end < 0:
            end = len(text)
        body = exact(old, new)(text[start:end])
        return text[:start] + body + text[end:]

    return apply


def moved(function, line, before):
    def apply(text):
        start = text.index("def " + function + "(")
        end = text.index("\ndef ", start + 1)
        body = exact(line, "")(text[start:end])
        body = exact(before, line + before)(body)
        return text[:start] + body + text[end:]

    return apply


def add_raw_bearer(text):
    # Restore the old non-possessive encoded-space ambiguity, which greedily
    # treats %20 as token characters after backtracking the separator.
    anchor = "    spans = list(_merge_spans([spans, original_fields]))\n"
    code = """    legacy = []
    pattern = r"(?i)bearer(?:\\s|%(?:25){0,2}20)+(?P<token>[A-Za-z0-9._~+/%=-]{8,}+)"
    for match in re.finditer(pattern, text):
        if match["token"] != _REDACTED:
            legacy.append((*match.span("token"), "bearer_token", _REDACTED))
    spans = list(_merge_spans([spans, original_fields, legacy]))
"""
    return exact(anchor, code)(text)


def disable_cookie_loop(text):
    start = text.index("        while separator :=", text.index("def _cookie_spans("))
    end = text.index("):\n", start) + len("):\n")
    return text[:start] + "        while False:\n" + text[end:]


# name -> file, edit, selected file/expression, optional differential equivalence
MUTANTS = {}


def add(name, file, edit, tests=R3, selection="", equivalent=False):
    MUTANTS[name] = (file, edit, tests, selection, equivalent)


add(
    "skip_check_in_submit",
    LIFE,
    within("submit_muse_exchange", "    _check_question(request.question)\n", ""),
    R2,
    "test_f12_submit_has_its_own_guard",
)
add(
    "skip_body_equality_in_wire",
    LIFE,
    exact("if payload.body != {", "if False and payload.body != {"),
    R2,
    "test_f12_wire_binds_checked_text_to_actual_body",
)
add(
    "key_unquote_one_layer",
    SAFE,
    within("_credential_name", "range(3)", "range(1)"),
    R3,
    "sensitive_key_api",
)
add(
    "ending_drops_encoded_qmark",
    SAFE,
    within(
        "_query_spans", '(boundary.group() == "?" and not encoded)', '(boundary.group() == "?")'
    ),
    R2,
    "test_f7",
)
add(
    "ending_readds_literal_qmark",
    SAFE,
    within("_query_spans", '(boundary.group() == "?" and not encoded)', "False"),
    R2,
    "test_f7",
)
for name, function, line, before in (
    (
        "submit",
        "submit_muse_exchange",
        "    _check_question(request.question)\n",
        '    replay = _replay(ctx, "muse.send", request)\n',
    ),
    (
        "open",
        "open_muse_discussion",
        "    _check_question(request.topic)\n",
        '    replay = _replay(ctx, "muse.open", request)\n',
    ),
    (
        "prepare_send",
        "prepare_send",
        "    _check_question(request.question)\n",
        '    receipt = _receipt(ctx, "muse.send", request)\n',
    ),
):
    add(
        "order_check_before_replay_" + name,
        LIFE,
        moved(function, line, before),
        R2,
        "test_f4",
    )
add(
    "no_fixpoint_check_prepare_send",
    LIFE,
    exact(
        "if packet.stale or redact_text(packet.rendered_text)[0] != packet.rendered_text:",
        "if packet.stale:",
    ),
    "tests/test_b16a_privacy.py",
    "preexisting_packet",
)
add(
    "no_fixpoint_check_wire",
    LIFE,
    exact("if redact_text(packet_text)[0] != packet_text:", "if False:"),
    RUN,
    "frozen_stale",
)
add(
    "sentinel_counted_again",
    SAFE,
    exact("        if text[start:end] == _REDACTED:\n            continue\n", ""),
    R3,
    "differential",
    True,
)
add(
    "value_not_possessive",
    SAFE,
    within(
        "value",
        'chunk = re.compile(r"[^\\s,;&\\"\'{}\\[\\]()]++")',
        'chunk = re.compile(r"[^\\s,;&\\"\'{}\\[\\]()]+")',
    ),
    R3,
    "differential",
    True,
)
add("bearer_space_not_possessive", SAFE, add_raw_bearer, R3, "bearer_separator_boundary")
add(
    "fields_key_not_possessive",
    SAFE,
    exact('r"[A-Za-z_][A-Za-z0-9_.-]{0,127}+"', 'r"[A-Za-z_][A-Za-z0-9_.-]{0,127}"'),
    R3,
    "differential",
    True,
)
add("cookie_first_pair_only", SAFE, disable_cookie_loop, R2, "test_f2")

add(
    "chain_after_assignments",
    SAFE,
    exact(
        "            _cookie_spans(text, syntax),\n            _jar_spans(text),",
        "            _jar_spans(text),\n            _cookie_spans(text, syntax),",
    ),
    R3,
    "differential",
    True,
)
add(
    "frozen_proof_kind_ack",
    LIFE,
    within("_refuse_frozen", 'kind="not_submitted",', 'kind="ack",'),
    R2,
    "test_f4",
)
add(
    "frozen_bytes_unknown",
    LIFE,
    within(
        "_refuse_frozen",
        "outcome='abandoned',bytes_handed_off=0,",
        "outcome='abandoned',bytes_handed_off=NULL,",
    ),
    R2,
    "test_f4",
)
add(
    "wire_skips_title_check",
    LIFE,
    within("_wire", '        _check_question(params["title"])\n', ""),
    R2,
    "test_f4",
)
# The old operator deleted a stored-title check. Its removal is now the required
# fix; restoring it is the non-equivalent regression operator for R2-5.
add(
    "preview_skips_stored_topic",
    LIFE,
    within(
        "_approval_preview",
        "    topic = question = None\n",
        "    topic = question = None\n    if isinstance(requ"
        "est, m.MuseSendRequest):\n        _check_question(c"
        "tx.store.get(m.Discussion, request.discussion_id)."
        "topic)\n",
    ),
    RUN,
    "stored_topic",
)
# There is no alphabet fallback now. Restore its original narrow alphabet gate
# without the uppercase rescue; punctuated/short defaults must still be refused.
add(
    "fallback_uppercase_removed",
    SAFE,
    within(
        "redaction_sensitive_value",
        "    return not _credential_value_exempt(key, value)",
        "    return isinstance(value, str) and re.fullmatch"
        '(r"[A-Za-z0-9_.=-]{8,}", value) is not None and no'
        "t _credential_value_exempt(key, value)",
    ),
    R3,
    "generated_password_rates",
)

# New operators: each rule class, exempt class and each lifecycle fix.
add(
    "assignment_removed",
    SAFE,
    within("_assignment_spans", "if not redaction_sensitive_key(key):", "if True:"),
    R3,
    "leak_matrix",
)
add(
    "structured_containers_ignored",
    SAFE,
    within(
        "redaction_sensitive_value",
        "    return not _credential_value_exempt(key, value)",
        "    return isinstance(value, str) and not _credential_value_exempt(key, value)",
    ),
    R3,
    "safe_value",
)
add(
    "xml_removed",
    SAFE,
    within("_xml_spans", "if not redaction_sensitive_key(key):", "if True:"),
    R3,
    "encodings_and_syntax",
)
add(
    "flags_removed",
    SAFE,
    within(
        "_assignment_spans",
        '        if match["flagspace"] and not match["flag"]:',
        '        if match["flag"] or (match["flagspace"] and not match["flag"]):',
    ),
    R3,
    "encodings_and_syntax",
)
add(
    "authorization_only_bearer",
    SAFE,
    within(
        "_header_spans",
        '        name = _credential_name(match["key"])\n',
        '        name = _credential_name(match["key"])\n    '
        '    if "authorization" in name and not text[match.'
        'end():].startswith("Bearer "):\n            continu'
        "e\n",
    ),
    R3,
    "leak_matrix",
)
add(
    "cookies_need_pairs",
    SAFE,
    within(
        "_header_spans",
        '        name = _credential_name(match["key"])\n',
        '        name = _credential_name(match["key"])\n    '
        '    if "cookie" in name and "=" not in text[match.'
        "end():]:\n            continue\n",
    ),
    R3,
    "leak_matrix",
)
add("jar_removed", SAFE, exact("            _jar_spans(text),\n", ""), R3, "encodings_and_syntax")
add(
    "drop_literal_exemption",
    SAFE,
    within(
        "_credential_value_exempt",
        'if not value or value.lower() in {"null", "none", "nil", "true", "false"}:',
        "if not value:",
    ),
    R3,
    "closed_exemptions",
)
add(
    "drop_placeholder_exemption",
    SAFE,
    within(
        "_credential_value_exempt",
        '    if re.fullmatch(\n        r"(?:\\*{3,}',
        '    if False and re.fullmatch(\n        r"(?:\\*{3,}',
    ),
    R3,
    "closed_exemptions",
)
add(
    "drop_code_reference_exemption",
    SAFE,
    within(
        "_credential_value_exempt",
        'if value.rsplit(".", 1)[-1].lower().replace("_", "") == leaf or value.startswith(',
        'if False and (value.rsplit(".", 1)[-1].lower().rep'
        'lace("_", "") == leaf) or value.startswith(',
    ),
    R3,
    "closed_exemptions",
)
add(
    "drop_environment_exemption",
    SAFE,
    within(
        "_credential_value_exempt",
        '    if re.fullmatch(r"os\\.',
        '    if False and re.fullmatch(r"os\\.',
    ),
    R3,
    "closed_exemptions",
)
add(
    "drop_type_exemption",
    SAFE,
    exact("    if _type_reference(value):\n", "    if False and _type_reference(value):\n"),
    R3,
    "closed_exemptions or closed_type",
)
add(
    "drop_counter_exemption",
    SAFE,
    within(
        "_credential_value_exempt",
        'if leaf.endswith("session") and re.fullmatch(',
        'if False and leaf.endswith("session") and re.fullmatch(',
    ),
    R3,
    "benign2",
)
add(
    "drop_pagination_exemption",
    SAFE,
    within("redaction_sensitive_key", 'or ("page" in leaf and leaf.endswith("token"))', "or False"),
    R3,
    "exempt_names",
)
add("bare_bearer_removed", SAFE, exact("            _bearer_spans(text),\n", ""), R3, "handoff")
add(
    "bearer_prose_redacted",
    SAFE,
    within("_bearer_spans", "        if (\n", "        if True or (\n"),
    R3,
    "benign2",
)
add(
    "prefixes_removed",
    SAFE,
    exact("            _prefix_spans(text),\n", ""),
    R3,
    "well_known_prefixes",
)
add("jwt_removed", SAFE, exact("            _jwt_spans(text),\n", ""), R3, "well_known_prefixes")
add("pem_removed", SAFE, exact("            _pem_spans(text),\n", ""), R3, "well_known_prefixes")
add(
    "userinfo_https_only",
    SAFE,
    within(
        "_userinfo_spans",
        'r"(?i)(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*+://"',
        'r"(?i)https?://"',
    ),
    R3,
    "leak_matrix",
)
add(
    "percent_one_layer",
    SAFE,
    exact("budgets = {False: 3, True: 3}", "budgets = {False: 1, True: 3}"),
    R3,
    "encodings_and_syntax",
)
add(
    "json_escape_ignored",
    SAFE,
    exact("budgets = {False: 3, True: 3}", "budgets = {False: 3, True: 0}"),
    R3,
    "leak_matrix or interleaved",
)
add(
    "form_plus_ignored",
    SAFE,
    exact(
        "        if form:\n            # Decode literal",
        "        if False:\n            # Decode literal",
    ),
    R3,
    "canonical_value_delimiters",
)
add(
    "assignment_closure_removed",
    SAFE,
    exact(
        "view, _close_assignment_spans(view, _view_spans(view, encoded, syntax), syntax)",
        "view, _view_spans(view, encoded, syntax)",
    ),
    R3,
    "differential",
)
add(
    "bearer_closure_removed",
    SAFE,
    exact(
        "def _close_bearer_spans(text, spans):",
        "def _close_bearer_spans(text, spans):\n    yield from spans\n    return",
    ),
    R3,
    "differential",
)
add(
    "packet_fixpoint_backstop_removed",
    CONTENT,
    within(
        "prepare_packet",
        "        else:\n            # No digest or receipt",
        "        if False:\n            # No digest or receipt",
    ),
    RUN,
    "packet_fixpoint_backstop",
)
add(
    "detail_truncated_before_redacting",
    LIFE,
    exact(
        "safe_detail = redact_text(attempt.evidence.detail)[0][:512]",
        "safe_detail = redact_text(attempt.evidence.detail[:512])[0]",
    ),
    RUN,
    "prefix_512",
)
add(
    "detail_redacted_inside_batch",
    LIFE,
    exact(
        "    safe_detail = redact_text(attempt.evidence.detail)[0][:512]\n\n    def save(writer):",
        "    def save(writer):\n        safe_detail = redact"
        "_text(attempt.evidence.detail)[0][:512]",
    ),
    RUN,
    "remote_detail",
)
add(
    "restart_inside_value",
    SAFE,
    within(
        "_assignment_spans",
        "        position = max(position, end - int(closed_quote))",
        "        position = match.end()",
    ),
    R3,
    "no_restart_inside_consumed_value",
)
add(
    "approval_failure_masks_refusal",
    SAFE,
    exact("                return m.failure(refusal)", "                return m.failure(error)"),
    RUN,
    "approval_failure",
)
add(
    "frozen_next_action_generic",
    LIFE,
    exact("        refusal.detail.next_action,", '        "Prepare a new send",'),
    RUN,
    "frozen_stale",
)
add(
    "reconcile_checks_topic",
    LIFE,
    within(
        "open_muse_discussion",
        "    if request.discussion_id is not None:\n",
        "    if request.discussion_id is not None:\n        _check_question(request.topic)\n",
    ),
    RUN,
    "reconcile_after_upgrade",
)
add(
    "auth_symlink_refused",
    SAFE,
    within(
        "revoke",
        "stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)",
        "stat.S_ISREG(info.st_mode)",
    ),
    RUN,
    "auth_list_revoke",
)
add(
    "auth_opens_workspace",
    SAFE,
    exact(
        "return AppContext(paths, None, policy=load_config(paths.root))",
        "return AppContext.open(workspace)",
    ),
    RUN,
    "auth_list_revoke",
)
add(
    "settle_bound_ignored",
    LIFE,
    within(
        "_exchange_view", 'if len(outbound_text.encode("utf-8")) > 49152 + 8192 + 2:', "if False:"
    ),
    RUN,
    "settle_view",
)

add(
    "yaml_block_ignored",
    SAFE,
    within("_assignment_spans", 'syntax.yaml_ends.get(match.start("sep"))', "None"),
    R3,
    "yaml_sensitive_blocks",
)
add(
    "unicode_assignment_spacing_ignored",
    SAFE,
    within(
        "_assignment_spans",
        r"(?:\s*+(?P<sep>=>|[=:])\s*+|(?P<flagspace>\s++))",
        r"(?:[ \t]*+(?P<sep>=>|[=:])[ \t]*+|(?P<flagspace>[ \t]++))",
    ),
    R3,
    "differential",
)

add(
    "packet_preview_stale_check_removed",
    CONTENT,
    within(
        "preview_packet",
        "if redact_text(packet.rendered_text)[0] != packet.rendered_text:",
        "if False:",
    ),
    RUN,
    "packet_preview_refuses",
)

add(
    "curl_removed",
    SAFE,
    exact("            _curl_spans(text, syntax),\n", ""),
    R3,
    "encodings_and_syntax",
)
add(
    "userinfo_authority_boundary_removed",
    SAFE,
    within(
        "_userinfo_spans",
        "        authority_end = min(end, authority.first(first_at + 1))",
        "        authority_end = end",
    ),
    R3,
    "authority_ends",
)

add(
    "header_flag_assignment_ignored",
    SAFE,
    exact(
        '            and not match["flag"]\n',
        "",
    ),
    R3,
    "header_names_in_flags",
)

add(
    "header_flag_line_terminator_removed",
    SAFE,
    within(
        "_assignment_spans",
        '            match["flag"]\n            and not quoted',
        "            False\n            and not quoted",
    ),
    R3,
    "header_flag_values_consume",
)


add(
    "toml_triple_quote_ends_removed",
    SAFE,
    exact("                self.ends[triple[1]] = match.end()\n", ""),
    R3,
    "structured_string_terminators",
)
add(
    "yaml_plain_scalar_end_removed",
    SAFE,
    within("_assignment_spans", 'syntax.yaml_scalars.get(match.start("sep"))', "None"),
    R3,
    "structured_string_terminators",
)
add(
    "xml_attributes_ignored",
    SAFE,
    within("_xml_spans", 'r")(?=[\\s>])"', 'r")[^\\S\\r\\n]*+>"'),
    R3,
    "structured_string_terminators",
)
add(
    "annotation_default_skipped",
    SAFE,
    within(
        "_assignment_spans",
        "if equals >= 0 and _type_reference(text[start:equals].strip()):",
        "if False:",
    ),
    R3,
    "annotated_assignment_default",
)
add(
    "encoded_prefix_names_one_layer",
    SAFE,
    exact("or unquote(unquote(unquote(key)))", "or unquote(key)"),
    R3,
    "encoded_existing_prefix_keys",
)
add(
    "self_closing_xml_consumes_tail",
    SAFE,
    within("_xml_spans", 'if text[value_start - 1 : value_start] == "/":', "if False:"),
    R3,
    "xml_self_closing",
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*")
    parser.add_argument("--root", type=Path, default=Path("/tmp/b16a-r3-mutants"))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--hash-equivalence", action="store_true")
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    plugin = args.root / "plugin"
    plugin.mkdir(exist_ok=True)
    (plugin / "mutcheck.py").write_text("""import json, os
from pathlib import Path
import metamuse.safety, metamuse._muse_lifecycle, metamuse.content

def pytest_sessionstart(session):
    modules = (metamuse.safety, metamuse._muse_lifecycle, metamuse.content)
    paths = {m.__name__: m.__file__ for m in modules}
    assert all(Path(p).is_relative_to(os.environ["MUTANT_SRC"]) for p in paths.values())
    Path(os.environ["MUTCHECK_OUT"]).write_text(json.dumps(paths))
""")
    (plugin / "equivalence.py").write_text("""import json
from b16a_r3_corpus import differential_corpus
from metamuse.safety import redact_text
for name,text in enumerate(differential_corpus()):
    out, findings = redact_text(text)
    print(json.dumps([name,out,findings,redact_text(out)], ensure_ascii=True))
""")
    if args.hash_equivalence:
        script = plugin / "equivalence.py"
        code = script.read_text()
        code = code.replace("import json\n", "import hashlib, json\n")
        code = code.replace("for name,text", "digest = hashlib.sha256()\ncount = 0\nfor name,text")
        code = code.replace(
            "    print(json.dumps([name,out,findings,redact_text(out)], ensure_ascii=True))",
            "    digest.update(json.dumps([name,out,findings,redact_text(out)], "
            "ensure_ascii=True).encode())\n"
            "    count += 1",
        )
        script.write_text(
            code + "print(json.dumps({'inputs':count,'sha256':digest.hexdigest()}))\n"
        )
    snapshot = args.root / "head-src"
    if snapshot.exists():
        shutil.rmtree(snapshot)
    shutil.copytree(REPO / "src", snapshot, ignore=shutil.ignore_patterns("__pycache__"))
    for name in args.names or MUTANTS:
        file, edit, tests, selection, equivalent = MUTANTS[name]
        base = args.root / name
        source = base / "src"
        if source.exists():
            shutil.rmtree(source)
        shutil.copytree(snapshot, source)
        path = source / file
        before = path.read_text()
        after = edit(before)
        assert before != after
        compile(after, str(path), "exec")
        path.write_text(after)
        (base / "mutation.diff").write_text(
            "".join(
                difflib.unified_diff(
                    before.splitlines(True),
                    after.splitlines(True),
                    "head/" + file,
                    "mutant/" + file,
                )
            )
        )
        if args.prepare_only:
            print(name, flush=True)
            continue
        env = {
            **os.environ,
            "UV_OFFLINE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": f"{source}:{plugin}:{REPO / 'tests'}",
            "MUTANT_SRC": str(source),
            "MUTCHECK_OUT": str(base / "imports.json"),
        }
        command = [
            "pytest-disktmp",
            "uv",
            "run",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "mutcheck",
            "-rf",
            tests,
        ]
        if selection:
            command.extend(["-k", selection])
        result = subprocess.run(command, cwd=REPO, env=env, text=True, capture_output=True)
        (base / "pytest.log").write_text(" ".join(command) + "\n" + result.stdout + result.stderr)
        failed = [
            line.split(" ")[1] for line in result.stdout.splitlines() if line.startswith("FAILED ")
        ]
        imported = (base / "imports.json").exists()
        row = {
            "mutant": name,
            "killed": result.returncode == 1 and bool(failed) and imported,
            "exit": result.returncode,
            "imported": imported,
            "failed": failed,
            "summary": result.stdout.strip().splitlines()[-1]
            if result.stdout.strip()
            else result.stderr[-200:],
        }
        if equivalent and result.returncode == 0:
            # Compare output, finding classes/counts and second-pass result for
            # each of the exact 100,372 fixed-seed reviewer inputs.
            for label, tree in (("head", snapshot), ("mutant", source)):
                destination = base / (label + ".jsonl")
                with destination.open("w") as stream:
                    subprocess.run(
                        ["uv", "run", "python", str(plugin / "equivalence.py")],
                        cwd=REPO,
                        env={**env, "PYTHONPATH": f"{tree}:{REPO / 'tests'}"},
                        stdout=stream,
                        check=True,
                    )
            row["equivalent_inputs"] = (
                100372
                if (base / "head.jsonl").read_bytes() == (base / "mutant.jsonl").read_bytes()
                else 0
            )
            if args.hash_equivalence:
                proof = json.loads((base / "head.jsonl").read_text())
                assert proof["inputs"] == 100372
                row["equivalence_sha256"] = proof["sha256"]

        with (args.root / "results.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
