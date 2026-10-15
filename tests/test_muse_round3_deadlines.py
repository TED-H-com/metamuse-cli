"""Module deadline and test-responder liveness contracts, without a network."""

import math
import threading
import time

import pytest
from test_muse_transport import MemoryEndpoint, serve

from metamuse._muse_link import IncompleteBody, ScriptedLink
from metamuse._muse_noise import NoiseParty, handshake_standard


@pytest.mark.parametrize("timeout", [0, -1, math.nan, math.inf])
def test_r3_p5_invalid_handshake_budget_never_sends_msg1(timeout):
    link = ScriptedLink()
    with pytest.raises(IncompleteBody):
        handshake_standard(
            NoiseParty("initiator"),
            link,
            timeout=timeout,
            clock=lambda: 0,
            confidential=lambda: pytest.fail("confidential callback"),
            standard_handoff=lambda callback: pytest.fail("msg3"),
        )
    assert not link.sent


def test_r3_i_responder_waits_until_the_link_closes():
    deadline = time.monotonic() + 30
    entered = threading.Event()
    budgets = []
    errors = []

    class Observed(MemoryEndpoint):
        def recv(self, timeout=0):
            budgets.append(timeout)
            entered.set()
            return super().recv(timeout)

    link = Observed()
    thread = threading.Thread(target=serve, args=(link, None, errors), daemon=True)
    thread.start()
    try:
        assert entered.wait(max(0, deadline - time.monotonic()))
        assert budgets == [None]
        assert thread.is_alive()
    finally:
        link.close()
        thread.join(max(0, deadline - time.monotonic()))
    assert not thread.is_alive() and not errors
