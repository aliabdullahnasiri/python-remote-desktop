"""Small, bounded input protocol; native imports occur only after approval."""

import os
from collections.abc import Callable
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

KEYS = {
    "BackSpace": "backspace",
    "Tab": "tab",
    "Return": "enter",
    "Escape": "esc",
    "Delete": "delete",
    "Insert": "insert",
    "Home": "home",
    "End": "end",
    "Prior": "page_up",
    "Next": "page_down",
    "Left": "left",
    "Right": "right",
    "Up": "up",
    "Down": "down",
    "Shift_L": "shift_l",
    "Shift_R": "shift_r",
    "Control_L": "ctrl_l",
    "Control_R": "ctrl_r",
    "Alt_L": "alt_l",
    "Alt_R": "alt_r",
    "Super_L": "cmd",
    "Super_R": "cmd_r",
    "Caps_Lock": "caps_lock",
    **{f"F{i}": f"f{i}" for i in range(1, 13)},
}


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Move(Event):
    type: Literal["move"]
    x: float = Field(ge=0, le=1, allow_inf_nan=False)
    y: float = Field(ge=0, le=1, allow_inf_nan=False)


class Button(Event):
    type: Literal["button"]
    button: Literal["left", "middle", "right"]
    down: bool


class Key(Event):
    type: Literal["key"]
    key: str = Field(min_length=1, max_length=20)
    down: bool


class Scroll(Event):
    type: Literal["scroll"]
    dy: int = Field(ge=-10, le=10)


class Reset(Event):
    type: Literal["reset", "heartbeat"]


ADAPTER = TypeAdapter(
    Annotated[Move | Button | Key | Scroll | Reset, Field(discriminator="type")]
)


def decode(raw: object):
    if not isinstance(raw, str) or len(raw) > 512:
        raise ValueError("Invalid input message")
    event = ADAPTER.validate_json(raw)
    if isinstance(event, Key) and not (
        event.key in KEYS.values() or len(event.key) == 1 and event.key.isprintable()
    ):
        raise ValueError("Unsupported key")
    return event


class InputBackend(Protocol):
    def key(self, name: str, down: bool) -> None: ...
    def button(self, name: str, down: bool) -> None: ...
    def move(self, x: int, y: int) -> None: ...
    def scroll(self, dy: int) -> None: ...


class NativeInput:
    def __init__(self):
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            raise RuntimeError(
                "Remote input requires an X11 session; native Wayland is unsupported."
            )
        try:
            from pynput import keyboard, mouse

            self.keyboard = keyboard.Controller()
            self.mouse = mouse.Controller()
            self.key_enum = keyboard.Key
            self.button_enum = mouse.Button
        except Exception as exc:
            raise RuntimeError(
                "Cannot initialize remote input. Check desktop/accessibility permissions."
            ) from exc

    def key(self, name, down):
        key = name if len(name) == 1 else getattr(self.key_enum, name)
        (self.keyboard.press if down else self.keyboard.release)(key)

    def button(self, name, down):
        (self.mouse.press if down else self.mouse.release)(
            getattr(self.button_enum, name)
        )

    def move(self, x, y):
        self.mouse.position = (x, y)

    def scroll(self, dy):
        self.mouse.scroll(0, dy)


class InputReceiver:
    def __init__(self, backend: InputBackend, bounds: dict[str, int]):
        self.backend = backend
        self.bounds = bounds
        self.keys: set[str] = set()
        self.buttons: set[str] = set()

    def receive(self, raw):
        event = decode(raw)
        if isinstance(event, Move):
            b = self.bounds
            self.backend.move(
                b["left"] + round(event.x * (b["width"] - 1)),
                b["top"] + round(event.y * (b["height"] - 1)),
            )
        elif isinstance(event, (Key, Button)):
            name = event.key if isinstance(event, Key) else event.button
            held = self.keys if isinstance(event, Key) else self.buttons
            action = self.backend.key if isinstance(event, Key) else self.backend.button
            if event.down:
                if name not in held:
                    if len(held) >= 128:
                        raise ValueError("Too many held inputs")
                    held.add(name)
                    action(name, True)
            elif name in held:
                action(name, False)
                held.remove(name)
        elif isinstance(event, Scroll):
            self.backend.scroll(event.dy)
        elif event.type == "reset":
            self.release()

    def release(self):
        # Try every release even if one OS call fails.
        error = None
        for held, action in (
            (self.keys, self.backend.key),
            (self.buttons, self.backend.button),
        ):
            for name in list(held):
                try:
                    action(name, False)
                    held.remove(name)
                except Exception as exc:  # noqa: BLE001
                    # Release remaining inputs before reporting backend errors.
                    error = exc
        if error:
            raise error


class ViewerInput:
    def __init__(self, send: Callable[[dict], bool]):
        self.send = send
        self.size: tuple[int, int] = (0, 0)
        self.window: tuple[int, int] = (0, 0)
        self.keys: dict[int, str] = {}

    def reset(self, event=None):
        self.keys.clear()
        self.send({"type": "reset"})

    def move(self, event):
        w, h = self.size
        ww, wh = self.window
        x, y = event.x - (ww - w) / 2, event.y - (wh - h) / 2
        if w < 2 or h < 2 or not (0 <= x < w and 0 <= y < h):
            return False
        return self.send(
            {"type": "move", "x": min(1.0, x / (w - 1)), "y": min(1.0, y / (h - 1))}
        )

    def button(self, event, down):
        names = {1: "left", 2: "middle", 3: "right"}
        if event.num in names and (not down or self.move(event)):
            self.send({"type": "button", "button": names[event.num], "down": down})

    def key(self, event, down):
        if event.keysym == "Escape":
            return
        if down:
            key = KEYS.get(event.keysym)
            if key is None:
                # keysyms retain letters during Ctrl combinations (char becomes a control code).
                key = event.keysym if len(event.keysym) == 1 else event.char
            if not key or (len(key) == 1 and not key.isprintable()):
                return
            key = self.keys.setdefault(event.keycode, key)
        else:
            key = self.keys.pop(event.keycode, None)
        if key:
            self.send({"type": "key", "key": key, "down": down})
        return "break"

    def scroll(self, event):
        if self.move(event):
            delta = (
                1
                if getattr(event, "num", None) == 4
                else (
                    -1
                    if getattr(event, "num", None) == 5
                    else (1 if event.delta > 0 else -1)
                )
            )
            self.send({"type": "scroll", "dy": delta})
