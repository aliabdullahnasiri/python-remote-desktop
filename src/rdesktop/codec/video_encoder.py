"""Local H.264 MP4 encoder for authorized screen-capture sessions.

The encoder intentionally does not open any network connection. Video output is
used to verify the frame -> H.264 pipeline before we add WebRTC transport.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageOps

if TYPE_CHECKING:
    import av


class H264Encoder:
    """Encode PIL RGB frames into a fixed-size H.264 / MP4 video.

    Frame dimensions are locked to the first input frame, with odd dimensions
    rounded down to even values for yuv420p compatibility. Later frames are
    letterboxed to avoid changing dimensions in the middle of a file.
    """

    def __init__(
        self,
        output: Path,
        fps: int = 15,
        bitrate: int = 800_000,
        preset: str = "ultrafast",
    ) -> None:
        if not 1 <= fps <= 60:
            raise ValueError("FPS must be between 1 and 60.")
        if not 100_000 <= bitrate <= 20_000_000:
            raise ValueError("Bitrate must be between 100000 and 20000000 bps.")
        if output.suffix.lower() != ".mp4":
            raise ValueError("Output file must use the .mp4 extension.")
        if preset not in (
            "ultrafast",
            "superfast",
            "veryfast",
            "faster",
            "fast",
            "medium",
        ):
            raise ValueError("Unsupported x264 preset.")

        self.output = Path(output)
        self.fps = fps
        self.bitrate = bitrate
        self.preset = preset
        self.frames_written = 0
        self.width = 0
        self.height = 0
        self._container: Any = None
        self._stream: Any = None
        self._closed = False

    def __enter__(self) -> "H264Encoder":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _start(self, image: Image.Image) -> None:
        try:
            import av
        except ImportError as exc:
            raise RuntimeError(
                "PyAV is missing. Install dependencies with: uv sync"
            ) from exc

        self.width = image.width - image.width % 2
        self.height = image.height - image.height % 2
        if self.width < 2 or self.height < 2:
            raise ValueError("Input frame is too small to encode.")

        self.output.parent.mkdir(parents=True, exist_ok=True)
        container = av.open(str(self.output), mode="w", format="mp4")
        try:
            stream = container.add_stream("libx264", rate=self.fps)
            stream.width = self.width
            stream.height = self.height
            stream.pix_fmt = "yuv420p"
            stream.bit_rate = self.bitrate
            stream.codec_context.gop_size = self.fps * 2
            stream.options = {"preset": self.preset, "tune": "zerolatency"}
        except Exception as exc:
            container.close()
            raise RuntimeError(
                "Cannot initialize the H.264 libx264 encoder. "
                "Check your PyAV/FFmpeg installation."
            ) from exc
        self._container = container
        self._stream = stream

    def write(self, image: Image.Image) -> None:
        """Encode one PIL frame. Call at a fixed rate to maintain video timing."""
        if self._closed:
            raise RuntimeError("Cannot encode after closing the file.")
        if self._stream is None:
            self._start(image)

        assert self._stream is not None and self._container is not None
        # Preserve source aspect ratio, keeping stream dimensions constant even
        # if the monitor resolution changes during recording.
        content = ImageOps.contain(
            image.convert("RGB"),
            (self.width, self.height),
            method=Image.Resampling.BILINEAR,
        )
        canvas = Image.new("RGB", (self.width, self.height), "black")
        canvas.paste(
            content,
            ((self.width - content.width) // 2, (self.height - content.height) // 2),
        )

        import av

        frame = av.VideoFrame.from_image(canvas).reformat(format="yuv420p")
        frame.pts = self.frames_written
        frame.time_base = Fraction(1, self.fps)
        for packet in self._stream.encode(frame):
            self._container.mux(packet)
        self.frames_written += 1

    def close(self) -> None:
        """Flush delayed packets and finalize the MP4 file (idempotent)."""
        if self._closed:
            return
        self._closed = True
        if self._container is None:
            return
        try:
            if self._stream is not None:
                for packet in self._stream.encode():
                    self._container.mux(packet)
        finally:
            self._container.close()
            self._container = None
            self._stream = None
