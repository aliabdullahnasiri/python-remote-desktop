"""Command-line entry point."""

from __future__ import annotations

import time
from uuid import uuid4
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from rdesktop import __version__
from rdesktop.capture.screen import ScreenCapture
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
        description = "Virtual desktop (all monitors)" if index == 0 else f"Monitor {index}"
        table.add_row(
            str(index),
            f"{monitor['left']}, {monitor['top']}",
            f"{monitor['width']} × {monitor['height']}",
            description,
        )

    console.print(table)


@app.command()
def capture(
    monitor: int = typer.Option(1, min=0, help="Monitor index; 0 is the virtual desktop."),
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


if __name__ == "__main__":
    app()
