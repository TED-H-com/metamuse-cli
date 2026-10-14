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


class RemoteApproval(m.Model):
    """One yt-dlp extractor key and the exact hosts it may contact.

    Hosts are normalized exact names. A subdomain, a path, or a query does not
    extend the approval. Generic and unknown extractor keys are never implied.
    ``auth_ref`` is optional operator configuration. ``CollectRequest`` has none.
    """

    extractor: str = Field(min_length=1, max_length=64, pattern=r"[A-Za-z][A-Za-z0-9_-]{0,63}")
    hosts: list[str] = Field(min_length=1, max_length=32)
    auth_ref: str | None = Field(default=None, pattern=r"auth_[0-9a-f]{32}")


class RemoteMediaConfig(m.Model):
    """Operator allowlist for remote media. Empty refuses every remote URL."""

    provider: str = Field(default="remote-media", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:@-]{0,127}$")
    account: str = Field(default="anonymous", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:@-]{0,127}$")
    approvals: list[RemoteApproval] = Field(default_factory=list, max_length=32)


class WorkerConfig(m.Model):
    max_executors: Literal[1] = 1


class EnabledConfig(m.Model):
    enabled: bool = False


class MuseConfig(EnabledConfig):
    backend: Literal["live", "fixture"] = "live"
    binding: m.MuseBinding | None = None


class Config(m.Model):
    app: AppPolicy = Field(default_factory=AppPolicy)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    limits: m.Limits = Field(default_factory=m.Limits)
    muse: MuseConfig = Field(default_factory=MuseConfig)
    publishing: EnabledConfig = Field(default_factory=EnabledConfig)
    remote_media: RemoteMediaConfig = Field(default_factory=RemoteMediaConfig)


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
[remote_media]
"""


def load_config(root: Path) -> Config:
    path = root / "metamuse.toml"
    if not path.exists():
        return Config()
    error = None
    try:
        return Config.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    except ValidationError as exc:
        error = m.validation_error(exc)
        for entry in exc.errors(include_input=False, include_context=False, include_url=False):
            if entry["loc"] in (("remote_media", "provider"), ("remote_media", "account")):
                error = m.CoreError(
                    m.ErrorCode.INPUT_INVALID,
                    "Invalid remote_media." + entry["loc"][1] + " setting",
                )
                break
    except (ValueError, UnicodeError):
        error = m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid workspace configuration")
    raise error
