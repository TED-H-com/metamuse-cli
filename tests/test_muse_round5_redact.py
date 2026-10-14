"""Codec parity and raw/decoded span union; no mutation may expose a tail."""

import base64
import json
from urllib.parse import quote, quote_plus
from uuid import UUID

import pytest
from metamuse._muse_codec import redact
from metamuse._muse_redact import sanitize_text
from metamuse.muse import _Secrets

SECRET = "Zq" + "Bearer42Synthetic"
VECTORS = [
    f"upstream said Authorization: Bearer {SECRET} end",
    f"Proxy-Authorization: Basic {SECRET}",
    f"bearer {SECRET}",
    f"cookie: theme=dark; hatch_sess={SECRET}",
    *[f"{key}={SECRET}" for key in ("auth_token", "access_token", "hatch_sess")],
]


@pytest.mark.parametrize("text", VECTORS)
def test_r5_3_codec_superset(text):
    assert SECRET not in redact(text)
    secrets = _Secrets()
    for output in (
        sanitize_text(text),
        secrets.text(text),
        secrets.body(text.encode()).decode(),
        secrets.body(json.dumps({"detail": text}).encode()).decode(),
    ):
        assert SECRET not in output


@pytest.mark.parametrize(
    "value",
    [
        bytes(range(0xA1, 0xC1)).hex(),
        str(UUID(int=0xA1B2C3D4000000000000000000001234)),
        base64.b64encode(bytes(range(32))).decode(),
        "s0:" + "synthA.synthB.synthC",
    ],
)
@pytest.mark.parametrize(
    "context",
    [
        "100%{} end",
        "100%25{} end",
        "%%{} end",
        "%41{} end",
        "{}%",
        "raw {} end",
        "quoted {}",
        "plus {}",
        "double {}",
    ],
)
def test_r5_3_known_raw_and_decoded_union(value, context):
    encoded = (
        quote(value, safe="")
        if context.startswith("quoted")
        else (
            quote_plus(value, safe="")
            if context.startswith("plus")
            else (quote(quote(value, safe=""), safe="") if context.startswith("double") else value)
        )
    )
    output = sanitize_text(context.format(encoded), (value,))
    assert value not in output and value[4:] not in output


def test_r5_3_known_overlap_preserves_neighbours():
    value = str(UUID(int=0xA1B2C3D4000000000000000000001234))
    secrets = _Secrets()
    for variant in (value, value.upper(), value.replace("-", ""), value.replace("-", "").upper()):
        secrets.add(variant)
    assert secrets.text("id " + value + " KEEP-THIS-CONTEXT") == "id redacted KEEP-THIS-CONTEXT"
    outer = "OUTsyntheticHEADbcdeTAILsynthetic"
    assert sanitize_text("jar " + outer + " end", (outer, "bcde")) == "jar redacted end"


def test_r5_3_known_double_encoded_value():
    value = "KsyntheticLeft%2541syntheticRight"
    for echo in (value, "KsyntheticLeft%41syntheticRight", "KsyntheticLeftAsyntheticRight"):
        assert "syntheticRight" not in sanitize_text("echo " + echo + " end", (value,))
