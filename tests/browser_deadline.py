"""Bound browser hangs independently of much larger production/job deadlines."""

import functools
import inspect
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest


def browser_deadline(test):
    @functools.wraps(test)
    def bounded(*args, **kwargs):
        from metamuse import facebook

        if os.environ.get("METAMUSE_BROWSER_TEST_CHILD") == test.__name__ or (
            facebook.find_playwright_browser() is None
        ):
            return test(*args, **kwargs)
        arguments = inspect.signature(test).bind(*args, **kwargs).arguments
        scratch = arguments["tmp_path"] / "deadline-child"
        env = dict(os.environ, METAMUSE_BROWSER_TEST_CHILD=test.__name__, UV_OFFLINE="1")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(scratch),
                str(Path(inspect.getfile(test))) + "::" + test.__name__,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            start_new_session=True,
        )
        try:
            output, _ = process.communicate(timeout=90)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate()
            pytest.fail("Browser test exceeded independent 90s deadline:\n" + output)
        assert process.returncode == 0, output

    return bounded
