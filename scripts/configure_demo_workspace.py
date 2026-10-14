#!/usr/bin/env python3
"""Point the demo workspace at local synthetic inputs and the fixture Muse backend.

The capture terminal runs this file. It only rewrites metamuse.toml. It does
not open the database or call application functions.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path("/tmp/metamuse-demo")
CONFIG = ROOT / "ws" / "metamuse.toml"


def main() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    app = "[app]\n"
    if app not in text:
        raise SystemExit("workspace config has no [app] section")
    text = text.replace(
        app,
        '[app]\nimport_roots = ["/tmp/metamuse-demo/inputs"]\n',
        1,
    )
    muse = "[muse]\nenabled = false\n"
    if muse not in text:
        raise SystemExit("workspace config has no disabled [muse] section")
    text = text.replace(
        muse,
        '[muse]\nenabled = true\nbackend = "fixture"\n',
        1,
    )
    CONFIG.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
