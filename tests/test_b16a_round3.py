"""Round-3 independent-review regressions. OFFLINE, synthetic input only."""

import base64
import json
import random
import re
import string
from collections import Counter
from urllib.parse import quote, unquote

import pytest
from b16a_r3_corpus import HANDOFF, REVIEW_BENIGN2, differential_corpus, leak_matrix

from metamuse import models as m
from metamuse import safety
from metamuse._muse_lifecycle import _check_question
from metamuse.content import _safe_value


def checked(text):
    output, findings = safety.redact_text(text)
    assert output != text and any(row["kind"] != "local_path" for row in findings)
    assert safety.redact_text(output) == (output, [])
    with pytest.raises(m.CoreError) as error:
        _check_question(text)
    assert error.value.detail.code == m.ErrorCode.INPUT_INVALID
    assert error.value.detail.message == "Muse text contains credential values"
    return output


@pytest.mark.parametrize("name,text,marker", leak_matrix(), ids=lambda value: value[:48])
def test_r2_1_leak_matrix(name, text, marker):
    output = checked(text)
    assert marker not in output


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "access_token",
        "client_secret",
        "api_key",
        "hatch_sess",
        "cookie",
        "authorization",
        "private_key",
    ],
)
@pytest.mark.parametrize(
    "kind",
    ["str", "bang", "space", "unicode", "int", "float", "list", "list_bang", "dict", "dict_bang"],
)
def test_r2_1_safe_value(key, kind):
    marker = f"{kind}_MARKER_SYNTHETIC_ONLY"
    value = {
        "str": marker,
        "bang": marker + "!",
        "space": "correct horse " + marker,
        "unicode": "ñ" + marker,
        "int": int("9" * 12 + "4242"),
        "float": 4242.4242,
        "list": [marker],
        "list_bang": [marker + "!"],
        "dict": {"value": marker},
        "dict_bang": {"value": marker + "!"},
    }[kind]
    records = []
    output = _safe_value({"meta": {key: value}}, records)
    assert output == {"meta": {key: "[REDACTED]"}}
    assert records == [{"kind": "sensitive_field", "count": 1}]


def test_r2_1_generated_password_rates():
    rng = random.Random(1014)
    punctuation = "!@#$%^&*()-_=+"
    pool = string.ascii_letters + string.digits + punctuation
    templates = {
        "env": "DB_PASSWORD={p}",
        "kv": "password={p}",
        "yaml": "password: {p}",
        "json": '{{"password": "{p}"}}',
        "toml": 'password = "{p}"',
        "basic": None,
    }
    for name, template in templates.items():
        for _ in range(300):
            while True:
                password = "".join(rng.choice(pool) for _ in range(16))
                if any(c in punctuation for c in password) and any(c.isdigit() for c in password):
                    break
            secret = (
                base64.b64encode(("svc:" + password).encode()).decode()
                if template is None
                else password
            )
            text = (
                "Authorization: Basic " + secret
                if template is None
                else template.format(p=password)
            )
            assert secret not in checked(text), name


@pytest.mark.parametrize("kind", ["b64url", "hex", "jwt", "dotted"])
@pytest.mark.parametrize(
    "template",
    [
        "Authorization: Bearer {t}",
        '{{"Authorization": "Bearer {t}"}}',
        "curl -H 'Authorization: Bearer {t}' https://example.invalid/v1/items",
    ],
)
def test_r2_3_fixpoint_token_formats(kind, template):
    rng = random.Random(20261014)
    alphabet = string.ascii_letters + string.digits + "-_"
    for _ in range(300):
        token = "".join(
            rng.choice("0123456789abcdef" if kind == "hex" else alphabet) for _ in range(40)
        )
        if kind == "jwt":
            token = (
                base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
                .decode()
                .rstrip("=")
                + "."
                + token
                + "."
                + token
            )
        if kind == "dotted":
            token = "ya" + str(29) + "." + token
        assert token not in checked(template.format(t=token))


# The reviewer used deliberately abbreviated vendor tokens in its handoff probe;
# those invalid lengths are checked separately from valid documented formats.
INVALID_PREFIX_HANDOFF = {
    "ghp_prefix",
    "xoxb_prefix",
    "aws_akid",
    "glpat",
    "stripe_sk_live",
    "google_aiza",
}


@pytest.mark.parametrize(
    "name,text,marker",
    [row for row in HANDOFF if row[0] not in INVALID_PREFIX_HANDOFF],
    ids=lambda value: value[:48],
)
def test_r2_4_handoff(name, text, marker):
    output = checked(text)
    if marker in text:
        assert marker not in output


@pytest.mark.parametrize("layers", [0, 1, 2, 3])
@pytest.mark.parametrize(
    "template",
    [
        "password={v}!",
        "password => {v}",
        "Cookie: {v}",
        "Cookie={v}",
        "pAsSwOrD: {v}",
        "theme=dark; hatch_sess={v}; hatch_csrf={v}",
        "theme=dark, hatch_sess={v}, hatch_csrf={v}",
        "theme=dark&hatch_sess={v}&hatch_csrf={v}",
        "domain.invalid\tTRUE\t/\tFALSE\t0\tsid\t{v}",
        "<password>{v}</password>",
        "--password {v}",
        "-Dpassword={v}",
        "curl -u user:{v}",
        "postgres://:{v}@host/a",
        'note {{"password": ["{v}", {{"v": 4}}]}} end',
    ],
)
def test_r2_4_encodings_and_syntax(layers, template):
    marker = f"{layers}_MARKER_SYNTHETIC_ONLY"
    text = template.format(v=marker)
    for _ in range(layers):
        text = quote(text, safe="")
    assert marker not in checked(text)


EXEMPT = (
    "",
    "null",
    "None",
    "nil",
    "true",
    "false",
    "<your-password>",
    "${VALUE}",
    "$VAR",
    "%VAR%",
    "{{VALUE}}",
    "{}",
    "{name}",
    "%s",
    "[REDACTED]",
    "REDACTED",
    "***",
    "xxxxx",
    "XXXXX",
    "...",
    "…",
    "password",
    "config.password",
    'os.environ["KEY"]',
    'os.getenv("KEY")',
    "process.env.PASSWORD",
    "str",
    "string",
    "bytes",
    "int",
    "bool",
    "Any",
    "SecretStr",
    "Optional[str]",
    "Optional[SecretStr]",
    "str | None",
)


@pytest.mark.parametrize("value", EXEMPT)
def test_classifier_closed_exemptions(value):
    text = "password=" + value
    assert safety.redact_text(text) == (text, [])
    _check_question(text)
    records = []
    assert _safe_value({"password": value}, records) == {"password": value}
    assert records == []


@pytest.mark.parametrize(
    "name",
    [
        "pageToken",
        "nextPageToken",
        "page_token",
        "NextToken",
        "next_token",
        "continuationToken",
        "syncToken",
        "cursor",
        "pad_token",
        "eos_token",
        "bos_token",
        "unk_token",
        "sep_token",
        "cls_token",
        "mask_token",
        "max_tokens",
        "max_new_tokens",
        "num_tokens",
        "eos_token_id",
        "custom_token_id",
    ],
)
def test_classifier_exempt_names(name):
    value = f"{name}_MARKER_SYNTHETIC_ONLY"
    for text in (
        f"{name}={value}",
        json.dumps({name: value}),
        f"https://example.invalid/?{name}={value}",
    ):
        assert safety.redact_text(text) == (text, [])
        _check_question(text)


BENIGN2_ACCEPTED = {
    "py_type_hint",
    "py_type_hint_optional",
    "ts_interface",
    "py_environ",
    "py_self_attr",
    "py_kwarg_passthrough",
    "py_kwarg_attr",
    "hf_pad_eos",
    "hf_sep_token",
    "js_cookie_read",
    "shell_env_ref",
    "shell_env_ref2",
    "yaml_secret_ref",
    "placeholder_angle",
    "placeholder_redacted",
    "placeholder_xxx",
    "google_pageToken",
    "google_page_token_py",
    "aws_NextToken",
    "azure_continuationToken",
    "next_token_query",
    "sync_token",
    "max_new_tokens",
    "max_tokens_json",
    "num_tokens_prose",
    "prompt_tokens",
    "special_tokens",
    "eos_token_id",
    "hdr_prose_auth_scheme",
    "session_counter_neg",
    "session_counter_pct",
    "session_year",
    "url_plain",
    "bearer_prose",
    "bearer_capital",
    "bearer_longword",
    "abs_path",
}


@pytest.mark.parametrize("name,text", REVIEW_BENIGN2.items())
def test_r2_6_benign2_binding_policy(name, text):
    if name in BENIGN2_ACCEPTED:
        _check_question(text)
        if name != "abs_path":
            assert safety.redact_text(text) == (text, [])
    else:
        checked(text)


def prefix_cases():
    body = "".join(string.ascii_letters[i % 52] if i % 3 else str(i % 10) for i in range(160))
    for char in "pousr":
        yield "github_" + char, "gh" + char + "_" + body[:36]
    yield "github_pat", "github" + "_pat_" + body[:22] + "_" + body[:59]
    yield "gitlab", "glpat" + "-" + body[:20]
    for char in "bpa":
        yield (
            "slack_" + char,
            "xox" + char + "-" + str(123456789012) + "-" + str(123456789012) + "-" + body[:24],
        )
    for prefix in ("AK" + "IA", "AS" + "IA"):
        yield prefix, prefix + ("A" + str(1)) * 8
    yield "google", "AI" + "za" + body[:35]
    for prefix in ("sk", "rk"):
        yield "stripe_" + prefix, prefix + "_live_" + body[:24]
    yield "npm", "np" + "m_" + body[:36]
    yield "hf", "h" + "f_" + body[:34]
    yield "openai", "s" + "k-" + body[:40]
    yield "anthropic", "s" + "k-ant-" + body[:40]
    yield (
        "jwt",
        ".".join(
            base64.urlsafe_b64encode(json.dumps(v).encode()).decode().rstrip("=")
            for v in ({"alg": "HS256"}, {"sub": "synthetic"})
        )
        + "."
        + body[:43],
    )
    for label in (
        "PRIVATE KEY",
        "RSA PRIVATE KEY",
        "EC PRIVATE KEY",
        "OPENSSH PRIVATE KEY",
        "ENCRYPTED PRIVATE KEY",
    ):
        yield (
            "pem_" + label,
            "-----BE" + "GIN " + label + "-----\n" + body + "\n-----E" + "ND " + label + "-----",
        )
    yield "pem_unterminated", "-----BE" + "GIN PRIVATE KEY-----\n" + body


@pytest.mark.parametrize("name,value", list(prefix_cases()))
@pytest.mark.parametrize("layers", [0, 1, 2, 3])
def test_r2_7_well_known_prefixes(name, value, layers):
    text = "Found in the log: " + value + " (rotate it)"
    for _ in range(layers):
        text = quote(text, safe="")
    assert value not in checked(text)


@pytest.mark.parametrize(
    "text",
    [
        "Bearer%20%20%20%5D",
        "Bearer%2520%2520%2520%255D",
        "Bearer%20%09%5D",
        "Bearer+instruments",
        "The bearer instrument was signed",
    ],
)
def test_r2_10_bearer_separator_boundary(text):
    assert safety.redact_text(text) == (text, [])
    _check_question(text)


def test_r2_3_differential_fixpoint_corpus():
    count = 0
    # Independent, deliberately narrow oracle for unambiguous named synthetic
    # values. The separate tagged corpus covers every other credential grammar.
    named_marker = re.compile(
        r"(?<![\w.])(?:password|passwd|token|secret|hatch_sess|mm_sess)"
        r"[\"']?\s*(?:=>|[=:])\s*[\"']?(?P<marker>SYNTH[\w]+)",
        re.I,
    )

    def canonical(value):
        for _ in range(3):
            value = unquote(value)
            value = re.sub(
                r'\\(?:u[0-9a-fA-F]{4}|["\\/bfnrt])',
                lambda match: json.loads('"' + match[0] + '"'),
                value,
            )
        return value

    for text in differential_corpus():
        output, _ = safety.redact_text(text)
        assert safety.redact_text(output) == (output, []), repr(text)
        decoded_output = canonical(output)
        decoded_input = canonical(text)
        private_markers = Counter()
        for match in named_marker.finditer(decoded_input):
            # A quoted code reference is one consumed value, not a second key:
            # e.g. '"token": "token": marker' exempts the first value.
            key_end = re.search(r"[\"']?\s*(?:=>|[=:])", match[0]).start()
            if (
                decoded_input[match.start() - 1 : match.start()] in {'"', "'"}
                and match[0][key_end : key_end + 1] in {'"', "'"}
                and decoded_input[: match.start() - 1].rstrip().endswith((":", "="))
            ):
                continue
            private_markers[match["marker"]] += 1
        for marker, occurrences in private_markers.items():
            assert decoded_output.count(marker) <= decoded_input.count(marker) - occurrences, repr(
                text
            )
        count += 1
    assert count == 100372


@pytest.mark.parametrize("layers", [1, 2, 3])
@pytest.mark.parametrize(
    "syntax",
    [
        "query_space",
        "query_quote",
        "userinfo_quote",
        "userinfo_space",
        "form_plus",
        "form_value_plus",
    ],
)
def test_canonical_value_delimiters(layers, syntax):
    marker = f"{syntax}_MARKER_SYNTHETIC_ONLY"
    value = (
        ("two " + marker)
        if syntax.endswith("space")
        else ('two"' + marker)
        if syntax.endswith("quote")
        else marker
    )
    if syntax.startswith("query"):
        encoded = quote(value, safe="")
        for _ in range(layers - 1):
            encoded = quote(encoded, safe="")
        text = "?password=" + encoded + "&format=json"
    elif syntax.startswith("userinfo"):
        text = "https://user:" + quote(value, safe="") + "@example.invalid/a"
        for _ in range(layers - 1):
            text = quote(text, safe="")
    else:
        text = (
            "password+%3D+" + marker
            if syntax == "form_plus"
            else "?password=one+two+" + marker + "&view=1"
        )
        for _ in range(layers - 1):
            text = quote(text, safe="+")
    assert marker not in checked(text)


@pytest.mark.parametrize("template", ["local_path=password={v}", '{{"local_path":"password={v}"}}'])
def test_local_path_cannot_hide_credential_class(template):
    marker = f"{len(template)}_MARKER_LOCAL_PATH"
    assert marker not in checked(template.format(v=marker))


@pytest.mark.parametrize("layers", [1, 2, 3])
def test_sensitive_key_api_decodes_every_layer(layers):
    from metamuse.safety import redaction_sensitive_key, redaction_sensitive_value

    key = "password"
    for _ in range(layers):
        key = key.replace("p", "%70").replace("%", "%25") if "%" in key else "%70assword"
    assert redaction_sensitive_key(key)
    marker = f"{'R3'}_MARKER-key-api"
    assert redaction_sensitive_value(key, marker)
    assert marker not in json.dumps(_safe_value({key: marker}, []))


@pytest.mark.parametrize(
    "template",
    [
        r'{{"body":"?token\u00253D{value}"}}',
        r'{{"body":"password\u00253D{value}"}}',
        r"?token%5Cu003D{value}",
    ],
)
def test_interleaved_canonical_encodings(template):
    marker = f"{'R3'}_MARKER-interleaved"
    text = template.format(value=marker)
    redacted, findings = safety.redact_text(text)
    assert marker not in redacted
    assert findings
    assert safety.redact_text(redacted) == (redacted, [])
    with pytest.raises(m.CoreError):
        _check_question(text)


def test_closed_type_union_and_counter_names():
    assert safety.redact_text("password: Optional[str | None]") == (
        "password: Optional[str | None]",
        [],
    )
    assert safety.redact_text("max_tokens=256") == ("max_tokens=256", [])
    text = "max_token=256"
    assert safety.redact_text(text)[0] != text


def test_no_restart_inside_consumed_value(monkeypatch):
    text = "token=" * 4096
    syntax = safety._Syntax(text)
    real = syntax.value
    calls = []

    def value(*args, **kwargs):
        calls.append(args[1])
        return real(*args, **kwargs)

    monkeypatch.setattr(syntax, "value", value)
    spans = list(safety._assignment_spans(text, syntax))
    assert spans == [(len("token="), len(text), "session_cookie", "[REDACTED]")]
    assert calls == [len("token=")]


@pytest.mark.parametrize(
    "block",
    [
        "  username: reader\n  value: {marker}",
        "  - item: {marker}\n  - second: 42",
        "  nested:\n    content: {marker}",
        "  {marker}\n  second line",
        '  "{marker}"\n  other: 42',
    ],
)
@pytest.mark.parametrize("header", ["password:", "password: |", '"password":'])
def test_yaml_sensitive_blocks_redacted_whole(block, header):
    marker = f"{'YAML'}_MARKER-whole-block"
    text = header + "\n" + block.format(marker=marker) + "\npublic: retained"
    output = checked(text)
    assert marker not in output
    assert "public: retained" in output
    assert "username" not in output and "nested:" not in output


@pytest.mark.parametrize(
    "text",
    [
        r"?password\u002b%3D{marker}",
        r"?password=A\u002b{marker}&view=1",
        r"?password%3Dfoo%2B{marker}&view=1",
    ],
)
def test_json_escaped_form_plus_preserves_value_span(text):
    marker = f"{'FORM'}_MARKER-escaped-plus"
    output = checked(text.format(marker=marker))
    assert marker not in output


@pytest.mark.parametrize("suffix", ["/a:public@path", "?q=a:public@query", "#a:public@fragment"])
def test_userinfo_authority_ends_before_public_path(suffix):
    marker = f"{'URL'}_MARKER-authority"
    text = f"https://user:{marker}@example.invalid" + suffix
    assert checked(text) == "https://user:[REDACTED]@example.invalid" + suffix
    benign = "https://example.invalid" + suffix
    assert safety.redact_text(benign) == (benign, [])
    _check_question(benign)


def test_userinfo_unbounded_scheme_has_forward_anchor():
    marker = f"{'SCHEME'}_MARKER-password"
    scheme = "a" + "12345" * 200
    text = f"{scheme}://user:{marker}@example.invalid/a"
    assert marker not in checked(text)
