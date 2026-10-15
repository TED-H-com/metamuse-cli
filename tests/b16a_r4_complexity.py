"""Iterated-call CPU growth, including every reviewer family."""

import argparse
import json
import time

from b16a_r3_complexity import UNITS as PRIOR
from b16a_r3_complexity import build as prior_build
from b16a_r3_complexity import cpu_samples

REVIEW = {
    "generic_chain_space": ("", "a=x "),
    "generic_chain_comma": ("", "a=x, "),
    "chain_unbalanced_paren": ("", "a=x) "),
    "chain_open_paren": ("", "a=(x "),
    "angle_value_run": ("", "password=<"),
    "angle_closed_run": ("", "password=<a> "),
    "lt_only_run": ("password=", "<"),
    "form_plus_run": ("?q=", "a+"),
    "form_colon_plus_run": ("q=", "password%3A+"),
    "form_eq_plus_run": ("?", "password%3D+a%3D"),
    "form_u002b_run": ("q%3D", "\\u002b"),
    "pem_begin_run": ("", "-----BE" + "GIN " + "PRIVATE KEY-----\n"),
    "pem_label_mix": (
        "",
        "-----BE" + "GIN A " + "PRIVATE KEY-----\n-----E" + "ND B PRIVATE KEY-----\n",
    ),
    "jwt_dot_run": ("", "eyJa."),
    "jwt_seg_run": ("", "eyJa.b."),
    "deep_pct_run": ("", "%2525"),
    "pct3_run": ("", "%252541"),
    "placeholder_dollar_open": ("password=", "${a"),
    "placeholder_brace_open": ("password=", "{{a"),
    "placeholder_angle_long": ("password=<", "a"),
    "backslash_run": ("", "\\\\"),
    "jesc_quote_run": ("", '\\\\\\"'),
    "u0022_run": ("", "\\u0022"),
    "u005c_run": ("", "\\u005c"),
    "dquote_run": ("", '"'),
    "squote_run": ("", "'"),
    "json_key_run": ("", '"a":'),
    "bearer_plus_run": ("", "Bearer+"),
    "bearer_nbsp_run": ("", "bearer\xa0"),
    "bearer_redacted_run": ("", "Bearer [REDACTED]"),
    "userinfo_scheme_run": ("", "a://"),
    "userinfo_at_run": ("https://a:", "@"),
    "userinfo_pct_at_run": ("https%3A%2F%2Fa%3A", "%40"),
    "query_pct_param": ("?token=", "%41"),
    "cookie_many_pairs": ("Cookie: ", "a=b; "),
    "nested_json_prose": ("see ", '{"a": {"b": '),
    "kv_eq_run": ("", "k=v="),
    "yaml_block_lines": ("password: |\n", "  a\n"),
    "yaml_key_lines": ("", "password:\n"),
    "xml_open_run": ("", "<password>"),
    "xml_attr_quote_run": ("<password ", '"'),
    "header_fold_run": ("Cookie: a", "\n b"),
    "annotation_run": ("", "password: str "),
    "local_path_nested": ("", 'local_path="token=x" '),
    "query_literal_q": ("?token=", "?"),
    "amp_token_run": ("", "&token="),
    "session_num_run": ("", "session=1 "),
    "cdata_like": ("<password>", "<![CDATA["),
    "escaped_quote_run": ('"', '\\"'),
    "sensitive_eq_run": ("", "token="),
    "url_q_semicolon": ("", "?token=;"),
    "userinfo_encoded_run": ("", "https%3A%2F%2Fa%3Ab%40"),
    "chain_bang_eq_semi": ("", "!a="),
    "many_cookie_pairs": ("", "a=1; "),
}
NEW = {
    "long_sk_body": ("s" + "k-", "A"),
    "decoded_terminator": ("password=", "%3B"),
    "form_bearer_after_q": ("Is this valid? ", "Bearer+{value} "),
    "form_flag": ("q=", "--password+{value}%20"),
    "namespace_xml": ("", "<wsse:Password>{value}</wsse:Password> "),
    "xml_cdata": ("", "<password><![CDATA[{value}]]></password> "),
    "subscript_key": ("", "cfg['password']='{value}'; "),
    "fullwidth_operator": ("", "password：{value} "),
    "har_name_value": ("", '{"name":"sid","value":"{value}"} '),
    "har_cookies": ("", '{"cookies":[{"name":"theme","value":"{value}"}]} '),
    "sql_password": ("", "ALTER USER service IDENTIFIED BY '{value}'; "),
    "mysql_password": ("", "mysql -p{value}\n"),
    "sshpass_password": ("", "sshpass -p {value} ssh host\n"),
    "putty_private": ("Pu" + "TTY-User-Key-File-3: ssh-ed25519\n", "a\n"),
    "pgp_private": ("-----BE" + "GIN PGP PRIVATE KEY BLOCK-----\n", "a\n"),
    "nested_placeholder": ("password=", "${A:-"),
    "url_query_plain_run": ("", "https://example.invalid/?password={value}&view=1 "),
    "header_decoded_newline": ("Authorization: Basic ", "a%0A"),
    "container_decoded_closer": ("password%3D%5B", "%255D"),
    "numeric_cookie_chains": ("", "a=123;b=456; "),
    "mysql_command_chain": ("", "mysql -p{value} db && mysqldump -p{value} db; "),
    "mysql_mentions": ("", "echo mysql; echo mysqldump; "),
    "curl_attached_user": ("", "curl " + "-uuser:{value} https://example.invalid/a "),
    "fragment_form_scope": ("", "https://example.invalid/#?discuss+--password+required "),
    "quoted_form_bodies": ("", '"q=--password+{value}" '),
    "canonical_form_scope": ("", "q%3D--password%2B{value}%0A"),
    "consumed_nested_assignment": ("", '"token": +https://example.invalid/password:\n{value}\n'),
}


def build(family, size):
    if family in PRIOR:
        return prior_build(family, size)
    prefix, unit = (
        REVIEW[family.removeprefix("review_")] if family.startswith("review_") else NEW[family]
    )
    unit = unit.replace("{value}", f"{'Complex'}{4242}{'Marker'}")
    suffix = ";" if family == "review_chain_bang_eq_semi" else ""
    return prefix + unit * max(1, (size - len(prefix)) // len(unit)) + suffix


def families():
    return tuple(PRIOR) + tuple("review_" + key for key in REVIEW) + tuple(NEW)


def settle_samples(text):
    from metamuse._muse_redact import sanitize_document

    values = []
    for _ in range(3):
        started = time.process_time()
        sanitize_document(text)
        values.append(time.process_time() - started)
    return min(values)


def growth(family, sizes=(16384, 32768, 65536, 131072), *, settle=False):
    measure = settle_samples if settle else cpu_samples
    samples = [measure(build(family, size)) for size in sizes]
    return samples, [b / a for a, b in zip(samples, samples[1:], strict=False)]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="16384,32768,65536,131072,57346,1048576")
    parser.add_argument("--case", choices=families())
    parser.add_argument("--settle", action="store_true")
    args = parser.parse_args()
    sizes = [int(s) for s in args.sizes.split(",")]
    for family in [args.case] if args.case else families():
        samples, _ = growth(family, sizes, settle=args.settle)
        print(
            json.dumps(
                {
                    "family": family,
                    "sizes": sizes,
                    "cpu": samples,
                    "ratios": [
                        samples[i + 1] / samples[i] for i in range(min(3, len(samples) - 1))
                    ],
                }
            ),
            flush=True,
        )
