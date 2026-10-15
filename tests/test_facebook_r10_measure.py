"""Opt-in MEASURE capture benchmark; synthetic, on the shared loaded host."""

import json
import os
import time
from pathlib import Path

from browser_deadline import browser_deadline
from facebook_parity import capture_session
from test_facebook import _synthetic_post_html  # noqa: F401


@browser_deadline
def test_r10_capture_cost(tmp_path, monkeypatch):
    with capture_session(monkeypatch) as session:
        session._page.set_content(
            "<html><head></head><body>" + "<span>TEXT</span>" * 5000 + "</body></html>"
        )

        def cpu():
            return sum(
                p["cpuTime"]
                for p in session._worker_cdp.send("SystemInfo.getProcessInfo")["processInfo"]
            )

        before_cpu, before_self, start = cpu(), time.process_time(), time.monotonic()
        _, nodes, gaps = session._capture()
        result = {
            "nodes": len(nodes),
            "wall_seconds": time.monotonic() - start,
            "python_cpu_seconds": time.process_time() - before_self,
            "chromium_cpu_seconds": cpu() - before_cpu,
            "gaps": gaps,
            "host_io_pressure": Path("/proc/pressure/io").read_text(),
        }
        print(json.dumps(result))
        if os.environ.get("B13_CAPTURE_MEASURE"):
            Path(os.environ["B13_CAPTURE_MEASURE"]).write_text(json.dumps(result, indent=2) + "\n")
        assert len(nodes) >= 10000 and not gaps
