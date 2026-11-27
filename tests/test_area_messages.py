"""Logical area messages from the opcode list, labelled by System Builder.

Each vector is a packet System Builder sent to (or got back from) area 8 at
one site on 2026-11-27, with the text it showed for it.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aiodynet import (
    AreaLinksReport,
    ClearAreaLinks,
    LightControlAllPresets,
    Message,
    OccupancyDetectionAllPresets,
    Packet,
    RampToLevelExceptOff,
    RecallPreviousPreset,
    RequestAreaLinks,
    SetAreaLinks,
    SetJoinLevel,
    SetPresetBank,
    SetRMask,
    UnknownMessage,
    decode,
)

SITE_VECTORS: list[tuple[str, Message]] = [
    # "Ramp Channels toward Exclude with a ramp rate of 0.00s", channel All
    ("1c08ff5f0000ff", RampToLevelExceptOff(area=8)),
    # "Recall Previous Preset with a fade of 0.00s"
    ("1c08000f0000ff", RecallPreviousPreset(area=8)),
    # "Resume Light Control for all Presets" / "Suspend ..."
    ("1c08ff110001ff", LightControlAllPresets(area=8, resume=True)),
    ("1c08ff110000ff", LightControlAllPresets(area=8, resume=False)),
    # "Resume Occupancy Detection For All Presets", unchanged by the refactor
    ("1c08ff310001ff", OccupancyDetectionAllPresets(area=8, resume=True)),
    # "Set Bank to 0", "Set Rmask 0x00", "Set Join Level 0"
    ("1c0800640000ff", SetPresetBank(area=8, bank=0)),
    ("1c0800400000ff", SetRMask(area=8, mask=0)),
    ("1c0800140000ff", SetJoinLevel(area=8, join_level=0)),
    # "Set Area Links Bits 0xFFFFFF", "Clear Area Link Bits 0xFFFFFF"
    ("1c08ff20ffffff", SetAreaLinks(area=8, bits=0xFFFFFF)),
    ("1c08ff21ffffff", ClearAreaLinks(area=8, bits=0xFFFFFF)),
    # "Request Area Link Bits", "Reply Area Link Bits 0x00"
    ("1c0800230000ff", RequestAreaLinks(area=8)),
    ("1c0800220000ff", AreaLinksReport(area=8, bits=0)),
]


@pytest.mark.parametrize(("raw", "expected"), SITE_VECTORS)
def test_site_packets(raw: str, expected: Message) -> None:
    packet = Packet.from_hex(raw)
    assert decode(packet) == expected
    assert expected.to_packet() == packet


def test_link_bits_follow_the_opcode_list() -> None:
    # Bits 1-8 in byte 2, 9-16 in byte 4, 17-24 in byte 5.
    packet = SetAreaLinks(area=8, bits=0x030201).to_packet()
    assert packet == Packet.from_hex("1c0801200203ff")


def test_light_control_needs_zero_or_one() -> None:
    packet = Packet.from_hex("1c08ff110002ff")
    assert decode(packet) == UnknownMessage(packet=packet)


@pytest.mark.parametrize(
    ("make", "match"),
    [
        (lambda: SetPresetBank(area=8, bank=256), "bank must be 0-255"),
        (lambda: SetAreaLinks(area=8, bits=0x1000000), "bits must be 0-16777215"),
        (lambda: RampToLevelExceptOff(area=8, level_raw=256), "level_raw must be"),
    ],
)
def test_fields_are_checked(make: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        make()  # type: ignore[operator]


byte = st.integers(0, 0xFF)
channel = st.none() | st.integers(1, 255)


@given(
    st.builds(
        LightControlAllPresets,
        area=byte,
        channel=channel,
        resume=st.booleans(),
        join=byte,
    )
    | st.builds(SetPresetBank, area=byte, bank=byte, join=byte)
    | st.builds(SetRMask, area=byte, mask=byte, join=byte)
    | st.builds(SetJoinLevel, area=byte, join_level=byte, join=byte)
    | st.builds(SetAreaLinks, area=byte, bits=st.integers(0, 0xFFFFFF), join=byte)
    | st.builds(ClearAreaLinks, area=byte, bits=st.integers(0, 0xFFFFFF), join=byte)
    | st.builds(AreaLinksReport, area=byte, bits=st.integers(0, 0xFFFFFF), join=byte)
    | st.builds(RequestAreaLinks, area=byte, join=byte)
    | st.builds(
        RampToLevelExceptOff,
        area=byte,
        channel=channel,
        level_raw=byte,
        ramp_raw=byte,
        join=byte,
    )
)
def test_round_trip(message: Message) -> None:
    assert decode(message.to_packet()) == message
