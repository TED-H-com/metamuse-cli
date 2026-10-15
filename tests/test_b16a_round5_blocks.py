"""Private key blocks keep their extent in encoded and inline captures."""

import json
from urllib.parse import quote_plus

import pytest

from metamuse.safety import redact_text


def block(kind, marker, closed=True):
    if kind == "putty":
        text = "Pu" + f"TTY-User-Key-File-2: ssh-rsa\nPrivate-Lines: 1\n{marker}"
        return text + ("\nPrivate-MAC: 00" if closed else "")
    label = "PGP " + "PRIVATE KEY BLOCK" if kind == "pgp" else "RSA " + "PRIVATE KEY"
    return (
        "-----BE"
        + f"GIN {label}-----\n{marker}"
        + ("\n-----E" + f"ND {label}-----" if closed else "")
    )


@pytest.mark.parametrize("kind", ["putty", "pgp", "pem"])
@pytest.mark.parametrize("context", ["raw", "json", "json2", "inline", "form", "crlf"])
@pytest.mark.parametrize("closed", [False, True])
def test_r45_key_blocks_in_every_capture(kind, context, closed):
    marker = f"{'KEY'}_MARKER_PRIVATE_BLOCK"
    text = block(kind, marker, closed)
    if context == "json":
        text = json.dumps({"content": text})
    elif context == "json2":
        text = json.dumps({"content": json.dumps({"key": text})})
    elif context == "form":
        text = "q=" + quote_plus(text)
    elif context == "inline":
        text = "key_file: " + text
    elif context == "crlf":
        text = "capture\r\n" + text.replace("\n", "\r\n")
    result, findings = redact_text(text)
    assert marker not in result and findings, (text, result, findings)
    assert redact_text(result)[0] == result


def test_r45_putty_extent_stops_after_mac_or_enclosing_quote():
    marker = f"{'KEY'}_MARKER_BOUNDED_BLOCK"
    for closed in (False, True):
        text = json.dumps({"content": block("putty", marker, closed), "public": "keep me"})
        result, findings = redact_text(text)
        assert marker not in result and findings
        assert '"public": "keep me"' in result
