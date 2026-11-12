# Phase 5: signaling protocol

The server exchanges connection metadata for a future aiortc host/viewer.
It does not capture screens, carry video, or execute keyboard/mouse commands.
One session contains one host and at most one viewer.

## Run

Install dependencies with `uv sync --extra dev`. Generate a token:

```sh
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Set the resulting value in the server and clients. Linux:

```sh
export RDC_SIGNALING_TOKEN='paste-generated-token'
uv run rdc serve
```

PowerShell:

```powershell
$env:RDC_SIGNALING_TOKEN = 'paste-generated-token'
uv run rdc serve
```

The default bind address is 127.0.0.1:8000.
`GET /health` returns `{"status":"ok"}`.
Use `--host 0.0.0.0 --port 8000` for LAN testing.
For deployment across untrusted networks, terminate TLS at a reverse proxy and
connect with `wss://`; the CLI server itself uses plain HTTP/WebSocket.
Use a single worker: sessions live in memory and disappear on restart.
An equivalent ASGI launch is:

```sh
uv run uvicorn rdesktop.server.app:create_app --factory --workers 1 --ws websockets --ws-max-size 65536
```

## Authentication and pairing

Open `ws://127.0.0.1:8000/ws` with the HTTP header
`Authorization: Bearer <RDC_SIGNALING_TOKEN>`.
Tokens are not accepted in URLs. Browser Origin headers are rejected because
this endpoint is for native CLI clients. Missing or incorrect credentials reject
the WebSocket handshake.

1. Host sends `{"type":"create"}`.
2. Host receives `{"type":"created","session_id":"...","token":"..."}`.
3. Share the session ID and its join token with the intended viewer.
4. Viewer connects with the same server authorization header and sends:
   `{"type":"join","session_id":"...","token":"...","name":"Alice"}`.
5. Viewer receives `{"type":"pending"}`. Host receives
   `{"type":"approval_requested","name":"Alice"}`.
6. The host client must ask its local user for approval and send
   `{"type":"approve","approved":true}` only on approval.
7. Both receive `{"type":"approved"}`. A false decision closes the session with
   `{"type":"session_closed","reason":"rejected"}`.

The viewer name is a self-reported label, not a verified identity.
The server enforces that only the host socket can approve. The interactive
host prompt will be part of the client implementation in Phase 6.
Approval here permits signaling only; it does not grant remote input permission.

## Negotiation messages

After approval, the server forwards these messages only to the other socket in
the same session. Clients cannot specify arbitrary recipients.

| Message | Direction | Fields |
| --- | --- | --- |
| `offer` | Host → viewer | `sdp`: nonempty string, up to 60,000 characters |
| `answer` | Viewer → host | `sdp`: nonempty string, after the offer |
| `ice` | Either direction | `candidate`: string or null; `sdpMid`: string or null; `sdpMLineIndex`: nonnegative integer or null |

An ICE candidate of null marks end-of-candidates. Omitted ICE fields default to
null and are included in forwarded messages. The initial offer/answer exchange
is supported once per session; renegotiation requires a new session.
SDP/candidate syntax is interpreted by the future WebRTC clients.

Send `{"type":"ping"}` periodically (for example every 60 seconds) from
**each client** to receive `{"type":"pong"}` and keep that client's signaling
connection active. These application messages are separate from WebSocket
transport ping/pong frames.

Either side may send `{"type":"leave"}`. Both receive
`{"type":"session_closed","reason":"peer_left"}` and are closed.
Disconnects terminate both sides, invalidate the session credentials, and send
the remaining peer `{"type":"session_closed","reason":"disconnected"}`.
Reconnection requires a new session and new approval.

## Limits and lifecycle

- First message deadline: 10 seconds.
- Inactivity deadline per client after registration: 300 seconds.
- Maximum active sessions: 100 per server process.
- Maximum text message size: 65,536 UTF-8 bytes.
- Binary messages, unknown fields/types, wrong roles and out-of-order signaling
  are rejected.
- Invalid messages receive `{"type":"error","message":"Invalid message, credentials, or session state."}`
  followed by closure. If the sender belongs to a session, that session ends.
- Wrong join tokens and duplicate viewers do not end the legitimate session.
- Session secrets and SDP are not logged by application code.

This milestone uses shared server credentials and ephemeral session secrets.
User accounts, device identity, token rotation and per-device permissions remain
Phase 8 work.

Implementation references:
[FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/) and
[Uvicorn settings](https://uvicorn.dev/settings/).
