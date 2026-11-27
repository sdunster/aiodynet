"""Physical (0x5C) device messages confirmed against System Builder.

The vectors are real packets captured at one site on 2026-11-27, with the
meaning System Builder (the official DyNet tool) gave each one.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aiodynet import (
    APPLICATION_RUNNING,
    DEVICE_RESET,
    DeviceIdentify,
    DeviceStatusReport,
    DeviceStatusType,
    Message,
    Packet,
    RequestDeviceStatus,
    RequestSignon,
    UnknownMessage,
    decode,
)

byte = st.integers(0, 0xFF)

SITE_VECTORS: list[tuple[str, Message]] = [
    # "Request Device Status - Bootloader running Reason", to the kitchen D5Sen.
    (
        "5cb31d40000016",
        RequestDeviceStatus(
            device_code=0xB3,
            box=29,
            status_type=DeviceStatusType.BOOTLOADER_RUNNING_REASON,
        ),
    ),
    # "... (This is not the bootloader, this is the application)".
    (
        "5cb31d4155ff16",
        DeviceStatusReport(
            device_code=0xB3, box=29, sub_opcode=0x16, data1_raw=0x55, data2_raw=0xFF
        ),
    ),
    # "Device D5Sen (0xB3) Box 54 v02.11, Device Reset", after a config write.
    (
        "5cb33600021180",
        DeviceIdentify(
            device_code=0xB3,
            box=54,
            firmware_major=0x02,
            firmware_minor=0x11,
            boot_raw=DEVICE_RESET,
        ),
    ),
]


@pytest.mark.parametrize(("raw", "expected"), SITE_VECTORS)
def test_site_packets(raw: str, expected: Message) -> None:
    packet = Packet.from_hex(raw)
    assert decode(packet) == expected
    assert expected.to_packet() == packet


def test_request_device_status_drops_the_unused_bytes() -> None:
    packet = Packet.from_hex("5cb31d40123416")
    request = decode(packet)
    assert request == RequestDeviceStatus(device_code=0xB3, box=29, status_type=0x16)
    assert request.to_packet() == Packet.from_hex("5cb31d40000016")


def test_sign_on_still_decodes_as_request_signon() -> None:
    assert decode(Packet.from_hex("5cb32a4000000a")) == RequestSignon(
        device_code=0xB3, box=42
    )


def test_request_device_status_refuses_sign_on() -> None:
    with pytest.raises(ValueError, match="use RequestSignon"):
        RequestDeviceStatus(device_code=0xB3, box=42, status_type=0x0A)


def test_request_device_status_checks_its_type() -> None:
    with pytest.raises(ValueError, match="status_type must be 0-255"):
        RequestDeviceStatus(device_code=0xB3, box=42, status_type=0x100)


@pytest.mark.parametrize(
    ("sub_opcode", "data1", "running"),
    [
        (DeviceStatusType.BOOTLOADER_RUNNING_REASON, APPLICATION_RUNNING, True),
        (DeviceStatusType.BOOTLOADER_RUNNING_REASON, 0x01, False),
        (DeviceStatusType.DO_D2_SIGNON, APPLICATION_RUNNING, None),
    ],
)
def test_application_running(sub_opcode: int, data1: int, running: bool | None) -> None:
    report = DeviceStatusReport(
        device_code=0xB3, box=29, sub_opcode=sub_opcode, data1_raw=data1
    )
    assert report.application_running is running


@pytest.mark.parametrize(("boot", "after_reset"), [(0x00, False), (0x80, True)])
def test_identify_after_reset(boot: int, after_reset: bool) -> None:
    identify = DeviceIdentify(
        device_code=0xB3, box=11, firmware_major=2, firmware_minor=0x11, boot_raw=boot
    )
    assert identify.after_reset is after_reset


@given(
    st.builds(
        RequestDeviceStatus,
        device_code=byte,
        box=byte,
        status_type=byte.filter(lambda value: value != DeviceStatusType.DO_D2_SIGNON),
    )
)
def test_round_trip(message: RequestDeviceStatus) -> None:
    assert decode(message.to_packet()) == message


def test_other_physical_opcodes_stay_unknown() -> None:
    packet = Packet.from_hex("5cb31d0a000010")  # PE cell raw level reply: unlabelled
    assert decode(packet) == UnknownMessage(packet=packet)
