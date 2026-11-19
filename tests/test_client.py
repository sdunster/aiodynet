import asyncio
import logging
import socket
import time
from collections.abc import Callable
from dataclasses import replace
from unittest.mock import patch

import pytest

from aiodynet import (
    ChannelLevelReport,
    Dynet2Frame,
    Dynet2SignonReply,
    DynetClient,
    DynetConnectionError,
    Level,
    NotConnectedError,
    Packet,
    PresetReport,
    PresetSelect,
    Received,
    RequestChannelLevel,
    RequestPreset,
    RequestSignon,
    UnknownDynet2Message,
    UnknownMessage,
    check_connection,
)
from aiodynet.testing import FakeGateway

from .conftest import ClientFactory


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


async def until(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    """Wait until `condition()` is true."""
    async with asyncio.timeout(timeout):
        while not condition():  # noqa: ASYNC110 - polling test state
            await asyncio.sleep(0.005)


# Construction


@pytest.mark.parametrize(
    "kwargs",
    [
        {"connect_timeout": 0},
        {"reconnect_delay": -1},
        {"max_reconnect_delay": 0},
        {"send_interval": -0.1},
    ],
)
def test_rejects_bad_timings(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError, match=r"must be positive|can't be negative"):
        DynetClient("127.0.0.1", 1, **kwargs)


def test_starts_disconnected() -> None:
    client = DynetClient("192.0.2.1", 50000)
    assert client.host == "192.0.2.1"
    assert client.port == 50000
    assert not client.connected
    assert not client.closed


# Connecting and closing


async def test_context_manager(gateway: FakeGateway) -> None:
    async with DynetClient(gateway.host, gateway.port) as client:
        was_connected = client.connected
        await gateway.wait_for_client()
    assert was_connected
    assert client.closed
    assert not client.connected
    await until(lambda: gateway.client_count == 0)


async def test_connect_failure_leaves_client_reusable(gateway: FakeGateway) -> None:
    client = DynetClient(gateway.host, free_port(), connect_timeout=1)
    with pytest.raises(DynetConnectionError, match="Can't connect"):
        await client.connect()
    assert not client.connected
    await client.close()


async def test_connect_timeout() -> None:
    async def hang(*_: object) -> None:
        await asyncio.sleep(10)

    client = DynetClient("192.0.2.1", 1, connect_timeout=0.01)
    with (
        patch("asyncio.open_connection", hang),
        pytest.raises(DynetConnectionError, match="Timed out"),
    ):
        await client.connect()


async def test_connect_twice(make_client: ClientFactory) -> None:
    client = make_client()
    await client.connect()
    with pytest.raises(RuntimeError, match="Already connected"):
        await client.connect()


async def test_connect_after_close(make_client: ClientFactory) -> None:
    client = make_client()
    await client.close()
    with pytest.raises(RuntimeError, match="was closed"):
        await client.connect()


async def test_close_while_connecting(gateway: FakeGateway) -> None:
    client = DynetClient(gateway.host, gateway.port)
    started = asyncio.Event()
    real_open = asyncio.open_connection

    async def slow_open(
        host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        started.set()
        await asyncio.sleep(0.05)
        return await real_open(host, port)

    with patch("asyncio.open_connection", slow_open):
        connecting = asyncio.create_task(client.connect())
        await started.wait()
        await client.close()
        with pytest.raises(NotConnectedError, match="Closed while connecting"):
            await connecting
    assert not client.connected


async def test_close_is_idempotent(make_client: ClientFactory) -> None:
    client = make_client()
    await client.connect()
    await client.close()
    await client.close()
    assert client.closed


async def test_keepalive_is_enabled(make_client: ClientFactory) -> None:
    calls: list[tuple[int, int, int]] = []
    real = socket.socket.setsockopt

    def spy(sock: socket.socket, level: int, option: int, value: int) -> None:
        calls.append((level, option, value))
        real(sock, level, option, value)

    with patch.object(socket.socket, "setsockopt", spy):
        await make_client().connect()
    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in calls


async def test_check_connection(gateway: FakeGateway) -> None:
    await check_connection(gateway.host, gateway.port)
    await until(lambda: gateway.connections == 1 and gateway.client_count == 0)
    assert gateway.received == []


async def test_check_connection_failure() -> None:
    with pytest.raises(DynetConnectionError):
        await check_connection("127.0.0.1", free_port(), timeout=1)


# Receiving


async def test_dispatches_decoded_packets(
    gateway: FakeGateway, make_client: ClientFactory
) -> None:
    client = make_client()
    seen: list[Received] = []
    client.subscribe(seen.append)
    await client.connect()
    await gateway.wait_for_client()

    await gateway.send(
        PresetSelect(area=5, preset=2), Packet.from_hex("5c0102030405ff")
    )
    await until(lambda: len(seen) == 2)
    assert seen[0].message == PresetSelect(area=5, preset=2)
    assert seen[0].raw == PresetSelect(area=5, preset=2).to_packet()
    assert isinstance(seen[1].message, UnknownMessage)


async def test_partial_packets_and_noise(
    gateway: FakeGateway, make_client: ClientFactory
) -> None:
    client = make_client()
    seen: list[Received] = []
    client.subscribe(seen.append)
    await client.connect()
    await gateway.wait_for_client()

    raw = bytes(PresetSelect(area=1, preset=1).to_packet())
    await gateway.send(b"\x09" + raw[:3])
    await asyncio.sleep(0.02)
    await gateway.send(raw[3:])
    await until(lambda: len(seen) == 1)
    assert seen[0].message == PresetSelect(area=1, preset=1)


GREETING = Dynet2Frame.from_hex(
    "ac 04 82 dc 00 5a aa 55 55 00 0c 00 00 00 00 00 00 00 43 c8"
)
D5SEN = Dynet2SignonReply(
    device_code=0xB3,
    box=42,
    firmware_major=0x02,
    firmware_minor=0x11,
    max_rx_size=128,
    max_tx_size=128,
)


async def test_dynet2_frames_reach_subscribers_in_order(
    gateway: FakeGateway, make_client: ClientFactory
) -> None:
    client = make_client()
    seen: list[Received] = []
    client.subscribe(seen.append)
    await client.connect()
    await gateway.wait_for_client()

    await gateway.send(GREETING, PresetSelect(area=1, preset=1), D5SEN)
    await until(lambda: len(seen) == 3)
    assert [r.raw for r in seen] == [
        GREETING,
        PresetSelect(area=1, preset=1).to_packet(),
        D5SEN.to_frame(),
    ]
    assert [r.message for r in seen] == [
        UnknownDynet2Message(frame=GREETING),
        PresetSelect(area=1, preset=1),
        D5SEN,
    ]


async def test_noise_reaches_noise_subscribers_in_order(
    gateway: FakeGateway, make_client: ClientFactory
) -> None:
    client = make_client()
    seen: list[object] = []
    client.subscribe(lambda received: seen.append(received.message))
    client.subscribe_noise(seen.append)
    await client.connect()
    await gateway.wait_for_client()

    preset = PresetSelect(area=1, preset=1)
    await gateway.send(b"\x09\x00" + bytes(preset.to_packet()) + b"\x1c\x01")
    await until(lambda: len(seen) == 2)
    assert seen == [b"\x09\x00", preset]

    # The partial packet left when the gateway drops the connection is noise too.
    await gateway.disconnect_clients()
    await until(lambda: len(seen) == 3)
    assert seen[2] == b"\x1c\x01"


async def test_noise_callback_errors_are_contained(
    gateway: FakeGateway,
    make_client: ClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = make_client()
    noise: list[bytes] = []

    def broken(_: bytes) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    client.subscribe_noise(broken)
    client.subscribe_noise(noise.append)
    await client.connect()
    await gateway.wait_for_client()
    await gateway.send(b"\x09")
    await until(lambda: noise == [b"\x09"])
    assert "Error in noise callback" in caplog.text


async def test_unsubscribe(gateway: FakeGateway, make_client: ClientFactory) -> None:
    client = make_client()
    first: list[Received] = []
    second: list[Received] = []
    unsubscribe = client.subscribe(first.append)
    client.subscribe(second.append)
    await client.connect()
    await gateway.wait_for_client()

    unsubscribe()
    unsubscribe()  # harmless
    await gateway.send(PresetSelect(area=1, preset=1))
    await until(lambda: len(second) == 1)
    assert first == []


async def test_callback_errors_are_contained(
    gateway: FakeGateway,
    make_client: ClientFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = make_client()
    seen: list[Received] = []

    def broken(_: Received) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    client.subscribe(broken)
    client.subscribe(seen.append)
    await client.connect()
    await gateway.wait_for_client()

    await gateway.send(PresetSelect(area=1, preset=1), PresetSelect(area=1, preset=2))
    await until(lambda: len(seen) == 2)
    assert "Error in packet callback" in caplog.text


async def test_a_callback_may_unsubscribe_itself(
    gateway: FakeGateway, make_client: ClientFactory
) -> None:
    client = make_client()
    seen: list[Received] = []

    def once(received: Received) -> None:
        seen.append(received)
        unsubscribe()

    unsubscribe = client.subscribe(once)
    await client.connect()
    await gateway.wait_for_client()
    await gateway.send(PresetSelect(area=1, preset=1), PresetSelect(area=1, preset=2))
    await asyncio.sleep(0.05)
    assert len(seen) == 1


# Sending


async def test_send_message_and_packet(
    gateway: FakeGateway, make_client: ClientFactory
) -> None:
    client = make_client()
    await client.connect()
    await client.send(PresetSelect(area=2, preset=1))
    await client.send(Packet.from_hex("1c0100630000ff"))
    assert await gateway.next_packet() == Packet.from_hex("1c0200000000ffe3")
    assert await gateway.next_packet() == Packet.from_hex("1c0100630000ff81")


async def test_send_when_not_connected() -> None:
    client = DynetClient("192.0.2.1", 1)
    with pytest.raises(NotConnectedError, match="Not connected"):
        await client.send(PresetSelect(area=1, preset=1))


async def test_send_after_close(make_client: ClientFactory) -> None:
    client = make_client()
    await client.connect()
    await client.close()
    with pytest.raises(NotConnectedError):
        await client.send(PresetSelect(area=1, preset=1))


async def test_sends_are_paced(
    gateway: FakeGateway, make_client: ClientFactory
) -> None:
    client = make_client(send_interval=0.05)
    await client.connect()
    start = time.monotonic()
    await asyncio.gather(
        *(client.send(PresetSelect(area=1, preset=p)) for p in (1, 2, 3))
    )
    # The third packet waits for two intervals.
    assert time.monotonic() - start >= 0.1
    await until(lambda: len(gateway.received) == 3)
    assert [p[3] for p in gateway.received] == [0, 1, 2]


async def test_default_pacing_matches_dynalite_devices() -> None:
    client = DynetClient("192.0.2.1", 1)
    assert client._send_interval == 0.2


async def test_cancelled_send_is_skipped(
    gateway: FakeGateway, make_client: ClientFactory
) -> None:
    client = make_client(send_interval=0.1)
    await client.connect()
    first = asyncio.create_task(client.send(PresetSelect(area=1, preset=1)))
    second = asyncio.create_task(client.send(PresetSelect(area=1, preset=2)))
    third = asyncio.create_task(client.send(PresetSelect(area=1, preset=3)))
    await first
    second.cancel()
    await third
    await until(lambda: len(gateway.received) == 2)
    assert [p[3] for p in gateway.received] == [0, 2]


async def test_queued_sends_fail_when_the_connection_drops(
    gateway: FakeGateway, make_client: ClientFactory
) -> None:
    client = make_client(send_interval=0.2)
    await client.connect()
    await client.send(PresetSelect(area=1, preset=1))
    pending = asyncio.create_task(client.send(PresetSelect(area=1, preset=2)))
    await asyncio.sleep(0.02)
    await gateway.disconnect_clients()
    with pytest.raises(NotConnectedError, match="Connection lost"):
        await pending


async def test_queued_sends_fail_on_close(make_client: ClientFactory) -> None:
    client = make_client(send_interval=0.2)
    await client.connect()
    await client.send(PresetSelect(area=1, preset=1))
    pending = asyncio.create_task(client.send(PresetSelect(area=1, preset=2)))
    await asyncio.sleep(0.02)
    await client.close()
    with pytest.raises(NotConnectedError):
        await pending


async def test_write_error_fails_the_send_and_reconnects(
    gateway: FakeGateway, make_client: ClientFactory
) -> None:
    client = make_client()
    await client.connect()
    await gateway.wait_for_client()
    writer = client._writer
    assert writer is not None

    with patch.object(writer, "write", side_effect=OSError("broken pipe")):
        with pytest.raises(NotConnectedError, match="Write failed: broken pipe"):
            await client.send(PresetSelect(area=1, preset=1))
        await until(lambda: gateway.connections == 2 and client.connected)
    await client.send(PresetSelect(area=1, preset=2))
    assert await gateway.next_packet() == PresetSelect(area=1, preset=2).to_packet()


# Reconnecting


async def test_reconnects_after_the_gateway_drops(
    gateway: FakeGateway, make_client: ClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    client = make_client()
    states: list[bool] = []
    client.subscribe_connection(states.append)
    seen: list[Received] = []
    client.subscribe(seen.append)
    await client.connect()
    await gateway.wait_for_client()

    await gateway.disconnect_clients()
    await until(lambda: states == [False, True])
    assert "closed the connection" in caplog.text
    await gateway.wait_for_client()
    assert client.connected
    assert gateway.connections == 2

    # Subscriptions survive the reconnect.
    await gateway.send(PresetSelect(area=1, preset=1))
    await until(lambda: len(seen) == 1)


async def test_keeps_retrying_with_backoff(caplog: pytest.LogCaptureFixture) -> None:
    async with FakeGateway() as gateway:
        port = gateway.port
        client = DynetClient(
            gateway.host,
            port,
            reconnect_delay=0.01,
            max_reconnect_delay=0.02,
            connect_timeout=1,
        )
        await client.connect()
        states: list[bool] = []
        client.subscribe_connection(states.append)
    # The gateway is gone. Let a few attempts fail.
    caplog.set_level(logging.DEBUG, logger="aiodynet")
    await until(lambda: caplog.text.count("Reconnect failed") >= 3)
    assert states == [False]
    assert "retrying in 0.02 s" in caplog.text

    # Bring a gateway back on the same port.
    async with FakeGateway(port=port) as revived:
        await until(lambda: states == [False, True])
        await revived.wait_for_client()
        await client.close()


@pytest.mark.usefixtures("gateway")
async def test_read_error_triggers_reconnect(
    make_client: ClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    client = make_client()
    states: list[bool] = []
    client.subscribe_connection(states.append)
    real_open = asyncio.open_connection

    async def open_with_failing_read(
        host: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Open a connection whose first read fails; only the client's reader."""
        reader, writer = await real_open(host, port)
        real_read = reader.read

        async def fail_once(n: int = -1) -> bytes:
            del n
            reader.read = real_read  # type: ignore[method-assign]
            raise ConnectionResetError("reset by peer")

        reader.read = fail_once  # type: ignore[method-assign]
        return reader, writer

    with patch("asyncio.open_connection", open_with_failing_read):
        await client.connect()
    await until(lambda: states == [False, True])
    assert "Connection to 127.0.0.1 lost: reset by peer" in caplog.text


async def test_connection_callback_errors_are_contained(
    gateway: FakeGateway, make_client: ClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    client = make_client()
    states: list[bool] = []

    def broken(_: bool) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    client.subscribe_connection(broken)
    client.subscribe_connection(states.append)
    await client.connect()
    await gateway.wait_for_client()
    await gateway.disconnect_clients()
    await until(lambda: states == [False, True])
    assert "Error in connection callback" in caplog.text


async def test_no_callbacks_after_close(
    gateway: FakeGateway, make_client: ClientFactory
) -> None:
    client = make_client()
    states: list[bool] = []
    client.subscribe_connection(states.append)
    await client.connect()
    await gateway.wait_for_client()
    await client.close()
    await gateway.send(PresetSelect(area=1, preset=1))
    await asyncio.sleep(0.05)
    assert states == []


# Queries


def answer(
    packet: Packet,
) -> list[PresetReport | ChannelLevelReport | Packet | Dynet2Frame]:
    """Reply the way a site with area 1 (channels 1-2) and a D5Sen would."""
    match packet:
        case _ if packet == RequestSignon(device_code=0xB3, box=42).to_packet():
            return [
                # Another device's sign-on, then the one asked for.
                replace(D5SEN, box=43).to_frame(),
                D5SEN.to_frame(),
            ]
        case _ if packet == RequestPreset(area=1).to_packet():
            return [
                PresetReport(area=9, preset=3),  # someone else's answer
                PresetReport(area=1, preset=4),
            ]
        case _ if packet == RequestChannelLevel(area=1, channel=2).to_packet():
            return [
                PresetReport(area=1, preset=4),  # not a level report
                ChannelLevelReport(
                    area=1, channel=1, target_level=Level.OFF, actual_level=Level.OFF
                ),
                ChannelLevelReport(
                    area=1, channel=2, target_level=Level.FULL, actual_level=Level(0x80)
                ),
            ]
        case _:
            return []


async def test_query_preset() -> None:
    async with (
        FakeGateway(replies=answer) as gateway,
        DynetClient(gateway.host, gateway.port, send_interval=0) as client,
    ):
        seen: list[Received] = []
        client.subscribe(seen.append)
        report = await client.query(RequestPreset(area=1))
        assert report == PresetReport(area=1, preset=4)
        await until(lambda: len(seen) == 2)  # subscribers still see everything


async def test_query_channel_level() -> None:
    async with (
        FakeGateway(replies=answer) as gateway,
        DynetClient(gateway.host, gateway.port, send_interval=0) as client,
    ):
        report = await client.query(RequestChannelLevel(area=1, channel=2))
        assert report.channel == 2
        assert report.actual_level == Level(0x80)


async def test_query_signon() -> None:
    async with (
        FakeGateway(replies=answer) as gateway,
        DynetClient(gateway.host, gateway.port, send_interval=0) as client,
    ):
        reply = await client.query(RequestSignon(device_code=0xB3, box=42))
        assert reply == D5SEN


async def test_query_rejects_a_signon_to_all_devices() -> None:
    client = DynetClient("127.0.0.1", 1)
    with pytest.raises(ValueError, match="needs one device"):
        await client.query(RequestSignon(device_code=0xAA, box=0x55))


async def test_query_takes_the_first_match_only() -> None:
    def twice(packet: Packet) -> list[PresetReport]:
        del packet
        return [PresetReport(area=1, preset=1), PresetReport(area=1, preset=2)]

    async with (
        FakeGateway(replies=twice) as gateway,
        DynetClient(gateway.host, gateway.port, send_interval=0) as client,
    ):
        assert (await client.query(RequestPreset(area=1))).preset == 1


async def test_query_timeout(make_client: ClientFactory) -> None:
    client = make_client()
    await client.connect()
    with pytest.raises(TimeoutError):
        await client.query(RequestPreset(area=7), timeout=0.05)
    # The temporary subscription is gone.
    assert client._packet_callbacks == []


async def test_query_when_not_connected() -> None:
    client = DynetClient("192.0.2.1", 1)
    with pytest.raises(NotConnectedError):
        await client.query(RequestPreset(area=1))
    assert client._packet_callbacks == []


async def test_keepalive_tolerates_missing_options(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("TCP_KEEPIDLE", "TCP_KEEPALIVE", "TCP_KEEPINTVL", "TCP_KEEPCNT"):
        monkeypatch.delattr(socket, name, raising=False)
    client = make_client()
    await client.connect()
    assert client.connected


async def test_keepalive_tolerates_unsupported_options(
    make_client: ClientFactory,
) -> None:
    real = socket.socket.setsockopt

    def picky(sock: socket.socket, level: int, option: int, value: int) -> None:
        if level == socket.IPPROTO_TCP and option != socket.TCP_NODELAY:
            raise OSError("not supported")
        real(sock, level, option, value)

    with patch.object(socket.socket, "setsockopt", picky):
        client = make_client()
        await client.connect()
    assert client.connected


async def test_close_with_a_cancelled_send_still_queued(
    make_client: ClientFactory,
) -> None:
    client = make_client(send_interval=0.2)
    await client.connect()
    await client.send(PresetSelect(area=1, preset=1))
    cancelled = asyncio.create_task(client.send(PresetSelect(area=1, preset=2)))
    await asyncio.sleep(0.01)
    cancelled.cancel()
    await client.close()
    assert cancelled.cancelled()
