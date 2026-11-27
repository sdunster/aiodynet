"""Typed DyNet messages and the codec between them and the wire.

A message says what a packet *means*; a `Packet` is what goes on the wire.
DyNet 2 frames work the same way: `decode` turns a `Dynet2Frame` into a
`Dynet2Message`, and `DynetClient.send` sends one with its `to_frame`.
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
from ipaddress import IPv6Address
from typing import ClassVar, Final, Self, cast, overload, override

from .dynet2 import DYNET2_MAX_LENGTH, DYNET2_MIN_LENGTH, Dynet2Frame
from .packet import Packet, SyncType
from .values import FADE_UNIT, JOIN_ALL, Level, decode_fade, encode_fade

__all__ = [
    "APPLICATION_RUNNING",
    "DEVICE_RESET",
    "DYNET2_ALL_DEVICES",
    "MAIN_EEPROM",
    "MAX_MEMORY_CHUNK",
    "PHYSICAL_LEVEL_FULL",
    "AnyDynet1Message",
    "AnyDynet2Message",
    "AnyMessage",
    "AreaCascadingSync",
    "AreaCommand",
    "AreaCommandType",
    "AreaLinksReport",
    "AreaOff",
    "AreaPresetMessage",
    "AreaToLevel",
    "ChannelLevelReport",
    "ChannelOff",
    "ChannelOn",
    "ClearAreaLinks",
    "ClockDate",
    "ClockTime",
    "DaliTunnelData",
    "DeviceIdentify",
    "DeviceStatusReport",
    "DeviceStatusType",
    "Dynet2BuildNumberReply",
    "Dynet2ChannelLevelReport",
    "Dynet2Command",
    "Dynet2DeviceRequest",
    "Dynet2FadeLevelTemperature",
    "Dynet2MemoryData",
    "Dynet2MemoryErrorReply",
    "Dynet2Message",
    "Dynet2PcbVersionReply",
    "Dynet2ReadMemory",
    "Dynet2RequestChannelLevel",
    "Dynet2SignonReply",
    "Dynet2StatusRequest",
    "Dynet2TunnelData",
    "Dynet2WriteMemory",
    "Dynet2WriteMemoryAck",
    "FadeChannelToLevel",
    "FadeChannelToPreset",
    "FadeScale",
    "HoldTimerAllPresets",
    "LightControlAllPresets",
    "LinearPresetSelect",
    "LogicalMessage",
    "MemoryErrorCode",
    "Message",
    "MotionActivitySync",
    "MotionEventCounts",
    "OccupancyDetectionAllPresets",
    "Opcode",
    "Panic",
    "PhysicalMessage",
    "PhysicalOpcode",
    "PhysicalStateReport",
    "PhysicalStateType",
    "PresetLevelReport",
    "PresetReport",
    "PresetSelect",
    "ProgramLevelsToPreset",
    "RampToLevelExceptOff",
    "RawLightLevelReport",
    "RecallPreviousPreset",
    "RequestAreaLinks",
    "RequestChannelLevel",
    "RequestDeviceStatus",
    "RequestFirmwareVersion",
    "RequestPreset",
    "RequestPresetLevel",
    "RequestSignon",
    "RequestUserPreference",
    "RestoreSavedPreset",
    "ResumeOccupancyDetection",
    "SetAreaLinks",
    "SetChannelLevel",
    "SetJoinLevel",
    "SetPhysicalChannelLevel",
    "SetPresetBank",
    "SetRMask",
    "SetUserPreference",
    "StatusQuery",
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
    RECALL_PREVIOUS_PRESET = 0x0F
    LIGHT_CONTROL_ALL_PRESETS = 0x11
    SET_JOIN_LEVEL = 0x14
    DISABLE_PANELS = 0x15
    ENABLE_PANELS = 0x16
    PANIC = 0x17
    UNPANIC = 0x18
    SET_AREA_LINKS = 0x20
    CLEAR_AREA_LINKS = 0x21
    REPORT_AREA_LINKS = 0x22
    REQUEST_AREA_LINKS = 0x23
    MOTION_ACTIVITY_SYNC = 0x2E
    AREA_CASCADING_SYNC = 0x2F
    HOLD_TIMER_ALL_PRESETS = 0x30
    OCCUPANCY_DETECTION_ALL_PRESETS = 0x31
    SUSPEND_OCCUPANCY_DETECTION = 0x3A
    RESUME_OCCUPANCY_DETECTION = 0x3B
    SET_RMASK = 0x40
    SET_USER_PREFERENCE = 0x48
    REQUEST_USER_PREFERENCE = 0x49
    REPORT_USER_PREFERENCE = 0x4A
    REPORT_CHANNEL_LEVEL = 0x60
    REQUEST_CHANNEL_LEVEL = 0x61
    RAMP_TO_LEVEL_EXCEPT_OFF = 0x5F
    REPORT_PRESET = 0x62
    REQUEST_PRESET = 0x63
    SET_PRESET_BANK = 0x64
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
    RAW_LIGHT_LEVEL = 0x0A
    REQUEST_DEVICE_STATUS = 0x40
    REPORT_DEVICE_STATUS = 0x41
    WRITE_TIME = 0x50
    WRITE_DATE = 0x53
    REQUEST_FIRMWARE_VERSION = 0x80
    SET_PHYSICAL_CHANNEL_LEVEL = 0x92
    REPLY_PHYSICAL_STATE = 0xB8


class DeviceStatusType(IntEnum):
    """What a physical 0x40 Request Device Status asks for (byte 6).

    The same value comes back in byte 6 of the 0x41 `DeviceStatusReport`.
    Names are the ones System Builder shows.
    """

    DO_D2_SIGNON = 0x0A
    """Sign on over DyNet 2. Sent as `RequestSignon`; the answer is a DyNet 2
    `Dynet2SignonReply`."""
    BOOTLOADER_RUNNING_REASON = 0x16
    """Whether the device is running its application or its bootloader, and
    why. System Builder asks this before reading a device's memory."""


_DO_D2_SIGNON = DeviceStatusType.DO_D2_SIGNON

APPLICATION_RUNNING: Final = 0x55
"""Data byte 1 of a `BOOTLOADER_RUNNING_REASON` report when the application,
not the bootloader, is running ("This is not the bootloader, this is the
application")."""

DEVICE_RESET: Final = 0x80
"""`DeviceIdentify.boot_raw` when a device announces itself after a reset."""

PHYSICAL_LEVEL_FULL: Final = 0xFE
"""`SetPhysicalChannelLevel.level` for 100 %. 0 is off."""


class PhysicalStateType(IntEnum):
    """What a `PhysicalStateReport` reports (byte 4)."""

    LIGHT_LEVEL = 0x01
    """A sensor's light level in lux."""


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
    """Fade every channel in an area to off (opcode 0x04).

    The official DyNet network monitor calls it "Recall Off Preset". It is
    not preset 5: on hardware it took the area's channels to 0 % while the
    area kept reporting its previous preset.
    """

    opcode: ClassVar[int] = Opcode.AREA_OFF
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.AREA_OFF,)


@dataclass(frozen=True, slots=True, kw_only=True)
class RecallPreviousPreset(_AreaFadeMessage):
    """Recall an area's previous preset (opcode 0x0F).

    System Builder labels ``1c 08 00 0f 00 00`` "Recall Previous Preset with a
    fade of 0.00s".
    """

    opcode: ClassVar[int] = Opcode.RECALL_PREVIOUS_PRESET
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.RECALL_PREVIOUS_PRESET,)


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
class _ResumeAllPresets(LogicalMessage):
    """Suspend or resume a feature for every preset (byte 5: 1 resume, 0 suspend).

    A packet with any other value in byte 5 decodes as `UnknownMessage`.
    Byte 4 is unused.
    """

    opcode: ClassVar[int]

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
            _channel_to_wire(self.channel), self.opcode, 0, int(self.resume)
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
class OccupancyDetectionAllPresets(_ResumeAllPresets):
    """Suspend or resume occupancy detection for every preset (opcode 0x31).

    Byte 5 is 0x01 to resume and 0x00 to suspend, confirmed with the official
    DyNet network monitor. A packet with any other value there decodes as
    `UnknownMessage`. Byte 4 is unused.
    """

    opcode: ClassVar[int] = Opcode.OCCUPANCY_DETECTION_ALL_PRESETS
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.OCCUPANCY_DETECTION_ALL_PRESETS,)


@dataclass(frozen=True, slots=True, kw_only=True)
class LightControlAllPresets(_ResumeAllPresets):
    """Suspend or resume light level control for every preset (opcode 0x11).

    The opcode list calls it "Light Level Compensation Enable / Disable All
    Presets". System Builder shows ``1c 08 ff 11 00 01`` as "Resume Light
    Control for all Presets" and byte 5 = 0x00 as "Suspend".
    """

    opcode: ClassVar[int] = Opcode.LIGHT_CONTROL_ALL_PRESETS
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.LIGHT_CONTROL_ALL_PRESETS,)


@dataclass(frozen=True, slots=True, kw_only=True)
class _AreaByteMessage(LogicalMessage):
    """A logical message with one data byte in byte 2; bytes 4-5 unused."""

    opcode: ClassVar[int]
    field_name: ClassVar[str]

    def _value(self) -> int:
        value: int = getattr(self, self.field_name)
        return value

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_int(self.field_name, self._value(), 0, 0xFF)

    @override
    def to_packet(self) -> Packet:
        return self._packet(self._value(), self.opcode, 0, 0)

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, value, _, _, _, join = packet.data
        return cls(area=area, join=join, **{cls.field_name: value})


@dataclass(frozen=True, slots=True, kw_only=True)
class SetPresetBank(_AreaByteMessage):
    """Set an area's preset bank (opcode 0x64, "Preset Offset / Set Bank").

    System Builder shows ``1c 08 00 64 00 00`` as "Set Bank to 0". Banks
    group presets in eights, as in `PresetSelect`'s bank byte.
    """

    opcode: ClassVar[int] = Opcode.SET_PRESET_BANK
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.SET_PRESET_BANK,)
    field_name: ClassVar[str] = "bank"

    bank: int
    """The bank, 0-255, as sent."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SetRMask(_AreaByteMessage):
    """Set an area's RMask (opcode 0x40).

    System Builder shows ``1c 08 00 40 00 00`` as "Set Rmask 0x00". What the
    mask selects isn't documented in the opcode list.
    """

    opcode: ClassVar[int] = Opcode.SET_RMASK
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.SET_RMASK,)
    field_name: ClassVar[str] = "mask"

    mask: int
    """The mask byte."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SetJoinLevel(_AreaByteMessage):
    """Set the join level in smart panels (opcode 0x14).

    System Builder shows ``1c 08 00 14 00 00`` as "Set Join Level 0".
    """

    opcode: ClassVar[int] = Opcode.SET_JOIN_LEVEL
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.SET_JOIN_LEVEL,)
    field_name: ClassVar[str] = "join_level"

    join_level: int
    """The join level byte."""


@dataclass(frozen=True, slots=True, kw_only=True)
class _AreaLinksMessage(LogicalMessage):
    """Area link bits: bits 1-8 in byte 2, 9-16 in byte 4, 17-24 in byte 5."""

    opcode: ClassVar[int]

    bits: int
    """The 24 link bits, 0-0xFFFFFF. Bit 0 is link 1, as the opcode list
    numbers them ("Area Bits 1-8" in byte 2)."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_int("bits", self.bits, 0, 0xFFFFFF)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            self.bits & 0xFF, self.opcode, self.bits >> 8 & 0xFF, self.bits >> 16
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, low, _, middle, high, join = packet.data
        return cls(area=area, bits=low | middle << 8 | high << 16, join=join)


@dataclass(frozen=True, slots=True, kw_only=True)
class SetAreaLinks(_AreaLinksMessage):
    """Set area link bits (opcode 0x20).

    System Builder shows ``1c 08 ff 20 ff ff`` as "Set Area Links Bits
    0xFFFFFF". Which link each bit stands for depends on the site's base
    link area, so the bits are kept as one number.
    """

    opcode: ClassVar[int] = Opcode.SET_AREA_LINKS
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.SET_AREA_LINKS,)


@dataclass(frozen=True, slots=True, kw_only=True)
class ClearAreaLinks(_AreaLinksMessage):
    """Clear area link bits (opcode 0x21, "Clear Area Link Bits")."""

    opcode: ClassVar[int] = Opcode.CLEAR_AREA_LINKS
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.CLEAR_AREA_LINKS,)


@dataclass(frozen=True, slots=True, kw_only=True)
class AreaLinksReport(_AreaLinksMessage):
    """An area's link bits (opcode 0x22, "Reply Area Link Bits").

    The answer to `RequestAreaLinks`. Two devices answered one request for
    area 8 at one site, both with no links.
    """

    opcode: ClassVar[int] = Opcode.REPORT_AREA_LINKS
    opcodes: ClassVar[tuple[int, ...]] = (Opcode.REPORT_AREA_LINKS,)


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestAreaLinks(LogicalMessage):
    """Ask an area for its link bits (opcode 0x23); answered by `AreaLinksReport`."""

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.REQUEST_AREA_LINKS,)

    @override
    def to_packet(self) -> Packet:
        return self._packet(0, Opcode.REQUEST_AREA_LINKS, 0, 0)

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, _, _, _, _, join = packet.data
        return cls(area=area, join=join)


@dataclass(frozen=True, slots=True, kw_only=True)
class RampToLevelExceptOff(LogicalMessage):
    """Ramp channels to a level, leaving out channels at 0 % (opcode 0x5F).

    The opcode list calls it "Ramp Channel / Area to Level Except for
    Channels at 0% or Don't Care". System Builder shows
    ``1c 08 ff 5f 00 00`` as "Ramp Channels toward Exclude with a ramp rate of
    0.00s", channel "All". Only that packet has been seen, so the level and
    ramp rate are kept raw.
    """

    opcodes: ClassVar[tuple[int, ...]] = (Opcode.RAMP_TO_LEVEL_EXCEPT_OFF,)

    channel: int | None = None
    """The channel, numbered from 1, or ``None`` for every channel."""
    level_raw: int = 0
    """Byte 4, the level."""
    ramp_raw: int = 0
    """Byte 5, the ramp rate (0 was shown as 0.00 s)."""

    @override
    def __post_init__(self) -> None:
        LogicalMessage.__post_init__(self)
        _check_channel_or_all(self.channel)
        _check_int("level_raw", self.level_raw, 0, 0xFF)
        _check_int("ramp_raw", self.ramp_raw, 0, 0xFF)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            _channel_to_wire(self.channel),
            Opcode.RAMP_TO_LEVEL_EXCEPT_OFF,
            self.level_raw,
            self.ramp_raw,
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, area, channel, _, level, ramp, join = packet.data
        return cls(
            area=area,
            channel=_channel_from_wire(channel),
            level_raw=level,
            ramp_raw=ramp,
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
class RequestDeviceStatus(PhysicalMessage):
    """Ask a device for one kind of status: physical 0x40 Request Device Status.

    Byte 6 says which (`DeviceStatusType`); bytes 4-5 are unused. The device
    answers with a `DeviceStatusReport` carrying the same type. Sign-on
    (0x0A) has its own class, `RequestSignon`, because a DyNet 2 frame
    answers it, so this class refuses that type.

    ``5c b3 1d 40 00 00 16`` ("Request Device Status - Bootloader running
    Reason") got ``5c b3 1d 41 55 ff 16`` back from a D5Sen within 40 ms.
    """

    opcodes: ClassVar[tuple[int, ...]] = (PhysicalOpcode.REQUEST_DEVICE_STATUS,)

    status_type: int
    """What to ask for, 0-255. `DeviceStatusType` names the known ones."""

    @override
    def __post_init__(self) -> None:
        PhysicalMessage.__post_init__(self)
        _check_int("status_type", self.status_type, 0, 0xFF)
        if self.status_type == _DO_D2_SIGNON:
            msg = "status_type 0x0a is sign-on: use RequestSignon"
            raise ValueError(msg)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            PhysicalOpcode.REQUEST_DEVICE_STATUS, 0, 0, self.status_type
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, device_code, box, _, _, _, status_type = packet.data
        return cls(device_code=device_code, box=box, status_type=status_type)


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceIdentify(PhysicalMessage):
    """A device saying what it is (physical opcode 0x00).

    Devices send these in bursts, for example on start-up. The official DyNet
    network monitor shows ``5c b3 2a 00 02 11 00`` as "Device D5Sen (0xB3)
    Box 42 v02.11". Byte 6 (boot / sign-on) is kept raw. System Builder
    shows 0x00 as "Device Identify" and 0x80 (`DEVICE_RESET`) as "Device
    Reset": a device sends 0x80 as it starts up after a reset command, and
    0x00 at other times (a D5Sen also sends one when it sees a flash of
    light).
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

    @property
    def after_reset(self) -> bool:
        """Whether the device sent this as it started up after a reset."""
        return self.boot_raw == DEVICE_RESET

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

    The answer to physical 0x40 Request Device Status (`RequestDeviceStatus`).
    Byte 6 is the sub-opcode, the same as in the request (see
    `DeviceStatusType`). What the two data bytes mean depends on the
    sub-opcode, so they are kept raw. The official DyNet network monitor
    shows ``5c aa 55 41 00 00 0a`` as "Report Device Status - Do D2 Signon
    (data 1 0x00, data 2 0x00)", addressed to all devices, and
    ``5c b3 1d 41 55 ff 16`` as "Report Device Status - Bootloader running
    Reason (This is not the bootloader, this is the application)".
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

    @property
    def application_running(self) -> bool | None:
        """Whether the application (not the bootloader) is running.

        Only for a `BOOTLOADER_RUNNING_REASON` report; `None` for any other.
        Only `APPLICATION_RUNNING` has been seen. Any other value is taken to
        mean the bootloader is running, with data byte 1 as its reason.
        """
        if self.sub_opcode != DeviceStatusType.BOOTLOADER_RUNNING_REASON:
            return None
        return self.data1_raw == APPLICATION_RUNNING

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
class SetPhysicalChannelLevel(PhysicalMessage):
    """Set one output channel of one device (physical 0x92).

    ``5c dev box 92 channel level fade``: this addresses a device's own
    channel directly, not a logical area. System Builder's "flash channel"
    sends it, alternating 0 and 100 %, and shows ``5c 4c 05 92 03 fe 00``
    as "Set Physical Channel Level (Channel 3, Level 100%, Fade 0.00s)".

    Unlike logical channel levels, the level is **not inverted**: 0 is off
    and 0xFE (`PHYSICAL_LEVEL_FULL`) is 100 %. Only those two values have
    been seen; `percent` assumes the scale between them is linear. The
    channel is the number System Builder shows, sent as is. The fade byte's
    unit is unknown (only 0, "0.00s", has been seen), so it is kept raw.

    When System Builder fades a DALI controller's channel with a DyNet 2
    "Fade Physical Channel Level" frame, the gateway passes it on to other
    clients as this packet.
    """

    opcodes: ClassVar[tuple[int, ...]] = (PhysicalOpcode.SET_PHYSICAL_CHANNEL_LEVEL,)

    channel: int
    """The device's channel number, 0-255, as System Builder shows it."""
    level: int
    """0 (off) to 0xFE (100 %). 0xFF has not been seen."""
    fade_raw: int = 0
    """Byte 6. Only 0 (no fade) has been seen."""

    @override
    def __post_init__(self) -> None:
        PhysicalMessage.__post_init__(self)
        _check_int("channel", self.channel, 0, 0xFF)
        _check_int("level", self.level, 0, 0xFF)
        _check_int("fade_raw", self.fade_raw, 0, 0xFF)

    @classmethod
    def from_percent(
        cls, *, device_code: int, box: int, channel: int, percent: float
    ) -> Self:
        """Build one from a percentage (0-100), with no fade."""
        if not 0 <= percent <= 100:
            msg = f"percent must be 0-100, got {percent}"
            raise ValueError(msg)
        level = round(percent * PHYSICAL_LEVEL_FULL / 100)
        return cls(device_code=device_code, box=box, channel=channel, level=level)

    @property
    def percent(self) -> float:
        """The level as a percentage, assuming a linear scale."""
        return self.level * 100 / PHYSICAL_LEVEL_FULL

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            PhysicalOpcode.SET_PHYSICAL_CHANNEL_LEVEL,
            self.channel,
            self.level,
            self.fade_raw,
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, device_code, box, _, channel, level, fade = packet.data
        return cls(
            device_code=device_code,
            box=box,
            channel=channel,
            level=level,
            fade_raw=fade,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class PhysicalStateReport(PhysicalMessage):
    """A device reporting a reading: physical 0xB8 "Reply Present Physical State".

    ``5c dev box b8 type value_high value_low``. A D5Sen sends one in answer
    to a DyNet 2 PE cell status poll (request type 0x82), and System
    Builder shows ``5c b3 2a b8 01 06 5b`` as "Reply Present Physical State -
    Light Level 1627 Lux". Other types are kept as the raw 16-bit value.
    """

    opcodes: ClassVar[tuple[int, ...]] = (PhysicalOpcode.REPLY_PHYSICAL_STATE,)

    state_type: int
    """What the value is (`PhysicalStateType`)."""
    value: int
    """The reading, 0-65535 (bytes 5-6, big-endian)."""

    @override
    def __post_init__(self) -> None:
        PhysicalMessage.__post_init__(self)
        _check_int("state_type", self.state_type, 0, 0xFF)
        _check_int("value", self.value, 0, 0xFFFF)

    @property
    def lux(self) -> int | None:
        """The light level in lux, or `None` if this reports something else."""
        return self.value if self.state_type == PhysicalStateType.LIGHT_LEVEL else None

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            PhysicalOpcode.REPLY_PHYSICAL_STATE,
            self.state_type,
            self.value >> 8,
            self.value & 0xFF,
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, device_code, box, _, state_type, high, low = packet.data
        return cls(
            device_code=device_code,
            box=box,
            state_type=state_type,
            value=high << 8 | low,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RawLightLevelReport(PhysicalMessage):
    """A sensor's raw light level (physical 0x0A).

    A D5Sen sends one in answer to a DyNet 2 "PE Cell status - Filtered Raw
    LL" poll (`StatusQuery.PE_CELL_RAW_LIGHT_LEVEL`). System Builder labels
    ``5c b3 1d 0a 00 00 01`` "Light Level Request, Filtered raw illuminance
    value (Level: 0x01)", even though the sensor sends it. Byte 4 was always
    0 and is kept raw. Only levels up to 0x10 have been seen (at dusk and
    night), so byte 5 is assumed to be the high byte of a 16-bit level.
    """

    opcodes: ClassVar[tuple[int, ...]] = (PhysicalOpcode.RAW_LIGHT_LEVEL,)

    level: int
    """The filtered raw illuminance, 0-65535 (unitless)."""
    reserved_raw: int = 0
    """Byte 4."""

    @override
    def __post_init__(self) -> None:
        PhysicalMessage.__post_init__(self)
        _check_int("level", self.level, 0, 0xFFFF)
        _check_int("reserved_raw", self.reserved_raw, 0, 0xFF)

    @override
    def to_packet(self) -> Packet:
        return self._packet(
            PhysicalOpcode.RAW_LIGHT_LEVEL,
            self.reserved_raw,
            self.level >> 8,
            self.level & 0xFF,
        )

    @override
    @classmethod
    def from_packet(cls, packet: Packet) -> Self:
        _, device_code, box, _, reserved, high, low = packet.data
        return cls(
            device_code=device_code,
            box=box,
            level=high << 8 | low,
            reserved_raw=reserved,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class UnknownMessage(Message):
    """A packet aiodynet has no typed message for. It is passed on unchanged."""

    packet: Packet = field()

    @override
    def to_packet(self) -> Packet:
        return self.packet


# DyNet 2

_REQUEST_CLASS = 0x80
_REPLY_CLASS = 0x81
_SIGNON_TYPE = 0x01
_BOOTLOADER_SIGNON_TYPE = 0x02
_DEVICE_REQUEST_LENGTH = 12
_SHORT_REPLY_LENGTH = 16
_BUILD_REPLY_CODE = 0x06
_PCB_REPLY_CODE = 0x0F
_READ_MEMORY = 0x9A
_MEMORY_DATA = 0x9B
_WRITE_MEMORY = 0x9C
_WRITE_MEMORY_ACK = 0x9D
_MEMORY_HEADER_LENGTH = 13
"""Body bytes before a memory frame's data: type, two addresses, selector,
start address and length."""
_MEMORY_REQUEST_LENGTH = 20
_MEMORY_ERROR_FLAG = 0x10
"""Set in a memory read reply's first selector byte when the device refused."""
_MEMORY_ERROR_LENGTH = 16
"""Body length of a memory error reply: the header, the code and padding."""
_STATUS_REQUEST = 0x82
_STATUS_REQUEST_LENGTH = 20
_TUNNEL_DATA = 0xA3
_CHANNEL_LEVEL_REQUEST = 0x22
_CHANNEL_LEVEL_REPORT = 0x23
_CHANNEL_LEVEL_REQUEST_LENGTH = 16
_CHANNEL_LEVEL_REPORT_LENGTH = 32
_CHANNEL_LEVEL_KIND = 0x03
_CHANNEL_LEVEL_EXTRA_LENGTH = 10
_FADE_LEVEL_TEMPERATURE = 0x10
_FADE_LEVEL_TEMPERATURE_LENGTH = 24
_FADE_LEVEL_TEMPERATURE_KIND = 0x06
_DYNET2_FADE_UNIT = 0.01
_TUNNEL_HEADER_LENGTH = 12
_DALI_TUNNEL_OPCODE = 0x10
_DALI_HEADER_LENGTH = 5
_METRIC_HEADER = b"\x01\x01"
_METRIC_HEADER_LENGTH = 16
_MOTION_EVENT_COUNT = 0x0013

MAX_MEMORY_CHUNK: Final = 111
"""The most data bytes one memory read or write carries to a device whose
largest packet is 128 bytes. System Builder reads and writes in chunks of at
most this size."""

MAIN_EEPROM: Final = b"\x00\x00"
"""The memory selector System Builder shows as "main device, memory type
EEPROM"."""
_SIGNON_REPLY_LENGTH = 20
_SIGNON_EXTENSION_LENGTH = 24
_SIGNON_IPV6 = slice(5, 21)
_SERIAL_TYPE = 0x0A
_SERIAL_REQUEST_LENGTH = 12
_SERIAL_REPLY_LENGTH = 20


def _check_packet_size(name: str, value: object) -> None:
    _check_int(name, value, DYNET2_MIN_LENGTH, DYNET2_MAX_LENGTH)
    if cast("int", value) % 4:
        msg = f"{name} must be a multiple of 4, got {value}"
        raise ValueError(msg)


class Dynet2Command(IntEnum):
    """The commands in a DyNet 2 device request (frame type 0x80, byte 9).

    Names follow System Builder. Each was sent to a D5Sen and an Antumbra
    panel at one site, and the replies matched what System Builder shows.
    """

    RESET = 0x00
    """"Reset the device". It restarts and sends a `DeviceIdentify` with
    `after_reset` set. No other reply."""
    SIGNON = 0x01
    """"Request the device sign on". Answered by a `Dynet2SignonReply`."""
    BOOTLOADER_SIGNON = 0x02
    """Sign on with the bootloader's version. Answered by a
    `Dynet2SignonReply` with `bootloader` set."""
    FIRMWARE_BUILD = 0x06
    """"Request device firmware build number". Answered by a
    `Dynet2BuildNumberReply`."""
    BOOTLOADER_BUILD = 0x07
    """"Request device bootloader firmware build number". Answered by a
    `Dynet2BuildNumberReply` with `bootloader` set."""
    SILENCE = 0x08
    """"Silence. Listen to me": the device stops sending and blinks three
    times. System Builder's "flash" (identify) resends it every 2 s, and
    sends it once before writing to a device's memory. No reply."""
    GO_BACK_TO_WORK = 0x09
    """"Go back to work": ends `SILENCE`. No reply."""
    SERIAL_NUMBER = 0x0A
    """Sent as `Dynet2RequestSerialNumber`; answered by a
    `Dynet2SerialNumberReply`."""
    PCB_VERSION = 0x0F
    """"Request PCB assembly version and revision". Answered by a
    `Dynet2PcbVersionReply`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2Message(ABC):
    """Base class for messages carried in DyNet 2 frames.

    They aren't `Message`s, because they encode to a `Dynet2Frame` rather than
    a `Packet`. `DynetClient.send` takes them too.
    """

    @abstractmethod
    def to_frame(self) -> Dynet2Frame:
        """Encode the message as a frame."""


def _check_raw(name: str, value: object, length: int) -> None:
    if not isinstance(value, bytes):
        msg = f"{name} must be bytes, not {type(value).__name__}"
        raise TypeError(msg)
    if len(value) != length:
        msg = f"{name} must be {length} bytes, got {len(value)}"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2SignonReply(Dynet2Message):
    """A device announcing itself, the DyNet 2 "Reply Device Signon".

    A device sends one when it gets a `RequestSignon` addressed to it, or a
    `Dynet2DeviceRequest` for `SIGNON` or `BOOTLOADER_SIGNON`. The layout,
    checked field by field against the official DyNet network monitor::

        ac 04 | 81 | source: dev box box | target: dev box box | 01 | rr rr
              | firmware: major minor | max rx | max tx | port | slave | C2 C1

    Byte 2 = 0x81 is a reply, and byte 9 echoes the request's command: 0x01
    for the application's sign-on, 0x02 ("Reply Device Bootloader Signing")
    for the bootloader's, whose firmware fields then hold the bootloader's
    version (`bootloader`). Bytes 10-11 (``rr``) are unknown and are kept
    raw.

    A 44-byte form adds 24 bytes before the checksum (`extension_raw`). One
    device sent it, a DALI controller that is also an Ethernet bridge::

        ... | slave | 03 00 00 00 00 | IPv6 address (16 bytes) | 00 00 00 | C2 C1

    The 16 bytes were that device's IPv6 address (see `ipv6_address`), and
    the rest of the frame matched its firmware version. Its source address
    was 0xAA box 0x5555, all devices, not its own, so this reply doesn't say
    which device sent it and `DynetClient.query` doesn't match it to the
    request. What the 0x03 means is unknown.
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
    extension_raw: bytes = b""
    """Bytes 18-41 of the 44-byte form, or empty for the 20-byte form."""
    bootloader: bool = False
    """Whether this is the bootloader's sign-on (byte 9 = 0x02)."""

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
        _check_raw("reserved_raw", self.reserved_raw, 2)
        if self.extension_raw != b"":
            _check_raw("extension_raw", self.extension_raw, _SIGNON_EXTENSION_LENGTH)

    @property
    def ipv6_address(self) -> IPv6Address | None:
        """The IPv6 address in the 44-byte form, or `None` in the 20-byte form."""
        if not self.extension_raw:
            return None
        return IPv6Address(self.extension_raw[_SIGNON_IPV6])

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
            len(frame)
            in (_SIGNON_REPLY_LENGTH, _SIGNON_REPLY_LENGTH + _SIGNON_EXTENSION_LENGTH)
            and body[0] == _REPLY_CLASS
            and body[7] in (_SIGNON_TYPE, _BOOTLOADER_SIGNON_TYPE)
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
            extension_raw=body[16:],
            bootloader=body[7] == _BOOTLOADER_SIGNON_TYPE,
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        body = (
            bytes((_REPLY_CLASS, self.device_code))
            + self.box.to_bytes(2)
            + bytes((self.target_device_code,))
            + self.target_box.to_bytes(2)
            + bytes((_BOOTLOADER_SIGNON_TYPE if self.bootloader else _SIGNON_TYPE,))
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
            + self.extension_raw
        )
        return Dynet2Frame.build(body)


def _check_addresses(message: object, *prefixes: str) -> None:
    for prefix in prefixes:
        _check_int(
            f"{prefix}device_code", getattr(message, f"{prefix}device_code"), 0, 0xFF
        )
        _check_int(f"{prefix}box", getattr(message, f"{prefix}box"), 0, 0xFFFF)


def _address(device_code: int, box: int) -> bytes:
    return bytes((device_code,)) + box.to_bytes(2)


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2RequestSerialNumber(Dynet2Message):
    """Ask a device for its serial number (DyNet 2 request type 0x0A).

    Layout::

        ac 02 | 80 | source: dev box box | target: dev box box | 0a | C2 C1

    Byte 2 = 0x80 is a request. The device answers with a
    `Dynet2SerialNumberReply`. System Builder (device 0xDA box 1) sends one to
    each device it sees sign on or identify itself.
    """

    device_code: int
    """The device being asked."""
    box: int
    source_device_code: int
    """Who is asking."""
    source_box: int

    def __post_init__(self) -> None:
        """Check every field fits its byte(s)."""
        _check_addresses(self, "", "source_")

    @classmethod
    def from_frame(cls, frame: Dynet2Frame) -> Self:
        """Read a serial number request. Raises `ValueError` if the frame isn't one."""
        body = frame.body
        if not (
            len(frame) == _SERIAL_REQUEST_LENGTH
            and body[0] == _REQUEST_CLASS
            and body[7] == _SERIAL_TYPE
        ):
            msg = f"Not a DyNet 2 serial number request: {frame.hex()}"
            raise ValueError(msg)
        return cls(
            source_device_code=body[1],
            source_box=int.from_bytes(body[2:4]),
            device_code=body[4],
            box=int.from_bytes(body[5:7]),
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        return Dynet2Frame.build(
            bytes((_REQUEST_CLASS,))
            + _address(self.source_device_code, self.source_box)
            + _address(self.device_code, self.box)
            + bytes((_SERIAL_TYPE,))
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2SerialNumberReply(Dynet2Message):
    """A device's serial number, the answer to `Dynet2RequestSerialNumber`.

    Layout::

        ac 04 | 81 | source: dev box box | target: dev box box | 0a | rr
              | serial: 4 bytes, big-endian | pp pp pp | C2 C1

    The serials in two replies matched the ones System Builder holds for
    those devices, and 42 of 44 more matched too (the other two devices had
    been replaced since that record was made). Byte 10 (``rr``) was 0x0A in
    every reply seen, and bytes 15-17 (``pp``) were zero. They are kept raw;
    the zeros look like padding to a multiple of four bytes.
    """

    device_code: int
    """The device the serial number belongs to."""
    box: int
    target_device_code: int
    """Who the reply is addressed to: the device that asked."""
    target_box: int
    serial_number: int
    """The serial number, as System Builder shows it."""
    reserved_raw: bytes = b"\x0a"
    """Byte 10, meaning unknown."""
    padding_raw: bytes = b"\x00\x00\x00"
    """Bytes 15-17."""

    def __post_init__(self) -> None:
        """Check every field fits its byte(s)."""
        _check_addresses(self, "", "target_")
        _check_int("serial_number", self.serial_number, 0, 0xFFFFFFFF)
        _check_raw("reserved_raw", self.reserved_raw, 1)
        _check_raw("padding_raw", self.padding_raw, 3)

    @classmethod
    def from_frame(cls, frame: Dynet2Frame) -> Self:
        """Read a serial number reply. Raises `ValueError` if the frame isn't one."""
        body = frame.body
        if not (
            len(frame) == _SERIAL_REPLY_LENGTH
            and body[0] == _REPLY_CLASS
            and body[7] == _SERIAL_TYPE
        ):
            msg = f"Not a DyNet 2 serial number reply: {frame.hex()}"
            raise ValueError(msg)
        return cls(
            device_code=body[1],
            box=int.from_bytes(body[2:4]),
            target_device_code=body[4],
            target_box=int.from_bytes(body[5:7]),
            reserved_raw=body[8:9],
            serial_number=int.from_bytes(body[9:13]),
            padding_raw=body[13:16],
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        return Dynet2Frame.build(
            bytes((_REPLY_CLASS,))
            + _address(self.device_code, self.box)
            + _address(self.target_device_code, self.target_box)
            + bytes((_SERIAL_TYPE,))
            + self.reserved_raw
            + self.serial_number.to_bytes(4)
            + self.padding_raw
        )


def _check_bool(name: str, value: object) -> None:
    if not isinstance(value, bool):
        msg = f"{name} must be a bool, not {type(value).__name__}"
        raise TypeError(msg)


def _reply_body(
    frame: Dynet2Frame, commands: tuple[int, ...], length: int, what: str
) -> bytes:
    """Return the body of a 0x81 reply to one of `commands`, or raise."""
    body = frame.body
    if not (len(frame) == length and body[0] == _REPLY_CLASS and body[7] in commands):
        msg = f"Not a DyNet 2 {what}: {frame.hex()}"
        raise ValueError(msg)
    return body


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2DeviceRequest(Dynet2Message):
    """A command to one device: DyNet 2 request (0x80) with one command byte.

    Layout::

        ac 02 | 80 | source: dev box box | target: dev box box | command | C2 C1

    `command` is a `Dynet2Command`, or any other byte (kept as an int).
    `SERIAL_NUMBER` has its own class, `Dynet2RequestSerialNumber`, so this
    one refuses it. System Builder sends these from device 0xDA box 1, and
    replies are addressed back to the source.
    """

    device_code: int
    """The device the command is for."""
    box: int
    source_device_code: int
    """Who is sending it; replies are addressed here."""
    source_box: int
    command: int
    """What to do, 0-255 (`Dynet2Command`)."""

    def __post_init__(self) -> None:
        """Check every field fits its byte(s)."""
        _check_addresses(self, "", "source_")
        _check_int("command", self.command, 0, 0xFF)
        if self.command == Dynet2Command.SERIAL_NUMBER:
            msg = "command 0x0a is the serial number: use Dynet2RequestSerialNumber"
            raise ValueError(msg)

    @classmethod
    def from_frame(cls, frame: Dynet2Frame) -> Self:
        """Read a device request. Raises `ValueError` if the frame isn't one."""
        body = frame.body
        if not (len(frame) == _DEVICE_REQUEST_LENGTH and body[0] == _REQUEST_CLASS):
            msg = f"Not a DyNet 2 device request: {frame.hex()}"
            raise ValueError(msg)
        return cls(
            source_device_code=body[1],
            source_box=int.from_bytes(body[2:4]),
            device_code=body[4],
            box=int.from_bytes(body[5:7]),
            command=body[7],
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        return Dynet2Frame.build(
            bytes((_REQUEST_CLASS,))
            + _address(self.source_device_code, self.source_box)
            + _address(self.device_code, self.box)
            + bytes((self.command,))
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2BuildNumberReply(Dynet2Message):
    """A device's firmware or bootloader build number.

    The answer to a `Dynet2DeviceRequest` for `FIRMWARE_BUILD` or
    `BOOTLOADER_BUILD`. Layout::

        ac 03 | 81 | source: dev box box | target: dev box box | 06 or 07 | 06
              | 00 or 01 | build: 2 bytes | C2 C1

    Byte 9 echoes the command. Byte 11 was 0x00 for the firmware and 0x01 for
    the bootloader; a reply whose byte 11 doesn't match its command decodes
    as `UnknownDynet2Message`. System Builder shows both as "Main device".
    Byte 10 was always 0x06 and is kept raw. Build 1 was the only value
    seen, so the byte order is assumed big-endian like the other fields.
    """

    device_code: int
    """The device the build number belongs to."""
    box: int
    target_device_code: int
    """Who the reply is addressed to: the device that asked."""
    target_box: int
    bootloader: bool
    """Whether this is the bootloader's build number, not the firmware's."""
    build: int
    """The build number, 0-65535."""
    reserved_raw: bytes = bytes((_BUILD_REPLY_CODE,))
    """Byte 10."""

    def __post_init__(self) -> None:
        """Check every field fits its byte(s)."""
        _check_addresses(self, "", "target_")
        _check_bool("bootloader", self.bootloader)
        _check_int("build", self.build, 0, 0xFFFF)
        _check_raw("reserved_raw", self.reserved_raw, 1)

    @classmethod
    def from_frame(cls, frame: Dynet2Frame) -> Self:
        """Read a build number reply. Raises `ValueError` if the frame isn't one."""
        commands = (Dynet2Command.FIRMWARE_BUILD, Dynet2Command.BOOTLOADER_BUILD)
        body = _reply_body(frame, commands, _SHORT_REPLY_LENGTH, "build number reply")
        bootloader = body[7] == Dynet2Command.BOOTLOADER_BUILD
        if body[9] != bootloader:
            msg = f"Build number reply byte 11 doesn't match its command: {frame.hex()}"
            raise ValueError(msg)
        return cls(
            device_code=body[1],
            box=int.from_bytes(body[2:4]),
            target_device_code=body[4],
            target_box=int.from_bytes(body[5:7]),
            bootloader=bootloader,
            reserved_raw=body[8:9],
            build=int.from_bytes(body[10:12]),
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        command = (
            Dynet2Command.BOOTLOADER_BUILD
            if self.bootloader
            else Dynet2Command.FIRMWARE_BUILD
        )
        return Dynet2Frame.build(
            bytes((_REPLY_CLASS,))
            + _address(self.device_code, self.box)
            + _address(self.target_device_code, self.target_box)
            + bytes((command,))
            + self.reserved_raw
            + bytes((int(self.bootloader),))
            + self.build.to_bytes(2)
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2PcbVersionReply(Dynet2Message):
    """A device's circuit board (PCB assembly) version and revision.

    The answer to a `Dynet2DeviceRequest` for `PCB_VERSION`. Layout::

        ac 03 | 81 | source: dev box box | target: dev box box | 0f | 0f | 00
              | version | revision | C2 C1

    System Builder shows ``0f 0f 00 01 02`` as "PCB version 1, revision 2",
    which matches the "1.2" its job records for that D5Sen. Bytes 10-11 are
    kept raw (System Builder calls 0x00 in byte 11 the "Main device").
    """

    device_code: int
    """The device the version belongs to."""
    box: int
    target_device_code: int
    """Who the reply is addressed to: the device that asked."""
    target_box: int
    version: int
    revision: int
    reserved_raw: bytes = bytes((_PCB_REPLY_CODE, 0))
    """Bytes 10-11."""

    def __post_init__(self) -> None:
        """Check every field fits its byte(s)."""
        _check_addresses(self, "", "target_")
        _check_int("version", self.version, 0, 0xFF)
        _check_int("revision", self.revision, 0, 0xFF)
        _check_raw("reserved_raw", self.reserved_raw, 2)

    @classmethod
    def from_frame(cls, frame: Dynet2Frame) -> Self:
        """Read a PCB version reply. Raises `ValueError` if the frame isn't one."""
        body = _reply_body(
            frame, (Dynet2Command.PCB_VERSION,), _SHORT_REPLY_LENGTH, "PCB reply"
        )
        return cls(
            device_code=body[1],
            box=int.from_bytes(body[2:4]),
            target_device_code=body[4],
            target_box=int.from_bytes(body[5:7]),
            reserved_raw=body[8:10],
            version=body[10],
            revision=body[11],
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        return Dynet2Frame.build(
            bytes((_REPLY_CLASS,))
            + _address(self.device_code, self.box)
            + _address(self.target_device_code, self.target_box)
            + bytes((Dynet2Command.PCB_VERSION,))
            + self.reserved_raw
            + bytes((self.version, self.revision))
        )


def _pad(body: bytes) -> bytes:
    return body + bytes(-len(body) % 4)


def _check_memory(message: object) -> None:
    _check_raw("selector_raw", getattr(message, "selector_raw"), 2)  # noqa: B009
    _check_int("address", getattr(message, "address"), 0, 0xFFFF)  # noqa: B009


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2ReadMemory(Dynet2Message):
    """Ask a device for bytes of its memory (DyNet 2 type 0x9A).

    Layout::

        ac 04 | 9a | source: dev box box | target: dev box box | selector (2)
              | start address (2) | length (2) | 00 00 00 | C2 C1

    System Builder shows ``00 00 | 00 1a | 00 05`` as "Request Read Data from
    Device Memory - Main device, memory type EEPROM, start address 0x1a,
    data length 5". The device answers with a `Dynet2MemoryData`. Reading
    changes nothing. Ask for at most `MAX_MEMORY_CHUNK` bytes, so the reply
    fits a 128-byte frame.
    """

    device_code: int
    """The device to read from."""
    box: int
    source_device_code: int
    """Who is asking; the reply is addressed here."""
    source_box: int
    address: int
    """The first byte to read, 0-65535."""
    length: int
    """How many bytes, 0-65535."""
    selector_raw: bytes = MAIN_EEPROM
    """Bytes 9-10: which device and memory. `MAIN_EEPROM` is the main
    device's EEPROM; ``02 00`` has been seen once, meaning unknown."""

    def __post_init__(self) -> None:
        """Check every field fits its byte(s)."""
        _check_addresses(self, "", "source_")
        _check_memory(self)
        _check_int("length", self.length, 0, 0xFFFF)

    @classmethod
    def from_frame(cls, frame: Dynet2Frame) -> Self:
        """Read a memory read request. Raises `ValueError` if the frame isn't one."""
        body = frame.body
        if not (
            len(frame) == _MEMORY_REQUEST_LENGTH
            and body[0] == _READ_MEMORY
            and body[_MEMORY_HEADER_LENGTH:] == bytes(3)
        ):
            msg = f"Not a DyNet 2 memory read request: {frame.hex()}"
            raise ValueError(msg)
        return cls(
            source_device_code=body[1],
            source_box=int.from_bytes(body[2:4]),
            device_code=body[4],
            box=int.from_bytes(body[5:7]),
            selector_raw=body[7:9],
            address=int.from_bytes(body[9:11]),
            length=int.from_bytes(body[11:13]),
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        return Dynet2Frame.build(
            _pad(
                bytes((_READ_MEMORY,))
                + _address(self.source_device_code, self.source_box)
                + _address(self.device_code, self.box)
                + self.selector_raw
                + self.address.to_bytes(2)
                + self.length.to_bytes(2)
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class _MemoryData(Dynet2Message):
    """Shared layout of a memory read reply and a memory write."""

    frame_type: ClassVar[int]

    device_code: int
    box: int
    target_device_code: int
    target_box: int
    address: int
    """The first byte, 0-65535."""
    data: bytes
    """The bytes, starting at `address`."""
    selector_raw: bytes = MAIN_EEPROM
    """Bytes 9-10: which device and memory (see `Dynet2ReadMemory`)."""
    trailer_raw: bytes = b""
    """Bytes after the data, if they aren't the zero padding that brings the
    frame to a multiple of four bytes. System Builder shows a reply with only
    that padding as "no error"."""

    def __post_init__(self) -> None:
        """Check every field fits its byte(s)."""
        _check_addresses(self, "", "target_")
        _check_memory(self)
        for name in ("data", "trailer_raw"):
            if not isinstance(getattr(self, name), bytes):
                msg = f"{name} must be bytes, not {type(getattr(self, name)).__name__}"
                raise TypeError(msg)
        if len(self.data) > 0xFFFF:
            msg = f"data must be at most 65535 bytes, got {len(self.data)}"
            raise ValueError(msg)

    @classmethod
    def from_frame(cls, frame: Dynet2Frame) -> Self:
        """Read this kind of frame. Raises `ValueError` if the frame isn't one."""
        body = frame.body
        end = _MEMORY_HEADER_LENGTH + int.from_bytes(body[11:13])
        if not (
            len(body) >= _MEMORY_HEADER_LENGTH
            and body[0] == cls.frame_type
            and end <= len(body)
        ):
            msg = f"Not a DyNet 2 {cls.__name__}: {frame.hex()}"
            raise ValueError(msg)
        trailer = body[end:]
        return cls(
            device_code=body[1],
            box=int.from_bytes(body[2:4]),
            target_device_code=body[4],
            target_box=int.from_bytes(body[5:7]),
            selector_raw=body[7:9],
            address=int.from_bytes(body[9:11]),
            data=body[_MEMORY_HEADER_LENGTH:end],
            trailer_raw=b"" if trailer == bytes(-end % 4) else trailer,
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        return Dynet2Frame.build(
            _pad(
                bytes((self.frame_type,))
                + _address(self.device_code, self.box)
                + _address(self.target_device_code, self.target_box)
                + self.selector_raw
                + self.address.to_bytes(2)
                + len(self.data).to_bytes(2)
                + self.data
                + self.trailer_raw
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2MemoryData(_MemoryData):
    """Bytes of a device's memory, the answer to `Dynet2ReadMemory` (type 0x9B).

    Layout::

        ac nn | 9b | source: dev box box | target: dev box box | selector (2)
              | start address (2) | length (2) | data | zero padding | C2 C1

    `device_code` and `box` are the device the memory belongs to; the target
    is whoever asked. System Builder shows a reply to the read in
    `Dynet2ReadMemory` as "... data length 5, no error". Blank (never
    written) EEPROM reads as 0xFF. A refused read comes back as a
    `Dynet2MemoryErrorReply` instead: one byte, with 0x10 set in the
    selector's first byte.
    """

    frame_type: ClassVar[int] = _MEMORY_DATA


class MemoryErrorCode(IntEnum):
    """Error codes in a `Dynet2MemoryErrorReply`, as System Builder names them."""

    MEMORY_DOES_NOT_EXIST = 19
    """"Failure to read - Memory does not exist": the start address is past
    the end of that memory. A D5Sen sent it for reads starting at 0x8000 and
    0xFF90, so its EEPROM is 32 KiB."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2MemoryErrorReply(Dynet2Message):
    """A device refusing a `Dynet2ReadMemory` (type 0x9B with an error flag).

    Layout::

        ac 04 | 9b | source: dev box box | target: dev box box
              | selector (2), first byte | 0x10 | start address (2) | 00 01
              | error code | 00 00 | C2 C1

    A `Dynet2MemoryData` frame, with 0x10 set in the selector's first byte
    and one data byte: the error code. System Builder shows a reply to a read
    at 0x8000 as "... Start Address : 0x8000, Data Length : 1, Error code :
    19, Detail : Failure to read - Memory does not exist". The device checks
    only the start address: a read that starts in range and runs past the end
    gets 0xFF for the missing bytes, with no error.
    """

    device_code: int
    """The device that refused."""
    box: int
    target_device_code: int
    """Who the reply is addressed to: the device that asked."""
    target_box: int
    address: int
    """The start address of the refused read, 0-65535."""
    error_code: int
    """The error, 0-255. `MemoryErrorCode` names the known ones."""
    selector_raw: bytes = MAIN_EEPROM
    """The request's selector, without the error flag."""

    def __post_init__(self) -> None:
        """Check every field fits its byte(s)."""
        _check_addresses(self, "", "target_")
        _check_memory(self)
        if self.selector_raw[0] & _MEMORY_ERROR_FLAG:
            msg = (
                f"selector_raw must not include the error flag 0x10, "
                f"got {self.selector_raw.hex()}"
            )
            raise ValueError(msg)
        _check_int("error_code", self.error_code, 0, 0xFF)

    @property
    def error(self) -> MemoryErrorCode | None:
        """The error, if `MemoryErrorCode` names it."""
        try:
            return MemoryErrorCode(self.error_code)
        except ValueError:
            return None

    @classmethod
    def from_frame(cls, frame: Dynet2Frame) -> Self:
        """Read a memory error reply. Raises `ValueError` if the frame isn't one."""
        body = frame.body
        if not (
            len(body) == _MEMORY_ERROR_LENGTH
            and body[0] == _MEMORY_DATA
            and body[7] & _MEMORY_ERROR_FLAG
            and body[11:13] == b"\x00\x01"
            and body[14:] == b"\x00\x00"
        ):
            msg = f"Not a DyNet 2 memory error reply: {frame.hex()}"
            raise ValueError(msg)
        return cls(
            device_code=body[1],
            box=int.from_bytes(body[2:4]),
            target_device_code=body[4],
            target_box=int.from_bytes(body[5:7]),
            selector_raw=bytes((body[7] & ~_MEMORY_ERROR_FLAG, body[8])),
            address=int.from_bytes(body[9:11]),
            error_code=body[13],
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        return Dynet2Frame.build(
            bytes((_MEMORY_DATA,))
            + _address(self.device_code, self.box)
            + _address(self.target_device_code, self.target_box)
            + bytes((self.selector_raw[0] | _MEMORY_ERROR_FLAG, self.selector_raw[1]))
            + self.address.to_bytes(2)
            + b"\x00\x01"
            + bytes((self.error_code,))
            + b"\x00\x00"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2WriteMemory(_MemoryData):
    """Write bytes to a device's memory (DyNet 2 type 0x9C).

    The same layout as `Dynet2MemoryData`, with `device_code` and `box` the
    **sender** and the target the device being written. The device answers
    with a `Dynet2WriteMemoryAck`. System Builder writes in chunks of at most
    `MAX_MEMORY_CHUNK` bytes, after a `SILENCE` command, and resets the
    device afterwards. This changes the device's configuration: don't send
    it without knowing what the bytes mean.
    """

    frame_type: ClassVar[int] = _WRITE_MEMORY


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2WriteMemoryAck(Dynet2Message):
    """A device acknowledging a `Dynet2WriteMemory` (type 0x9D).

    Layout::

        ac 04 | 9d | source: dev box box | target: dev box box | selector (2)
              | start address (2) | result (2) | 00 00 00 | C2 C1

    The two bytes where the length would be were 00 00 after every write
    seen (all of them succeeded), so they are kept raw.
    """

    device_code: int
    """The device that was written."""
    box: int
    target_device_code: int
    """Who wrote it."""
    target_box: int
    address: int
    """The start address of the write, 0-65535."""
    selector_raw: bytes = MAIN_EEPROM
    result_raw: bytes = b"\x00\x00"
    """Bytes 13-14; 00 00 after a successful write."""

    def __post_init__(self) -> None:
        """Check every field fits its byte(s)."""
        _check_addresses(self, "", "target_")
        _check_memory(self)
        _check_raw("result_raw", self.result_raw, 2)

    @classmethod
    def from_frame(cls, frame: Dynet2Frame) -> Self:
        """Read a write acknowledgement. Raises `ValueError` if it isn't one."""
        body = frame.body
        if not (
            len(frame) == _MEMORY_REQUEST_LENGTH
            and body[0] == _WRITE_MEMORY_ACK
            and body[_MEMORY_HEADER_LENGTH:] == bytes(3)
        ):
            msg = f"Not a DyNet 2 memory write acknowledgement: {frame.hex()}"
            raise ValueError(msg)
        return cls(
            device_code=body[1],
            box=int.from_bytes(body[2:4]),
            target_device_code=body[4],
            target_box=int.from_bytes(body[5:7]),
            selector_raw=body[7:9],
            address=int.from_bytes(body[9:11]),
            result_raw=body[11:13],
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        return Dynet2Frame.build(
            _pad(
                bytes((_WRITE_MEMORY_ACK,))
                + _address(self.device_code, self.box)
                + _address(self.target_device_code, self.target_box)
                + self.selector_raw
                + self.address.to_bytes(2)
                + self.result_raw
            )
        )


class StatusQuery(IntEnum):
    """What a `Dynet2StatusRequest` asks for: bytes 9-10, as one 16-bit value.

    The high byte picks the function (0x04 PE cell, 0x05 motion detector),
    the low byte what to report. Names follow System Builder.
    """

    WHO_ARE_YOU = 0x000C
    """"Request Who are you". Gateways send it to all devices a few times
    after each new connection, and answer it themselves when a client sends
    it, with a 0x83 frame to that client only (not typed)."""
    PE_CELL_RAW_LIGHT_LEVEL = 0x0401
    """"PE Cell status - Filtered Raw LL". A D5Sen answers with a
    `RawLightLevelReport`."""
    PE_CELL_LIGHT_LEVEL = 0x0403
    """"PE Cell status - filtered LL, user cal". A D5Sen answers with a
    `PhysicalStateReport` giving the light level in lux."""
    MOTION_POWER_LEVEL = 0x0501
    """"Request Motion status - power level". D5Sens never answered it,
    with or without motion."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2StatusRequest(Dynet2Message):
    """Ask a device for a status or reading (DyNet 2 type 0x82).

    Layout::

        ac 04 | 82 | source: dev box box | target: dev box box | query (2)
              | first (2) | last (2) | 00 00 00 | C2 C1

    System Builder shows ``04 03 | 00 01 | 00 01`` as "PE Cell status -
    filtered LL, user cal (Min 1 - Max 1)": `first` and `last` are the range
    of sensors (PE cells, motion detectors) to report on. A D5Sen answered
    each such poll within 60 ms, and every client on the gateway saw the
    answer. Its reply doesn't depend on the source address.
    """

    device_code: int
    """The device being asked."""
    box: int
    source_device_code: int
    """Who is asking."""
    source_box: int
    query: int
    """What to report, 0-65535 (`StatusQuery`)."""
    first: int = 1
    """The first sensor to report on, 0-65535."""
    last: int = 1
    """The last sensor to report on, 0-65535."""

    def __post_init__(self) -> None:
        """Check every field fits its byte(s)."""
        _check_addresses(self, "", "source_")
        for name in ("query", "first", "last"):
            _check_int(name, getattr(self, name), 0, 0xFFFF)

    @classmethod
    def from_frame(cls, frame: Dynet2Frame) -> Self:
        """Read a status request. Raises `ValueError` if the frame isn't one."""
        body = frame.body
        if not (
            len(frame) == _STATUS_REQUEST_LENGTH
            and body[0] == _STATUS_REQUEST
            and body[_MEMORY_HEADER_LENGTH:] == bytes(3)
        ):
            msg = f"Not a DyNet 2 status request: {frame.hex()}"
            raise ValueError(msg)
        return cls(
            source_device_code=body[1],
            source_box=int.from_bytes(body[2:4]),
            device_code=body[4],
            box=int.from_bytes(body[5:7]),
            query=int.from_bytes(body[7:9]),
            first=int.from_bytes(body[9:11]),
            last=int.from_bytes(body[11:13]),
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        return Dynet2Frame.build(
            _pad(
                bytes((_STATUS_REQUEST,))
                + _address(self.source_device_code, self.source_box)
                + _address(self.device_code, self.box)
                + self.query.to_bytes(2)
                + self.first.to_bytes(2)
                + self.last.to_bytes(2)
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class DaliTunnelData:
    """DALI commands carried in a `Dynet2TunnelData` frame.

    System Builder shows ``10 20 01 00 01 a5 09 a5 09`` as "DALI Tunneling
    opcode:0x10, Universe: 2, Galaxy:1, Cookie:1, Format:0x20 0xA509 -
    INITIALISE ... 0xA509 - INITIALISE" (the universe is the frame's
    `Dynet2TunnelData.channel_raw`). Format 0x10 carried one 16-bit DALI
    frame and 0x20 two.
    """

    opcode: int
    format_raw: int
    galaxy: int
    cookie: int
    """Counts up across one System Builder session."""
    frames: bytes
    """The DALI forward frames, 2 bytes each (address byte, then command)."""

    @classmethod
    def from_bytes(cls, data: bytes) -> Self | None:
        """Read DALI tunnel data, or return `None` if `data` isn't that."""
        if len(data) < _DALI_HEADER_LENGTH or data[0] != _DALI_TUNNEL_OPCODE:
            return None
        return cls(
            opcode=data[0],
            format_raw=data[1],
            galaxy=data[2],
            cookie=int.from_bytes(data[3:5]),
            frames=data[_DALI_HEADER_LENGTH:],
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class MotionEventCounts:
    """A sensor's motion event counts, a binary metric in a `Dynet2TunnelData`.

    A D5Sen with statistics turned on (in System Builder) broadcasts one
    every five minutes. System Builder shows the frame as "Binary Metric Data
    - Metric type: Motion event count, Data: [Detection period: 60, Number of
    counts: 5, Counts from earliest to latest: (0,0,0,0,4)], Metric address:
    [DC:0xB3, BN:54], ... Metric id: 3, Metric version: 2.1, Header version:
    1.1". The metric header's layout is read from that one frame, and the
    two bytes it doesn't explain are kept raw (`header_raw`).
    """

    device_code: int
    """The metric's device (its "metric address")."""
    box: int
    period: int
    """Seconds each count covers."""
    counts: tuple[int, ...]
    """Motion events per period, oldest first."""
    metric_id: int
    metric_version: str
    """For example ``2.1``."""
    header_raw: bytes
    """Metric bytes 2 and 6, meaning unknown (01 01 so far)."""

    @classmethod
    def from_bytes(cls, data: bytes) -> Self | None:
        """Read the metric, or return `None` if `data` isn't a motion event count.

        Layout: header version (01 01), ?, metric address (device, box
        box), ?, DID (2), metric id (2), metric type (2, 0x13), metric
        version (2), data length, then period (2), count, and one byte per
        count.
        """
        if (
            len(data) < _METRIC_HEADER_LENGTH + 3
            or data[0:2] != _METRIC_HEADER
            or int.from_bytes(data[11:13]) != _MOTION_EVENT_COUNT
        ):
            return None
        body = data[_METRIC_HEADER_LENGTH:]
        count = body[2]
        if data[15] != len(body) or len(body) != 3 + count:
            return None
        return cls(
            device_code=data[3],
            box=int.from_bytes(data[4:6]),
            period=int.from_bytes(body[0:2]),
            counts=tuple(body[3:]),
            metric_id=int.from_bytes(data[9:11]),
            metric_version=f"{data[13]}.{data[14]}",
            header_raw=bytes((data[2], data[6])),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2TunnelData(Dynet2Message):
    """Data tunnelled to or from a device (DyNet 2 type 0xA3).

    Layout::

        ac nn | a3 | source: dev box box | target: dev box box | channel
              | length (2) | offset (2) | data | zero padding | C2 C1

    System Builder sends DALI commands to a DALI controller this way ("Data
    Tunneling - DALI", see `dali`), and a D5Sen with statistics turned on
    broadcasts its metrics this way (see `motion_event_counts`). Byte 9
    (`channel_raw`) was the DALI universe (2) in the first, and 7 in the
    metrics.
    """

    device_code: int
    """The sender."""
    box: int
    target_device_code: int
    target_box: int
    channel_raw: int
    """Byte 9."""
    data: bytes
    offset: int = 0
    """System Builder's "Data Offset", 0-65535 (always 0 so far)."""
    trailer_raw: bytes = b""
    """Bytes after the data, if they aren't zero padding."""

    def __post_init__(self) -> None:
        """Check every field fits its byte(s)."""
        _check_addresses(self, "", "target_")
        _check_int("channel_raw", self.channel_raw, 0, 0xFF)
        _check_int("offset", self.offset, 0, 0xFFFF)
        for name in ("data", "trailer_raw"):
            if not isinstance(getattr(self, name), bytes):
                msg = f"{name} must be bytes, not {type(getattr(self, name)).__name__}"
                raise TypeError(msg)
        if len(self.data) > 0xFFFF:
            msg = f"data must be at most 65535 bytes, got {len(self.data)}"
            raise ValueError(msg)

    @property
    def dali(self) -> DaliTunnelData | None:
        """The DALI commands this carries, or `None`."""
        return DaliTunnelData.from_bytes(self.data)

    @property
    def motion_event_counts(self) -> MotionEventCounts | None:
        """The motion event count metric this carries, or `None`."""
        return MotionEventCounts.from_bytes(self.data)

    @classmethod
    def from_frame(cls, frame: Dynet2Frame) -> Self:
        """Read tunnel data. Raises `ValueError` if the frame isn't that."""
        body = frame.body
        end = _TUNNEL_HEADER_LENGTH + int.from_bytes(body[8:10])
        if not (
            len(body) >= _TUNNEL_HEADER_LENGTH
            and body[0] == _TUNNEL_DATA
            and end <= len(body)
        ):
            msg = f"Not DyNet 2 tunnel data: {frame.hex()}"
            raise ValueError(msg)
        trailer = body[end:]
        return cls(
            device_code=body[1],
            box=int.from_bytes(body[2:4]),
            target_device_code=body[4],
            target_box=int.from_bytes(body[5:7]),
            channel_raw=body[7],
            offset=int.from_bytes(body[10:12]),
            data=body[_TUNNEL_HEADER_LENGTH:end],
            trailer_raw=b"" if trailer == bytes(-end % 4) else trailer,
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        return Dynet2Frame.build(
            _pad(
                bytes((_TUNNEL_DATA,))
                + _address(self.device_code, self.box)
                + _address(self.target_device_code, self.target_box)
                + bytes((self.channel_raw,))
                + len(self.data).to_bytes(2)
                + self.offset.to_bytes(2)
                + self.data
                + self.trailer_raw
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2RequestChannelLevel(Dynet2Message):
    """Ask a logical channel for its level and colour temperature (type 0x22).

    Layout::

        ac 03 | 22 | source: dev box box | area (2) | join | 03 | channel (2)
              | value (2) | C2 C1

    System Builder shows ``22 da 00 01 | 00 08 | ff | 03 | 00 01 | 00 00`` as
    "Request Channel Level and Temperature" to "Area Rumpus [8] (8), Channel
    Downlights (1), Join 0xFF", "Value: 0". It has no target device: the
    channel's owner answers with a `Dynet2ChannelLevelReport`.

    DyNet 2 sends the channel number as is (``00 01`` is channel 1), unlike
    DyNet 1, which sends channel - 1. Byte 8 (``03``) is kept raw.
    """

    source_device_code: int
    """Who is asking."""
    source_box: int
    area: int
    """The area, 0-65535."""
    channel: int
    """The channel, 0-65535, as System Builder numbers it."""
    join: int = JOIN_ALL
    value_raw: int = 0
    """Bytes 11-12, "Value" (0 when System Builder asks)."""
    reserved_raw: int = _CHANNEL_LEVEL_KIND
    """Byte 8."""

    def __post_init__(self) -> None:
        """Check every field fits its byte(s)."""
        _check_addresses(self, "source_")
        for name in ("area", "channel", "value_raw"):
            _check_int(name, getattr(self, name), 0, 0xFFFF)
        _check_int("join", self.join, 0, 0xFF)
        _check_int("reserved_raw", self.reserved_raw, 0, 0xFF)

    @classmethod
    def from_frame(cls, frame: Dynet2Frame) -> Self:
        """Read a channel level request. Raises `ValueError` if it isn't one."""
        body = frame.body
        if not (
            len(frame) == _CHANNEL_LEVEL_REQUEST_LENGTH
            and body[0] == _CHANNEL_LEVEL_REQUEST
        ):
            msg = f"Not a DyNet 2 channel level request: {frame.hex()}"
            raise ValueError(msg)
        return cls(
            source_device_code=body[1],
            source_box=int.from_bytes(body[2:4]),
            area=int.from_bytes(body[4:6]),
            join=body[6],
            reserved_raw=body[7],
            channel=int.from_bytes(body[8:10]),
            value_raw=int.from_bytes(body[10:12]),
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        return Dynet2Frame.build(
            bytes((_CHANNEL_LEVEL_REQUEST,))
            + _address(self.source_device_code, self.source_box)
            + self.area.to_bytes(2)
            + bytes((self.join, self.reserved_raw))
            + self.channel.to_bytes(2)
            + self.value_raw.to_bytes(2)
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2ChannelLevelReport(Dynet2Message):
    """A logical channel's level and colour temperature (type 0x23).

    Layout::

        ac 07 | 23 | source: dev box box | area (2) | join | 03 | channel (2)
              | value (2) | current level | target level | current K (2)
              | target K (2) | 10 bytes, zero so far | C2 C1

    System Builder shows ``... 7f 7f 0c 85 0c 80`` from a DALI controller as
    "Reply Channel Level and Temperature (Current Level 50%, Target Level
    50%, Current Temperature 3205 Kelvin, Target Temperature 3200 Kelvin)",
    for a tunable-white channel. Levels use the same scale as
    `SetPhysicalChannelLevel`: 0 is off and 0xFE 100 %, not inverted. After
    System Builder set 5990 K, a reply read ``17 88 17 66``: current 6024 K,
    target 5990 K, which confirms the temperatures' order. Both levels were
    equal in every reply seen, so their order is assumed to match.
    """

    device_code: int
    """The device that owns the channel."""
    box: int
    area: int
    channel: int
    current_level: int
    """0 (off) to 0xFE (100 %)."""
    target_level: int
    current_kelvin: int
    """The current colour temperature, in kelvin."""
    target_kelvin: int
    join: int = JOIN_ALL
    value_raw: int = 0
    reserved_raw: int = _CHANNEL_LEVEL_KIND
    extra_raw: bytes = bytes(_CHANNEL_LEVEL_EXTRA_LENGTH)
    """The 10 bytes after the temperatures."""

    def __post_init__(self) -> None:
        """Check every field fits its byte(s)."""
        _check_addresses(self, "")
        for name in ("area", "channel", "value_raw", "current_kelvin", "target_kelvin"):
            _check_int(name, getattr(self, name), 0, 0xFFFF)
        for name in ("join", "reserved_raw", "current_level", "target_level"):
            _check_int(name, getattr(self, name), 0, 0xFF)
        _check_raw("extra_raw", self.extra_raw, _CHANNEL_LEVEL_EXTRA_LENGTH)

    @property
    def current_percent(self) -> float:
        """The current level as a percentage, assuming a linear scale."""
        return self.current_level * 100 / PHYSICAL_LEVEL_FULL

    @property
    def target_percent(self) -> float:
        """The target level as a percentage, assuming a linear scale."""
        return self.target_level * 100 / PHYSICAL_LEVEL_FULL

    @classmethod
    def from_frame(cls, frame: Dynet2Frame) -> Self:
        """Read a channel level report. Raises `ValueError` if it isn't one."""
        body = frame.body
        if not (
            len(frame) == _CHANNEL_LEVEL_REPORT_LENGTH
            and body[0] == _CHANNEL_LEVEL_REPORT
        ):
            msg = f"Not a DyNet 2 channel level report: {frame.hex()}"
            raise ValueError(msg)
        return cls(
            device_code=body[1],
            box=int.from_bytes(body[2:4]),
            area=int.from_bytes(body[4:6]),
            join=body[6],
            reserved_raw=body[7],
            channel=int.from_bytes(body[8:10]),
            value_raw=int.from_bytes(body[10:12]),
            current_level=body[12],
            target_level=body[13],
            current_kelvin=int.from_bytes(body[14:16]),
            target_kelvin=int.from_bytes(body[16:18]),
            extra_raw=body[18:],
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        return Dynet2Frame.build(
            bytes((_CHANNEL_LEVEL_REPORT,))
            + _address(self.device_code, self.box)
            + self.area.to_bytes(2)
            + bytes((self.join, self.reserved_raw))
            + self.channel.to_bytes(2)
            + self.value_raw.to_bytes(2)
            + bytes((self.current_level, self.target_level))
            + self.current_kelvin.to_bytes(2)
            + self.target_kelvin.to_bytes(2)
            + self.extra_raw
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2FadeLevelTemperature(Dynet2Message):
    """Fade a logical channel to a level and colour temperature (type 0x10).

    Layout::

        ac 05 | 10 | source: dev box box | area (2) | join | 06 | channel (2)
              | level | ff | 00 | fade (2) | kelvin (2) | 00 00 00 | C2 C1

    System Builder shows ``... 00 08 | ff | 06 | 00 01 | 7f | ff | 00 | 00 c8 |
    0d a2`` as "Fade to level and Temperature" to "Area Rumpus [8] (8),
    Channel Downlights (1), Join 0xFF": "Level 50%, Temperature 3490 Kelvin,
    Fade 2s (time), Fade all channels". The DALI controller faded its
    tunable-white fittings and later reported the new temperature (a
    `Dynet2ChannelLevelReport`).

    The level is on the `SetPhysicalChannelLevel` scale (0xFE = 100 %). Only
    fades of 2 s (0x00C8) were seen, so `fade` assumes 10 ms steps. Byte 8
    (``06``), byte 12 (``ff``, perhaps "Fade all channels") and byte 13
    (``00``, perhaps the fade unit "(time)") are kept raw.
    """

    source_device_code: int
    """Who is sending it."""
    source_box: int
    area: int
    """The area, 0-65535."""
    channel: int
    """The channel, 0-65535, sent as is (unlike DyNet 1)."""
    level: int
    """0 (off) to 0xFE (100 %)."""
    kelvin: int
    """The colour temperature to fade to, in kelvin."""
    fade_raw: int = 0
    """Bytes 14-15; see `fade`."""
    join: int = JOIN_ALL
    reserved_raw: int = _FADE_LEVEL_TEMPERATURE_KIND
    """Byte 8."""
    flags_raw: bytes = b"\xff\x00"
    """Bytes 12-13."""

    def __post_init__(self) -> None:
        """Check every field fits its byte(s)."""
        _check_addresses(self, "source_")
        for name in ("area", "channel", "kelvin", "fade_raw"):
            _check_int(name, getattr(self, name), 0, 0xFFFF)
        for name in ("join", "reserved_raw", "level"):
            _check_int(name, getattr(self, name), 0, 0xFF)
        _check_raw("flags_raw", self.flags_raw, 2)

    @property
    def fade(self) -> float:
        """The fade in seconds, assuming 10 ms steps."""
        return round(self.fade_raw * _DYNET2_FADE_UNIT, 2)

    @property
    def percent(self) -> float:
        """The level as a percentage, assuming a linear scale."""
        return self.level * 100 / PHYSICAL_LEVEL_FULL

    @classmethod
    def from_frame(cls, frame: Dynet2Frame) -> Self:
        """Read a fade to level and temperature. Raises `ValueError` otherwise."""
        body = frame.body
        if not (
            len(frame) == _FADE_LEVEL_TEMPERATURE_LENGTH
            and body[0] == _FADE_LEVEL_TEMPERATURE
            and body[17:] == bytes(3)
        ):
            msg = f"Not a DyNet 2 fade to level and temperature: {frame.hex()}"
            raise ValueError(msg)
        return cls(
            source_device_code=body[1],
            source_box=int.from_bytes(body[2:4]),
            area=int.from_bytes(body[4:6]),
            join=body[6],
            reserved_raw=body[7],
            channel=int.from_bytes(body[8:10]),
            level=body[10],
            flags_raw=body[11:13],
            fade_raw=int.from_bytes(body[13:15]),
            kelvin=int.from_bytes(body[15:17]),
        )

    @override
    def to_frame(self) -> Dynet2Frame:
        return Dynet2Frame.build(
            _pad(
                bytes((_FADE_LEVEL_TEMPERATURE,))
                + _address(self.source_device_code, self.source_box)
                + self.area.to_bytes(2)
                + bytes((self.join, self.reserved_raw))
                + self.channel.to_bytes(2)
                + bytes((self.level,))
                + self.flags_raw
                + self.fade_raw.to_bytes(2)
                + self.kelvin.to_bytes(2)
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class UnknownDynet2Message(Dynet2Message):
    """A DyNet 2 frame aiodynet has no typed message for, passed on unchanged.

    A gateway's 0x83 answer to "Who are you" is one.
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
    | RecallPreviousPreset
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
    | LightControlAllPresets
    | SetPresetBank
    | SetRMask
    | SetJoinLevel
    | SetAreaLinks
    | ClearAreaLinks
    | AreaLinksReport
    | RequestAreaLinks
    | RampToLevelExceptOff
    | AreaCascadingSync
    | ClockTime
    | ClockDate
    | RequestSignon
    | RequestDeviceStatus
    | RequestFirmwareVersion
    | DeviceStatusReport
    | DeviceIdentify
    | SetPhysicalChannelLevel
    | PhysicalStateReport
    | RawLightLevelReport
    | UnknownMessage
)
"""Every message `decode` can return for a `Packet`."""

type AnyDynet2Message = (
    Dynet2SignonReply
    | Dynet2RequestSerialNumber
    | Dynet2SerialNumberReply
    | Dynet2DeviceRequest
    | Dynet2BuildNumberReply
    | Dynet2PcbVersionReply
    | Dynet2ReadMemory
    | Dynet2MemoryData
    | Dynet2MemoryErrorReply
    | Dynet2WriteMemory
    | Dynet2WriteMemoryAck
    | Dynet2StatusRequest
    | Dynet2TunnelData
    | Dynet2RequestChannelLevel
    | Dynet2ChannelLevelReport
    | Dynet2FadeLevelTemperature
    | UnknownDynet2Message
)
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
    RecallPreviousPreset,
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
    LightControlAllPresets,
    SetPresetBank,
    SetRMask,
    SetJoinLevel,
    SetAreaLinks,
    ClearAreaLinks,
    AreaLinksReport,
    RequestAreaLinks,
    RampToLevelExceptOff,
    HoldTimerAllPresets,
    SuspendOccupancyDetection,
    ResumeOccupancyDetection,
    ProgramLevelsToPreset,
)

_PHYSICAL_TYPES: tuple[type[PhysicalMessage], ...] = (
    ClockTime,
    ClockDate,
    RequestSignon,
    RequestDeviceStatus,
    DeviceIdentify,
    DeviceStatusReport,
    RequestFirmwareVersion,
    SetPhysicalChannelLevel,
    PhysicalStateReport,
    RawLightLevelReport,
)

_Dynet2Decoder = Callable[[Dynet2Frame], "AnyDynet2Message"]
_DYNET2_TYPES: tuple[_Dynet2Decoder, ...] = (
    # The serial number request comes before the generic device request.
    Dynet2SignonReply.from_frame,
    Dynet2RequestSerialNumber.from_frame,
    Dynet2SerialNumberReply.from_frame,
    Dynet2DeviceRequest.from_frame,
    Dynet2BuildNumberReply.from_frame,
    Dynet2PcbVersionReply.from_frame,
    Dynet2ReadMemory.from_frame,
    # A memory error reply is a memory data frame with an error flag.
    Dynet2MemoryErrorReply.from_frame,
    Dynet2MemoryData.from_frame,
    Dynet2WriteMemory.from_frame,
    Dynet2WriteMemoryAck.from_frame,
    Dynet2StatusRequest.from_frame,
    Dynet2TunnelData.from_frame,
    Dynet2RequestChannelLevel.from_frame,
    Dynet2ChannelLevelReport.from_frame,
    Dynet2FadeLevelTemperature.from_frame,
)

_Decoder = Callable[[Packet], "AnyDynet1Message"]
_DECODERS: dict[tuple[int, int], list[_Decoder]] = {}
"""The decoders for each (sync, opcode), tried in order. An opcode can carry
more than one message, told apart by its data bytes (physical 0x40 is both
`RequestSignon` and `RequestDeviceStatus`)."""
for _type in _LOGICAL_TYPES:
    for _opcode in _type.opcodes:
        # Every concrete class is a member of AnyMessage; mypy can't see that
        # through the base-class tuple.
        _DECODERS.setdefault((SyncType.LOGICAL, _opcode), []).append(
            cast("_Decoder", _type.from_packet)
        )
for _ptype in _PHYSICAL_TYPES:
    for _opcode in _ptype.opcodes:
        _DECODERS.setdefault((SyncType.PHYSICAL, _opcode), []).append(
            cast("_Decoder", _ptype.from_packet)
        )


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
        for dynet2_decoder in _DYNET2_TYPES:
            try:
                return dynet2_decoder(raw)
            except ValueError:
                pass
        return UnknownDynet2Message(frame=raw)
    packet = raw
    for decoder in _DECODERS.get((packet.sync, packet.data[3]), ()):
        try:
            return decoder(packet)
        except ValueError:
            pass  # bytes that don't fit the message, such as a time that isn't BCD
    return UnknownMessage(packet=packet)
