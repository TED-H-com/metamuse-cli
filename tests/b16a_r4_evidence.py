"""Reproducible offline password, handoff and benign measurements."""

import ast
import base64
import json
import random
import string
import sysconfig
from pathlib import Path
from urllib.parse import quote, quote_plus

from b16a_r3_corpus import HANDOFF, REVIEW_BENIGN2

from metamuse.safety import redact_text


def literal(file, name):
    tree = ast.parse(Path(__file__).with_name(file).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(name)


BENIGN = literal("test_b16a_round2.py", "BENIGN")
CODE = literal("test_b16a_round4_cookie.py", "BENIGN")
INVALID_PREFIX_HANDOFF = literal("test_b16a_round3.py", "INVALID_PREFIX_HANDOFF")
URL_TERMINATOR_HANDOFF = literal("test_b16a_round3.py", "URL_TERMINATOR_HANDOFF")


def stdlib_lines():
    root = Path(sysconfig.get_paths()["stdlib"])
    for directory in (root, root / "json", root / "email"):
        for path in sorted(directory.glob("*.py")):
            for line in path.read_text().splitlines():
                text = line.strip()
                if "=" in text and not text.startswith("#") and len(text) < 300:
                    yield text


def survives(output, value):
    return any(value[i : i + 6] in output for i in range(len(value) - 5))


def passwords():
    for pool_name, punctuation in [
        ("r2charset", "!@#$%^&*()-_=+"),
        ("full_punct", string.punctuation),
    ]:
        rng = random.Random(1014)
        values = []
        while len(values) < 300:
            value = "".join(
                rng.choice(string.ascii_letters + string.digits + punctuation) for _ in range(16)
            )
            if any(c in punctuation for c in value) and any(c.isdigit() for c in value):
                values.append(value)
        for shape in (
            "env",
            "kv",
            "yaml",
            "json",
            "toml",
            "basic",
            "flag",
            "form_first",
            "form_mid",
            "url_query",
            "userinfo_enc",
            "userinfo_raw",
            "url_query_raw",
        ):
            counts = dict(
                n=0,
                verbatim=0,
                partial6=0,
                refused=0,
                excluded=0,
                remainder_verbatim=0,
                remainder_partial6=0,
            )
            for value in values:
                terminators = "/?#\\" + ("" if shape == "userinfo_raw" else "&")
                excluded = shape in {"userinfo_raw", "url_query_raw", "url_query"} and any(
                    c in value for c in terminators
                )
                secret = (
                    base64.b64encode(("svc:" + value).encode()).decode()
                    if shape == "basic"
                    else value
                )
                toml = value.replace("\\", "\\\\").replace('"', '\\"')
                text = {
                    "env": "DB_PASSWORD=" + value,
                    "kv": "password=" + value,
                    "yaml": "password: " + value,
                    "json": json.dumps({"password": value}),
                    "toml": 'password = "' + toml + '"',
                    "basic": "Authorization: Basic " + secret,
                    "flag": "tool --password=" + value + " --verbose",
                    "form_first": "password=" + quote_plus(value) + "&username=bob",
                    "form_mid": "username=bob&password=" + quote_plus(value) + "&remember=1",
                    "url_query": "https://example.invalid/?password="
                    + quote(value, safe="")
                    + "&v=1",
                    "userinfo_enc": "postgres://app:"
                    + quote(value, safe="")
                    + "@example.invalid/app",
                    "userinfo_raw": "postgres://app:" + value + "@example.invalid/app",
                    "url_query_raw": "https://example.invalid/?password=" + value + "&v=1",
                }[shape]
                representations = [secret]
                if shape in {"form_first", "form_mid", "url_query", "userinfo_enc"}:
                    representations.extend((quote_plus(secret), quote(secret, safe="")))
                out, findings = redact_text(text)
                if excluded:
                    counts["excluded"] += 1
                    counts["remainder_verbatim"] += any(v in out for v in representations)
                    counts["remainder_partial6"] += any(survives(out, v) for v in representations)
                    continue
                counts["n"] += 1
                counts["verbatim"] += any(v in out for v in representations)
                counts["partial6"] += any(survives(out, v) for v in representations)
                counts["refused"] += any(f["kind"] != "local_path" for f in findings)
            yield dict(table="password", pool=pool_name, shape=shape, **counts)


def bearers():
    rng = random.Random(1014)
    for length in (24, 32, 48):
        tokens = [base64.b64encode(rng.randbytes(length)).decode() for _ in range(1000)]
        for name, template in (
            ("q_before", "Is this token still valid? Bearer {t}"),
            ("q_after", "Bearer {t} is rejected, why?"),
            ("url_before", "Calling /v1/items?limit=10 with Bearer {t} returns 401"),
        ):
            results = [(token, *redact_text(template.format(t=token))) for token in tokens]
            yield dict(
                table="bearer",
                length=len(tokens[0]),
                template=name,
                n=len(tokens),
                with_plus=sum("+" in token for token in tokens),
                accepted=sum(
                    not any(f["kind"] != "local_path" for f in fs) for _, _, fs in results
                ),
                verbatim=sum(token in out for token, out, _ in results),
                partial6=sum(survives(out, token) for token, out, _ in results),
            )


def other_tables():
    eligible = [
        row for row in HANDOFF if row[0] not in INVALID_PREFIX_HANDOFF | URL_TERMINATOR_HANDOFF
    ]
    for name, rows in [("handoff", eligible)]:
        outputs = [(text, marker, *redact_text(text)) for _, text, marker in rows]
        yield dict(
            table=name,
            n=len(rows),
            refused=sum(any(r["kind"] != "local_path" for r in f) for _, _, _, f in outputs),
            verbatim=sum(marker in out for text, marker, out, _ in outputs if marker in text),
            partial6=sum(
                any(
                    marker[i : i + 6] in out and marker[i : i + 6] not in text.replace(marker, "")
                    for i in range(len(marker) - 5)
                )
                for text, marker, out, _ in outputs
                if marker in text
            ),
            incidental_syntax_matches=sum(
                survives(out, marker) for text, marker, out, _ in outputs if marker in text
            ),
            excluded=len(HANDOFF) - len(rows),
        )
    for name, corpus in [
        ("round1_round2", BENIGN),
        ("review_benign2", REVIEW_BENIGN2.values()),
        ("six_code_examples", CODE),
        ("stdlib", list(stdlib_lines())),
    ]:
        outputs = [(text, *redact_text(text)) for text in corpus]
        yield dict(
            table="benign",
            corpus=name,
            n=len(outputs),
            changed=sum(t != out for t, out, _ in outputs),
            refused=sum(any(r["kind"] != "local_path" for r in f) for _, _, f in outputs),
        )


if __name__ == "__main__":
    for row in [*passwords(), *bearers(), *other_tables()]:
        print(json.dumps(row), flush=True)
