"""Bounded diagnostics probes; nested pytest cases are counted by the author."""

import ctypes
import faulthandler
import hashlib
import importlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def test_cohort_activates_safe_diagnostics(tmp_path, monkeypatch):
    import ci_cohorts

    captured = []

    def no_runtime(args, plugins):
        captured.extend(plugins)
        return 2

    monkeypatch.setattr(pytest, "main", no_runtime)
    assert (
        ci_cohorts.run_cohort(
            "core",
            tmp_path / "core.json",
            "a" * 40,
            ["-q", "-o", "faulthandler_timeout=0"],
        )
        == 2
    )
    safe = [plugin for plugin in captured if hasattr(plugin, "TimedDiagnostics")]
    assert len(safe) == 1, "cohort has no activated GIL-held timed diagnostics"
    assert Path(safe[0].__file__).resolve() == ROOT / "scripts/pytest_diagnostics.py"
    assert safe[0].INTERVAL_SECONDS == 30
    with pytest.raises(pytest.UsageError, match="faulthandler_timeout=0"):
        safe[0].pytest_configure(SimpleNamespace(getini=lambda name: 30))
    session = SimpleNamespace(
        config=SimpleNamespace(stash={safe[0].OBSERVER: SimpleNamespace(error=OSError("probe"))}),
        exitstatus=0,
    )
    safe[0].pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.INTERNAL_ERROR
    session.exitstatus = 2
    safe[0].pytest_sessionfinish(session, 2)
    assert session.exitstatus == 2


def evidence_dir(tmp_path, name):
    base = Path(os.environ.get("DIAGNOSTIC_TEST_EVIDENCE", str(tmp_path))) / name
    base.mkdir(parents=True, exist_ok=True)
    return base


def blocked_worker(release):
    release.wait()


def project_text_workload(dumped):
    deadline = time.monotonic() + 5
    while not dumped.is_set() and time.monotonic() < deadline:
        text = "public_key=value;" * 512
        assert len([piece for piece in text.split(";") if "=" in piece]) == 512
    assert dumped.is_set(), "Python observer never collected a timed traceback"


def test_startup_interrupt_owns_and_stops_worker(tmp_path, monkeypatch):
    diagnostic = importlib.import_module("pytest_diagnostics")
    evidence = evidence_dir(tmp_path, "startup-interrupt")
    captured = {}
    real_start = threading.Thread.start
    target = diagnostic.TimedDiagnostics.watching.__wrapped__.__code__
    previous_trace = sys.gettrace()

    def record_start(worker):
        real_start(worker)
        captured["worker"] = worker

    def interrupt_after_start(frame, event, arg):
        if event == "line" and frame.f_code is target and "worker" in captured:
            captured["cancel"] = frame.f_locals["cancel"]
            captured["started"] = captured["worker"].is_alive()
            captured["owned"] = frame.f_locals["self"].worker is captured["worker"]
            sys.settrace(None)
            signal.raise_signal(signal.SIGINT)
        return interrupt_after_start

    monkeypatch.setattr(threading.Thread, "start", record_start)
    with (evidence / "traceback.log").open("w") as stream:
        observer = diagnostic.TimedDiagnostics(stream.fileno())
        owned_fd = observer.fd
        try:
            with pytest.raises(KeyboardInterrupt):
                sys.settrace(interrupt_after_start)
                try:
                    with observer.watching("synthetic::startup_interrupt"):
                        pytest.fail("startup SIGINT did not precede the test body")
                finally:
                    sys.settrace(previous_trace)
            observer.close()
            result = {
                "worker_started": captured["started"],
                "worker_owned_at_interrupt": captured["owned"],
                "worker_alive_after_close": captured["worker"].is_alive(),
                "worker_cleared": observer.worker is None,
                "cancel_cleared": observer.cancel is None,
                "fd_closed": observer.fd is None,
            }
            (evidence / "result.json").write_text(json.dumps(result, indent=2))
            assert not result["worker_alive_after_close"], "startup worker survives close"
            assert all(result[name] for name in result if name != "worker_alive_after_close")
            assert observer.error is None and faulthandler.is_enabled()
            with pytest.raises(OSError):
                os.fstat(owned_fd)
        finally:
            sys.settrace(previous_trace)
            # The immutable baseline leaks this worker; never wait for its timer.
            if "cancel" in captured:
                captured["cancel"].set()
                captured["worker"].join(timeout=1)
                (evidence / "cleanup.json").write_text(
                    json.dumps({"worker_alive": captured["worker"].is_alive()})
                )
                assert not captured["worker"].is_alive()
            observer.close()
    trace = (evidence / "traceback.log").read_text()
    assert "PYTEST_DIAGNOSTIC_START node=synthetic::startup_interrupt" in trace
    assert "PYTEST_DIAGNOSTIC_FINISH" not in trace


def test_pre_start_failure_preserves_error_and_closes_fd(tmp_path, monkeypatch):
    diagnostic = importlib.import_module("pytest_diagnostics")
    evidence = evidence_dir(tmp_path, "pre-start-failure")
    failure = RuntimeError("synthetic thread-start failure")
    created = []

    def fail_before_start(worker):
        created.append(worker)
        raise failure

    monkeypatch.setattr(threading.Thread, "start", fail_before_start)
    with (evidence / "traceback.log").open("w") as stream:
        observer = diagnostic.TimedDiagnostics(stream.fileno())
        owned_fd = observer.fd
        try:
            with pytest.raises(RuntimeError) as caught:
                with observer.watching("synthetic::pre_start_failure"):
                    pytest.fail("failed start entered the test body")
            assert caught.value is failure
            assert len(created) == 1 and created[0].ident is None
            assert observer.worker is None and observer.cancel is None
        finally:
            observer.close()
        with pytest.raises(OSError):
            os.fstat(owned_fd)
    trace = (evidence / "traceback.log").read_text()
    assert "PYTEST_DIAGNOSTIC_START node=synthetic::pre_start_failure" in trace
    assert "PYTEST_DIAGNOSTIC_FINISH" not in trace


@pytest.mark.parametrize("outcome", ["success", "failure", "interrupt"])
def test_timed_dump_has_live_frames_and_cleans_up(tmp_path, monkeypatch, outcome):
    diagnostic = importlib.import_module("pytest_diagnostics")
    evidence = evidence_dir(tmp_path, outcome)
    release, dumped = threading.Event(), threading.Event()
    blocked = threading.Thread(target=blocked_worker, args=(release,))
    blocked.start()
    real_dump = faulthandler.dump_traceback
    gil_checks = []

    def held_dump(**kwargs):
        gil_checks.append(ctypes.pythonapi.PyGILState_Check())
        real_dump(**kwargs)
        dumped.set()

    def forbidden_native_timer(*args, **kwargs):
        raise AssertionError("native timed watchdog must never be armed")

    monkeypatch.setattr(faulthandler, "dump_traceback", held_dump)
    monkeypatch.setattr(faulthandler, "dump_traceback_later", forbidden_native_timer)
    error = {"success": None, "failure": ValueError, "interrupt": KeyboardInterrupt}[outcome]
    with (evidence / "traceback.log").open("w") as stream:
        observer = diagnostic.TimedDiagnostics(stream.fileno(), interval=0.03)
        owned_fd = observer.fd
        try:

            def run_body():
                with observer.watching("synthetic::test_" + outcome):
                    project_text_workload(dumped)
                    if error:
                        raise error("synthetic outcome")

            if error:
                with pytest.raises(error, match="synthetic outcome"):
                    run_body()
            else:
                run_body()
            assert observer.worker is None and observer.error is None
            assert gil_checks == [1] and faulthandler.is_enabled()
        finally:
            observer.close()
            release.set()
            blocked.join()
        with pytest.raises(OSError):
            os.fstat(owned_fd)
        # A fast test cancels its shipped 30-second observer without waiting.
        with_stop = diagnostic.TimedDiagnostics(stream.fileno())
        try:
            with with_stop.watching("synthetic::fast"):
                pass
            assert with_stop.worker is None and gil_checks == [1]
        finally:
            with_stop.close()
    trace = (evidence / "traceback.log").read_text()
    assert "synthetic::test_" + outcome in trace
    assert "project_text_workload" in trace and "blocked_worker" in trace
    assert "PYTEST_DIAGNOSTIC_START node=synthetic::test_" + outcome in trace
    assert "PYTEST_DIAGNOSTIC_FINISH node=synthetic::test_" + outcome in trace
    assert "PYTEST_DIAGNOSTIC node=synthetic::fast" not in trace and not blocked.is_alive()


def test_only_exact_sixth_harness_is_admitted(tmp_path, monkeypatch):
    import installed_journeys as installed

    work, evidence = tmp_path / "work", tmp_path / "provenance"
    monkeypatch.setattr(installed, "source_identity", lambda *args: {"runtime_commit": "a" * 40})
    installed.prepare(ROOT, work, evidence, "a" * 40)
    data = json.loads((evidence / "provenance.json").read_text())
    original = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in installed.HARNESS
    }
    helper = ROOT / "scripts/pytest_diagnostics.py"
    expected = {**original, "conftest.py": hashlib.sha256(helper.read_bytes()).hexdigest()}
    assert len(original) == 5 and data["harness_hashes"] == expected
    assert data["diagnostic_helper"] == {
        "source": "scripts/pytest_diagnostics.py",
        "destination": "checks/conftest.py",
        "sha256": expected["conftest.py"],
    }
    copied = work / "checks/conftest.py"
    copied.write_bytes(helper.read_bytes() + b"\n# changed\n")
    with pytest.raises(ValueError, match="harness/corpus"):
        installed.verify_harness(ROOT, work)
    shutil.copyfile(helper, copied)
    unknown = work / "checks/duplicate-helper.py"
    shutil.copyfile(helper, unknown)
    with pytest.raises(ValueError, match="harness/corpus"):
        installed.verify_harness(ROOT, work)
    unknown.unlink()
    copied.unlink()
    copied.symlink_to(helper)
    with pytest.raises(ValueError, match="harness/corpus"):
        installed.verify_harness(ROOT, work)
    copied.unlink()
    shutil.copyfile(helper, copied)
    assert installed.verify_harness(ROOT, work) == expected


def test_gil_stuck_keeps_start_evidence_and_external_termination(tmp_path):
    evidence = evidence_dir(tmp_path, "gil-gap")
    ready = tmp_path / "ready"
    code = (
        "import ctypes, faulthandler, pathlib, sys\n"
        f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
        "from pytest_diagnostics import TimedDiagnostics\n"
        "assert faulthandler.is_enabled()\n"
        "libc = ctypes.PyDLL(None)\n"
        "observer = TimedDiagnostics(2, interval=0.03)\n"
        "with observer.watching('synthetic::gil_stuck'):\n"
        f"    pathlib.Path({str(ready)!r}).touch()\n"
        "    libc.pause()\n"  # PyDLL deliberately retains the GIL in this tiny child.
    )
    command = [sys.executable, "-u", "-X", "faulthandler", "-c", code]
    (evidence / "argv.json").write_text(json.dumps(command, indent=2))
    with (
        (evidence / "stdout").open("w") as stdout,
        (evidence / "stderr").open("w") as stderr,
    ):
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists(), "synthetic child never reached the native GIL boundary"
            time.sleep(0.15)
            assert process.poll() is None
            os.killpg(process.pid, signal.SIGTERM)
            assert process.wait(timeout=5) == -signal.SIGTERM
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    trace = (evidence / "stderr").read_text()
    assert "PYTEST_DIAGNOSTIC_START node=synthetic::gil_stuck" in trace
    assert "PYTEST_DIAGNOSTIC node=" not in trace
    assert "PYTEST_DIAGNOSTIC_FINISH" not in trace
    (evidence / "exit.json").write_text(
        json.dumps({"returncode": process.returncode, "actual_pytest_cases": 0})
    )


def test_actual_cohort_and_copied_helper_wiring(tmp_path, monkeypatch):
    import installed_journeys as installed

    evidence = evidence_dir(tmp_path, "wiring")
    cohort = tmp_path / "cohort"
    cohort.mkdir()

    # No real journey or product code is imported by any of the three child cases.
    def probe(expected):
        return (
            "    import faulthandler, pathlib, sys\n"
            "    plugins = [p for p in request.config.pluginmanager.get_plugins() "
            "if hasattr(p, 'TimedDiagnostics')]\n"
            "    assert len(plugins) == 1\n"
            "    plugin = plugins[0]\n"
            f"    expected = pathlib.Path({str(expected)!r})\n"
            "    assert pathlib.Path(plugin.__file__).resolve() == expected\n"
            "    observer = request.config.stash[plugin.OBSERVER]\n"
            "    assert observer.interval == 30 and observer.worker.is_alive()\n"
            "    assert float(request.config.getini('faulthandler_timeout')) == 0\n"
            "    assert faulthandler.is_enabled() and 'metamuse' not in sys.modules\n"
        )

    (cohort / "pytest.ini").write_text("[pytest]\n")
    (cohort / "test_probe.py").write_text(
        "def test_diagnostic(request):\n" + probe(ROOT / "scripts/pytest_diagnostics.py")
    )
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""}
    env.pop("PYTHONPATH", None)
    command = [
        sys.executable,
        "-X",
        "faulthandler",
        str(ROOT / "scripts/ci_cohorts.py"),
        "run",
        "--cohort",
        "core",
        "--source-sha",
        "a" * 40,
        "--manifest",
        str(evidence / "core.json"),
        "--",
        "-q",
        "--maxfail=1",
        "-o",
        "faulthandler_timeout=0",
        "--basetemp",
        str(tmp_path / "cohort-pytest"),
        "--junitxml",
        str(evidence / "cohort.xml"),
    ]
    (evidence / "cohort-argv.json").write_text(json.dumps(command, indent=2))
    with (
        (evidence / "cohort.stdout").open("w") as stdout,
        (evidence / "cohort.stderr").open("w") as stderr,
    ):
        process = subprocess.Popen(
            command, cwd=cohort, env=env, stdout=stdout, stderr=stderr, start_new_session=True
        )
        try:
            result = process.wait(timeout=30)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
    (evidence / "cohort-exit.json").write_text(
        json.dumps({"returncode": result, "actual_cases": 1})
    )
    assert result == 0, (evidence / "cohort.stdout").read_text() + (
        evidence / "cohort.stderr"
    ).read_text()
    manifest = json.loads((evidence / "core.json").read_text())
    assert manifest["selected"] == manifest["finished"] == ["test_probe.py::test_diagnostic"]
    assert manifest["pytest_exit_code"] == 0

    work = tmp_path / "installed"
    (work / "run").mkdir(parents=True)
    for node in installed.NODES:
        module, name = node.split("::")
        path = work / "checks" / module
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"def {name}(request):\n" + probe(work / "checks/conftest.py"))
    shutil.copyfile(ROOT / installed.DIAGNOSTIC_SOURCE, work / "checks/conftest.py")
    popen = subprocess.Popen
    with (
        (evidence / "installed.stdout").open("w") as stdout,
        (evidence / "installed.stderr").open("w") as stderr,
    ):

        def capture_child(command, **kwargs):
            (evidence / "installed-argv.json").write_text(json.dumps(command, indent=2))
            return popen(command, **kwargs, stdout=stdout, stderr=stderr)

        with monkeypatch.context() as patch:
            patch.setattr(installed.subprocess, "Popen", capture_child)
            data = {"pytest_exit_code": None}
            assert installed.run_pytest(sys.executable, work, evidence, data) == 0
    assert data["summary"] == {"passed": 2, "failed": 0, "skipped": 0}
    (evidence / "installed-exit.json").write_text(json.dumps({"returncode": 0, "actual_cases": 2}))
    assert (
        "PYTEST_DIAGNOSTIC_READY interval=30s fatal=True"
        in (evidence / "cohort.stderr").read_text()
    )
    assert (
        "PYTEST_DIAGNOSTIC_READY interval=30s fatal=True"
        in (evidence / "installed.stderr").read_text()
    )
