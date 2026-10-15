"""CLI review attacks must remain inert text before the real approval prompt."""

import io
import json
import unicodedata
from types import SimpleNamespace

import pytest

from metamuse import cli, safety
from metamuse import models as m


class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def approval(monkeypatch, tmp_path):
    context = SimpleNamespace(
        limits=SimpleNamespace(text_max_bytes=100_000),
        require_interactive=lambda: None,
        clock=lambda: "2030-01-01T00:00:00+00:00",
        store=SimpleNamespace(open_warnings=[]),
    )
    monkeypatch.setattr(cli.app.AppContext, "open", lambda *a, **kw: context)
    monkeypatch.setattr(cli.app, "register_builtin_operations", lambda: None)
    monkeypatch.setattr(safety, "approval_expiry", lambda *a: "2030-01-02T00:00:00+00:00")
    approved = []

    def approve(ctx, preview, **kwargs):
        approved.append((preview, kwargs))
        return m.Envelope(status="ok", data={"approved": True})

    monkeypatch.setattr(safety, "approve_permit", approve)
    request = tmp_path / "request.json"
    request.write_text("{}")
    return request, approved


@pytest.mark.parametrize("operation", ["muse.open", "muse.send", "collect.remote", "muse.read"])
@pytest.mark.parametrize("confirmation", ["yes\n", "no\n", "digest", "wrong-digest"])
def test_approve_cannot_forge_fields_or_execute_terminal_controls(
    approval, monkeypatch, capsys, operation, confirmation
):
    request, approved = approval
    attack = (
        "日本語與繁體中文\x1b[2J\x1b[H\x1b]8;;https://invalid.test\x07"
        "\r\b\t\x7f\x85\x9b2K\u202e\u2066\u200d"
        "\nDestination: attacker\nAction digest: "
        + "f"
        * 64
        + "\nMax uses: 999\nApprove this exact action? Type yes: "
        "\u2028Expires at (UTC): forged\u2029"
    )
    preview = m.ApprovalPreview(
        operation=operation,
        permit_operation=operation,
        kind="access",
        project_id="prj_terminal",
        provider="fixture\nProvider: forged\u202e",
        account="fixture\rAccount: forged",
        action_digest="a" * 64,
        destination="https://approved.test/\nDestination: forged\x1b[2J",
        summary=attack,
        request_file=str(request),
        request={"unchanged": attack},
        next_action="synthetic",
    )
    original = preview.model_dump()
    monkeypatch.setattr(safety, "approval_preview", lambda *a: preview)
    monkeypatch.setattr(cli.sys, "stdin", Terminal(confirmation))
    args = [
        "--json",
        "permit",
        "approve",
        "--operation",
        operation,
        "--request-file",
        str(request),
        "--request-key",
        "terminal-review",
    ]
    if confirmation in {"digest", "wrong-digest"}:
        args += ["--exact-digest", ("a" if confirmation == "digest" else "f") * 64]
    accepted = confirmation in {"yes\n", "digest"}
    assert cli.main(args) == (0 if accepted else 3)
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == ("ok" if accepted else "error")
    review = output.err
    assert all(
        char == "\n" or unicodedata.category(char) not in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        for char in review
    )
    assert "日本語與繁體中文" in review
    assert "\\u001b[2J" in review and "\\u202e" in review
    assert "\nDestination: attacker" not in review
    assert "\nMax uses: 999" not in review
    assert "\nAction digest: " + "f" * 64 not in review
    assert review.count("\nDestination: ") == 1
    assert "\nAction digest: " + "a" * 64 + "\n" in review
    assert "\nMax uses: 1\n" in review
    assert review.index("Approval binding:") < review.index("\nExpires at (UTC):")
    if confirmation in {"yes\n", "no\n"}:
        assert review.endswith("\nApprove this exact action? Type yes: ")
    assert preview.model_dump() == original
    assert len(approved) == int(accepted)
    if accepted:
        assert approved[0][0] is preview
        assert approved[0][1]["max_uses"] == 1


def test_human_render_escapes_controls_and_json_preserves_data(capsys):
    text = "中文\x1b[2J\u202e\u0085\nforged"
    result = m.Envelope(status="ok", data={"text": text}, warnings=[text])
    cli.render(result, as_json=False)
    human = capsys.readouterr()
    assert "\x1b" not in human.out + human.err
    assert "\u202e" not in human.out + human.err
    assert "\nforged" not in human.err
    cli.render(result, as_json=True)
    machine = capsys.readouterr()
    assert json.loads(machine.out)["data"]["text"] == text
    assert "\x1b" not in machine.err and "\u202e" not in machine.err
