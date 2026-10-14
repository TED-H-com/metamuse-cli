import socket

import pytest

from metamuse.app import AppContext, create_project


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Network access attempted in an offline test")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
def ctx(tmp_path):
    return AppContext.open(tmp_path / "workspace")


@pytest.fixture
def project(ctx):
    return create_project(ctx, {"name": "Synthetic project", "request_key": "project-1"}).data["id"]


@pytest.fixture(autouse=True)
def muse_transport_disk_root(request, monkeypatch):
    """Transport fixture helpers use the wrapper's disk basetemp, including imported helpers."""
    if request.node.path.name not in {
        "test_muse_transport.py",
        "test_muse_findings.py",
        "test_muse_standard.py",
    }:
        return
    tmp_path = request.getfixturevalue("tmp_path")
    monkeypatch.setenv("METAMUSE_TEST_STORE_ROOT", str(tmp_path / "muse-transports"))
