"""Shared Unicode components for Muse credential keys and confidential markers."""

import unicodedata

# Unicode Default_Ignorable_Code_Point ranges (including reserved tag/VS space).
# Cf is also removed, independently of this property. No locale or casefolding
# happens until after the compatibility normalization and boundary split.
_IGNORABLE = (
    (0xAD, 0xAD),
    (0x34F, 0x34F),
    (0x61C, 0x61C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def components(value: str) -> tuple[str, ...]:
    text = unicodedata.normalize(
        "NFKC",
        "".join(
            c
            for c in value
            if ord(c) < 128
            or (
                unicodedata.category(c) != "Cf"
                and not any(lo <= ord(c) <= hi for lo, hi in _IGNORABLE)
            )
        ),
    )
    words, start = [], 0
    for i, c in enumerate(text):
        if not c.isalnum():
            if start < i:
                words.append(text[start:i].casefold())
            start = i + 1
        elif i > start:
            prev = text[i - 1]
            if (
                prev.isalpha()
                and c.isdigit()
                or prev.isdigit()
                and c.isalpha()
                or prev.islower()
                and c.isupper()
                or prev.isupper()
                and c.isupper()
                and i + 1 < len(text)
                and text[i + 1].islower()
            ):
                words.append(text[start:i].casefold())
                start = i
    if start < len(text):
        words.append(text[start:].casefold())
    return tuple(words)


def compact(value: str) -> str:
    return "".join(components(value))


def confidential_marker(value: str) -> bool:
    return any(part.startswith("cvm") or "confidential" in part for part in components(value))
