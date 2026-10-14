"""Prove the CI-only browser skip gate without contacting a network."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("required,exit_code", [(False, 0), (True, 1)])
def test_ci_browser_skip_is_failure_only_when_required(tmp_path, required, exit_code):
    (tmp_path / "conftest.py").write_text(Path(__file__).with_name("conftest.py").read_text())
    (tmp_path / "test_facebook_probe.py").write_text(
        'import pytest\ndef test_missing_browser(): pytest.skip("Synthetic Chromium absence")\n'
    )
    env = {
        **os.environ,
        "METAMUSE_REQUIRE_BROWSER_TESTS": "1" if required else "0",
        "PYTEST_ADDOPTS": "",
    }
    # Nested pytest stays inside the outer wrapper’s unique disk directory.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--basetemp", str(tmp_path / "pytest-temp")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert ("CI requires browser tests to run" in result.stdout) == required
