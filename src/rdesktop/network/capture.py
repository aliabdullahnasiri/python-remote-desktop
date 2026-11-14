"""Paced screen track with thread-affine MSS capture."""

import asyncio
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

from aiortc import VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from av import VideoFrame
from PIL import ImageOps

from rdesktop.capture.screen import ScreenCapture
from rdesktop.config import CaptureSettings


class ScreenVideoTrack(VideoStreamTrack):
    def __init__(
        self,
        monitor: int = 1,
        fps: int = 15,
        capture_factory: Callable[[CaptureSettings], ScreenCapture] = ScreenCapture,
    ) -> None:
        super().__init__()
        self.settings = CaptureSettings(max_fps=fps)
        self.settings.validate()
        if monitor < 0:
            raise ValueError("Monitor index must be nonnegative.")
        self.bounds: dict[str, int] = {}
        self.monitor = monitor
        self._factory = capture_factory
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="rdc-capture"
        )
        self._capture: ScreenCapture | None = None
        self._started: float | None = None
        self._next_frame = 0.0
        self._size: tuple[int, int] | None = None
        self._closed = False
        self.failure: Exception | None = None
        self.failed = asyncio.Event()

    def _open(self) -> None:
        # Creation, reads and close must all run on the same thread for MSS.
        self._capture = self._factory(self.settings)
        self._capture.start()
        if not 0 <= self.monitor < len(self._capture.monitors()):
            raise ValueError(f"Monitor {self.monitor} is unavailable. Run: rdc devices")

        self.bounds = self._capture.monitors()[self.monitor]

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("The screen track is closed.")
        if self._capture is None:
            await asyncio.get_running_loop().run_in_executor(self._executor, self._open)

    def _frame(self) -> VideoFrame:
        assert self._capture is not None
        image = self._capture.capture(self.monitor).image
        if self._size is None:
            self._size = (
                image.width - image.width % 2,
                image.height - image.height % 2,
            )
            if min(self._size) < 2:
                raise ValueError("Captured frame is too small for H.264.")
        image = ImageOps.pad(image.convert("RGB"), self._size, color="black")
        return VideoFrame.from_image(image)

    async def recv(self) -> VideoFrame:
        if self.readyState != "live":
            raise MediaStreamError
        try:
            await self.start()
            now = time.monotonic()
            if self._started is None:
                self._started = now
                self._next_frame = now
            await asyncio.sleep(max(0, self._next_frame - now))
            timestamp = time.monotonic()
            self._next_frame = timestamp + 1 / self.settings.max_fps
            frame = await asyncio.get_running_loop().run_in_executor(
                self._executor, self._frame
            )
            frame.pts = round((timestamp - self._started) * 90000)
            frame.time_base = Fraction(1, 90000)
            return frame
        except Exception as exc:
            self.failure = exc
            self.failed.set()
            self.stop()
            raise

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stop()

        def close_capture() -> None:
            if self._capture is not None:
                self._capture.close()

        try:
            await asyncio.get_running_loop().run_in_executor(
                self._executor, close_capture
            )
        finally:
            self._executor.shutdown(wait=True)
