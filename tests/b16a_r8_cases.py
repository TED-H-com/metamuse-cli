"""Confirmed separator cases and their existing canonical representations."""

import json
from urllib.parse import quote, quote_plus

from b16a_r6_context import Row

CURL = "cu" + "rl"
MARKER = f"{'D10'}_CONTINUATION_SYNTHETIC_358713"


def private_cases():
    for flag in ("-u", "-b"):
        value = "alice:" + MARKER if flag == "-u" else MARKER
        for name, separator in (("lf", "\n"), ("crlf", "\r\n")):
            yield (
                f"continuation:{name}:{flag}",
                CURL + " " + chr(92) + separator + flag + " " + value,
            )
        for separator in ";|&":
            for count in (1, 3):
                yield (
                    f"escape:{ord(separator)}:{count}:{flag}",
                    f"{CURL} x" + chr(92) * count + separator + f"y {flag} {value}",
                )
        for delimiter in ('"', "'", "`", "$'"):
            for separator in (";", "|", "&", "\n", "\r\n"):
                yield (
                    f"quoted:{delimiter!r}:{repr(separator)}:{flag}",
                    f"{CURL} -H {delimiter}X:A{separator}B{delimiter[-1]} {flag} {value}",
                )
        yield f"output_escape:{flag}", f"{CURL} --output " + chr(92) + f"; {flag} {value}"
        yield (
            f"consumed_escape:{flag}",
            f"{CURL} {flag} " + ("alice:x" if flag == "-u" else "x") + chr(92) + f"; -b {MARKER}",
        )
        for separator in ";|&":
            value = "${A:-one" + separator + MARKER + "}"
            yield (
                f"owned_default:{flag}:{separator}",
                f"{CURL} {flag} " + ("alice:" if flag == "-u" else "") + value,
            )
    for separator in ";|&":
        yield (
            f"exempt_lookup:{separator}",
            f'{CURL} -b os.getenv("public{separator}message") -b {MARKER}',
        )


def representations(text):
    yield "raw", text
    yield "json1", json.dumps({"cmd": text})
    yield "json2", json.dumps(json.dumps({"cmd": text}))
    yield "percent", quote(text, safe="")
    yield "form", "q=" + quote_plus(text)


def sql_cases():
    for clause in ("IDENTIFIED BY", "with password"):
        for principal in ("USER", "role", "LOGIN"):
            yield (
                f"sql_later:{clause}:{principal}",
                f"CREATE {clause} '{MARKER}' {principal} alice",
                True,
            )
    for statement in ("CREATE", "alter", "GRANT", "set"):
        yield (
            f"sql_forward:{statement}",
            f"{statement} IDENTIFIED BY 'public' USER alice; "
            f"{statement} USER bob IDENTIFIED BY '{MARKER}'; WITH PASSWORD 'public'",
            False,
        )


def rows():
    for family, text in private_cases():
        for representation, encoded in representations(text):
            rep = {"json2": "json_in_json", "percent": "pct1", "form": "form_plus"}.get(
                representation, representation
            )
            yield Row("matrix:r8:" + family + ":" + rep, encoded, (MARKER,))
    for family, text, benign in sql_cases():
        for representation, encoded in representations(text):
            rep = {"json2": "json_in_json", "percent": "pct1", "form": "form_plus"}.get(
                representation, representation
            )
            yield Row(
                "matrix:r8:" + family + ":" + rep,
                encoded,
                () if benign else (MARKER,),
                benign=benign,
            )
