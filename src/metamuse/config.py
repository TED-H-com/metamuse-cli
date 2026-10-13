"""One typed workspace policy, shared by app, store and executor."""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from metamuse import models as m


class AppPolicy(m.Model):
    network_mode: Literal["offline", "live"] = "offline"
    telemetry_enabled: bool = False
    # Directories local import may read. Empty refuses every local file.
    import_roots: list[str] = Field(default_factory=list)


class WorkerConfig(m.Model):
    max_executors: Literal[1] = 1


class EnabledConfig(m.Model):
    enabled: bool = False


class Config(m.Model):
    app: AppPolicy = Field(default_factory=AppPolicy)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    limits: m.Limits = Field(default_factory=m.Limits)
    muse: EnabledConfig = Field(default_factory=EnabledConfig)
    publishing: EnabledConfig = Field(default_factory=EnabledConfig)


DEFAULT_CONFIG = """[app]
network_mode = "offline"
telemetry_enabled = false
[worker]
max_executors = 1
[limits]
max_urls = 10
max_items = 500
max_bytes = 1073741824
wall_clock_seconds = 900
media_max_bytes = 536870912
text_max_bytes = 20971520
packet_max_utf8_bytes = 49152
question_max_utf8_bytes = 8192
read_timeout_seconds = 30
safe_read_retries = 2
muse_poll_initial_seconds = 5
muse_poll_max_seconds = 30
muse_wait_seconds = 120
max_exchanges = 3
[muse]
enabled = false
[publishing]
enabled = false
"""


def load_config(root: Path) -> Config:
    path = root / "metamuse.toml"
    if not path.exists():
        return Config()
    try:
        return Config.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    except ValidationError as exc:
        raise m.validation_error(exc) from exc
    except (ValueError, UnicodeError) as exc:
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid workspace configuration") from exc
