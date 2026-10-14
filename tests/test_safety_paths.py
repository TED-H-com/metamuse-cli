"""Local input path checks (AT-031 path part, FR-020)."""

import os

import pytest

from metamuse import models as m
from metamuse.safety import open_input_file


def test_reads_exact_bytes_inside_a_root(tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    target = root / "備註.txt"
    target.write_bytes("繁中😀\r\n".encode())
    with open_input_file(str(target), max_bytes=100, roots=[root]) as handle:
        assert handle.read() == "繁中😀\r\n".encode()
        assert handle.bytes_read == len("繁中😀\r\n".encode())


def test_rejects_nul_option_names_and_missing_roots(tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    with pytest.raises(m.CoreError) as missing:
        with open_input_file(str(root / "a.txt"), max_bytes=10, roots=None):
            pass
    assert missing.value.detail.code == m.ErrorCode.SCOPE_REQUIRED
    assert "import_roots" in missing.value.detail.next_action

    with pytest.raises(m.CoreError) as nul:
        with open_input_file("bad\x00name", max_bytes=10, roots=[root]):
            pass
    assert nul.value.detail.code == m.ErrorCode.INPUT_INVALID

    flagged = root / "-secret.txt"
    flagged.write_text("x", encoding="utf-8")
    with pytest.raises(m.CoreError) as option:
        with open_input_file(str(flagged), max_bytes=10, roots=[root]):
            pass
    assert option.value.detail.code == m.ErrorCode.INPUT_INVALID
    assert "option" in option.value.detail.message


def test_rejects_symlink_escape_and_non_regular_files(tmp_path):
    root = tmp_path / "imports"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    real = outside / "secret.txt"
    real.write_text("secret", encoding="utf-8")
    link = root / "alias.txt"
    link.symlink_to(real)
    with pytest.raises(m.CoreError) as linked:
        with open_input_file(str(link), max_bytes=100, roots=[root]):
            pass
    assert linked.value.detail.code == m.ErrorCode.INPUT_INVALID

    nested = root / "sub"
    nested.mkdir()
    via_parent = nested / "via"
    via_parent.symlink_to(outside)
    with pytest.raises(m.CoreError) as parent:
        with open_input_file(str(via_parent / "secret.txt"), max_bytes=100, roots=[root]):
            pass
    assert parent.value.detail.code == m.ErrorCode.INPUT_INVALID

    escaped = root / ".." / "outside" / "secret.txt"
    with pytest.raises(m.CoreError) as outside_file:
        with open_input_file(str(escaped), max_bytes=100, roots=[root]):
            pass
    assert outside_file.value.detail.code == m.ErrorCode.SCOPE_DENIED

    with pytest.raises(m.CoreError) as directory:
        with open_input_file(str(root), max_bytes=100, roots=[root]):
            pass
    assert directory.value.detail.code == m.ErrorCode.SCOPE_DENIED

    fifo = root / "pipe"
    try:
        os.mkfifo(fifo)
    except OSError:
        pytest.skip("this system cannot create a fifo")
    with pytest.raises(m.CoreError) as pipe:
        with open_input_file(str(fifo), max_bytes=100, roots=[root]):
            pass
    assert pipe.value.detail.code == m.ErrorCode.INPUT_INVALID


def test_byte_limit_counts_reads(tmp_path):
    root = tmp_path / "imports"
    root.mkdir()
    target = root / "wide.txt"
    target.write_bytes(b"0123456789")
    with pytest.raises(m.CoreError) as limited:
        with open_input_file(str(target), max_bytes=4, roots=[root]) as handle:
            handle.read()
    assert limited.value.detail.code == m.ErrorCode.LIMIT_REACHED


def test_symlink_import_root_is_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real)
    target = real / "note.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(m.CoreError) as error:
        with open_input_file(str(target), max_bytes=10, roots=[alias]):
            pass
    assert error.value.detail.code == m.ErrorCode.INPUT_INVALID
