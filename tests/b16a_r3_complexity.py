"""CPU-only complexity families ported from the reviewer, plus each new grammar."""

import gc
import json
import time

from metamuse.safety import redact_text

UNITS = {
    "local_path_value": ("", 'local_path="password={value}" '),
    "uesc_run": ('"', "\\u0061"),
    "escaped_quote_run": ('"', '\\"'),
    "escaped_squote_run": ("'", "\\'"),
    "cookie_eq_chain": ("", "a="),
    "kv_eq_run": ("", "k=v="),
    "sensitive_eq_run": ("", "token="),
    "sensitive_colon_run": ("", "password:"),
    "sensitive_colon_space_run": ("", "password: "),
    "quoted_path_unclosed": ('"', "/a"),
    "url_q_semicolon": ("", "?token=;"),
    "url_q_bang_semicolon": ("", "?token=!;"),
    "url_q_pct_semicolon": ("", "%3Ftoken%3D;"),
    "url_amp_semicolon": ("https://x.invalid/", "&token=;"),
    "userinfo_encoded_run": ("", "https%3A%2F%2Fa%3Ab%40"),
    "userinfo_encoded_nopw": ("", "https%3A%2F%2Fa%3A%40"),
    "userinfo_literal_run": ("", "https://a:b@"),
    "userinfo_colon_only": ("", "https://a:"),
    "bearer_space_run": ("", "Bearer "),
    "bearer_pct_run": ("", "Bearer%20"),
    "bearer_word_run": ("", "bearer bearerXX "),
    "long_encoded_param": ("?token=", "%20"),
    "many_cookie_pairs": ("", "a=1; "),
    "cookie_header_pairs": ("Cookie: ", "a=b; "),
    "cookie_header_eq": ("Cookie: ", "a="),
    "cookie_header_multiline": ("Cookie: x=y", "\n a=b"),
    "json_many_unclosed": ("", '"k": "bbbbbbbbbb'),
    "json_password_unclosed_run": ("", '{"password": "x'),
    "json_nested_prose": ("note ", '{"a": '),
    "json_escaped_value_run": ('{"password": "', "\\u0041"),
    "encoded_json_unclosed": ("", "%22a%22%3A%22b"),
    "encoded_json_mixed_layers": (
        "",
        "%22a%22%3A%22b%2522a%2522%3A%2522b%252522a%252522%3A%252522b",
    ),
    "encoded_key_long": ("%22", "a"),
    "headers_authz_run": ("", "Authorization: x "),
    "authz_bearer_hdr_run": ("", "Authorization: Bearer "),
    "set_cookie_attr_run": ("Set-Cookie: sid=x", "; Path=/"),
    "space_pct_run": ("password", "%20"),
    "assign_space_run": ("password", " "),
    "sk_run": ("", "sk-"),
    "path_slash_run": ("", "/a"),
    "path_unclosed_alt_quotes": ("", "\"/a/'/a/"),
    "plain": ("", "a"),
    "long_scheme": ("", "abc1234"),
    "chain_bang_eq_semi": ("", "!a="),
    "chain_at_eq_semi": ("", "@a="),
    "chain_control": ("", "!a=b "),
    "prefixes": ("", "gh" + "p_{value} "),
    "jwt": ("", "{jwt} "),
    "pem": ("", "{pem}\n"),
    "bearer": ("", "Bearer {value} "),
    "cookie_jar": ("", "domain.invalid\tTRUE\t/\tFALSE\t0\tsid\t{value}\n"),
    "json_escaped_doc": ("", "{escaped}\n"),
    "xml": ("", "<password>{value}</password> "),
    "yaml": ("", "password:\n  nested:\n    value: {value}\n"),
    "flag": ("", "--password {value} "),
    "flag_D": ("", "-Dpassword={value} "),
    "curl": ("", "curl " + "-u user:{value} "),
    "form": ("", "msg=login+password%3D{value}&view=1 "),
}


def build(case, n):
    value = f"{'Synthetic'}{123456789}{'Marker'}{'AbCdEfGhIjKlMn'}"
    jwt = "ey" + "JhbGciOiJIUzI1NiJ9." + value + "." + value
    pem = "-----BE" + "GIN PRIVATE KEY-----\n" + value + "\n-----E" + "ND PRIVATE KEY-----"
    escaped = json.dumps(json.dumps({"password": value}))
    prefix, unit = UNITS[case]
    unit = (
        unit.replace("{value}", value[:36] if case == "prefixes" else value)
        .replace("{jwt}", jwt)
        .replace("{pem}", pem)
        .replace("{escaped}", escaped)
    )
    suffix = ";" if case in {"chain_bang_eq_semi", "chain_at_eq_semi", "chain_control"} else ""
    return prefix + unit * max(1, (n - len(prefix)) // len(unit)) + suffix


def cpu_samples(text, reps=3):
    redact_text(text[:128])
    enabled = gc.isenabled()
    gc.disable()
    try:
        samples = []
        for _ in range(reps):
            started = time.process_time()
            redact_text(text)
            samples.append(time.process_time() - started)
        return min(samples)
    finally:
        if enabled:
            gc.enable()


def growth(case, sizes=(16384, 32768, 65536, 131072)):
    samples = [cpu_samples(build(case, size)) for size in sizes]
    return samples, [later / earlier for earlier, later in zip(samples, samples[1:], strict=False)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", default="16384,32768,65536,131072,57344,1048576")
    parser.add_argument("--case", choices=tuple(UNITS))
    args = parser.parse_args()
    for case in [args.case] if args.case else UNITS:
        for size in map(int, args.sizes.split(",")):
            text = build(case, size)
            print(
                json.dumps(
                    {"family": case, "size": size, "chars": len(text), "cpu": cpu_samples(text)}
                ),
                flush=True,
            )
