"""OFFLINE: account setup validates formats, binds config, and never authorizes IO."""

import json
import os
import socket
import tomllib

import pytest
from test_facebook import _synthetic_storage_state_bytes

from metamuse import app, cli, config, safety
from metamuse import connect as setup
from metamuse import models as m

SECRET = "SYNTHETIC_CONNECT_PRIVATE_VALUE"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    def forbidden(*args, **kwargs):
        pytest.fail("Account setup attempted network IO")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    ctx = app.AppContext.open(tmp_path / "workspace")
    return ctx, tmp_path


def credential(root, service, *, raw=None):
    if raw is None:
        raw = {
            "muse": json.dumps(
                {"format": "cookie-header", "cookie_header": "session=" + SECRET}
            ).encode(),
            "facebook": _synthetic_storage_state_bytes(secret_cookie=SECRET),
            "publishing": SECRET.encode(),
        }[service]
    path = root / (service + ".private")
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


def request(root, service, **overrides):
    return setup.ConnectRequest(
        service=service,
        account="test-account",
        auth_file=str(credential(root, service)),
        **({"page_id": "123456", "api_version": "v25.0"} if service == "publishing" else {}),
        **overrides,
    )


@pytest.mark.parametrize("service", ["muse", "facebook", "publishing"])
@pytest.mark.parametrize("live", [False, True])
def test_connect_binds_private_ref_preserves_settings_without_network(env, service, live):
    ctx, root = env
    path = ctx.paths.root / "metamuse.toml"
    path.write_text(path.read_text().replace("[app]", "# operator comment\n[app]"))
    result = setup.connect_account(ctx, request(root, service, enable_network=live))
    assert result.status == "ok"
    assert result.data["configured"] is True and result.data["account_verified"] is False
    assert SECRET not in json.dumps(result.model_dump(mode="json"))
    policy = config.load_config(ctx.paths.root)
    assert policy.app.network_mode == ("live" if live else "offline")
    assert "# operator comment" in path.read_text()
    assert (
        SECRET not in path.read_text()
        and str(root / (service + ".private")) not in path.read_text()
    )
    assert path.stat().st_mode & 0o777 == 0o600
    binding = policy.muse.binding if service == "muse" else getattr(policy, service)
    assert binding.auth_ref == result.data["auth_ref"]
    assert binding.account == "test-account"
    assert (
        safety.SecretStore().resolve(
            binding.auth_ref, provider=result.data["provider"], account=binding.account
        )
        == (root / (service + ".private")).read_bytes()
    )
    assert len(safety.SecretStore().list()) == 1
    assert app.list_jobs(ctx, {}).data["jobs"] == []
    assert ctx.policy == policy
    listed = safety.list_auth(ctx).data["references"]
    assert listed[0]["auth_ref"] == binding.auth_ref and listed[0]["config_bound"] is True
    if service == "muse":
        assert policy.muse.enabled and policy.muse.backend == "live"
    elif service == "facebook":
        assert policy.facebook.approved_page_types == ["post_permalink"]
    else:
        assert policy.publishing.page_id == "123456"
    revoked = safety.revoke_auth(ctx, binding.auth_ref).data
    assert revoked["config_bound"] is True and revoked["next_action"]


@pytest.mark.parametrize("service", ["muse", "facebook", "publishing"])
@pytest.mark.parametrize("raw", [b"", b"[]", b"null", b"not a secret\n", b'{"cookie_header":12}'])
def test_invalid_secret_does_not_change_config_or_import(env, service, raw):
    ctx, root = env
    if service == "publishing" and raw == b"null":
        raw = b"token with whitespace"
    req = request(root, service)
    credential(root, service, raw=raw)
    original = (ctx.paths.root / "metamuse.toml").read_bytes()
    with pytest.raises(m.CoreError) as error:
        setup.connect_account(ctx, req)
    assert error.value.detail.code == m.ErrorCode.AUTH_REQUIRED
    assert (ctx.paths.root / "metamuse.toml").read_bytes() == original
    assert safety.SecretStore().list() == []


def test_config_write_failure_removes_new_ref_and_preserves_original(env, monkeypatch):
    ctx, root = env
    path = ctx.paths.root / "metamuse.toml"
    original = path.read_bytes()
    replace = os.replace

    def fail(source, destination, **kwargs):
        if destination == path:
            raise OSError("synthetic disk failure")
        return replace(source, destination, **kwargs)

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        setup.connect_account(ctx, request(root, "muse"))
    assert path.read_bytes() == original
    assert safety.SecretStore().list() == []
    assert list(ctx.paths.root.glob(".connect-*")) == []


def test_concurrent_config_edit_is_not_overwritten_and_import_is_rolled_back(env, monkeypatch):
    ctx, root = env
    path = ctx.paths.root / "metamuse.toml"
    original = path.read_text()
    put = safety.SecretStore.put

    def edit(self, *args, **kwargs):
        result = put(self, *args, **kwargs)
        path.write_text(original + "\n# concurrent operator change\n")
        return result

    monkeypatch.setattr(safety.SecretStore, "put", edit)
    with pytest.raises(m.CoreError) as error:
        setup.connect_account(ctx, request(root, "facebook"))
    assert error.value.detail.code == m.ErrorCode.JOB_BUSY
    assert path.read_text() == original + "\n# concurrent operator change\n"
    assert safety.SecretStore().list() == []


def test_noninteractive_cli_refuses_before_opening_workspace_or_auth_file(tmp_path, capsys):
    workspace = tmp_path / "not-created"
    assert (
        cli.main(
            [
                "--workspace",
                str(workspace),
                "--json",
                "--non-interactive",
                "connect",
                "muse",
                "--account",
                "test",
                "--auth-file",
                "/not/opened",
            ]
        )
        == 3
    )
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_SCOPE_REQUIRED"
    assert not workspace.exists()


@pytest.mark.parametrize("service", ["muse", "facebook", "publishing"])
def test_human_cli_connect(env, service, capsys):
    ctx, root = env
    secret = credential(root, service)
    args = [
        "--workspace",
        str(ctx.paths.root),
        "--json",
        "connect",
        service,
        "--account",
        "test",
        "--auth-file",
        str(secret),
    ]
    if service == "publishing":
        args += ["--page", "123456", "--api-version", "v25.0"]
    assert cli.main(args) == 0
    output = capsys.readouterr()
    assert SECRET not in output.out + output.err
    assert json.loads(output.out)["data"]["configured"] is True
    assert tomllib.loads((ctx.paths.root / "metamuse.toml").read_text())[service]["enabled"]
