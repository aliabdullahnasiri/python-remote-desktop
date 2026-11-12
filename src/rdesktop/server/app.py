"""FastAPI signaling endpoint; run one worker because sessions live in memory."""

import asyncio
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from .protocol import Create, Join, messages
from .sessions import Session, close, send

MAX_MESSAGE_BYTES = 65536


def create_app(token: str | None = None) -> FastAPI:
    sessions: dict[str, Session] = {}

    @asynccontextmanager
    async def lifespan(app):
        configured = (
            token if token is not None else os.environ.get("RDC_SIGNALING_TOKEN", "")
        )
        if len(configured) < 32:
            raise RuntimeError(
                "Set RDC_SIGNALING_TOKEN to a random token of at least 32 characters."
            )
        app.state.token = configured
        yield
        for session in list(sessions.values()):
            await session.end("server_shutdown")
        sessions.clear()

    app = FastAPI(title="Remote Desktop Signaling", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    async def receive(socket: WebSocket):
        packet = await socket.receive()
        if packet["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(packet.get("code", 1000))
        text = packet.get("text")
        if text is None or len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError("Expected a JSON text message of at most 65536 bytes.")
        return messages.validate_json(text)

    @app.websocket("/ws")
    async def signaling(socket: WebSocket):
        expected = f"Bearer {app.state.token}".encode()
        provided = socket.headers.get("authorization", "").encode()
        # This CLI protocol does not accept browser-originated connections.
        if socket.headers.get("origin") or not secrets.compare_digest(
            provided, expected
        ):
            await socket.close(code=1008)
            return
        await socket.accept()
        session = None
        try:
            first = await asyncio.wait_for(receive(socket), timeout=10)
            if isinstance(first, Create):
                if len(sessions) >= 100:
                    raise ValueError("Server session capacity reached.")
                session = Session(socket)
                sessions[session.id] = session
                await send(
                    socket,
                    {
                        "type": "created",
                        "session_id": session.id,
                        "token": session.token,
                    },
                )
            elif isinstance(first, Join):
                target = sessions.get(first.session_id)
                if target is None or not secrets.compare_digest(
                    target.token.encode(), first.token.encode()
                ):
                    raise ValueError("Invalid session credentials.")
                async with target.lock:
                    if target.state != "waiting":
                        raise ValueError("Session is unavailable.")
                    session = target
                    session.viewer = socket
                    session.state = "pending"
                    await send(socket, {"type": "pending"})
                    await send(
                        session.host, {"type": "approval_requested", "name": first.name}
                    )
            else:
                raise TypeError("First message must be create or join.")
            while session.state != "closed":
                # Idle sessions and unanswered approval requests expire.
                message = await asyncio.wait_for(receive(socket), timeout=300)
                await session.relay(socket, message)
        except (ValidationError, ValueError, TypeError):
            try:
                await send(
                    socket,
                    {
                        "type": "error",
                        "message": "Invalid message, credentials, or session state.",
                    },
                )
            except (WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
                pass
        except (WebSocketDisconnect, RuntimeError, OSError, TimeoutError):
            pass
        finally:
            if session is not None:
                await session.end("disconnected")
                sessions.pop(session.id, None)
            await close(socket)

    return app
