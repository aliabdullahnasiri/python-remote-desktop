# Python Remote Desktop — Approved Screen Streaming (Phases 1–6)

A cross-platform starting point for an authorized remote-desktop application.
This milestone implements the CLI, local screen capture, H.264 recording, local
playback, an approval-gated WebSocket signaling server, and live WebRTC screen
streaming. Remote keyboard and mouse control are not implemented yet.

## Requirements

- Python 3.11+
- `uv`
- Windows 10/11, Linux, or macOS
- On Linux, an X11 session is the easiest path for `mss`.
  Wayland desktop capture generally requires a PipeWire/desktop-portal integration;
  `mss` may not be able to capture a Wayland session directly.

## Install

```bash
uv sync --extra dev
```

Run commands from the project root:

```bash
uv run rdc --help
uv run rdc version
uv run rdc devices
uv run rdc capture --monitor 1 --fps 15 --duration 10
```

Monitor indexes follow `mss`: index `0` is the combined virtual desktop; `1..N`
are individual monitors. Use `rdc devices` to see the available indexes.

`capture` runs locally and prints capture statistics. Press Ctrl+C to stop.
Every captured frame is saved as a JPEG in `screenshots/<session-id>/`, named
`frame_000001.jpg`, `frame_000002.jpg`, and so on. Each run creates a separate
session folder to avoid overwriting earlier captures. The `screenshots/` folder
is ignored by Git.

## Record and play video

```bash
uv run rdc encode --duration 10 --output recording.mp4
uv run rdc view recording.mp4
```

The viewer decodes the first video stream with PyAV and displays it in a
resizable Tkinter window, preserving its aspect ratio. Playback follows video
timestamps. Press **Space** to pause/resume and **Escape** to close, or use the
window close button. The last frame remains visible when playback finishes.
Audio and seeking are not supported in this milestone.

A graphical desktop and Tkinter are required for playback. On Arch Linux install
`tk`; on Debian/Ubuntu install `python3-tk`. Windows Python installations should
include the Tcl/Tk component. Playback does not require access to screen capture.

## Signaling server

Generate a random server token, set it as `RDC_SIGNALING_TOKEN`, then run:

```bash
uv run rdc serve --host 127.0.0.1 --port 8000
```

The FastAPI server provides `GET /health` and `/ws` for authenticated host/viewer
pairing, host approval, and SDP/ICE exchange. See [the signaling protocol](docs/signaling.md)
for Linux/Windows setup, message examples, deployment requirements, and limits.
The host and viewer use this server to establish an approved WebRTC session.

## Live screen streaming

Set the same `RDC_SIGNALING_TOKEN` on the server, host and viewer. Start the server,
then run these commands in separate terminals:

```bash
# Host (shared computer)
uv run rdc host --server ws://SERVER_IP:8000/ws --monitor 1 --fps 15

# Viewer: use the host's session ID, then enter its join token when prompted
uv run rdc connect SESSION_ID --server ws://SERVER_IP:8000/ws --name Alice
```

Type **y** at the host approval prompt to begin sharing. Close the viewer window,
press **Escape**, or press **Ctrl+C** at the host to stop.
See [the streaming guide](docs/streaming.md) for complete Windows PowerShell and
Linux setup, firewall configuration, STUN/TURN, and current limitations.

## Low-bandwidth profile

The default profile is capped at 15 frames per second and scales frames down
to fit within 1280×720 while preserving aspect ratio. This is a capture profile,
not yet a network bitrate guarantee.

## Tests

```bash
uv run pytest
```

## Build Windows and Linux executables

The [GitHub Actions pipeline](.github/workflows/build.yml) runs on branch pushes,
pull requests, version tags (`v*`), and manual runs from the Actions tab. Each run
installs dependencies from `uv.lock`, runs the tests, and builds a standalone
executable on Windows and Linux using Python 3.12 and [rdc.spec](rdc.spec).
The frozen CLI and signaling server are smoke-tested before uploading artifacts.

Download `rdc-windows-x64` or `rdc-linux-x64` from the completed workflow's
**Artifacts** section. Extract the archive and run `rdc.exe --help` on Windows or
`./rdc --help` on Linux. Python does not need to be installed on the target machine.
Linux builds target x86-64 systems with glibc 2.35 or newer (Ubuntu 22.04+);
a graphical desktop and X11 libraries are still required for capture/playback.
The builds do not add native Wayland capture support.

To publish both archives and SHA-256 checksums to a GitHub release, push a version
tag after committing the workflow (update the application version first):

```bash
git tag v0.1.0
git push origin v0.1.0
```

The release publishes only after both platform jobs succeed. It uses the built-in
`GITHUB_TOKEN`; no personal access token is required. Executables are unsigned.

For local builds, run these commands on the target OS (PyInstaller is not a
cross-compiler). The build Python must include Tkinter:

```bash
uv sync --locked --extra dev
uv run --no-sync pyinstaller --clean --noconfirm rdc.spec
uv run --no-sync python scripts/smoke_build.py
```

The output is `dist/rdc.exe` on Windows and `dist/rdc` on Linux.

## Current milestone boundaries

- Phase 1: Typer CLI, command layout, configuration, errors and tests.
- Phase 2: monitor enumeration, frame capture, downscaling and capture statistics.
- Phase 3: local H.264/MP4 recording with PyAV.
- Phase 4: timestamp-paced local video playback with pause/resume and resizing.
- Phase 5: authenticated FastAPI signaling with host approval and session cleanup.
- Phase 6: approved H.264 WebRTC screen sharing with a live viewer.
- Not included yet: remote keyboard/mouse,
  authentication service, or background service installation.

The host must clearly show that screen sharing is active and require approval
before a viewer can connect or receive control permissions.
