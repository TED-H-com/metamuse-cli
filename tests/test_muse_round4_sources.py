"""Public Muse behavior cites repository evidence and labels the inference."""

from pathlib import Path

from metamuse import muse


def test_r4_5_public_sources_are_checkable():
    root = Path(__file__).resolve().parents[1]
    for path in [
        Path(muse.__file__),
        root / "docs/dev/MUSE.md",
        root / "docs/dev/MUSE_SPIKE.md",
        root / "docs/design/03_SDS.md",
        root / "docs/design/MetaMuse_CLI_Plan_Spec_SDS_v1.1.md",
    ]:
        text = path.read_text()
        assert ("REPORT" + "-2") not in text
        assert ("S" + "1") not in text
        assert "spike §6.2" not in text and "spike§6.2" not in text
    text = (root / "docs/dev/MUSE.md").read_text()
    assert "MUSE_SPIKE §9.1" in text and "unavailable/retry-later is an inference" in text
