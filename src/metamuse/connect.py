"""Human-only account setup. Binding credentials never grants a remote action."""

from __future__ import annotations

import os
import tempfile
import tomllib
from pathlib import Path
from typing import Literal

import tomlkit
from pydantic import Field

from metamuse import models as m
from metamuse import safety
from metamuse.config import Config
from metamuse.store import file_lock


class ConnectRequest(m.Model):
    service: Literal["muse", "facebook", "publishing"]
    account: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:@-]{0,127}$")
    auth_file: str = Field(min_length=1)
    provider: str = Field(default="muse", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:@-]{0,127}$")
    page_id: str | None = Field(default=None, pattern=r"^[0-9]{1,32}$")
    api_version: str | None = Field(default=None, pattern=r"^v[0-9]{1,2}\.0$")
    enable_network: bool = False


def _validate_secret(service: str, secret: bytes) -> None:
    if service == "muse":
        from metamuse._muse_adapters import parse_cookie_header

        parse_cookie_header(secret)
    elif service == "facebook":
        from metamuse.facebook import sanitize_storage_state

        sanitize_storage_state(secret)
    else:
        from metamuse.publishing import validate_page_token

        validate_page_token(secret)


def connect_account(ctx, request: ConnectRequest) -> m.Envelope:
    """Import only the named file and atomically bind the matching service config."""
    ctx.require_interactive()
    request = ConnectRequest.model_validate(request)
    provider = {"facebook": "facebook", "publishing": "facebook-pages"}.get(
        request.service, request.provider
    )
    for label in (provider, request.account):
        safety._label(label)
        if safety.redact_text(label)[0] != label:
            raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Invalid account or provider label")
    if request.service == "publishing" and (not request.page_id or not request.api_version):
        raise m.CoreError(
            m.ErrorCode.INPUT_INVALID, "Publishing setup requires --page and --api-version"
        )
    if request.service != "publishing" and (request.page_id or request.api_version):
        raise m.CoreError(m.ErrorCode.INPUT_INVALID, "Page settings apply only to publishing")

    path = ctx.paths.root / "metamuse.toml"
    secret_store = ctx.secret_store or safety.SecretStore()
    with file_lock(ctx.paths.runtime / "connect.lock", blocking=False):
        # Reject a replacement/symlink and preserve the rest of the operator's document.
        original = safety._read_secret_file(path, safety.SECRET_MAX_BYTES)
        doc = tomlkit.parse(original.decode("utf-8"))
        Config.model_validate(tomllib.loads(original.decode("utf-8")))
        secret = safety._read_secret_file(
            Path(request.auth_file).expanduser(), safety.SECRET_MAX_BYTES
        )
        _validate_secret(request.service, secret)
        # Validate config before storing credentials. The real opaque ref has this shape.
        section = doc.setdefault(request.service, tomlkit.table())
        section["enabled"] = True
        section["auth_ref"] = "auth_" + "0" * 32
        if request.service == "muse":
            section["backend"] = "live"
            del section["auth_ref"]
            section["binding"] = {
                "provider": provider,
                "account": request.account,
                "auth_ref": "auth_" + "0" * 32,
            }
        else:
            section["account"] = request.account
            if request.service == "facebook":
                section["approved_page_types"] = ["post_permalink"]
            else:
                section["backend"] = "live"
                section["page_id"] = request.page_id
                section["api_version"] = request.api_version
        if request.enable_network:
            doc.setdefault("app", tomlkit.table())["network_mode"] = "live"
        Config.model_validate(tomllib.loads(tomlkit.dumps(doc)))
        metadata = secret_store.put(
            secret, provider=provider, account=request.account, created_at=ctx.clock()
        )
        ref = metadata["auth_ref"]
        if request.service == "muse":
            section["binding"]["auth_ref"] = ref
        else:
            section["auth_ref"] = ref
        temporary = None
        committed = False
        try:
            config = Config.model_validate(tomllib.loads(tomlkit.dumps(doc)))
            fd, temporary = tempfile.mkstemp(prefix=".connect-", dir=ctx.paths.root)
            with os.fdopen(fd, "wb") as stream:
                stream.write(tomlkit.dumps(doc).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            if safety._read_secret_file(path, safety.SECRET_MAX_BYTES) != original:
                raise m.CoreError(
                    m.ErrorCode.JOB_BUSY, "Workspace configuration changed; run connect again"
                )
            os.replace(temporary, path)
            committed = True
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
            if not committed:
                secret_store.revoke(ref)
    ctx.policy = config
    ctx.limits = config.limits
    return m.Envelope(
        status="ok",
        data={
            "service": request.service,
            "provider": provider,
            "account": request.account,
            "auth_ref": ref,
            "configured": True,
            "network_mode": config.app.network_mode,
            "account_verified": False,
            "next_action": safety.operator_command(ctx, "doctor"),
        },
        warnings=[
            "Credentials were checked locally. "
            "No account was contacted and no action was approved.",
            *(
                ["Workspace network mode is live; individual actions still require approval."]
                if request.enable_network
                else []
            ),
        ],
    )
