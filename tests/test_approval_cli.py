"""Human CLI confirmation, expiry and preview privacy through installed entrypoints."""

import json
import os
import pty
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from test_journey import Entrypoints


def test_operator_confirmation_expiry_replay_and_secret_redaction(tmp_path):
    entry = Entrypoints(tmp_path)
    entry.configure()
    project = entry.cli("project", "create", "--name", "Approvals", "--request-key", "project")[
        "data"
    ]["id"]
    request = {"project_id": project, "topic": "Synthetic operator topic", "request_key": "open"}
    preview = entry.cli(
        "muse",
        "open",
        "--project",
        project,
        "--topic",
        request["topic"],
        "--request-key",
        "open",
        code=3,
    )["approval"]
    path = Path(preview["request_file"])
    path.write_text(json.dumps(request))
    common = [
        "permit",
        "approve",
        "--operation",
        "muse.open",
        "--request-file",
        str(path),
        "--request-key",
        "interactive",
    ]
    entry.cli(*common, operator=True, code=3)
    entry.cli(*common, "--exact-digest", preview["action_digest"], code=3)
    assert entry.cli("permit", "list", "--project", project, operator=True)["data"]["permits"] == []
    master, slave = pty.openpty()
    try:
        log = tmp_path / "confirmation.log"
        with log.open("w+") as stderr:
            proc = subprocess.Popen(
                [str(entry.command), "--workspace", str(entry.workspace), "--json", *common],
                stdin=slave,
                stdout=subprocess.PIPE,
                stderr=stderr,
                text=True,
            )
            try:
                deadline = time.monotonic() + 30
                while "Type yes:" not in log.read_text():
                    assert proc.poll() is None and time.monotonic() < deadline
                    time.sleep(0.02)
                os.write(master, b"yes\n")
                stdout, _ = proc.communicate(timeout=30)
                assert proc.returncode == 0
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=10)
        grant = json.loads(stdout)["data"]
        review = log.read_text()
        assert all("  | " + line in review for line in preview["summary"].split("\n"))
        assert "Approval binding:\n" in review
        assert "\nAction digest: " + preview["action_digest"] + "\n" in review
        assert f"Expires at (UTC): {grant['expires_at']} (remaining: " in review
        assert "Max uses: 1\n" in review
        assert review.index("Expires at (UTC):") < review.index("Type yes:")
        assert review.index("Max uses: 1") < review.index("Type yes:")
    finally:
        os.close(master)
        os.close(slave)
    lifetime = datetime.fromisoformat(grant["expires_at"]) - datetime.fromisoformat(
        grant["issued_at"]
    )
    assert 86390 <= lifetime.total_seconds() <= 86400
    assert grant["exact_digest"] == preview["action_digest"]
    assert grant["max_uses"] == 1
    replay = entry.cli(*common, "--exact-digest", preview["action_digest"], operator=True)["data"]
    assert (
        replay["id"] == grant["id"]
        and replay["expires_at"] == grant["expires_at"]
        and replay["replayed"] is True
    )
    expired = entry.cli(
        *common[:-1],
        "expired",
        "--exact-digest",
        preview["action_digest"],
        "--expires-at",
        datetime.now(UTC).isoformat(),
        operator=True,
        code=3,
    )
    assert expired["error"]["code"] == "E_SCOPE_DENIED"
    private_topic = "Cookie: hatch_sess=" + "SYNTHETIC_JOURNEY_SECRET"
    safe = entry.cli(
        "muse",
        "open",
        "--project",
        project,
        "--topic",
        private_topic,
        "--request-key",
        "private-preview",
        code=3,
    )["approval"]
    assert "[REDACTED]" in safe["topic"] and "[REDACTED]" in safe["request"]["topic"]
    assert safe["action_digest"] != preview["action_digest"]
    assert entry.cli("jobs", "list", "--project", project)["data"]["jobs"] == []
    entry.hygiene()


def test_operator_review_shows_effective_nondefault_lifetime_and_uses(tmp_path):
    entry = Entrypoints(tmp_path)
    entry.configure()
    project = entry.cli("project", "create", "--name", "Limits", "--request-key", "project")[
        "data"
    ]["id"]
    request = {"project_id": project, "topic": "Synthetic limits", "request_key": "open"}
    preview = entry.cli(
        "muse",
        "open",
        "--project",
        project,
        "--topic",
        request["topic"],
        "--request-key",
        "open",
        code=3,
    )["approval"]
    expiry = (datetime.now(UTC) + timedelta(hours=6)).isoformat()
    grant = entry.approve(
        preview, request, "limits", extra=("--expires-at", expiry, "--max-uses", "7")
    )["data"]
    assert grant["expires_at"] == expiry and grant["max_uses"] == 7
    review = entry.diagnostics[-1]
    assert f"Expires at (UTC): {expiry} (remaining: " in review
    assert "Max uses: 7\n" in review
    # A failed digest confirmation must still display the effective review first.
    refused = entry.approve(
        preview,
        request,
        "limits-mismatch",
        digest="0" * 64,
        extra=("--expires-at", expiry, "--max-uses", "7"),
        code=3,
    )
    assert refused["error"]["message"] == "Approval digest differs from this request"
    assert f"Expires at (UTC): {expiry} (remaining: " in entry.diagnostics[-1]
    assert "Max uses: 7\n" in entry.diagnostics[-1]
