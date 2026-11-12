import asyncio
import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn
from aiortc import RTCConfiguration
from PIL import Image
from typer.testing import CliRunner

from rdesktop.capture.screen import CapturedFrame, ScreenCapture
from rdesktop.config import CaptureSettings
from rdesktop.main import app
from rdesktop.network.capture import ScreenVideoTrack
from rdesktop.network.options import ice_configuration, request_approval, validate_url
from rdesktop.network.peer import PeerSession
from rdesktop.server.app import create_app

TOKEN = "local-streaming-test-token-32-chars"


class FakeCapture(ScreenCapture):
    def __init__(self, settings: CaptureSettings):
        super().__init__(settings)
        self.threads: list[int] = []
        self.frames = 0
        self.closed = False

    def start(self) -> None:
        self.threads.append(threading.get_ident())

    def monitors(self) -> list[dict[str, int]]:
        return [{}, {}]

    def capture(self, monitor_index: int = 1) -> CapturedFrame:
        self.threads.append(threading.get_ident())
        self.frames += 1
        return CapturedFrame(
            Image.new("RGB", (321, 181), "red"), time.monotonic(), 321, 181
        )

    def close(self) -> None:
        self.threads.append(threading.get_ident())
        self.closed = True


@pytest.fixture
def signaling_server() -> Iterator[str]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(TOKEN),
                log_level="error",
                ws="websockets-sansio",
            )
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started:
                if time.monotonic() > deadline or not thread.is_alive():
                    pytest.fail("Test signaling server did not start")
                time.sleep(0.01)
            yield f"ws://127.0.0.1:{port}/ws"
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            assert not thread.is_alive()


def test_screen_track_pacing_dimensions_and_thread_affinity():
    capture = FakeCapture(CaptureSettings())

    async def run():
        track = ScreenVideoTrack(capture_factory=lambda settings: capture)
        try:
            frames = [await track.recv() for _ in range(3)]
            assert [(frame.width, frame.height) for frame in frames] == [(320, 180)] * 3
            assert all(frame.time_base is not None for frame in frames)
            timestamps = [frame.pts for frame in frames]
            assert all(value is not None for value in timestamps)
            assert len(set(timestamps)) == 3
            assert timestamps == sorted(
                value for value in timestamps if value is not None
            )
            assert frames[-1].time - frames[0].time >= 2 / 15 - 0.01
        finally:
            await track.aclose()
        assert capture.closed
        assert len(set(capture.threads)) == 1
        assert capture.threads[0] != threading.get_ident()

    asyncio.run(run())


@pytest.mark.parametrize("approved", [True, False])
def test_real_webrtc_stream_requires_approval_and_cleans_up(signaling_server, approved):
    async def run():
        loop = asyncio.get_running_loop()
        credentials = loop.create_future()
        requested = asyncio.Event()
        decision = asyncio.Event()
        received = asyncio.Event()
        captures: list[FakeCapture] = []
        frames = []

        async def approve(name):
            assert name == "Test viewer"
            requested.set()
            await decision.wait()
            return approved

        def capture_factory(settings):
            capture = FakeCapture(settings)
            captures.append(capture)
            return capture

        def frame_received(frame):
            frames.append(frame)
            if len(frames) >= 3:
                received.set()

        host = PeerSession(
            host=True,
            server=signaling_server,
            token=TOKEN,
            configuration=RTCConfiguration(iceServers=[]),
            approve=approve,
            report=lambda message: None,
            on_created=lambda session, token: credentials.set_result((session, token)),
            track_factory=lambda: ScreenVideoTrack(capture_factory=capture_factory),
        )
        host_task = asyncio.create_task(host.run())
        viewer = None
        viewer_task = None
        try:
            session_id, token = await asyncio.wait_for(credentials, 5)
            viewer = PeerSession(
                host=False,
                server=signaling_server,
                token=TOKEN,
                configuration=RTCConfiguration(iceServers=[]),
                session_id=session_id,
                join_token=token,
                name="Test viewer",
                on_frame=frame_received,
                report=lambda message: None,
            )
            viewer_task = asyncio.create_task(viewer.run())
            await asyncio.wait_for(requested.wait(), 5)
            await asyncio.sleep(0.1)
            assert not captures
            assert not frames
            decision.set()
            if approved:
                await asyncio.wait_for(received.wait(), 15)
                assert host.connected.is_set()
                assert viewer.connected.is_set()
                assert frames[0].width == 320 and frames[0].height == 180
                red, green, blue = frames[0].to_image().getpixel((100, 100))
                assert red > 200 and green < 30 and blue < 30
                viewer.stopped.set()
            await asyncio.wait_for(asyncio.gather(host_task, viewer_task), 5)
            if approved:
                assert captures[0].closed
                assert len(set(captures[0].threads)) == 1
            else:
                assert not captures
                assert not frames
        finally:
            host.stopped.set()
            if viewer is not None:
                viewer.stopped.set()
            for task in (host_task, viewer_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (host_task, viewer_task) if task is not None),
                return_exceptions=True,
            )

    asyncio.run(run())


def test_invalid_join_fails_without_capture(signaling_server):
    async def run():
        viewer = PeerSession(
            host=False,
            server=signaling_server,
            token=TOKEN,
            configuration=RTCConfiguration(iceServers=[]),
            session_id="missing",
            join_token="wrong",
            report=lambda message: None,
        )
        with pytest.raises(RuntimeError, match="rejected"):
            await asyncio.wait_for(viewer.run(), 5)

    asyncio.run(run())


def test_approval_defaults_to_no_and_handles_eof(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert asyncio.run(request_approval("viewer")) is False

    def eof(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert asyncio.run(request_approval("viewer")) is False
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert asyncio.run(request_approval("viewer")) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://host/ws",
        "ws://host/",
        "ws://user:secret@host/ws",
        "ws://host/ws?token=secret",
    ],
)
def test_rejects_invalid_signaling_urls(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_ice_configuration(monkeypatch):
    for key in (
        "RDC_STUN_URL",
        "RDC_TURN_URL",
        "RDC_TURN_USERNAME",
        "RDC_TURN_PASSWORD",
    ):
        monkeypatch.delenv(key, raising=False)
    assert ice_configuration().iceServers == []
    monkeypatch.setenv("RDC_TURN_URL", "turn:localhost:3478")
    with pytest.raises(ValueError, match="USERNAME"):
        ice_configuration()
    monkeypatch.setenv("RDC_TURN_USERNAME", "test-user")
    monkeypatch.setenv("RDC_TURN_PASSWORD", "test-password")
    servers = ice_configuration().iceServers
    assert servers is not None
    assert servers[0].username == "test-user"


def test_host_cli_requires_token_and_valid_fps(monkeypatch):
    monkeypatch.delenv("RDC_SIGNALING_TOKEN", raising=False)
    runner = CliRunner()
    result = runner.invoke(app, ["host"])
    assert result.exit_code == 1
    assert "RDC_SIGNALING_TOKEN" in result.output
    assert runner.invoke(app, ["host", "--fps", "16"]).exit_code == 2


def test_capture_failure_and_invalid_monitor_release_resources():
    class BrokenCapture(FakeCapture):
        def capture(self, monitor_index: int = 1) -> CapturedFrame:
            raise OSError("display unavailable")

    async def run():
        capture = BrokenCapture(CaptureSettings())
        track = ScreenVideoTrack(capture_factory=lambda settings: capture)
        try:
            with pytest.raises(OSError, match="display unavailable"):
                await track.recv()
            assert track.failed.is_set()
            assert track.readyState == "ended"
        finally:
            await track.aclose()
        assert capture.closed

        capture = FakeCapture(CaptureSettings())
        track = ScreenVideoTrack(monitor=9, capture_factory=lambda settings: capture)
        try:
            with pytest.raises(ValueError, match="unavailable"):
                await track.start()
        finally:
            await track.aclose()
        assert capture.closed

    asyncio.run(run())


def test_disconnect_while_host_approval_is_pending(signaling_server):
    async def run():
        credentials = asyncio.get_running_loop().create_future()
        requested = asyncio.Event()
        cancelled = asyncio.Event()

        async def approve(name):
            requested.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            return False

        host = PeerSession(
            host=True,
            server=signaling_server,
            token=TOKEN,
            configuration=RTCConfiguration(iceServers=[]),
            approve=approve,
            report=lambda message: None,
            on_created=lambda session, token: credentials.set_result((session, token)),
        )
        host_task = asyncio.create_task(host.run())
        viewer_task = None
        try:
            session_id, token = await asyncio.wait_for(credentials, 5)
            viewer = PeerSession(
                host=False,
                server=signaling_server,
                token=TOKEN,
                configuration=RTCConfiguration(iceServers=[]),
                session_id=session_id,
                join_token=token,
                report=lambda message: None,
            )
            viewer_task = asyncio.create_task(viewer.run())
            await asyncio.wait_for(requested.wait(), 5)
            viewer.stopped.set()
            await asyncio.wait_for(asyncio.gather(host_task, viewer_task), 5)
            assert cancelled.is_set()
            assert not host.connected.is_set()
        finally:
            for task in (host_task, viewer_task):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(task for task in (host_task, viewer_task) if task is not None),
                return_exceptions=True,
            )

    asyncio.run(run())


def test_live_viewer_displays_latest_frame_and_closes(monkeypatch):
    import sys
    from types import SimpleNamespace

    from av import VideoFrame
    from PIL import ImageTk

    from rdesktop.viewer.live import LiveViewer

    rendered = []

    class Root:
        def __init__(self):
            self.updates = 0
            self.destroyed = False
            self.close_callback = None

        def title(self, text):
            pass

        def geometry(self, text):
            pass

        def minsize(self, *args):
            pass

        def protocol(self, name, callback):
            self.close_callback = callback

        def bind(self, *args):
            pass

        def update(self):
            self.updates += 1
            if self.updates == 3:
                assert self.close_callback is not None
                self.close_callback()

        def destroy(self):
            self.destroyed = True

    root = Root()

    class Label:
        def __init__(self, *args, **kwargs):
            pass

        def pack(self, **kwargs):
            pass

        def winfo_width(self):
            return 160 if root.updates == 1 else 320

        def winfo_height(self):
            return 90 if root.updates == 1 else 180

        def configure(self, image):
            rendered.append(image.copy())

    monkeypatch.setitem(
        sys.modules,
        "tkinter",
        SimpleNamespace(Tk=lambda: root, Label=Label, TclError=RuntimeError),
    )
    monkeypatch.setattr(ImageTk, "PhotoImage", lambda image, **kwargs: image)
    viewer = LiveViewer()
    viewer.show(VideoFrame.from_image(Image.new("RGB", (640, 360), "red")))
    viewer.show(VideoFrame.from_image(Image.new("RGB", (640, 360), "blue")))
    asyncio.run(viewer.run(asyncio.Event()))
    assert [image.size for image in rendered] == [(160, 90), (320, 180)]
    assert all(image.getpixel((0, 0)) == (0, 0, 255) for image in rendered)
    assert root.destroyed
