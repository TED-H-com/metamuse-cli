"""Verbatim auth cleanup guidance retains an explicitly chosen workspace."""

import json
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

from test_b16a_round3_runtime import isolated_env

from metamuse import safety


def test_r310_auth_revoke_suggestion_preserves_workspace(tmp_path):
    env = isolated_env(tmp_path)
    store = safety.SecretStore(Path(env["XDG_CONFIG_HOME"]) / "metamuse" / "auth")
    store.root.mkdir(parents=True, mode=0o700)
    ref = "auth_" + uuid.uuid4().hex
    bad = store.root / (ref + ".json")
    bad.write_text("{truncated")
    bad.chmod(0o600)
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()
    (workspace / "metamuse.toml").write_text(
        '[muse]\nenabled = true\nbackend = "fixture"\n[muse.binding]\nprovider = "fixture-provider"\naccount = "acct"\nauth_ref = "'
        + ref
        + '"\n'
    )
    executable = str(Path(sys.executable).with_name("metamuse"))
    listing = subprocess.run(
        [executable, "--workspace", str(workspace), "--json", "auth", "list"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    envelope = json.loads(listing.stdout)
    command = shlex.split(envelope["data"]["unavailable"][0]["next_action"])
    # Only resolve the installed executable. Every suggested argument is verbatim.
    command[0] = executable
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    revoked = json.loads(result.stdout)
    assert revoked["data"]["config_bound"] is True
    assert "rebind" in revoked["data"]["next_action"].lower()
    assert not bad.exists()
    assert not (workspace / "metamuse.sqlite3").exists()
    assert not Path(env["XDG_DATA_HOME"]).exists()
