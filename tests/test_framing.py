import logging

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aiodynet import Dynet2Frame, Packet, PacketFramer

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


def test_on_discard_gets_each_run_of_noise() -> None:
    runs: list[bytes] = []
    framer = PacketFramer(on_discard=runs.append)
    assert list(framer.feed(b"\x09\x00" + bytes(A) + b"\x1c" + bytes(B))) == [A, B]
    assert list(framer.feed(b"\x07")) == []
    assert runs == [b"\x09\x00", b"\x1c", b"\x07"]


def test_skips_a_corrupt_packet() -> None:
    framer = PacketFramer()
    corrupt = bytearray(bytes(A))
    corrupt[4] ^= 0xFF
    assert list(framer.feed(bytes(corrupt) + bytes(B))) == [B]
    assert framer.discarded == 8


def test_noise_without_a_sync_byte_is_dropped_at_once() -> None:
    framer = PacketFramer()
    assert list(framer.feed(bytes(9))) == []
    assert framer.discarded == 9
    assert framer.pending == 0


def test_a_sync_byte_near_the_end_waits_for_the_rest() -> None:
    framer = PacketFramer()
    assert list(framer.feed(bytes(3) + bytes(A)[:5])) == []
    assert framer.discarded == 3
    assert framer.pending == 5


def test_reset_drops_a_partial_packet() -> None:
    runs: list[bytes] = []
    framer = PacketFramer(on_discard=runs.append)
    list(framer.feed(bytes(A)[:5]))
    framer.reset()
    assert framer.pending == 0
    assert framer.discarded == 5
    assert runs == [bytes(A)[:5]]
    framer.reset()  # nothing left to drop
    assert runs == [bytes(A)[:5]]
    assert list(framer.feed(bytes(B))) == [B]


packets = st.builds(
    lambda sync, rest: Packet(bytes((sync,)) + rest),
    st.sampled_from([0x1C, 0x5C]),
    st.binary(min_size=6, max_size=6),
)


# Noise without sync bytes can never start a packet, so it can't hide one.
noise = st.lists(
    st.integers(0, 255).map(lambda b: 0 if b in {0x1C, 0x5C, 0xAC} else b)
).map(bytes)


# DyNet 2 frames (specs/dynet2-framing.md). Captured at a real site.

GREETING = Dynet2Frame.from_hex(
    "ac 04 82 dc 00 5a aa 55 55 00 0c 00 00 00 00 00 00 00 43 c8"
)
REQUEST = Dynet2Frame.from_hex("ac 02 80 da 00 01 b3 00 2a 0a ef f0")
REPLY = Dynet2Frame.from_hex(
    "ac 04 81 b3 00 2a da 00 01 0a 0a 03 02 c8 7a 00 00 00 e8 44"
)
AREA_111 = Packet.from_hex("1c 6f 00 62 00 00 ff 14")
AREA_95 = Packet.from_hex("1c 5f 03 62 00 00 ff 21")
AREA_20 = Packet.from_hex("1c 14 ff 2f 00 00 ff a3")


def join(*items: Packet | Dynet2Frame | bytes) -> bytes:
    return b"".join(bytes(item) for item in items)


@pytest.mark.parametrize(
    "items",
    [
        [GREETING, AREA_111],
        [REQUEST, REPLY, AREA_95],
        [GREETING, AREA_20],
    ],
)
def test_captured_streams(items: list[Packet | Dynet2Frame]) -> None:
    framer = PacketFramer()
    assert list(framer.feed(join(*items))) == items
    assert framer.discarded == 0
    assert framer.pending == 0


def test_frames_are_not_noise(caplog: pytest.LogCaptureFixture) -> None:
    framer = PacketFramer()
    with caplog.at_level(logging.DEBUG):
        assert list(framer.feed(join(REQUEST, REPLY, AREA_95))) == [
            REQUEST,
            REPLY,
            AREA_95,
        ]
    assert framer.discarded == 0
    assert "Skipped" not in caplog.text


def test_shortest_frame() -> None:
    frame = Dynet2Frame.build(b"")
    assert list(PacketFramer().feed(bytes(frame))) == [frame]


def test_a_partial_frame_waits_without_skipping() -> None:
    framer = PacketFramer()
    raw = bytes(GREETING)
    assert list(framer.feed(raw[:1])) == []
    assert list(framer.feed(raw[1:19])) == []
    assert framer.pending == 19
    assert framer.discarded == 0
    assert list(framer.feed(raw[19:] + bytes(A))) == [GREETING, A]


@pytest.mark.parametrize("frame", [GREETING, REQUEST, REPLY])
@pytest.mark.parametrize("index", [-1, -2])
def test_a_bad_frame_checksum_is_noise(frame: Dynet2Frame, index: int) -> None:
    raw = bytearray(bytes(frame))
    raw[index] ^= 0x01
    framer = PacketFramer()
    assert list(framer.feed(bytes(raw) + bytes(AREA_20))) == [AREA_20]
    assert framer.discarded == len(frame)


def test_a_corrupt_length_byte_is_noise() -> None:
    raw = bytearray(bytes(GREETING))
    raw[1] = 0x02  # claims 12 bytes; the checksum there doesn't hold
    framer = PacketFramer()
    assert list(framer.feed(bytes(raw) + bytes(AREA_20))) == [AREA_20]
    assert framer.discarded == len(GREETING)


def test_a_long_length_doesnt_hold_back_packets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = bytearray(bytes(GREETING))
    raw[1] = 0xFF  # claims 1024 bytes
    framer = PacketFramer()
    with caplog.at_level(logging.DEBUG):
        assert list(framer.feed(bytes(raw) + bytes(AREA_20))) == [AREA_20]
    assert framer.pending == 0
    assert "Not waiting for a 1024-byte DyNet 2 frame (limit 64)" in caplog.text


def test_ac_noise_before_a_packet() -> None:
    # 0xAC then 0x1C claims 116 bytes, over the limit, so A comes out at once.
    framer = PacketFramer()
    assert list(framer.feed(b"\xac" + bytes(A))) == [A]
    assert framer.discarded == 1


def test_ac_noise_with_a_short_length_holds_packets_back_briefly() -> None:
    # 0xAC 0x02 claims 12 bytes, so the framer waits for them before deciding.
    framer = PacketFramer()
    assert list(framer.feed(b"\xac\x02" + bytes(A))) == []
    assert framer.pending == 10
    assert list(framer.feed(bytes(B))) == [A, B]
    assert framer.discarded == 2


def test_ac_inside_a_packet_is_data() -> None:
    area_172 = Packet.from_hex("1c ac 00 00 00 00 ff")
    framer = PacketFramer()
    assert list(framer.feed(join(area_172, GREETING, area_172))) == [
        area_172,
        GREETING,
        area_172,
    ]
    assert framer.discarded == 0


def test_joining_mid_packet_at_an_ac_byte() -> None:
    area_172 = Packet.from_hex("1c ac 00 00 00 00 ff")
    framer = PacketFramer()
    assert list(framer.feed(bytes(area_172)[1:] + bytes(A))) == [A]
    assert framer.discarded == 7


def test_a_higher_limit_accepts_longer_frames() -> None:
    long_frame = Dynet2Frame.build(bytes(96))
    assert list(PacketFramer().feed(bytes(long_frame))) == []
    framer = PacketFramer(max_dynet2_length=1024)
    assert list(framer.feed(bytes(long_frame))) == [long_frame]


@pytest.mark.parametrize("limit", [3, 1025])
def test_rejects_a_bad_limit(limit: int) -> None:
    with pytest.raises(ValueError, match="max_dynet2_length must be 4-1024"):
        PacketFramer(max_dynet2_length=limit)


def test_reset_drops_a_partial_frame() -> None:
    framer = PacketFramer()
    list(framer.feed(bytes(GREETING)[:7]))
    framer.reset()
    assert framer.pending == 0
    assert list(framer.feed(bytes(B))) == [B]


frames = st.integers(0, 15).flatmap(
    lambda n: st.binary(min_size=4 * n, max_size=4 * n).map(Dynet2Frame.build)
)


@given(st.lists(packets | frames, max_size=20), st.data())
def test_any_split_yields_the_same_items(
    items: list[Packet | Dynet2Frame], data: st.DataObject
) -> None:
    stream = join(*items)
    cuts = sorted(data.draw(st.lists(st.integers(0, len(stream)), max_size=10)))
    framer = PacketFramer()
    out: list[Packet | Dynet2Frame] = []
    start = 0
    for cut in [*cuts, len(stream)]:
        out.extend(framer.feed(stream[start:cut]))
        start = cut
    assert out == items
    assert framer.pending == 0
    assert framer.discarded == 0


@given(noise, st.lists(packets | frames, min_size=1, max_size=5))
def test_recovers_after_noise(
    garbage: bytes, items: list[Packet | Dynet2Frame]
) -> None:
    framer = PacketFramer()
    assert list(framer.feed(garbage + join(*items))) == items
    assert framer.discarded == len(garbage)
