"""Command-line entry point."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
from rich.console import Console
from rich.table import Table

from rdesktop import __version__
from rdesktop.capture.screen import ScreenCapture
from rdesktop.codec.video_encoder import H264Encoder
from rdesktop.config import CaptureSettings

app = typer.Typer(
    name="rdc",
    help="Consent-based remote desktop development client.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def version() -> None:
    """Print the application version."""
    console.print(f"rdc {__version__}")


@app.command()
def devices() -> None:
    """List available monitors and their dimensions."""
    try:
        with ScreenCapture() as capture:
            monitors = capture.monitors()
    except Exception as exc:
        raise typer.Exit(code=1) from exc

    table = Table(title="Available displays")
    table.add_column("Index", justify="right")
    table.add_column("Position")
    table.add_column("Resolution")
    table.add_column("Description")

    for index, monitor in enumerate(monitors):
        description = (
            "Virtual desktop (all monitors)" if index == 0 else f"Monitor {index}"
        )
        table.add_row(
            str(index),
            f"{monitor['left']}, {monitor['top']}",
            f"{monitor['width']} × {monitor['height']}",
            description,
        )

    console.print(table)


@app.command()
def capture(
    monitor: int = typer.Option(
        1, min=0, help="Monitor index; 0 is the virtual desktop."
    ),
    fps: int = typer.Option(15, min=1, max=15, help="Capture limit (1–15 FPS)."),
    duration: float = typer.Option(
        0.0, min=0.0, help="Seconds to capture; 0 means until Ctrl+C."
    ),
) -> None:
    """Capture the local screen and report performance (no network streaming yet)."""
    settings = CaptureSettings(max_fps=fps)
    frame_interval = 1.0 / fps
    started = time.perf_counter()
    deadline = started + duration if duration > 0 else None
    frames = 0
    last_report = started

    try:
        with ScreenCapture(settings) as screen:
            output_dir = Path("screenshots") / uuid4().hex
            output_dir.mkdir(parents=True, exist_ok=False)
            console.print(f"Saving frames to: {output_dir.resolve()}")
            console.print(
                f"Capture active — monitor {monitor}, max {fps} FPS, "
                f"up to {settings.max_width}×{settings.max_height}. "
                "Press Ctrl+C to stop."
            )

            while deadline is None or time.perf_counter() < deadline:
                loop_started = time.perf_counter()
                frame = screen.capture(monitor)
                frames += 1

                frame.image.save(
                    output_dir / f"frame_{frames:06d}.jpg",
                    format="JPEG",
                    quality=settings.jpeg_quality,
                )

                now = time.perf_counter()
                if now - last_report >= 1.0:
                    elapsed = max(now - started, 0.001)
                    console.print(
                        f"Frames: {frames} | Average: {frames / elapsed:.1f} FPS | "
                        f"Frame: {frame.width}×{frame.height} "
                        f"(source {frame.source_width}×{frame.source_height})"
                    )
                    last_report = now

                sleep_for = frame_interval - (time.perf_counter() - loop_started)
                if sleep_for > 0:
                    time.sleep(sleep_for)

    except KeyboardInterrupt:
        console.print("\nCapture stopped by user.")
    except Exception as exc:
        console.print(f"[red]Capture failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    elapsed = max(time.perf_counter() - started, 0.001)
    console.print(f"Stopped. Captured {frames} frames in {elapsed:.1f}s.")


@app.command()
def encode(
    output: Annotated[Path, typer.Option(help="Local .mp4 output path.")] = Path(
        "recording.mp4"
    ),
    monitor: int = typer.Option(
        1, min=0, help="Monitor index; 0 is the virtual desktop."
    ),
    fps: int = typer.Option(15, min=1, max=15, help="Frames per second (1–15)."),
    duration: float = typer.Option(
        10.0, min=0.0, help="Recording duration in seconds; 0 until Ctrl+C."
    ),
    bitrate: int = typer.Option(
        800_000, min=100_000, max=5_000_000, help="Target bitrate in bits/sec."
    ),
) -> None:
    """Record the local screen to H.264 / MP4. No networking or remote control."""
    settings = CaptureSettings(max_fps=fps)
    frame_interval = 1.0 / fps
    started = time.perf_counter()
    deadline = started + duration if duration > 0 else None
    encoded = 0
    next_report = started + 1

    console.print(
        f"[bold yellow]Local screen recording ACTIVE[/bold yellow] "
        f"| monitor {monitor} | {fps} FPS | {bitrate:,} bps | {output}"
    )
    console.print("Press Ctrl+C to finish and save the recording.")
    try:
        with (
            ScreenCapture(settings) as capture,
            H264Encoder(output, fps=fps, bitrate=bitrate) as encoder,
        ):
            # Validate monitor upfront, before initializing an output file.
            monitors = capture.monitors()
            if not 0 <= monitor < len(monitors):
                raise ValueError(f"Monitor {monitor} is unavailable. Run: rdc devices")
            while deadline is None or time.perf_counter() < deadline:
                tick = time.perf_counter()
                frame = capture.capture(monitor)
                encoder.write(frame.image)
                encoded += 1
                now = time.perf_counter()
                if now >= next_report:
                    console.print(
                        f"Encoded {encoded} frames | {encoded / max(now-started, 0.001):.1f} FPS"
                    )
                    next_report = now + 1.0
                delay = frame_interval - (time.perf_counter() - tick)
                if delay > 0:
                    time.sleep(delay)
    except KeyboardInterrupt:
        console.print("Stopping local recording...")
    except Exception as exc:
        console.print(f"[red]Encoding failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    if encoded:
        console.print(
            f"[green]Saved {encoded} encoded frames to {output.resolve()}[/green]"
        )
    else:
        console.print("No frames were captured. No video was created.")


@app.command()
def view(
    video: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
            help="Local video file to play.",
        ),
    ],
) -> None:
    """Play a local recording. Space pauses; Escape closes the window."""
    from rdesktop.viewer.player import play_video

    console.print("Local playback | Space: pause/resume | Escape: close")
    try:
        frames = play_video(video)
    except KeyboardInterrupt:
        console.print("Playback stopped.")
        return
    except Exception as exc:
        console.print(f"[red]Playback failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"Viewer closed. Displayed {frames} frames.")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Signaling bind address."),
    port: int = typer.Option(8000, min=1, max=65535, help="Signaling port."),
) -> None:
    """Start the signaling server (requires RDC_SIGNALING_TOKEN)."""
    import os

    import uvicorn

    from rdesktop.server.app import create_app

    if len(os.environ.get("RDC_SIGNALING_TOKEN", "")) < 32:
        console.print(
            "[red]Set RDC_SIGNALING_TOKEN to a random token of at least 32 characters.[/red]"
        )
        raise typer.Exit(code=1)
    uvicorn.run(
        create_app(),
        host=host,
        port=port,
        workers=1,
        ws="websockets",
        ws_max_size=65536,
        ws_max_queue=16,
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )


def _run_stream(
    *,
    hosting: bool,
    allow_control: bool = False,
    server: str,
    monitor: int = 1,
    fps: int = 15,
    session_id: str = "",
    name: str = "Viewer",
) -> None:
    import asyncio
    import os

    from rdesktop.network.options import (
        ice_configuration,
        signaling_token,
        validate_url,
    )
    from rdesktop.network.peer import PeerSession
    from rdesktop.viewer.live import LiveViewer

    try:
        token = signaling_token()
        validate_url(server)
        configuration = ice_configuration()
        join_token = ""
        if not hosting:
            join_token = os.environ.get("RDC_JOIN_TOKEN", "") or typer.prompt(
                "Session join token", hide_input=True
            )
        viewer = None if hosting else LiveViewer()
        session = PeerSession(
            host=hosting,
            allow_control=allow_control,
            server=server,
            token=token,
            configuration=configuration,
            monitor=monitor,
            fps=fps,
            session_id=session_id,
            join_token=join_token,
            name=name,
            report=lambda message: console.print(message, markup=False),
            on_frame=viewer.show if viewer is not None else None,
        )
        if viewer is not None:
            viewer.send_input = session.send_input
        asyncio.run(session.run(viewer.run if viewer is not None else None))
    except KeyboardInterrupt:
        console.print("Session stopped.")
    except Exception as exc:
        console.print(f"Streaming failed: {exc}", markup=False)
        raise typer.Exit(code=1) from exc


@app.command()
def host(
    server: Annotated[
        str, typer.Option(help="Signaling WebSocket URL.")
    ] = "ws://127.0.0.1:8000/ws",
    monitor: Annotated[
        int, typer.Option(min=0, help="Monitor index; 0 shares all monitors.")
    ] = 1,
    fps: Annotated[int, typer.Option(min=1, max=15, help="Maximum capture FPS.")] = 15,
    allow_control: Annotated[
        bool, typer.Option(help="Include keyboard/mouse control in host approval.")
    ] = False,
) -> None:
    """Share your screen after approving one viewer in this terminal."""
    _run_stream(
        hosting=True,
        server=server,
        monitor=monitor,
        fps=fps,
        allow_control=allow_control,
    )


@app.command()
def connect(
    session_id: Annotated[str, typer.Argument(help="Session ID printed by the host.")],
    server: Annotated[
        str, typer.Option(help="Signaling WebSocket URL.")
    ] = "ws://127.0.0.1:8000/ws",
    name: Annotated[
        str, typer.Option(help="Viewer label shown in the host approval prompt.")
    ] = "Viewer",
) -> None:
    """View an approved live screen; prompts for the session join token."""
    _run_stream(hosting=False, server=server, session_id=session_id, name=name)


if __name__ == "__main__":
    app()
