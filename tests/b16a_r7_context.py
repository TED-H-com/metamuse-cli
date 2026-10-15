"""Native representations retained under every natural and seeded random context."""

import argparse
import json
import random
import time
from collections import Counter
from urllib.parse import quote, quote_plus

from b16a_r6_context import NATURAL as PRIOR_NATURAL
from b16a_r6_context import Row, random_context, unwrapped, visible
from b16a_r6_context import rows as prior_rows
from b16a_r7_orchestrator import ROWS as ORCHESTRATOR_ROWS
from b16a_r7_orchestrator import V as ORCHESTRATOR_MARKER
from b16a_r7_probes import ROWS

from metamuse.safety import redact_text

SEED = 10152027
NATURAL = PRIOR_NATURAL + (
    ("plural_users", "the users' {x}"),
    ("plural_clients", "the clients' {x}"),
    ("raw_string", "r'\\' {x}"),
    ("unclosed_double", '"unclosed {x}'),
    ("left_brace", "{{x}"),
    ("left_bracket", "[{x}"),
    ("left_paren", "({x}"),
    ("self_closing", "<div/>{x}"),
    ("xml_comment", "<!-- user's tag -->{x}"),
    ("yaml_indicator", "key: |2- # note\n  {x}"),
    ("putty_comment", "[\nComment: prod] key\n{x}\n]"),
    ("shell_operand", "${C:-{x}}"),
    ("markdown_link", "[x]({x})"),
    ("shell_quote_idiom", "'\"'\"' {x}"),
    ("shell_backslash_idiom", "'\\'' {x}"),
    ("sql_doubled", "'' {x}"),
    ("typographic", "“{x}”"),
)


def rows():
    yield from prior_rows()
    for family, text, markers in ROWS:
        yield Row("r7:" + family, text, markers)

    for family, text in ORCHESTRATOR_ROWS:
        accepted = (
            "owned_raw_closer_with_follower" if text.startswith("'https://alice:pa',") else None
        )
        benign = ORCHESTRATOR_MARKER not in text and text not in {
            "x = 'password=%s'%pw",
            'x = "user=%s password=%s" % (u, p)',
        }
        yield Row(
            "orchestrator:" + family,
            text,
            (ORCHESTRATOR_MARKER,) if ORCHESTRATOR_MARKER in text else (),
            benign,
            accepted,
        )
    yield Row(
        "benign:compact_json_url_email",
        '{"url":"https://example.invalid","email":"alice@example.invalid"}',
        benign=True,
    )
    marker = f"{'CONTEXT7'}_MARKER_PRIVATE_643829"
    for family, template in (
        ("ownership", "password=pa]-{v}"),
        ("generic_cookie_chain", "theme=dark; foo={v}"),
        ("follower", "password='pa'.{v}'"),
        ("doubled", 'password="pa"" {v}"'),
        ("shell_quote", "password='pa'\"'\"'{v}'"),
        ("layer_true", "https://alice:<{v}%3e@example.invalid/p"),
        ("shell_operand", 'curl -b "${{C:--u alice:{v}}}"'),
        ("markup", '<input value="{v}" type=" password "/>'),
        ("malformed_markup", "<password label='bob\\'s'>{v}</password>"),
        ("yaml", "password: # note\n  {v}\nnext: public"),
        ("form", "Log: cmd=curl+-H+'X:1'+--user+alice:{v}"),
    ):
        yield Row("decision:" + family, template.format(v=marker), (marker,))


def native_wrap(row, prefix, suffix, inside):
    if not inside:
        return prefix + " " + row.text + " " + suffix
    source = unwrapped(row.text)
    value = prefix + " " + source + " " + suffix
    if row.family.startswith("matrix:"):
        rep = row.family.rsplit(":", 1)[-1]
        if rep.startswith("pct"):
            for _ in range(int(rep[3]) if rep[3].isdigit() else 1):
                value = quote(value, safe="")
            return value
        if rep == "form_plus":
            return "q=" + quote_plus(value)
        if rep == "jsonesc":
            return json.dumps(value)[1:-1]
        if rep == "json_in_json":
            return json.dumps(json.dumps({"log": value}))
    # Preserve the complete source representation outside. Inside placements
    # rebuild only whole capture wrappers, never URL/password subcomponents.
    if row.text != source:
        try:
            obj = json.loads(row.text)
        except (ValueError, RecursionError):
            obj = None
        if isinstance(obj, str):
            return json.dumps(value)
        if isinstance(obj, dict) and len(obj) == 1 and isinstance(next(iter(obj.values())), str):
            return json.dumps({next(iter(obj)): value})
        if row.text.startswith("q="):
            return "q=" + quote_plus(value)
        if "%" in row.text and row.text != source:
            return quote(value, safe="")
    return value


def cases(*, full=False, seed=SEED, pool=None):
    rng = random.Random(seed)
    pool = list(rows()) if pool is None else pool
    anchors = {}
    for row in pool:
        anchors.setdefault(row.family, row)
    chosen = pool if full else list(anchors.values())
    for index, row in enumerate(chosen):
        contexts = NATURAL if full else [NATURAL[index % len(NATURAL)]]
        for j, (name, template) in enumerate(contexts):
            before, after = template.split("{x}")
            for inside in (False, True) if full else ((index + j) % 2 == 0,):
                yield row, name, inside, native_wrap(row, before, after, inside)
        for j in range(8 if full else 1):
            before, after = random_context(rng), random_context(rng)
            for inside in (False, True) if full else ((index + j) % 2 == 0,):
                yield row, "random", inside, native_wrap(row, before, after, inside)


def verify(*, full=False, seed=SEED, shard=0, shards=1, pool=None):
    counts = Counter()
    failures = []
    started = time.monotonic()
    for index, (row, ctx, inside, text) in enumerate(cases(full=full, seed=seed, pool=pool)):
        if index % shards != shard:
            continue
        out, found = redact_text(text)
        counts["placement:" + ("inside" if inside else "outside")] += 1
        counts["context:" + ctx] += 1
        counts["family:" + row.family] += 1
        counts["pairs"] += 1
        if row.category:
            counts["accepted:" + row.category] += 1
        elif row.benign:
            kind = "R" if ctx == "random" else "N"
            counts["benign_" + kind] += 1
            changed = (out, found) != (text, [])
            counts["changed_" + kind] += changed
            if changed and kind == "N" and len(failures) < 50:
                failures.append((row.family, ctx, text, out, "benign"))
        else:
            counts["in_scope"] += 1
            leak = visible(out, row.markers)
            counts["leaks"] += leak
            if leak and len(failures) < 50:
                failures.append((row.family, ctx, text, out, "leak"))
                if full:
                    print(json.dumps({"failure": failures[-1]}), flush=True)
        nonfix = redact_text(out)[0] != out
        counts["nonfixpoint"] += nonfix
        if counts["pairs"] % 100000 == 0:
            print(
                json.dumps({"progress": dict(counts), "wall": time.monotonic() - started}),
                flush=True,
            )
    return {
        "seed": seed,
        "counts": dict(counts),
        "failures": failures,
        "wall": time.monotonic() - started,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--shards", type=int, default=1)
    args = parser.parse_args()
    print(
        json.dumps(verify(full=args.full, seed=args.seed, shard=args.shard, shards=args.shards)),
        flush=True,
    )
