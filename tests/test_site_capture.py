"""Exercise the generated Bash exit checks without capture tools or a display."""

from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import capture_site as capture  # noqa: E402


def _execute_scene(tmp_path: Path, code: int, *, partial: bool = False):
    stub = f"stub{code}"
    # Structural input also exercises the old generator on the review's base commit.
    scene = SimpleNamespace(
        stem="test-scene",
        commands=("stub-start", stub, "touch remaining-command-ran"),
        expected_partial_commands=(stub,) if partial else (),
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "out").mkdir()
    for name, status in (("stub-start", 0), (stub, code), ("sleep", 0)):
        path = bin_dir / name
        path.write_text(f"#!/bin/sh\nexit {status}\n")
        path.chmod(0o755)
    transcript = tmp_path / "transcript.txt"
    done = tmp_path / "scene.done"
    script = capture._scene_script(scene, transcript, done)
    # Change only the fixed working directory; execute run(), calls and completion
    # logic verbatim. The sleep stub avoids the terminal's screenshot hold time.
    script = script.replace("cd /tmp/metamuse-demo\n", f"cd {shlex.quote(str(tmp_path))}\n", 1)
    result = subprocess.run(
        ["bash", "--noprofile", "--norc"],
        input=script,
        cwd=tmp_path,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": "/private-home-must-not-appear",
            "SYNTHETIC_SECRET": "environment-value-must-not-appear",
        },
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    return result, done


@pytest.mark.parametrize(
    ("code", "partial", "passes"),
    [
        pytest.param(0, False, True, id="default-0"),
        pytest.param(1, False, False, id="default-1"),
        pytest.param(5, False, False, id="default-5"),
        pytest.param(7, False, False, id="default-7"),
        pytest.param(0, True, False, id="partial-0"),
        pytest.param(1, True, False, id="partial-1"),
        pytest.param(5, True, True, id="partial-5"),
        pytest.param(7, True, False, id="partial-7"),
    ],
)
def test_generated_script_requires_exact_exit(tmp_path, code, partial, passes):
    result, done = _execute_scene(tmp_path, code, partial=partial)
    assert (result.returncode == 0) is passes, result.stdout + result.stderr
    assert done.exists() is passes
    assert (tmp_path / "remaining-command-ran").exists() is passes


def test_failure_stops_remaining_commands(tmp_path):
    result, done = _execute_scene(tmp_path, 5)
    assert not (tmp_path / "remaining-command-ran").exists()
    assert not done.exists()
    assert result.returncode != 0


@pytest.mark.parametrize(("code", "partial"), [(5, False), (0, True), (1, True), (7, False)])
def test_failure_diagnostic_is_specific_and_bounded(tmp_path, code, partial):
    result, _ = _execute_scene(tmp_path, code, partial=partial)
    expected = "yes" if partial else "no"
    assert f"test-scene: command 2/3 exited {code} (expected 5: {expected})" in result.stdout
    assert str(tmp_path) not in result.stdout + result.stderr
    assert "private-home-must-not-appear" not in result.stdout + result.stderr
    assert "environment-value-must-not-appear" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("declaration", "message"),
    [
        pytest.param(("missing-command",), "exactly one command", id="missing-command"),
        pytest.param(("cd /tmp/metamuse-demo",), "summary.*partial", id="no-partial-summary"),
    ],
)
def test_invalid_partial_declaration_fails_during_module_load(monkeypatch, declaration, message):
    source = Path(capture.__file__).read_text()
    original = 'stem="01-setup",'
    assert source.count(original) == 1
    source = source.replace(
        original, f"{original}\n        expected_partial_commands={declaration!r},", 1
    )
    module = ModuleType("invalid_capture_scene")
    module.__file__ = capture.__file__
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(ValueError, match=message):
        exec(compile(source, module.__file__, "exec"), module.__dict__)


@pytest.mark.parametrize(
    ("commands", "declaration", "message"),
    [
        (("stub5",), ("stub5", "stub5"), "duplicate"),
        (("stub5", "stub5"), ("stub5",), "exactly one command"),
    ],
)
def test_ambiguous_partial_declarations_are_rejected(commands, declaration, message):
    with pytest.raises(ValueError, match=message):
        capture.Scene(
            stem="ambiguous-scene",
            summary="A partial result.",
            acceptance=(),
            commands=commands,
            expected_partial_commands=declaration,
        )


def test_partial_declaration_follows_the_exact_command_when_reordered(tmp_path):
    scene = capture.Scene(
        stem="partial-scene",
        summary="An explicitly partial result.",
        acceptance=(),
        commands=("stub5", "stub0"),
        expected_partial_commands=("stub5",),
    )
    reordered = replace(scene, commands=("stub0", "stub5"))
    script = capture._scene_script(reordered, tmp_path / "transcript", tmp_path / "done")
    assert "\nrun 1 0 stub0\nrun 2 5 stub5\n" in script


def test_shipped_scenes_validate_without_claiming_partial_command_exits():
    for scene in (*capture.SCENES, capture.HERO):
        assert replace(scene) == scene
        assert scene.expected_partial_commands == ()
    queue = next(scene for scene in capture.SCENES if scene.stem == "02-queue")
    assert "partial" in queue.summary
    # These commands report the worker/read success via exit 0, even though the
    # committed transcript shows that the stored import job itself is partial.
    assert queue.commands[5:7] == (
        "metamuse --workspace ws worker run --once",
        'metamuse --workspace ws jobs get "$J" > out/job.json',
    )
    transcript = (ROOT / "site/images/02-queue.txt").read_text()
    assert '"state": "partial"' in transcript
    assert '{"committed":7,"rejected":7}' in transcript
