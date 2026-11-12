"""Single-process session state. Each session serializes its own operations."""

import asyncio
import secrets
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect

from .protocol import Answer, Approve, Ice, Leave, Offer, Ping


async def send(socket: WebSocket, message: dict) -> None:
    await asyncio.wait_for(socket.send_json(message), timeout=5)


async def close(socket: WebSocket) -> None:
    try:
        await asyncio.wait_for(socket.close(), timeout=5)
    except (WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
        pass


@dataclass
class Session:
    host: WebSocket
    id: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    viewer: WebSocket | None = None
    state: str = "waiting"
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def relay(self, socket: WebSocket, message) -> None:
        async with self.lock:
            if self.state == "closed":
                return
            if isinstance(message, Ping):
                await send(socket, {"type": "pong"})
                return
            is_host = socket is self.host
            if isinstance(message, Leave):
                await self._end("peer_left")
                return
            viewer = self.viewer
            if viewer is None:
                raise ValueError("A viewer must join before approval or signaling.")
            if isinstance(message, Approve):
                if not is_host or self.state != "pending":
                    raise ValueError("Only the host can approve a pending viewer.")
                if not message.approved:
                    await self._end("rejected")
                    return
                self.state = "approved"
                await send(self.host, {"type": "approved"})
                await send(viewer, {"type": "approved"})
                return
            if self.state not in ("approved", "offered", "connected"):
                raise ValueError("Host approval is required before signaling.")
            peer = viewer if is_host else self.host
            if isinstance(message, Offer):
                if not is_host or self.state != "approved":
                    raise ValueError("The host must send the initial offer once.")
                self.state = "offered"
            elif isinstance(message, Answer):
                if is_host or self.state != "offered":
                    raise ValueError("The viewer must answer the host offer once.")
                self.state = "connected"
            elif not isinstance(message, Ice):
                raise TypeError("Unexpected message for an established session.")
            await send(peer, message.model_dump())

    async def _end(self, reason: str) -> None:
        self.state = "closed"
        for socket in (self.host, self.viewer):
            if socket is not None:
                try:
                    await send(socket, {"type": "session_closed", "reason": reason})
                except (WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
                    pass
                await close(socket)

    async def end(self, reason: str) -> None:
        async with self.lock:
            if self.state != "closed":
                await self._end(reason)
