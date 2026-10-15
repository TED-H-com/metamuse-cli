"""Closer/follower products use the public redactor and retain exact cut evidence."""

import itertools
import json
import random
import string
import time
from collections import Counter

from metamuse.safety import redact_text

SEED = 712026
MARKER = f"{'CLASS7'}_MARKER_PRIVATE_824391"
CLOSERS = "'\"`”’)]}>"
FOLLOWERS = (*string.punctuation, "a", "1", " ", "")
PREFIXES = ("", "the users' ", "r'\\' ", '"unclosed ', "[", "(", "{", "<", "'x' ", '"x" ')
WRAPPERS = ("", "'", '"', "adjacent:'", 'adjacent:"', "adjacent:(", "adjacent:[")
FAMILIES = {
    "assignment": "password={v}",
    "header": "Cookie: {v}",
    "flag": "run --password {v}",
    "curl_user": 'cu' + 'rl ' + '-u alice:{v} https://example.invalid',
    "curl_cookie": 'cu' + 'rl -b {v} https://example.invalid',
    "sshpass": "sshpass -p {v} ssh host",
    "mysql": "mysql -p{v}",
    "sql": "CREATE USER u IDENTIFIED BY {v};",
    "cookie": "theme=dark; sid={v}",
    "userinfo": "https://alice:{v}@example.invalid/p",
    "query": "https://example.invalid/p?token={v}",
    "form": "body=password={v}&user=bob",
    "xml": "<password>{v}</password>",
    "html": "<input name=password value={v}>",
    "yaml": "password: |2-\n  {v}\nnext: public",
    "putty": "Pu"
    + "TTY-User-Key-File-3: ssh-rsa\nComment: {v}\nPrivate-Lines: 1\n"
    + MARKER
    + "\nPrivate-MAC: public",
}


def rows(*, full=False):
    product = list(itertools.product(FAMILIES, CLOSERS, FOLLOWERS, PREFIXES, WRAPPERS))
    if not full:
        rng = random.Random(SEED)
        product = rng.sample(product, 1000)
    for family, closer, follower, prefix, wrapper in product:
        # Password whitespace and URL /?# are syntax terminators, not delimiters;
        # preserve that existing SPEC category separately from owned D2 cuts.
        value = "pa" + closer + follower + MARKER
        adjacent = wrapper.startswith("adjacent:")
        opener = wrapper.split(":", 1)[1] if adjacent else wrapper
        matching = {"'": "'", '"': '"', "(": ")", "[": "]"}.get(opener, "")
        if not adjacent and opener:
            value = opener + value + matching
        text = FAMILIES[family].format(v=value)
        if adjacent:
            text = opener + text + matching
        text = prefix + text
        yield family, closer, follower, prefix, wrapper, text


def category(family, closer, follower, prefix, wrapper):
    """Independent policy oracle: distinguish a value terminator from a closer."""
    own = wrapper in {"'", '"'}
    if family == "userinfo" and (closer in "/?#" or follower in {"/", "?", "#", "\\", " "}):
        return "literal_url_terminator"
    if family == "query" and not own and follower in {"&", "#", " "}:
        return "literal_syntax_terminator"
    if family == "html" and not own and (closer == ">" or follower in {">", " "}):
        return "literal_syntax_terminator"
    if family == "html" and own and closer == wrapper and follower == ">":
        return "literal_syntax_terminator"
    if (
        not own
        and follower == " "
        and family
        in {
            "assignment",
            "flag",
            "curl_user",
            "curl_cookie",
            "sshpass",
            "mysql",
            "sql",
            "cookie",
            "form",
        }
    ):
        return "literal_syntax_terminator"
    valid = follower in {" ", ",", ";", "&"}
    if own and closer == wrapper and valid:
        return "owned_raw_closer_with_follower"
    owned = []
    if wrapper.startswith("adjacent:"):
        owned.append(wrapper.split(":", 1)[1])
    if prefix in {"[", "(", "{", "<"}:
        owned.append(prefix)
    pairs = {"'": "'", '"': '"', "(": ")", "[": "]", "{": "}", "<": ">"}
    if not own and valid and any(pairs[o] == closer for o in owned):
        return "owned_raw_closer_with_follower"
    return None


def verify(*, full=False):
    counts = Counter()
    failures = []
    started = time.monotonic()
    for family, closer, follower, prefix, wrapper, text in rows(full=full):
        out, findings = redact_text(text)
        counts["rows"] += 1
        counts["family:" + family] += 1
        accepted = category(family, closer, follower, prefix, wrapper)
        if accepted:
            counts["category:" + accepted] += 1
        if MARKER in out:
            counts["visible"] += 1
            if accepted:
                counts["visible:" + accepted] += 1
            else:
                counts["leaks"] += 1
                if len(failures) < 100:
                    failures.append(
                        [family, closer, follower, prefix, wrapper, text, out, findings]
                    )
        if redact_text(out)[0] != out:
            counts["nonfixpoint"] += 1
    return {
        "seed": SEED,
        "counts": dict(counts),
        "failures": failures,
        "wall": time.monotonic() - started,
    }


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--full", action="store_true")
    a = p.parse_args()
    print(json.dumps(verify(full=a.full)))
