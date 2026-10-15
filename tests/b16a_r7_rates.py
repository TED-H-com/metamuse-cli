"""Reproducible seeded password, Bearer, handoff and review-neighbour tables."""

import base64
import json
import random
import string
from collections import Counter
from urllib.parse import quote_plus

from b16a_r3_corpus import HANDOFF
from b16a_r6_neighbours import generate_rows

from metamuse.safety import redact_text


def outcome(text, value):
    out, found = redact_text(text)
    return Counter(
        cases=1,
        no_finding=int(not any(f["kind"] != "local_path" for f in found)),
        verbatim=int(value in out),
        raw_visible_piece=int(any(value[i : i + 6] in out for i in range(len(value) - 5))),
        visible_piece=int(
            any(
                value[i : i + 6] in out and value[i : i + 6] not in text.replace(value, "")
                for i in range(len(value) - 5)
            )
        ),
        nonfixpoint=int(redact_text(out)[0] != out),
    )


def measure():
    rng = random.Random(1014)
    for shape, template in {
        "env": "DB_PASSWORD={v}",
        "kv": "password={v}",
        "yaml": "password: {v}",
        "json": '{{"password": "{v}"}}',
        "toml": 'password = "{v}"',
        "basic": "Authorization: Basic {v}",
    }.items():
        stats = Counter()
        pool = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
        for _ in range(300):
            while True:
                value = "".join(rng.choice(pool) for _ in range(16))
                if any(c in "!@#$%^&*()-_=+" for c in value) and any(c.isdigit() for c in value):
                    break
            if shape == "basic":
                value = base64.b64encode(("svc:" + value).encode()).decode()
            stats.update(outcome(template.format(v=value), value))
        yield {"table": "password_round3", "shape": shape, **stats}
    for shape in ("env", "kv", "flag", "form_first", "form_mid", "userinfo_raw", "url_query"):
        rng = random.Random(1014)
        stats = Counter()
        pool = string.ascii_letters + string.digits + string.punctuation
        for _ in range(300):
            while True:
                value = "".join(rng.choice(pool) for _ in range(16))
                if any(c in string.punctuation for c in value) and any(c.isdigit() for c in value):
                    break
            if shape in {"userinfo_raw", "url_query"} and any(c in value for c in "/?#\\&"):
                stats["excluded_url_grammar"] += 1
                continue
            text = {
                "env": "DB_PASSWORD=" + value,
                "kv": "password=" + value,
                "flag": "tool --password=" + value + " --verbose",
                "form_first": "password=" + quote_plus(value) + "&username=bob",
                "form_mid": "username=bob&password=" + quote_plus(value) + "&scope=read",
                "userinfo_raw": "https://user:" + value + "@example.invalid/a",
                "url_query": "https://example.invalid/?password=" + value + "&view=1",
            }[shape]
            stats.update(outcome(text, value))
        yield {"table": "password_full_punctuation", "shape": shape, **stats}
    for length in (24, 32, 48):
        rng = random.Random(1014)
        tokens = [base64.b64encode(rng.randbytes(length)).decode() for _ in range(300)]
        for shape, template in enumerate(
            (
                "Is this token still valid? Bearer {t}",
                "Calling /v1/items?limit=10 with Bearer {t} returns 401",
                "Bearer {t} is rejected, why?",
            )
        ):
            stats = Counter()
            for value in tokens:
                stats.update(outcome(template.format(t=value), value))
            yield {
                "table": "bearer",
                "token_chars": len(tokens[0]),
                "shape": shape,
                "tokens_with_plus": sum("+" in t for t in tokens),
                **stats,
            }
    excluded = {
        "ghp_prefix",
        "xoxb_prefix",
        "aws_akid",
        "glpat",
        "stripe_sk_live",
        "google_aiza",
        "ui_pw_has_slash",
        "ui_pw_has_hash",
        "ui_pw_has_question",
    }
    for name, text, marker in HANDOFF:
        if name in excluded or marker not in text:
            continue
        yield {"table": "handoff", "shape": name, **outcome(text, marker)}
    grouped = {}
    for shape, text, markers, benign in generate_rows():
        stats = grouped.setdefault(shape, Counter())
        out, found = redact_text(text)
        stats["rows"] += 1
        if benign:
            stats["benign"] += 1
            stats["changed"] += (out, found) != (text, [])
        else:
            stats["private"] += 1
            stats["leaks"] += any(m in out for m in markers)
        stats["nonfixpoint"] += redact_text(out)[0] != out
    for shape, stats in grouped.items():
        yield {"table": "review_neighbours", "shape": shape, **stats}


if __name__ == "__main__":
    for row in measure():
        print(json.dumps(row), flush=True)
