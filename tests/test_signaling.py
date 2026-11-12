import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from typer.testing import CliRunner

from rdesktop.main import app as cli
from rdesktop.server.app import create_app

TOKEN = "a" * 32
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client():
    with TestClient(create_app(TOKEN)) as client:
        yield client


def create(host):
    host.send_json({"type": "create"})
    reply = host.receive_json()
    assert reply["type"] == "created"
    return reply


def join(viewer, credentials):
    viewer.send_json(
        {
            "type": "join",
            "session_id": credentials["session_id"],
            "token": credentials["token"],
            "name": "Test viewer",
        }
    )


def pair(host, viewer):
    join(viewer, create(host))
    assert viewer.receive_json() == {"type": "pending"}
    assert host.receive_json() == {"type": "approval_requested", "name": "Test viewer"}


def approve(host, viewer):
    host.send_json({"type": "approve", "approved": True})
    assert host.receive_json() == {"type": "approved"}
    assert viewer.receive_json() == {"type": "approved"}


def test_health_and_configuration(client, monkeypatch):
    assert client.get("/health").json() == {"status": "ok"}
    monkeypatch.delenv("RDC_SIGNALING_TOKEN", raising=False)
    assert CliRunner().invoke(cli, ["serve"]).exit_code == 1
    with (
        pytest.raises(RuntimeError, match="RDC_SIGNALING_TOKEN"),
        TestClient(create_app("")),
    ):
        pass


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong"},
        {**HEADERS, "Origin": "https://example.com"},
    ],
)
def test_authentication(client, headers):
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/ws", headers=headers),
    ):
        pass


def test_approved_offer_answer_ice_and_leave(client):
    with (
        client.websocket_connect("/ws", headers=HEADERS) as host,
        client.websocket_connect("/ws", headers=HEADERS) as viewer,
    ):
        pair(host, viewer)
        approve(host, viewer)
        for sender, receiver, message in [
            (host, viewer, {"type": "offer", "sdp": "v=0\r\n"}),
            (viewer, host, {"type": "answer", "sdp": "v=0\r\n"}),
            (
                host,
                viewer,
                {
                    "type": "ice",
                    "candidate": "candidate:1",
                    "sdpMid": "0",
                    "sdpMLineIndex": 0,
                },
            ),
            (
                viewer,
                host,
                {
                    "type": "ice",
                    "candidate": None,
                    "sdpMid": None,
                    "sdpMLineIndex": None,
                },
            ),
        ]:
            sender.send_json(message)
            assert receiver.receive_json() == message
        viewer.send_json({"type": "ping"})
        assert viewer.receive_json() == {"type": "pong"}
        viewer.send_json({"type": "leave"})
        assert host.receive_json() == {
            "type": "session_closed",
            "reason": "peer_left",
        }
        assert viewer.receive_json() == {
            "type": "session_closed",
            "reason": "peer_left",
        }


@pytest.mark.parametrize(
    "message",
    [
        {"type": "offer", "sdp": "premature"},
        {"type": "approve", "approved": True},
    ],
)
def test_viewer_cannot_bypass_approval(client, message):
    with (
        client.websocket_connect("/ws", headers=HEADERS) as host,
        client.websocket_connect("/ws", headers=HEADERS) as viewer,
    ):
        pair(host, viewer)
        viewer.send_json(message)
        assert viewer.receive_json()["type"] == "error"
        assert host.receive_json()["type"] == "session_closed"


def test_rejection(client):
    with (
        client.websocket_connect("/ws", headers=HEADERS) as host,
        client.websocket_connect("/ws", headers=HEADERS) as viewer,
    ):
        pair(host, viewer)
        host.send_json({"type": "approve", "approved": False})
        assert viewer.receive_json() == {
            "type": "session_closed",
            "reason": "rejected",
        }
        assert host.receive_json()["reason"] == "rejected"


def test_wrong_token_and_duplicate_viewer_do_not_end_session(client):
    with client.websocket_connect("/ws", headers=HEADERS) as host:
        credentials = create(host)
        with client.websocket_connect("/ws", headers=HEADERS) as stranger:
            join(stranger, {**credentials, "token": "wrong"})
            assert stranger.receive_json()["type"] == "error"
        with client.websocket_connect("/ws", headers=HEADERS) as viewer:
            join(viewer, credentials)
            assert viewer.receive_json()["type"] == "pending"
            assert host.receive_json()["type"] == "approval_requested"
            with client.websocket_connect("/ws", headers=HEADERS) as duplicate:
                join(duplicate, credentials)
                assert duplicate.receive_json()["type"] == "error"
            approve(host, viewer)


@pytest.mark.parametrize("departing", ["host", "viewer"])
def test_disconnect_closes_peer_and_invalidates_session(client, departing):
    with client.websocket_connect("/ws", headers=HEADERS) as host:
        credentials = create(host)
        with client.websocket_connect("/ws", headers=HEADERS) as viewer:
            join(viewer, credentials)
            viewer.receive_json()
            host.receive_json()
            sockets = {"host": host, "viewer": viewer}
            sockets[departing].close()
            other = viewer if departing == "host" else host
            assert other.receive_json()["type"] == "session_closed"
            with client.websocket_connect("/ws", headers=HEADERS) as retry:
                join(retry, credentials)
                assert retry.receive_json()["type"] == "error"


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        '{"type":"create","unexpected":true}',
        '{"type":"approve","approved":"true"}',
        '{"type":"create","padding":"' + "x" * 65536 + '"}',
    ],
)
def test_invalid_messages(client, raw):
    with client.websocket_connect("/ws", headers=HEADERS) as socket:
        socket.send_text(raw)
        assert socket.receive_json()["type"] == "error"


def test_binary_message_rejected(client):
    with client.websocket_connect("/ws", headers=HEADERS) as socket:
        socket.send_bytes(b"invalid")
        assert socket.receive_json()["type"] == "error"


def test_sessions_are_isolated(client):
    with (
        client.websocket_connect("/ws", headers=HEADERS) as host,
        client.websocket_connect("/ws", headers=HEADERS) as other,
    ):
        credentials = create(host)
        other_credentials = create(other)
        assert credentials["session_id"] != other_credentials["session_id"]
        with client.websocket_connect("/ws", headers=HEADERS) as viewer:
            join(viewer, credentials)
            viewer.receive_json()
            host.receive_json()
            approve(host, viewer)
            other.send_json({"type": "ping"})
            assert other.receive_json() == {"type": "pong"}
            host.send_json({"type": "offer", "sdp": "only for my viewer"})
            assert viewer.receive_json()["sdp"] == "only for my viewer"


def test_viewer_cannot_send_offer_after_approval(client):
    with (
        client.websocket_connect("/ws", headers=HEADERS) as host,
        client.websocket_connect("/ws", headers=HEADERS) as viewer,
    ):
        pair(host, viewer)
        approve(host, viewer)
        viewer.send_json({"type": "offer", "sdp": "wrong direction"})
        assert viewer.receive_json()["type"] == "error"
        assert host.receive_json()["type"] == "session_closed"
