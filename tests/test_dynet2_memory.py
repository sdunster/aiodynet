"""DyNet 2 device memory frames: read (0x9A/0x9B) and write (0x9C/0x9D).

The frames are real, captured at one site in November 2026 between System
Builder (0xDA box 1) and D5Sen sensors (0xB3), which System Builder labelled.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aiodynet import (
    MAIN_EEPROM,
    MAX_MEMORY_CHUNK,
    Dynet2Frame,
    Dynet2MemoryData,
    Dynet2Message,
    Dynet2ReadMemory,
    Dynet2WriteMemory,
    Dynet2WriteMemoryAck,
    UnknownDynet2Message,
    decode,
)

SITE_FRAMES: list[tuple[str, Dynet2Message]] = [
    # "Request Read Data from Device Memory - Main device, memory type EEPROM,
    # start address 0x1a, data length 5", and its "no error" reply.
    (
        "ac 04 9a da 00 01 b3 00 2a 00 00 00 1a 00 05 00 00 00 87 21",
        Dynet2ReadMemory(
            device_code=0xB3,
            box=42,
            source_device_code=0xDA,
            source_box=1,
            address=0x1A,
            length=5,
        ),
    ),
    (
        "ac 05 9b b3 00 2a da 00 01 00 00 00 1a 00 05 00 03 92 ff d5 00 00 a1 8c",
        Dynet2MemoryData(
            device_code=0xB3,
            box=42,
            target_device_code=0xDA,
            target_box=1,
            address=0x1A,
            data=bytes.fromhex("00 03 92 ff d5"),
        ),
    ),
    # A read with selector 02 00 from a sensor whose memory was blank.
    (
        "ac 04 9a da 00 01 b3 00 00 02 00 00 18 00 02 00 00 00 dd f4",
        Dynet2ReadMemory(
            device_code=0xB3,
            box=0,
            source_device_code=0xDA,
            source_box=1,
            address=0x18,
            length=2,
            selector_raw=b"\x02\x00",
        ),
    ),
    (
        "ac 04 9b b3 00 00 da 00 01 02 00 00 18 00 02 ff ff 00 70 f3",
        Dynet2MemoryData(
            device_code=0xB3,
            box=0,
            target_device_code=0xDA,
            target_box=1,
            address=0x18,
            data=b"\xff\xff",
            selector_raw=b"\x02\x00",
        ),
    ),
    # Turning off the hall sensor's motion LED: one byte at 0x0223, then the ack.
    (
        "ac 04 9c da 00 01 b3 00 36 00 00 02 23 00 01 f8 00 00 3b 2e",
        Dynet2WriteMemory(
            device_code=0xDA,
            box=1,
            target_device_code=0xB3,
            target_box=54,
            address=0x0223,
            data=b"\xf8",
        ),
    ),
    (
        "ac 04 9d b3 00 36 da 00 01 00 00 02 23 00 00 00 00 00 89 36",
        Dynet2WriteMemoryAck(
            device_code=0xB3,
            box=54,
            target_device_code=0xDA,
            target_box=1,
            address=0x0223,
        ),
    ),
]


@pytest.mark.parametrize(("text", "expected"), SITE_FRAMES)
def test_site_frames(text: str, expected: Dynet2Message) -> None:
    frame = Dynet2Frame.from_hex(text)
    assert decode(frame) == expected
    assert expected.to_frame() == frame


def test_a_full_chunk_fills_a_128_byte_frame() -> None:
    write = Dynet2WriteMemory(
        device_code=0xDA,
        box=1,
        target_device_code=0xB3,
        target_box=54,
        address=0x0090,
        data=bytes(range(MAX_MEMORY_CHUNK)),
    )
    frame = write.to_frame()
    assert len(frame) == 128
    assert decode(frame) == write


def test_a_trailer_that_isnt_padding_is_kept() -> None:
    frame = Dynet2Frame.from_hex(
        "ac 04 9b b3 00 00 da 00 01 00 00 00 18 00 02 ff ff 07"
    )
    reply = decode(frame)
    assert isinstance(reply, Dynet2MemoryData)
    assert reply.trailer_raw == b"\x07"
    assert reply.to_frame() == frame


@pytest.mark.parametrize(
    "text",
    [
        "ac 00",  # empty
        # A read reply that claims more data than the frame holds.
        "ac 04 9b b3 00 00 da 00 01 00 00 00 18 00 09 ff ff 00",
        # A read request with something in its padding.
        "ac 04 9a da 00 01 b3 00 00 00 00 00 18 00 02 00 00 01",
        # A write acknowledgement with something in its padding.
        "ac 04 9d b3 00 36 da 00 01 00 00 02 23 00 00 00 01 00",
    ],
)
def test_frames_that_dont_fit_stay_unknown(text: str) -> None:
    frame = Dynet2Frame.from_hex(text)
    assert decode(frame) == UnknownDynet2Message(frame=frame)


def test_fields_are_checked() -> None:
    with pytest.raises(ValueError, match="selector_raw must be 2 bytes"):
        Dynet2ReadMemory(
            device_code=0xB3,
            box=1,
            source_device_code=0xDA,
            source_box=1,
            address=0,
            length=1,
            selector_raw=b"\x00",
        )
    with pytest.raises(ValueError, match="address must be 0-65535"):
        Dynet2WriteMemoryAck(
            device_code=0xB3, box=1, target_device_code=0xDA, target_box=1, address=-1
        )
    with pytest.raises(TypeError, match="data must be bytes"):
        Dynet2MemoryData(
            device_code=0xB3,
            box=1,
            target_device_code=0xDA,
            target_box=1,
            address=0,
            data="ff",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="at most 65535 bytes"):
        Dynet2MemoryData(
            device_code=0xB3,
            box=1,
            target_device_code=0xDA,
            target_box=1,
            address=0,
            data=bytes(0x10000),
        )


byte = st.integers(0, 0xFF)
word = st.integers(0, 0xFFFF)
selector = st.binary(min_size=2, max_size=2)


@given(
    st.builds(
        Dynet2ReadMemory,
        device_code=byte,
        box=word,
        source_device_code=byte,
        source_box=word,
        address=word,
        length=word,
        selector_raw=selector,
    )
    | st.builds(
        Dynet2MemoryData,
        device_code=byte,
        box=word,
        target_device_code=byte,
        target_box=word,
        address=word,
        data=st.binary(max_size=MAX_MEMORY_CHUNK),
        selector_raw=selector,
    )
    | st.builds(
        Dynet2WriteMemory,
        device_code=byte,
        box=word,
        target_device_code=byte,
        target_box=word,
        address=word,
        data=st.binary(max_size=MAX_MEMORY_CHUNK),
        selector_raw=selector,
    )
    | st.builds(
        Dynet2WriteMemoryAck,
        device_code=byte,
        box=word,
        target_device_code=byte,
        target_box=word,
        address=word,
        selector_raw=selector,
        result_raw=st.binary(min_size=2, max_size=2),
    )
)
def test_round_trip(message: Dynet2Message) -> None:
    assert decode(message.to_frame()) == message


def test_main_eeprom_is_the_default() -> None:
    assert MAIN_EEPROM == b"\x00\x00"
