"""Typed DyNet messages and the codec between them and `Packet`.

A message says what a packet *means*; a `Packet` is what goes on the wire.
Every message class handles exactly one wire form. Bytes the protocol marks
as unused are dropped when decoding and sent as zero, so ``decode(p).to_packet()
== p`` whenever those bytes were zero. The original `Packet` is always passed
to subscribers alongside the message, so nothing is hidden.

Messages are immutable and check their fields when built, so an out-of-range
area, channel, preset, level or fade raises `ValueError` before anything is
sent. All fields are keyword-only, so ``SetChannelLevel(area=1, channel=2,
...)`` can't silently swap two numbers.

Numbering follows the DyNet tools and keypads, not the wire: channels and
presets start at 1. Areas are sent as-is (0-255).

Anything this module has no class for decodes to `UnknownMessage`, which
keeps the packet, so nothing on the bus is ever lost.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, ClassVar, Self, override

from .packet import Packet, SyncType
from .values import FADE_UNIT, JOIN_ALL, Level, decode_fade, encode_fade

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "AnyMessage",
    "AreaPresetMessage",
    "ChannelLevelReport",
    "FadeChannelToPreset",
    "LinearPresetSelect",
    "LogicalMessage",
    "Message",
    "Opcode",
    "PresetReport",
    "PresetSelect",
    "RequestChannelLevel",
    "RequestPreset",
    "SetChannelLevel",
    "UnknownMessage",
    "decode",
]


class Opcode(IntEnum):
    """The logical (0x1C) opcodes aiodynet decodes into typed messages."""

    PRESET_1 = 0x00
    PRESET_2 = 0x01
    PRESET_3 = 0x02
    PRESET_4 = 0x03
    PRESET_5 = 0x0A
    PRESET_6 = 0x0B
    PRESET_7 = 0x0C
    PRESET_8 = 0x0D
    REPORT_CHANNEL_LEVEL = 0x60
    REQUEST_CHANNEL_LEVEL = 0x61
    REPORT_PRESET = 0x62
    REQUEST_PRESET = 0x63
    LINEAR_PRESET = 0x65
    FADE_CHANNEL_TO_PRESET = 0x6B
    SET_CHANNEL_1_LEVEL = 0x80
    SET_CHANNEL_2_LEVEL = 0x81
    SET_CHANNEL_3_LEVEL = 0x82
    SET_CHANNEL_4_LEVEL = 0x83


# Presets 1-8 within a bank use these opcodes, in order.
_PRESET_OPCODES = (
    Opcode.PRESET_1,
    Opcode.PRESET_2,
    Opcode.PRESET_3,
    Opcode.PRESET_4,
    Opcode.PRESET_5,
    Opcode.PRESET_6,
    Opcode.PRESET_7,
    Opcode.PRESET_8,
)
_PRESET_INDEX = {opcode: index for index, opcode in enumerate(_PRESET_OPCODES)}

_SET_CHANNEL_OPCODES = (
    Opcode.SET_CHANNEL_1_LEVEL,
    Opcode.SET_CHANNEL_2_LEVEL,
    Opcode.SET_CHANNEL_3_LEVEL,
    Opcode.SET_CHANNEL_4_LEVEL,
)

ALL_CHANNELS = 0xFF
"""The wire value some opcodes use for "every channel in the area"."""

MAX_PRESET_FADE = 0xFFFF * FADE_UNIT
"""Longest fade a preset message carries: 1310.7 s."""

MAX_CHANNEL_FADE = 0xFF * FADE_UNIT
"""Longest fade `SetChannelLevel` carries: 5.1 s."""


def _check_int(name: str, value: object, low: int, high: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{name} must be an int, not {type(value).__name__}"
        raise TypeError(msg)
    if not low <= value <= high:
        msg = f"{name} must be {low}-{high}, got {value}"
        raise ValueError(msg)


def _check_level(name: str, value: object) -> None:
    if not isinstance(value, Level):
        msg = (
            f"{name} must be a Level, not {type(value).__name__} "
            "(use Level.from_percent(), Level.from_fraction() or Level(raw))"
        )
        raise TypeError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class Message(ABC):
    """Base class for every message."""

    @abstractmethod
    def to_packet(self) -> Packet:
        """Encode the message as a packet."""


@dataclass(frozen=True, slots=True, kw_only=True)
class LogicalMessage(Message):
    """A message addressed to an area (sync byte 0x1C)."""

    opcodes: ClassVar[tuple[int, ...]]

    area: int
    """The area, 0-255."""

    join: int = JOIN_ALL
    """The join mask. Leave it at 0xFF unless your site uses joins."""

    def __post_init__(self) -> None:
        """Validate the common fields."""
        _check_int("area", self.area, 0, 0xFF)
        _check_int("join", self.join, 0, 0xFF)

    def _packet(self, b2: int, opcode: int, b4: int, b5: int) -> Packet:
        return Packet.build(SyncType.LOGICAL, self.area, b2, opcode, b4, b5, self.join)

    @classmethod
    @abstractmethod
    def from_packet(cls, packet: Packet) -> Self:
        """Decode a logical packet whose opcode is in `opcodes`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class AreaPresetMessage(LogicalMessage):
    """Any message that means "this area is now at this preset".

    `PresetSelect`, `LinearPresetSelect` and `PresetReport` all derive from
    it, so one ``isinstance`` check catches a preset however it was sent.
    """

    preset: int
    """The preset, numbered from 1."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PresetSelect(AreaPresetMessage):
    """Select a preset in an area (opcodes 0x00-0x03 and 0x0A-0x0D).

    This is what keypads send, and the usual way to recall a preset. Presets
    1-2048 are reached as preset 1-8 of one of 256 banks.
    """

    opcodes: ClassVar[tuple[int, ...]] = _PRESET_OPCODES

    fade: float = 0.0
    """Fade time in seconds, up to `MAX_PRESET_FADE`, in 20 ms steps."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_int("preset", self.preset, 1, 2048)
        encode_fade(self.fade, unit=FADE_UNIT, max_steps=0xFFFF, field="fade")

    @override
    def to_packet(self) -> Packet:
        bank, index = divmod(self.preset - 1, 8)
        steps = encode_fade(self.fade, unit=FADE_UNIT, max_steps=0xFFFF, field="fade")
        return self._packet(steps & 0xFF, _PRESET_OPCODES[index], steps >> 8, bank)

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, fade_low, opcode, fade_high, bank, join = packet.data
        return cls(
            area=area,
            preset=bank * 8 + _PRESET_INDEX[Opcode(opcode)] + 1,
            fade=decode_fade(fade_low | fade_high << 8, unit=FADE_UNIT),
            join=join,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class LinearPresetSelect(AreaPresetMessage):
    """Select a preset by its number in one byte (opcode 0x65).

    Some controllers send this form. It reaches presets 1-256 only; prefer
    `PresetSelect` when sending.
    """

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.LINEAR_PRESET,)

    fade: float = 0.0
    """Fade time in seconds, up to `MAX_PRESET_FADE`, in 20 ms steps."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_int("preset", self.preset, 1, 256)
        encode_fade(self.fade, unit=FADE_UNIT, max_steps=0xFFFF, field="fade")

    @override
    def to_packet(self) -> Packet:
        steps = encode_fade(self.fade, unit=FADE_UNIT, max_steps=0xFFFF, field="fade")
        return self._packet(
            self.preset - 1, Opcode.LINEAR_PRESET, steps & 0xFF, steps >> 8
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, preset, _, fade_low, fade_high, join = packet.data
        return cls(
            area=area,
            preset=preset + 1,
            fade=decode_fade(fade_low | fade_high << 8, unit=FADE_UNIT),
            join=join,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class PresetReport(AreaPresetMessage):
    """An area reporting its current preset (opcode 0x62).

    Sent in reply to `RequestPreset`.
    """

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.REPORT_PRESET,)

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_int("preset", self.preset, 1, 256)

    @override
    def to_packet(self) -> Packet:
        return self._packet(self.preset - 1, Opcode.REPORT_PRESET, 0, 0)

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, preset, _, _, _, join = packet.data
        return cls(area=area, preset=preset + 1, join=join)


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestPreset(LogicalMessage):
    """Ask an area to report its current preset (opcode 0x63).

    The area answers with a `PresetReport`. Most areas answer for channel 1;
    some only answer when asked about a channel they actually have.
    """

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.REQUEST_PRESET,)

    channel: int = 1
    """The channel to ask about, numbered from 1."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_int("channel", self.channel, 1, 256)

    @override
    def to_packet(self) -> Packet:
        return self._packet(0, Opcode.REQUEST_PRESET, self.channel - 1, 0)

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, _, _, channel, _, join = packet.data
        return cls(area=area, channel=channel + 1, join=join)


@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelLevelReport(LogicalMessage):
    """A channel reporting its level (opcode 0x60).

    Sent in reply to `RequestChannelLevel`. While a channel is fading,
    `actual_level` is where it is now and `target_level` where it is heading.
    """

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.REPORT_CHANNEL_LEVEL,)

    channel: int
    """The channel, numbered from 1."""

    target_level: Level
    """The level the channel is fading to."""

    actual_level: Level
    """The level the channel is at right now."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_int("channel", self.channel, 1, 256)
        _check_level("target_level", self.target_level)
        _check_level("actual_level", self.actual_level)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            self.channel - 1,
            Opcode.REPORT_CHANNEL_LEVEL,
            self.target_level.raw,
            self.actual_level.raw,
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, channel, _, target, actual, join = packet.data
        return cls(
            area=area,
            channel=channel + 1,
            target_level=Level(target),
            actual_level=Level(actual),
            join=join,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestChannelLevel(LogicalMessage):
    """Ask a channel to report its level (opcode 0x61).

    The channel answers with a `ChannelLevelReport`.
    """

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.REQUEST_CHANNEL_LEVEL,)

    channel: int
    """The channel, numbered from 1."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_int("channel", self.channel, 1, 256)

    @override
    def to_packet(self) -> Packet:
        return self._packet(self.channel - 1, Opcode.REQUEST_CHANNEL_LEVEL, 0, 0)

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, channel, _, _, _, join = packet.data
        return cls(area=area, channel=channel + 1, join=join)


@dataclass(frozen=True, slots=True, kw_only=True)
class SetChannelLevel(LogicalMessage):
    """Fade one channel to a level (opcodes 0x80-0x83).

    Channels 1-1024 are reached as channel 1-4 of one of 256 banks. The fade
    is one byte of 20 ms steps, so the longest is 5.1 s (`MAX_CHANNEL_FADE`).
    """

    opcodes: ClassVar[tuple[int, ...]] = _SET_CHANNEL_OPCODES

    channel: int
    """The channel, numbered from 1."""

    level: Level
    """The level to fade to."""

    fade: float = 0.0
    """Fade time in seconds, up to `MAX_CHANNEL_FADE`, in 20 ms steps."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_int("channel", self.channel, 1, 1024)
        _check_level("level", self.level)
        encode_fade(self.fade, unit=FADE_UNIT, max_steps=0xFF, field="fade")

    @override
    def to_packet(self) -> Packet:
        bank, index = divmod(self.channel - 1, 4)
        steps = encode_fade(self.fade, unit=FADE_UNIT, max_steps=0xFF, field="fade")
        # The bank byte is offset by one: 0xFF is channels 1-4, 0x00 is 5-8.
        return self._packet(
            self.level.raw, _SET_CHANNEL_OPCODES[index], (bank - 1) & 0xFF, steps
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, level, opcode, bank, steps, join = packet.data
        return cls(
            area=area,
            channel=((bank + 1) & 0xFF) * 4 + (opcode - Opcode.SET_CHANNEL_1_LEVEL) + 1,
            level=Level(level),
            fade=decode_fade(steps, unit=FADE_UNIT),
            join=join,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class FadeChannelToPreset(LogicalMessage):
    """Fade one channel, or every channel in the area, to a preset (opcode 0x6B).

    The fade byte's unit isn't confirmed on hardware, so it is kept raw.
    """

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.FADE_CHANNEL_TO_PRESET,)

    channel: int | None
    """The channel, numbered from 1, or ``None`` for every channel."""

    preset: int
    """The preset, numbered from 1."""

    fade_raw: int = 0
    """The fade byte exactly as sent (unit unconfirmed)."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        if self.channel is not None:
            _check_int("channel", self.channel, 1, 255)
        _check_int("preset", self.preset, 1, 256)
        _check_int("fade_raw", self.fade_raw, 0, 0xFF)

    @override
    def to_packet(self) -> Packet:
        channel = ALL_CHANNELS if self.channel is None else self.channel - 1
        return self._packet(
            channel, Opcode.FADE_CHANNEL_TO_PRESET, self.preset - 1, self.fade_raw
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, channel, _, preset, fade, join = packet.data
        return cls(
            area=area,
            channel=None if channel == ALL_CHANNELS else channel + 1,
            preset=preset + 1,
            fade_raw=fade,
            join=join,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class UnknownMessage(Message):
    """A packet aiodynet has no typed message for. It is passed on unchanged."""

    packet: Packet = field()

    @override
    def to_packet(self) -> Packet:
        return self.packet


type AnyMessage = (
    PresetSelect
    | LinearPresetSelect
    | PresetReport
    | RequestPreset
    | ChannelLevelReport
    | RequestChannelLevel
    | SetChannelLevel
    | FadeChannelToPreset
    | UnknownMessage
)
"""Every message `decode` can return. Match on it with ``assert_never`` for an
exhaustive check."""

_LOGICAL_TYPES: tuple[type[LogicalMessage], ...] = (
    PresetSelect,
    LinearPresetSelect,
    PresetReport,
    RequestPreset,
    ChannelLevelReport,
    RequestChannelLevel,
    SetChannelLevel,
    FadeChannelToPreset,
)

_DECODERS: dict[int, Callable[[Packet], AnyMessage]] = {
    opcode: message_type.from_packet  # type: ignore[misc]
    for message_type in _LOGICAL_TYPES
    for opcode in message_type.opcodes
}


def decode(packet: Packet) -> AnyMessage:
    """Decode a packet into a typed message.

    Never raises for a valid `Packet`: anything without a typed message comes
    back as `UnknownMessage`.
    """
    if packet.is_logical and (decoder := _DECODERS.get(packet.data[3])):
        return decoder(packet)
    return UnknownMessage(packet=packet)
