"""Independent Round 5 neighbour matrix generator along dimensions outside the author's matrix."""

from __future__ import annotations

import json
from urllib.parse import quote, quote_plus


SEC = "SYNTH" + "q" + "NEIGHBOUR" + "q" + "4242"
SEC2 = "SYNTH" + "q" + "TAILPART" + "q" + "4242"
CURL_U = "curl " + "-u"
PUTTY2 = "Pu" + "TTY-User-Key-File-2: ssh-rsa"
PUTTY3 = "Pu" + "TTY-User-Key-File-3: ssh-ed25519"
OPENSSH_B = "-----BE" + "GIN OPENSSH PRIVATE KEY-----"
OPENSSH_E = "-----E" + "ND OPENSSH PRIVATE KEY-----"
PKCS8_B = "-----BE" + "GIN ENCRYPTED PRIVATE KEY-----"
PKCS8_E = "-----E" + "ND ENCRYPTED PRIVATE KEY-----"

SCHEMES = ("ftp", "ssh", "git+ssh", "redis", "postgres", "postgresql", "mongodb+srv", "amqp")


def generate_rows():
    rows = []

    # 1. Schemes x URL forms x Preceding URLs
    hosts = ("db.example.invalid", "db.example.invalid:5432", "[::1]", "[::1]:5432", "münchen.example.invalid")
    firsts = ("https://example.invalid", "https://[::1]:8080")
    for scheme in SCHEMES:
        for case_fn in (str.lower, str.upper):
            sc = case_fn(scheme)
            for host in hosts:
                for first in firsts:
                    text = f"{first},{sc}://alice:{SEC}@{host}/app"
                    rows.append(("schemes_url_forms", text, (SEC,), False))
        # Benign mirrors: scheme with port + email separated by comma or escaped newline
        rows.append(("schemes_benign_csv", f"{scheme}://db.example.invalid:5432,alice@example.invalid", (), True))
        rows.append(("schemes_benign_escaped_nl", f"{scheme}://db.example.invalid:5432\\nalice@example.invalid", (), True))

    # Protocol-relative URLs
    for host in ("example.invalid", "example.invalid:8080", "[::1]:8080", "münchen.example.invalid"):
        for prefix in ("", "https://example.invalid,", "url="):
            text = f"{prefix}//alice:{SEC}@{host}/path"
            rows.append(("url_protocol_relative", text, (SEC,), False))
        rows.append(("url_protocol_relative_benign", f"//{host}/path", (), True))

    # 2. Contexts (Markdown autolinks, HTML/XML attributes, YAML flow/block, TOML inline, .env export, f-strings, triple quotes, backticks, heredocs)
    for scheme in ("https", "postgres", "redis", "ssh"):
        rows.append(("ctx_autolink_userinfo", f"<{scheme}://alice:{SEC}@example.invalid/p>", (SEC,), False))
        rows.append(("ctx_autolink_query", f"<{scheme}://example.invalid/p?token={SEC}>", (SEC,), False))
        rows.append(("ctx_autolink_benign_port_email", f"<{scheme}://example.invalid:8080>,<alice@example.invalid>", (), True))
        rows.append(("ctx_autolink_benign_empty_query", f"<{scheme}://example.invalid/p?token=>", (), True))

    for tag, k_attr, v_attr, key_name in (
        ("input", "name", "value", "password"),
        ("input", "name", "value", "api_key"),
        ("param", "name", "value", "client_secret"),
        ("meta", "name", "content", "csrf_token"),
    ):
        rows.append(("ctx_html_xml_name_value", f'<{tag} {k_attr}="{key_name}" {v_attr}="{SEC}">', (SEC,), False))
        rows.append(("ctx_html_xml_benign", f'<{tag} {k_attr}="username" {v_attr}="alice">', (), True))

    for q in ('"', "'"):
        rows.append(("ctx_yaml_flow", f"{{user: alice, password: {q}{SEC}{q}, role: admin}}", (SEC,), False))
        rows.append(("ctx_toml_inline", f'cfg = {{ user = "alice", api_key = "{SEC}" }}', (SEC,), False))
        rows.append(("ctx_dotenv_export", f"export SECRET_TOKEN={q}{SEC}{q}", (SEC,), False))
        rows.append(("ctx_dotenv_export_after_apostrophe", f"# don't share\nexport SECRET_TOKEN={q}{SEC} {SEC2}{q}", (SEC, SEC2), False))
        rows.append(("ctx_dotenv_benign_after_apostrophe", f"# don't share\nexport SECRET_TOKEN={q}$SECRET_TOKEN{q}\nexport USER={q}alice{q}", (), True))

    # Backticks, f-strings, triple quotes, heredocs
    for cmd_tpl in (
        f"{CURL_U} {{q}}alice:{SEC}{{q}} https://example.invalid",
        f"curl --user {{q}}alice:{SEC}{{q}} https://example.invalid",
        f"curl -b {{q}}{SEC}{{q}} https://example.invalid",
        f"sshpass -p {{q}}{SEC} {SEC2}{{q}} ssh host",
        f"mysql -p{{q}}{SEC} {SEC2}{{q}} db",
        f"CREATE USER u IDENTIFIED BY {{q}}{SEC} {SEC2}{{q}};",
    ):
        markers = (SEC, SEC2) if SEC2 in cmd_tpl else (SEC,)
        # Backtick around whole command or around argument
        rows.append(("ctx_backtick_whole", "`" + cmd_tpl.format(q="'") + "`", markers, False))
        rows.append(("ctx_backtick_arg", cmd_tpl.format(q="`"), markers, False))
        # Triple double-quoted with double-quoted arg
        rows.append(("ctx_triple_dq", 'doc = """' + cmd_tpl.format(q='"') + '"""', markers, False))
        # Triple single-quoted with single-quoted arg
        rows.append(("ctx_triple_sq", "doc = '''" + cmd_tpl.format(q="'") + "'''", markers, False))
        # PEP 701 f-string with double-quoted arg
        rows.append(("ctx_fstring_pep701", 'msg = f"run {sh(\'' + cmd_tpl.format(q='"') + '\')}"', markers, False))
        # Heredoc with apostrophe in comment
        rows.append(("ctx_heredoc_apostrophe", "cat <<EOF\n# operator's note\n" + cmd_tpl.format(q="'") + "\nEOF", markers, False))

    # Triple-quote benign mirrors
    rows.append(("ctx_triple_dq_benign_json", 'doc = """{"u":"https://example.invalid:8080","e":"alice@example.invalid"}"""', (), True))
    rows.append(("ctx_triple_dq_benign_query", 'doc = """{"u":"https://example.invalid?token=","e":"ok"}"""', (), True))
    rows.append(("ctx_triple_dq_benign_getenv", 'doc = """token = os.getenv("TOKEN")\nx = 1"""', (), True))

    # 3. Quoting: Unbalanced and escaped quotes immediately before a credential, across layers 0..2
    for prefix, q in (
        ("don't run ", "'"),
        ("It's failing: ", "'"),
        ('Note "unfinished: ', '"'),
        (r'\"', '"'),
        (r"\'", "'"),
    ):
        for cmd_tpl in (
            f"{CURL_U} {{q}}alice:{SEC}{{q}} https://example.invalid",
            f"curl --user {{q}}alice:{SEC}{{q}} https://example.invalid",
            f"curl -b {{q}}{SEC}{{q}} https://example.invalid",
            f"sshpass -p {{q}}{SEC} {SEC2}{{q}} ssh host",
            f"mysql -p{{q}}{SEC} {SEC2}{{q}} db",
            f"CREATE USER u IDENTIFIED BY {{q}}{SEC} {SEC2}{{q}};",
        ):
            markers = (SEC, SEC2) if SEC2 in cmd_tpl else (SEC,)
            raw = prefix + cmd_tpl.format(q=q)
            for layer in (0, 1, 2):
                enc = raw
                for _ in range(layer):
                    enc = json.dumps({"cmd": enc})
                rows.append((f"quoting_unbalanced_L{layer}", enc, markers, False))

    # Bracket/paren + unbalanced quote before URL userinfo or query
    for open_b, close_b in (("[", "]"), ("(", ")")):
        rows.append(("quoting_bracket_userinfo", f"{open_b}# don't\n'https://alice:pa{close_b}{SEC}@example.invalid/p'{close_b}", (SEC,), False))
        rows.append(("quoting_bracket_query", f"{open_b}# don't\n'https://example.invalid/p?token=pa{close_b}{SEC}'{close_b}", (SEC,), False))
        rows.append(("quoting_triple_bracket_userinfo", f'docs = {open_b}""" "https://alice:pa{close_b}{SEC}@example.invalid/p" """{close_b}', (SEC,), False))

    # 4. HAR: postData.params, queryString, request.cookies with expires, response.headers, repeated name keys, and surrounding contexts
    har_structures = {
        "postData_params": f'{{"postData":{{"params":[{{"name":"password","value":"{SEC}"}}]}}}}',
        "queryString": f'{{"queryString":[{{"name":"api_key","value":"{SEC}"}}]}}',
        "request_cookies_expires": f'{{"request":{{"cookies":[{{"name":"sid","value":"{SEC}","expires":"2026-12-31T23:59:59Z"}}]}}' + "}",
        "response_headers": f'{{"response":{{"headers":[{{"name":"Set-Cookie","value":"{SEC}"}}]}}}}',
        "repeated_name_sensitive_first": f'{{"name":"sid","value":"{SEC}","name":"theme"}}',
        "repeated_name_in_params": f'{{"postData":{{"params":[{{"name":"password","value":"{SEC}","name":"username"}}]}}}}',
        "repeated_name_in_querystring": f'{{"queryString":[{{"name":"api_key","value":"{SEC}","name":"page"}}]}}',
        "repeated_name_sensitive_last": f'{{"name":"theme","value":"{SEC}","name":"sid"}}',
    }
    for hname, hjson in har_structures.items():
        for wrapper_name, wrapper in (
            ("bare", "{x}"),
            ("json1", json.dumps({"har": "{x}"})),
            ("markdown_fence", "```json\n{x}\n```"),
            ("log_prefix", "HAR capture: {x}"),
            ("yaml_block", "har: |\n  {x}"),
        ):
            text = json.dumps({"har": hjson}) if wrapper_name == "json1" else wrapper.format(x=hjson)
            rows.append((f"har_{hname}_{wrapper_name}", text, (SEC,), False))

    # 5. Key blocks: OpenSSH, encrypted PKCS#8, PuTTY v2/v3 across JSON, percent, form, CRLF, and quoted comments
    key_blocks = {
        "openssh": f"{OPENSSH_B}\n{SEC}\n{OPENSSH_E}",
        "pkcs8_enc": f"{PKCS8_B}\n{SEC}\n{PKCS8_E}",
        "putty2_apostrophe_comment": f"alice's key:\n{PUTTY2}\nEncryption: none\nComment: alice's laptop\nPublic-Lines: 1\nAAAA\nPrivate-Lines: 1\n{SEC}\nPrivate-MAC: deadbeef",
        "putty3_crlf": f"{PUTTY3}\r\nEncryption: none\r\nComment: key\r\nPublic-Lines: 1\r\nAAAA\r\nPrivate-Lines: 1\r\n{SEC}\r\nPrivate-MAC: deadbeef",
    }
    for kname, ktext in key_blocks.items():
        for ending in ("\n", "\r\n"):
            raw = ktext.replace("\n", ending) if "\r\n" not in ktext else ktext
            rows.append((f"key_{kname}_raw", raw, (SEC,), False))
            rows.append((f"key_{kname}_json1", json.dumps({"k": raw}), (SEC,), False))
            rows.append((f"key_{kname}_pct1", quote(raw, safe=""), (SEC,), False))
            rows.append((f"key_{kname}_form_kv", "blob=" + quote_plus(raw), (SEC,), False))
            rows.append((f"key_{kname}_form_kv_json2", json.dumps({"outer": json.dumps({"body": "blob=" + quote_plus(raw)})}), (SEC,), False))
            rows.append((f"key_{kname}_form_bare", quote_plus(raw), (SEC,), False))

    # 6. R4-6 exempt outer values hiding inner header / XML / command credentials
    for r46_name, r46_text in (
        ("header_in_getenv", f'cookie = os.getenv("COOKIE", "set_cookie={SEC}")'),
        ("auth_in_getenv", f'authorization = os.getenv("AUTH", "cookie={SEC}")'),
        ("header_in_angle", f"Authorization: <cookie={SEC}>"),
        ("header_in_shell_default_space", f"Cookie: ${{COOKIE:- set_cookie={SEC}}}"),
        ("xml_in_shell_default", f"<token>${{T:-<password>{SEC}</password>}}</token>"),
        ("xml_in_mustache", f"<token>{{{{<password>{SEC}</password>}}}}</token>"),
    ):
        for layer in (0, 1, 2):
            enc = r46_text
            for _ in range(layer):
                enc = json.dumps({"code": enc})
            rows.append((f"r46_{r46_name}_L{layer}", enc, (SEC,), False))

    return rows

