import pytest
import os
from pathlib import Path
from metamuse import models as m
from metamuse.config import load_config

def test_config_permissions(tmp_path):
    config = tmp_path / "metamuse.toml"
    config.write_text("[app]\nnetwork_mode = 'offline'")
    os.chmod(str(config), 0o600)
    config.chmod(0o644)
    with pytest.raises(m.CoreError) as exc:
        load_config(tmp_path)
    assert exc.value.detail.code == m.ErrorCode.INPUT_INVALID

    config.chmod(0o600)
    loaded = load_config(tmp_path)
    assert loaded.app.network_mode == "offline"
