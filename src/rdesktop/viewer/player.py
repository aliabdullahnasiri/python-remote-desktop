"""Timestamp-paced local playback with a Tk window and PyAV decoder."""

from __future__ import annotations

import time
from pathlib import Path


def play_video(path: Path) -> int:
    """Play the first video stream; return the number of displayed frames."""
    import av

    try:
        import tkinter as tk

        from PIL import Image, ImageTk
    except ImportError as exc:
        raise RuntimeError(
            "Tkinter is required for playback. Install your OS Tkinter package "
            "(tk on Arch, python3-tk on Debian/Ubuntu), or Python's "
            "Tcl/Tk component on Windows."
        ) from exc

    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError("The file contains no video stream.")
        stream = container.streams.video[0]
        frames = iter(container.decode(stream))
        first = next(frames, None)
        if first is None:
            raise ValueError("The video contains no decodable frames.")
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            raise RuntimeError(
                "Cannot open the viewer window. A graphical desktop is required."
            ) from exc

        root.title(f"rdc — {path.name} | Space: pause | Esc: close")
        root.geometry(f"{min(first.width, 1280)}x{min(first.height, 720)}")
        root.minsize(160, 90)
        label = tk.Label(root, bg="black")
        label.pack(fill="both", expand=True)
        pending = first
        origin = float(first.time or 0)
        interval = 1 / float(stream.average_rate or 15)
        started = time.perf_counter()
        paused_at = None
        displayed = 0
        index = 0
        errors = []
        current_image = None
        photo: ImageTk.PhotoImage | None = None

        def render(event=None):
            nonlocal photo
            if current_image is None:
                return
            image = current_image.copy()
            image.thumbnail(
                (max(label.winfo_width(), 1), max(label.winfo_height(), 1)),
                Image.Resampling.BILINEAR,
            )
            # Keep a Python reference for as long as the frame is displayed.
            photo = ImageTk.PhotoImage(image, master=root)
            label.configure(image=photo)

        def pause(event=None):
            nonlocal paused_at, started
            now = time.perf_counter()
            if paused_at is None:
                paused_at = now
                root.title(f"rdc — {path.name} | Paused | Space: resume")
            else:
                started += now - paused_at
                paused_at = None
                root.title(f"rdc — {path.name} | Space: pause | Esc: close")

        def tick():
            nonlocal pending, displayed, index, current_image
            try:
                if paused_at is not None:
                    root.after(20, tick)
                    return
                timestamp = (
                    float(pending.time) - origin
                    if pending.time is not None
                    else index * interval
                )
                remaining = started + max(0, timestamp) - time.perf_counter()
                if remaining > 0:
                    root.after(max(1, min(20, round(remaining * 1000))), tick)
                    return
                current_image = pending.to_image()
                render()
                displayed += 1
                index += 1
                pending = next(frames, None)
                if pending is None:
                    # Leave the last frame visible until the viewer is closed.
                    root.title(f"rdc — {path.name} | Finished | Esc: close")
                    root.unbind("<space>")
                    return
                root.after(1, tick)
            except Exception as exc:  # noqa: BLE001
                # Transfer callback failures to the caller after closing the window.
                errors.append(exc)
                root.quit()

        root.bind("<space>", pause)
        root.bind("<Escape>", lambda event: root.quit())
        label.bind("<Configure>", render)
        root.protocol("WM_DELETE_WINDOW", root.quit)
        root.after(1, tick)
        try:
            root.mainloop()
        finally:
            root.destroy()
        if errors:
            raise RuntimeError(f"Video playback failed: {errors[0]}") from errors[0]
        return displayed
