"""Synthetic affected-header rows and representations for the final-CI repair."""

import json
from urllib.parse import quote, quote_plus

from b16a_r6_context import Row

REPRESENTATIONS = ("raw", "json", "json_in_json", "jsonesc", "pct1", "pct2", "pct3", "form_plus")
PUBLIC = "PUBLIC_BOUNDARY_7793"
URL_CASES = (
    (
        "https://www.facebook.com/x=1;y=2/videos/111",
        "https://www.facebook.com/[REDACTED]",
    ),
    (
        "https://www.facebook.com/watch/?v=111;x=1",
        "https://www.facebook.com/watch/?[REDACTED]",
    ),
    (
        "https://MEDIA.fixture.test:443/one;public=1?note=visible&public=1",
        "https://MEDIA.fixture.test:443/one;public=1?note=visible&public=1",
    ),
    ("https://example.invalid/x=1", "https://example.invalid/x=1"),
    ("https://example.invalid/path;public=1", "https://example.invalid/path;public=1"),
)


def private_url_part(text):
    return text.split("?", 1)[1] if "?" in text else text.split("com/", 1)[1]


def samples():
    first = f"{'Zq71Md'}_FIRST_SYNTHETIC_359821"
    tail = f"{'Yt82Ne'}_TAIL_SYNTHETIC_468932"
    for name, text, kind in (
        ("cookie_crlf", f"Cookie: a={first};\r\n b={tail}", "cookie"),
        ("cookie_lf_tab", f"cOoKiE: a={first};\n\tb={tail}", "cookie"),
        ("cookie_colon_tail", f"Cookie: a={first};\r\n b: {tail}", "cookie"),
        ("cookie_quoted", f'Cookie: "a={first};"\r\n b="left {tail} right"', "cookie"),
        ("cookie_escaped", f'Cookie: a="left\\"{first}";\n b={tail}', "cookie"),
        ("set_cookie", f"Set-Cookie: a={first};\r\n b={tail}; HttpOnly", "cookie"),
        ("cookie_header", f"cookie_header=a={first};\n b={tail}", "session_cookie"),
        ("authorization", f"Authorization: Bearer {first}\r\n response={tail}", "authorization"),
        (
            "authorization_colon_tail",
            f"Authorization: Basic {first}\n response: {tail}",
            "authorization",
        ),
        ("proxy_authorization", f"Proxy-Authorization: Basic {first}\n\t{tail}", "authorization"),
    ):
        yield name, text, (first, tail), kind


def encode(text, representation):
    if representation == "json":
        return json.dumps({"log": text})
    if representation == "json_in_json":
        return json.dumps(json.dumps({"log": text}))
    if representation == "jsonesc":
        return json.dumps(text)[1:-1]
    if representation.startswith("pct"):
        for _ in range(int(representation[-1])):
            text = quote(text, safe="")
        return text
    if representation == "form_plus":
        return "q=" + quote_plus(text)
    assert representation == "raw"
    return text


def benign():
    return (
        "The Authorization header carries a bearer token",
        "Cookie: [REDACTED]\r\nX-Public: public",
        "Authorization: Bearer\nPublic note without a credential",
        "Cookie: null\r\n X-Public: public",
    )


def rows():
    for name, text, markers, _ in samples():
        for representation in REPRESENTATIONS:
            yield Row(
                f"matrix:folded_{name}:{representation}", encode(text, representation), markers
            )
    for index, text in enumerate(benign()):
        for representation in REPRESENTATIONS:
            # Without a containing JSON string, an escaped newline is deeper
            # value data, not a same-layer header terminator (SPEC layer rule).
            private_tail = index == 3 or (representation == "jsonesc" and index > 0)
            yield Row(
                f"matrix:folded_benign_{index}:{representation}",
                encode(text, representation),
                ("Public" if index == 2 else "public",) if private_tail else (),
                benign=not private_tail,
            )
    for index, (text, expected) in enumerate(URL_CASES):
        for representation in REPRESENTATIONS:
            yield Row(
                f"matrix:legacy_cookie_url_{index}:{representation}",
                encode(text, representation),
                (private_url_part(text),) if expected != text else (),
                benign=expected == text,
            )
