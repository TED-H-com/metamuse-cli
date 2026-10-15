"""Seeded class matrix ported from the independent review, with structural neighbours."""

import json
import random
from dataclasses import dataclass
from urllib.parse import quote, quote_plus

SPECIALS = [
    "",
    "!",
    "'",
    '"',
    ",",
    ";",
    "]",
    ")",
    "}",
    ">",
    "<",
    "#",
    "&",
    "\\",
    " ",
    "%",
    "=",
    ":",
    "@",
    "/",
    "?",
    "+",
    "é",
    "`",
    "|",
    "*",
    "$",
    "^",
    "(",
    "[",
    "{",
    "~",
]

NAMES = ["password", "api_key", "access_token", "client_secret", "DB_PASSWORD", "hatch_sess"]

SYNTAX = {
    "eq": "{n}={v}",
    "eq_sp": "{n} = {v}",
    "colon": "note {n}: {v}",
    "arrow": "{n} => {v}",
    "json": '{{"{n}": "{v}"}}',
    "json_sq": "{{'{n}': '{v}'}}",
    "toml": '{n} = "{v}"',
    "toml_ml": '{n} = """{v}"""',
    "yaml_dq": '{n}: "{v}"',
    "yaml_block": "{n}: |\n  {v}\nnext: 1",
    "xml": "<{n}>{v}</{n}>",
    "xml_attr": '<{n} kind="x">{v}</{n}>',
    "xml_attrval": '<cfg {n}="{v}"/>',
    "flag_eq": "run --{n}={v} --x",
    "flag_sp": "run --{n} {v} --x",
    "flag_D": "java -D{n}={v} -jar a.jar",
    "export": "export {n}={v}",
    "export_q": 'export {n}="{v}"',
    "env_sq": "{n}='{v}'",
}

OTHER = {
    "curl_u": "curl " + "-u user:{v} https://example.invalid/",
    "authz_basic": "Authorization: Basic {v}",
    "proxy_digest": "Proxy-Authorization: Digest {v}",
    "cookie_opaque": "Cookie: {v}",
    "cookie_pair": "Cookie: theme=dark; sid={v}",
    "setcookie": "Set-Cookie: sid={v}; Path=/; HttpOnly",
    "userinfo": "https://user:{v}@example.invalid/x",
    "userinfo_pg": "postgres://app:{v}@db.example.invalid:5432/app",
    "query": "https://example.invalid/cb?access_token={v}&view=1",
    "query_first": "https://example.invalid/cb?view=1&token={v}",
    "form_first": "password={v}&user=bob",
    "jar": "example.invalid\tTRUE\t/\tFALSE\t0\tsid\t{v}",
}


@dataclass(frozen=True)
class Row:
    shape: str
    text: str
    markers: tuple[str, ...] = ()
    category: str | None = None
    benign: bool = False


def encodings(text):
    yield "raw", text
    value = text
    for n in range(1, 4):
        value = quote(value, safe="")
        yield f"pct{n}", value
    encoded = quote(text, safe="")
    yield (
        "pct1_lower",
        "".join(
            c.lower() if (i and encoded[i - 1] == "%") or (i > 1 and encoded[i - 2] == "%") else c
            for i, c in enumerate(encoded)
        ),
    )
    yield "form_plus", "q=" + quote_plus(text)
    yield "jsonesc", json.dumps(text)[1:-1]
    yield "json_in_json", json.dumps(json.dumps({"log": text}))


def category(shape, special, encoding):
    if special.isspace():
        if shape in {
            "eq",
            "eq_sp",
            "colon",
            "arrow",
            "flag_eq",
            "flag_sp",
            "flag_D",
            "export",
            "curl_u",
            "userinfo",
            "userinfo_pg",
            "query",
            "query_first",
            "form_first",
        }:
            return "literal_syntax_terminator"
    if special == '"' and shape in {"json", "toml", "yaml_dq", "export_q", "xml_attrval"}:
        return "literal_syntax_terminator"
    if special == "'" and shape in {"json_sq", "env_sq"}:
        return "literal_syntax_terminator"
    if (
        (shape == "userinfo" and special in "/?#\\")
        or (shape == "userinfo_pg" and special in "/?#")
    ) and special:
        return "literal_url_terminator"
    if (shape in {"query", "query_first", "form_first"} and special in {"&", "#"}) or (
        shape == "form_first" and special == ";"
    ):
        return "literal_syntax_terminator"
    if shape in {"query", "query_first"} and special == "?" and encoding != "raw":
        return "encoded_query_question_terminator"
    return None


def reviewer_matrix():
    first = f"{'SYN'}THqheadqVAL{4242}"
    last = f"{'SYN'}THqtailqVAL{4242}"
    for shape, template in {**SYNTAX, **OTHER}.items():
        for name in NAMES if shape in SYNTAX else [""]:
            for special in SPECIALS:
                value = first + special + last
                raw = template.format(n=name, v=value)
                for encoding, text in encodings(raw):
                    yield Row(
                        shape + ":" + encoding,
                        text,
                        (first, last),
                        category(shape, special, encoding),
                    )


def neighbours():
    from test_b16a_round5_blocks import block
    from test_b16a_round5_quotes import RULES, context
    from test_b16a_round5_urls import FIRST, JOINS

    def encode(text, layer):
        if layer.startswith("percent"):
            for _ in range(int(layer[-1])):
                text = quote(text, safe="")
            return text
        for _ in range(int(layer)):
            text = json.dumps(text)
        return text

    def class_encodings(text):
        yield from encodings(text)
        value = text
        for _ in range(3):
            value = json.dumps({"capture": value})
        yield "json3", value
        value = "q=" + quote_plus(text)
        for n in (2, 3):
            value = "q=" + quote_plus(value)
            yield f"nested_form{n}", value

    marker = f"{'NEIGHBOUR'}_MARKER_PRIVATE_VALUE"
    for first in FIRST:
        for join in JOINS:
            for layer in ("0", "1", "2", "3", "percent1", "percent2", "percent3"):
                yield Row(
                    "authority",
                    encode(first + join + f"https://alice:{marker}@example.invalid/a", layer),
                    (marker,),
                )
                if first in FIRST[:4]:
                    for mirror in ("alice@example.invalid", "@handle"):
                        yield Row(
                            "authority_mirror",
                            encode(json.dumps([first, join, mirror]), layer),
                            benign=True,
                        )
    for layer in (1, 2, 3):
        second = f"https://alice:{marker}@example.invalid/a"
        for _ in range(layer):
            second = quote(second, safe="")
        yield Row("mixed_layer_authority", "https://example.invalid|" + second, (marker,))
        # A real enclosing JSON boundary distinguishes an email from userinfo.
        yield Row(
            "mixed_layer_authority_mirror",
            json.dumps(["https://example.invalid", quote("alice@example.invalid", safe="")]),
            benign=True,
        )
    for rule in RULES:
        for layer in ("0", "1", "2", "3", "shell", "percent1", "percent2", "percent3"):
            yield Row("quoted_argument", context(rule.format(v=marker), layer), (marker,))
            yield Row("call_mirror", context('token=os.getenv("TOKEN")', layer), benign=True)
    for earlier in ('5" screen', "https://example.invalid/?q=%22x"):
        for reverse in (False, True):
            entry = (
                {"value": marker, "name": "sid"} if reverse else {"name": "sid", "value": marker}
            )
            for _, text in class_encodings(
                json.dumps(
                    {
                        "earlier": earlier,
                        "headers": [entry],
                        "cookies": [{"name": "theme", "value": marker}],
                    }
                )
            ):
                yield Row("har", text, (marker,))
            for _, text in class_encodings(
                json.dumps(
                    {
                        "earlier": earlier,
                        "entries": [{"name": "q", "value": earlier}],
                        "cookies": [],
                    }
                )
            ):
                yield Row("har_mirror", text, benign=True)
    for earlier in ('5" screen', "https://example.invalid/?q=%22x"):
        for _, text in class_encodings(
            json.dumps({"earlier": earlier, "cookies": [{"name": "theme", "value": marker}]})
        ):
            yield Row("har_without_sensitive_container", text, (marker,))
    number = f"{98765432}{10123456}"
    for reverse in (False, True):
        entry = (
            {"value": int(number), "name": "sid"}
            if reverse
            else {"name": "sid", "value": int(number)}
        )
        for _, text in class_encodings(json.dumps({"headers": [entry]})):
            yield Row("har_scalar", text, (number,))
        public_entry = dict(entry, name="theme")
        for _, text in class_encodings(json.dumps({"entries": [public_entry]})):
            yield Row("har_scalar_mirror", text, benign=True)
        entry = {"value": marker, "name": "sid"} if reverse else {"name": "sid", "value": marker}
        # A partial value-first object cannot establish its later sensitive name.
        complete = json.dumps({"headers": [entry]})
        for _, text in class_encodings(complete[:-2]):
            yield Row("har_partial", text, (marker,))
    form = "q=" + quote_plus(json.dumps({"cookies": [{"name": "theme", "value": marker}]}))
    for _depth in range(3):
        yield Row("encoded_form_har", form, (marker,))
        form = quote(form, safe="")
    for kind in ("putty", "pgp", "pem"):
        for ending in ("\n", "\r\n"):
            for closed in (False, True):
                raw = block(kind, marker, closed).replace("\n", ending)
                for _, text in class_encodings("key_file: " + raw):
                    yield Row("key_block", text, (marker,))
                public = raw.replace("PRIVATE", "PUBLIC").replace(
                    "Pu" + "TTY-User-Key-File", "Pu" + "TTY-User-Public-Key-File"
                )
                for _, text in class_encodings("key_file: " + public):
                    yield Row("key_block_mirror", text, benign=True)
    for shape in (
        'password=${X:-"api_key={v}"}',
        'token=os.getenv("T", "password={v}")',
        'password="<password={v}>"',
    ):
        for _, text in class_encodings(shape.replace("{v}", marker)):
            yield Row("nested_exempt", text, (marker,))
    for text in (
        'password=${X:-"user_name=public"}',
        'token=os.getenv("T", "theme=public")',
        'password="<user_name=public>"',
        "password=$VAR",
        "password=${A:-${B}}",
        'token=os.getenv("TOKEN")',
    ):
        for _, encoded in class_encodings(text):
            yield Row("exempt_mirror", encoded, benign=not text.startswith("password=${X:-"))


CATEGORIES = {
    "literal_syntax_terminator",
    "literal_url_terminator",
    "encoded_query_question_terminator",
}


def verify(rows):
    from collections import Counter

    from metamuse.safety import redact_text

    counts = Counter()
    examples = []
    for row in rows:
        counts["rows"] += 1
        output, findings = redact_text(row.text)
        counts["nonfixpoint"] += redact_text(output)[0] != output
        if row.category is not None:
            assert row.category in CATEGORIES, row
            counts["out_of_scope:" + row.category] += 1
            # Authority-terminated candidates have no userinfo at all; check
            # their first marker's documented visibility instead of skipping it.
            first = row.markers[0] if row.markers else None
            if first:
                visible_first = first in output
                counts["excluded_first_visible"] += visible_first
                if row.category != "literal_url_terminator" and not row.shape.startswith(
                    ("userinfo:", "userinfo_pg:")
                ):
                    assert not visible_first, (row, output)
        elif row.benign:
            counts["benign"] += 1
            counts["benign_changed"] += (output, findings) != (row.text, [])
            if (output, findings) != (row.text, []) and len(examples) < 20:
                examples.append((row.shape, row.text, output, findings))
        else:
            counts["in_scope"] += 1
            leaked = any(
                marker in output
                or quote(marker, safe="") in output
                or any(
                    marker[i : i + 6] in output
                    and marker[i : i + 6] in row.text
                    and marker[i : i + 6] not in row.text.replace(marker, "")
                    for i in range(len(marker) - 5)
                )
                for marker in row.markers
            )
            counts["leaks"] += leaked
            if leaked and len(examples) < 20:
                examples.append((row.shape, row.text, output, findings))
    return {**counts, "examples": examples}


def seeded_subset():
    rng = random.Random(1015)
    for row in reviewer_matrix():
        if rng.randrange(40) == 0:
            yield row
    yield from neighbours()


if __name__ == "__main__":
    for name, rows in (("reviewer_32256", reviewer_matrix()), ("neighbours", neighbours())):
        print(json.dumps({"table": name, **verify(rows)}), flush=True)
