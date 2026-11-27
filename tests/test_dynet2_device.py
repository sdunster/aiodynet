"""DyNet 2 device requests (0x80) and their replies (0x81).

Every frame here was captured at one site on 2026-11-27, sent to or from a
D5Sen (0xB3) or an Antumbra panel (0xFD), and labelled by System Builder.
"""

from typing import TypedDict

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aiodynet import (
    Dynet2BuildNumberReply,
    Dynet2Command,
    Dynet2DeviceRequest,
    Dynet2Frame,
    Dynet2Message,
    Dynet2PcbVersionReply,
    Dynet2RequestSerialNumber,
    Dynet2SignonReply,
    UnknownDynet2Message,
    decode,
)


class Addresses(TypedDict):
    device_code: int
    box: int
    target_device_code: int
    target_box: int


# Replies from the kitchen D5Sen (0xB3 box 29) to System Builder (0xDA box 1).
KITCHEN_TO_SB: Addresses = {
    "device_code": 0xB3,
    "box": 29,
    "target_device_code": 0xDA,
    "target_box": 1,
}


def request(
    command: int, device_code: int = 0xB3, box: int = 29
) -> Dynet2DeviceRequest:
    return Dynet2DeviceRequest(
        device_code=device_code,
        box=box,
        source_device_code=0xDA,
        source_box=1,
        command=command,
    )


SITE_FRAMES: list[tuple[str, Dynet2Message]] = [
    # "Request the device sign on"
    ("ac 02 80 da 00 01 b3 00 1d 01 cc da", request(Dynet2Command.SIGNON)),
    ("ac 02 80 da 00 01 b3 00 1d 02 cd db", request(Dynet2Command.BOOTLOADER_SIGNON)),
    ("ac 02 80 da 00 01 b3 00 1d 06 d1 df", request(Dynet2Command.FIRMWARE_BUILD)),
    ("ac 02 80 da 00 01 b3 00 1d 07 d2 e0", request(Dynet2Command.BOOTLOADER_BUILD)),
    ("ac 02 80 da 00 01 b3 00 1d 0f da e8", request(Dynet2Command.PCB_VERSION)),
    # System Builder's "flash" on the kitchen panel, then "Go back to work".
    (
        "ac 02 80 da 00 01 fd 00 03 08 c7 11",
        request(Dynet2Command.SILENCE, device_code=0xFD, box=3),
    ),
    ("ac 02 80 da 00 01 b3 00 1d 09 d4 e2", request(Dynet2Command.GO_BACK_TO_WORK)),
    # "Reset the device", after a configuration write to the hall sensor.
    (
        "ac 02 80 da 00 01 b3 00 36 00 fd f2",
        request(Dynet2Command.RESET, box=54),
    ),
    # System Builder: Reply Device Signon, FW version v02.11, sizes 128.
    (
        "ac 04 81 b3 00 1d aa 55 55 01 00 00 02 11 1f 1f 00 00 96 a7",
        Dynet2SignonReply(
            device_code=0xB3,
            box=29,
            firmware_major=0x02,
            firmware_minor=0x11,
            max_rx_size=128,
            max_tx_size=128,
        ),
    ),
    # "Reply Device Bootloader Signing"
    (
        "ac 04 81 b3 00 1d aa 55 55 02 00 00 75 05 1f 1f 00 00 15 0f",
        Dynet2SignonReply(
            device_code=0xB3,
            box=29,
            firmware_major=0x75,
            firmware_minor=0x05,
            max_rx_size=128,
            max_tx_size=128,
            bootloader=True,
        ),
    ),
    # System Builder: firmware build number, main device, build 1.
    (
        "ac 03 81 b3 00 1d da 00 01 06 06 00 00 01 5e e8",
        Dynet2BuildNumberReply(**KITCHEN_TO_SB, bootloader=False, build=1),
    ),
    # System Builder: bootloader build number, main device, build 1.
    (
        "ac 03 81 b3 00 1d da 00 01 07 06 01 00 01 66 ea",
        Dynet2BuildNumberReply(**KITCHEN_TO_SB, bootloader=True, build=1),
    ),
    # System Builder: PCB assembly version 1, revision 2, main device.
    (
        "ac 03 81 b3 00 1d da 00 01 0f 0f 00 01 02 b2 fc",
        Dynet2PcbVersionReply(**KITCHEN_TO_SB, version=1, revision=2),
    ),
]


@pytest.mark.parametrize(("text", "expected"), SITE_FRAMES)
def test_site_frames(text: str, expected: Dynet2Message) -> None:
    frame = Dynet2Frame.from_hex(text)
    assert decode(frame) == expected
    assert expected.to_frame() == frame


def test_serial_number_keeps_its_own_class() -> None:
    frame = Dynet2Frame.from_hex("ac 02 80 da 00 01 b3 00 2a 0a ef f0")
    assert isinstance(decode(frame), Dynet2RequestSerialNumber)
    with pytest.raises(ValueError, match="use Dynet2RequestSerialNumber"):
        request(Dynet2Command.SERIAL_NUMBER)


def test_unknown_commands_are_kept() -> None:
    message = decode(Dynet2Frame.from_hex("ac 02 80 da 00 01 b3 00 1d 33"))
    assert message == request(0x33)


@pytest.mark.parametrize(
    "text",
    [
        # Byte 11 says bootloader, the command says firmware.
        "ac 03 81 b3 00 1d da 00 01 06 06 01 00 01",
        # A 16-byte reply to a command with no typed reply.
        "ac 03 81 b3 00 1d da 00 01 05 06 00 00 01",
        # A 20-byte frame with a build number command.
        "ac 04 81 b3 00 1d da 00 01 06 06 00 00 01 00 00 00 00",
    ],
)
def test_frames_that_dont_fit_stay_unknown(text: str) -> None:
    frame = Dynet2Frame.from_hex(text)
    assert decode(frame) == UnknownDynet2Message(frame=frame)


def test_bootloader_must_be_a_bool() -> None:
    with pytest.raises(TypeError, match="bootloader must be a bool"):
        Dynet2BuildNumberReply(**KITCHEN_TO_SB, bootloader=1, build=1)  # type: ignore[arg-type]


def test_raw_field_lengths_are_checked() -> None:
    with pytest.raises(ValueError, match="reserved_raw must be 2 bytes"):
        Dynet2PcbVersionReply(
            **KITCHEN_TO_SB, version=1, revision=2, reserved_raw=b"\x0f"
        )


byte = st.integers(0, 0xFF)
box = st.integers(0, 0xFFFF)


@given(
    st.builds(
        Dynet2DeviceRequest,
        device_code=byte,
        box=box,
        source_device_code=byte,
        source_box=box,
        command=byte.filter(lambda value: value != Dynet2Command.SERIAL_NUMBER),
    )
    | st.builds(
        Dynet2BuildNumberReply,
        device_code=byte,
        box=box,
        target_device_code=byte,
        target_box=box,
        bootloader=st.booleans(),
        build=box,
        reserved_raw=st.binary(min_size=1, max_size=1),
    )
    | st.builds(
        Dynet2PcbVersionReply,
        device_code=byte,
        box=box,
        target_device_code=byte,
        target_box=box,
        version=byte,
        revision=byte,
        reserved_raw=st.binary(min_size=2, max_size=2),
    )
)
def test_round_trip(message: Dynet2Message) -> None:
    assert decode(message.to_frame()) == message


def test_signon_reply_round_trips_the_bootloader_flag() -> None:
    reply = Dynet2SignonReply(
        device_code=0xFD,
        box=3,
        firmware_major=3,
        firmware_minor=0x23,
        max_rx_size=128,
        max_tx_size=128,
        bootloader=True,
    )
    assert decode(reply.to_frame()) == reply
    assert reply.to_frame().body[7] == Dynet2Command.BOOTLOADER_SIGNON
