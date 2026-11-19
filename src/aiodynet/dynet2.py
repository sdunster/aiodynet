"""DyNet 2 frames: the variable-length 0xAC frames some gateways pass through.

A DyNet 2 frame is a multiple of four bytes long::

    0xAC | n | body ... | C2 | C1

where the whole frame is ``(n + 1) * 4`` bytes and the last two bytes are a
Fletcher-16 checksum computed modulo 256 over everything before them.
`Dynet2Frame` holds one frame raw. What the body means isn't documented, so
nothing here decodes it.

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


DYNET2_ALL_DEVICES: Final = (0xAA, 0x5555)
"""The DyNet 2 destination (device code, box) that means every device."""

_REPLY_CLASS: Final = 0x81
_SIGNON_TYPE: Final = 0x01
_SIGNON_REPLY_LENGTH: Final = 20


def _check(name: str, value: int, low: int, high: int) -> None:
    if not low <= value <= high:
        msg = f"{name} must be {low}-{high}, got {value}"
        raise InvalidPacketError(msg)


def _check_size(name: str, value: int) -> None:
    if value % 4 or not DYNET2_MIN_LENGTH <= value <= DYNET2_MAX_LENGTH:
        msg = f"{name} must be a multiple of 4 from 4 to 1024, got {value}"
        raise InvalidPacketError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class Dynet2SignonReply:
    """A device announcing itself: the DyNet 2 "Reply Device Signon".

    A device sends one when it gets a DyNet 1 physical 0x40 Request Device
    Status with sub-opcode 0x0A ("Do D2 Signon") addressed to it. The layout,
    checked field by field against the official DyNet network monitor::

        ac 04 | 81 | source: dev box box | target: dev box box | 01 | rr rr
              | firmware: major minor | max rx | max tx | port | slave | C2 C1

    Byte 2 = 0x81 is a reply. A 20-byte reply with byte 9 = 0x01 is taken to be
    a sign-on, because another 0x81 reply seen on the bus has 0x0A there and
    isn't a sign-on. That rule rests on one confirmed frame. Bytes 10-11
    (``rr``) are unknown and are kept raw.
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
        for name in ("device_code", "target_device_code", "port", "slave"):
            _check(name, getattr(self, name), 0, 0xFF)
        for name in ("firmware_major", "firmware_minor"):
            _check(name, getattr(self, name), 0, 0xFF)
        for name in ("box", "target_box"):
            _check(name, getattr(self, name), 0, 0xFFFF)
        _check_size("max_rx_size", self.max_rx_size)
        _check_size("max_tx_size", self.max_tx_size)
        reserved: object = self.reserved_raw  # checked at runtime, for untyped callers
        if not isinstance(reserved, bytes) or len(reserved) != 2:
            msg = f"reserved_raw must be 2 bytes, got {reserved!r}"
            raise InvalidPacketError(msg)

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
        """Read a sign-on reply. Raises `InvalidPacketError` if it isn't one."""
        if not cls.matches(frame):
            msg = f"Not a DyNet 2 sign-on reply: {frame.hex()}"
            raise InvalidPacketError(msg)
        b = frame.body
        return cls(
            device_code=b[1],
            box=int.from_bytes(b[2:4]),
            target_device_code=b[4],
            target_box=int.from_bytes(b[5:7]),
            reserved_raw=b[8:10],
            firmware_major=b[10],
            firmware_minor=b[11],
            max_rx_size=(b[12] + 1) * 4,
            max_tx_size=(b[13] + 1) * 4,
            port=b[14],
            slave=b[15],
        )

    @staticmethod
    def matches(frame: Dynet2Frame) -> bool:
        """Whether `frame` has the shape of a sign-on reply."""
        body = frame.body
        return (
            len(frame) == _SIGNON_REPLY_LENGTH
            and body[0] == _REPLY_CLASS
            and body[7] == _SIGNON_TYPE
        )

    def to_frame(self) -> Dynet2Frame:
        """Build the frame, for example to play a device in tests."""
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


def decode_dynet2(frame: Dynet2Frame) -> Dynet2SignonReply | None:
    """Return the typed meaning of a DyNet 2 frame, or ``None`` if unknown.

    Only sign-on replies are understood so far.
    """
    if Dynet2SignonReply.matches(frame):
        return Dynet2SignonReply.from_frame(frame)
    return None
