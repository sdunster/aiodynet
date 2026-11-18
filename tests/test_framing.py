import logging

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aiodynet import Packet, PacketFramer

A = Packet.from_hex("1c0200000000ff")
B = Packet.from_hex("1c03c801000000")
P = Packet.from_hex("5c0102030405ff")


def test_whole_packets() -> None:
    framer = PacketFramer()
    assert list(framer.feed(bytes(A) + bytes(B) + bytes(P))) == [A, B, P]
    assert framer.pending == 0
    assert framer.discarded == 0


def test_packet_split_across_reads() -> None:
    framer = PacketFramer()
    raw = bytes(A)
    assert list(framer.feed(raw[:3])) == []
    assert framer.pending == 3
    assert list(framer.feed(raw[3:])) == [A]


def test_skips_noise_and_logs_it(caplog: pytest.LogCaptureFixture) -> None:
    framer = PacketFramer()
    with caplog.at_level(logging.DEBUG):
        assert list(framer.feed(b"\x09\x00" + bytes(A) + b"\x1c" + bytes(B))) == [A, B]
    assert framer.discarded == 3
    assert "Skipped 2 byte(s)" in caplog.text


def test_skips_a_corrupt_packet() -> None:
    framer = PacketFramer()
    corrupt = bytearray(bytes(A))
    corrupt[4] ^= 0xFF
    assert list(framer.feed(bytes(corrupt) + bytes(B))) == [B]
    assert framer.discarded == 8


def test_trailing_noise_is_dropped_once_it_cannot_start_a_packet() -> None:
    framer = PacketFramer()
    assert list(framer.feed(bytes(9))) == []
    assert framer.discarded == 2
    assert framer.pending == 7


def test_reset_drops_a_partial_packet() -> None:
    framer = PacketFramer()
    list(framer.feed(bytes(A)[:5]))
    framer.reset()
    assert framer.pending == 0
    assert list(framer.feed(bytes(B))) == [B]


packets = st.builds(
    lambda sync, rest: Packet(bytes((sync,)) + rest),
    st.sampled_from([0x1C, 0x5C]),
    st.binary(min_size=6, max_size=6),
)


@given(st.lists(packets, max_size=20), st.data())
def test_any_split_yields_the_same_packets(
    items: list[Packet], data: st.DataObject
) -> None:
    stream = b"".join(bytes(p) for p in items)
    cuts = sorted(data.draw(st.lists(st.integers(0, len(stream)), max_size=10)))
    framer = PacketFramer()
    out: list[Packet] = []
    start = 0
    for cut in [*cuts, len(stream)]:
        out.extend(framer.feed(stream[start:cut]))
        start = cut
    assert out == items
    assert framer.pending == 0


# Noise without sync bytes can never start a packet, so it can't hide one.
noise = st.lists(st.integers(0, 255).map(lambda b: 0 if b in {0x1C, 0x5C} else b)).map(
    bytes
)


@given(noise, st.lists(packets, min_size=1, max_size=5))
def test_recovers_after_noise(garbage: bytes, items: list[Packet]) -> None:
    framer = PacketFramer()
    assert list(framer.feed(garbage + b"".join(bytes(p) for p in items))) == items
    assert framer.discarded == len(garbage)
