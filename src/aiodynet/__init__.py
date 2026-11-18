"""Asyncio client for the Signify Dynalite DyNet lighting control bus.

aiodynet reads and writes DyNet 1 packets through a TCP gateway. It knows the
protocol, not your site: there are no devices, rooms or names here, only areas,
channels, presets and levels as they appear on the bus.
"""

from .client import (
    ConnectionCallback,
    DynetClient,
    PacketCallback,
    Received,
    check_connection,
)
from .errors import (
    DynetConnectionError,
    DynetError,
    InvalidPacketError,
    NotConnectedError,
)
from .framing import PacketFramer
from .messages import (
    MAX_CHANNEL_FADE,
    MAX_PRESET_FADE,
    AnyMessage,
    AreaPresetMessage,
    ChannelLevelReport,
    FadeChannelToPreset,
    LinearPresetSelect,
    LogicalMessage,
    Message,
    Opcode,
    PresetReport,
    PresetSelect,
    RequestChannelLevel,
    RequestPreset,
    SetChannelLevel,
    UnknownMessage,
    decode,
)
from .packet import PACKET_LENGTH, Packet, SyncType
from .values import JOIN_ALL, Level

__all__ = [
    "JOIN_ALL",
    "MAX_CHANNEL_FADE",
    "MAX_PRESET_FADE",
    "PACKET_LENGTH",
    "AnyMessage",
    "AreaPresetMessage",
    "ChannelLevelReport",
    "ConnectionCallback",
    "DynetClient",
    "DynetConnectionError",
    "DynetError",
    "FadeChannelToPreset",
    "InvalidPacketError",
    "Level",
    "LinearPresetSelect",
    "LogicalMessage",
    "Message",
    "NotConnectedError",
    "Opcode",
    "Packet",
    "PacketCallback",
    "PacketFramer",
    "PresetReport",
    "PresetSelect",
    "Received",
    "RequestChannelLevel",
    "RequestPreset",
    "SetChannelLevel",
    "SyncType",
    "UnknownMessage",
    "check_connection",
    "decode",
]
