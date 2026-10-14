import base64
import inspect
import json
import os
import stat
import traceback

import pytest

from metamuse import cli, safety
from metamuse import models as m

FAKE = b"FAKE_SYNTHETIC_SECRET_DO_NOT_USE\x00\xff"
LABELS = {"provider": "fixture-provider", "account": "fixture-account"}


@pytest.fixture(autouse=True)
def private_config(tmp_path, monkeypatch):
    monkeypatch.setattr(safety, "user_config_path", lambda *a, **kw: tmp_path / "config")


def secret_file(tmp_path, content=FAKE):
    path = tmp_path / "operator-specified.secret"
    path.write_bytes(content)
    path.chmod(0o600)
    return path


def test_import_returns_only_metadata_and_backend_resolves(ctx, tmp_path, caplog, capsys):
    path = secret_file(tmp_path)
    imported = safety.import_auth(
        ctx,
        m.AuthImportRequest(path=str(path), provider="fixture-provider", account="fixture-account"),
    )
    data = imported.data
    assert set(data) == {"auth_ref", "provider", "account", "storage_kind", "created_at"}
    assert data["auth_ref"].startswith("auth_") and str(path) not in imported.model_dump_json()
    assert data["storage_kind"] == "private_file"
    stored = tmp_path / "config/auth" / (data["auth_ref"] + ".json")
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert stat.S_IMODE(stored.parent.stat().st_mode) == 0o700
    assert (
        safety.resolve_secret(
            data["auth_ref"], provider="fixture-provider", account="fixture-account"
        )
        == FAKE
    )
    with ctx.store.connection() as conn:
        database = "\n".join(conn.iterdump())
    output = capsys.readouterr()
    assert "FAKE_SYNTHETIC_SECRET" not in (
        imported.model_dump_json() + database + caplog.text + output.out + output.err
    )
    assert base64.b64encode(FAKE).decode() not in database


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o606, 0o660])
def test_group_other_permissions_rejected(ctx, tmp_path, mode):
    path = secret_file(tmp_path)
    path.chmod(mode)
    with pytest.raises(m.CoreError) as error:
        safety.import_auth(ctx, m.AuthImportRequest(path=str(path)))
    assert error.value.detail.code == m.ErrorCode.AUTH_REQUIRED
    assert "FAKE" not in str(error.value)
    assert not (tmp_path / "config").exists()


@pytest.mark.parametrize(
    "unsafe", ["symlink", "parent_symlink", "directory", "fifo", "oversize", "empty"]
)
def test_unsafe_secret_file_rejected(ctx, tmp_path, unsafe):
    original = secret_file(tmp_path)
    path = tmp_path / "unsafe"
    if unsafe == "symlink":
        path.symlink_to(original)
    elif unsafe == "parent_symlink":
        path.symlink_to(tmp_path, target_is_directory=True)
        path = path / original.name
    elif unsafe == "directory":
        path.mkdir(mode=0o700)
    elif unsafe == "fifo":
        os.mkfifo(path, mode=0o600)
    else:
        path = secret_file(
            tmp_path, b"x" * (safety.SECRET_MAX_BYTES + 1) if unsafe == "oversize" else b""
        )
    with pytest.raises(m.CoreError):
        safety.import_auth(ctx, m.AuthImportRequest(path=str(path)))


def test_wrong_owner_rejected(ctx, tmp_path, monkeypatch):
    path = secret_file(tmp_path)
    real_uid = os.getuid()
    monkeypatch.setattr(safety.os, "getuid", lambda: real_uid + 1)
    with pytest.raises(m.CoreError):
        safety.import_auth(ctx, m.AuthImportRequest(path=str(path)))


def test_only_named_file_is_opened(ctx, tmp_path, monkeypatch):
    path = secret_file(tmp_path)
    opened = []
    real_open = os.open

    def observe(name, flags, *args, **kwargs):
        if not flags & os.O_DIRECTORY:
            opened.append(str(name))
            assert flags & os.O_NOFOLLOW
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(safety.os, "open", observe)
    safety.import_auth(ctx, m.AuthImportRequest(path=str(path)))
    assert opened[0] == path.name
    assert len(opened) == 2  # Named operator file and newly created atomic staging file.


def test_atomic_write_failure_cleans_up_and_sanitizes(ctx, tmp_path, monkeypatch):
    path = secret_file(tmp_path)

    def fail(*a, **kw):
        raise OSError("FAKE_STORAGE_SECRET")

    monkeypatch.setattr(safety.os, "replace", fail)
    with pytest.raises(m.CoreError) as error:
        safety.import_auth(ctx, m.AuthImportRequest(path=str(path)))
    assert list((tmp_path / "config/auth").iterdir()) == []
    assert "FAKE_STORAGE_SECRET" not in "".join(traceback.format_exception(error.value))


def test_secret_store_rejects_checkout_and_symlink_root(tmp_path):
    checkout = tmp_path / "synthetic-checkout"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "src/metamuse").mkdir(parents=True)
    with pytest.raises(m.CoreError):
        safety.SecretStore(checkout / "auth").put(
            FAKE, provider=None, account=None, created_at="2026-10-13T00:00:00Z"
        )
    assert not (checkout / "auth").exists()
    link = tmp_path / "config-link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(m.CoreError):
        safety.SecretStore(link / "auth").put(
            FAKE, provider=None, account=None, created_at="2026-10-13T00:00:00Z"
        )
    assert not (tmp_path / "auth").exists()


@pytest.mark.parametrize("ref", ["../secret", "auth_missing", "auth_" + "a" * 32])
def test_invalid_missing_references_are_sanitized(ref):
    with pytest.raises(m.CoreError) as error:
        safety.resolve_secret(ref, **LABELS)
    assert error.value.detail.code == m.ErrorCode.AUTH_REQUIRED


def test_reference_account_isolation_and_tamper(ctx, tmp_path):
    result = safety.import_auth(
        ctx,
        m.AuthImportRequest(
            path=str(secret_file(tmp_path)), provider="fixture-provider", account="fixture-account"
        ),
    ).data
    for labels in ({"provider": "other-provider"}, {"account": "other-account"}):
        with pytest.raises(m.CoreError):
            safety.resolve_secret(result["auth_ref"], **{**LABELS, **labels})
    path = tmp_path / "config/auth" / (result["auth_ref"] + ".json")
    path.write_text('{"secret_b64":"FAKE_CORRUPT_SECRET"')
    with pytest.raises(m.CoreError) as error:
        safety.resolve_secret(result["auth_ref"], **LABELS)
    assert "FAKE_CORRUPT_SECRET" not in "".join(traceback.format_exception(error.value))
    path.unlink()
    path.symlink_to(secret_file(tmp_path))
    with pytest.raises(m.CoreError):
        safety.resolve_secret(result["auth_ref"], **LABELS)


def test_human_cli_import_and_rejection(ctx, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.app.AppContext, "open", lambda *a, **kw: ctx)
    path = secret_file(tmp_path)
    assert (
        cli.main(
            [
                "--json",
                "auth",
                "import",
                "--file",
                str(path),
                "--provider",
                LABELS["provider"],
                "--account",
                LABELS["account"],
            ]
        )
        == 0
    )
    response = json.loads(capsys.readouterr().out)
    assert safety.resolve_secret(response["data"]["auth_ref"], **LABELS) == FAKE
    path.chmod(0o644)
    assert cli.main(["--json", "auth", "import", "--file", str(path)]) == 3
    response = capsys.readouterr()
    assert "FAKE_SYNTHETIC_SECRET" not in response.out + response.err


def test_symlink_plus_parent_component_is_rejected_before_open(ctx, tmp_path, monkeypatch):
    good = secret_file(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "nested").mkdir(parents=True)
    (elsewhere / good.name).write_bytes(b"FAKE_OTHER_SECRET")
    (elsewhere / good.name).chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(elsewhere / "nested", target_is_directory=True)
    ambiguous = link / ".." / good.name
    # The old abspath collapse would read good; the OS path points elsewhere. Read neither.
    monkeypatch.setattr(safety.os, "open", lambda *a, **kw: pytest.fail("Ambiguous path opened"))
    with pytest.raises(m.CoreError) as error:
        safety.import_auth(ctx, m.AuthImportRequest(path=str(ambiguous), **LABELS))
    assert error.value.detail.code == m.ErrorCode.AUTH_REQUIRED
    assert str(ambiguous) not in str(error.value)


@pytest.mark.parametrize(
    "label, value", [("provider", None), ("account", None), ("provider", ""), ("account", "")]
)
def test_backend_labels_are_explicit_valid_and_checked_before_secret_read(
    ctx, tmp_path, monkeypatch, label, value
):
    result = safety.import_auth(ctx, m.AuthImportRequest(path=str(secret_file(tmp_path)), **LABELS))
    monkeypatch.setattr(
        safety,
        "_read_secret_file",
        lambda *a, **kw: pytest.fail("Unlabeled resolution read a file"),
    )
    with pytest.raises(m.CoreError) as error:
        safety.resolve_secret(result.data["auth_ref"], **{**LABELS, label: value})
    assert error.value.detail.code == m.ErrorCode.AUTH_REQUIRED


@pytest.mark.parametrize("function", [safety.resolve_secret, safety.SecretStore.resolve])
def test_resolution_signature_requires_both_labels(function):
    parameters = inspect.signature(function).parameters
    assert parameters["provider"].default is inspect.Parameter.empty
    assert parameters["account"].default is inspect.Parameter.empty


def test_unlabeled_import_cannot_be_resolved_by_backend(ctx, tmp_path):
    result = safety.import_auth(ctx, m.AuthImportRequest(path=str(secret_file(tmp_path))))
    with pytest.raises(m.CoreError):
        safety.resolve_secret(result.data["auth_ref"], **LABELS)


def test_noninteractive_feature_auth_refuses_before_read(ctx, tmp_path, monkeypatch):
    ctx.non_interactive = True
    monkeypatch.setattr(
        safety, "_read_secret_file", lambda *a, **kw: pytest.fail("Noninteractive auth read a file")
    )
    with pytest.raises(m.CoreError) as error:
        safety.import_auth(ctx, m.AuthImportRequest(path=str(tmp_path / "FAKE_missing"), **LABELS))
    assert error.value.detail.code == m.ErrorCode.SCOPE_REQUIRED
