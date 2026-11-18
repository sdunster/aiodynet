"""DyNet 1 packets: the 8-byte frames that travel on the bus.

Every DyNet 1 packet is eight bytes::

    sync | b1 | b2 | b3 | b4 | b5 | b6 | checksum

``sync`` says how to read the rest (see `SyncType`), and the checksum makes the
sum of all eight bytes zero modulo 256. For logical (0x1C) packets, ``b1`` is
the area, ``b3`` the opcode and ``b6`` the join; the meaning of ``b2``, ``b4``
and ``b5`` depends on the opcode.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Self, override

from .errors import InvalidPacketError

if TYPE_CHECKING:
    from collections.abc import Iterator

PACKET_LENGTH = 8
"""Length in bytes of every DyNet 1 packet, including the checksum."""


class SyncType(IntEnum):
    """The first byte of a DyNet 1 packet."""

    LOGICAL = 0x1C
    """Logical message: addressed to an area (presets, channel levels, ...)."""

    PHYSICAL = 0x5C
    """Physical message: addressed to a device by device code and box number."""


def checksum(data: bytes) -> int:
    """Return the checksum byte for the first seven bytes of a packet."""
    return -sum(data) & 0xFF


@dataclass(frozen=True, slots=True)
class Packet:
    """One DyNet 1 packet.

    Build one from its seven payload bytes with `Packet.build`, or parse eight
    bytes received from the bus with `Packet.from_bytes`. The checksum is always
    computed for you, so a `Packet` can't hold an invalid one.
    """

    data: bytes
    """The first seven bytes, from the sync byte up to (not including) the
    checksum."""

    def __post_init__(self) -> None:
        """Validate the payload."""
        data: object = self.data  # checked at runtime too, for untyped callers
        if not isinstance(data, bytes):
            msg = f"Packet data must be bytes, not {type(data).__name__}"
            raise TypeError(msg)
        if len(self.data) != PACKET_LENGTH - 1:
            msg = (
                f"Packet data must be {PACKET_LENGTH - 1} bytes "
                f"(without the checksum), got {len(self.data)}"
            )
            raise InvalidPacketError(msg)

    @classmethod
    def build(  # noqa: PLR0913, PLR0917
        cls, sync: int, b1: int, b2: int, b3: int, b4: int, b5: int, b6: int
    ) -> Self:
        """Build a packet from its seven payload bytes."""
        values = (sync, b1, b2, b3, b4, b5, b6)
        for index, value in enumerate(values):
            if not 0 <= value <= 0xFF:
                msg = f"Byte {index} must be 0-255, got {value}"
                raise InvalidPacketError(msg)
        return cls(bytes(values))

    @classmethod
    def from_bytes(cls, raw: bytes | bytearray | memoryview) -> Self:
        """Parse a full 8-byte packet and check its checksum."""
        raw = bytes(raw)
        if len(raw) != PACKET_LENGTH:
            msg = f"A packet is {PACKET_LENGTH} bytes, got {len(raw)}"
            raise InvalidPacketError(msg)
        expected = checksum(raw[:-1])
        if raw[-1] != expected:
            msg = (
                f"Bad checksum in {raw.hex(' ')}: "
                f"got 0x{raw[-1]:02x}, expected 0x{expected:02x}"
            )
            raise InvalidPacketError(msg)
        return cls(raw[:-1])

    @classmethod
    def from_hex(cls, text: str) -> Self:
        """Parse a packet written in hex, with or without its checksum.

        Spaces, colons and dashes between bytes are ignored, so ``"1c 01 00 00
        00 00 ff"`` and ``"1c010000000000ffe4"`` both work. With seven bytes the
        checksum is added; with eight it is checked.
        """
        cleaned = "".join(text.replace(":", " ").replace("-", " ").split())
        try:
            raw = bytes.fromhex(cleaned)
        except ValueError as err:
            msg = f"Not a hex packet: {text!r}"
            raise InvalidPacketError(msg) from err
        if len(raw) == PACKET_LENGTH - 1:
            return cls(raw)
        return cls.from_bytes(raw)

    @property
    def checksum(self) -> int:
        """The checksum byte."""
        return checksum(self.data)

    @property
    def sync(self) -> int:
        """The sync byte. Compare it with `SyncType`."""
        return self.data[0]

    @property
    def is_logical(self) -> bool:
        """Whether this is a logical (area-addressed) packet."""
        return self.data[0] == SyncType.LOGICAL

    def __bytes__(self) -> bytes:
        """Return all eight bytes, ready to write to the bus."""
        return self.data + bytes((self.checksum,))

    def __iter__(self) -> Iterator[int]:
        """Iterate over all eight bytes, including the checksum."""
        return iter(bytes(self))

    def __len__(self) -> int:
        """Return the packet length, always 8."""
        return PACKET_LENGTH

    def __getitem__(self, index: int) -> int:
        """Return one byte (0-7) of the packet, including the checksum."""
        return bytes(self)[index]

    def hex(self, sep: str = " ") -> str:
        """Return the packet as hex, including the checksum.

        Bytes are separated by `sep`; pass ``""`` for none.
        """
        return bytes(self).hex(sep) if sep else bytes(self).hex()

    @override
    def __repr__(self) -> str:
        """Return a short, readable representation."""
        return f"Packet({self.hex()})"
