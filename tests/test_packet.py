import pytest
from hypothesis import given
from hypothesis import strategies as st

from aiodynet import InvalidPacketError, Packet, SyncType
from aiodynet.packet import checksum

payloads = st.binary(min_size=7, max_size=7)


def test_build_adds_checksum() -> None:
    packet = Packet.build(0x1C, 0x02, 0x00, 0x00, 0x00, 0x00, 0xFF)
    assert bytes(packet) == bytes.fromhex("1c0200000000ffe3")
    assert packet.checksum == 0xE3
    assert packet.sync == SyncType.LOGICAL
    assert packet.is_logical


def test_physical_packet_is_not_logical() -> None:
    packet = Packet.build(0x5C, 1, 2, 3, 4, 5, 0xFF)
    assert packet.sync == SyncType.PHYSICAL
    assert not packet.is_logical


@pytest.mark.parametrize("bad", [-1, 256])
def test_build_rejects_out_of_range_bytes(bad: int) -> None:
    with pytest.raises(InvalidPacketError, match="Byte 3 must be 0-255"):
        Packet.build(0x1C, 1, 2, bad, 4, 5, 6)


def test_data_must_be_seven_bytes() -> None:
    with pytest.raises(InvalidPacketError, match="must be 7 bytes"):
        Packet(b"\x1c\x01")


def test_data_must_be_bytes() -> None:
    with pytest.raises(TypeError, match="must be bytes, not bytearray"):
        Packet(bytearray(7))  # type: ignore[arg-type]


def test_from_bytes_checks_length() -> None:
    with pytest.raises(InvalidPacketError, match="A packet is 8 bytes, got 7"):
        Packet.from_bytes(bytes(7))


def test_from_bytes_checks_checksum() -> None:
    with pytest.raises(InvalidPacketError, match="expected 0xe3"):
        Packet.from_bytes(bytes.fromhex("1c0200000000ff00"))


def test_from_bytes_accepts_bytearray_and_memoryview() -> None:
    raw = bytes.fromhex("1c0200000000ffe3")
    assert Packet.from_bytes(bytearray(raw)) == Packet.from_bytes(memoryview(raw))


@pytest.mark.parametrize(
    "text",
    [
        "1c0200000000ffe3",
        "1c0200000000ff",
        "1c 02 00 00 00 00 ff e3",
        "1C:02:00:00:00:00:FF",
        "1c-02-00-00-00-00-ff",
        "  1c 02 00 00\n00 00 ff  ",
    ],
)
def test_from_hex(text: str) -> None:
    assert Packet.from_hex(text) == Packet(bytes.fromhex("1c0200000000ff"))


@pytest.mark.parametrize("text", ["zz", "1c02", "1c0200000000ff00", "1c0"])
def test_from_hex_rejects_bad_input(text: str) -> None:
    with pytest.raises(InvalidPacketError):
        Packet.from_hex(text)


def test_sequence_protocol_and_repr() -> None:
    packet = Packet.from_hex("1c0200000000ff")
    assert len(packet) == 8
    assert list(packet) == [0x1C, 2, 0, 0, 0, 0, 0xFF, 0xE3]
    assert packet[7] == 0xE3
    assert packet[-1] == 0xE3
    assert packet.hex() == "1c 02 00 00 00 00 ff e3"
    assert packet.hex("") == "1c0200000000ffe3"
    assert repr(packet) == "Packet(1c 02 00 00 00 00 ff e3)"


def test_packets_are_hashable_values() -> None:
    a = Packet.from_hex("1c0200000000ff")
    b = Packet.from_hex("1c0200000000ffe3")
    assert a == b
    assert len({a, b}) == 1


@given(payloads)
def test_checksum_makes_the_sum_zero(data: bytes) -> None:
    packet = Packet(data)
    assert sum(bytes(packet)) % 256 == 0
    assert checksum(data) == packet.checksum


@given(payloads)
def test_bytes_round_trip(data: bytes) -> None:
    packet = Packet(data)
    assert Packet.from_bytes(bytes(packet)) == packet
    assert Packet.from_hex(packet.hex()) == packet


@given(payloads, st.integers(0, 7), st.integers(1, 255))
def test_any_single_byte_corruption_is_detected(
    data: bytes, index: int, delta: int
) -> None:
    raw = bytearray(bytes(Packet(data)))
    raw[index] = (raw[index] + delta) % 256
    with pytest.raises(InvalidPacketError):
        Packet.from_bytes(raw)
