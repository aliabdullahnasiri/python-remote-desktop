import sys
from types import SimpleNamespace

import pytest
from PIL import Image
from typer.testing import CliRunner

from rdesktop import main
from rdesktop.codec.video_encoder import H264Encoder
from rdesktop.viewer import player


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "sample.mp4"
    with H264Encoder(path, fps=15) as encoder:
        for color in ("red", "green", "blue"):
            encoder.write(Image.new("RGB", (64, 48), color))
    return path


def test_playback_decodes_and_schedules_frames(monkeypatch, video):
    clock = [0.0]
    rendered = []

    class Root:
        def __init__(self):
            self.queue = []
            self.destroyed = False
            self.titles = []

        def title(self, value):
            self.titles.append(value)

        def geometry(self, value):
            pass

        def minsize(self, *args):
            pass

        def bind(self, *args):
            pass

        def unbind(self, *args):
            pass

        def protocol(self, *args):
            pass

        def after(self, delay, callback):
            self.queue.append((delay, callback))

        def quit(self):
            self.queue.clear()

        def destroy(self):
            self.destroyed = True

        def mainloop(self):
            while self.queue:
                delay, callback = self.queue.pop(0)
                clock[0] += delay / 1000
                callback()

    class Label:
        def __init__(self, *args, **kwargs):
            pass

        def pack(self, **kwargs):
            pass

        def bind(self, *args):
            pass

        def winfo_width(self):
            return 320

        def winfo_height(self):
            return 240

        def configure(self, image):
            rendered.append(image)

    root = Root()
    monkeypatch.setitem(
        sys.modules,
        "tkinter",
        SimpleNamespace(Tk=lambda: root, Label=Label, TclError=RuntimeError),
    )
    from PIL import ImageTk

    monkeypatch.setattr(ImageTk, "PhotoImage", lambda image, **kwargs: image.copy())
    monkeypatch.setattr(player.time, "perf_counter", lambda: clock[0])
    assert player.play_video(video) == 3
    assert len(rendered) == 3
    assert rendered[0].size == (64, 48)
    assert clock[0] >= 2 / 15
    assert root.destroyed
    assert "Finished" in root.titles[-1]


def test_view_command_success(monkeypatch, video):
    seen = []
    monkeypatch.setattr(player, "play_video", lambda path: seen.append(path) or 3)
    result = CliRunner().invoke(main.app, ["view", str(video)])
    assert result.exit_code == 0
    assert "Displayed 3 frames" in result.output
    assert seen == [video]


def test_view_command_errors(monkeypatch, tmp_path):
    runner = CliRunner()
    assert (
        runner.invoke(main.app, ["view", str(tmp_path / "missing.mp4")]).exit_code == 2
    )
    assert runner.invoke(main.app, ["view", str(tmp_path)]).exit_code == 2
    broken = tmp_path / "broken.mp4"
    broken.write_text("invalid video")

    def fail(path):
        raise ValueError("Invalid video")

    monkeypatch.setattr(player, "play_video", fail)
    result = runner.invoke(main.app, ["view", str(broken)])
    assert result.exit_code == 1
    assert "Playback failed" in result.output
