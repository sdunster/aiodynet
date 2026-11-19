import asyncio
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from aiodynet import Dynet2Frame, Packet, PresetReport, PresetSelect, RequestPreset
from aiodynet.testing import FakeGateway


async def wait_until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not condition():  # noqa: ASYNC110 - polling test state
            await asyncio.sleep(0.005)


async def connect(
    gateway: FakeGateway,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.open_connection(gateway.host, gateway.port)
    await gateway.wait_for_client()
    return reader, writer


async def test_records_and_frames_what_clients_send() -> None:
    async with FakeGateway() as gateway:
        _, writer = await connect(gateway)
        raw = bytes(PresetSelect(area=1, preset=1).to_packet())
        writer.write(b"\x00" + raw[:4])
        await writer.drain()
        writer.write(raw[4:])
        await writer.drain()
        assert await gateway.next_packet() == PresetSelect(area=1, preset=1).to_packet()
        assert gateway.received == [PresetSelect(area=1, preset=1).to_packet()]
        assert bytes(gateway.raw_received) == b"\x00" + raw
        writer.close()
        await writer.wait_closed()


async def test_sends_messages_packets_and_bytes_to_every_client() -> None:
    async with FakeGateway() as gateway:
        reader1, writer1 = await connect(gateway)
        reader2, writer2 = await connect(gateway)
        await wait_until(lambda: gateway.client_count == 2)
        assert gateway.connections == 2
        packet = Packet.from_hex("5c0102030405ff")
        await gateway.send(PresetReport(area=1, preset=1), packet, b"\x01")
        expected = (
            bytes(PresetReport(area=1, preset=1).to_packet()) + bytes(packet) + b"\x01"
        )
        for reader in (reader1, reader2):
            assert await reader.readexactly(17) == expected
        for writer in (writer1, writer2):
            writer.close()
            await writer.wait_closed()


async def test_replies() -> None:
    def replies(packet: Packet) -> list[PresetReport]:
        if packet == RequestPreset(area=3).to_packet():
            return [PresetReport(area=3, preset=2)]
        return []

    async with FakeGateway(replies=replies) as gateway:
        reader, writer = await connect(gateway)
        writer.write(bytes(PresetSelect(area=3, preset=1).to_packet()))
        writer.write(bytes(RequestPreset(area=3).to_packet()))
        await writer.drain()
        reply = await reader.readexactly(8)
        assert Packet.from_bytes(reply) == PresetReport(area=3, preset=2).to_packet()
        writer.close()
        await writer.wait_closed()


async def test_client_disconnect_is_noticed() -> None:
    async with FakeGateway() as gateway:
        _, writer = await connect(gateway)
        writer.close()
        await writer.wait_closed()
        await wait_until(lambda: gateway.client_count == 0)
        with pytest.raises(TimeoutError):
            await gateway.wait_for_client(timeout=0.01)


async def test_one_client_leaving_keeps_the_other() -> None:
    async with FakeGateway() as gateway:
        _, first = await connect(gateway)
        _, second = await connect(gateway)
        await wait_until(lambda: gateway.client_count == 2)
        first.close()
        await first.wait_closed()
        await wait_until(lambda: gateway.client_count == 1)
        await gateway.wait_for_client(timeout=0.01)  # still has a client
        second.close()
        await second.wait_closed()


async def test_disconnect_clients() -> None:
    async with FakeGateway() as gateway:
        reader, writer = await connect(gateway)
        await gateway.disconnect_clients()
        assert gateway.client_count == 0
        assert await reader.read() == b""
        writer.close()
        await writer.wait_closed()


async def test_next_packet_timeout() -> None:
    async with FakeGateway() as gateway:
        with pytest.raises(TimeoutError):
            await gateway.next_packet(timeout=0.01)


async def test_stop_is_safe_without_start_and_twice() -> None:
    gateway = FakeGateway()
    await gateway.stop()
    await gateway.start()
    await gateway.stop()
    await gateway.stop()


async def test_connections_arriving_during_stop_are_aborted() -> None:
    gateway = FakeGateway()
    await gateway.start()
    await gateway.stop()
    writer = MagicMock()
    gateway._handle(MagicMock(), writer)
    writer.transport.abort.assert_called_once()
    assert gateway.connections == 0


async def test_reuses_a_given_port() -> None:
    async with FakeGateway() as first:
        port = first.port
    async with FakeGateway(port=port) as second:
        assert second.port == port


async def test_frames_from_clients_are_not_recorded() -> None:
    frame = Dynet2Frame.build(b"")
    async with FakeGateway() as gateway:
        _, writer = await asyncio.open_connection(gateway.host, gateway.port)
        writer.write(bytes(frame) + bytes(PresetSelect(area=1, preset=1).to_packet()))
        await writer.drain()
        assert await gateway.next_packet() == PresetSelect(area=1, preset=1).to_packet()
        assert gateway.received == [PresetSelect(area=1, preset=1).to_packet()]
        writer.close()
        await writer.wait_closed()


async def test_replies_can_be_raw_bytes() -> None:
    def noisy(packet: Packet) -> list[bytes]:
        del packet
        return [b"\x09\x09"]

    async with FakeGateway(replies=noisy) as gateway:
        reader, writer = await asyncio.open_connection(gateway.host, gateway.port)
        writer.write(bytes(PresetSelect(area=1, preset=1).to_packet()))
        await writer.drain()
        assert await reader.readexactly(2) == b"\x09\x09"
        writer.close()
        await writer.wait_closed()
