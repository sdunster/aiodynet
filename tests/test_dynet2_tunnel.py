"""DyNet 2 tunnel data (0xA3), captured at one site on 2026-11-27.

System Builder labelled each: DALI commands it sent to a DALI controller
(0x64 box 42), and a D5Sen's motion event count metric.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aiodynet import (
    DaliTunnelData,
    Dynet2Frame,
    Dynet2TunnelData,
    MotionEventCounts,
    UnknownDynet2Message,
    decode,
)

METRIC = (
    "ac 09 a3 b3 00 36 aa 55 55 07 00 18 00 00 01 01 01 b3 00 36 01 00 00 00 03"
    " 00 13 02 01 08 00 3c 05 00 00 00 00 04 48 07"
)
INITIALISE = (
    "ac 06 a3 da 00 01 64 00 2a 02 00 09 00 00 10 20 01 00 01 a5 09 a5 09 00 00 00"
    " b6 57"
)
RECALL_MAX = "ac 05 a3 da 00 01 64 00 2a 02 00 07 00 00 10 10 01 00 03 09 05 00 46 f8"


def test_motion_event_counts() -> None:
    frame = Dynet2Frame.from_hex(METRIC)
    message = decode(frame)
    assert isinstance(message, Dynet2TunnelData)
    assert (message.device_code, message.box) == (0xB3, 54)
    assert (message.target_device_code, message.target_box) == (0xAA, 0x5555)
    assert message.channel_raw == 7
    assert len(message.data) == 24
    assert message.dali is None
    assert message.motion_event_counts == MotionEventCounts(
        device_code=0xB3,
        box=54,
        period=60,
        counts=(0, 0, 0, 0, 4),
        metric_id=3,
        metric_version="2.1",
        header_raw=b"\x01\x01",
    )
    assert message.to_frame() == frame


@pytest.mark.parametrize(
    ("text", "dali"),
    [
        # "Format:0x20 0xA509 - INITIALISE ... 0xA509 - INITIALISE", cookie 1.
        (
            INITIALISE,
            DaliTunnelData(
                opcode=0x10,
                format_raw=0x20,
                galaxy=1,
                cookie=1,
                frames=bytes.fromhex("a5 09 a5 09"),
            ),
        ),
        # RECALL MAX LEVEL to short address 4, cookie 3.
        (
            RECALL_MAX,
            DaliTunnelData(
                opcode=0x10, format_raw=0x10, galaxy=1, cookie=3, frames=b"\x09\x05"
            ),
        ),
    ],
)
def test_dali(text: str, dali: DaliTunnelData) -> None:
    frame = Dynet2Frame.from_hex(text)
    message = decode(frame)
    assert isinstance(message, Dynet2TunnelData)
    assert (message.device_code, message.box) == (0xDA, 1)
    assert (message.target_device_code, message.target_box) == (0x64, 42)
    assert message.channel_raw == 2  # the DALI universe
    assert message.dali == dali
    assert message.motion_event_counts is None
    assert message.to_frame() == frame


@pytest.mark.parametrize(
    "data",
    [
        b"",
        bytes.fromhex("02 01 01 b3 00 36 01 00 00 00 03 00 13 02 01 08 00 3c 05 00"),
        # A different metric type.
        bytes.fromhex("01 01 01 b3 00 36 01 00 00 00 03 00 14 02 01 04 00 3c 01 00"),
        # Data length doesn't match.
        bytes.fromhex("01 01 01 b3 00 36 01 00 00 00 03 00 13 02 01 09 00 3c 01 00"),
        # Count doesn't match.
        bytes.fromhex("01 01 01 b3 00 36 01 00 00 00 03 00 13 02 01 04 00 3c 02 00"),
    ],
)
def test_not_motion_event_counts(data: bytes) -> None:
    assert MotionEventCounts.from_bytes(data) is None


def test_short_data_is_not_dali() -> None:
    assert DaliTunnelData.from_bytes(b"\x10\x10\x01\x00") is None


def test_a_trailer_that_isnt_padding_is_kept() -> None:
    frame = Dynet2Frame.from_hex(
        "ac 04 a3 da 00 01 64 00 2a 02 00 01 00 00 10 07 00 00"
    )
    message = decode(frame)
    assert isinstance(message, Dynet2TunnelData)
    assert message.data == b"\x10"
    assert message.trailer_raw == b"\x07\x00\x00"
    assert message.to_frame() == frame


def test_a_length_past_the_end_is_unknown() -> None:
    frame = Dynet2Frame.from_hex(
        "ac 04 a3 da 00 01 64 00 2a 02 00 09 00 00 10 00 00 00"
    )
    assert decode(frame) == UnknownDynet2Message(frame=frame)


def test_fields_are_checked() -> None:
    with pytest.raises(TypeError, match="data must be bytes"):
        Dynet2TunnelData(
            device_code=0xDA,
            box=1,
            target_device_code=0x64,
            target_box=42,
            channel_raw=2,
            data="10",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="at most 65535 bytes"):
        Dynet2TunnelData(
            device_code=0xDA,
            box=1,
            target_device_code=0x64,
            target_box=42,
            channel_raw=2,
            data=bytes(0x10000),
        )


word = st.integers(0, 0xFFFF)


@given(
    st.builds(
        Dynet2TunnelData,
        device_code=st.integers(0, 0xFF),
        box=word,
        target_device_code=st.integers(0, 0xFF),
        target_box=word,
        channel_raw=st.integers(0, 0xFF),
        data=st.binary(max_size=200),
        offset=word,
    )
)
def test_round_trip(message: Dynet2TunnelData) -> None:
    assert decode(message.to_frame()) == message
