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
