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
presets start at 1. Areas are sent as-is (0-255). Where an opcode can address
one channel or the whole area, ``channel=None`` means every channel (0xFF on
the wire).

Fields whose unit the DyNet opcode list doesn't pin down are kept raw and named
``*_raw``, rather than guessed.

Anything this module has no class for decodes to `UnknownMessage`, which
keeps the packet, so nothing on the bus is ever lost.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import ClassVar, Self, cast, override

from .packet import Packet, SyncType
from .values import FADE_UNIT, JOIN_ALL, Level, decode_fade, encode_fade

__all__ = [
    "AnyMessage",
    "AreaCommand",
    "AreaCommandType",
    "AreaOff",
    "AreaPresetMessage",
    "AreaToLevel",
    "ChannelLevelReport",
    "ChannelOff",
    "ChannelOn",
    "ClockDate",
    "ClockTime",
    "FadeChannelToLevel",
    "FadeChannelToPreset",
    "FadeScale",
    "LinearPresetSelect",
    "LogicalMessage",
    "Message",
    "MotionActivitySync",
    "Opcode",
    "Panic",
    "PhysicalMessage",
    "PhysicalOpcode",
    "PresetLevelReport",
    "PresetReport",
    "PresetSelect",
    "RequestChannelLevel",
    "RequestPreset",
    "RequestPresetLevel",
    "RequestUserPreference",
    "ResetPreset",
    "RestoreSavedPreset",
    "SetChannelLevel",
    "SetUserPreference",
    "StopFade",
    "UnknownMessage",
    "Unpanic",
    "UserPreferenceReport",
    "decode",
]


class Opcode(IntEnum):
    """The logical (0x1C) opcodes aiodynet decodes into typed messages."""

    PRESET_1 = 0x00
    PRESET_2 = 0x01
    PRESET_3 = 0x02
    PRESET_4 = 0x03
    AREA_OFF = 0x04
    DECREMENT_AREA_LEVEL = 0x05
    INCREMENT_AREA_LEVEL = 0x06
    PROGRAM_CURRENT_PRESET = 0x08
    PRESET_5 = 0x0A
    PRESET_6 = 0x0B
    PRESET_7 = 0x0C
    PRESET_8 = 0x0D
    RESET_PRESET = 0x0F
    DISABLE_PANELS = 0x15
    ENABLE_PANELS = 0x16
    PANIC = 0x17
    UNPANIC = 0x18
    MOTION_ACTIVITY_SYNC = 0x2E
    SET_USER_PREFERENCE = 0x48
    REQUEST_USER_PREFERENCE = 0x49
    REPORT_USER_PREFERENCE = 0x4A
    REPORT_CHANNEL_LEVEL = 0x60
    REQUEST_CHANNEL_LEVEL = 0x61
    REPORT_PRESET = 0x62
    REQUEST_PRESET = 0x63
    LINEAR_PRESET = 0x65
    SAVE_CURRENT_PRESET = 0x66
    RESTORE_SAVED_PRESET = 0x67
    FADE_CHANNEL_TO_PRESET = 0x6B
    FADE_CHANNEL_TO_LEVEL_TENTHS = 0x71
    FADE_CHANNEL_TO_LEVEL_SECONDS = 0x72
    FADE_CHANNEL_TO_LEVEL_MINUTES = 0x73
    CHANNEL_OFF = 0x74
    CHANNEL_ON = 0x75
    STOP_FADE = 0x76
    AREA_TO_LEVEL = 0x79
    SET_CHANNEL_1_LEVEL = 0x80
    SET_CHANNEL_2_LEVEL = 0x81
    SET_CHANNEL_3_LEVEL = 0x82
    SET_CHANNEL_4_LEVEL = 0x83
    REQUEST_PRESET_LEVEL = 0x91
    REPORT_PRESET_LEVEL = 0x92


class PhysicalOpcode(IntEnum):
    """The physical (0x5C) opcodes aiodynet decodes into typed messages."""

    WRITE_TIME = 0x50
    WRITE_DATE = 0x53


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


def _check_channel_or_all(value: object) -> None:
    if value is not None:
        _check_int("channel", value, 1, 255)


def _channel_to_wire(channel: int | None) -> int:
    return ALL_CHANNELS if channel is None else channel - 1


def _channel_from_wire(byte: int) -> int | None:
    return None if byte == ALL_CHANNELS else byte + 1


def _preset_fade_steps(fade: float) -> int:
    return encode_fade(fade, unit=FADE_UNIT, max_steps=0xFFFF, field="fade")


def _preset_fade(low: int, high: int) -> float:
    return decode_fade(low | high << 8, unit=FADE_UNIT)


def _to_bcd(value: int) -> int:
    return (value // 10) << 4 | value % 10


def _from_bcd(byte: int, name: str) -> int:
    tens, units = byte >> 4, byte & 0x0F
    if tens > 9 or units > 9:
        msg = f"{name} byte 0x{byte:02x} is not BCD"
        raise ValueError(msg)
    return tens * 10 + units


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


class AreaCommandType(IntEnum):
    """Area commands that carry no data (each is its own opcode)."""

    DECREMENT_LEVEL = Opcode.DECREMENT_AREA_LEVEL
    """Lower every channel in the area by one step (1/255)."""
    INCREMENT_LEVEL = Opcode.INCREMENT_AREA_LEVEL
    """Raise every channel in the area by one step (1/255)."""
    PROGRAM_CURRENT_PRESET = Opcode.PROGRAM_CURRENT_PRESET
    """Leave program mode and store the current levels in the current preset."""
    DISABLE_PANELS = Opcode.DISABLE_PANELS
    """Disable the area's panels (keypads)."""
    ENABLE_PANELS = Opcode.ENABLE_PANELS
    """Enable the area's panels (keypads)."""
    SAVE_CURRENT_PRESET = Opcode.SAVE_CURRENT_PRESET
    """Remember the current preset, for `RestoreSavedPreset`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class AreaCommand(LogicalMessage):
    """An area command with no data, such as enabling panels (see `AreaCommandType`)."""

    opcodes: ClassVar[tuple[int, ...]] = tuple(AreaCommandType)

    command: AreaCommandType

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        command: object = self.command  # checked at runtime for untyped callers
        if not isinstance(command, AreaCommandType):
            msg = (
                f"command must be an AreaCommandType, not {type(self.command).__name__}"
            )
            raise TypeError(msg)

    @override
    def to_packet(self) -> Packet:
        return self._packet(0, self.command, 0, 0)

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, _, opcode, _, _, join = packet.data
        return cls(area=area, command=AreaCommandType(opcode), join=join)


@dataclass(frozen=True, slots=True, kw_only=True)
class _AreaFadeMessage(LogicalMessage):
    """An area command with a preset-style fade (low byte 2, high byte 4)."""

    opcode: ClassVar[int]

    fade: float = 0.0
    """Fade time in seconds, up to `MAX_PRESET_FADE`, in 20 ms steps."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _preset_fade_steps(self.fade)

    @override
    def to_packet(self) -> Packet:
        steps = _preset_fade_steps(self.fade)
        return self._packet(steps & 0xFF, self.opcode, steps >> 8, 0)

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, low, _, high, _, join = packet.data
        return cls(area=area, fade=_preset_fade(low, high), join=join)


@dataclass(frozen=True, slots=True, kw_only=True)
class AreaOff(_AreaFadeMessage):
    """Fade every channel in an area to off (opcode 0x04)."""

    opcode: ClassVar[int] = Opcode.AREA_OFF
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.AREA_OFF,)


@dataclass(frozen=True, slots=True, kw_only=True)
class ResetPreset(_AreaFadeMessage):
    """Fade an area back to the levels of its current preset (opcode 0x0F)."""

    opcode: ClassVar[int] = Opcode.RESET_PRESET
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.RESET_PRESET,)


@dataclass(frozen=True, slots=True, kw_only=True)
class Panic(_AreaFadeMessage):
    """Put an area into panic mode (opcode 0x17)."""

    opcode: ClassVar[int] = Opcode.PANIC
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.PANIC,)


@dataclass(frozen=True, slots=True, kw_only=True)
class Unpanic(_AreaFadeMessage):
    """Take an area out of panic mode (opcode 0x18)."""

    opcode: ClassVar[int] = Opcode.UNPANIC
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.UNPANIC,)


@dataclass(frozen=True, slots=True, kw_only=True)
class AreaToLevel(LogicalMessage):
    """Fade every channel in an area to one level (opcode 0x79)."""

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.AREA_TO_LEVEL,)

    level: Level
    """The level to fade to."""

    fade: float = 0.0
    """Fade time in seconds, up to `MAX_PRESET_FADE`, in 20 ms steps."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_level("level", self.level)
        _preset_fade_steps(self.fade)

    @override
    def to_packet(self) -> Packet:
        steps = _preset_fade_steps(self.fade)
        return self._packet(
            self.level.raw, Opcode.AREA_TO_LEVEL, steps & 0xFF, steps >> 8
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, level, _, low, high, join = packet.data
        return cls(
            area=area, level=Level(level), fade=_preset_fade(low, high), join=join
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class _ChannelFadeMessage(LogicalMessage):
    """A channel command with a preset-style fade (low byte 4, high byte 5)."""

    opcode: ClassVar[int]

    channel: int | None
    """The channel, numbered from 1, or ``None`` for every channel."""

    fade: float = 0.0
    """Fade time in seconds, up to `MAX_PRESET_FADE`, in 20 ms steps."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_channel_or_all(self.channel)
        _preset_fade_steps(self.fade)

    @override
    def to_packet(self) -> Packet:
        steps = _preset_fade_steps(self.fade)
        return self._packet(
            _channel_to_wire(self.channel), self.opcode, steps & 0xFF, steps >> 8
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, channel, _, low, high, join = packet.data
        return cls(
            area=area,
            channel=_channel_from_wire(channel),
            fade=_preset_fade(low, high),
            join=join,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelOff(_ChannelFadeMessage):
    """Fade a channel, or every channel, to off (opcode 0x74)."""

    opcode: ClassVar[int] = Opcode.CHANNEL_OFF
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.CHANNEL_OFF,)


@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelOn(_ChannelFadeMessage):
    """Fade a channel, or every channel, to full (opcode 0x75)."""

    opcode: ClassVar[int] = Opcode.CHANNEL_ON
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.CHANNEL_ON,)


@dataclass(frozen=True, slots=True, kw_only=True)
class StopFade(LogicalMessage):
    """Stop a channel, or every channel, where it is mid-fade (opcode 0x76)."""

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.STOP_FADE,)

    channel: int | None
    """The channel, numbered from 1, or ``None`` for every channel."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_channel_or_all(self.channel)

    @override
    def to_packet(self) -> Packet:
        return self._packet(_channel_to_wire(self.channel), Opcode.STOP_FADE, 0, 0)

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, channel, _, _, _, join = packet.data
        return cls(area=area, channel=_channel_from_wire(channel), join=join)


class FadeScale(IntEnum):
    """Which of the three "fade channel to level" opcodes a message uses.

    The opcode list describes them as 0.1 s, 1 s and 1 minute steps. That
    isn't confirmed on hardware, so `FadeChannelToLevel` keeps the fade raw.
    """

    TENTHS = Opcode.FADE_CHANNEL_TO_LEVEL_TENTHS
    SECONDS = Opcode.FADE_CHANNEL_TO_LEVEL_SECONDS
    MINUTES = Opcode.FADE_CHANNEL_TO_LEVEL_MINUTES


@dataclass(frozen=True, slots=True, kw_only=True)
class FadeChannelToLevel(LogicalMessage):
    """Fade a channel, or every channel, to a level (opcodes 0x71-0x73).

    These are the "newer" channel-level opcodes. `SetChannelLevel` (0x80-0x83)
    is what existing integrations send.
    """

    opcodes: ClassVar[tuple[int, ...]] = tuple(FadeScale)

    channel: int | None
    """The channel, numbered from 1, or ``None`` for every channel."""

    level: Level
    """The level to fade to."""

    scale: FadeScale
    """Which opcode, and so which unit `fade_raw` counts in."""

    fade_raw: int = 0
    """The fade byte exactly as sent, in `scale` units."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_channel_or_all(self.channel)
        _check_level("level", self.level)
        scale: object = self.scale  # checked at runtime for untyped callers
        if not isinstance(scale, FadeScale):
            msg = f"scale must be a FadeScale, not {type(self.scale).__name__}"
            raise TypeError(msg)
        _check_int("fade_raw", self.fade_raw, 0, 0xFF)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            _channel_to_wire(self.channel), self.scale, self.level.raw, self.fade_raw
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, channel, opcode, level, fade, join = packet.data
        return cls(
            area=area,
            channel=_channel_from_wire(channel),
            level=Level(level),
            scale=FadeScale(opcode),
            fade_raw=fade,
            join=join,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RestoreSavedPreset(LogicalMessage):
    """Go back to the preset remembered by ``SAVE_CURRENT_PRESET`` (opcode 0x67).

    The fade byte's unit isn't documented, so it is kept raw.
    """

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.RESTORE_SAVED_PRESET,)

    fade_raw: int = 0
    """The fade byte exactly as sent (unit unconfirmed)."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_int("fade_raw", self.fade_raw, 0, 0xFF)

    @override
    def to_packet(self) -> Packet:
        return self._packet(0, Opcode.RESTORE_SAVED_PRESET, 0, self.fade_raw)

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, _, _, _, fade, join = packet.data
        return cls(area=area, fade_raw=fade, join=join)


@dataclass(frozen=True, slots=True, kw_only=True)
class MotionActivitySync(LogicalMessage):
    """A motion sensor sharing its activity timer with others (opcode 0x2E).

    Sent by DyNet motion sensors (DUS704 and similar). The timer's unit isn't
    documented, so it is kept raw.
    """

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.MOTION_ACTIVITY_SYNC,)

    channel: int | None
    """The channel, numbered from 1, or ``None`` for every channel."""

    time_raw: int
    """The 16-bit time value exactly as sent (unit unconfirmed)."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_channel_or_all(self.channel)
        _check_int("time_raw", self.time_raw, 0, 0xFFFF)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            _channel_to_wire(self.channel),
            Opcode.MOTION_ACTIVITY_SYNC,
            self.time_raw >> 8,
            self.time_raw & 0xFF,
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, channel, _, high, low, join = packet.data
        return cls(
            area=area,
            channel=_channel_from_wire(channel),
            time_raw=high << 8 | low,
            join=join,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class _UserPreferenceMessage(LogicalMessage):
    """Set or report a user preference value (high byte 4, low byte 5)."""

    opcode: ClassVar[int]

    preference: int
    """Which preference, 0-255. What each one means depends on the device."""

    value: int
    """The 16-bit value."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_int("preference", self.preference, 0, 0xFF)
        _check_int("value", self.value, 0, 0xFFFF)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            self.preference, self.opcode, self.value >> 8, self.value & 0xFF
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, preference, _, high, low, join = packet.data
        return cls(area=area, preference=preference, value=high << 8 | low, join=join)


@dataclass(frozen=True, slots=True, kw_only=True)
class SetUserPreference(_UserPreferenceMessage):
    """Set a user preference in an area (opcode 0x48)."""

    opcode: ClassVar[int] = Opcode.SET_USER_PREFERENCE
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.SET_USER_PREFERENCE,)


@dataclass(frozen=True, slots=True, kw_only=True)
class UserPreferenceReport(_UserPreferenceMessage):
    """An area reporting a user preference (opcode 0x4A)."""

    opcode: ClassVar[int] = Opcode.REPORT_USER_PREFERENCE
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.REPORT_USER_PREFERENCE,)


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestUserPreference(LogicalMessage):
    """Ask an area for a user preference (opcode 0x49)."""

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.REQUEST_USER_PREFERENCE,)

    preference: int
    """Which preference, 0-255."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_int("preference", self.preference, 0, 0xFF)

    @override
    def to_packet(self) -> Packet:
        return self._packet(self.preference, Opcode.REQUEST_USER_PREFERENCE, 0, 0)

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, preference, _, _, _, join = packet.data
        return cls(area=area, preference=preference, join=join)


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestPresetLevel(LogicalMessage):
    """Ask what level a channel has stored for a preset (opcode 0x91)."""

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.REQUEST_PRESET_LEVEL,)

    channel: int
    """The channel, numbered from 1."""

    preset: int
    """The preset, numbered from 1."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_int("channel", self.channel, 1, 256)
        _check_int("preset", self.preset, 1, 256)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            self.channel - 1, Opcode.REQUEST_PRESET_LEVEL, self.preset - 1, 0
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, channel, _, preset, _, join = packet.data
        return cls(area=area, channel=channel + 1, preset=preset + 1, join=join)


@dataclass(frozen=True, slots=True, kw_only=True)
class PresetLevelReport(LogicalMessage):
    """A channel reporting the level it has stored for a preset (opcode 0x92)."""

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.REPORT_PRESET_LEVEL,)

    channel: int
    """The channel, numbered from 1."""

    preset: int
    """The preset, numbered from 1."""

    level: Level
    """The stored level."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_int("channel", self.channel, 1, 256)
        _check_int("preset", self.preset, 1, 256)
        _check_level("level", self.level)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            self.channel - 1,
            Opcode.REPORT_PRESET_LEVEL,
            self.preset - 1,
            self.level.raw,
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, channel, _, preset, level, join = packet.data
        return cls(
            area=area,
            channel=channel + 1,
            preset=preset + 1,
            level=Level(level),
            join=join,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class PhysicalMessage(Message):
    """A message addressed to a device (sync byte 0x5C)."""

    opcodes: ClassVar[tuple[int, ...]]

    device_code: int
    """The device type code, 0-255."""

    box: int
    """The box number, 0-255."""

    def __post_init__(self) -> None:
        """Validate the common fields."""
        _check_int("device_code", self.device_code, 0, 0xFF)
        _check_int("box", self.box, 0, 0xFF)

    def _packet(self, opcode: int, d1: int, d2: int, d3: int) -> Packet:
        return Packet.build(
            SyncType.PHYSICAL, self.device_code, self.box, opcode, d1, d2, d3
        )

    @classmethod
    @abstractmethod
    def from_packet(cls, packet: Packet) -> Self:
        """Decode a physical packet whose opcode is in `opcodes`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ClockTime(PhysicalMessage):
    """A device setting or broadcasting the time of day (physical opcode 0x50).

    Hours, minutes and seconds are BCD on the wire. A packet whose bytes
    aren't valid BCD times decodes as `UnknownMessage` instead.
    """

    opcodes: ClassVar[tuple[int, ...]] = (PhysicalOpcode.WRITE_TIME,)

    hour: int
    minute: int
    second: int

    @override
    def __post_init__(self) -> None:
        PhysicalMessage.__post_init__(self)
        _check_int("hour", self.hour, 0, 23)
        _check_int("minute", self.minute, 0, 59)
        _check_int("second", self.second, 0, 59)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            PhysicalOpcode.WRITE_TIME,
            _to_bcd(self.hour),
            _to_bcd(self.minute),
            _to_bcd(self.second),
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, device_code, box, _, hour, minute, second = packet.data
        return cls(
            device_code=device_code,
            box=box,
            hour=_from_bcd(hour, "hour"),
            minute=_from_bcd(minute, "minute"),
            second=_from_bcd(second, "second"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ClockDate(PhysicalMessage):
    """A device setting or broadcasting the date (physical opcode 0x53).

    The day and two-digit year are BCD. The middle byte packs the day of the
    week into its top three bits and the month (BCD) into the low five. A
    packet that doesn't fit decodes as `UnknownMessage` instead.
    """

    opcodes: ClassVar[tuple[int, ...]] = (PhysicalOpcode.WRITE_DATE,)

    year: int
    """The year, 2000-2099 (only the last two digits are sent)."""

    month: int
    day: int

    day_of_week: int
    """The 3-bit day-of-week field as sent. Wednesday was 3 on a real site."""

    @override
    def __post_init__(self) -> None:
        PhysicalMessage.__post_init__(self)
        _check_int("year", self.year, 2000, 2099)
        _check_int("month", self.month, 1, 12)
        _check_int("day", self.day, 1, 31)
        _check_int("day_of_week", self.day_of_week, 0, 7)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            PhysicalOpcode.WRITE_DATE,
            _to_bcd(self.day),
            self.day_of_week << 5 | _to_bcd(self.month),
            _to_bcd(self.year - 2000),
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, device_code, box, _, day, packed, year = packet.data
        return cls(
            device_code=device_code,
            box=box,
            year=2000 + _from_bcd(year, "year"),
            month=_from_bcd(packed & 0x1F, "month"),
            day=_from_bcd(day, "day"),
            day_of_week=packed >> 5,
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
    | AreaCommand
    | AreaOff
    | ResetPreset
    | Panic
    | Unpanic
    | AreaToLevel
    | ChannelOff
    | ChannelOn
    | StopFade
    | FadeChannelToLevel
    | RestoreSavedPreset
    | MotionActivitySync
    | SetUserPreference
    | RequestUserPreference
    | UserPreferenceReport
    | RequestPresetLevel
    | PresetLevelReport
    | ClockTime
    | ClockDate
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
    AreaCommand,
    AreaOff,
    ResetPreset,
    Panic,
    Unpanic,
    AreaToLevel,
    ChannelOff,
    ChannelOn,
    StopFade,
    FadeChannelToLevel,
    RestoreSavedPreset,
    MotionActivitySync,
    SetUserPreference,
    RequestUserPreference,
    UserPreferenceReport,
    RequestPresetLevel,
    PresetLevelReport,
)

_PHYSICAL_TYPES: tuple[type[PhysicalMessage], ...] = (ClockTime, ClockDate)

_Decoder = Callable[[Packet], "AnyMessage"]
_DECODERS: dict[tuple[int, int], _Decoder] = {}
for _type in _LOGICAL_TYPES:
    for _opcode in _type.opcodes:
        # Every concrete class is a member of AnyMessage; mypy can't see that
        # through the base-class tuple.
        _DECODERS[SyncType.LOGICAL, _opcode] = cast("_Decoder", _type.from_packet)
for _ptype in _PHYSICAL_TYPES:
    for _opcode in _ptype.opcodes:
        _DECODERS[SyncType.PHYSICAL, _opcode] = cast("_Decoder", _ptype.from_packet)


def decode(packet: Packet) -> AnyMessage:
    """Decode a packet into a typed message.

    Never raises for a valid `Packet`: anything without a typed message comes
    back as `UnknownMessage`.
    """
    decoder = _DECODERS.get((packet.sync, packet.data[3]))
    if decoder is not None:
        try:
            return decoder(packet)
        except ValueError:
            pass  # bytes that don't fit the message, such as a time that isn't BCD
    return UnknownMessage(packet=packet)
