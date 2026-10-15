"""Exercise Bash exit checks and capture publication without GUI tools or a display."""

from __future__ import annotations

import json
import os
import shlex
import stat
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


def _tree_bytes(root):
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


@pytest.fixture
def capture_run(tmp_path, monkeypatch):
    """Only replace the GUI, demo setup and source/tool metadata lookups."""
    repo = tmp_path / "repo"
    demo = tmp_path / "demo"
    out = repo / "site/images"
    out.mkdir(parents=True)
    executable = repo / ".venv/bin/metamuse"
    executable.parent.mkdir(parents=True)
    executable.touch()
    for name in ("01-setup.png", "01-setup.txt", "obsolete.png", "manifest.json", "README.md"):
        (out / name).write_bytes(f"old {name}\n".encode())

    def prepare():
        (demo / "tmp").mkdir(parents=True)
        (demo / "inputs").mkdir()
        for name in ("mixed.jsonl", "secrets.jsonl", "mcp.jsonl"):
            (demo / "inputs" / name).write_text("{}\n")

    monkeypatch.setattr(capture, "REPO", repo)
    monkeypatch.setattr(capture, "DEMO", demo)
    monkeypatch.setattr(capture, "OUT", out)
    monkeypatch.setattr(capture, "SCENES", capture.SCENES[:2])
    monkeypatch.setattr(capture, "_prepare_demo", prepare)
    monkeypatch.setattr(capture, "_start_xvfb", lambda: (object(), ":offline"))
    stopped = []
    monkeypatch.setattr(capture, "_stop", stopped.append)
    monkeypatch.setattr(capture, "_versions", lambda: {"test": "offline"})
    monkeypatch.setattr(capture, "_run", lambda *args, **kwargs: "0" * 40)
    return SimpleNamespace(out=out, demo=demo, before=_tree_bytes(out), stopped=stopped)


def _stage_fake_frame(scene, destination):
    png = destination / f"{scene.stem}.png"
    transcript = destination / f"{scene.stem}.txt"
    png.write_bytes(f"synthetic frame {scene.stem}".encode())
    transcript.write_text(f"synthetic transcript {scene.stem}\n")
    return {
        "png": png.name,
        "transcript": transcript.name,
        "bytes": png.stat().st_size,
        "sha256": capture.sha256_file(png),
        "transcript_sha256": capture.sha256_file(transcript),
    }


def _assert_failed_run_untouched(run):
    assert _tree_bytes(run.out) == run.before
    assert list(run.out.parent.iterdir()) == [run.out]
    assert list((run.demo / "tmp").iterdir()) == []
    assert len(run.stopped) == 1


@pytest.mark.parametrize(
    "failure",
    [SystemExit("unexpected exit 5"), RuntimeError("capture failed"), KeyboardInterrupt()],
)
def test_failed_capture_preserves_previous_outputs(capture_run, monkeypatch, failure):
    calls = []

    def fake_capture(scene, display, staged=None):
        # The optional argument also exercises main() on the unmodified review base.
        calls.append(scene.stem)
        if len(calls) == 2:
            raise failure
        return _stage_fake_frame(scene, staged if staged is not None else capture.OUT)

    monkeypatch.setattr(capture, "_capture_scene", fake_capture)
    with pytest.raises(type(failure)):
        capture.main()
    assert calls == [scene.stem for scene in capture.SCENES]
    _assert_failed_run_untouched(capture_run)


def test_real_bash_exit5_aborts_without_publishing(capture_run, monkeypatch, tmp_path):
    calls = []
    bash_dir = tmp_path / "bash-scene"
    bash_dir.mkdir()

    def fake_capture(scene, display, staged=None):
        calls.append(scene.stem)
        if len(calls) == 2:
            result, done = _execute_scene(bash_dir, 5)
            assert result.returncode != 0
            assert "exited 5 (expected 5: no)" in result.stdout
            assert not done.exists()
            assert not (bash_dir / "remaining-command-ran").exists()
            raise SystemExit("scene failed: inspect the demo transcript")
        return _stage_fake_frame(scene, staged if staged is not None else capture.OUT)

    monkeypatch.setattr(capture, "_capture_scene", fake_capture)
    with pytest.raises(SystemExit, match="scene failed"):
        capture.main()
    _assert_failed_run_untouched(capture_run)


@pytest.mark.parametrize("previous_set", [True, False])
def test_success_publishes_complete_set_after_hero(capture_run, monkeypatch, previous_set):
    if not previous_set:
        for path in capture_run.out.iterdir():
            path.unlink()
        capture_run.out.rmdir()
        capture_run.before = {}
    calls = []

    def fake_capture(scene, display, staged):
        assert _tree_bytes(capture_run.out) == capture_run.before
        assert staged.is_relative_to(capture_run.demo / "tmp")
        calls.append(scene.stem)
        return _stage_fake_frame(scene, staged)

    monkeypatch.setattr(capture, "_capture_scene", fake_capture)
    capture.main()
    assert calls == [scene.stem for scene in (*capture.SCENES, capture.HERO)]
    manifest = json.loads((capture_run.out / "manifest.json").read_text())
    assert [Path(record["png"]).stem for record in manifest["images"]] == calls
    names = {"README.md", "manifest.json"}
    for record in manifest["images"]:
        names.update((record["png"], record["transcript"]))
        assert capture.sha256_file(capture_run.out / record["png"]) == record["sha256"]
        assert (
            capture.sha256_file(capture_run.out / record["transcript"])
            == record["transcript_sha256"]
        )
        assert (capture_run.out / record["png"]).stat().st_size == record["bytes"]
    assert set(_tree_bytes(capture_run.out)) == names
    readme = (capture_run.out / "README.md").read_text()
    assert all(f"### {stem}.png" in readme for stem in calls)
    assert list(capture_run.out.parent.iterdir()) == [capture_run.out]
    assert list((capture_run.demo / "tmp").iterdir()) == []
    assert len(capture_run.stopped) == 1


@pytest.mark.parametrize("file_key", ["png", "transcript"])
def test_commit_verifies_copied_hashes_before_manifest(capture_run, monkeypatch, file_key):
    real_copy = capture.shutil.copy2

    def corrupt_copy(source, target):
        result = real_copy(source, target)
        if Path(target).suffix == (".png" if file_key == "png" else ".txt"):
            Path(target).write_bytes(b"corrupted during copy")
        return result

    real_write = Path.write_text
    manifest_writes = []

    def watch_manifest(path, *args, **kwargs):
        if path.name == "manifest.json":
            manifest_writes.append(path)
        return real_write(path, *args, **kwargs)

    monkeypatch.setattr(
        capture, "_capture_scene", lambda scene, display, staged: _stage_fake_frame(scene, staged)
    )
    monkeypatch.setattr(capture.shutil, "copy2", corrupt_copy)
    monkeypatch.setattr(Path, "write_text", watch_manifest)
    with pytest.raises(RuntimeError, match="hash mismatch"):
        capture.main()
    assert manifest_writes == []
    _assert_failed_run_untouched(capture_run)


@pytest.mark.parametrize("failure", [OSError("rename failed"), KeyboardInterrupt()])
def test_commit_rename_failure_restores_old_directory(capture_run, monkeypatch, failure):
    rename = Path.rename

    def fail_publish(path, target):
        if path.name.startswith(".images-stage-"):
            assert not capture_run.out.exists()
            raise failure
        return rename(path, target)

    monkeypatch.setattr(
        capture, "_capture_scene", lambda scene, display, staged: _stage_fake_frame(scene, staged)
    )
    monkeypatch.setattr(Path, "rename", fail_publish)
    with pytest.raises(RuntimeError, match="capture output commit failed"):
        capture.main()
    _assert_failed_run_untouched(capture_run)


def test_failed_rollback_retains_old_set_and_reports_backup(capture_run, monkeypatch):
    rename = Path.rename

    def fail_publish_and_restore(path, target):
        if path.name.startswith((".images-stage-", ".images-backup-")):
            raise OSError("rename failed")
        return rename(path, target)

    monkeypatch.setattr(
        capture, "_capture_scene", lambda scene, display, staged: _stage_fake_frame(scene, staged)
    )
    monkeypatch.setattr(Path, "rename", fail_publish_and_restore)
    with pytest.raises(RuntimeError, match="commit and rollback failed") as error:
        capture.main()
    assert not capture_run.out.exists()
    backups = list(capture_run.out.parent.iterdir())
    assert len(backups) == 1
    assert backups[0].name.startswith(".images-backup-")
    assert backups[0].name in str(error.value)
    assert _tree_bytes(backups[0]) == capture_run.before
    assert list((capture_run.demo / "tmp").iterdir()) == []


def test_backup_cleanup_failure_keeps_complete_new_set(capture_run, monkeypatch):
    remove = capture.shutil.rmtree

    def fail_backup_cleanup(path, *args, **kwargs):
        if Path(path).name.startswith(".images-backup-"):
            raise OSError("cleanup failed")
        return remove(path, *args, **kwargs)

    monkeypatch.setattr(
        capture, "_capture_scene", lambda scene, display, staged: _stage_fake_frame(scene, staged)
    )
    monkeypatch.setattr(capture.shutil, "rmtree", fail_backup_cleanup)
    with pytest.raises(RuntimeError, match="committed; backup cleanup failed") as error:
        capture.main()
    manifest = json.loads((capture_run.out / "manifest.json").read_text())
    assert len(manifest["images"]) == len(capture.SCENES) + 1
    for record in manifest["images"]:
        assert capture.sha256_file(capture_run.out / record["png"]) == record["sha256"]
        assert (
            capture.sha256_file(capture_run.out / record["transcript"])
            == record["transcript_sha256"]
        )
    backups = list(capture_run.out.parent.glob(".images-backup-*"))
    assert len(backups) == 1
    assert backups[0].name in str(error.value)
    assert _tree_bytes(backups[0]) == capture_run.before
    assert list(capture_run.out.parent.glob(".images-stage-*")) == []
    assert list((capture_run.demo / "tmp").iterdir()) == []


def test_publication_preserves_existing_directory_mode(capture_run, monkeypatch):
    capture_run.out.chmod(0o750)
    rename = Path.rename
    published_modes = []

    def observe_publish(path, target):
        if path.name.startswith(".images-stage-"):
            published_modes.append(stat.S_IMODE(path.stat().st_mode))
        return rename(path, target)

    monkeypatch.setattr(
        capture, "_capture_scene", lambda scene, display, staged: _stage_fake_frame(scene, staged)
    )
    monkeypatch.setattr(Path, "rename", observe_publish)
    capture.main()
    assert published_modes == [0o750]
    assert stat.S_IMODE(capture_run.out.stat().st_mode) == 0o750


@pytest.mark.parametrize("umask", [0o002, 0o027])
def test_initial_publication_matches_plain_mkdir_mode(capture_run, monkeypatch, tmp_path, umask):
    for path in capture_run.out.iterdir():
        path.unlink()
    capture_run.out.rmdir()
    monkeypatch.setattr(
        capture, "_capture_scene", lambda scene, display, staged: _stage_fake_frame(scene, staged)
    )
    rename = Path.rename
    published_modes = []

    def observe_publish(path, target):
        if path.name.startswith(".images-stage-"):
            published_modes.append(stat.S_IMODE(path.stat().st_mode))
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", observe_publish)
    previous_umask = os.umask(umask)
    try:
        probe = tmp_path / "plain-mkdir-probe"
        probe.mkdir()
        expected_mode = stat.S_IMODE(probe.stat().st_mode)
        capture.main()
    finally:
        os.umask(previous_umask)
    assert published_modes == [expected_mode]
    assert stat.S_IMODE(capture_run.out.stat().st_mode) == expected_mode


@pytest.mark.parametrize("previous_set", [True, False])
def test_interrupt_after_publish_reports_completed_commit(capture_run, monkeypatch, previous_set):
    if not previous_set:
        for path in capture_run.out.iterdir():
            path.unlink()
        capture_run.out.rmdir()
    rename = Path.rename

    def publish_then_interrupt(path, target):
        result = rename(path, target)
        if path.name.startswith(".images-stage-"):
            raise KeyboardInterrupt()
        return result

    monkeypatch.setattr(
        capture, "_capture_scene", lambda scene, display, staged: _stage_fake_frame(scene, staged)
    )
    monkeypatch.setattr(Path, "rename", publish_then_interrupt)
    with pytest.raises(RuntimeError) as error:
        capture.main()
    assert str(error.value).startswith("capture output committed;")
    manifest = json.loads((capture_run.out / "manifest.json").read_text())
    assert [Path(record["png"]).stem for record in manifest["images"]] == [
        scene.stem for scene in (*capture.SCENES, capture.HERO)
    ]
    names = {"manifest.json", "README.md"}
    for record in manifest["images"]:
        names.update((record["png"], record["transcript"]))
        assert capture.sha256_file(capture_run.out / record["png"]) == record["sha256"]
        assert (
            capture.sha256_file(capture_run.out / record["transcript"])
            == record["transcript_sha256"]
        )
    assert set(_tree_bytes(capture_run.out)) == names
    assert all(
        f"### {record['png']}" in (capture_run.out / "README.md").read_text()
        for record in manifest["images"]
    )
    backups = list(capture_run.out.parent.glob(".images-backup-*"))
    if previous_set:
        assert len(backups) == 1
        assert f"backup retained: {backups[0].name}" in str(error.value)
        assert _tree_bytes(backups[0]) == capture_run.before
    else:
        assert backups == []
        assert "no backup to clean up" in str(error.value)
    assert list(capture_run.out.parent.glob(".images-stage-*")) == []
    assert list((capture_run.demo / "tmp").iterdir()) == []
    assert len(capture_run.stopped) == 1


def test_capture_scene_writes_only_to_staging(capture_run, monkeypatch, tmp_path):
    """Exercise the real scene writer with a tiny in-memory framebuffer double."""
    scene = capture.Scene("offline", "Synthetic frame", (), ("true",), columns=10, lines=4)
    capture._prepare_demo()
    transcripts = capture_run.demo / "transcripts"
    transcripts.mkdir()
    (transcripts / "offline.txt").write_text("synthetic\n")
    (capture_run.demo / "offline.done").touch()
    staged = tmp_path / "staged"
    staged.mkdir()

    class Frame:
        def __init__(self, width=148, height=120):
            self.width, self.height = width, height
            self.size = (width, height)

        def convert(self, mode):
            return self

        def crop(self, rect):
            return Frame(rect[2] - rect[0], rect[3] - rect[1])

        def tobytes(self):
            return b"synthetic RGB pixels"

        def save(self, path, **kwargs):
            path.write_bytes(b"synthetic PNG encoding")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    pillow = ModuleType("PIL")
    pillow.Image = SimpleNamespace(new=lambda *args: Frame(), open=lambda path: Frame())
    pillow.ImageChops = SimpleNamespace(
        difference=lambda *args: SimpleNamespace(getbbox=lambda: (2, 22, 3, 30))
    )
    pillow.ImageGrab = SimpleNamespace(grab=lambda **kwargs: Frame())
    monkeypatch.setitem(sys.modules, "PIL", pillow)
    monkeypatch.setattr(capture.subprocess, "Popen", lambda *args, **kwargs: object())
    monkeypatch.setattr(capture.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        capture, "_window_box", lambda *args: {"x": 0, "y": 0, "width": 148, "height": 120}
    )
    record = capture._capture_scene(scene, ":offline", staged)
    assert set(_tree_bytes(staged)) == {"offline.png", "offline.txt"}
    assert capture.sha256_file(staged / record["png"]) == record["sha256"]
    assert capture.sha256_file(staged / record["transcript"]) == record["transcript_sha256"]
    assert record["bytes"] == (staged / "offline.png").stat().st_size
    assert record["lossless_verified"] is True
    assert _tree_bytes(capture_run.out) == capture_run.before
