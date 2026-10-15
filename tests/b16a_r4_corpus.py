"""Seeded review generators, retained as repository tests rather than /tmp imports."""

import argparse
import json
import random
import re
import string
from collections import Counter
from urllib.parse import quote, quote_plus, unquote

from b16a_r3_corpus import FRAGMENTS, differential_corpus

from metamuse import safety

EXTRA = [
    "+",
    "%2B",
    "q=",
    "password%3A",
    "%3A+",
    "X%3DY",
    "note+",
    r"\u002b",
    r"\u003d",
    r"\u003f",
    "Bearer+",
    "mode=fast",
    "k=v",
    " level=high",
    "password+",
    "%3D",
    "+%3D+",
    "token%3D",
    "secret: ",
    "api_key=",
    "--password ",
    "-u ",
    "u:p@",
    "?a=1",
    "%2522",
    "%5C",
    "\\\\",
    "%0A",
]


def extended_corpus(n=200000):
    rng = random.Random(11)
    pool = FRAGMENTS + EXTRA * 3
    for _ in range(n):
        yield "".join(rng.choice(pool) for _ in range(rng.randint(1, 14)))


def realistic_notes(n=20000):
    rng = random.Random(5)
    names = ["password", "token", "secret", "api_key", "session", "passwd", "client_secret"]
    separators = [": ", "=", " = ", ": "]
    fillers = ["login with", "note", "then retry", "see", "and", "for the api", "q"]
    for _ in range(n):
        parts, markers = [], []
        for _ in range(rng.randint(1, 3)):
            value = (
                "Zq"
                + "".join(rng.choice(string.ascii_letters) for _ in range(6))
                + str(rng.randint(10, 99))
                + rng.choice(["", "=", "+/", "!", "@x"])
            )
            markers.append(value[:10])
            parts.extend((rng.choice(fillers), rng.choice(names) + rng.choice(separators) + value))
        encoder = rng.choice([quote_plus, lambda value: quote(value, safe="")])
        carrier = rng.choice(
            ["q={}", "https://example.invalid/search?q={}", "body: msg={}&x=1", "msg={}"]
        )
        yield carrier.format(encoder(" ".join(parts))), markers


def verify(corpus, *, tagged=False):
    distribution = Counter()
    stats = Counter()
    single = safety._redact_once
    changing = 0

    def counted(text):
        nonlocal changing
        output, findings = single(text)
        changing += output != text
        return output, findings

    safety._redact_once = counted
    try:
        for entry in corpus:
            text, markers = entry if tagged else (entry, [])
            changing = 0
            output, findings = safety.redact_text(text)
            distribution[changing] += 1
            unstable = any(row["kind"] == "redaction_unstable" for row in findings)
            stats["inputs"] += 1
            stats["unstable"] += unstable
            assert safety.redact_text(output) == (output, []), repr(text)
            if not tagged:
                check_named_markers(text, output, stats)
            for marker in markers:
                stats["markers"] += 1
                stats["visible"] += marker in output
                assert marker not in output, (text, output)
                assert not any(marker[i : i + 6] in output for i in range(len(marker) - 5)), (
                    text,
                    output,
                )
            if tagged:
                assert not unstable, repr(text)
    finally:
        safety._redact_once = single
    return {**dict(stats), "changing_passes": dict(sorted(distribution.items()))}


def check_named_markers(text, output, stats):
    """Independent narrow oracle for unambiguous named synthetic credentials."""

    def canonical(value):
        for _ in range(3):
            value = unquote(value)
            value = re.sub(
                r'\\(?:u[0-9a-fA-F]{4}|["\\/bfnrt])',
                lambda match: json.loads('"' + match[0] + '"'),
                value,
            )
        return value

    original, public = canonical(text), canonical(output)
    pattern = re.compile(
        r"(?<![\w.-])(?P<quote>[\"']?)(?:--|-D|-)?"
        r"(?:password|passwd|token|secret|hatch_sess|mm_sess)"
        r"(?P=quote)\s*(?:=>|[=:])\s*[\"']?(?P<marker>SYNTH[\w]+)",
        re.I,
    )
    markers = Counter()
    for match in pattern.finditer(original):
        if match["quote"] and original[: match.start()].rstrip().endswith((":", "=")):
            continue
        markers[match["marker"]] += 1
    for marker, occurrences in markers.items():
        stats["named_markers_checked"] += occurrences
        assert public.count(marker) <= original.count(marker) - occurrences, (text, output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--subset", type=int)
    args = parser.parse_args()
    from itertools import islice

    for name, corpus, tagged in (
        ("author", differential_corpus(), False),
        ("reviewer", extended_corpus(), False),
        ("realistic", realistic_notes(), True),
    ):
        print(
            json.dumps(
                {
                    "corpus": name,
                    **verify(islice(corpus, args.subset) if args.subset else corpus, tagged=tagged),
                }
            ),
            flush=True,
        )
