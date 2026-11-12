"""Strict wire messages shared by the signaling endpoint."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Create(Message):
    type: Literal["create"]


class Join(Message):
    type: Literal["join"]
    session_id: str = Field(min_length=1, max_length=128)
    token: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=80)


class Approve(Message):
    type: Literal["approve"]
    approved: bool


class Offer(Message):
    type: Literal["offer"]
    sdp: str = Field(min_length=1, max_length=60000)


class Answer(Message):
    type: Literal["answer"]
    sdp: str = Field(min_length=1, max_length=60000)


class Ice(Message):
    type: Literal["ice"]
    candidate: str | None = Field(default=None, max_length=4096)
    sdpMid: str | None = Field(default=None, max_length=256)
    sdpMLineIndex: int | None = Field(default=None, ge=0, le=65535)


class Ping(Message):
    type: Literal["ping"]


class Leave(Message):
    type: Literal["leave"]


messages = TypeAdapter(
    Annotated[
        Create | Join | Approve | Offer | Answer | Ice | Leave | Ping,
        Field(discriminator="type"),
    ]
)
