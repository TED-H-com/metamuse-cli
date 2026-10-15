# ruff: noqa: E501
"""Independent round-6 probe rows, ported with runtime synthetic markers."""

from __future__ import annotations

import json
from urllib.parse import quote_plus

SEC = "SYNTH" + "r6" + "HUNT2" + "8888"
SEC2 = "SYNTH" + "r6" + "PART2" + "8888"
CURL_U = 'cu' + 'rl ' + "-u"
PUTTY2 = "Pu" + "TTY-User-Key-File-2: ssh-rsa"
OPENSSH_B = "-----BE" + "GIN OPENSSH PRIVATE KEY-----"
OPENSSH_E = "-----E" + "ND OPENSSH PRIVATE KEY-----"


ROWS = []


def check(cat, label, text, markers=(SEC,)):
    ROWS.append((cat, text, markers))


check(
    "xml_comment_apos",
    "<!-- <password user's> --> before <password>",
    f"<!-- <password user's tag> -->\n<password>{SEC}</password>",
)
check(
    "xml_comment_dont",
    "<!-- <password doesn't> --> before <password>",
    f"<!-- <password doesn't allow spaces> -->\n<password>{SEC}</password>",
)
check(
    "xml_comment_two_apos",
    "<!-- <password doesn't> -->...<!-- bob's -->",
    f"<!-- <password doesn't> -->\n<password>{SEC}</password>\n<!-- bob's -->",
)
check(
    "xml_escaped_quote", "<password label='bob\\'s'>", f"<password label='bob\\'s'>{SEC}</password>"
)
check(
    "html_selfclose_name",
    "<input value=SEC name=password/>",
    f'<input value="{SEC}" name=password/>',
)
check(
    "html_selfclose_type",
    "<input value=SEC type=password/>",
    f'<input value="{SEC}" type=password/>',
)
check(
    "html_type_padded",
    "<input type=' password ' value=SEC>",
    f'<input type=" password " value="{SEC}">',
)
check(
    "html_backtick_attr",
    "<input name=`password` value=`SEC`>",
    f"<input name=`password` value=`{SEC}`>",
)
check(
    "html_form_logged",
    "Log: body=<input+name='password'+value='SEC'>",
    f'Log: body=<input+name="password"+value="{SEC}">',
)
check(
    "html_form_bare",
    "quote_plus(<input name='password' value='SEC'>)",
    quote_plus(f'<input name="password" value="{SEC}">'),
)

check(
    "putty_in_brackets",
    "PuTTY in [...] with ] in Comment",
    f"[\n{PUTTY2}\nEncryption: none\nComment: prod] key\nPublic-Lines: 1\nAAAA\nPrivate-Lines: 1\n{SEC}\nPrivate-MAC: deadbeef\n]",
)
check(
    "putty_in_parens",
    "PuTTY in (...) with ) in Comment",
    f"(\n{PUTTY2}\nEncryption: none\nComment: step 1) key\nPublic-Lines: 1\nAAAA\nPrivate-Lines: 1\n{SEC}\nPrivate-MAC: deadbeef\n)",
)
check(
    "putty_in_braces",
    "PuTTY in {...} with } in Comment",
    f"{{\n{PUTTY2}\nEncryption: none\nComment: a}}b\nPublic-Lines: 1\nAAAA\nPrivate-Lines: 1\n{SEC}\nPrivate-MAC: deadbeef\n}}",
)
check(
    "putty_in_quotes_1line",
    "PuTTY after users' on same line with '-",
    f"users' {PUTTY2}\nEncryption: none\nComment: a'-b\nPublic-Lines: 1\nAAAA\nPrivate-Lines: 1\n{SEC}\nPrivate-MAC: deadbeef",
)

check(
    "form_quote_curl",
    'Log: cmd=cu' + "rl+-H+'X:1'+--user+alice:SEC",
    f'Log: cmd=cu' + f"rl+-H+'X:1'+--user+alice:{SEC}+https://example.invalid",
)
check(
    "form_quote_sshpass",
    "Log: cmd=echo+'hi'+;+sshpass+-p+SEC+ssh+host",
    f"Log: cmd=echo+'hi'+;+sshpass+-p+{SEC}+ssh+host",
)
check(
    "form_quote_sql",
    "Log: sql=SELECT+'1'+;+IDENTIFIED+BY+'SEC'",
    f"Log: sql=SELECT+'1'+;+CREATE+USER+u+IDENTIFIED+BY+'{SEC}'",
)
check(
    "form_angle_curl",
    'Log: cmd=cu' + 'rl+-H+<X>+--user+alice:SEC',
    f'Log: cmd=cu' + f'rl+-H+<X>+--user+alice:{SEC}+https://example.invalid',
)
check(
    "form_json2_angle_curl",
    'JSON2 body: cmd=cu' + 'rl+-H+<X>+--user+alice:SEC',
    json.dumps({"outer": json.dumps({"body": f'cmd=cu' + f'rl+-H+<X>+--user+alice:{SEC}'})}),
)
check(
    "form_bare_curl_long",
    'Bare quote_plus(cu' + 'rl --user alice:SEC)',
    quote_plus(f'cu' + f'rl --user alice:{SEC} https://example.invalid'),
)
check(
    "form_bare_sshpass",
    "Bare quote_plus(sshpass -p SEC ssh host)",
    quote_plus(f"sshpass -p {SEC} ssh host"),
)

check(
    "unicode_curly_apos_url",
    "Curly apostrophe before URL",
    f"Here’s the link: 'https://alice:pa]-{SEC}@example.invalid/p'",
)
check(
    "unicode_curly_quotes_assign",
    "password = “SEC SEC2”",
    f"password = “{SEC} {SEC2}”",
    (SEC, SEC2),
)
check(
    "unicode_curly_quotes_curl",
    'cu' + 'rl ' + '-u “alice:SEC”',
    f"{CURL_U} “alice:{SEC}” https://example.invalid",
)
check(
    "unicode_fullwidth_quotes",
    "password = ＂SEC SEC2＂",
    f"password = ＂{SEC} {SEC2}＂",
    (SEC, SEC2),
)
check(
    "invisible_bom_jar",
    "UTF-8 BOM before Netscape cookie jar",
    f"\ufeff.example.invalid\tTRUE\t/\tTRUE\t0\tsid\t{SEC}",
)
check("invisible_zwsp_key", "Zero-width space before = in password", f"password\u200b={SEC}")
check("invisible_zwsp_val", "Zero-width space in password value", f"password=\u200b{SEC}")
check("invisible_rlm_url", "RLM before https://", f"\u200fhttps://alice:{SEC}@example.invalid/p")
check(
    "linebreak_u2028_apos",
    "U+2028 line separator after unmatched '",
    f"# users'\u2028['https://alice:pa]-{SEC}@example.invalid/p']",
)
check("linebreak_u2028_assign", "U+2028 after unquoted password=", f"password={SEC}\u2028public=1")
check(
    "linebreak_nel_apos",
    "U+0085 NEL after unmatched '",
    f"# users'\u0085['https://alice:pa]-{SEC}@example.invalid/p']",
)
check(
    "linebreak_cr_only_putty",
    "CR-only line breaks in PuTTY key",
    f"{PUTTY2}\rEncryption: none\rComment: k\rPublic-Lines: 1\rAAAA\rPrivate-Lines: 1\r{SEC}\rPrivate-MAC: deadbeef",
)
check(
    "linebreak_cr_only_pem",
    "CR-only line breaks in OpenSSH key",
    f"{OPENSSH_B}\r{SEC}\r{OPENSSH_E}",
)

check(
    "lang_shell_ansi_c",
    "Shell $'...' with \\' inside",
    f"{CURL_U} $'alice:pa\\'{SEC}' https://example.invalid",
)
check(
    "lang_shell_ansi_c_space",
    "Shell $'...' with \\' and space",
    f"{CURL_U} $'alice:pa\\' {SEC}' https://example.invalid",
)
check(
    "lang_shell_locale_dq",
    'Shell $"..." before cu' + 'rl ' + '-u',
    f"msg=$\"users'\"; {CURL_U} 'alice:pa]-{SEC}' https://example.invalid",
)
check(
    "lang_py_raw_sq",
    "Python r'\\' before URL",
    f"[r'\\' 'https://alice:pa]-{SEC}@example.invalid/p']",
)
check(
    "lang_py_bytes_sq",
    "Python b'...' with space in password",
    f"password = b'{SEC} {SEC2}'",
    (SEC, SEC2),
)
check(
    "lang_py_fstring_repr",
    "Python f'{x!r}' before URL",
    f"f'{{x!r}} https://alice:pa]-{SEC}@example.invalid/p'",
)
check(
    "lang_js_template",
    "JS template literal `${...}` with ' inside",
    f"`users' ${{x}} 'https://alice:pa]-{SEC}@example.invalid/p'`",
)
check(
    "lang_go_raw_backtick",
    "Go raw string `...` with \\ at end",
    f"s := `C:\\`; {CURL_U} `alice:my {SEC}` https://example.invalid",
)
check(
    "lang_rust_raw_hash",
    'Rust r#"..."# containing unclosed "',
    f'let a = r#"say "hi"#; let b = ["https://alice:pa]-{SEC}@example.invalid/p"];',
)
check(
    "lang_yaml_block_indent",
    "YAML |2- indentation indicator",
    f"password: |2-\n  {SEC}\n  {SEC2}\nother: ok",
    (SEC, SEC2),
)
check(
    "lang_yaml_fold_plus",
    "YAML >+ chomping indicator",
    f"password: >+\n  {SEC}\n  {SEC2}\nother: ok",
    (SEC, SEC2),
)
check(
    "lang_toml_multiline_sq",
    "TOML ''' with \\ at end of line",
    f"password = '''{SEC} \\\n {SEC2}'''",
    (SEC, SEC2),
)
check(
    "lang_sql_doubled_sq",
    "SQL doubled '' inside IDENTIFIED BY",
    f"CREATE USER u IDENTIFIED BY 'pa'' {SEC}';",
)
check("lang_csv_doubled_dq", 'CSV doubled "" inside password=', f'password="pa"" {SEC}"')
check(
    "lang_ps_backtick_dq",
    'PowerShell `" inside double quotes',
    f'{CURL_U} "alice:pa`" {SEC}" https://example.invalid',
)
check(
    "md_double_backtick",
    "Markdown `` code span `` with ` inside",
    f"`` ` `` ['https://alice:pa]-{SEC}@example.invalid/p']",
)
check(
    "md_tilde_fence",
    "Markdown ~~~ fence",
    f"~~~bash\n{CURL_U} 'alice:{SEC}' https://example.invalid\n~~~",
)
