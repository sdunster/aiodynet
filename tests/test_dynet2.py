import pytest

from aiodynet import (
    DYNET2_SYNC,
    Dynet2Frame,
    InvalidPacketError,
    dynet2_checksum,
    dynet2_length,
)

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
