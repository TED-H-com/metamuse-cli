"""Round-6 exemption, recovery, local pairing and HTML growth families."""

from b16a_r4_complexity import complexity_criterion as prior_criterion
from b16a_r5_complexity import build as prior_build
from b16a_r5_complexity import families as prior_families

NEW = {
    "balanced_mustache": "token={{",
    "unclosed_dollar": "token=${X:-",
    "unclosed_getenv": "token=os.getenv(a ",
    "unclosed_environ": "token=os.environ[a ",
    "unclosed_double_brace": "token={{",
    "unclosed_angle": "token=<",
    "unclosed_percent": "token=%",
    "unclosed_optional": "token=Optional[",
    "nested_exempt": 'cookie=os.getenv("COOKIE", "set_cookie=a") ',
    "har_swallowed_candidates": '{"x":"{ { { { ","name":"sid","value":"a"} ',
    "har_late_failure": '{"x":',
    "pairing_apostrophes": "don't user's It's rock 'n' roll ",
    "pairing_stray_quotes": "note '\nnote \"\nnote `\n",
    "html_attributes": '<input name="password" value="a"> ',
    "html_benign": '<input name="username" value="alice"> ',
    "protocol_relative": "//alice:a@example.invalid/a ",
    "block_plus_spaces": "-----BE" + "GIN+PRIVATE+KEY-----\na\n",
    "json_exempt_quoted_punctuation": 'token=os.getenv("TOKEN_)") ',
    "triple_quotes": '"""\npassword=a\n"""\n',
}


def families():
    return prior_families() + tuple("r6_" + name for name in NEW)


def build(family, size):
    if not family.startswith("r6_"):
        return prior_build(family, size)
    if family == "r6_balanced_mustache":
        n = size // 10
        return "token={{" * n + "PUBLIC" + "}}" * n
    unit = NEW[family[3:]]
    return (unit * (size // len(unit) + 1))[:size]


def complexity_criterion(samples):
    _, slope, overall = prior_criterion(samples)
    return slope <= 1.20 and overall <= 12, slope, overall
