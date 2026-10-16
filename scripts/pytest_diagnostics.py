"""GIL-held timed pytest dumps; native fatal-signal handling stays enabled.

Loaded explicitly by the cohort runner or copied byte-for-byte as checks/conftest.py.
The Python observer can run only when the interpreter makes the GIL available.
Flushed item boundaries survive that gap; external deadlines remain independent.
"""

import contextlib
import faulthandler
import os
import sys
import threading

import pytest

INTERVAL_SECONDS = 30.0
OBSERVER = pytest.StashKey()


class TimedDiagnostics:
    def __init__(self, stderr_fd, interval=INTERVAL_SECONDS):
        self.fd = os.dup(stderr_fd)
        self.interval = interval
        self.worker = None
        self.cancel = None
        self.error = None

    @contextlib.contextmanager
    def watching(self, nodeid):
        if self.worker is not None or self.fd is None:
            raise RuntimeError("Diagnostic observer is already active or closed")
        os.write(self.fd, f"PYTEST_DIAGNOSTIC_START node={nodeid}\n".encode())
        cancel = threading.Event()

        def collect():
            if not cancel.wait(self.interval):
                try:
                    os.write(
                        self.fd,
                        f"\nPYTEST_DIAGNOSTIC node={nodeid} after={self.interval:g}s\n".encode(),
                    )
                    # A Python thread enters this synchronous stdlib call with
                    # the GIL, unlike the native dump_traceback_later watchdog.
                    faulthandler.dump_traceback(file=self.fd, all_threads=True)
                except Exception as exc:
                    self.error = exc

        worker = threading.Thread(target=collect, name="pytest-timed-diagnostic", daemon=False)
        entered = False
        try:
            self.cancel = cancel
            self.worker = worker
            worker.start()
            entered = True
            yield
        finally:
            self.stop()
            if entered:
                os.write(self.fd, f"PYTEST_DIAGNOSTIC_FINISH node={nodeid}\n".encode())

    def stop(self):
        if self.cancel is not None:
            self.cancel.set()
        if self.worker is not None and self.worker.is_alive():
            self.worker.join()
        self.worker = self.cancel = None

    def close(self):
        try:
            self.stop()
        finally:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None


def pytest_configure(config):
    if float(config.getini("faulthandler_timeout") or 0) != 0:
        raise pytest.UsageError("GIL-held diagnostics require faulthandler_timeout=0")
    capture = config.pluginmanager.get_plugin("capturemanager")
    # Duplicate the real stderr once, outside test capture. Only this duplicate
    # is owned/closed here; pytest and -X faulthandler retain their fatal handler.
    with capture.global_and_fixture_disabled() if capture else contextlib.nullcontext():
        config.stash[OBSERVER] = TimedDiagnostics(sys.__stderr__.fileno())


def pytest_sessionstart(session):
    if not faulthandler.is_enabled():
        raise pytest.UsageError("GIL-held diagnostics require native fatal handling enabled")
    observer = session.config.stash[OBSERVER]
    os.write(
        observer.fd,
        (
            f"PYTEST_DIAGNOSTIC_READY interval={observer.interval:g}s fatal=True "
            f"helper={__file__}\n"
        ).encode(),
    )


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_protocol(item):
    with item.config.stash[OBSERVER].watching(item.nodeid):
        return (yield)


def pytest_sessionfinish(session, exitstatus):
    error = session.config.stash[OBSERVER].error
    if error is not None:
        print(f"Timed diagnostic failed: {error!r}", file=sys.stderr, flush=True)
        if exitstatus == 0:
            session.exitstatus = pytest.ExitCode.INTERNAL_ERROR


def pytest_unconfigure(config):
    if OBSERVER in config.stash:
        config.stash[OBSERVER].close()
        del config.stash[OBSERVER]
