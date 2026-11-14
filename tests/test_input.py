import json
from types import SimpleNamespace

import pytest

from rdesktop.control.input import InputReceiver, ViewerInput, decode


class Backend:
    def __init__(self):
        self.events = []

    def key(self, *args):
        self.events.append(("key", *args))

    def button(self, *args):
        self.events.append(("button", *args))

    def move(self, *args):
        self.events.append(("move", *args))

    def scroll(self, *args):
        self.events.append(("scroll", *args))


def test_monitor_offsets_pressed_state_and_release():
    backend = Backend()
    receiver = InputReceiver(
        backend, {"left": -1920, "top": 100, "width": 1920, "height": 1080}
    )
    for event in [
        {"type": "move", "x": 1.0, "y": 0.0},
        {"type": "key", "key": "ctrl_l", "down": True},
        {"type": "key", "key": "ctrl_l", "down": True},
        {"type": "button", "button": "left", "down": True},
        {"type": "scroll", "dy": -2},
    ]:
        receiver.receive(json.dumps(event))
    receiver.release()
    receiver.release()
    assert backend.events == [
        ("move", -1, 100),
        ("key", "ctrl_l", True),
        ("button", "left", True),
        ("scroll", -2),
        ("key", "ctrl_l", False),
        ("button", "left", False),
    ]


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        "x" * 513,
        "[]",
        "null",
        "{}",
        '{"type":"move","x":NaN,"y":0}',
        '{"type":"move","x":2,"y":0}',
        '{"type":"key","key":"bad key","down":true}',
        '{"type":"key","key":"a","down":1}',
        '{"type":"scroll","dy":100}',
        '{"type":"reset","extra":1}',
    ],
)
def test_invalid_input(raw):
    with pytest.raises(ValueError):
        decode(raw)


def test_viewer_letterbox_and_ctrl_key_release_identity():
    events = []
    controls = ViewerInput(lambda event: events.append(event) or True)
    controls.size = (640, 360)
    controls.window = (640, 480)
    assert not controls.move(SimpleNamespace(x=20, y=10))
    assert controls.move(SimpleNamespace(x=639, y=419))
    assert events[-1] == {"type": "move", "x": 1.0, "y": 1.0}
    controls.window = (641, 481)
    assert controls.move(SimpleNamespace(x=640, y=420))
    assert events[-1] == {"type": "move", "x": 1.0, "y": 1.0}
    controls.key(SimpleNamespace(keysym="c", char="\x03", keycode=54), True)
    controls.key(SimpleNamespace(keysym="C", char="C", keycode=54), False)
    assert events[-2:] == [
        {"type": "key", "key": "c", "down": True},
        {"type": "key", "key": "c", "down": False},
    ]
    controls.reset()
    assert events[-1] == {"type": "reset"}


def test_release_attempts_all_inputs_after_backend_failure():
    backend = Backend()
    receiver = InputReceiver(backend, {})
    receiver.keys.add("a")
    receiver.buttons.add("left")

    def fail(*args):
        raise RuntimeError("failed")

    backend.key = fail
    with pytest.raises(RuntimeError):
        receiver.release()
    assert backend.events == [("button", "left", False)]


def test_input_channel_failure_releases_pressed_keys():
    import asyncio

    from aiortc import RTCConfiguration

    from rdesktop.network.peer import PeerSession

    class Channel:
        label = "rdc-input-v1"
        ordered = True
        maxRetransmits = None
        maxPacketLifeTime = None

        def __init__(self):
            self.handlers = {}

        def on(self, name):
            def register(fn):
                self.handlers[name] = fn
                return fn

            return register

    async def run():
        host = PeerSession(
            host=True,
            server="ws://localhost:8000/ws",
            token="x" * 32,
            configuration=RTCConfiguration(iceServers=[]),
            allow_control=True,
        )
        backend = Backend()
        host._input = InputReceiver(backend, {})
        channel = Channel()
        host._attach_input(channel)
        channel.handlers["message"]('{"type":"key","key":"shift_l","down":true}')
        assert backend.events == [("key", "shift_l", True)]
        channel.handlers["message"]('{"type":"key","key":"oops","down":true}')
        assert host.stopped.is_set()
        channel.handlers["close"]()
        assert backend.events[-1] == ("key", "shift_l", False)

    asyncio.run(run())


def test_input_watchdog_releases_stale_keys(monkeypatch):
    import asyncio

    from aiortc import RTCConfiguration

    from rdesktop.network.peer import PeerSession

    async def run():
        host = PeerSession(
            host=True,
            server="ws://localhost:8000/ws",
            token="x" * 32,
            configuration=RTCConfiguration(iceServers=[]),
        )
        backend = Backend()
        host._input = InputReceiver(backend, {})
        host._input.receive('{"type":"key","key":"a","down":true}')
        host._last_input = asyncio.get_running_loop().time() - 4
        ticks = 0

        async def tick(seconds):
            nonlocal ticks
            ticks += 1
            if ticks > 1:
                raise asyncio.CancelledError

        monkeypatch.setattr(asyncio, "sleep", tick)
        with pytest.raises(asyncio.CancelledError):
            await host._input_watchdog()
        assert backend.events == [("key", "a", True), ("key", "a", False)]

    asyncio.run(run())


def test_control_approval_prompt_is_explicit(monkeypatch):
    import asyncio

    from rdesktop.network.options import request_approval

    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "y")
    assert asyncio.run(request_approval("Alice", control=True))
    assert "CONTROL your keyboard/mouse" in prompts[0]
