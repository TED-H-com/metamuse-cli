"""Cancellation interrupts the socket; native curl ownership transfers sequentially."""

import threading
import time
from types import SimpleNamespace

import pytest
from curl_cffi.const import CurlWsFlag

from metamuse._muse_http import CurlSocket
from metamuse._muse_link import SendUncertain


class Calls:
    def __init__(self):
        self.active = None
        self.log = []
        self.overlaps = []

    def enter(self, name):
        thread = threading.get_ident()
        if self.active is not None:
            self.overlaps.append((self.active, name, thread))
        self.active = (name, thread)
        self.log.append((name, thread))

    def leave(self):
        self.active = None


def join(worker, deadline):
    worker.join(max(0, deadline - time.monotonic()))
    assert not worker.is_alive(), "curl operation did not unwind"


def test_close_does_not_touch_websocket_while_recv_is_active():
    # Port of the concurrency repro: no native handle, so it proves ownership,
    # not a native crash or a way to interrupt an arbitrary descriptor-less fake.
    entered, release = threading.Event(), threading.Event()
    calls, failures = Calls(), []
    deadline = time.monotonic() + 30

    class WebSocket:
        def recv_fragment(self):
            calls.enter("recv")
            entered.set()
            try:
                assert release.wait(max(0, deadline - time.monotonic()))
                return b"", SimpleNamespace(flags=CurlWsFlag.CLOSE, bytesleft=0)
            finally:
                calls.leave()

        def close(self):
            calls.enter("cleanup")
            calls.leave()

    pipe = CurlSocket(WebSocket(), "203.0.113.1")

    def receive():
        try:
            pipe.recv(60)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=receive, daemon=True)
    worker.start()
    try:
        assert entered.wait(max(0, deadline - time.monotonic()))
        pipe.close()  # Must return while the fake recv is still held.
        before_release = list(calls.log)
    finally:
        release.set()
        join(worker, deadline)
        pipe.close()
    assert not failures
    assert not calls.overlaps, calls.overlaps
    assert [name for name, _ in before_release] == ["recv"]
    assert [name for name, _ in calls.log] == ["recv", "cleanup"]
    assert calls.log[-1][1] == worker.ident


def test_self_thread_close_waits_for_its_native_operation_to_unwind():
    calls = Calls()

    class WebSocket:
        def recv_fragment(self):
            calls.enter("recv")
            pipe.close()  # A listener can run in the transport owner's thread.
            calls.leave()
            return b"", SimpleNamespace(flags=CurlWsFlag.CLOSE, bytesleft=0)

        def close(self):
            calls.enter("cleanup")
            calls.leave()

    pipe = CurlSocket(WebSocket(), "203.0.113.1")
    try:
        pipe.recv(1)
    finally:
        pipe.close()
    assert not calls.overlaps
    assert [name for name, _ in calls.log] == ["recv", "cleanup"]


def test_close_before_send_never_calls_websocket_send():
    sent = []

    class WebSocket:
        def send_bytes(self, data):
            sent.append(data)

    pipe = CurlSocket(WebSocket(), "203.0.113.1")
    pipe.close()
    with pytest.raises(SendUncertain):
        pipe.send(b"never handed off")
    assert not sent


class NativeBoundary:
    """A tracked curl boundary over an actual local socket, never a remote peer."""

    def __init__(self, sock, mode="recv"):
        self.sock, self.mode = sock, mode
        self.curl = self
        self.closed = False
        self.autoclose = True
        self.calls = Calls()
        self.entered = threading.Event()
        self.woke = threading.Event()
        self.release = threading.Event()
        self.deadline = time.monotonic() + 30

    def getinfo(self, option):
        self.calls.enter("getinfo")
        try:
            return self.sock.fileno()
        finally:
            self.calls.leave()

    def recv_fragment(self):
        self.calls.enter("recv")
        self.entered.set()
        try:
            chunk = self.sock.recv(1024)
            self.woke.set()
            assert self.release.wait(max(0, self.deadline - time.monotonic()))
            return chunk, SimpleNamespace(flags=CurlWsFlag.BINARY, bytesleft=0)
        finally:
            self.calls.leave()

    def send_bytes(self, data):
        self.calls.enter("send")
        self.entered.set()
        try:
            assert self.release.wait(max(0, self.deadline - time.monotonic()))
            self.sock.sendall(data)
        finally:
            self.calls.leave()

    def terminate(self):
        self.calls.enter("cleanup")
        try:
            self.closed = True
            self.sock.close()
        finally:
            self.calls.leave()

    def close(self):
        self.terminate()


@pytest.mark.parametrize("operation", ["recv", "send"])
def test_native_interrupt_returns_before_operation_and_cleans_on_owner(operation):
    import select
    import socket

    local, remote = socket.socketpair()
    ws = NativeBoundary(local)
    pipe = CurlSocket(ws, "203.0.113.1")
    failures, outcomes = [], []

    def owner():
        try:
            outcomes.append(pipe.recv(60) if operation == "recv" else pipe.send(b"rejected"))
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=owner, daemon=True)
    worker.start()
    try:
        assert ws.entered.wait(max(0, ws.deadline - time.monotonic()))
        pipe.close()
        # close has returned but the owner is still held inside its native call.
        assert worker.is_alive()
        assert not ws.closed
        assert not ws.calls.overlaps
        assert select.select([remote], [], [], 0)[0] == [remote]
        remote.settimeout(1)
        assert remote.recv(1024) == b""  # Shutdown is effective when close returns.
        if operation == "recv":
            assert ws.woke.wait(max(0, ws.deadline - time.monotonic())), (
                "shutdown did not wake recv"
            )
        ws.release.set()
        join(worker, ws.deadline)
        assert ws.closed
        cleanup = [thread for name, thread in ws.calls.log if name == "cleanup"]
        assert cleanup == [worker.ident]
        assert not ws.calls.overlaps
        assert pipe._interrupt is None
        if operation == "recv":
            assert outcomes == [None] and not failures
        else:
            assert len(failures) == 1 and isinstance(failures[0], SendUncertain)
        pipe.close()
        with pytest.raises(SendUncertain):
            pipe.send(b"must not send")
        assert [name for name, _ in ws.calls.log].count("send") == int(operation == "send")
        assert [name for name, _ in ws.calls.log].count("cleanup") == 1
    finally:
        # Also unblocks the old implementation when the test fails before release.
        try:
            local.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        remote.close()
        ws.release.set()
        join(worker, ws.deadline)
        pipe.close()
        local.close()


def test_again_select_wakes_without_any_callback_curl_call(monkeypatch):
    import socket

    from curl_cffi.const import CurlECode
    from curl_cffi.curl import CurlError

    import metamuse._muse_http as http

    local, remote = socket.socketpair()
    ws = NativeBoundary(local)

    def again():
        ws.calls.enter("recv")
        ws.calls.leave()
        raise CurlError("synthetic again", CurlECode.AGAIN)

    ws.recv_fragment = again
    pipe = CurlSocket(ws, "203.0.113.1")
    selecting, released, outcomes, errors = threading.Event(), threading.Event(), [], []
    original = http.select.select

    def waiting(*args):
        selecting.set()
        result = original(*args)
        released.set()
        return result

    monkeypatch.setattr(http.select, "select", waiting)

    def owner():
        try:
            outcomes.append(pipe.recv(60))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=owner, daemon=True)
    worker.start()
    try:
        assert selecting.wait(max(0, ws.deadline - time.monotonic()))
        pipe.close()
        assert released.wait(max(0, ws.deadline - time.monotonic()))
        join(worker, ws.deadline)
        assert outcomes == [None] and not errors
        assert not ws.calls.overlaps
        assert [name for name, _ in ws.calls.log] == ["getinfo", "recv", "cleanup"]
        assert ws.calls.log[-1][1] == worker.ident
    finally:
        remote.close()
        pipe.close()
        join(worker, ws.deadline)


def test_duplicate_descriptor_cannot_interrupt_a_reused_curl_fd():
    import os
    import socket

    local, remote = socket.socketpair()
    victim, victim_peer = socket.socketpair()
    remote.settimeout(1)
    victim.settimeout(1)
    ws = NativeBoundary(local)
    pipe = CurlSocket(ws, "203.0.113.1")
    original_fd = local.fileno()
    # Simulate backend-side closure, then force that descriptor number to refer
    # to an unrelated connection. Cancellation must use the owned duplicate.
    local.close()
    os.dup2(victim.fileno(), original_fd)
    try:
        pipe.close()
        assert remote.recv(1) == b""
        victim_peer.sendall(b"alive")
        assert victim.recv(5) == b"alive"
        assert pipe._interrupt is None
        assert [name for name, _ in ws.calls.log].count("cleanup") == 1
        pipe.close()
    finally:
        os.close(original_fd)
        remote.close()
        victim.close()
        victim_peer.close()


def test_concurrent_idle_close_cleans_exactly_once_and_does_not_leak():
    import socket

    local, remote = socket.socketpair()
    ws = NativeBoundary(local)
    pipe = CurlSocket(ws, "203.0.113.1")
    interrupt = pipe._interrupt
    barrier, errors = threading.Barrier(5), []

    def close():
        try:
            barrier.wait(timeout=10)
            pipe.close()
        except BaseException as exc:
            errors.append(exc)

    workers = [threading.Thread(target=close, daemon=True) for _ in range(4)]
    for worker in workers:
        worker.start()
    try:
        barrier.wait(timeout=10)
        for worker in workers:
            join(worker, ws.deadline)
        assert not errors and not ws.calls.overlaps
        assert [name for name, _ in ws.calls.log] == ["getinfo", "cleanup"]
        assert interrupt.fileno() == -1 and local.fileno() == -1
        assert remote.recv(1) == b""
    finally:
        pipe.close()
        remote.close()


def test_no_descriptor_fake_again_is_an_api_error_and_still_cleans():
    from curl_cffi.const import CurlECode
    from curl_cffi.curl import CurlError

    from metamuse.models import CoreError, ErrorCode

    cleaned = []

    class Fake:
        def recv_fragment(self):
            raise CurlError("again", CurlECode.AGAIN)

        def close(self):
            cleaned.append(1)

    pipe = CurlSocket(Fake(), "203.0.113.1")
    try:
        with pytest.raises(CoreError) as caught:
            pipe.recv(1)
        assert caught.value.detail.code == ErrorCode.DEPENDENCY_MISSING
    finally:
        pipe.close()
    assert cleaned == [1]


@pytest.mark.parametrize("blocking", [False, True])
def test_descriptor_duplication_preserves_native_flags(blocking):
    import fcntl
    import os
    import socket

    local, remote = socket.socketpair()
    local.setblocking(blocking)  # Before the adapter owns this synthetic socket.
    observer = os.dup(local.fileno())
    flags = fcntl.fcntl(observer, fcntl.F_GETFL)
    timeout = local.gettimeout()
    pipe = CurlSocket(NativeBoundary(local), "203.0.113.1")
    try:
        assert fcntl.fcntl(observer, fcntl.F_GETFL) == flags
        assert os.get_blocking(observer) is blocking
        assert local.gettimeout() == timeout
        pipe.close()
        assert fcntl.fcntl(observer, fcntl.F_GETFL) == flags
        assert os.get_blocking(observer) is blocking
    finally:
        pipe.close()
        os.close(observer)
        local.close()
        remote.close()


def test_duplicate_wrap_failure_releases_descriptor_and_handle(monkeypatch):
    import os
    import socket

    from metamuse.models import CoreError, ErrorCode

    local, remote = socket.socketpair()
    ws = NativeBoundary(local)
    descriptors = []
    duplicate = socket.dup

    def track_duplicate(fd):
        result = duplicate(fd)
        descriptors.append(result)
        return result

    def failed_wrapper(*args, **kwargs):
        raise OSError("synthetic descriptor wrapper failure")

    monkeypatch.setattr(socket, "dup", track_duplicate)
    monkeypatch.setattr(socket, "socket", failed_wrapper)
    try:
        with pytest.raises(CoreError) as caught:
            CurlSocket(ws, "203.0.113.1")
        assert caught.value.detail.code == ErrorCode.NETWORK_TIMEOUT
        assert caught.value.detail.message == "Muse socket has no file descriptor"
        assert len(descriptors) == 1
        with pytest.raises(OSError):
            os.fstat(descriptors[0])
        assert local.fileno() == -1 and remote.recv(1) == b""
        assert [name for name, _ in ws.calls.log] == ["getinfo", "cleanup"]
    finally:
        local.close()
        remote.close()


@pytest.fixture
def native_curl():
    """Locked backend against one fixed loopback peer: no DNS or external service."""
    import base64
    import fcntl
    import hashlib
    import os
    import socket

    from curl_cffi import Curl
    from curl_cffi.const import CurlECode, CurlInfo, CurlOpt
    from curl_cffi.curl import CurlError
    from curl_cffi.requests import WebSocket

    state = SimpleNamespace(
        calls=Calls(),
        receiving=threading.Event(),
        sending=threading.Event(),
        release=threading.Event(),
        peer_done=threading.Event(),
        errors=[],
        peer_bytes=[],
        again=[],
        gate_recv=False,
        gate_send=False,
        deadline=time.monotonic() + 30,
        workers=[],
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(30)

    def server():
        try:
            with listener.accept()[0] as peer:
                peer.settimeout(30)
                request = b""
                while b"\r\n\r\n" not in request:
                    request += peer.recv(4096)
                    assert len(request) <= 8192
                key = next(
                    line.split(b":", 1)[1].strip()
                    for line in request.split(b"\r\n")
                    if line.lower().startswith(b"sec-websocket-key:")
                )
                accept = base64.b64encode(
                    hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()
                )
                peer.sendall(
                    b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                    b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n"
                )
                state.peer_bytes.append(peer.recv(4096))
        except BaseException as exc:
            state.errors.append(exc)
        finally:
            state.peer_done.set()

    class TrackedCurl(Curl):
        def getinfo(self, option):
            state.calls.enter("getinfo")
            try:
                return super().getinfo(option)
            finally:
                state.calls.leave()

        def ws_recv(self):
            state.calls.enter("recv")
            state.receiving.set()
            try:
                if state.gate_recv:
                    assert state.release.wait(max(0, state.deadline - time.monotonic()))
                try:
                    return super().ws_recv()
                except CurlError as exc:
                    if exc.code == CurlECode.AGAIN:
                        state.again.append(exc.code)
                    raise
            finally:
                state.calls.leave()

        def ws_send(self, *args, **kwargs):
            state.calls.enter("send")
            state.sending.set()
            try:
                if state.gate_send:
                    assert state.release.wait(max(0, state.deadline - time.monotonic()))
                return super().ws_send(*args, **kwargs)
            finally:
                state.calls.leave()

        def close(self):
            if self._curl is None:
                return
            state.calls.enter("cleanup")
            try:
                super().close()
            finally:
                state.calls.leave()

    server_thread = threading.Thread(target=server, daemon=True)
    server_thread.start()
    curl = state.curl = TrackedCurl()
    ws = WebSocket(curl=curl)
    pipe = None
    observer = None
    try:
        # libcurl connects directly to this literal loopback listener; the global
        # Python no-network fixture stays enabled for every other connection.
        ws.connect(
            f"ws://127.0.0.1:{listener.getsockname()[1]}/synthetic",
            timeout=10,
            curl_options={CurlOpt.PROXY: b""},
        )
        fd = curl.getinfo(CurlInfo.ACTIVESOCKET)
        observer = os.dup(fd)
        state.flags = fcntl.fcntl(observer, fcntl.F_GETFL)
        state.observer = observer
        state.default_timeout = socket.getdefaulttimeout()
        pipe = state.pipe = CurlSocket(ws, "127.0.0.1")
        state.interrupt = pipe._interrupt
        yield state
    finally:
        if pipe is not None:
            pipe.close()
        else:
            ws.terminate()
        state.release.set()
        for worker in state.workers:
            join(worker, state.deadline)
        listener.close()
        join(server_thread, state.deadline)
        if observer is not None:
            os.close(observer)


def assert_native_flags(state):
    import fcntl
    import os
    import socket

    assert not os.get_blocking(state.observer), "curl must remain nonblocking"
    assert fcntl.fcntl(state.observer, fcntl.F_GETFL) == state.flags
    assert socket.getdefaulttimeout() == state.default_timeout


def assert_native_cleaned(state, owner):
    assert_native_flags(state)  # Observer pins the file description past cleanup.
    assert not state.calls.overlaps, state.calls.overlaps
    assert [thread for name, thread in state.calls.log if name == "cleanup"] == [owner]
    assert state.curl._curl is None and state.interrupt.fileno() == -1
    assert state.peer_done.wait(max(0, state.deadline - time.monotonic()))
    assert not state.errors
    assert state.peer_bytes == [b""]  # Neither app bytes nor a WS close frame.


@pytest.mark.parametrize("wait", ["recv", "select"])
def test_locked_curl_backend_cancellation_wakes_receive_and_cleans_handle(
    native_curl, monkeypatch, wait
):
    import metamuse._muse_http as http
    from metamuse.models import CoreError, ErrorCode

    state = native_curl
    assert_native_flags(state)
    selecting, outcomes, errors = threading.Event(), [], []
    original = http.select.select

    def waiting(*args):
        selecting.set()
        return original(*args)

    monkeypatch.setattr(http.select, "select", waiting)
    state.gate_recv = wait == "recv"

    def owner():
        try:
            outcomes.append(state.pipe.recv(60))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=owner, daemon=True)
    state.workers.append(worker)
    worker.start()
    entered = state.receiving if wait == "recv" else selecting
    assert entered.wait(max(0, state.deadline - time.monotonic()))
    state.pipe.close()
    assert_native_flags(state)
    if wait == "recv":
        # The notification returned while the wrapper holds the native operation.
        assert worker.is_alive() and state.curl._curl is not None
        assert not any(name == "cleanup" for name, _ in state.calls.log)
    else:
        assert state.again, "the real backend must reach AGAIN/select on an idle peer"
    state.release.set()
    join(worker, state.deadline)
    # Native shutdown during ws_recv can produce the existing fixed read error.
    assert (outcomes == [None] and not errors) or (
        wait == "recv"
        and not outcomes
        and len(errors) == 1
        and isinstance(errors[0], CoreError)
        and errors[0].detail.code == ErrorCode.NETWORK_TIMEOUT
        and errors[0].detail.message == "Muse websocket read failed"
    ), (outcomes, errors)
    assert not any(name == "send" for name, _ in state.calls.log)
    assert_native_cleaned(state, worker.ident)


def test_locked_curl_idle_receive_obeys_finite_deadline_without_peer_close(native_curl):
    state = native_curl
    assert_native_flags(state)
    finished, outcomes, errors = threading.Event(), [], []

    def owner():
        try:
            outcomes.append(state.pipe.recv(0.05))
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    worker = threading.Thread(target=owner, daemon=True)
    state.workers.append(worker)
    started = time.monotonic()
    worker.start()
    completed = finished.wait(5)
    elapsed = time.monotonic() - started
    assert completed, "finite idle receive blocked inside native curl; peer is still open"
    join(worker, state.deadline)
    assert outcomes == [None] and not errors
    assert state.again and not state.peer_done.is_set()
    assert elapsed >= 0.04  # The deadline, not a peer close, ended the read.
    assert_native_flags(state)
    assert state.curl._curl is not None and not state.calls.overlaps
    state.pipe.close()
    assert_native_cleaned(state, threading.get_ident())


def test_locked_curl_shutdown_prevents_send_after_entry_check(native_curl):
    state = native_curl
    assert_native_flags(state)
    state.gate_send = True
    outcomes, errors = [], []

    def owner():
        try:
            outcomes.append(state.pipe.send(b"must never reach the peer"))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=owner, daemon=True)
    state.workers.append(worker)
    worker.start()
    assert state.sending.wait(max(0, state.deadline - time.monotonic()))
    # The pipe's closed check already passed. Socket shutdown must still prevent
    # the real ws_send below from transmitting when the gate is released.
    state.pipe.close()
    assert worker.is_alive() and state.curl._curl is not None
    assert_native_flags(state)
    state.release.set()
    join(worker, state.deadline)
    assert not outcomes and len(errors) == 1 and isinstance(errors[0], SendUncertain)
    assert_native_cleaned(state, worker.ident)


@pytest.mark.parametrize("route", ["read", "session.start", "chat.stream"])
def test_pending_msg2_notification_interrupts_adapter_before_native_cleanup(ctx, route):
    import socket

    from test_muse_round8_notifications import freeze
    from test_muse_round8c_registration import setup_connection
    from test_muse_round9_pending import call
    from test_muse_transport import Token

    transport, http, connector, writer = setup_connection(ctx)
    transport._clock = lambda: 0.0  # Test ordering independently of shared-disk latency.
    connect = connector.connect
    pairs, wrappers, outcomes, errors = [], [], [], []
    deadline = time.monotonic() + 60
    token = Token()

    class NoiseBoundary(NativeBoundary):
        def __init__(self, sock, peer, pause):
            super().__init__(sock)
            self.peer, self.pause = peer, pause
            self.deadline = deadline

        def send_bytes(self, data):
            self.calls.enter("send")
            try:
                self.peer.send(data)
            finally:
                self.calls.leave()

        def recv_fragment(self):
            if self.pause:
                return super().recv_fragment()
            self.calls.enter("recv")
            try:
                return self.peer.recv(0), SimpleNamespace(flags=CurlWsFlag.BINARY, bytesleft=0)
            finally:
                self.calls.leave()

        def terminate(self):
            super().terminate()
            self.peer.close()

    def wrapped_connect(*args, **kwargs):
        peer = connect(*args, **kwargs)
        local, remote = socket.socketpair()
        pairs.append((local, remote))
        ws = NoiseBoundary(local, peer, len(wrappers) == 0)
        wrappers.append(ws)
        return CurlSocket(ws, peer.peer_address)

    connector.connect = wrapped_connect

    def owner():
        try:
            outcomes.append(call(transport, route, token))
        except BaseException as exc:
            errors.append(exc)

    # Wait for first receive without polling: connect publishes the boundary via
    # an event so the test never relies on scheduling or a sleep.
    created = threading.Event()
    original = connector.connect

    def created_connect(*args, **kwargs):
        pipe = original(*args, **kwargs)
        created.set()
        return pipe

    connector.connect = created_connect
    worker = threading.Thread(target=owner, daemon=True)
    worker.start()
    try:
        assert created.wait(max(0, deadline - time.monotonic()))
        ws = wrappers[0]
        assert ws.entered.wait(max(0, deadline - time.monotonic()))
        freeze(writer, confidential=True)
        # The Store notification has returned, with transport lock held by owner.
        assert transport._trust_invalidated and not ws.closed
        assert ws.woke.wait(max(0, deadline - time.monotonic()))
        assert not ws.calls.overlaps
        assert not connector.peers[0].frames and connector.peers[0].msg3_payload is None
        ws.release.set()
        join(worker, deadline)
        assert not errors and len(outcomes) == 1 and outcomes[0].bytes_handed_off
        assert len(connector.urls) == 2 and len(http.calls) == 8
        assert [len(peer.frames) for peer in connector.peers] == [0, 1]
        assert token.calls == (route != "read")
        assert ws.closed and not ws.calls.overlaps
        assert [thread for name, thread in ws.calls.log if name == "cleanup"] == [worker.ident]
    finally:
        for ws in wrappers:
            ws.release.set()
        for _local, remote in pairs:
            remote.close()
        join(worker, deadline)
        transport.close()
        for local, _remote in pairs:
            local.close()


def test_error_and_close_at_operation_exit_cannot_strand_cleanup(monkeypatch):
    calls = Calls()

    class Fake:
        def recv_fragment(self):
            raise OSError("synthetic read failure")

        def close(self):
            calls.log.append(("cleanup", threading.get_ident()))

    pipe = CurlSocket(Fake(), "203.0.113.1")
    lock = pipe._state_lock

    class NotifyAtIdle:
        fired = False

        def __enter__(self):
            return lock.__enter__()

        def __exit__(self, *args):
            idle = not pipe._active and not self.fired
            lock.__exit__(*args)
            if idle:
                self.fired = True
                pipe.close()  # Exactly at the operation -> idle ownership transfer.

    monkeypatch.setattr(pipe, "_state_lock", NotifyAtIdle())
    from metamuse.models import CoreError, ErrorCode

    with pytest.raises(CoreError) as caught:
        pipe.recv(1)
    assert caught.value.detail.code == ErrorCode.NETWORK_TIMEOUT
    assert [name for name, _ in calls.log] == ["cleanup"]
    pipe.close()
    assert len(calls.log) == 1
