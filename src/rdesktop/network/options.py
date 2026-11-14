"""Client configuration and cancellable terminal approval."""

import asyncio
import os
import threading
from urllib.parse import urlsplit

from aiortc import RTCConfiguration, RTCIceServer


def signaling_token() -> str:
    token = os.environ.get("RDC_SIGNALING_TOKEN", "")
    if len(token) < 32:
        raise ValueError(
            "Set RDC_SIGNALING_TOKEN to the same token used by the server."
        )
    return token


def validate_url(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ("ws", "wss")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/ws"
    ):
        raise ValueError(
            "Use a ws:// or wss:// server URL ending in /ws, without credentials or query parameters."
        )
    return url


def ice_configuration() -> RTCConfiguration:
    """No third-party ICE servers by default; configure them explicitly."""
    servers = []
    stun = os.environ.get("RDC_STUN_URL")
    turn = os.environ.get("RDC_TURN_URL")
    if stun:
        if not stun.startswith("stun:"):
            raise ValueError("RDC_STUN_URL must start with stun:.")
        servers.append(RTCIceServer(urls=stun))
    if turn:
        if not turn.startswith(("turn:", "turns:")):
            raise ValueError("RDC_TURN_URL must start with turn: or turns:.")
        username = os.environ.get("RDC_TURN_USERNAME")
        credential = os.environ.get("RDC_TURN_PASSWORD")
        if not username or not credential:
            raise ValueError("Set RDC_TURN_USERNAME and RDC_TURN_PASSWORD for TURN.")
        servers.append(
            RTCIceServer(urls=turn, username=username, credential=credential)
        )
    return RTCConfiguration(iceServers=servers)


async def request_approval(name: str, *, control: bool = False) -> bool:
    """Do not let a blocked terminal read prevent network shutdown."""
    loop = asyncio.get_running_loop()
    answer: asyncio.Future[bool] = loop.create_future()
    safe_name = "".join(c for c in name if c.isprintable())[:80]

    def deliver(value: bool) -> None:
        if not answer.done():
            answer.set_result(value)

    access = (
        "see your screen and CONTROL your keyboard/mouse"
        if control
        else "see your screen"
    )

    def read() -> None:
        try:
            approved = (
                input(f"Allow viewer {safe_name!r} to {access}? [y/N]: ")
                .strip()
                .lower()
                == "y"
            )
        except (EOFError, OSError):
            approved = False
        try:
            loop.call_soon_threadsafe(deliver, approved)
        except RuntimeError:
            pass  # The session has already closed.

    threading.Thread(target=read, daemon=True, name="rdc-approval").start()
    try:
        return await asyncio.wait_for(answer, timeout=120)
    except TimeoutError:
        return False
