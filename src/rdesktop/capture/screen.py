"""Cross-platform local screen capture using mss and Pillow."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Self

from PIL import Image

from rdesktop.config import CaptureSettings


@dataclass(slots=True)
class CapturedFrame:
    image: Image.Image
    captured_at: float
    source_width: int
    source_height: int

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height


def fit_dimensions(
    width: int, height: int, max_width: int, max_height: int
) -> tuple[int, int]:
    """Fit a frame inside a bounding box without changing its aspect ratio."""
    if min(width, height, max_width, max_height) <= 0:
        raise ValueError("Image and bounding-box dimensions must be positive.")

    scale = min(max_width / width, max_height / height, 1.0)
    return max(1, round(width * scale)), max(1, round(height * scale))


class ScreenCapture:
    """Capture a selected monitor. Index 0 means the virtual desktop."""

    def __init__(self, settings: CaptureSettings | None = None) -> None:
        self.settings = settings or CaptureSettings()
        self.settings.validate()
        self._mss: Any | None = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def start(self) -> None:
        if self._mss is not None:
            return

        # On Linux, mss commonly uses X11. Wayland sessions may require
        # PipeWire/desktop-portal capture instead.
        if (
            os.name == "posix"
            and os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
            and not os.environ.get("DISPLAY")
        ):
            raise RuntimeError(
                "A Wayland session without XWayland DISPLAY was detected. "
                "This mss backend cannot capture it; a PipeWire/desktop-portal "
                "backend is needed for native Wayland capture."
            )

        try:
            import mss
        except ImportError as exc:
            raise RuntimeError("Missing dependency. Install with: uv sync") from exc

        self._mss = mss.mss()

    def close(self) -> None:
        if self._mss is not None:
            self._mss.close()
            self._mss = None

    def monitors(self) -> list[dict[str, int]]:
        if self._mss is None:
            self.start()
        assert self._mss is not None
        return [dict(monitor) for monitor in self._mss.monitors]

    def capture(self, monitor_index: int = 1) -> CapturedFrame:
        if self._mss is None:
            self.start()
        assert self._mss is not None

        monitors = self._mss.monitors
        if not 0 <= monitor_index < len(monitors):
            raise ValueError(
                f"Invalid monitor index {monitor_index}. "
                f"Valid indexes: 0 through {len(monitors) - 1}."
            )

        shot = self._mss.grab(monitors[monitor_index])
        source_width, source_height = shot.size
        image = Image.frombytes("RGB", shot.size, shot.rgb)

        target_size = fit_dimensions(
            image.width,
            image.height,
            self.settings.max_width,
            self.settings.max_height,
        )
        if target_size != image.size:
            image = image.resize(target_size, Image.Resampling.LANCZOS)

        return CapturedFrame(
            image=image,
            captured_at=time.perf_counter(),
            source_width=source_width,
            source_height=source_height,
        )
