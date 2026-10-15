"""Precisely scoped cookie chains and a real benign code budget."""

import sysconfig
from pathlib import Path

import pytest
from test_b16a_round4 import assert_private, marker

from metamuse._muse_lifecycle import _check_question
from metamuse.safety import redact_text

BENIGN = (
    "def __init__(self, name, kind, *, code=None, globals=None):",
    "parser.add_argument('-j', '--workers', default=1, type=int)",
    "plt.plot(x, y, color='r', marker='o')",
    "delta = timedelta(hours=24, microseconds=-1)",
    "Set mode=fast level=high in the form",
    "Run with mode=fast, level=high please",
)


@pytest.mark.parametrize("text", BENIGN)
def test_r36_benign_calls_signatures_and_prose(text):
    assert redact_text(text) == (text, [])
    _check_question(text)


@pytest.mark.parametrize(
    "template",
    [
        "theme=dark; foo={v}",
        "a=1, sid={v}",
        'document.cookie = "theme=dark, foo={v}"',
        "curl -b 'theme=dark, foo={v}' https://example.invalid/a",
        "curl --cookie 'theme=dark&foo={v}' https://example.invalid/a",
        "Cookie: theme=dark&foo={v}",
        "Set-Cookie: theme=dark, foo={v}; Secure",
    ],
)
def test_r36_defined_chains_and_cookie_contexts(template):
    value = marker()
    assert_private(template.format(v=value), value)


@pytest.mark.parametrize("joiner", [", ", "&", " "])
def test_r36_generic_pairs_outside_context_are_data(joiner):
    text = "mode=fast" + joiner + "level=high"
    assert redact_text(text) == (text, [])
    _check_question(text)


def test_r36_numeric_generic_cookie_chain_is_private():
    value = f"{12345}{67890}"
    assert_private("theme=" + value + "; visits=" + value[::-1], value)


def stdlib_lines():
    root = Path(sysconfig.get_paths()["stdlib"])
    for directory in (root, root / "json", root / "email"):
        for path in sorted(directory.glob("*.py")):
            for line in path.read_text().splitlines():
                text = line.strip()
                if "=" in text and not text.startswith("#") and len(text) < 300:
                    yield text


def test_r36_stdlib_refusal_budget():
    lines = list(stdlib_lines())
    assert len(lines) > 30000
    refused = sum(
        any(row["kind"] != "local_path" for row in redact_text(line)[1]) for line in lines
    )
    assert refused / len(lines) <= 0.004, (refused, len(lines))
