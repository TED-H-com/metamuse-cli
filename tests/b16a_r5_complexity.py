"""Every prior family, the reviewer additions, and the new scanner boundaries."""

import json
from urllib.parse import quote_plus

from b16a_r4_complexity import build as prior_build
from b16a_r4_complexity import families as prior_families

REVIEW = {
    "r4_layered_terminators": ("password=", "%22%2C%3B%5D%7D"),
    "r4_whatwg_authority": ("https://user:", "a:b@"),
    "r4_whatwg_nonspecial_bs": ("postgres://user:", "a\\b@"),
    "r4_cdata_run": ("<password><![CDATA[", "a]]><![CDATA["),
    "r4_xml_ns_open": ("", "<wsse:Password>"),
    "r4_har_name_value": ("", '{"name":"sid","value":"a"},'),
    "r4_har_cookies": ("", '{"cookies":[{"name":"a","value":"b"}]},'),
    "r4_sql_identified": ("", "IDENTIFIED BY 'a' "),
    "r4_sql_with_password": ("", "WITH PASSWORD 'a' "),
    "r4_sshpass_p": ("", "sshpass -p a "),
    "r4_mysql_attached_p": ("mysql ", "-pa "),
    "r4_pgp_private": ("", "-----BE" + "GIN PGP PRIVATE KEY BLOCK-----\n"),
    "r4_putty_header": ("", "Pu" + "TTY-User-Key-File-2: ssh-rsa\n"),
    "r4_brace_balanced_placeholder": ("password=${a", "{b}"),
    "r4_sk_long_body": ("sk-", "A"),
    "r4_quoted_key_subscript": ("", 'cfg["password"]="a" '),
    "r4_single_dash_flag": ("", "-token=a "),
    "r4_quoted_annotation": ("", 'api_token: "Optional[str]" = "a"\n'),
    "r4_fullwidth_colon": ("", "password：a "),
    "r4_jwt_unsigned": ("", "ey" + "JhbGciOiJub25lIn0.eyJzdWIiOiIxIn0."),
}
NEW = {
    "adjacent_authorities": "https://example.invalid,https://alice:{v}@example.invalid/a ",
    "quoted_command_layers": json.dumps(
        {"cmd": "curl " + '-u "John Doe:{v}" https://example.invalid'}
    ),
    "har_escaped_quote": json.dumps(
        {"earlier": '5" screen', "headers": [{"value": "{v}", "name": "sid"}]}
    ),
    "har_scalar": '{"value":12345,"name":"sid"} ',
    "putty_inline_json": json.dumps(
        {"key": "Pu" + "TTY-User-Key-File-2: ssh-rsa\n{v}\nPrivate-MAC: 00"}
    ),
    "exempt_nested_defaults": 'token=os.getenv("T", "password={v}") ',
    "open_brackets": "[",
    "invalid_utf8_percent": "%80",
    "nested_form_block": "q="
    + quote_plus(
        "q=" + quote_plus("-----BE" + f"GIN PRIVATE KEY-----\n{'Complex'}{4242}{'Marker'}")
    ),
    "escaped_outer_exemption": 'password=\\"<user_name=public>\\" ',
}
STD = ("k_arrow", "k_arrow_json", "vm_id", "vm_id_json")


def families():
    return (
        prior_families()
        + tuple("agy_" + k for k in REVIEW)
        + tuple("r5_" + k for k in NEW)
        + tuple("muse_std_" + k for k in STD)
        + ("r5_balanced_exempt",)
    )


def build(family, size):
    marker = f"{'Complex'}{4242}{'Marker'}"
    if family in prior_families():
        return prior_build(family, size)
    if family.startswith("muse_std_"):
        unit = "k=>" if "k_arrow" in family else "vm_id=v1&"
        text = unit * max(1, size // len(unit))
        return json.dumps({"reply": text}) if family.endswith("_json") else text
    if family == "r5_balanced_exempt":
        n = size // len("token=${X:-}")
        return "token=${X:-" * n + "$VAR" + "}" * n
    if family.startswith("agy_"):
        prefix, unit = REVIEW[family.removeprefix("agy_")]
        suffix = "}" if family.endswith("brace_balanced_placeholder") else ""
    else:
        prefix, unit, suffix = "", NEW[family.removeprefix("r5_")].replace("{v}", marker), ""
    return prefix + unit * max(1, (size - len(prefix) - len(suffix)) // len(unit)) + suffix
