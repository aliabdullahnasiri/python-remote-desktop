# Python Remote Desktop — Foundation (Phases 1–2)

A cross-platform starting point for an authorized remote-desktop application.
This milestone implements the CLI and local screen capture only. It does **not**
implement networking, remote input, persistence, or unattended access.

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
uv run rdc capture --monitor 1 --fps 15 --duration 10 --snapshot first-frame.jpg
```

Monitor indexes follow `mss`: index `0` is the combined virtual desktop; `1..N`
are individual monitors. Use `rdc devices` to see the available indexes.

`capture` runs locally and prints capture statistics. Press Ctrl+C to stop.
The optional `--snapshot` saves the first captured frame as a JPEG.

## Low-bandwidth profile

The default profile is capped at 15 frames per second and scales frames down
to fit within 1280×720 while preserving aspect ratio. This is a capture profile,
not yet a network bitrate guarantee.

## Tests

```bash
uv run pytest
```

## Build a Windows executable

Build on Windows (PyInstaller is not a cross-compiler):

```powershell
uv sync --extra dev
uv run pyinstaller --clean --onefile --name rdc --paths src src/rdesktop/main.py
```

The output will be in `dist/rdc.exe`. Build separately on each target OS.
For Linux, run PyInstaller on Linux; for Windows, run it on Windows.

## Current milestone boundaries

- Phase 1: Typer CLI, command layout, configuration, errors and tests.
- Phase 2: monitor enumeration, frame capture, downscaling and capture statistics.
- Not included yet: WebRTC signaling, video transport, remote keyboard/mouse,
  authentication service, or background service installation.

The host must clearly show that screen sharing is active and require approval
before a viewer can connect or receive control permissions.
