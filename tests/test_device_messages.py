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
    PHYSICAL_LEVEL_FULL,
    DeviceIdentify,
    DeviceStatusReport,
    DeviceStatusType,
    Message,
    Packet,
    PhysicalStateReport,
    PhysicalStateType,
    RequestDeviceStatus,
    RequestSignon,
    SetPhysicalChannelLevel,
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
    # System Builder flashing relay channel 3 of a DDRC (0x4C) box 5:
    # "Set Physical Channel Level (Channel 3, Level 100%, Fade 0.00s)".
    (
        "5c4c059203fe00",
        SetPhysicalChannelLevel(
            device_code=0x4C, box=5, channel=3, level=PHYSICAL_LEVEL_FULL
        ),
    ),
    (
        "5c4c0592030000",
        SetPhysicalChannelLevel(device_code=0x4C, box=5, channel=3, level=0),
    ),
    # The gateway's translation of a DyNet 2 fade to DALI channel 75.
    (
        "5c642a924bfe00",
        SetPhysicalChannelLevel(device_code=0x64, box=42, channel=75, level=0xFE),
    ),
    # "Reply Present Physical State - Light Level 1627 Lux", from a D5Sen.
    (
        "5cb32ab801065b",
        PhysicalStateReport(
            device_code=0xB3,
            box=42,
            state_type=PhysicalStateType.LIGHT_LEVEL,
            value=1627,
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


def test_physical_level_percent() -> None:
    full = SetPhysicalChannelLevel.from_percent(
        device_code=0x4C, box=5, channel=3, percent=100
    )
    assert full.level == PHYSICAL_LEVEL_FULL
    assert full.percent == 100
    assert full.fade_raw == 0
    half = SetPhysicalChannelLevel.from_percent(
        device_code=0x4C, box=5, channel=3, percent=50
    )
    assert half.level == 0x7F
    assert (
        SetPhysicalChannelLevel.from_percent(
            device_code=0x4C, box=5, channel=3, percent=0
        ).level
        == 0
    )


@pytest.mark.parametrize("percent", [-1, 100.5])
def test_physical_level_percent_range(percent: float) -> None:
    with pytest.raises(ValueError, match="percent must be 0-100"):
        SetPhysicalChannelLevel.from_percent(
            device_code=0x4C, box=5, channel=3, percent=percent
        )


def test_physical_state_lux() -> None:
    light = PhysicalStateReport(device_code=0xB3, box=29, state_type=1, value=15)
    assert light.lux == 15
    other = PhysicalStateReport(device_code=0xB3, box=29, state_type=0x0D, value=15)
    assert other.lux is None


@given(
    st.builds(
        SetPhysicalChannelLevel,
        device_code=byte,
        box=byte,
        channel=byte,
        level=byte,
        fade_raw=byte,
    )
    | st.builds(
        PhysicalStateReport,
        device_code=byte,
        box=byte,
        state_type=byte,
        value=st.integers(0, 0xFFFF),
    )
)
def test_physical_round_trip(message: Message) -> None:
    assert decode(message.to_packet()) == message
