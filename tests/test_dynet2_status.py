"""DyNet 2 status requests (0x82), captured at one site on 2026-11-27.

System Builder (0xDA box 1) polled D5Sen sensors with these and labelled
each; the gateway's greeting is the same frame type.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aiodynet import (
    Dynet2Frame,
    Dynet2StatusRequest,
    StatusQuery,
    UnknownDynet2Message,
    decode,
)


def poll(query: int, box: int) -> Dynet2StatusRequest:
    return Dynet2StatusRequest(
        device_code=0xB3, box=box, source_device_code=0xDA, source_box=1, query=query
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # System Builder: PE Cell status, filtered LL, user cal, min 1 max 1.
        (
            "ac 04 82 da 00 01 b3 00 2a 04 03 00 01 00 01 00 00 00 9d f3",
            poll(StatusQuery.PE_CELL_LIGHT_LEVEL, 42),
        ),
        # System Builder: PE Cell status, Filtered Raw LL, min 1 max 1.
        (
            "ac 04 82 da 00 01 b3 00 1d 04 01 00 01 00 01 00 00 00 0b e4",
            poll(StatusQuery.PE_CELL_RAW_LIGHT_LEVEL, 29),
        ),
        # System Builder: Request Motion status, power level, min 1 max 1.
        (
            "ac 04 82 da 00 01 b3 00 1d 05 01 00 01 00 01 00 00 00 14 e5",
            poll(StatusQuery.MOTION_POWER_LEVEL, 29),
        ),
    ],
)
def test_site_frames(text: str, expected: Dynet2StatusRequest) -> None:
    frame = Dynet2Frame.from_hex(text)
    assert decode(frame) == expected
    assert expected.to_frame() == frame


def test_padding_must_be_zero() -> None:
    frame = Dynet2Frame.from_hex(
        "ac 04 82 da 00 01 b3 00 1d 05 01 00 01 00 01 00 00 01"
    )
    assert decode(frame) == UnknownDynet2Message(frame=frame)


def test_query_range_is_checked() -> None:
    with pytest.raises(ValueError, match="query must be 0-65535"):
        poll(0x10000, 29)


word = st.integers(0, 0xFFFF)


@given(
    st.builds(
        Dynet2StatusRequest,
        device_code=st.integers(0, 0xFF),
        box=word,
        source_device_code=st.integers(0, 0xFF),
        source_box=word,
        query=word,
        first=word,
        last=word,
    )
)
def test_round_trip(message: Dynet2StatusRequest) -> None:
    assert decode(message.to_frame()) == message
