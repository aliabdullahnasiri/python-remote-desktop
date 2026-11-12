"""One approved, receive-only viewer paired with a screen-sharing host."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from aiortc import (
    MediaStreamTrack,
    RTCConfiguration,
    RTCPeerConnection,
    RTCRtpSender,
    RTCSessionDescription,
)
from aiortc.mediastreams import MediaStreamError
from aiortc.sdp import candidate_from_sdp
from av import VideoFrame
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from rdesktop.server.protocol import Answer, Ice, Offer

from .capture import ScreenVideoTrack
from .options import request_approval, validate_url

Report = Callable[[str], None]


class PeerSession:
    """Own signaling, media tasks and capture resources for a single session."""

    def __init__(
        self,
        *,
        host: bool,
        server: str,
        token: str,
        configuration: RTCConfiguration,
        session_id: str = "",
        join_token: str = "",
        name: str = "Viewer",
        monitor: int = 1,
        fps: int = 15,
        report: Report = print,
        approve: Callable[[str], Awaitable[bool]] = request_approval,
        on_created: Callable[[str, str], None] | None = None,
        on_frame: Callable[[VideoFrame], None] | None = None,
        track_factory: Callable[[], ScreenVideoTrack] | None = None,
        connection_timeout: float = 45,
    ) -> None:
        self.host = host
        self.server = validate_url(server)
        if len(token) < 32:
            raise ValueError("The server token must contain at least 32 characters.")
        self.token = token
        self.configuration = configuration
        self.session_id = session_id
        self.join_token = join_token
        if not host and (not session_id or not join_token or not 1 <= len(name) <= 80):
            raise ValueError(
                "A viewer needs session credentials and a name of 1–80 characters."
            )
        self.name = name
        self.report = report
        self.approve = approve
        self.on_created = on_created
        self.on_frame = on_frame
        self.track_factory = track_factory or (lambda: ScreenVideoTrack(monitor, fps))
        self.connection_timeout = connection_timeout
        self.stopped = asyncio.Event()
        self.connected = asyncio.Event()
        self._error: BaseException | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._pc: RTCPeerConnection | None = None
        self._socket: ClientConnection | None = None
        self._source: ScreenVideoTrack | None = None
        self._state = "new"
        self._closing = False
        self._received_video = False

    def _fail(self, error: BaseException) -> None:
        if not self._closing:
            self._error = error
            self.stopped.set()

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)

        def completed(done: asyncio.Task[None]) -> None:
            self._tasks.discard(done)
            if not done.cancelled() and (error := done.exception()) is not None:
                self._fail(error)

        task.add_done_callback(completed)

    async def _send(self, message: dict) -> None:
        assert self._socket is not None
        await asyncio.wait_for(self._socket.send(json.dumps(message)), timeout=10)

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(60)
            await self._send({"type": "ping"})

    async def _watch_connection(self) -> None:
        try:
            await asyncio.wait_for(
                self.connected.wait(), timeout=self.connection_timeout
            )
        except TimeoutError:
            self._fail(
                RuntimeError(
                    "WebRTC connection timed out. Check firewalls and STUN/TURN configuration."
                )
            )

    async def _watch_capture(self) -> None:
        assert self._source is not None
        await self._source.failed.wait()
        self._fail(RuntimeError(f"Screen capture failed: {self._source.failure}"))

    async def _consume(self, track: MediaStreamTrack) -> None:
        try:
            while True:
                frame = await asyncio.wait_for(track.recv(), timeout=30)
                if not isinstance(frame, VideoFrame):
                    raise TypeError("Expected a video frame.")
                if self.on_frame is not None:
                    self.on_frame(frame)
        except MediaStreamError:
            self.report("Remote video ended.")
            self.stopped.set()
        except TimeoutError:
            self._fail(RuntimeError("No video received for 30 seconds."))

    async def _ask(self, name: str) -> None:
        approved = await self.approve(name)
        await self._send({"type": "approve", "approved": approved})
        if not approved:
            self.report("Screen sharing request declined.")

    def _prefer_h264(self) -> None:
        assert self._pc is not None
        codecs = [
            codec
            for codec in RTCRtpSender.getCapabilities("video").codecs
            if codec.mimeType.lower() == "video/h264"
        ]
        if not codecs:
            raise RuntimeError("H.264 is unavailable in this aiortc installation.")
        for transceiver in self._pc.getTransceivers():
            if transceiver.kind == "video":
                transceiver.setCodecPreferences(codecs)

    async def _local_description(self, kind: str) -> None:
        assert self._pc is not None
        description = await (
            self._pc.createOffer() if kind == "offer" else self._pc.createAnswer()
        )
        # aiortc gathers candidates here. Send the complete SDP, including ICE,
        # instead of relying on browser-style onicecandidate callbacks.
        await asyncio.wait_for(self._pc.setLocalDescription(description), timeout=30)
        local = self._pc.localDescription
        assert local is not None
        message = {"type": local.type, "sdp": local.sdp}
        (Offer if kind == "offer" else Answer).model_validate(message)
        await self._send(message)

    async def _handle(self, message: dict) -> None:
        assert self._pc is not None
        kind = message.get("type")
        if kind == "pong":
            return
        if kind == "error":
            raise RuntimeError("The signaling server rejected the request.")
        if kind == "session_closed":
            self.report(f"Session ended: {message.get('reason', 'closed')}")
            self.stopped.set()
            return
        if kind == "created" and self.host and self._state == "new":
            session_id, token = message.get("session_id"), message.get("token")
            if not isinstance(session_id, str) or not isinstance(token, str):
                raise ValueError("Invalid session credentials from the server.")
            self._state = "waiting"
            self.report(
                f"Session ID: {session_id}\nJoin token: {token}\nWaiting for a viewer. Ctrl+C stops hosting."
            )
            if self.on_created is not None:
                self.on_created(session_id, token)
        elif kind == "pending" and not self.host and self._state == "new":
            self._state = "pending"
            self.report("Waiting for host approval.")
        elif kind == "approval_requested" and self.host and self._state == "waiting":
            name = message.get("name")
            if not isinstance(name, str) or len(name) > 80:
                raise ValueError("Invalid viewer name.")
            self._state = "pending"
            self._spawn(self._ask(name))
        elif kind == "approved" and self._state == "pending":
            self._state = "approved"
            self.report("Screen sharing approved.")
            self._spawn(self._watch_connection())
            if self.host:
                self._source = self.track_factory()
                await self._source.start()
                self._spawn(self._watch_capture())
                self._pc.addTransceiver(self._source, direction="sendonly")
                self._prefer_h264()
                self.report("SCREEN SHARING ACTIVE — Ctrl+C ends sharing.")
                await self._local_description("offer")
                self._state = "offered"
        elif kind == "offer" and not self.host and self._state == "approved":
            offer = Offer.model_validate(message)
            self._pc.addTransceiver("video", direction="recvonly")
            self._prefer_h264()
            await self._pc.setRemoteDescription(
                RTCSessionDescription(sdp=offer.sdp, type="offer")
            )
            await self._local_description("answer")
            self._state = "negotiated"
        elif kind == "answer" and self.host and self._state == "offered":
            answer = Answer.model_validate(message)
            await self._pc.setRemoteDescription(
                RTCSessionDescription(sdp=answer.sdp, type="answer")
            )
            self._state = "negotiated"
        elif kind == "ice" and self._pc.remoteDescription is not None:
            ice = Ice.model_validate(message)
            candidate = None
            if ice.candidate:
                candidate = candidate_from_sdp(ice.candidate.removeprefix("candidate:"))
                candidate.sdpMid = ice.sdpMid
                candidate.sdpMLineIndex = ice.sdpMLineIndex
            await self._pc.addIceCandidate(candidate)
        else:
            raise ValueError("Unexpected signaling message or session state.")

    async def _receive(self) -> None:
        assert self._socket is not None
        try:
            async for raw in self._socket:
                if not isinstance(raw, str):
                    raise TypeError("Expected a signaling text message.")
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise TypeError("Expected a signaling object.")
                await self._handle(message)
                if self.stopped.is_set():
                    return
        except ConnectionClosed:
            if not self.stopped.is_set():
                raise RuntimeError(
                    "Signaling connection lost; screen sharing stopped."
                ) from None
        if not self.stopped.is_set():
            raise RuntimeError("Signaling connection closed; screen sharing stopped.")

    async def run(
        self,
        display: Callable[[asyncio.Event], Awaitable[None]] | None = None,
    ) -> None:
        self._pc = RTCPeerConnection(self.configuration)
        pc = self._pc

        @pc.on("connectionstatechange")
        def connection_changed() -> None:
            self.report(f"WebRTC: {pc.connectionState}")
            if pc.connectionState == "connected":
                self.connected.set()
            elif pc.connectionState == "failed":
                self._fail(
                    RuntimeError(
                        "WebRTC connection failed. Check network and TURN settings."
                    )
                )
            elif pc.connectionState == "closed":
                self.stopped.set()

        @pc.on("track")
        def received_track(track: MediaStreamTrack) -> None:
            if self.host or track.kind != "video" or self._received_video:
                self._fail(RuntimeError("Unexpected remote media track."))
                track.stop()
                return
            self._received_video = True
            self._spawn(self._consume(track))

        try:
            async with connect(
                self.server,
                additional_headers={"Authorization": f"Bearer {self.token}"},
                max_size=65536,
                max_queue=16,
                open_timeout=10,
                close_timeout=5,
                ping_interval=20,
                ping_timeout=20,
            ) as socket:
                self._socket = socket
                await self._send(
                    {"type": "create"}
                    if self.host
                    else {
                        "type": "join",
                        "session_id": self.session_id,
                        "token": self.join_token,
                        "name": self.name,
                    }
                )
                self._spawn(self._heartbeat())
                self._spawn(self._receive())
                if display is not None:

                    async def show() -> None:
                        await display(self.stopped)
                        self.stopped.set()

                    self._spawn(show())
                await self.stopped.wait()
                if self._error is not None:
                    raise self._error
        finally:
            self._closing = True
            self.stopped.set()
            tasks = list(self._tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await pc.close()
            finally:
                if self._source is not None:
                    await self._source.aclose()
            self.report(
                "Screen sharing stopped." if self.host else "Viewer disconnected."
            )
