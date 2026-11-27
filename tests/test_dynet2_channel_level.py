"""DyNet 2 channel level and temperature, captured at one site on 2026-11-27.

System Builder asked area 8 (Rumpus) channel 1 (Downlights) for its level,
and a DALI controller (0x64 box 42) answered for its tunable-white fittings.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aiodynet import (
    Dynet2ChannelLevelReport,
    Dynet2Frame,
    Dynet2RequestChannelLevel,
    UnknownDynet2Message,
    decode,
)

# System Builder: Request Channel Level and Temperature, area 8, channel 1.
REQUEST = "ac 03 22 da 00 01 00 08 ff 03 00 01 00 00 d2 b7"
# System Builder: Reply Channel Level and Temperature, current and target
# level 50 %, current temperature 3205 K, target 3200 K.
REPORT = (
    "ac 07 23 64 00 2a 00 08 ff 03 00 01 00 00 7f 7f 0c 85 0c 80"
    " 00 00 00 00 00 00 00 00 00 00 3b 8a"
)


def test_request() -> None:
    frame = Dynet2Frame.from_hex(REQUEST)
    message = Dynet2RequestChannelLevel(
        source_device_code=0xDA, source_box=1, area=8, channel=1
    )
    assert decode(frame) == message
    assert message.to_frame() == frame


def test_report() -> None:
    frame = Dynet2Frame.from_hex(REPORT)
    report = decode(frame)
    assert report == Dynet2ChannelLevelReport(
        device_code=0x64,
        box=42,
        area=8,
        channel=1,
        current_level=0x7F,
        target_level=0x7F,
        current_kelvin=3205,
        target_kelvin=3200,
    )
    assert isinstance(report, Dynet2ChannelLevelReport)
    assert report.current_percent == pytest.approx(50, abs=0.5)
    assert report.target_percent == pytest.approx(50, abs=0.5)
    assert report.to_frame() == frame


@pytest.mark.parametrize(
    "text",
    [
        "ac 04 22 da 00 01 00 08 ff 03 00 01 00 00 00 00 00 00",  # too long
        "ac 06 23 64 00 2a 00 08 ff 03 00 01 00 00 7f 7f 0c 85 0c 80 00 00 00 00 00 00",
    ],
)
def test_other_lengths_stay_unknown(text: str) -> None:
    frame = Dynet2Frame.from_hex(text)
    assert decode(frame) == UnknownDynet2Message(frame=frame)


def test_extra_bytes_are_checked() -> None:
    with pytest.raises(ValueError, match="extra_raw must be 10 bytes"):
        Dynet2ChannelLevelReport(
            device_code=0x64,
            box=42,
            area=8,
            channel=1,
            current_level=0,
            target_level=0,
            current_kelvin=0,
            target_kelvin=0,
            extra_raw=b"",
        )


byte = st.integers(0, 0xFF)
word = st.integers(0, 0xFFFF)


@given(
    st.builds(
        Dynet2RequestChannelLevel,
        source_device_code=byte,
        source_box=word,
        area=word,
        channel=word,
        join=byte,
        value_raw=word,
        reserved_raw=byte,
    )
    | st.builds(
        Dynet2ChannelLevelReport,
        device_code=byte,
        box=word,
        area=word,
        channel=word,
        current_level=byte,
        target_level=byte,
        current_kelvin=word,
        target_kelvin=word,
        join=byte,
        value_raw=word,
        reserved_raw=byte,
        extra_raw=st.binary(min_size=10, max_size=10),
    )
)
def test_round_trip(
    message: Dynet2RequestChannelLevel | Dynet2ChannelLevelReport,
) -> None:
    assert decode(message.to_frame()) == message
