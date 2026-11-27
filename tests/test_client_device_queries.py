"""DynetClient.query for device-level requests and their DyNet 1 or 2 replies.

The fake site answers each request with a decoy first (another device, or
the wrong kind of reply) and then the real answer, so these check matching
as well as the round trip.
"""

from collections.abc import AsyncIterator, Iterable
from typing import TypedDict

import pytest

from aiodynet import (
    DeviceStatusReport,
    DeviceStatusType,
    Dynet2BuildNumberReply,
    Dynet2Command,
    Dynet2DeviceRequest,
    Dynet2Frame,
    Dynet2MemoryData,
    Dynet2Message,
    Dynet2PcbVersionReply,
    Dynet2ReadMemory,
    Dynet2RequestSerialNumber,
    Dynet2SerialNumberReply,
    Dynet2SignonReply,
    Dynet2StatusRequest,
    DynetClient,
    Message,
    Packet,
    PhysicalStateReport,
    RequestDeviceStatus,
    RequestSignon,
    StatusQuery,
    decode,
)
from aiodynet.testing import FakeGateway


class Device(TypedDict):
    device_code: int
    box: int


class Source(TypedDict):
    source_device_code: int
    source_box: int


KITCHEN: Device = {"device_code": 0xB3, "box": 29}
SB: Source = {"source_device_code": 0xDA, "source_box": 1}


def signon(*, box: int = 29, bootloader: bool = False) -> Dynet2SignonReply:
    return Dynet2SignonReply(
        device_code=0xB3,
        box=box,
        firmware_major=0x75 if bootloader else 0x02,
        firmware_minor=0x11,
        max_rx_size=128,
        max_tx_size=128,
        bootloader=bootloader,
    )


def build(*, bootloader: bool) -> Dynet2BuildNumberReply:
    return Dynet2BuildNumberReply(
        device_code=0xB3,
        box=29,
        target_device_code=0xDA,
        target_box=1,
        bootloader=bootloader,
        build=2 if bootloader else 1,
    )


PCB = Dynet2PcbVersionReply(
    device_code=0xB3,
    box=29,
    target_device_code=0xDA,
    target_box=1,
    version=1,
    revision=2,
)
SERIAL = Dynet2SerialNumberReply(
    device_code=0xB3,
    box=29,
    target_device_code=0xDA,
    target_box=1,
    serial_number=12345678,
)


def memory(address: int) -> Dynet2MemoryData:
    return Dynet2MemoryData(
        device_code=0xB3,
        box=29,
        target_device_code=0xDA,
        target_box=1,
        address=address,
        data=b"\x00\x03",
    )


def lux(box: int, value: int) -> PhysicalStateReport:
    return PhysicalStateReport(device_code=0xB3, box=box, state_type=1, value=value)


# Each command's decoys, then its answer.
COMMAND_REPLIES: dict[int, list[Dynet2Message]] = {
    Dynet2Command.SIGNON: [signon(bootloader=True), signon(box=42), signon()],
    Dynet2Command.BOOTLOADER_SIGNON: [signon(), signon(bootloader=True)],
    Dynet2Command.FIRMWARE_BUILD: [build(bootloader=True), build(bootloader=False)],
    Dynet2Command.BOOTLOADER_BUILD: [PCB, build(bootloader=True)],
    Dynet2Command.PCB_VERSION: [build(bootloader=False), PCB],
}


def frame_replies(frame: Dynet2Frame) -> Iterable[Message | Dynet2Message]:
    match decode(frame):
        case Dynet2RequestSerialNumber():
            return [SERIAL]
        case Dynet2DeviceRequest(command=command):
            return COMMAND_REPLIES[command]
        case Dynet2ReadMemory(address=address):
            return [memory(0x18), memory(address)]
        case Dynet2StatusRequest():
            return [lux(42, 1627), lux(29, 15)]
    return []


def replies(packet: Packet) -> Iterable[Message | Dynet2Message]:
    if packet == RequestSignon(device_code=0xB3, box=29).to_packet():
        return [signon(bootloader=True), signon()]
    if (
        packet
        == RequestDeviceStatus(
            **KITCHEN, status_type=DeviceStatusType.BOOTLOADER_RUNNING_REASON
        ).to_packet()
    ):
        return [
            DeviceStatusReport(**KITCHEN, sub_opcode=0x0A),
            DeviceStatusReport(**KITCHEN, sub_opcode=0x16, data1_raw=0x55),
        ]
    return []


@pytest.fixture
async def client() -> AsyncIterator[DynetClient]:
    async with (
        FakeGateway(replies=replies, frame_replies=frame_replies) as gateway,
        DynetClient(gateway.host, gateway.port, send_interval=0) as client,
    ):
        yield client


def ask(command: int) -> Dynet2DeviceRequest:
    return Dynet2DeviceRequest(**KITCHEN, **SB, command=command)


async def test_device_status(client: DynetClient) -> None:
    report = await client.query(
        RequestDeviceStatus(
            **KITCHEN, status_type=DeviceStatusType.BOOTLOADER_RUNNING_REASON
        )
    )
    assert report.application_running is True


async def test_signon_skips_the_bootloader_signon(client: DynetClient) -> None:
    assert await client.query(RequestSignon(**KITCHEN)) == signon()


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (Dynet2Command.SIGNON, signon()),
        (Dynet2Command.BOOTLOADER_SIGNON, signon(bootloader=True)),
        (Dynet2Command.FIRMWARE_BUILD, build(bootloader=False)),
        (Dynet2Command.BOOTLOADER_BUILD, build(bootloader=True)),
        (Dynet2Command.PCB_VERSION, PCB),
    ],
)
async def test_device_requests(
    client: DynetClient, command: int, expected: Dynet2Message
) -> None:
    assert await client.query(ask(command)) == expected


async def test_serial_number(client: DynetClient) -> None:
    reply = await client.query(Dynet2RequestSerialNumber(**KITCHEN, **SB))
    assert reply == SERIAL


async def test_read_memory(client: DynetClient) -> None:
    reply = await client.query(
        Dynet2ReadMemory(**KITCHEN, **SB, address=0x1A, length=2)
    )
    assert reply == memory(0x1A)


async def test_light_level(client: DynetClient) -> None:
    request = Dynet2StatusRequest(
        **KITCHEN, **SB, query=StatusQuery.PE_CELL_LIGHT_LEVEL
    )
    assert (await client.query(request)).lux == 15


@pytest.mark.parametrize(
    ("request_", "match"),
    [
        (ask(Dynet2Command.SILENCE), "has no reply"),
        (ask(Dynet2Command.RESET), "has no reply"),
        (
            Dynet2StatusRequest(**KITCHEN, **SB, query=StatusQuery.MOTION_POWER_LEVEL),
            "can't wait for status query 0x0501",
        ),
        (
            Dynet2StatusRequest(
                device_code=0xB3,
                box=300,
                **SB,
                query=StatusQuery.PE_CELL_LIGHT_LEVEL,
            ),
            "box of 0-255",
        ),
        (
            RequestDeviceStatus(device_code=0xAA, box=0x55, status_type=0x16),
            "needs one device",
        ),
        (
            Dynet2DeviceRequest(
                device_code=0xAA, box=0x5555, **SB, command=Dynet2Command.SIGNON
            ),
            "needs one device",
        ),
    ],
)
async def test_requests_query_cant_wait_for(
    request_: Dynet2DeviceRequest | Dynet2StatusRequest | RequestDeviceStatus,
    match: str,
) -> None:
    client = DynetClient("127.0.0.1", 1)
    with pytest.raises(ValueError, match=match):
        await client.query(request_)
