from ipaddress import IPv6Address

import pytest

from aiodynet import (
    DYNET2_ALL_DEVICES,
    Dynet2Frame,
    Dynet2RequestSerialNumber,
    Dynet2SerialNumberReply,
    Dynet2SignonReply,
    Dynet2StatusRequest,
    InvalidPacketError,
    Packet,
    RequestDeviceStatus,
    RequestSignon,
    StatusQuery,
    UnknownDynet2Message,
    decode,
)
from aiodynet.dynet2 import DYNET2_SYNC, dynet2_checksum, dynet2_length

# Captured from the gateway at beachpi on 2026-11-19 (specs/dynet2-framing.md).
GREETING = "ac 04 82 dc 00 5a aa 55 55 00 0c 00 00 00 00 00 00 00 43 c8"
REQUEST = "ac 02 80 da 00 01 b3 00 2a 0a ef f0"
REPLY = "ac 04 81 b3 00 2a da 00 01 0a 0a 03 02 c8 7a 00 00 00 e8 44"


@pytest.mark.parametrize("text", [GREETING, REQUEST, REPLY])
def test_captured_frames_verify(text: str) -> None:
    frame = Dynet2Frame.from_hex(text)
    assert frame.hex() == text
    assert bytes(frame) == bytes.fromhex(text)
    assert len(frame) == dynet2_length(frame.data[1])
    assert frame.checksum == dynet2_checksum(frame.data[:-2])
    assert frame.body == bytes.fromhex(text)[2:-2]


def test_checksum_is_fletcher16_mod_256() -> None:
    # B is the sum of the running sums, A the plain sum; the frame ends B, A.
    assert dynet2_checksum(b"") == b"\x00\x00"
    assert dynet2_checksum(b"\x01\x02") == bytes((1 + 3, 3))
    assert dynet2_checksum(b"\xff\xff") == bytes(((0xFF + 0xFE) & 0xFF, 0xFE))


def test_lengths() -> None:
    assert dynet2_length(0) == 4
    assert dynet2_length(2) == 12
    assert dynet2_length(255) == 1024


def test_build_adds_header_and_checksum() -> None:
    frame = Dynet2Frame.build(bytes.fromhex("80 da 00 01 b3 00 2a 0a"))
    assert frame.hex() == REQUEST
    assert Dynet2Frame.build(b"").hex() == "ac 00 58 ac"
    assert len(Dynet2Frame.build(bytes(1020))) == 1024


@pytest.mark.parametrize("size", [1, 3, 1021, 1024])
def test_build_rejects_bad_body_sizes(size: int) -> None:
    with pytest.raises(InvalidPacketError, match="4n bytes"):
        Dynet2Frame.build(bytes(size))


def test_from_hex_adds_a_missing_checksum() -> None:
    assert Dynet2Frame.from_hex(REQUEST[:-6]).hex() == REQUEST
    assert Dynet2Frame.from_hex("ac:02:80:da:00:01:b3:00:2a:0a").hex() == REQUEST
    assert Dynet2Frame.from_hex("ac-00").hex() == "ac 00 58 ac"


def test_from_bytes() -> None:
    raw = bytearray.fromhex(REPLY)
    assert Dynet2Frame.from_bytes(raw).hex() == REPLY
    assert Dynet2Frame.from_bytes(memoryview(raw)).hex() == REPLY


@pytest.mark.parametrize("index", [-1, -2])
def test_rejects_a_flipped_checksum_byte(index: int) -> None:
    raw = bytearray.fromhex(GREETING)
    raw[index] ^= 0x01
    with pytest.raises(InvalidPacketError, match="Bad checksum"):
        Dynet2Frame.from_bytes(raw)


def test_rejects_a_wrong_length_byte() -> None:
    raw = bytearray.fromhex(GREETING)
    raw[1] = 3
    with pytest.raises(InvalidPacketError, match="means 16 bytes, got 20"):
        Dynet2Frame.from_bytes(raw)


@pytest.mark.parametrize("raw", [b"", b"\xac", b"\x1c\x00\x00\x00"])
def test_rejects_a_missing_sync_byte(raw: bytes) -> None:
    with pytest.raises(InvalidPacketError, match="starts with 0xac"):
        Dynet2Frame(raw)


def test_rejects_non_bytes() -> None:
    with pytest.raises(TypeError, match="must be bytes, not bytearray"):
        Dynet2Frame(bytearray.fromhex(REQUEST))  # type: ignore[arg-type]


def test_rejects_bad_hex() -> None:
    with pytest.raises(InvalidPacketError, match="Not a hex frame"):
        Dynet2Frame.from_hex("ac zz")


def test_hex_and_repr() -> None:
    frame = Dynet2Frame.from_hex(REQUEST)
    assert frame.hex("") == REQUEST.replace(" ", "")
    assert repr(frame) == f"Dynet2Frame({REQUEST})"
    assert frame.data[0] == DYNET2_SYNC


# Sign-on replies. SIGNON was decoded by the official DyNet network monitor as
# "Reply Device Signon (F/W version v02.11, max rx packet size 128, max tx
# packet size 128, port ID 0, slave device no 0)", target all devices.
SIGNON = "ac 04 81 b3 00 2a aa 55 55 01 00 00 02 11 1f 1f 00 00 3f b4"
D5SEN = Dynet2SignonReply(
    device_code=0xB3,
    box=42,
    firmware_major=0x02,
    firmware_minor=0x11,
    max_rx_size=128,
    max_tx_size=128,
)


def test_signon_reply_from_the_network_monitor() -> None:
    frame = Dynet2Frame.from_hex(SIGNON)
    reply = decode(frame)
    assert reply == D5SEN
    assert isinstance(reply, Dynet2SignonReply)
    assert reply.firmware_version == "02.11"
    assert reply.to_all_devices
    assert (reply.target_device_code, reply.target_box) == DYNET2_ALL_DEVICES
    assert (reply.port, reply.slave, reply.reserved_raw) == (0, 0, b"\x00\x00")
    assert D5SEN.to_frame() == frame


def test_greeting_is_who_are_you() -> None:
    frame = Dynet2Frame.from_hex(GREETING)
    message = decode(frame)
    assert message == Dynet2StatusRequest(
        device_code=0xAA,
        box=0x5555,
        source_device_code=0xDC,
        source_box=0x5A,
        query=StatusQuery.WHO_ARE_YOU,
        first=0,
        last=0,
    )
    assert message.to_frame() == frame


def test_gateway_who_are_you_reply_is_unknown() -> None:
    frame = Dynet2Frame.from_hex(
        "ac 04 83 dc 00 5a dc 00 5a 00 00 0d 00 00 00 00 00 00 31 ac"
    )
    message = decode(frame)
    assert message == UnknownDynet2Message(frame=frame)
    assert message.to_frame() == frame


@pytest.mark.parametrize(
    ("cls", "name"),
    [
        (Dynet2SignonReply, "sign-on reply"),
        (Dynet2RequestSerialNumber, "serial number request"),
        (Dynet2SerialNumberReply, "serial number reply"),
    ],
)
@pytest.mark.parametrize("text", [GREETING, REQUEST, REPLY, SIGNON])
def test_each_type_rejects_other_frames(
    cls: type[Dynet2SignonReply | Dynet2RequestSerialNumber | Dynet2SerialNumberReply],
    name: str,
    text: str,
) -> None:
    frame = Dynet2Frame.from_hex(text)
    if isinstance(decode(frame), cls):
        return
    with pytest.raises(ValueError, match=f"Not a DyNet 2 {name}"):
        cls.from_frame(frame)


def test_signon_reply_round_trip_with_every_field_set() -> None:
    reply = Dynet2SignonReply(
        device_code=0x01,
        box=0x1234,
        target_device_code=0xDA,
        target_box=1,
        firmware_major=0xFF,
        firmware_minor=0,
        max_rx_size=4,
        max_tx_size=1024,
        port=3,
        slave=7,
        reserved_raw=b"\xab\xcd",
    )
    assert not reply.to_all_devices
    assert decode(reply.to_frame()) == reply


@pytest.mark.parametrize(
    ("change", "error", "kind"),
    [
        ({"device_code": 256}, "device_code must be 0-255", ValueError),
        ({"box": 65536}, "box must be 0-65535", ValueError),
        ({"firmware_minor": -1}, "firmware_minor must be 0-255", ValueError),
        ({"slave": 300}, "slave must be 0-255", ValueError),
        ({"max_rx_size": 130}, "max_rx_size must be a multiple of 4", ValueError),
        ({"max_tx_size": 0}, "max_tx_size must be 4-1024", ValueError),
        ({"reserved_raw": b"\x00"}, "reserved_raw must be 2 bytes", ValueError),
        ({"reserved_raw": "00"}, "reserved_raw must be bytes", TypeError),
        ({"port": "0"}, "port must be an int", TypeError),
    ],
)
def test_signon_reply_validation(
    change: dict[str, object], error: str, kind: type[Exception]
) -> None:
    fields: dict[str, object] = {
        "device_code": 0xB3,
        "box": 42,
        "firmware_major": 2,
        "firmware_minor": 0x11,
        "max_rx_size": 128,
        "max_tx_size": 128,
    } | change
    with pytest.raises(kind, match=error):
        Dynet2SignonReply(**fields)  # type: ignore[arg-type]


# RequestSignon, a DyNet 1 physical packet. Sent to the D5Sen on a real site,
# where it got the sign-on reply above.


def test_request_signon_packet() -> None:
    request = RequestSignon(device_code=0xB3, box=42)
    assert request.to_packet().hex() == "5c b3 2a 40 00 00 0a 7d"
    assert decode(request.to_packet()) == request
    assert not request.to_all_devices
    assert RequestSignon(device_code=0xAA, box=0x55).to_all_devices


def test_other_device_status_requests_are_not_sign_on() -> None:
    packet = Packet.from_hex("5c b3 2a 40 00 00 04")  # sub-opcode 0x04, PCB link
    assert decode(packet) == RequestDeviceStatus(
        device_code=0xB3, box=42, status_type=4
    )


# Serial numbers. REQUEST and REPLY above are a real request (from System
# Builder, 0xDA box 1) and its answer. The serial numbers in the frames below
# are made up.


def test_captured_serial_number_request() -> None:
    request = decode(Dynet2Frame.from_hex(REQUEST))
    assert request == Dynet2RequestSerialNumber(
        device_code=0xB3, box=42, source_device_code=0xDA, source_box=1
    )
    assert request.to_frame().hex() == REQUEST


def test_captured_serial_number_reply_round_trips() -> None:
    reply = decode(Dynet2Frame.from_hex(REPLY))
    assert isinstance(reply, Dynet2SerialNumberReply)
    assert (reply.device_code, reply.box) == (0xB3, 42)
    assert (reply.target_device_code, reply.target_box) == (0xDA, 1)
    assert reply.to_frame().hex() == REPLY


def test_serial_number_reply() -> None:
    frame = Dynet2Frame.from_hex(
        "ac 04 81 fd 00 03 da 00 01 0a 0a 00 bc 61 4e 00 00 00"
    )
    reply = decode(frame)
    assert reply == Dynet2SerialNumberReply(
        device_code=0xFD,
        box=3,
        target_device_code=0xDA,
        target_box=1,
        serial_number=12345678,
    )
    assert reply.to_frame() == frame


def test_serial_number_reply_round_trip_with_every_field_set() -> None:
    reply = Dynet2SerialNumberReply(
        device_code=0x01,
        box=0xFFFF,
        target_device_code=0xFF,
        target_box=0x1234,
        serial_number=0xFFFFFFFF,
        reserved_raw=b"\x7f",
        padding_raw=b"\x01\x02\x03",
    )
    assert decode(reply.to_frame()) == reply


@pytest.mark.parametrize(
    ("change", "error", "kind"),
    [
        ({"device_code": 256}, "device_code must be 0-255", ValueError),
        ({"target_box": 65536}, "target_box must be 0-65535", ValueError),
        ({"serial_number": 1 << 32}, "serial_number must be 0-4294967295", ValueError),
        ({"serial_number": -1}, "serial_number must be 0-4294967295", ValueError),
        ({"reserved_raw": b""}, "reserved_raw must be 1 bytes", ValueError),
        ({"padding_raw": b"\x00"}, "padding_raw must be 3 bytes", ValueError),
        ({"padding_raw": "000000"}, "padding_raw must be bytes", TypeError),
    ],
)
def test_serial_number_reply_validation(
    change: dict[str, object], error: str, kind: type[Exception]
) -> None:
    fields: dict[str, object] = {
        "device_code": 0xFD,
        "box": 3,
        "target_device_code": 0xDA,
        "target_box": 1,
        "serial_number": 1,
    } | change
    with pytest.raises(kind, match=error):
        Dynet2SerialNumberReply(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"box": 65536}, "box must be 0-65535"),
        ({"source_device_code": -1}, "source_device_code must be 0-255"),
        ({"source_box": 70000}, "source_box must be 0-65535"),
    ],
)
def test_serial_number_request_validation(
    change: dict[str, object], error: str
) -> None:
    fields: dict[str, object] = {
        "device_code": 0xFD,
        "box": 3,
        "source_device_code": 0xDA,
        "source_box": 1,
    } | change
    with pytest.raises(ValueError, match=error):
        Dynet2RequestSerialNumber(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text",
    [
        # Right type byte, wrong class or length.
        "ac 02 81 da 00 01 fd 00 03 0a",
        "ac 04 80 fd 00 03 da 00 01 0a 0a 00 bc 61 4e 00 00 00",
        "ac 04 81 fd 00 03 da 00 01 0b 0a 00 bc 61 4e 00 00 00",
    ],
)
def test_near_misses_decode_as_unknown(text: str) -> None:
    frame = Dynet2Frame.from_hex(text)
    assert decode(frame) == UnknownDynet2Message(frame=frame)


# The 44-byte sign-on, as one DALI controller (also an Ethernet bridge) sent
# it, with its IPv6 address swapped for one from the documentation range.
LONG_SIGNON = (
    "ac 0a 81 aa 55 55 aa 55 55 01 00 00 01 11 86 86 00 00"
    " 03 00 00 00 00 20 01 0d b8 00 00 00 00 00 00 00 00 00 00 00 01 00 00 00"
)


def test_long_signon_reply() -> None:
    frame = Dynet2Frame.from_hex(LONG_SIGNON)
    reply = decode(frame)
    assert isinstance(reply, Dynet2SignonReply)
    assert (reply.device_code, reply.box) == DYNET2_ALL_DEVICES
    assert reply.to_all_devices
    assert reply.firmware_version == "01.11"
    assert (reply.max_rx_size, reply.max_tx_size) == (540, 540)
    assert reply.ipv6_address == IPv6Address("2001:db8::1")
    assert reply.extension_raw[:5] == bytes.fromhex("03 00 00 00 00")
    assert reply.to_frame() == frame


def test_short_signon_reply_has_no_ipv6_address() -> None:
    assert D5SEN.ipv6_address is None
    assert D5SEN.extension_raw == b""


@pytest.mark.parametrize(
    ("extension", "error", "kind"),
    [
        (bytes(23), "extension_raw must be 24 bytes, got 23", ValueError),
        (bytes(25), "extension_raw must be 24 bytes, got 25", ValueError),
        ("00" * 24, "extension_raw must be bytes", TypeError),
    ],
)
def test_signon_extension_validation(
    extension: object, error: str, kind: type[Exception]
) -> None:
    with pytest.raises(kind, match=error):
        Dynet2SignonReply(
            device_code=0x64,
            box=42,
            firmware_major=1,
            firmware_minor=0x11,
            max_rx_size=128,
            max_tx_size=128,
            extension_raw=extension,  # type: ignore[arg-type]
        )


def test_other_signon_lengths_decode_as_unknown() -> None:
    frame = Dynet2Frame.from_hex(
        "ac 06 81 b3 00 2a aa 55 55 01 00 00 02 11 1f 1f 00 00 00 00 00 00 00 00 00 00"
    )
    assert decode(frame) == UnknownDynet2Message(frame=frame)
