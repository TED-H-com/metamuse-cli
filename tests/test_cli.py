import argparse
import json
import sys
import tomllib

import pytest
from pydantic import ValidationError

from metamuse import cli
from metamuse import models as m


def run(tmp_path, capsys, command):
    code = cli.main(
        ["--workspace", str(tmp_path / "workspace"), "--json", "--non-interactive", *command]
    )
    output = capsys.readouterr()
    return code, json.loads(output.out), output.err


@pytest.mark.parametrize("command", [["init"], ["doctor"]])
def test_offline_init_doctor_no_socket(tmp_path, capsys, command):
    code, result, err = run(tmp_path, capsys, command)
    assert code == 0 and result["status"] == "ok" and not err


def test_live_doctor_unavailable_without_network(tmp_path, capsys):
    code, result, err = run(tmp_path, capsys, ["doctor", "--live"])
    assert code == 4 and result["error"]["code"] == "E_UNSUPPORTED" and not err


def test_cli_projects_jobs_worker(tmp_path, capsys):
    code, result, _ = run(
        tmp_path, capsys, ["project", "create", "--name", "Fixture", "--request-key", "one"]
    )
    assert code == 0 and result["data"]["name"] == "Fixture"
    code, result, _ = run(tmp_path, capsys, ["jobs", "list"])
    assert code == 0 and result["data"]["jobs"] == []
    code, result, _ = run(tmp_path, capsys, ["worker", "run", "--once"])
    assert code == 0 and result["data"]["steps_processed"] == 0
    code, result, _ = run(tmp_path, capsys, ["jobs", "get", "job_missing"])
    assert code == 2 and result["error"]["code"] == "E_REFERENCE_INVALID"


@pytest.mark.parametrize(
    "command",
    [
        ["search", "--project", "prj_fixture", "--query", "fixture"],
        [
            "packet",
            "prepare",
            "--project",
            "prj_fixture",
            "--selection-file",
            "missing.json",
            "--request-key",
            "one",
        ],
        [
            "muse",
            "open",
            "--project",
            "prj_fixture",
            "--topic",
            "fixture",
            "--access-grant",
            "pmt_fixture",
            "--request-key",
            "one",
        ],
        [
            "muse",
            "send",
            "--discussion",
            "dsc_fixture",
            "--packet",
            "pkt_fixture",
            "--question-file",
            "missing.txt",
            "--share-grant",
            "pmt_fixture",
            "--request-key",
            "one",
        ],
        ["muse", "read", "--exchange", "exc_fixture", "--refresh"],
        ["muse", "close-exchange", "exc_fixture", "--outcome", "answered", "--request-key", "one"],
        [
            "draft",
            "save",
            "--project",
            "prj_fixture",
            "--text-file",
            "missing.txt",
            "--platform",
            "generic",
            "--request-key",
            "one",
        ],
        ["draft", "preview", "drf_fixture", "--revision", "1"],
        [
            "export",
            "--project",
            "prj_fixture",
            "--refs-file",
            "missing.json",
            "--format",
            "jsonl",
            "--request-key",
            "one",
        ],
    ],
)
def test_registered_commands_report_errors_for_missing_data(tmp_path, capsys, command):
    command = list(command)
    for index, value in enumerate(command):
        if value in ("missing.json", "missing.txt"):
            path = tmp_path / value
            path.write_text(
                json.dumps([{"kind": "item_revision", "id": "itm_fixture", "revision": 1}])
                if value.endswith(".json")
                else "Synthetic text",
                encoding="utf-8",
            )
            command[index] = str(path)
    code, result, _ = run(tmp_path, capsys, command)
    if command[0] in ("auth", "permit"):
        assert code == 3 and result["error"]["code"] == "E_SCOPE_REQUIRED"
    elif command[0] in ("search", "packet", "draft", "export"):
        assert code == 2 and result["error"]["code"] == "E_REFERENCE_INVALID"
    elif command[0] == "muse":
        assert result["error"]["code"] in ("E_SCOPE_REQUIRED", "E_REFERENCE_INVALID")
        assert code in (2, 3)
    else:
        assert code == 4 and result["error"]["code"] == "E_UNSUPPORTED"


def test_invalid_args_clean_envelope(tmp_path, capsys):
    code, result, err = run(tmp_path, capsys, ["project", "create"])
    assert code == 2 and result["error"]["code"] == "E_INPUT_INVALID" and not err


def test_interrupt_exit_130(monkeypatch, tmp_path, capsys):
    def interrupt(args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "dispatch", interrupt)
    code, result, err = run(tmp_path, capsys, ["init"])
    assert code == 130 and result["data"]["remote_state"] == "unknown"
    assert "Interrupted" in err


def test_json_stdout_and_warning_stderr(capsys):
    cli.render(m.Envelope(status="ok", data={}, warnings=["Synthetic warning"]), as_json=True)
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "ok"
    assert output.err == "Warning: Synthetic warning\n"


def test_corrupt_database_returns_storage_error(tmp_path, capsys):
    run(tmp_path, capsys, ["init"])
    (tmp_path / "workspace/state.sqlite3").write_bytes(b"synthetic corruption")
    code, result, _ = run(tmp_path, capsys, ["doctor"])
    assert code == 7 and result["error"]["code"] == "E_STORAGE"


def test_help_flag(capsys):
    code = cli.main(["--help"])
    assert code == 0
    output = capsys.readouterr()
    assert "usage:" in output.out

def test_json_help_flag(capsys):
    code = cli.main(["--json", "--help"])
    assert code == 0
    output = capsys.readouterr()
    res = json.loads(output.out)
    assert res["status"] == "ok"
    assert res["data"]["help"] is True

def test_unhandled_exception(monkeypatch, capsys):
    def throw(*args, **kwargs):
        raise ValueError("Something unexpected")
    monkeypatch.setattr(cli, "dispatch", throw)
    code = cli.main(["--json", "init"])
    assert code == 7
    output = capsys.readouterr()
    res = json.loads(output.out)
    assert res["error"]["code"] == "E_STORAGE"

def test_toml_decode_error(monkeypatch, capsys):
    def throw(*args, **kwargs):
        raise tomllib.TOMLDecodeError("Bad TOML")
    monkeypatch.setattr(cli, "dispatch", throw)
    code = cli.main(["--json", "init"])
    assert code == 2
    output = capsys.readouterr()
    res = json.loads(output.out)
    assert res["error"]["code"] == "E_INPUT_INVALID"

def test_oserror(monkeypatch, capsys):
    def throw(*args, **kwargs):
        raise OSError("File not found")
    monkeypatch.setattr(cli, "dispatch", throw)
    code = cli.main(["--json", "init"])
    assert code == 7
    output = capsys.readouterr()
    res = json.loads(output.out)
    assert res["error"]["code"] == "E_STORAGE"

def test_validation_error(capsys):
    code = cli.main(["--json", "jobs", "list", "--limit", "bad"])
    assert code == 2
    output = capsys.readouterr()
    res = json.loads(output.out)
    assert res["error"]["code"] == "E_INPUT_INVALID"

def test_cli_operation_helper():
    assert cli._cli_operation(None) == "cli"
    assert cli._cli_operation(argparse.Namespace()) == "cli"
    assert cli._cli_operation(argparse.Namespace(command="project", action="create")) == "project create"
    assert cli._cli_operation(argparse.Namespace(command="init")) == "init"
    assert cli._cli_operation(argparse.Namespace(command="init", action=None)) == "init"

def test_terminal_text():
    assert cli._terminal_text("hello\nworld") == "hello\\u000aworld"
    assert cli._terminal_text("hello\nworld", multiline=True) == "hello\nworld"
    assert cli._terminal_text("\x01") == "\\u0001"


def test_read_utf8_and_json(tmp_path):
    path = tmp_path / "test.json"

    path.write_bytes(b"12345")
    with pytest.raises(m.CoreError) as exc:
        cli._read_utf8(str(path), max_bytes=4)
    assert exc.value.detail.code == m.ErrorCode.LIMIT_REACHED

    path.write_bytes(b"\xff\xfe")
    with pytest.raises(m.CoreError) as exc:
        cli._read_utf8(str(path), max_bytes=10)
    assert exc.value.detail.code == m.ErrorCode.INPUT_INVALID

    path.write_bytes(b"{bad}")
    with pytest.raises(m.CoreError) as exc:
        cli._read_json(str(path), max_bytes=10)
    assert exc.value.detail.code == m.ErrorCode.INPUT_INVALID

    path.write_bytes(b"NaN")
    with pytest.raises(m.CoreError) as exc:
        cli._read_json(str(path), max_bytes=10)
    assert exc.value.detail.code == m.ErrorCode.INPUT_INVALID

    path.write_bytes(b"1e999999999")
    with pytest.raises(m.CoreError) as exc:
        cli._read_json(str(path), max_bytes=20)
    assert exc.value.detail.code == m.ErrorCode.INPUT_INVALID

    path.write_bytes(b'{"a": 1, "a": 2}')
    with pytest.raises(m.CoreError) as exc:
        cli._read_json(str(path), max_bytes=20)
    assert exc.value.detail.code == m.ErrorCode.INPUT_INVALID

def test_parser_error():
    parser = cli.parser()
    with pytest.raises(m.CoreError) as exc:
        parser.error("some message")
    assert exc.value.detail.code == m.ErrorCode.INPUT_INVALID

def test_render_non_json(capsys):
    err_env = m.Envelope(
        status="error",
        error=m.ErrorDetail(code=m.ErrorCode.INPUT_INVALID, message="Oops", next_action="Fix it", retry_class="never")
    )
    cli.render(err_env, as_json=False)
    output = capsys.readouterr()
    assert "E_INPUT_INVALID: Oops\nFix it\n" in output.err

    # Demo mode test requires specific keys
    demo_env = m.Envelope(status="ok", data={"demo": True, "log": [], "workspace": "ws", "project_id": "prj", "export_directory": "dir"})
    cli.render(demo_env, as_json=False)
    output = capsys.readouterr()
    assert "Workspace: ws" in output.out

    # Configured service test
    config_env = m.Envelope(status="ok", data={"configured": True, "service": "muse", "account": "test", "network_mode": "offline", "next_action": "Do something"})
    cli.render(config_env, as_json=False)
    output = capsys.readouterr()
    assert "Saved muse settings for test." in output.out
    assert "Next: Do something" in output.out

    # General dict render test
    gen_env = m.Envelope(status="ok", data={"key": "value\x01"})
    cli.render(gen_env, as_json=False)
    output = capsys.readouterr()
    assert '"key": "value\\u0001"' in output.out

def test_system_exit_non_zero(monkeypatch, capsys):
    def throw(*args, **kwargs):
        sys.exit(1)
    monkeypatch.setattr(cli, "dispatch", throw)
    code = cli.main(["--json", "init"])
    assert code == 2
    output = capsys.readouterr()
    res = json.loads(output.out)
    assert res["error"]["code"] == "E_INPUT_INVALID"
