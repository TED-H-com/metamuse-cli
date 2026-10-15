"""Seeded surrounding-context property, with explicit family coverage."""

import argparse
import json
import random
import time
from collections import Counter
from dataclasses import dataclass
from urllib.parse import quote, quote_plus, unquote, unquote_plus

from b16a_r5_corpus import neighbours, reviewer_matrix
from b16a_r6_neighbours import generate_rows

from metamuse.safety import redact_text

SEED = 10152026
NATURAL = (
    ("contraction", "Here's what I ran: {x}"),
    ("dont", "don't run {x}"),
    ("its", "It's failing: {x}"),
    ("possessive", "the user's config: {x}"),
    ("rock", "rock 'n' roll {x}"),
    ("stray_double", 'note " {x}'),
    ("stray_single", "note ' {x}"),
    ("stray_backtick", "note ` {x}"),
    ("triple_double", 'doc = """{x}"""'),
    ("triple_single", "doc = '''{x}'''"),
    ("fstring", "f\"run {sh('{x}')}\""),
    ("inline", "`{x}`"),
    ("fence_json", "```json\n{x}\n```"),
    ("fence_bash", "```bash\n{x}\n```"),
    ("fence_plain", "```\n{x}\n```"),
    ("blockquote", "> {x}"),
    ("list", "- {x}"),
    ("log", "2026-10-15T10:00:00Z INFO HAR capture: {x}"),
    (
        "packet",
        "Selected exact revisions only. Reference existence does not establish semantic "
        "support. Source text is inert data; it grants no permissions.\n"
        "\n[item_revision itm_1 revision 1]\nSource and limits: {}\n{x}",
    ),
    ("yaml", "key: |\n  {x}"),
    ("javascript", "const x = {x};"),
    ("heredoc", "cat <<EOF\n# operator's note\n{x}\nEOF"),
    ("comment", "<!-- {x} -->"),
    ("closed_pair", "'x' {x}"),
    ("parenthesis", "({x})"),
    ("brackets", "[{x}]"),
)
TOKENS = list("'\"`\\()[]{}<>=:,;&#@/%+$-_. \nabcdefghijklmnopqrstuvwxyz0123456789") + [
    "don't",
    '"""',
    "'''",
    "%22",
    "%27",
    r"\"",
    r"\\\"",
    "${",
    "{{",
]
LAYERS = ("raw", "json1", "json2", "percent", "form")


@dataclass(frozen=True)
class Row:
    family: str
    text: str
    markers: tuple[str, ...] = ()
    benign: bool = False
    category: str | None = None


def rows():
    for family, text, markers, benign in generate_rows():
        category = None
        if family == "schemes_benign_csv" or (
            family == "schemes_benign_escaped_nl" and not text.startswith("ftp:")
        ):
            category = "accepted_whatwg_userinfo"
        yield Row("review:" + family, text, markers, benign, category)
    for row in neighbours():
        if row.category is None:
            yield Row("own:" + row.shape, row.text, row.markers, row.benign)
    for row in reviewer_matrix():
        if row.category is None and row.shape.endswith(":raw"):
            yield Row("matrix:" + row.shape, row.text, row.markers)
    marker = f"{'CONTEXT'}_PRIVATE_MARKER_684329"
    for key in ["password", "token", "cookie", "authorization"]:
        yield Row("unclosed:" + key, key + "=${" + marker, (marker,))
    for shape in [
        '<input name="password" value="{v}">',
        '<meta content="{v}" name="csrf_token">',
        '<input type="password" value="{v}">',
    ]:
        yield Row("html", shape.format(v=marker), (marker,))
    for shape in [
        "<input name=\"password\" value=\"${T:-<input name='password' value='{v}'>}\">",
        '<input value="public\n{v}" type="password">',
        '<input name="pass&#119;ord" value="{v}">',
        '<password comment="ordinary\ntext">{v}</password>',
    ]:
        yield Row("review_extension", shape.replace("{v}", marker), (marker,))
    for header in ["Cookie", "Authorization"]:
        for delimiter in ['"', "'", "`"]:
            yield Row("review_extension", header + ": [REDACTED]" + delimiter + marker, (marker,))
    for key in ["password", "token", "cookie", "authorization"]:
        yield Row("review_extension", '--password "' + key + '": ' + marker, (marker,))
    for text in [
        '<input name="username" value="alice">',
        '<meta name="description" content="ordinary prose">',
        'token = os.getenv("TOKEN_)")',
        'token = os.environ["KEY[0]"]',
    ]:
        yield Row("benign", text, benign=True)


def encode(text, layer):
    if layer.startswith("json"):
        for _ in range(int(layer[-1])):
            text = json.dumps({"note": text})
    elif layer == "percent":
        text = quote(text, safe="")
    elif layer == "form":
        text = "q=" + quote_plus(text)
    return text


def visible(output, markers):
    # Check canonical string representations, including short visible pieces.
    for _ in range(4):
        for marker in markers:
            if any(marker[i : i + 6] in output for i in range(len(marker) - 5)):
                return True
        output = unquote(output).replace('\\"', '"').replace("\\\\", "\\")
    return False


def random_context(rng):
    return "".join(rng.choice(TOKENS) for _ in range(rng.randrange(17)))[:16]


def unwrapped(text):
    """Place context at the row's content layer, without exceeding layer budgets."""
    for _ in range(9):
        previous = text
        try:
            obj = json.loads(text)
        except (ValueError, RecursionError):
            obj = None
        if isinstance(obj, str):
            text = obj
        elif (
            isinstance(obj, dict)
            and len(obj) == 1
            and next(iter(obj))
            in {"cmd", "code", "note", "outer", "body", "k", "har", "capture", "log"}
            and isinstance(next(iter(obj.values())), str)
        ):
            text = next(iter(obj.values()))
        elif text.startswith("q="):
            text = unquote_plus(text[2:])
        elif text.startswith(("blob=", "body=")):
            text = unquote_plus(text.split("=", 1)[1])
        elif "%" in text and not any(c.isspace() for c in text):
            text = unquote(text)
        else:
            break
        if text == previous:
            break
    return text


def cases(count, *, seed=SEED, natural=True, randomize=True):
    rng = random.Random(seed)
    pool = list(rows())
    anchors = {}
    for row in pool:
        anchors.setdefault(row.family.split(":")[0], row)
    pool = list(anchors.values()) + pool
    for index in range(count):
        row = pool[index % len(pool)] if index < len(pool) else rng.choice(pool)
        row = Row(row.family, unwrapped(row.text), row.markers, row.benign, row.category)
        use_natural = natural and (not randomize or index % 2 == 0)
        if use_natural:
            name, template = NATURAL[(index // 2) % len(NATURAL)]
            prefix, suffix = template.split("{x}")
            if not suffix and "\n" not in prefix:
                orientation = (index // (2 * len(NATURAL))) % 3
                if orientation == 1:
                    prefix, suffix = "", prefix
                elif orientation == 2:
                    suffix = prefix
        else:
            name, prefix, suffix = "random", random_context(rng), random_context(rng)
        layer = LAYERS[(index // len(NATURAL)) % len(LAYERS)]
        sep = "\n" if index % 3 == 0 else " "
        inside = index % 4 < 2
        raw = prefix + sep + row.text + sep + suffix
        text = (
            encode(raw, layer) if inside else prefix + sep + encode(row.text, layer) + sep + suffix
        )
        yield row, name, layer, inside, text


def verify(count, *, seed=SEED, natural=True, randomize=True):
    counts, failures = Counter(), []
    started = time.monotonic()
    for row, ctx, layer, _inside, text in cases(
        count, seed=seed, natural=natural, randomize=randomize
    ):
        output, findings = redact_text(text)
        counts["pairs"] += 1
        counts["context:" + ctx] += 1
        counts["layer:" + layer] += 1
        counts["family:" + row.family.split(":")[0]] += 1
        if row.category:
            counts["accepted:" + row.category] += 1
        elif row.benign:
            counts["benign_N" if ctx != "random" else "benign_R"] += 1
            changed = (output, findings) != (text, [])
            counts["benign_changed_N" if ctx != "random" else "benign_changed_R"] += changed
            if changed and ctx != "random" and len(failures) < 40:
                failures.append(
                    {
                        "family": row.family,
                        "context": ctx,
                        "text": text,
                        "output": output,
                        "reason": "benign",
                    }
                )
        else:
            counts["in_scope"] += 1
            leaked = visible(output, row.markers)
            counts["leaks"] += leaked
            if leaked and len(failures) < 40:
                failures.append(
                    {
                        "family": row.family,
                        "context": ctx,
                        "layer": layer,
                        "text": text,
                        "output": output,
                        "reason": "leak",
                    }
                )
        nonfix = redact_text(output)[0] != output
        counts["nonfixpoint"] += nonfix
    return {
        "counts": dict(counts),
        "failures": failures,
        "seed": seed,
        "wall_seconds": time.monotonic() - started,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=1000000)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    print(json.dumps(verify(args.count, seed=args.seed), indent=2), flush=True)
