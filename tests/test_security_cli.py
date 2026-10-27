import pytest
import os
from pathlib import Path
from metamuse import models as m
from metamuse.cli import _read_utf8

def test_cli_path_traversal(tmp_path):
    with pytest.raises(m.CoreError) as exc:
        _read_utf8("../test", 100)
    assert exc.value.detail.code == m.ErrorCode.INPUT_INVALID

def test_cli_symlink(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("hello")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    with pytest.raises(m.CoreError) as exc:
        _read_utf8(str(link), 100)
    assert exc.value.detail.code == m.ErrorCode.INPUT_INVALID
