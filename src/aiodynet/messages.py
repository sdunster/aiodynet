"""Typed DyNet messages and the codec between them and the wire.

A message says what a packet *means*; a `Packet` is what goes on the wire.
DyNet 2 frames work the same way: `decode` turns a `Dynet2Frame` into a
`Dynet2Message`. DyNet 2 messages only come from devices; aiodynet can't send
them.
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

Anything this module has no class for decodes to `UnknownMessage` (or
`UnknownDynet2Message` for a frame), which keeps the raw bytes, so nothing on
the bus is ever lost.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import ClassVar, Self, cast, overload, override

from .dynet2 import DYNET2_MAX_LENGTH, DYNET2_MIN_LENGTH, Dynet2Frame
from .packet import Packet, SyncType
from .values import FADE_UNIT, JOIN_ALL, Level, decode_fade, encode_fade

__all__ = [
    "DYNET2_ALL_DEVICES",
    "AnyDynet1Message",
    "AnyDynet2Message",
    "AnyMessage",
    "AreaCascadingSync",
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
    "DeviceIdentify",
    "DeviceStatusReport",
    "Dynet2Message",
    "Dynet2SignonReply",
    "FadeChannelToLevel",
    "FadeChannelToPreset",
    "FadeScale",
    "HoldTimerAllPresets",
    "LinearPresetSelect",
    "LogicalMessage",
    "Message",
    "MotionActivitySync",
    "OccupancyDetectionAllPresets",
    "Opcode",
    "Panic",
    "PhysicalMessage",
    "PhysicalOpcode",
    "PresetLevelReport",
    "PresetReport",
    "PresetSelect",
    "ProgramLevelsToPreset",
    "RequestChannelLevel",
    "RequestFirmwareVersion",
    "RequestPreset",
    "RequestPresetLevel",
    "RequestSignon",
    "RequestUserPreference",
    "ResetPreset",
    "RestoreSavedPreset",
    "ResumeOccupancyDetection",
    "SetChannelLevel",
    "SetUserPreference",
    "StopFade",
    "SuspendOccupancyDetection",
    "UnknownDynet2Message",
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
    PROGRAM_LEVELS_TO_PRESET = 0x09
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
    AREA_CASCADING_SYNC = 0x2F
    HOLD_TIMER_ALL_PRESETS = 0x30
    OCCUPANCY_DETECTION_ALL_PRESETS = 0x31
    SUSPEND_OCCUPANCY_DETECTION = 0x3A
    RESUME_OCCUPANCY_DETECTION = 0x3B
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

    DEVICE_IDENTIFY = 0x00
    REQUEST_DEVICE_STATUS = 0x40
    REPORT_DEVICE_STATUS = 0x41
    WRITE_TIME = 0x50
    WRITE_DATE = 0x53
    REQUEST_FIRMWARE_VERSION = 0x80


_DO_D2_SIGNON = 0x0A
"""The Request Device Status sub-opcode that asks a device to sign on."""

_ALL_PHYSICAL_DEVICES = (0xAA, 0x55)

DYNET2_ALL_DEVICES = (0xAA, 0x5555)
"""The DyNet 2 address (device code, box) that means every device."""


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

_TEMPERATURE_PREFERENCE = 0x0C
"""The user preference that holds a temperature."""

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

    @property
    def temperature(self) -> float | None:
        """The temperature in °C, for preference 0x0C (temperature).

        Byte 4 is whole degrees and byte 5 hundredths (0-99): the official
        DyNet network monitor shows ``1b 06`` as +27.06 °C. ``None`` for other
        preferences, for a hundredths byte over 99, and for byte 4 of 0x80 or
        more, because how negative readings are sent hasn't been seen.
        """
        whole, hundredths = self.value >> 8, self.value & 0xFF
        if self.preference != _TEMPERATURE_PREFERENCE or whole >= 0x80:
            return None
        if hundredths > 99:
            return None
        return whole + hundredths / 100


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
class AreaCascadingSync(LogicalMessage):
    """An area's cascading timer keeping linked areas in step (opcode 0x2F).

    The opcode list calls this "Corridor Holdoff Synchronise"; the official
    DyNet network monitor calls it "Area Cascading Synchronise". Areas send
    it every 30 s or so while the timer runs. Bytes 4-5 are unused.
    """

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.AREA_CASCADING_SYNC,)

    channel: int | None = None
    """The channel, numbered from 1, or ``None`` for every channel (all real
    packets seen so far)."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_channel_or_all(self.channel)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            _channel_to_wire(self.channel), Opcode.AREA_CASCADING_SYNC, 0, 0
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, channel, _, _, _, join = packet.data
        return cls(area=area, channel=_channel_from_wire(channel), join=join)


@dataclass(frozen=True, slots=True, kw_only=True)
class OccupancyDetectionAllPresets(LogicalMessage):
    """Suspend or resume occupancy detection for every preset (opcode 0x31).

    Byte 5 is 0x01 to resume and 0x00 to suspend, confirmed with the official
    DyNet network monitor. A packet with any other value there decodes as
    `UnknownMessage`. Byte 4 is unused.
    """

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.OCCUPANCY_DETECTION_ALL_PRESETS,)

    channel: int | None = None
    """The channel, numbered from 1, or ``None`` for every channel (all real
    packets seen so far)."""

    resume: bool
    """``True`` to resume detection, ``False`` to suspend it."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_channel_or_all(self.channel)
        resume: object = self.resume  # checked at runtime too, for untyped callers
        if not isinstance(resume, bool):
            msg = f"resume must be a bool, not {type(resume).__name__}"
            raise TypeError(msg)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            _channel_to_wire(self.channel),
            Opcode.OCCUPANCY_DETECTION_ALL_PRESETS,
            0,
            int(self.resume),
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, channel, _, _, state, join = packet.data
        if state not in {0, 1}:
            msg = f"suspend/resume byte 0x{state:02x} isn't 0 or 1"
            raise ValueError(msg)
        return cls(
            area=area,
            channel=_channel_from_wire(channel),
            resume=state == 1,
            join=join,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class HoldTimerAllPresets(LogicalMessage):
    """Suspend or resume the hold timer for every preset (opcode 0x30).

    The opcode list calls it the corridor holdoff hold timer. The layout
    matches 0x31 (`OccupancyDetectionAllPresets`), but which value of byte 5
    means suspend hasn't been checked, so it is kept raw. Byte 4 is unused.
    """

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.HOLD_TIMER_ALL_PRESETS,)

    channel: int | None = None
    """The channel, numbered from 1, or ``None`` for every channel."""

    state_raw: int
    """Byte 5, suspend or resume. 0x31 uses 0x01 for resume and 0x00 for
    suspend; this opcode probably does too."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_channel_or_all(self.channel)
        _check_int("state_raw", self.state_raw, 0, 0xFF)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            _channel_to_wire(self.channel),
            Opcode.HOLD_TIMER_ALL_PRESETS,
            0,
            self.state_raw,
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, channel, _, _, state, join = packet.data
        return cls(
            area=area, channel=_channel_from_wire(channel), state_raw=state, join=join
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class _OccupancyDetectionMessage(LogicalMessage):
    """Suspend or resume occupancy detection for the current preset."""

    opcode: ClassVar[int]

    channel: int | None = None
    """The channel, numbered from 1, or ``None`` for every channel."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_channel_or_all(self.channel)

    @override
    def to_packet(self) -> Packet:
        return self._packet(_channel_to_wire(self.channel), self.opcode, 0, 0)

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, channel, _, _, _, join = packet.data
        return cls(area=area, channel=_channel_from_wire(channel), join=join)


@dataclass(frozen=True, slots=True, kw_only=True)
class SuspendOccupancyDetection(_OccupancyDetectionMessage):
    """Suspend occupancy detection for the current preset (opcode 0x3A).

    `OccupancyDetectionAllPresets` does the same for every preset.
    """

    opcode: ClassVar[int] = Opcode.SUSPEND_OCCUPANCY_DETECTION
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.SUSPEND_OCCUPANCY_DETECTION,)


@dataclass(frozen=True, slots=True, kw_only=True)
class ResumeOccupancyDetection(_OccupancyDetectionMessage):
    """Resume occupancy detection for the current preset (opcode 0x3B)."""

    opcode: ClassVar[int] = Opcode.RESUME_OCCUPANCY_DETECTION
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.RESUME_OCCUPANCY_DETECTION,)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProgramLevelsToPreset(LogicalMessage):
    """Store an area's current channel levels as a preset (opcode 0x09).

    This overwrites the preset stored in the devices. Unlike
    ``AreaCommand(command=PROGRAM_CURRENT_PRESET)`` (0x08), it names the
    preset. Byte 2 is the preset counted from 0, as in `PresetReport`; bytes
    4-5 are unused.
    """

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.PROGRAM_LEVELS_TO_PRESET,)

    preset: int
    """The preset to store, 1-256."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_int("preset", self.preset, 1, 256)

    @override
    def to_packet(self) -> Packet:
        return self._packet(self.preset - 1, Opcode.PROGRAM_LEVELS_TO_PRESET, 0, 0)

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, preset, _, _, _, join = packet.data
        return cls(area=area, preset=preset + 1, join=join)


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
class RequestSignon(PhysicalMessage):
    """Ask a device to sign on: Request Device Status, "Do D2 Signon".

    Physical opcode 0x40 with sub-opcode 0x0A in byte 6. The device answers
    with a DyNet 2 `Dynet2SignonReply`, which `DynetClient.query` waits for.
    Device code 0xAA box 0x55 addresses every device, but at one site no
    device answered that, while each answered when addressed by itself. A 0x40
    packet with any other sub-opcode decodes as `UnknownMessage`.
    """

    opcodes: ClassVar[tuple[int, ...]] = (PhysicalOpcode.REQUEST_DEVICE_STATUS,)

    @property
    def to_all_devices(self) -> bool:
        """Whether this is addressed to every device (0xAA box 0x55)."""
        return (self.device_code, self.box) == _ALL_PHYSICAL_DEVICES

    @override
    def to_packet(self) -> Packet:
        return self._packet(PhysicalOpcode.REQUEST_DEVICE_STATUS, 0, 0, _DO_D2_SIGNON)

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, device_code, box, _, _, _, sub_opcode = packet.data
        if sub_opcode != _DO_D2_SIGNON:
            msg = f"Request Device Status sub-opcode 0x{sub_opcode:02x} isn't sign-on"
            raise ValueError(msg)
        return cls(device_code=device_code, box=box)


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceIdentify(PhysicalMessage):
    """A device saying what it is (physical opcode 0x00).

    Devices send these in bursts, for example on start-up. The official DyNet
    network monitor shows ``5c b3 2a 00 02 11 00`` as "Device D5Sen (0xB3)
    Box 42 v02.11". It doesn't show byte 6 (boot / sign-on), so that is kept
    raw.
    """

    opcodes: ClassVar[tuple[int, ...]] = (PhysicalOpcode.DEVICE_IDENTIFY,)

    firmware_major: int
    """The raw firmware major byte. Versions are written in hex; see
    `firmware_version`."""
    firmware_minor: int
    boot_raw: int = 0
    """Byte 6, "Boot / Signon" in the opcode list."""

    @override
    def __post_init__(self) -> None:
        PhysicalMessage.__post_init__(self)
        _check_int("firmware_major", self.firmware_major, 0, 0xFF)
        _check_int("firmware_minor", self.firmware_minor, 0, 0xFF)
        _check_int("boot_raw", self.boot_raw, 0, 0xFF)

    @property
    def firmware_version(self) -> str:
        """The firmware version as the network monitor prints it, e.g. ``02.11``."""
        return f"{self.firmware_major:02x}.{self.firmware_minor:02x}"

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            PhysicalOpcode.DEVICE_IDENTIFY,
            self.firmware_major,
            self.firmware_minor,
            self.boot_raw,
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, device_code, box, _, major, minor, boot = packet.data
        return cls(
            device_code=device_code,
            box=box,
            firmware_major=major,
            firmware_minor=minor,
            boot_raw=boot,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceStatusReport(PhysicalMessage):
    """A device status report (physical opcode 0x41 Report Device Status).

    The answer to physical 0x40 Request Device Status. Byte 6 is the
    sub-opcode, the same as in the request (0x0A is "Do D2 Signon"). What
    the two data bytes mean depends on the sub-opcode, so they are kept raw.
    The official DyNet network monitor shows ``5c aa 55 41 00 00 0a`` as
    "Report Device Status - Do D2 Signon (data 1 0x00, data 2 0x00)",
    addressed to all devices.
    """

    opcodes: ClassVar[tuple[int, ...]] = (PhysicalOpcode.REPORT_DEVICE_STATUS,)

    sub_opcode: int
    data1_raw: int = 0
    data2_raw: int = 0

    @override
    def __post_init__(self) -> None:
        PhysicalMessage.__post_init__(self)
        _check_int("sub_opcode", self.sub_opcode, 0, 0xFF)
        _check_int("data1_raw", self.data1_raw, 0, 0xFF)
        _check_int("data2_raw", self.data2_raw, 0, 0xFF)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            PhysicalOpcode.REPORT_DEVICE_STATUS,
            self.data1_raw,
            self.data2_raw,
            self.sub_opcode,
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, device_code, box, _, data1, data2, sub_opcode = packet.data
        return cls(
            device_code=device_code,
            box=box,
            sub_opcode=sub_opcode,
            data1_raw=data1,
            data2_raw=data2,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestFirmwareVersion(PhysicalMessage):
    """Ask a device for its firmware version (physical opcode 0x80).

    Bytes 4-5 are unused. The device answers with a `DeviceIdentify` from its
    own address, which carries the firmware major and minor version. At one
    site, 45 devices of five kinds all answered within about 250 ms, and each
    version matched the site's System Builder job.
    """

    opcodes: ClassVar[tuple[int, ...]] = (PhysicalOpcode.REQUEST_FIRMWARE_VERSION,)

    processor_id: int = 0
    """Byte 6: which processor in the device, for devices with more than one."""

    @override
    def __post_init__(self) -> None:
        PhysicalMessage.__post_init__(self)
        _check_int("processor_id", self.processor_id, 0, 0xFF)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            PhysicalOpcode.REQUEST_FIRMWARE_VERSION, 0, 0, self.processor_id
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, device_code, box, _, _, _, processor_id = packet.data
        return cls(device_code=device_code, box=box, processor_id=processor_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class UnknownMessage(Message):
    """A packet aiodynet has no typed message for. It is passed on unchanged."""

    packet: Packet = field()

    @override
    def to_packet(self) -> Packet:
        return self.packet


# DyNet 2

_REPLY_CLASS = 0x81
_SIGNON_TYPE = 0x01
_SIGNON_REPLY_LENGTH = 20


def _check_packet_size(name: str, value: object) -> None:
    _check_int(name, value, DYNET2_MIN_LENGTH, DYNET2_MAX_LENGTH)
    if cast("int", value) % 4:
        msg = f"{name} must be a multiple of 4, got {value}"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2Message(ABC):
    """Base class for messages carried in DyNet 2 frames.

    These only come from devices. aiodynet can't send DyNet 2, so they aren't
    `Message`s and `DynetClient.send` doesn't take them.
    """

    @abstractmethod
    def to_frame(self) -> Dynet2Frame:
        """Encode the message as a frame, for example to play a device in tests."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2SignonReply(Dynet2Message):
    """A device announcing itself, the DyNet 2 "Reply Device Signon".

    A device sends one when it gets a `RequestSignon` addressed to it. The
    layout, checked field by field against the official DyNet network
    monitor::

        ac 04 | 81 | source: dev box box | target: dev box box | 01 | rr rr
              | firmware: major minor | max rx | max tx | port | slave | C2 C1

    Byte 2 = 0x81 is a reply. A 20-byte reply with byte 9 = 0x01 is taken to be
    a sign-on, because another 0x81 reply seen on the bus has 0x0A there and
    isn't one. That rule rests on one confirmed frame. Bytes 10-11 (``rr``) are
    unknown and are kept raw.
    """

    device_code: int
    """The device type code of the device signing on (0xB3 is a D5Sen)."""
    box: int
    """Its box number, 0-65535."""
    target_device_code: int = DYNET2_ALL_DEVICES[0]
    """Who the reply is addressed to. 0xAA box 0x5555 means every device."""
    target_box: int = DYNET2_ALL_DEVICES[1]
    firmware_major: int
    """The raw firmware major byte. Versions are written in hex, 0x02 0x11 =
    "02.11"; see `firmware_version`."""
    firmware_minor: int
    max_rx_size: int
    """The largest packet the device accepts, in bytes."""
    max_tx_size: int
    """The largest packet the device sends, in bytes."""
    port: int = 0
    slave: int = 0
    """The slave device number."""
    reserved_raw: bytes = b"\x00\x00"
    """Bytes 10-11, meaning unknown."""

    def __post_init__(self) -> None:
        """Check every field fits its byte(s)."""
        for name in (
            "device_code",
            "target_device_code",
            "firmware_major",
            "firmware_minor",
            "port",
            "slave",
        ):
            _check_int(name, getattr(self, name), 0, 0xFF)
        for name in ("box", "target_box"):
            _check_int(name, getattr(self, name), 0, 0xFFFF)
        _check_packet_size("max_rx_size", self.max_rx_size)
        _check_packet_size("max_tx_size", self.max_tx_size)
        reserved: object = self.reserved_raw  # checked at runtime too
        if not isinstance(reserved, bytes):
            msg = f"reserved_raw must be bytes, not {type(reserved).__name__}"
            raise TypeError(msg)
        if len(reserved) != 2:
            msg = f"reserved_raw must be 2 bytes, got {len(reserved)}"
            raise ValueError(msg)

    @property
    def firmware_version(self) -> str:
        """The firmware version as the network monitor prints it, e.g. ``02.11``."""
        return f"{self.firmware_major:02x}.{self.firmware_minor:02x}"

    @property
    def to_all_devices(self) -> bool:
        """Whether the reply is addressed to every device."""
        return (self.target_device_code, self.target_box) == DYNET2_ALL_DEVICES

    @classmethod
    def from_frame(cls, frame: Dynet2Frame) -> Self:
        """Read a sign-on reply. Raises `ValueError` if the frame isn't one."""
        body = frame.body
        if not (
            len(frame) == _SIGNON_REPLY_LENGTH
            and body[0] == _REPLY_CLASS
            and body[7] == _SIGNON_TYPE
        ):
            msg = f"Not a DyNet 2 sign-on reply: {frame.hex()}"
            raise ValueError(msg)
        return cls(
            device_code=body[1],
            box=int.from_bytes(body[2:4]),
            target_device_code=body[4],
            target_box=int.from_bytes(body[5:7]),
            reserved_raw=body[8:10],
            firmware_major=body[10],
            firmware_minor=body[11],
            max_rx_size=(body[12] + 1) * 4,
            max_tx_size=(body[13] + 1) * 4,
            port=body[14],
            slave=body[15],
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        body = (
            bytes((_REPLY_CLASS, self.device_code))
            + self.box.to_bytes(2)
            + bytes((self.target_device_code,))
            + self.target_box.to_bytes(2)
            + bytes((_SIGNON_TYPE,))
            + self.reserved_raw
            + bytes(
                (
                    self.firmware_major,
                    self.firmware_minor,
                    self.max_rx_size // 4 - 1,
                    self.max_tx_size // 4 - 1,
                    self.port,
                    self.slave,
                )
            )
        )
        return Dynet2Frame.build(body)


@dataclass(frozen=True, slots=True, kw_only=True)
class UnknownDynet2Message(Dynet2Message):
    """A DyNet 2 frame aiodynet has no typed message for, passed on unchanged.

    Gateways send one (a greeting) a few times after every new connection.
    """

    frame: Dynet2Frame = field()

    @override
    def to_frame(self) -> Dynet2Frame:
        return self.frame


type AnyDynet1Message = (
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
    | ProgramLevelsToPreset
    | SuspendOccupancyDetection
    | ResumeOccupancyDetection
    | HoldTimerAllPresets
    | OccupancyDetectionAllPresets
    | AreaCascadingSync
    | ClockTime
    | ClockDate
    | RequestSignon
    | RequestFirmwareVersion
    | DeviceStatusReport
    | DeviceIdentify
    | UnknownMessage
)
"""Every message `decode` can return for a `Packet`."""

type AnyDynet2Message = Dynet2SignonReply | UnknownDynet2Message
"""Every message `decode` can return for a `Dynet2Frame`."""

type AnyMessage = AnyDynet1Message | AnyDynet2Message
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
    AreaCascadingSync,
    OccupancyDetectionAllPresets,
    HoldTimerAllPresets,
    SuspendOccupancyDetection,
    ResumeOccupancyDetection,
    ProgramLevelsToPreset,
)

_PHYSICAL_TYPES: tuple[type[PhysicalMessage], ...] = (
    ClockTime,
    ClockDate,
    RequestSignon,
    DeviceIdentify,
    DeviceStatusReport,
    RequestFirmwareVersion,
)

_Decoder = Callable[[Packet], "AnyDynet1Message"]
_DECODERS: dict[tuple[int, int], _Decoder] = {}
for _type in _LOGICAL_TYPES:
    for _opcode in _type.opcodes:
        # Every concrete class is a member of AnyMessage; mypy can't see that
        # through the base-class tuple.
        _DECODERS[SyncType.LOGICAL, _opcode] = cast("_Decoder", _type.from_packet)
for _ptype in _PHYSICAL_TYPES:
    for _opcode in _ptype.opcodes:
        _DECODERS[SyncType.PHYSICAL, _opcode] = cast("_Decoder", _ptype.from_packet)


@overload
def decode(raw: Packet) -> AnyDynet1Message: ...
@overload
def decode(raw: Dynet2Frame) -> AnyDynet2Message: ...
@overload
def decode(raw: Packet | Dynet2Frame) -> AnyMessage: ...
def decode(raw: Packet | Dynet2Frame) -> AnyMessage:
    """Decode a packet or DyNet 2 frame into a typed message.

    Never raises for a valid `Packet` or `Dynet2Frame`: anything without a
    typed message comes back as `UnknownMessage` or `UnknownDynet2Message`.
    """
    if isinstance(raw, Dynet2Frame):
        try:
            return Dynet2SignonReply.from_frame(raw)
        except ValueError:
            return UnknownDynet2Message(frame=raw)
    packet = raw
    decoder = _DECODERS.get((packet.sync, packet.data[3]))
    if decoder is not None:
        try:
            return decoder(packet)
        except ValueError:
            pass  # bytes that don't fit the message, such as a time that isn't BCD
    return UnknownMessage(packet=packet)
