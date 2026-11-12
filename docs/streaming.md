# Phase 6: approved WebRTC screen streaming

The host shares one selected monitor (or the combined desktop) with one viewer.
Video is H.264 over WebRTC, up to 1280×720 at 15 FPS. The CLI manages the session;
the viewer opens a resizable Tkinter window. No keyboard/mouse control or audio
is implemented.

## Install on all computers

Use the same project revision on server, host and viewer:

```sh
uv sync --extra dev
```

Screen capture supports Windows and Linux X11 through MSS. Native Wayland
capture still requires a future PipeWire/desktop-portal backend.
The viewer needs a graphical desktop with Tkinter: Python's Tcl/Tk component on
Windows, `tk` on Arch, or `python3-tk` on Debian/Ubuntu.

## Windows example (PowerShell)

Use three terminals; the signaling server and host can run on the same computer.

### 1. Signaling server

Generate the token once, print it locally, and share it with your intended host
and viewer through a trusted channel:

```powershell
$env:RDC_SIGNALING_TOKEN = uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
$env:RDC_SIGNALING_TOKEN
uv run rdc serve --host 0.0.0.0
```

### 2. Host (the shared computer)

Set the same server token in this terminal. Replace `192.168.1.10` with the
signaling computer's LAN IP, or use `127.0.0.1` if it is on this computer.

```powershell
$env:RDC_SIGNALING_TOKEN = 'paste-server-token'
uv run rdc devices
uv run rdc host --server ws://192.168.1.10:8000/ws --monitor 1 --fps 15
```

The host prints a **session ID** and a separate **join token**. Share both with
the intended viewer. The host waits without starting screen capture.

### 3. Viewer

```powershell
$env:RDC_SIGNALING_TOKEN = 'paste-server-token'
uv run rdc connect SESSION_ID --server ws://192.168.1.10:8000/ws --name Alice
```

Replace `SESSION_ID` with the ID printed by the host. Enter the join token at
the hidden prompt. Alternatively set `$env:RDC_JOIN_TOKEN = 'paste-join-token'`
before running the command. Tokens are not passed in command-line arguments.

### 4. Approve at the host

When the host asks `Allow viewer 'Alice' to see your screen? [y/N]:`, type
`y` and press Enter. Enter alone, EOF, or no response within 120 seconds denies
the request. The displayed viewer name is self-reported, not a verified identity.

After approval the host prints **SCREEN SHARING ACTIVE**. Once WebRTC connects,
the viewer window displays the shared screen.

Press **Ctrl+C** at the host, **Escape** in the viewer window, or close the viewer
window to disconnect. Each session has one viewer; reconnecting requires a new
host session, new join token, and new approval.

## Linux terminals

The commands are the same; environment variable syntax differs:

```sh
# Server terminal: generate once.
export RDC_SIGNALING_TOKEN="$(uv run python -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf '%s\n' "$RDC_SIGNALING_TOKEN"
uv run rdc serve --host 0.0.0.0
```

In the host and viewer terminals:

```sh
export RDC_SIGNALING_TOKEN='paste-server-token'
# Host:
uv run rdc host --server ws://192.168.1.10:8000/ws --monitor 1
# Viewer (a separate terminal/computer):
uv run rdc connect SESSION_ID --server ws://192.168.1.10:8000/ws --name Alice
```

## Network setup

Start on the same LAN. The server needs inbound TCP on port 8000 and the clients
need permission for WebRTC's UDP traffic. Allow the Python application through
the relevant firewall. A connection timing out usually means ICE cannot find a
working path; forwarding the signaling port alone does not carry video.

No public STUN service is contacted by default. For different networks, configure
your own STUN/TURN services on **both host and viewer**:

| Variable | Example | Purpose |
| --- | --- | --- |
| `RDC_STUN_URL` | `stun:stun.example.com:3478` | Discover reachable addresses |
| `RDC_TURN_URL` | `turn:turn.example.com:3478?transport=udp` | Relay media when direct connectivity fails |
| `RDC_TURN_USERNAME` | Your assigned username | TURN authentication |
| `RDC_TURN_PASSWORD` | Your assigned password | TURN authentication |

A TURN service is separate from this signaling server. Configure valid TURN
credentials; STUN alone cannot traverse every NAT/firewall. For signaling across
untrusted networks, use a TLS reverse proxy and a `wss://.../ws` URL.
WebRTC encrypts media with DTLS/SRTP; plain `ws://` signaling is not encrypted.

## Implementation and limits

- aiortc sends complete SDP offers/answers after gathering ICE candidates.
- The host sends video only; the viewer receives video only.
- Capture and downscaling occur on a single dedicated worker thread so MSS
  resources stay on their creating thread.
- Video frames have monotonic 90 kHz timestamps and an FPS cap. Dimensions remain
  fixed for a session, with even sizing and letterboxing for H.264.
- H.264 bitrate follows aiortc's encoder/congestion behavior; there is no strict
  800 Kbps streaming cap in this milestone.
- The viewer keeps only the latest pending frame to avoid a growing display queue.
- Both clients send signaling heartbeats. Loss of signaling ends sharing.
- Connection setup times out after 45 seconds; a viewer receiving no video for
  30 seconds stops. Failures close capture, peer connections and background tasks.
- CLI approval and connection state stay visible in the host terminal.
- No auto-reconnect, unattended access, remote input, or persistent pairing.
- Build standalone executables separately on each OS using the README command;
  Windows packaging and cross-machine GUI operation require manual platform testing.

References: [aiortc API](https://aiortc.readthedocs.io/en/latest/api.html)
and [signaling protocol](signaling.md).
