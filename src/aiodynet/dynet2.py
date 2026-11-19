"""DyNet 2 frames: the variable-length 0xAC frames some gateways pass through.

A DyNet 2 frame is a multiple of four bytes long::

    0xAC | n | body ... | C2 | C1

where the whole frame is ``(n + 1) * 4`` bytes and the last two bytes are a
Fletcher-16 checksum computed modulo 256 over everything before them.
`Dynet2Frame` holds one frame raw. `aiodynet.decode` turns the frames it
understands into typed messages.

Gateways send these on the same TCP stream as DyNet 1 packets: one greets every
new connection, and devices answer sign-on requests with them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Self, override

from .errors import InvalidPacketError

DYNET2_SYNC: Final = 0xAC
"""The first byte of every DyNet 2 frame."""

DYNET2_MIN_LENGTH: Final = 4
"""The shortest possible frame: sync, length and the two checksum bytes."""

DYNET2_MAX_LENGTH: Final = 1024
"""The longest frame the length byte can describe."""


def dynet2_length(length_byte: int) -> int:
    """Return the total frame length, in bytes, that byte 1 describes."""
    return (length_byte + 1) * 4


def dynet2_checksum(data: bytes | bytearray | memoryview) -> bytes:
    """Return the two checksum bytes for a frame without its checksum.

    The sum is Fletcher-16 taken modulo 256 (not 255): ``A`` adds up the
    bytes and ``B`` adds up each running value of ``A``. The frame ends with
    ``B`` then ``A``.

    >>> dynet2_checksum(bytes.fromhex("ac 02 80 da 00 01 b3 00 2a 0a")).hex()
    'eff0'
    """
    a = b = 0
    for value in data:
        a = (a + value) & 0xFF
        b = (b + a) & 0xFF
    return bytes((b, a))


@dataclass(frozen=True, slots=True)
class Dynet2Frame:
    """One complete DyNet 2 frame, kept as raw bytes.

    Parse one with `from_bytes` or `from_hex`, or build one around a body with
    `build`. A `Dynet2Frame` always has a valid sync byte, length byte and
    checksum.
    """

    data: bytes
    """The whole frame, from the sync byte to the end of the checksum."""

    def __post_init__(self) -> None:
        """Check the sync byte, the length and the checksum."""
        data: object = self.data  # checked at runtime too, for untyped callers
        if not isinstance(data, bytes):
            msg = f"Frame data must be bytes, not {type(data).__name__}"
            raise TypeError(msg)
        if len(data) < 2 or data[0] != DYNET2_SYNC:
            msg = f"A DyNet 2 frame starts with 0x{DYNET2_SYNC:02x}: {data.hex(' ')}"
            raise InvalidPacketError(msg)
        expected_length = dynet2_length(data[1])
        if len(data) != expected_length:
            msg = (
                f"Length byte 0x{data[1]:02x} means {expected_length} bytes, "
                f"got {len(data)}: {data.hex(' ')}"
            )
            raise InvalidPacketError(msg)
        expected = dynet2_checksum(data[:-2])
        if data[-2:] != expected:
            msg = (
                f"Bad checksum in {data.hex(' ')}: got {data[-2:].hex(' ')}, "
                f"expected {expected.hex(' ')}"
            )
            raise InvalidPacketError(msg)

    @classmethod
    def build(cls, body: bytes) -> Self:
        """Build a frame around `body`, adding the sync, length and checksum.

        `body` is everything between the length byte and the checksum, so its
        length must leave the frame a multiple of four bytes.
        """
        total = len(body) + 4
        if total % 4 or total > DYNET2_MAX_LENGTH:
            msg = (
                f"A frame body must be 4n bytes (at most {DYNET2_MAX_LENGTH - 4}), "
                f"got {len(body)}"
            )
            raise InvalidPacketError(msg)
        head = bytes((DYNET2_SYNC, total // 4 - 1)) + body
        return cls(head + dynet2_checksum(head))

    @classmethod
    def from_bytes(cls, raw: bytes | bytearray | memoryview) -> Self:
        """Parse a whole frame and check it."""
        return cls(bytes(raw))

    @classmethod
    def from_hex(cls, text: str) -> Self:
        """Parse a frame written in hex, with or without its checksum.

        Spaces, colons and dashes between bytes are ignored. A frame two bytes
        short of a multiple of four is taken to be missing its checksum, which
        is added; otherwise the checksum is checked.
        """
        cleaned = "".join(text.replace(":", " ").replace("-", " ").split())
        try:
            raw = bytes.fromhex(cleaned)
        except ValueError as err:
            msg = f"Not a hex frame: {text!r}"
            raise InvalidPacketError(msg) from err
        if len(raw) >= 2 and len(raw) % 4 == 2:
            raw += dynet2_checksum(raw)
        return cls(raw)

    @property
    def body(self) -> bytes:
        """The bytes between the length byte and the checksum."""
        return self.data[2:-2]

    @property
    def checksum(self) -> bytes:
        """The two checksum bytes."""
        return self.data[-2:]

    def __bytes__(self) -> bytes:
        """Return the whole frame."""
        return self.data

    def __len__(self) -> int:
        """Return the frame length in bytes, checksum included."""
        return len(self.data)

    def hex(self, sep: str = " ") -> str:
        """Return the frame as hex. Bytes are separated by `sep`."""
        return self.data.hex(sep) if sep else self.data.hex()

    @override
    def __repr__(self) -> str:
        """Return a short, readable representation."""
        return f"Dynet2Frame({self.hex()})"
