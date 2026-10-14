"""Ported independent-review corpus; generated synthetic values, no runtime scratch imports."""

import base64
from urllib.parse import quote


def marker(name: str) -> str:
    return "SYNTH" + "q" + name.replace("-", "").replace("_", "") + "q" + "VAL" + "4242"


def leak_matrix():
    rows = []

    def add(name, template, value=None, encode=False):
        value = marker(name) if value is None else value
        text = template.format(v=value)
        rows.append((name, quote(text, safe="") if encode else text, value))

    # Value-shape gate: credential values with ordinary password punctuation.
    add("assign_bang", "password={v}!")
    add("assign_amp", "db_password={v}&3")
    add("assign_dollar", "PGPASSWORD={v}$")
    add("assign_star", "api_key={v}*x")
    add("assign_hash", "client_secret={v}#1")
    add("assign_caret", "access_token={v}^x")
    add("assign_nonascii", "password={v}é")
    add("json_bang", 'cfg {{"password": "{v}!"}} end')
    add("json_space", 'cfg {{"password": "{v} two words"}} end')
    add("json_paren", 'cfg {{"client_secret": "{v}(x)"}} end')
    add("toml_bang", 'password = "{v}!"')
    add("yaml_colon_bang", "password: {v}!")
    add("env_export_bang", "export API_TOKEN='{v}!x'")
    add("url_query_bang", "https://example.invalid/cb?access_token={v}!x&view=1")
    # Authorization and cookie headers.
    basic = base64.b64encode(("admin:" + "s3" + "cret").encode()).decode()
    add("authz_basic_b64", "Authorization: Basic {v}", basic)
    add("authz_basic_marker", "Authorization: Basic {v}")
    add("json_authz_basic", '{{"Authorization": "Basic {v}"}}')
    add("cookie_opaque", "Cookie: {v}")
    add("cookie_colon_lower", "cookie: {v}")
    add("set_cookie_opaque", "Set-Cookie: {v}")
    add("proxy_authz", "Proxy-Authorization: Basic {v}")
    # Handoff shapes required by the round-2 decisions (bare Bearer, userinfo, colon, JSON).
    add("bearer_plus", "Bearer+{v}")
    add("bearer_after_pct20", "x%20Bearer%20{v}")
    add("authz_bearer_pct", "Authorization%3A%20Bearer%20{v}")
    add("authz_bearer_pct2", "Authorization%253A%2520Bearer%2520{v}")
    add("bearer_json_u0020", 'auth "Bearer\\u0020{v}"')
    add("bearer_nbsp", "Bearer {v}")
    add("ui_postgres", "postgres://app:{v}@db.example.invalid:5432/app")
    add("ui_redis_empty_user", "redis://:{v}@cache.example.invalid:6379/0")
    add("ui_empty_user", "https://:{v}@example.invalid/")
    add("ui_special_pw", "https://user:{v}!x@example.invalid/")
    add("ui_jsonesc", "https:\\/\\/user:{v}@example.invalid\\/")
    add("ui_pct_special", "https://user:{v}!x@example.invalid/", encode=True)
    add("colon_passwd", "passwd: {v}")
    add("colon_pwd", "pwd: {v}")
    add("colon_pass", "pass: {v}")
    add("colon_x_api_key", "x-api-key: {v}")
    add("colon_quoted_key", '"password": {v}')
    add("json_array", '{{"password": ["{v}"]}}')
    add("json_escaped_doc", '{{"body": "{{\\"password\\": \\"{v}\\"}}"}}')
    add("json_truncated", 'log: {{"password": "{v}')
    add("json_key_dash", '{{"x-api-key": "{v}"}}')
    add("json_pct", 'note {{"password": "{v}"}} end', encode=True)
    # Controls that should be protected (sanity).
    add("ctl_assign", "password={v}")
    add("ctl_bearer", "Bearer {v}")
    add("ctl_userinfo", "https://user:{v}@example.invalid/")
    add("ctl_colon", "password: {v}")
    add("ctl_json", 'cfg {{"password": "{v}"}} end')
    add("ctl_cookie", "Cookie: theme=dark; sid={v}")
    return rows


def mk(tag):
    return "SYN" + "TH" + "q" + tag + "q" + "VAL" + str(4242)


def enc(s, n):
    for _ in range(n):
        s = quote(s, safe="")
    return s


HANDOFF = []


def add(name, template):
    v = mk(name[:20].replace(" ", "_"))
    HANDOFF.append((name, template.replace("{v}", v), v))


# Bearer family
add("bearer_space", "Bearer {v}")
add("bearer_tab", "Bearer\t{v}")
add("bearer_pct20", "Bearer%20{v}")
add("bearer_pct2520", "Bearer%2520{v}")
add("bearer_plus", "Bearer+{v}")
add("bearer_after_pct20", "x%20Bearer%20{v}")
add("authz_hdr_pct", "Authorization%3A%20Bearer%20{v}")
add("authz_hdr_pct_plus", "Authorization%3A+Bearer+{v}")
add("authz_hdr_pct2", enc("Authorization: Bearer ", 2) + "{v}")
add("authz_hdr_pct3", enc("Authorization: Bearer ", 3) + "{v}")
add("bearer_json_u0020", 'auth "Bearer\\u0020{v}"')
add("bearer_after_digit", "hdr1Bearer {v}")
add("bearer_in_json_value", '{"auth": "Bearer {v}"}')
add("bearer_query_param", "https://example.invalid/?auth=Bearer%20{v}")
add("bearer_query_param_plus", "https://example.invalid/?auth=Bearer+{v}")
add("bearer_curl_pct", "curl%20-H%20%22Authorization%3A%20Bearer%20{v}%22")
add("bearer_nbsp", "Bearer {v}")
add("bearer_two_spaces", "Bearer  {v}")
add("bearer_newline", "Bearer\n{v}")
add("bearer_token68_equals", "Bearer {v}==")
add("proxy_authz_basic", "Proxy-Authorization: Basic {v}")
add("authz_basic", "Authorization: Basic {v}")
add("authz_basic_pct", "Authorization%3A%20Basic%20{v}")
# userinfo family
add("ui_https", "https://user:{v}@example.invalid/")
add("ui_http", "http://user:{v}@example.invalid/")
add("ui_empty_user", "https://:{v}@example.invalid/")
add("ui_special_pw", "https://user:{v}!x@example.invalid/")
add("ui_amp_pw", "https://user:{v}&x@example.invalid/")
add("ui_user_special", "https://us$er:{v}@example.invalid/")
add("ui_user_bang", "https://first!last:{v}@example.invalid/")
add("ui_git_https", "git+https://user:{v}@example.invalid/r.git")
add("ui_ftp", "ftp://user:{v}@example.invalid/")
add("ui_postgres", "postgres://app:{v}@db.example.invalid:5432/app")
add("ui_postgresql", "postgresql://app:{v}@db.example.invalid/app")
add("ui_mysql", "mysql://root:{v}@db.example.invalid/x")
add("ui_redis_empty_user", "redis://:{v}@cache.example.invalid:6379/0")
add("ui_amqp", "amqp://guest:{v}@mq.example.invalid/")
add("ui_mongodb_srv", "mongodb+srv://u:{v}@cluster.example.invalid/db")
add("ui_ssh", "ssh://git:{v}@example.invalid/")
add("ui_pct1", enc("https://user:", 1) + "{v}" + enc("@example.invalid/", 1))
add("ui_pct2", enc("https://user:", 2) + "{v}" + enc("@example.invalid/", 2))
add("ui_pct3", enc("https://user:", 3) + "{v}" + enc("@example.invalid/", 3))
add(
    "ui_in_next",
    "https://example.invalid/?next="
    + enc("https://user:", 1)
    + "{v}"
    + enc("@example.invalid/", 1),
)
add("ui_no_slash_end", "see https://user:{v}@example.invalid")
add("ui_quoted", '"https://user:{v}@example.invalid"')
add("ui_pw_has_slash", "https://user:{v}/x@example.invalid/")
add("ui_pw_has_hash", "https://user:{v}#x@example.invalid/")
add("ui_pw_has_question", "https://user:{v}?x@example.invalid/")
add("ui_pw_pct40", "https://user:{v}%40x@example.invalid/")
add("ui_long_user", "https://" + "u" * 400 + ":{v}@example.invalid/")
add("ui_backslash_jsonesc", "https:\\/\\/user:{v}@example.invalid\\/")
# colon forms
for name in [
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "session",
    "cookie",
    "set-cookie",
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "client_secret",
    "hatch_sess",
    "private_key",
    "x-api-key",
    "x-auth-token",
    "auth",
    "credentials",
    "pass",
]:
    add("colon " + name, name + ": {v}")
add("colon_space_before", "password : {v}")
add("colon_tab", "password:\t{v}")
add("colon_quoted_key_unquoted_val", '"password": {v}')
add("colon_single_quoted_val", "password: '{v}'")
add("colon_pct", "password%3A%20{v}")
add("colon_pct2", enc("password: ", 2) + "{v}")
add("colon_pct3", enc("password: ", 3) + "{v}")
add("colon_yaml_indent", "db:\n  password: {v}\n")
add("colon_yaml_list", "- password: {v}")
add("colon_trailing_comma", "password: {v},")
add("colon_after_digit", "user1password: {v}")
add("colon_value_numeric", f"password: {1234}{5678}")
add("colon_ini_eq", "password = {v}")
add("toml_single", "password = '{v}'")
add("env_export", "export PASSWORD={v}")
add("env_quoted", 'DB_PASSWORD="{v}"')
add("xml_element", "<password>{v}</password>")
add("cli_flag_eq", "--password={v}")
add("cli_flag_space", "--password {v}")
add("cli_D", "-Dpassword={v}")
add("cli_curl_user", "curl " + "-u admin:{v} https://example.invalid/")
# JSON in prose
add("json_basic", 'note {"password": "{v}"} end')
add("json_compact", '{"password":"{v}"}')
add("json_spaced", '{ "password" : "{v}" }')
add("json_single", "{'password': '{v}'}")
add("json_nested", '{"db": {"password": "{v}"}}')
add("json_numeric", '{"password": 12345678}')
add("json_array", '{"password": ["{v}"]}')
add("json_escaped_doc", '{"body": "{\\"password\\": \\"{v}\\"}"}')
add("json_escaped_bare", 'payload={\\"password\\":\\"{v}\\"}')
add("json_unicode_key", '{"pass\\u0077ord": "{v}"}')
add("json_unicode_value", '{"password": "\\u0041{v}"}')
add("json_pct", "%7B%22password%22%3A%22{v}%22%7D")
add("json_pct2", enc('{"password":"', 2) + "{v}" + enc('"}', 2))
add("json_pct_space", "%7B%22password%22%3A%20%22{v}%22%7D")
add("json_truncated", 'log: {"password": "{v}')
add("json_key_upper", '{"PASSWORD": "{v}"}')
add("json_key_dash", '{"x-api-key": "{v}"}')
add("json_sk", '{"key": "sk-{v}"}')
add("json_newline_in_value", '{"password": "a\n{v}"}')
add("json_cookie_header_obj", '{"headers": {"Cookie": "sid={v}"}}')
add("json_authorization", '{"Authorization": "Bearer {v}"}')
add("json_authorization_basic", '{"Authorization": "Basic {v}"}')
add("json_set_cookie_list", '{"set-cookie": ["sid={v}; Path=/"]}')
# cookies
add("cookie_hdr", "Cookie: sid={v}; theme=dark")
add("cookie_hdr_multiline", "Cookie: theme=dark;\n sid={v}")
add("set_cookie_attrs", "Set-Cookie: sid={v}; Path=/; Secure; HttpOnly")
add("set_cookie_expires_comma", "Set-Cookie: sid={v}; Expires=Wed, 21 Oct 2026 07:28:00 GMT")
add("cookie_chain_bare", "theme=dark; sid={v}")
add("cookie_chain_custom", "theme=dark; foo={v}")
add("cookie_numeric_member", "theme=dark; sid=123456789")
add("cookie_chain_comma", "a=1, sid={v}")
add("cookie_chain_quoted", 'theme="dark"; sid="{v}"')
add("cookie_hdr_lower_no_space", "cookie:sid={v}")
add("cookie_opaque", "Cookie: {v}")
add("set_cookie_opaque", "Set-Cookie: {v}")
# token prefixes (value-based, built by interpolation)
add("sk_prefix", "key sk-" + "proj" + "-{v}")
add("ghp_prefix", "ghp" + "_" + "{v}" + "abcdefghij")
add("xoxb_prefix", "xox" + "b-" + "{v}")
add("aws_akid", "AKIA" + "{v}")
add("jwt", "ey" + "J" + "hbGciOiJIUzI1NiJ9." + "{v}" + ".sig")
add("glpat", "glpat" + "-{v}")
add("stripe_sk_live", "sk" + "_live_" + "{v}")
add("google_aiza", "AIza" + "{v}")


REVIEW_BENIGN2 = {
    "py_type_hint": "def login(user: str, password: str) -> bool:",
    "py_type_hint_optional": "token: Optional[str] = None",
    "ts_interface": "interface Creds { password: string; token: string }",
    "py_environ": 'token = os.environ["GITHUB_TOKEN"]',
    "py_settings_attr": "token = settings.API_TOKEN",
    "py_config_index": 'secret = config["secret"]',
    "py_self_attr": "self.session = session",
    "py_kwarg_passthrough": "client = Client(token=token)",
    "py_kwarg_attr": "requests.get(url, auth=auth, cookies=session.cookies)",
    "hf_pad_eos": "tokenizer.pad_token = tokenizer.eos_token",
    "hf_sep_token": 'sep_token="[SEP]"',
    "js_cookie_read": "const session = req.cookies.session;",
    "shell_env_ref": 'export GITHUB_TOKEN="$TOKEN"',
    "shell_env_ref2": "password=$DB_PASSWORD",
    "yaml_secret_ref": "password: ${{ secrets.DB_PASSWORD }}",
    "k8s_secret_ref": "password:\n  valueFrom:\n    secretKeyRef:",
    "placeholder_angle": "password=<your-password>",
    "placeholder_redacted": "password: REDACTED",
    "placeholder_xxx": "api_key=xxxxxxxx",
    "placeholder_changeme": "password: changeme",
    "google_pageToken": "GET /v1/items?pageToken=CAUQAA&pageSize=50",
    "google_page_token_py": "service.list(page_token=next_token)",
    "aws_NextToken": '{"NextToken": "AAAAAQ", "Items": []}',
    "azure_continuationToken": '{"continuationToken": "c2VjcmV0"}',
    "next_token_query": "/api/list?next_token=abc123",
    "sync_token": "syncToken=CPDAlvWDx70CEPDAlvWDx70CGAE",
    "max_new_tokens": "max_new_tokens=512",
    "max_tokens_json": '{"max_tokens": 1024}',
    "num_tokens_prose": "num_tokens: 4096",
    "prompt_tokens": '{"usage": {"prompt_tokens": 12, "completion_tokens": 30}}',
    "special_tokens": 'tokenizer.add_special_tokens({"pad_token": "[PAD]"})',
    "eos_token_id": "eos_token_id=2",
    "agenda_session": "Session: Opening keynote",
    "agenda_session2": "Session: 2B, Room 4",
    "prose_password_required": "Password: required",
    "prose_token_erc": "Token: ERC-20 standard",
    "prose_secret_colon": "Secret: the meeting moved",
    "prose_cookie_type": "Cookie: chocolate chip",
    "prose_authorization_colon": "Authorization: required for this endpoint",
    "prose_authorization_basic": "Authorization: Basic or Bearer?",
    "hdr_prose_setcookie": "Set-Cookie: header has attributes",
    "hdr_prose_auth_scheme": "Authorization: Bearer",
    "session_counter_neg": "active_session=-1",
    "session_counter_pct": "session=50%",
    "session_year": "academic_session=2026",
    "fall_session": "academic_session=fall2026",
    "abs_path": "/home/alice/notes.txt",
    "url_plain": "https://example.invalid/docs?page=2",
    "url_session_query": "https://example.invalid/?session=morning",
    "bearer_prose": "The bearer instrument was signed",
    "bearer_capital": "Bearer bonds are risky investments",
    "bearer_longword": "Bearer instruments",
}


def _seed(template, pieces):
    return template.format(*(f"{''.join(parts)}" for parts in pieces))


def differential_seed():
    return [
        _seed(
            "Please review: {0}={1}!", [("pass", "word"), ("SYNTHq", "assign", "bangqV", "AL4242")]
        ),
        _seed(
            "Please review: {0}={1}&3",
            [("db_pa", "ssword"), ("SYNTHq", "assig", "nampqV", "AL4242")],
        ),
        _seed(
            "Please review: {0}={1}$",
            [("PGPAS", "SWORD"), ("SYNTHq", "assign", "d", "ollarq", "VAL424", "2")],
        ),
        _seed("Please review: api_key={0}*x", [("SYNTHq", "assign", "starqV", "AL4242")]),
        _seed(
            "Please review: {0}={1}#1",
            [("client", "_secre", "t"), ("SYNTHq", "assign", "hashqV", "AL4242")],
        ),
        _seed(
            "Please review: {0}={1}^x",
            [("access", "_token"), ("SYNTHq", "assign", "caretq", "VAL424", "2")],
        ),
        _seed(
            "Please review: {0}={1}é",
            [("pass", "word"), ("SYNTHq", "assign", "no", "nascii", "qVAL42", "42")],
        ),
        _seed(
            'Please review: cfg {{"{0}": "{1}!"}} end',
            [("pass", "word"), ("SYNTHq", "jsonb", "angqVA", "L4242")],
        ),
        _seed(
            'Please review: cfg {{"{0}": "{1} two words"}} end',
            [("pass", "word"), ("SYNTHq", "jsons", "paceqV", "AL4242")],
        ),
        _seed(
            'Please review: cfg {{"{0}": "{1}(x)"}} end',
            [("client", "_secre", "t"), ("SYNTHq", "jsonp", "arenqV", "AL4242")],
        ),
        _seed(
            'Please review: {0} = "{1}!"',
            [("pass", "word"), ("SYNTHq", "tomlb", "angqVA", "L4242")],
        ),
        _seed(
            "Please review: {0}: {1}!",
            [("pass", "word"), ("SYNTHq", "yamlco", "l", "onbang", "qVAL42", "42")],
        ),
        _seed(
            "Please review: export {0}='{1}!x'",
            [("API_", "TOKEN"), ("SYNTHq", "envexp", "o", "rtbang", "qVAL42", "42")],
        ),
        _seed(
            "Please review: https://example.invalid/cb?{0}={1}!x&view=1",
            [("access", "_token"), ("SYNTHq", "urlque", "r", "ybangq", "VAL424", "2")],
        ),
        _seed(
            "Please review: {0}: Basic {1}",
            [("Author", "izatio", "n"), ("YWRtaW", "46", "czNjcm", "V0")],
        ),
        _seed(
            "Please review: {0}: Basic {1}",
            [("Author", "izatio", "n"), ("SYNTHq", "authzb", "asi", "cmarke", "rqVAL4", "242")],
        ),
        _seed(
            'Please review: {{"{0}": "Basic {1}"}}',
            [("Author", "izatio", "n"), ("SYNTHq", "jsonau", "th", "zbasic", "qVAL42", "42")],
        ),
        _seed("Please review: Cookie: {0}", [("SYNTHq", "cookie", "o", "paqueq", "VAL424", "2")]),
        _seed(
            "Please review: cookie: {0}", [("SYNTHq", "cookie", "col", "onlowe", "rqVAL4", "242")]
        ),
        _seed(
            "Please review: {0}: {1}",
            [("Set-C", "ookie"), ("SYNTHq", "setcoo", "ki", "eopaqu", "eqVAL4", "242")],
        ),
        _seed(
            "Please review: {0}: Basic {1}",
            [("Proxy-", "Aut", "horiza", "tion"), ("SYNTHq", "proxya", "uthzqV", "AL4242")],
        ),
        _seed("Please review: Bearer+{0}", [("SYNTHq", "bearer", "plusqV", "AL4242")]),
        _seed(
            "Please review: x%{0}%{1}",
            [("20Be", "arer"), ("20SYNT", "Hqbear", "eraf", "terpct", "20qVAL", "4242")],
        ),
        _seed(
            "Please review: {0}%3A%{1}%{2}",
            [
                ("Author", "izatio", "n"),
                ("20Be", "arer"),
                ("20SYNT", "Hqauth", "zbe", "arerpc", "tqVAL4", "242"),
            ],
        ),
        _seed(
            "Please review: {0}%253A%{1}%{2}",
            [
                ("Author", "izatio", "n"),
                ("2520B", "earer"),
                ("2520SY", "NTHqau", "thzb", "earerp", "ct2qVA", "L4242"),
            ],
        ),
        _seed(
            'Please review: auth "Bearer\\{0}"',
            [("u0020S", "YNTHqb", "earer", "jsonu0", "020qVA", "L4242")],
        ),
        _seed("Please review: Bearer\xa0{0}", [("SYNTHq", "bearer", "nbspqV", "AL4242")]),
        _seed(
            "Please review: {0}://app:{1}@db.example.invalid:5432/app",
            [("post", "gres"), ("SYNTHq", "uipost", "gresqV", "AL4242")],
        ),
        _seed(
            "Please review: redis://:{0}@cache.example.invalid:6379/0",
            [("SYNTHq", "uiredi", "sem", "ptyuse", "rqVAL4", "242")],
        ),
        _seed(
            "Please review: https://:{0}@example.invalid/",
            [("SYNTHq", "uiempt", "yuserq", "VAL424", "2")],
        ),
        _seed(
            "Please review: https://user:{0}!x@example.invalid/",
            [("SYNTHq", "uispec", "ialpwq", "VAL424", "2")],
        ),
        _seed(
            "Please review: https:\\/\\/user:{0}@example.invalid\\/",
            [("SYNTHq", "uijso", "nescqV", "AL4242")],
        ),
        _seed(
            "Please review: https%3A%2F%2Fuser%{0}%21x%{1}.invalid%2F",
            [("3ASYNT", "Hquipc", "ts", "pecial", "qVAL42", "42"), ("40ex", "ample")],
        ),
        _seed("Please review: passwd: {0}", [("SYNTHq", "colonp", "asswdq", "VAL424", "2")]),
        _seed("Please review: pwd: {0}", [("SYNTHq", "colon", "pwdqVA", "L4242")]),
        _seed("Please review: pass: {0}", [("SYNTHq", "colon", "passqV", "AL4242")]),
        _seed(
            "Please review: {0}: {1}",
            [("x-ap", "i-key"), ("SYNTHq", "colonx", "a", "pikeyq", "VAL424", "2")],
        ),
        _seed(
            'Please review: "{0}": {1}',
            [("pass", "word"), ("SYNTHq", "colonq", "uo", "tedkey", "qVAL42", "42")],
        ),
        _seed(
            'Please review: {{"{0}": ["{1}"]}}',
            [("pass", "word"), ("SYNTHq", "jsona", "rrayqV", "AL4242")],
        ),
        _seed(
            'Please review: {{"body": "{{\\"{0}\\": \\"{1}\\"}}"}}',
            [("pass", "word"), ("SYNTHq", "jsones", "ca", "peddoc", "qVAL42", "42")],
        ),
        _seed(
            'Please review: log: {{"{0}": "{1}',
            [("pass", "word"), ("SYNTHq", "jsontr", "u", "ncated", "qVAL42", "42")],
        ),
        _seed(
            'Please review: {{"{0}": "{1}"}}',
            [("x-ap", "i-key"), ("SYNTHq", "jsonke", "ydashq", "VAL424", "2")],
        ),
        _seed(
            "Please review: note%20%7B%{0}%22%3A%20%{1}%22%7D%20end",
            [("22pas", "sword"), ("22SYNT", "Hqjso", "npctqV", "AL4242")],
        ),
        _seed(
            "Please review: {0}={1}", [("pass", "word"), ("SYNTHq", "ctlas", "signqV", "AL4242")]
        ),
        _seed("Please review: Bearer {0}", [("SYNTHq", "ctlbe", "arerqV", "AL4242")]),
        _seed(
            "Please review: https://user:{0}@example.invalid/",
            [("SYNTHq", "ctluse", "rinfoq", "VAL424", "2")],
        ),
        _seed(
            "Please review: {0}: {1}", [("pass", "word"), ("SYNTHq", "ctlco", "lonqVA", "L4242")]
        ),
        _seed(
            'Please review: cfg {{"{0}": "{1}"}} end',
            [("pass", "word"), ("SYNTHq", "ctlj", "sonqVA", "L4242")],
        ),
        _seed(
            "Please review: Cookie: theme=dark; sid={0}", [("SYNTHq", "ctlco", "okieqV", "AL4242")]
        ),
        _seed("def login(user: str, {0}: str) -> bool:", [("pass", "word")]),
        _seed("token: {0}[str] = None", [("Opti", "onal")]),
        _seed("{0} Creds {{ {1}: string; token: string }}", [("inte", "rface"), ("pass", "word")]),
        _seed('token = os.environ["{0}"]', [("GITHUB", "_TOKEN")]),
        _seed("token = {0}.{1}", [("sett", "ings"), ("API_", "TOKEN")]),
        _seed('secret = config["secret"]', []),
        _seed("self.session = session", []),
        _seed("client = Client(token=token)", []),
        _seed("{0}.get(url, auth=auth, cookies=session.cookies)", [("requ", "ests")]),
        _seed(
            "{0}.{1} = {2}.{3}",
            [("toke", "nizer"), ("pad_", "token"), ("toke", "nizer"), ("eos_", "token")],
        ),
        _seed('{0}="[SEP]"', [("sep_", "token")]),
        _seed("const session = req.cookies.session;", []),
        _seed('export {0}="$TOKEN"', [("GITHUB", "_TOKEN")]),
        _seed("{0}=${1}", [("pass", "word"), ("DB_PA", "SSWORD")]),
        _seed("{0}: ${{{{ secrets.{1} }}}}", [("pass", "word"), ("DB_PA", "SSWORD")]),
        _seed(
            "{0}:\n  {1}:\n    {2}:", [("pass", "word"), ("valu", "eFrom"), ("secret", "KeyRef")]
        ),
        _seed("{0}=<{1}>", [("pass", "word"), ("your-p", "asswor", "d")]),
        _seed("{0}: {1}", [("pass", "word"), ("REDA", "CTED")]),
        _seed("api_key={0}", [("xxxx", "xxxx")]),
        _seed("{0}: {1}", [("pass", "word"), ("chan", "geme")]),
        _seed("GET /v1/items?{0}=CAUQAA&{1}=50", [("page", "Token"), ("page", "Size")]),
        _seed("service.list({0}={1})", [("page_", "token"), ("next_", "token")]),
        _seed('{{"{0}": "AAAAAQ", "Items": []}}', [("Next", "Token")]),
        _seed('{{"{0}": "{1}"}}', [("contin", "ua", "tionTo", "ken"), ("c2Vj", "cmV0")]),
        _seed("/api/list?{0}=abc123", [("next_", "token")]),
        _seed("{0}={1}", [("sync", "Token"), ("CPDAlv", "WDx70C", "E", "PDAlvW", "Dx70CG", "AE")]),
        _seed("{0}=512", [("max_ne", "w", "_token", "s")]),
        _seed('{{"{0}": 1024}}', [("max_t", "okens")]),
        _seed("{0}: 4096", [("num_t", "okens")]),
        _seed(
            '{{"usage": {{"{0}": 12, "{1}": 30}}}}',
            [("prompt", "_token", "s"), ("comple", "ti", "on_tok", "ens")],
        ),
        _seed(
            '{0}.{1}({{"{2}": "[PAD]"}})',
            [("toke", "nizer"), ("add_sp", "eci", "al_tok", "ens"), ("pad_", "token")],
        ),
        _seed("{0}=2", [("eos_to", "ken_id")]),
        _seed("Session: Opening keynote", []),
        _seed("Session: 2B, Room 4", []),
        _seed("{0}: {1}", [("Pass", "word"), ("requ", "ired")]),
        _seed("Token: ERC-20 {0}", [("stan", "dard")]),
        _seed("Secret: the meeting moved", []),
        _seed("Cookie: {0} chip", [("choc", "olate")]),
        _seed(
            "{0}: {1} for this {2}", [("Author", "izatio", "n"), ("requ", "ired"), ("endp", "oint")]
        ),
        _seed("{0}: Basic or Bearer?", [("Author", "izatio", "n")]),
        _seed("{0}: header has {1}", [("Set-C", "ookie"), ("attri", "butes")]),
        _seed("{0}: Bearer", [("Author", "izatio", "n")]),
        _seed("{0}=-1", [("active", "_", "sessio", "n")]),
        _seed("session=50%", []),
        _seed("{0}=2026", [("academ", "ic", "_sessi", "on")]),
        _seed("{0}={1}", [("academ", "ic", "_sessi", "on"), ("fall", "2026")]),
        _seed("/home/alice/notes.txt", []),
        _seed("https://example.invalid/docs?page=2", []),
        _seed("https://example.invalid/?session=morning", []),
        _seed("The bearer {0} was signed", [("instr", "ument")]),
        _seed("Bearer bonds are risky {0}", [("inves", "tments")]),
        _seed("Bearer {0}", [("instr", "uments")]),
        _seed("Bearer {0}", [("SYNTHq", "bearer", "_", "spaceq", "VAL424", "2")]),
        _seed("Bearer\t{0}", [("SYNTHq", "bearer", "_tabqV", "AL4242")]),
        _seed("Bearer%{0}", [("20SYNT", "Hqbear", "er", "_pct20", "qVAL42", "42")]),
        _seed("Bearer%{0}", [("2520SY", "NTHqbe", "arer", "_pct25", "20qVAL", "4242")]),
        _seed("Bearer+{0}", [("SYNTHq", "bearer", "_plusq", "VAL424", "2")]),
        _seed(
            "x%{0}%{1}",
            [("20Be", "arer"), ("20SYNT", "Hqbear", "er_af", "ter_pc", "t20qVA", "L4242")],
        ),
        _seed(
            "{0}%3A%{1}%{2}",
            [
                ("Author", "izatio", "n"),
                ("20Be", "arer"),
                ("20SYNT", "Hqauth", "z_", "hdr_pc", "tqVAL4", "242"),
            ],
        ),
        _seed(
            "{0}%3A+Bearer+{1}",
            [("Author", "izatio", "n"), ("SYNTHq", "authz_", "hdr_", "pct_pl", "usqVAL", "4242")],
        ),
        _seed(
            "{0}%253A%{1}%{2}",
            [
                ("Author", "izatio", "n"),
                ("2520B", "earer"),
                ("2520SY", "NTHqau", "thz_", "hdr_pc", "t2qVAL", "4242"),
            ],
        ),
        _seed(
            "{0}%25253A%{1}%{2}",
            [
                ("Author", "izatio", "n"),
                ("252520", "Bearer"),
                ("252520", "SYNTHq", "authz", "_hdr_p", "ct3qVA", "L4242"),
            ],
        ),
        _seed('auth "Bearer\\{0}"', [("u0020S", "YNTHqb", "earer_", "json_u", "0020qV", "AL4242")]),
        _seed(
            "{0} {1}",
            [("hdr1B", "earer"), ("SYNTHq", "bearer", "_aft", "er_dig", "itqVAL", "4242")],
        ),
        _seed(
            '{{"auth": "Bearer {0}"}}', [("SYNTHq", "bearer", "_in_j", "son_va", "lueqVA", "L4242")]
        ),
        _seed(
            "https://example.invalid/?auth=Bearer%{0}",
            [("20SYNT", "Hqbear", "er_qu", "ery_pa", "ramqVA", "L4242")],
        ),
        _seed(
            "https://example.invalid/?auth=Bearer+{0}",
            [("SYNTHq", "bearer", "_quer", "y_para", "m_pqVA", "L4242")],
        ),
        _seed(
            "curl%20-H%20%{0}%3A%{1}%{2}%22",
            [
                ("22Auth", "o", "rizati", "on"),
                ("20Be", "arer"),
                ("20SYNT", "Hqbear", "er_", "curl_p", "ctqVAL", "4242"),
            ],
        ),
        _seed("Bearer\xa0{0}", [("SYNTHq", "bearer", "_nbspq", "VAL424", "2")]),
        _seed("Bearer  {0}", [("SYNTHq", "bearer", "_tw", "o_spac", "esqVAL", "4242")]),
        _seed("Bearer\n{0}", [("SYNTHq", "bearer", "_n", "ewline", "qVAL42", "42")]),
        _seed("Bearer {0}==", [("SYNTHq", "bearer", "_toke", "n68_eq", "ualqVA", "L4242")]),
        _seed(
            "{0}: Basic {1}",
            [
                ("Proxy-", "Aut", "horiza", "tion"),
                ("SYNTHq", "proxy_", "aut", "hz_bas", "icqVAL", "4242"),
            ],
        ),
        _seed(
            "{0}: Basic {1}",
            [("Author", "izatio", "n"), ("SYNTHq", "authz_", "basicq", "VAL424", "2")],
        ),
        _seed(
            "{0}%3A%20Basic%{1}",
            [("Author", "izatio", "n"), ("20SYNT", "Hqauth", "z_b", "asic_p", "ctqVAL", "4242")],
        ),
        _seed("https://user:{0}@example.invalid/", [("SYNTHq", "ui_ht", "tpsqVA", "L4242")]),
        _seed("http://user:{0}@example.invalid/", [("SYNTHq", "ui_h", "ttpqVA", "L4242")]),
        _seed(
            "https://:{0}@example.invalid/", [("SYNTHq", "ui_emp", "t", "y_user", "qVAL42", "42")]
        ),
        _seed(
            "https://user:{0}!x@example.invalid/",
            [("SYNTHq", "ui_spe", "c", "ial_pw", "qVAL42", "42")],
        ),
        _seed("https://user:{0}&x@example.invalid/", [("SYNTHq", "ui_am", "p_pwqV", "AL4242")]),
        _seed(
            "https://us$er:{0}@example.invalid/",
            [("SYNTHq", "ui_use", "r_", "specia", "lqVAL4", "242")],
        ),
        _seed(
            "https://first!last:{0}@example.invalid/",
            [("SYNTHq", "ui_use", "r", "_bangq", "VAL424", "2")],
        ),
        _seed(
            "git+https://user:{0}@example.invalid/r.git",
            [("SYNTHq", "ui_git", "_", "httpsq", "VAL424", "2")],
        ),
        _seed("ftp://user:{0}@example.invalid/", [("SYNTHq", "ui_f", "tpqVAL", "4242")]),
        _seed(
            "{0}://app:{1}@db.example.invalid:5432/app",
            [("post", "gres"), ("SYNTHq", "ui_pos", "tgresq", "VAL424", "2")],
        ),
        _seed(
            "{0}://app:{1}@db.example.invalid/app",
            [("postg", "resql"), ("SYNTHq", "ui_pos", "t", "gresql", "qVAL42", "42")],
        ),
        _seed("mysql://root:{0}@db.example.invalid/x", [("SYNTHq", "ui_my", "sqlqVA", "L4242")]),
        _seed(
            "redis://:{0}@cache.example.invalid:6379/0",
            [("SYNTHq", "ui_red", "is_e", "mpty_u", "serqVA", "L4242")],
        ),
        _seed("amqp://guest:{0}@mq.example.invalid/", [("SYNTHq", "ui_a", "mqpqVA", "L4242")]),
        _seed(
            "mongodb+srv://u:{0}@cluster.example.invalid/db",
            [("SYNTHq", "ui_mon", "go", "db_srv", "qVAL42", "42")],
        ),
        _seed("ssh://git:{0}@example.invalid/", [("SYNTHq", "ui_s", "shqVAL", "4242")]),
        _seed(
            "https%3A%2F%2Fuser%{0}%{1}.invalid%2F",
            [("3ASYNT", "Hqui_", "pct1qV", "AL4242"), ("40ex", "ample")],
        ),
        _seed(
            "https%253A%252F%{0}%{1}%{2}.invalid%252F",
            [("252F", "user"), ("253ASY", "NTHqui", "_pct2q", "VAL424", "2"), ("2540e", "xample")],
        ),
        _seed(
            "https%25253A%25252F%{0}%{1}%{2}.invalid%25252F",
            [
                ("25252", "Fuser"),
                ("25253A", "SYNTHq", "u", "i_pct3", "qVAL42", "42"),
                ("252540", "exampl", "e"),
            ],
        ),
        _seed(
            "https://example.invalid/?next=https%3A%2F%2Fuser%{0}%{1}.invalid%2F",
            [("3ASYNT", "Hqui_i", "n", "_nextq", "VAL424", "2"), ("40ex", "ample")],
        ),
        _seed(
            "see https://user:{0}@example.invalid",
            [("SYNTHq", "ui_no_", "sl", "ash_en", "dqVAL4", "242")],
        ),
        _seed('"https://user:{0}@example.invalid"', [("SYNTHq", "ui_qu", "otedqV", "AL4242")]),
        _seed(
            "https://user:{0}/x@example.invalid/",
            [("SYNTHq", "ui_pw_", "ha", "s_slas", "hqVAL4", "242")],
        ),
        _seed(
            "https://user:{0}#x@example.invalid/",
            [("SYNTHq", "ui_pw_", "ha", "s_hash", "qVAL42", "42")],
        ),
        _seed(
            "https://user:{0}?x@example.invalid/",
            [("SYNTHq", "ui_pw_", "has_", "questi", "onqVAL", "4242")],
        ),
        _seed(
            "https://user:{0}%40x@example.invalid/", [("SYNTHq", "ui_pw_", "pct40q", "VAL424", "2")]
        ),
        _seed(
            "https://{0}",
            [
                (
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuuu",
                    "uuuuu",
                )
            ],
        ),
        _seed(
            "https:\\/\\/user:{0}@example.invalid\\/",
            [("SYNTHq", "ui_bac", "kslas", "h_json", "escqVA", "L4242")],
        ),
        _seed("{0}: {1}", [("pass", "word"), ("SYNTHq", "colon_", "pa", "ssword", "qVAL42", "42")]),
        _seed("passwd: {0}", [("SYNTHq", "colon_", "p", "asswdq", "VAL424", "2")]),
        _seed("pwd: {0}", [("SYNTHq", "colon", "_pwdqV", "AL4242")]),
        _seed("token: {0}", [("SYNTHq", "colon_", "tokenq", "VAL424", "2")]),
        _seed("secret: {0}", [("SYNTHq", "colon_", "s", "ecretq", "VAL424", "2")]),
        _seed("session: {0}", [("SYNTHq", "colon_", "s", "ession", "qVAL42", "42")]),
        _seed("cookie: {0}", [("SYNTHq", "colon_", "c", "ookieq", "VAL424", "2")]),
        _seed(
            "{0}: {1}", [("set-c", "ookie"), ("SYNTHq", "colon_", "set", "-cooki", "eqVAL4", "242")]
        ),
        _seed(
            "{0}: {1}",
            [("author", "izatio", "n"), ("SYNTHq", "colon_", "auth", "orizat", "ionqVA", "L4242")],
        ),
        _seed("api_key: {0}", [("SYNTHq", "colon_", "a", "pi_key", "qVAL42", "42")]),
        _seed("apikey: {0}", [("SYNTHq", "colon_", "a", "pikeyq", "VAL424", "2")]),
        _seed(
            "{0}: {1}",
            [("access", "_token"), ("SYNTHq", "colon_", "acce", "ss_tok", "enqVAL", "4242")],
        ),
        _seed(
            "{0}: {1}",
            [("client", "_secre", "t"), ("SYNTHq", "colon_", "clie", "nt_sec", "retqVA", "L4242")],
        ),
        _seed(
            "{0}: {1}", [("hatch", "_sess"), ("SYNTHq", "colon_", "hat", "ch_ses", "sqVAL4", "242")]
        ),
        _seed(
            "{0}: {1}",
            [("priva", "te_key"), ("SYNTHq", "colon_", "pri", "vate_k", "eyqVAL", "4242")],
        ),
        _seed(
            "{0}: {1}", [("x-ap", "i-key"), ("SYNTHq", "colon_", "x-", "api-ke", "yqVAL4", "242")]
        ),
        _seed(
            "{0}: {1}",
            [("x-auth", "-token"), ("SYNTHq", "colon_", "x-au", "th-tok", "enqVAL", "4242")],
        ),
        _seed("auth: {0}", [("SYNTHq", "colon_", "authqV", "AL4242")]),
        _seed(
            "{0}: {1}",
            [("crede", "ntials"), ("SYNTHq", "colon_", "cre", "dentia", "lsqVAL", "4242")],
        ),
        _seed("pass: {0}", [("SYNTHq", "colon_", "passqV", "AL4242")]),
        _seed(
            "{0} : {1}",
            [("pass", "word"), ("SYNTHq", "colon_", "spac", "e_befo", "reqVAL", "4242")],
        ),
        _seed("{0}:\t{1}", [("pass", "word"), ("SYNTHq", "colon", "_tabqV", "AL4242")]),
        _seed(
            '"{0}": {1}',
            [("pass", "word"), ("SYNTHq", "colon_", "quote", "d_key_", "unqqVA", "L4242")],
        ),
        _seed(
            "{0}: '{1}'",
            [("pass", "word"), ("SYNTHq", "colon_", "singl", "e_quot", "ed_qVA", "L4242")],
        ),
        _seed("{0}%3A%{1}", [("pass", "word"), ("20SYNT", "Hqcolo", "n_pctq", "VAL424", "2")]),
        _seed(
            "{0}%253A%{1}", [("pass", "word"), ("2520SY", "NTHqco", "lo", "n_pct2", "qVAL42", "42")]
        ),
        _seed(
            "{0}%25253A%{1}",
            [("pass", "word"), ("252520", "SYNTHq", "col", "on_pct", "3qVAL4", "242")],
        ),
        _seed(
            "db:\n  {0}: {1}\n",
            [("pass", "word"), ("SYNTHq", "colon_", "yam", "l_inde", "ntqVAL", "4242")],
        ),
        _seed(
            "- {0}: {1}", [("pass", "word"), ("SYNTHq", "colon_", "ya", "ml_lis", "tqVAL4", "242")]
        ),
        _seed(
            "{0}: {1},",
            [("pass", "word"), ("SYNTHq", "colon_", "trail", "ing_co", "mmaqVA", "L4242")],
        ),
        _seed(
            "{0}: {1}",
            [("user1p", "asswor", "d"), ("SYNTHq", "colon_", "aft", "er_dig", "itqVAL", "4242")],
        ),
        _seed("{0}: {1}", [("pass", "word"), ("1234", "5678")]),
        _seed("{0} = {1}", [("pass", "word"), ("SYNTHq", "colon_", "i", "ni_eqq", "VAL424", "2")]),
        _seed("{0} = '{1}'", [("pass", "word"), ("SYNTHq", "toml_s", "ingleq", "VAL424", "2")]),
        _seed("export {0}={1}", [("PASS", "WORD"), ("SYNTHq", "env_ex", "portqV", "AL4242")]),
        _seed('{0}="{1}"', [("DB_PA", "SSWORD"), ("SYNTHq", "env_qu", "otedqV", "AL4242")]),
        _seed(
            "<{0}>{1}</{2}>",
            [("pass", "word"), ("SYNTHq", "xml_el", "ementq", "VAL424", "2"), ("pass", "word")],
        ),
        _seed("{0}={1}", [("--pas", "sword"), ("SYNTHq", "cli_fl", "ag_eqq", "VAL424", "2")]),
        _seed(
            "{0} {1}", [("--pas", "sword"), ("SYNTHq", "cli_fl", "ag", "_space", "qVAL42", "42")]
        ),
        _seed("{0}={1}", [("-Dpas", "sword"), ("SYNTHq", "cli", "_DqVAL", "4242")]),
        _seed(
            "curl " + "-u admin:{0} https://example.invalid/",
            [("SYNTHq", "cli_cu", "r", "l_user", "qVAL42", "42")],
        ),
        _seed(
            'note {{"{0}": "{1}"}} end',
            [("pass", "word"), ("SYNTHq", "json_b", "asicqV", "AL4242")],
        ),
        _seed(
            '{{"{0}":"{1}"}}',
            [("pass", "word"), ("SYNTHq", "json_c", "o", "mpactq", "VAL424", "2")],
        ),
        _seed(
            '{{ "{0}" : "{1}" }}', [("pass", "word"), ("SYNTHq", "json_s", "pacedq", "VAL424", "2")]
        ),
        _seed(
            "{{'{0}': '{1}'}}", [("pass", "word"), ("SYNTHq", "json_s", "ingleq", "VAL424", "2")]
        ),
        _seed(
            '{{"db": {{"{0}": "{1}"}}}}',
            [("pass", "word"), ("SYNTHq", "json_n", "estedq", "VAL424", "2")],
        ),
        _seed('{{"{0}": {1}}}', [("pass", "word"), ("1234", "5678")]),
        _seed('{{"{0}": ["{1}"]}}', [("pass", "word"), ("SYNTHq", "json_a", "rrayqV", "AL4242")]),
        _seed(
            '{{"body": "{{\\"{0}\\": \\"{1}\\"}}"}}',
            [("pass", "word"), ("SYNTHq", "json_e", "sca", "ped_do", "cqVAL4", "242")],
        ),
        _seed(
            'payload={{\\"{0}\\":\\"{1}\\"}}',
            [("pass", "word"), ("SYNTHq", "json_e", "sca", "ped_ba", "reqVAL", "4242")],
        ),
        _seed(
            '{{"pass\\{0}": "{1}"}}',
            [("u007", "7ord"), ("SYNTHq", "json_u", "nic", "ode_ke", "yqVAL4", "242")],
        ),
        _seed(
            '{{"{0}": "\\{1}"}}',
            [("pass", "word"), ("u0041S", "YNTHqj", "son_un", "icode_", "valueq", "VAL424", "2")],
        ),
        _seed(
            "%7B%{0}%22%3A%{1}%22%7D",
            [("22pas", "sword"), ("22SYNT", "Hqjson", "_pctqV", "AL4242")],
        ),
        _seed(
            "%257B%{0}%2522%253A%{1}%2522%257D",
            [("2522pa", "ssword"), ("2522SY", "NTHqjs", "o", "n_pct2", "qVAL42", "42")],
        ),
        _seed(
            "%7B%{0}%22%3A%20%{1}%22%7D",
            [("22pas", "sword"), ("22SYNT", "Hqjson", "_pc", "t_spac", "eqVAL4", "242")],
        ),
        _seed(
            'log: {{"{0}": "{1}',
            [("pass", "word"), ("SYNTHq", "json_t", "ru", "ncated", "qVAL42", "42")],
        ),
        _seed(
            '{{"{0}": "{1}"}}',
            [("PASS", "WORD"), ("SYNTHq", "json_k", "ey", "_upper", "qVAL42", "42")],
        ),
        _seed(
            '{{"{0}": "{1}"}}',
            [("x-ap", "i-key"), ("SYNTHq", "json_k", "e", "y_dash", "qVAL42", "42")],
        ),
        _seed('{{"key": "{0}"}}', [("sk-SYN", "THqjso", "n_skqV", "AL4242")]),
        _seed(
            '{{"{0}": "a\n{1}"}}',
            [("pass", "word"), ("SYNTHq", "json_n", "ewlin", "e_in_v", "aluqVA", "L4242")],
        ),
        _seed(
            '{{"headers": {{"Cookie": "sid={0}"}}}}',
            [("SYNTHq", "json_c", "ookie", "_heade", "r_oqVA", "L4242")],
        ),
        _seed(
            '{{"{0}": "Bearer {1}"}}',
            [("Author", "izatio", "n"), ("SYNTHq", "json_a", "utho", "rizati", "onqVAL", "4242")],
        ),
        _seed(
            '{{"{0}": "Basic {1}"}}',
            [("Author", "izatio", "n"), ("SYNTHq", "json_a", "uthor", "izatio", "n_bqVA", "L4242")],
        ),
        _seed(
            '{{"{0}": ["sid={1}; Path=/"]}}',
            [("set-c", "ookie"), ("SYNTHq", "json_s", "et_co", "okie_l", "istqVA", "L4242")],
        ),
        _seed("Cookie: sid={0}; theme=dark", [("SYNTHq", "cookie", "_hdrqV", "AL4242")]),
        _seed(
            "Cookie: theme=dark;\n sid={0}",
            [("SYNTHq", "cookie", "_hdr_", "multil", "ineqVA", "L4242")],
        ),
        _seed(
            "{0}: sid={1}; Path=/; Secure; {2}",
            [
                ("Set-C", "ookie"),
                ("SYNTHq", "set_co", "oki", "e_attr", "sqVAL4", "242"),
                ("Http", "Only"),
            ],
        ),
        _seed(
            "{0}: sid={1}; Expires=Wed, 21 Oct 2026 07:28:00 GMT",
            [("Set-C", "ookie"), ("SYNTHq", "set_co", "okie_", "expire", "s_cqVA", "L4242")],
        ),
        _seed("theme=dark; sid={0}", [("SYNTHq", "cookie", "_ch", "ain_ba", "reqVAL", "4242")]),
        _seed("theme=dark; foo={0}", [("SYNTHq", "cookie", "_cha", "in_cus", "tomqVA", "L4242")]),
        _seed("theme=dark; sid={0}", [("1234", "56789")]),
        _seed("a=1, sid={0}", [("SYNTHq", "cookie", "_cha", "in_com", "maqVAL", "4242")]),
        _seed(
            'theme="dark"; sid="{0}"', [("SYNTHq", "cookie", "_cha", "in_quo", "tedqVA", "L4242")]
        ),
        _seed("cookie:sid={0}", [("SYNTHq", "cookie", "_hdr_", "lower_", "no_qVA", "L4242")]),
        _seed("Cookie: {0}", [("SYNTHq", "cookie", "_", "opaque", "qVAL42", "42")]),
        _seed(
            "{0}: {1}",
            [("Set-C", "ookie"), ("SYNTHq", "set_co", "oki", "e_opaq", "ueqVAL", "4242")],
        ),
        _seed("key {0}", [("sk-pro", "j-SYNT", "Hqs", "k_pref", "ixqVAL", "4242")]),
        _seed("{0}", [("ghp_SY", "NTHqgh", "p_pref", "i", "xqVAL4", "242abc", "defghi", "j")]),
        _seed("{0}", [("xoxb-S", "YNTHqx", "oxb", "_prefi", "xqVAL4", "242")]),
        _seed("{0}", [("AKIASY", "NTHqaw", "s", "_akidq", "VAL424", "2")]),
        _seed(
            "{0}.{1}.sig", [("eyJhbG", "ciOi", "JIUzI1", "NiJ9"), ("SYNTHq", "jw", "tqVAL4", "242")]
        ),
        _seed("{0}", [("glpat-", "SYNTHq", "glpatq", "VAL424", "2")]),
        _seed("{0}", [("sk_liv", "e_SYNT", "Hqstri", "pe_sk_", "liveqV", "AL4242")]),
        _seed("{0}", [("AIzaSY", "NTHqgo", "og", "le_aiz", "aqVAL4", "242")]),
        _seed("the header was Bearer {0} today", [("SYNTH_", "BEARER", "_BARE_", "VAL424", "2")]),
        _seed("use bearer {0} here", [("SYNTH_", "BEARER", "_BA", "RE_LOW", "ER_VAL", "4242")]),
        _seed('token type "Bearer {0}"', [("SYNTH_", "BEARER", "_", "QUOTED", "_VAL42", "42")]),
        _seed("Bearer%{0}", [("20SYNT", "H_BEAR", "E", "R_PCT1", "_VAL42", "42")]),
        _seed(
            "{0}: Bearer {1}",
            [("Author", "izatio", "n"), ("SYNTH_", "AUTHZ_", "COLO", "N_CONT", "ROL_VA", "L4242")],
        ),
        _seed(
            "{0}=Bearer {1}",
            [("Author", "izatio", "n"), ("SYNTH_", "AUTHZ_", "E", "QUALS_", "VAL424", "2")],
        ),
        _seed(
            "{0} = Bearer {1}",
            [("Author", "izatio", "n"), ("SYNTH_", "AUTHZ_", "EQUA", "LS_SPA", "CED_VA", "L4242")],
        ),
        _seed(
            "{0}: Bearer {1}",
            [("Proxy-", "Aut", "horiza", "tion"), ("SYNTH_", "AUTHZ_", "PROXY_", "VAL424", "2")],
        ),
        _seed(
            "{0}: Basic {1}",
            [
                ("Proxy-", "Aut", "horiza", "tion"),
                ("SYNTH_", "AUTHZ_", "PRO", "XY_BAS", "IC_VAL", "4242"),
            ],
        ),
        _seed(
            "{0}: ApiKey {1}",
            [("Author", "izatio", "n"), ("SYNTH_", "AUTHZ_", "CUST", "OM_SCH", "EME_VA", "L4242")],
        ),
        _seed(
            "{0}: {1}",
            [("Author", "izatio", "n"), ("SYNTH_", "AUTHZ_", "NO", "_SCHEM", "E_VAL4", "242")],
        ),
        _seed(
            "{0}%3A%{1}%{2}",
            [
                ("Author", "izatio", "n"),
                ("20Be", "arer"),
                ("20SYNT", "H_AUTH", "Z", "_PCT1_", "VAL424", "2"),
            ],
        ),
        _seed(
            '{{"{0}": "Bearer {1}"}}',
            [("Author", "izatio", "n"), ("SYNTH_", "AUTHZ_", "JSON", "_CONTR", "OL_VAL", "4242")],
        ),
        _seed(
            "curl -H '{0}: Bearer {1}' https://example.invalid/",
            [("Author", "izatio", "n"), ("SYNTH_", "AUTHZ_", "CURL", "_CONTR", "OL_VAL", "4242")],
        ),
        _seed(
            "https://user:{0}@example.invalid/path",
            [("SYNTH_", "USERIN", "FO", "_HTTPS", "_VAL42", "42")],
        ),
        _seed(
            "{0}://app:{1}@db.example.invalid:5432/app",
            [("post", "gres"), ("SYNTH_", "USERIN", "FO_", "POSTGR", "ES_VAL", "4242")],
        ),
        _seed(
            "redis://:{0}@cache.example.invalid:6379/0",
            [("SYNTH_", "USERIN", "FO", "_REDIS", "_VAL42", "42")],
        ),
        _seed(
            "https://example.invalid/?next=https%3A%2F%2Fuser%{0}%{1}.invalid%2F",
            [("3ASYNT", "H_USER", "IN", "FO_PCT", "1_VAL4", "242"), ("40ex", "ample")],
        ),
        _seed(
            "https://example.invalid/?next=https%253A%252F%{0}%{1}%{2}.invalid%252F",
            [
                ("252F", "user"),
                ("253ASY", "NTH_US", "ERI", "NFO_PC", "T2_VAL", "4242"),
                ("2540e", "xample"),
            ],
        ),
        _seed(
            "https://example.invalid/?next=https%25253A%25252F%{0}%{1}%{2}.invalid%25252F",
            [
                ("25252", "Fuser"),
                ("25253A", "SYNTH_", "USER", "INFO_P", "CT3_VA", "L4242"),
                ("252540", "exampl", "e"),
            ],
        ),
        _seed(
            "{0}: {1}[{2}]{3}",
            [("pass", "word"), ("SYNTH", "_COLON"), ("PASS", "WORD"), ("_VAL", "4242")],
        ),
        _seed("token: {0}[TOKEN]{1}", [("SYNTH", "_COLON"), ("_VAL", "4242")]),
        _seed("secret: {0}[SECRET]{1}", [("SYNTH", "_COLON"), ("_VAL", "4242")]),
        _seed("session: {0}[SESSION]{1}", [("SYNTH", "_COLON"), ("_VAL", "4242")]),
        _seed(
            "{0}: {1}[{2}]{3}",
            [("sess", "ionid"), ("SYNTH", "_COLON"), ("SESS", "IONID"), ("_VAL", "4242")],
        ),
        _seed("sess: {0}[SESS]{1}", [("SYNTH", "_COLON"), ("_VAL", "4242")]),
        _seed("sessid: {0}[SESSID]{1}", [("SYNTH", "_COLON"), ("_VAL", "4242")]),
        _seed("cookie: {0}[COOKIE]{1}", [("SYNTH", "_COLON"), ("_VAL", "4242")]),
        _seed(
            "{0}: {1}[{2}]{3}",
            [
                ("cookie", "_heade", "r"),
                ("SYNTH", "_COLON"),
                ("COOKIE", "_HEADE", "R"),
                ("_VAL", "4242"),
            ],
        ),
        _seed("api_key: {0}[API_KEY]{1}", [("SYNTH", "_COLON"), ("_VAL", "4242")]),
        _seed(
            "{0}: {1}[{2}]{3}",
            [("access", "_token"), ("SYNTH", "_COLON"), ("ACCESS", "_TOKEN"), ("_VAL", "4242")],
        ),
        _seed(
            "{0}: {1}[{2}]{3}",
            [
                ("client", "_secre", "t"),
                ("SYNTH", "_COLON"),
                ("CLIENT", "_SECRE", "T"),
                ("_VAL", "4242"),
            ],
        ),
        _seed(
            "{0}: {1}[{2}]{3}",
            [
                ("refres", "h_toke", "n"),
                ("SYNTH", "_COLON"),
                ("REFRES", "H_TOKE", "N"),
                ("_VAL", "4242"),
            ],
        ),
        _seed(
            "{0}: {1}[{2}]{3}",
            [("my_pa", "ssword"), ("SYNTH", "_COLON"), ("MY_PA", "SSWORD"), ("_VAL", "4242")],
        ),
        _seed(
            "{0}: {1}[{2}]{3}",
            [("hatch", "_sess"), ("SYNTH", "_COLON"), ("HATCH", "_SESS"), ("_VAL", "4242")],
        ),
        _seed(
            "{0}: {1}[{2}]{3}",
            [("x_auth", "_token"), ("SYNTH", "_COLON"), ("X_AUTH", "_TOKEN"), ("_VAL", "4242")],
        ),
        _seed(
            "{0}: {1}[{2}]{3}",
            [("csrf", "token"), ("SYNTH", "_COLON"), ("CSRF", "TOKEN"), ("_VAL", "4242")],
        ),
        _seed("sid: {0}[SID]{1}", [("SYNTH", "_COLON"), ("_VAL", "4242")]),
        _seed("xs: {0}[XS]{1}", [("SYNTH", "_COLON"), ("_VAL", "4242")]),
        _seed("datr: {0}[DATR]{1}", [("SYNTH", "_COLON"), ("_VAL", "4242")]),
        _seed("{0}:{1}", [("pass", "word"), ("SYNTH_", "COLON_", "N", "OSPACE", "_VAL42", "42")]),
        _seed(
            '{0}: "{1}"',
            [("pass", "word"), ("SYNTH_", "COLON_", "YAM", "L_QUOT", "ED_VAL", "4242")],
        ),
        _seed("{0}: {1}", [("PASS", "WORD"), ("SYNTH_", "COLON_", "UPPER_", "VAL424", "2")]),
        _seed(
            "{0}: {1}",
            [
                ("X-Auth", "-Token"),
                ("SYNTH_", "COLON_", "HEADER", "_", "X_AUTH", "_TOKE_", "VAL424", "2"),
            ],
        ),
        _seed(
            "{0}: {1}",
            [("X-AP", "I-Key"), ("SYNTH_", "COLON_", "HEADER", "_X_API", "_KEY_V", "AL4242")],
        ),
        _seed(
            "{0}: {1}",
            [
                ("Privat", "e-Toke", "n"),
                ("SYNTH_", "COLON_", "HEADER", "_", "PRIVAT", "E_TOK_", "VAL424", "2"),
            ],
        ),
        _seed("{0}%3A%{1}", [("pass", "word"), ("20SYNT", "H_COLO", "N", "_PCT1_", "VAL424", "2")]),
        _seed(
            'note {{"{0}": "{1}"}} end',
            [("pass", "word"), ("SYNTH_", "JSON_P", "ROSE", "_CONTR", "OL_VAL", "4242")],
        ),
        _seed(
            "note {{'{0}': '{1}'}} end",
            [
                ("pass", "word"),
                ("SYNTH_", "JSON_P", "ROSE_S", "I", "NGLE_C", "ONTRO_", "VAL424", "2"),
            ],
        ),
        _seed(
            'log line: {{"{0}": "{1}',
            [("pass", "word"), ("SYNTH_", "JSON_T", "RUNCA", "TED_VA", "LUE_VA", "L4242")],
        ),
        _seed(
            'note {{"{0}": {1}}} end',
            [("pass", "word"), ("1234SY", "NTH_JS", "ON_NU", "MBER_V", "ALUE_V", "AL4242")],
        ),
        _seed(
            'note {{"{0}": ["{1}"]}} end',
            [("pass", "word"), ("SYNTH_", "JSON_A", "RRA", "Y_VALU", "E_VAL4", "242")],
        ),
        _seed(
            'note {{"headers": {{"{0}": "{1}"}}}} end',
            [("X-Cust", "om-Aut", "h"), ("SYNTH_", "JSON_N", "ESTE", "D_HEAD", "ERS_VA", "L4242")],
        ),
        _seed(
            '{{"msg": "{{\\"{0}\\": \\"{1}\\"}}"}}',
            [("pass", "word"), ("SYNTH_", "JSON_E", "SCAPED", "_IN_ST", "RING_V", "AL4242")],
        ),
        _seed(
            'note {{\\"{0}\\": \\"{1}\\"}} end',
            [("pass", "word"), ("SYNTH_", "JSON_E", "SCA", "PED_BA", "RE_VAL", "4242")],
        ),
        _seed(
            '"{{\\"{0}\\": \\"{1}\\"}}"',
            [("pass", "word"), ("SYNTH_", "JSON_D", "OUBL", "E_DUMP", "ED_VAL", "4242")],
        ),
        _seed(
            "data-x='{{&quot;{0}&quot;:&quot;{1}&quot;}}'",
            [("pass", "word"), ("SYNTH_", "JSON_H", "TML_", "ENTITI", "ES_VAL", "4242")],
        ),
        _seed(
            "https://example.invalid/cb?state=%7B%{0}%22%3A%{1}%22%7D",
            [("22pas", "sword"), ("22SYNT", "H_JSON", "_PCT", "_IN_QU", "ERY_VA", "L4242")],
        ),
        _seed(
            'note {{"{0}": "line1\n{1}"}} end',
            [("pass", "word"), ("SYNTH_", "JSON_R", "EALNEW", "LINE_V", "ALUE_V", "AL4242")],
        ),
        _seed("{0}={1}", [("PASS", "WORD"), ("SYNTH_", "CASE_U", "P", "PER_EQ", "_VAL42", "42")]),
        _seed(
            '{{"{0}": "{1}"}}',
            [("Pass", "Word"), ("SYNTH_", "CASE_M", "IX", "ED_JSO", "N_VAL4", "242")],
        ),
        _seed(
            "ｐａｓｓｗｏｒｄ={0}", [("SYNTH_", "UNICOD", "E_FULL", "WIDTH_", "NAME_V", "AL4242")]
        ),
        _seed("pаssword={0}", [("SYNTH_", "UNICOD", "E_CY", "RILLIC", "_A_VAL", "4242")]),
        _seed("pass\u200bword={0}", [("SYNTH_", "UNICOD", "E", "_ZWSP_", "VAL424", "2")]),
        _seed("pass\xadword={0}", [("SYNTH_", "UNICOD", "E_SO", "FT_HYP", "HEN_VA", "L4242")]),
        _seed(
            "{0}＝{1}",
            [
                ("pass", "word"),
                ("SYNTH_", "UNICOD", "E_FULL", "W", "IDTH_E", "QUALS_", "VAL424", "2"),
            ],
        ),
        _seed(
            "{0}：{1}",
            [("pass", "word"), ("SYNTH_", "UNICOD", "E_FULL", "WIDTH_", "COLON_", "VAL424", "2")],
        ),
        _seed("密碼：{0}", [("SYNTH_", "UNICOD", "E_CJK_", "NAME_C", "OLON_V", "AL4242")]),
        _seed(
            "{0}\xa0=\xa0{1}",
            [("pass", "word"), ("SYNTH_", "UNICOD", "E_NBSP", "_AROUN", "D_EQ_V", "AL4242")],
        ),
        _seed("pass\nword={0}", [("SYNTH_", "SPLIT_", "NAME", "_NEWLI", "NE_VAL", "4242")]),
        _seed(
            "{0}\n={1}", [("pass", "word"), ("SYNTH_", "SPLIT_", "BE", "FORE_E", "Q_VAL4", "242")]
        ),
        _seed(
            "{0}=\n{1}", [("pass", "word"), ("SYNTH_", "SPLIT_", "AF", "TER_EQ", "_VAL42", "42")]
        ),
        _seed(
            "https://example.invalid/?token&#61;{0}",
            [("SYNTH_", "HTML_E", "Q_", "ENTITY", "_VAL42", "42")],
        ),
        _seed(
            "https://example.invalid/?a=1&amp;token={0}",
            [("SYNTH_", "HTML_A", "MP", "_TOKEN", "_VAL42", "42")],
        ),
        _seed(
            "https://cdn.example.invalid/v/x.jpg?_nc_cat=1&amp;oe={0}&amp;oh={1}",
            [("65F1", "A2B3"), ("SYNTH_", "HTML_A", "MP", "_FB_OH", "_VAL42", "42")],
        ),
        _seed(
            "https://acct.example.invalid/c/b?sv=2022&amp;sig={0}",
            [("SYNTH_", "HTML_A", "M", "P_SIG_", "VAL424", "2")],
        ),
        _seed(
            "https://cdn.example.invalid/v/x.jpg?_nc_cat=1\\u0026oe={0}\\u0026oh={1}",
            [("65F1", "A2B3"), ("SYNTH_", "JSONES", "C_", "AMP_OH", "_VAL42", "42")],
        ),
        _seed(
            "https://acct.example.invalid/c/b?sv=2022\\{0}={1}",
            [("u002", "6sig"), ("SYNTH_", "JSONES", "C_", "AMP_SI", "G_VAL4", "242")],
        ),
        _seed(
            "https://s3.example.invalid/k?{0}=1\\{1}={2}",
            [
                ("X-Amz", "-Date"),
                ("u0026X", "-Amz", "-Signa", "ture"),
                ("SYNTH_", "JSONES", "C_", "AMP_AM", "Z_VAL4", "242"),
            ],
        ),
        _seed(
            "https://example.invalid/?a=1\\{0}={1}",
            [("u0026", "token"), ("SYNTH_", "JSONES", "C_A", "MP_TOK", "EN_VAL", "4242")],
        ),
        _seed(
            "user=bob|{0}={1}",
            [("pass", "word"), ("SYNTH_", "PIPE_A", "FTER_B", "ENIGN_", "PAIR_V", "AL4242")],
        ),
        _seed("a=1/token={0}", [("SYNTH_", "SLASH_", "AFTER_", "BENIGN", "_PAIR_", "VAL424", "2")]),
        _seed("https://example.invalid/?token=a+{0}&x=1", [("SYNTH_", "URL_P", "LUS_VA", "L4242")]),
        _seed(
            "https://example.invalid/?token=a%{0}&x=1",
            [("20SYNT", "H_URL_", "PCT20_", "VAL424", "2")],
        ),
        _seed(
            "https://example.invalid/?{0}=p%{1}&x=1",
            [
                ("pass", "word"),
                ("26SYNT", "H_URL_", "PCT26_", "I", "NSIDE_", "VALUE_", "VAL424", "2"),
            ],
        ),
        _seed(
            "https://example.invalid/?{0}=p%{1}&x=1",
            [
                ("pass", "word"),
                ("23SYNT", "H_URL_", "PCT23_", "I", "NSIDE_", "VALUE_", "VAL424", "2"),
            ],
        ),
        _seed(
            "https://example.invalid/?{0}=p?{1}&x=1",
            [
                ("pass", "word"),
                ("SYNTH_", "URL_LI", "TERAL_", "Q", "_INSID", "E_VAL_", "VAL424", "2"),
            ],
        ),
        _seed(
            "https://example.invalid/%3Ftoken%{0}%26view%3D1",
            [("3DSYNT", "H_URL_", "L", "AYERS1", "_VAL42", "42")],
        ),
        _seed(
            "https://example.invalid/%{0}%{1}%{2}%253D1",
            [
                ("253F", "token"),
                ("253DSY", "NTH_UR", "L_", "LAYERS", "2_VAL4", "242"),
                ("2526", "view"),
            ],
        ),
        _seed(
            "https://example.invalid/%{0}%{1}%{2}%25253D1",
            [
                ("25253", "Ftoken"),
                ("25253D", "SYNTH_", "URL", "_LAYER", "S3_VAL", "4242"),
                ("25252", "6view"),
            ],
        ),
        _seed(
            "https://example.invalid/%{0}%{1}%{2}%{3}",
            [
                ("252525", "3Ftoke", "n"),
                ("252525", "3DSYNT", "H_UR", "L_LAYE", "RS4_VA", "L4242"),
                ("252525", "26view"),
                ("2525", "253D1"),
            ],
        ),
        _seed(
            "https://example.invalid/%{0}%{1}%{2}%{3}",
            [
                ("252525", "2", "53Ftok", "en"),
                ("252525", "253DSY", "NTH_U", "RL_LAY", "ERS5_V", "AL4242"),
                ("252525", "2", "526vie", "w"),
                ("25252", "5253D1"),
            ],
        ),
        _seed(
            "{0}%{1}",
            [("hatch", "_sess"), ("3DSYNT", "H_PCT_", "BARE", "_PAIR_", "EQ_VAL", "4242")],
        ),
        _seed(
            "https://example.invalid/?data={0}%{1}",
            [
                ("hatch", "_sess"),
                ("3DSYNT", "H_PCT_", "VALUE_", "IN", "_NONSE", "NSITIV", "_VAL42", "42"),
            ],
        ),
        _seed(
            '{0}="line1\n{1}"',
            [("pass", "word"), ("SYNTH_", "MULTIL", "INE", "_QUOTE", "D_VAL4", "242")],
        ),
        _seed(
            '{0}="{1} PRIVATE {2}\n{3}\n{4} PRIVATE {5}"',
            [
                ("PRIVA", "TE_KEY"),
                ("-----", "BEGIN"),
                ("KEY-", "----"),
                ("SYNTH_", "MULTIL", "INE", "_PEM_E", "NV_VAL", "4242"),
                ("----", "-END"),
                ("KEY-", "----"),
            ],
        ),
        _seed("{0}: |\n  {1}\n", [("pass", "word"), ("SYNTH_", "YAML_B", "LOCK_V", "AL4242")]),
        _seed(
            "a=1; {0}=S1; b={1}",
            [("hatch", "_sess"), ("SYNTH_", "COOKIE", "_MID", "_CONTR", "OL_VAL", "4242")],
        ),
        _seed(
            "{0}=S1; auth={1}",
            [
                ("hatch", "_sess"),
                ("SYNTH_", "COOKIE", "_SENSI", "T", "IVE_TH", "EN_CU_", "VAL424", "2"),
            ],
        ),
        _seed(
            "auth={0}; {1}=S1",
            [
                ("SYNTH_", "COOKIE", "_CUSTO", "M", "_THEN_", "SENSI_", "VAL424", "2"),
                ("hatch", "_sess"),
            ],
        ),
        _seed(
            "theme=dark; {0}=S1; {1}={2}",
            [
                ("hatch", "_sess"),
                ("hatch", "_csrf"),
                ("SYNTH_", "COOKIE", "_THREE", "_", "CUSTOM", "_MID__", "VAL424", "2"),
            ],
        ),
        _seed(
            "theme=dark; auth={0}",
            [("SYNTH_", "COOKIE", "_ALL_C", "U", "STOM_C", "ONTRO_", "VAL424", "2")],
        ),
        _seed(
            "{0}: {1}={2}; Path=/; {3}; Secure",
            [
                ("Set-C", "ookie"),
                ("hatch", "_sess"),
                ("SYNTH_", "SET_CO", "OKIE_A", "T", "TRS_CO", "NTROL_", "VAL424", "2"),
                ("Http", "Only"),
            ],
        ),
        _seed(
            "{0}={1}; Path=/; {2}; Secure",
            [
                ("hatch", "_csrf"),
                ("SYNTH_", "BARE_S", "ET_CO", "OKIE_A", "TTRS_V", "AL4242"),
                ("Http", "Only"),
            ],
        ),
        _seed(
            "https://example.invalid/cb#{0}={1}&{2}=bearer",
            [
                ("access", "_token"),
                ("SYNTH_", "FRAG_A", "CCESS_", "T", "OKEN_C", "ONTRO_", "VAL424", "2"),
                ("token", "_type"),
            ],
        ),
        _seed(
            "https://example.invalid/#/route?token={0}",
            [("SYNTH_", "FRAG_R", "OUTE_Q", "U", "ERY_CO", "NTROL_", "VAL424", "2")],
        ),
        _seed(
            "https://example.invalid/p?next=%2Fapp%23token%{0}",
            [("3DSYNT", "H_QUER", "Y_FRA", "G_ENCO", "DED_VA", "L4242")],
        ),
        _seed(
            "mysql {0}={1} db", [("--pas", "sword"), ("SYNTH_", "CLI_LO", "NG_EQ_", "VAL424", "2")]
        ),
        _seed("tool --token {0}", [("SYNTH_", "CLI_LO", "NG", "_SPACE", "_VAL42", "42")]),
        _seed(
            "java {0}={1} -jar app.jar",
            [("-Dpas", "sword"), ("SYNTH_", "CLI_JA", "V", "A_PROP", "_VAL42", "42")],
        ),
        _seed(
            "{0}={1} psql",
            [("PGPAS", "SWORD"), ("SYNTH_", "ENV_AS", "SIGN", "_CONTR", "OL_VAL", "4242")],
        ),
        _seed(
            "{0} PRIVATE {1}\n{2}\n{3} PRIVATE {4}",
            [
                ("-----", "BEGIN"),
                ("KEY-", "----"),
                ("SYNTH_", "VALUE", "_PEM_V", "AL4242"),
                ("----", "-END"),
                ("KEY-", "----"),
            ],
        ),
        _seed(
            "https://example.invalid/?token={0}...",
            [
                (
                    "AAAAAA",
                    "AAAAAA",
                    "AAAAAA",
                    "AAAAAA",
                    "AAAAAA",
                    "AAAAAA",
                    "AAAAAA",
                    "AA",
                    "AAAAAA",
                    "AAAAAA",
                    "AAAAAA",
                    "AAAAAA",
                    "AAAAAA",
                    "AAAAAA",
                    "AAAAAA",
                    "AAA",
                )
            ],
        ),
        _seed('"?token="+t', []),
        _seed("https://example.invalid/?session=", []),
        _seed("the session ended", []),
        _seed("token economy", []),
        _seed("cookie recipe", []),
        _seed("def refresh(token): ...", []),
        _seed("{0}=256", [("max_t", "okens")]),
        _seed("{0}=256", [("max_", "token")]),
        _seed("{0} = 12", [("passwo", "rd_", "min_le", "ngth")]),
        _seed("session={0}.Session()", [("requ", "ests")]),
        _seed("{0}=getpass()", [("pass", "word")]),
        _seed("the {0} header carries a bearer token", [("Author", "izatio", "n")]),
        _seed("{0}: Bearer {1} over Basic", [("Author", "izatio", "n"), ("pref", "erred")]),
        _seed("Cookie: header prose", []),
        _seed("{0}: {1}=Lax", [("Set-C", "ookie"), ("Same", "Site")]),
        _seed("x=1; y=2", []),
        _seed("width=100; height=200", []),
        _seed("/docs/plan.md", []),
        _seed("/etc/hosts", []),
        _seed(
            '{{"{0}":"CAUQAA", "{1}":"end", "{2}":"start"}}',
            [("nextPa", "geToke", "n"), ("eos_", "token"), ("bos_", "token")],
        ),
        _seed('{{"session":"Morning keynote"}}', []),
        _seed("{0}=42", [("active", "_", "sessio", "n")]),
        _seed("{0}=3.5", [("active", "_", "sessio", "n")]),
    ]


FRAGMENTS = [
    "token",
    "password",
    "hatch_sess",
    "session",
    "sig",
    "oh",
    "a",
    "b",
    "x",
    "passwd",
    "=",
    "?",
    "&",
    "#",
    "%3D",
    "%26",
    "%3F",
    "%23",
    "%25",
    "%2526",
    "%253D",
    "%20",
    "%3A",
    "%40",
    '"',
    "'",
    ":",
    ": ",
    " ",
    ";",
    "; ",
    ",",
    "\n",
    "{",
    "}",
    "[",
    "]",
    "<",
    ">",
    "(",
    ")",
    "`",
    "|",
    "/home/u/f",
    "/a/bc",
    "/tmp/x",
    "C:\\x",
    "\\\\srv",
    "[REDACTED]",
    "REDACTED",
    "sk" + "-" + f"{'abcdefgh'}{12}",
    "Cookie: ",
    "Set-Cookie: ",
    "Authorization: ",
    "Bearer ",
    "Basic ",
    "bearer",
    "a=1",
    "b=2",
    "https://",
    "https://u:",
    "@",
    "example.invalid/",
    "%5B",
    "%5D",
    "\\",
    "\\u0026",
    "\\u0022",
    '\\"',
    "-",
    "_",
    ".",
    "1",
    "!",
    "$",
    "SYNTHVAL42",
    "ABCDEFGH12",
    '{"password": "',
    '"token": ',
    "Path=/",
    "theme=dark",
    "sid=",
    "\t",
    "\r\n ",
    "Bearer%20",
    "%20%20",
    "\\u0061",
]


def differential_corpus(n=100000):
    import random

    yield from differential_seed()
    rng = random.Random(7)
    for _ in range(n):
        yield "".join(rng.choice(FRAGMENTS) for _ in range(rng.randint(1, 12)))
