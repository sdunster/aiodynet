"""DynetClient.query for memory writes and their acknowledgements.

The vectors are System Builder's write of the hall D5Sen's motion LED flag
(0x0223 = f8) and the sensor's acknowledgement, captured on 2026-11-27.
"""

from collections.abc import AsyncIterator, Iterable

import pytest

from aiodynet import (
    DYNET2_ALL_DEVICES,
    Dynet2Frame,
    Dynet2Message,
    Dynet2WriteMemory,
    Dynet2WriteMemoryAck,
    DynetClient,
    MemoryWriteError,
    decode,
)
from aiodynet.testing import FakeGateway

WRITE = Dynet2Frame.from_bytes(
    bytes.fromhex("ac049cda0001b30036000002230001f800003b2e")
)
ACK = Dynet2Frame.from_bytes(bytes.fromhex("ac049db30036da00010000022300000000008936"))
FAILING_ADDRESS = 0x0300


def ack(
    address: int,
    *,
    box: int = 54,
    selector: bytes = b"\x00\x00",
    result: bytes = b"\x00\x00",
) -> Dynet2WriteMemoryAck:
    return Dynet2WriteMemoryAck(
        device_code=0xB3,
        box=box,
        target_device_code=0xDA,
        target_box=1,
        address=address,
        selector_raw=selector,
        result_raw=result,
    )


def frame_replies(frame: Dynet2Frame) -> Iterable[Dynet2Message]:
    message = decode(frame)
    assert isinstance(message, Dynet2WriteMemory)
    address = message.address
    if address == FAILING_ADDRESS:
        return [ack(address, result=b"\x00\x13")]
    # Decoys first: another device, another address, another memory.
    return [
        ack(address, box=55),
        ack(address + 1),
        ack(address, selector=b"\x01\x00"),
        ack(address),
    ]


@pytest.fixture
async def client() -> AsyncIterator[DynetClient]:
    async with (
        FakeGateway(frame_replies=frame_replies) as gateway,
        DynetClient(gateway.host, gateway.port, send_interval=0) as client,
    ):
        yield client


def write(address: int, data: bytes) -> Dynet2WriteMemory:
    return Dynet2WriteMemory(
        device_code=0xDA,
        box=1,
        target_device_code=0xB3,
        target_box=54,
        address=address,
        data=data,
    )


def test_system_builders_write_and_ack_decode() -> None:
    assert decode(WRITE) == write(0x0223, b"\xf8")
    assert write(0x0223, b"\xf8").to_frame() == WRITE
    assert decode(ACK) == ack(0x0223)


async def test_write_waits_for_its_ack(client: DynetClient) -> None:
    assert await client.query(write(0x0223, b"\xf8")) == ack(0x0223)


async def test_an_unexpected_result_raises(client: DynetClient) -> None:
    with pytest.raises(MemoryWriteError) as caught:
        await client.query(write(FAILING_ADDRESS, b"\x00"))
    assert caught.value.reply == ack(FAILING_ADDRESS, result=b"\x00\x13")
    assert str(caught.value) == (
        "device 0xb3 box 54 answered a write at 0x0300 with result 00 13"
    )


async def test_no_ack_times_out() -> None:
    async with (
        FakeGateway() as gateway,
        DynetClient(gateway.host, gateway.port, send_interval=0) as client,
    ):
        with pytest.raises(TimeoutError):
            await client.query(write(0x0223, b"\xf8"), timeout=0.05)


async def test_write_to_all_devices_is_refused(client: DynetClient) -> None:
    code, box = DYNET2_ALL_DEVICES
    request = Dynet2WriteMemory(
        device_code=0xDA,
        box=1,
        target_device_code=code,
        target_box=box,
        address=0,
        data=b"\x00",
    )
    with pytest.raises(ValueError, match="never write to all devices"):
        await client.query(request)
