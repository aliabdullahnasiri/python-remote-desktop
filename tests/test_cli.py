from types import SimpleNamespace

from PIL import Image
from typer.testing import CliRunner

from rdesktop import main


def test_capture_saves_numbered_frames_in_separate_sessions(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    class FakeCapture:
        def __init__(self, settings):
            self.frames = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def capture(self, monitor):
            self.frames += 1
            if self.frames > 2:
                raise KeyboardInterrupt
            return SimpleNamespace(image=Image.new('RGB', (2, 2)))

    monkeypatch.setattr(main, 'ScreenCapture', FakeCapture)
    monkeypatch.setattr(main.time, 'sleep', lambda _: None)
    runner = CliRunner()
    for _ in range(2):
        result = runner.invoke(main.app, ['capture'])
        assert result.exit_code == 0, result.output
    sessions = list((tmp_path / 'screenshots').iterdir())
    assert len(sessions) == 2
    for session in sessions:
        assert sorted(p.name for p in session.iterdir()) == [
            'frame_000001.jpg', 'frame_000002.jpg'
        ]
        with Image.open(session / 'frame_000001.jpg') as image:
            assert image.format == 'JPEG'
