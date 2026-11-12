"""Live video window, updated on the asyncio/main thread without frame queues."""

import asyncio
from typing import Any

from av import VideoFrame


class LiveViewer:
    def __init__(self) -> None:
        self._latest: VideoFrame | None = None

    def show(self, frame: VideoFrame) -> None:
        # Retain only the newest frame if rendering falls behind.
        self._latest = frame

    async def run(self, stopped: asyncio.Event) -> None:
        try:
            import tkinter as tk

            from PIL import Image, ImageTk
        except ImportError as exc:
            raise RuntimeError(
                "Live viewing requires Tkinter. Install tk on Arch, python3-tk on "
                "Debian/Ubuntu, or the Python Tcl/Tk component on Windows."
            ) from exc
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            raise RuntimeError(
                "Cannot open the viewer. A graphical desktop is required."
            ) from exc
        root.title("rdc — Waiting for approved stream | Escape: disconnect")
        root.geometry("960x540")
        root.minsize(320, 180)
        label = tk.Label(root, bg="black")
        label.pack(fill="both", expand=True)
        root.protocol("WM_DELETE_WINDOW", stopped.set)
        root.bind("<Escape>", lambda event: stopped.set())
        image: Any = None
        photo: Any = None
        previous_size = (0, 0)
        try:
            while not stopped.is_set():
                root.update()
                if stopped.is_set():
                    break
                size = (max(1, label.winfo_width()), max(1, label.winfo_height()))
                changed = self._latest is not None
                if self._latest is not None:
                    image = self._latest.to_image()
                    self._latest = None
                    root.title("rdc — LIVE screen view | Escape: disconnect")
                if image is not None and (changed or size != previous_size):
                    fitted = image.copy()
                    fitted.thumbnail(size, Image.Resampling.BILINEAR)
                    photo = ImageTk.PhotoImage(fitted, master=root)
                    label.configure(image=photo)
                    previous_size = size
                await asyncio.sleep(1 / 60)
        finally:
            root.destroy()
            self._latest = None
