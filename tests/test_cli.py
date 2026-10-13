import json

import pytest

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
        [
            "add",
            "--project",
            "prj_fixture",
            "--file",
            "missing.json",
            "--kind",
            "json",
            "--request-key",
            "one",
        ],
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
        ["mcp"],
        ["auth", "import", "--file", "missing.json"],
        ["permit", "create", "--project", "prj_fixture", "--kind", "share", "--request-key", "one"],
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
