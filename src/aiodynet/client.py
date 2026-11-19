"""An asyncio client for a DyNet TCP gateway."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Self, overload

from .dynet2 import Dynet2Frame
from .errors import DynetConnectionError, NotConnectedError
from .framing import PacketFramer
from .messages import (
    AnyMessage,
    ChannelLevelReport,
    Message,
    PresetReport,
    RequestChannelLevel,
    RequestPreset,
    decode,
)
from .packet import Packet

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

_LOGGER = logging.getLogger(__name__)

DEFAULT_CONNECT_TIMEOUT: Final = 10.0
DEFAULT_SEND_INTERVAL: Final = 0.2
DEFAULT_RECONNECT_DELAY: Final = 1.0
DEFAULT_MAX_RECONNECT_DELAY: Final = 60.0

_KEEPALIVE_IDLE: Final = 30
_KEEPALIVE_INTERVAL: Final = 10
_KEEPALIVE_COUNT: Final = 3
_READ_SIZE: Final = 1024


@dataclass(frozen=True, slots=True)
class Received:
    """A packet read from the bus, with its decoded meaning."""

    packet: Packet
    """The packet exactly as received."""

    message: AnyMessage
    """What it means. `UnknownMessage` for anything without a typed message."""


type PacketCallback = Callable[[Received], None]
type ConnectionCallback = Callable[[bool], None]
type Dynet2Callback = Callable[[Dynet2Frame], None]


async def check_connection(
    host: str, port: int, *, timeout: float = DEFAULT_CONNECT_TIMEOUT
) -> None:
    """Check that a gateway accepts TCP connections, then disconnect at once.

    Sends nothing to the bus. Raises `DynetConnectionError` if the gateway
    can't be reached within `timeout` seconds. Use it to validate a host and
    port, for example in a config flow, without starting a client.
    """
    _, writer = await _open(host, port, timeout)
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()


async def _open(
    host: str, port: int, timeout: float
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        async with asyncio.timeout(timeout):
            return await asyncio.open_connection(host, port)
    except TimeoutError as err:
        msg = f"Timed out connecting to {host}:{port} after {timeout} s"
        raise DynetConnectionError(msg) from err
    except OSError as err:
        msg = f"Can't connect to {host}:{port}: {err}"
        raise DynetConnectionError(msg) from err


def _enable_keepalive(writer: asyncio.StreamWriter) -> None:
    """Turn on TCP keepalive, so a dead gateway is noticed on a quiet bus."""
    sock: socket.socket | None = writer.get_extra_info("socket")
    if sock is None:  # pragma: no cover - only for exotic transports
        return
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    # Linux calls the idle option TCP_KEEPIDLE; macOS calls it TCP_KEEPALIVE.
    idle = getattr(socket, "TCP_KEEPIDLE", None) or getattr(
        socket, "TCP_KEEPALIVE", None
    )
    for option, value in (
        (idle, _KEEPALIVE_IDLE),
        (getattr(socket, "TCP_KEEPINTVL", None), _KEEPALIVE_INTERVAL),
        (getattr(socket, "TCP_KEEPCNT", None), _KEEPALIVE_COUNT),
    ):
        if option is not None:
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.IPPROTO_TCP, option, value)


class DynetClient:
    """A connection to one DyNet gateway, kept open until you close it.

    Use it as an async context manager::

        async with DynetClient("192.0.2.10", 50000) as client:
            client.subscribe(print)
            await client.send(PresetSelect(area=1, preset=1))

    or call `connect` and `close` yourself. `connect` raises if the gateway
    can't be reached; after that the client reconnects on its own, with a
    growing delay, until `close` is called. `close` stops everything the
    client started, and it is safe to call at any time and more than once.

    Packets are written one at a time, `send_interval` seconds apart, because
    gateways and the RS-485 bus behind them drop packets that arrive faster.

    Callbacks run in the event loop, in the order they were added. They must
    not block. An exception in one is logged and doesn't affect the others.
    """

    def __init__(  # noqa: PLR0913
        self,
        host: str,
        port: int,
        *,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        send_interval: float = DEFAULT_SEND_INTERVAL,
        reconnect_delay: float = DEFAULT_RECONNECT_DELAY,
        max_reconnect_delay: float = DEFAULT_MAX_RECONNECT_DELAY,
    ) -> None:
        """Set up the client. Nothing connects until `connect`."""
        for name, value in (
            ("connect_timeout", connect_timeout),
            ("reconnect_delay", reconnect_delay),
            ("max_reconnect_delay", max_reconnect_delay),
        ):
            if value <= 0:
                msg = f"{name} must be positive, got {value}"
                raise ValueError(msg)
        if send_interval < 0:
            msg = f"send_interval can't be negative, got {send_interval}"
            raise ValueError(msg)
        self.host: Final = host
        self.port: Final = port
        self._connect_timeout = connect_timeout
        self._send_interval = send_interval
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max(max_reconnect_delay, reconnect_delay)

        self._packet_callbacks: list[PacketCallback] = []
        self._connection_callbacks: list[ConnectionCallback] = []
        self._dynet2_callbacks: list[Dynet2Callback] = []
        self._writer: asyncio.StreamWriter | None = None
        self._queue: asyncio.Queue[tuple[bytes, asyncio.Future[None]]] = asyncio.Queue()
        self._run_task: asyncio.Task[None] | None = None
        self._started = False
        self._closed = False

    @property
    def connected(self) -> bool:
        """Whether the client is connected to the gateway right now."""
        return self._writer is not None

    @property
    def closed(self) -> bool:
        """Whether `close` has been called. A closed client can't reconnect."""
        return self._closed

    async def connect(self) -> None:
        """Connect to the gateway and start receiving.

        Raises `DynetConnectionError` if the first attempt fails; the client
        is then left as it was, and `connect` can be called again. Once
        connected, lost connections are re-established in the background.
        """
        if self._closed:
            msg = "This client was closed; create a new one"
            raise RuntimeError(msg)
        if self._started:
            msg = "Already connected"
            raise RuntimeError(msg)
        reader, writer = await _open(self.host, self.port, self._connect_timeout)
        if self.closed:  # close() was called while connecting
            writer.close()
            msg = "Closed while connecting"
            raise NotConnectedError(msg)
        self._started = True
        self._attach(writer)
        self._run_task = asyncio.create_task(
            self._run(reader), name=f"aiodynet {self.host}:{self.port}"
        )

    async def close(self) -> None:
        """Disconnect and stop all background work.

        No callbacks run once this is called, and anything still waiting in
        `send` raises `NotConnectedError`.
        """
        if self._closed:
            return
        self._closed = True
        self._packet_callbacks.clear()
        self._connection_callbacks.clear()
        self._dynet2_callbacks.clear()
        if self._run_task is not None:
            self._run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._run_task
            self._run_task = None
        # The task may have been cancelled before it ever ran its cleanup.
        if (writer := self._writer) is not None:
            self._writer = None
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
        self._fail_queued()

    async def __aenter__(self) -> Self:
        """Connect; see `connect`."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close; see `close`."""
        await self.close()

    def subscribe(self, callback: PacketCallback) -> Callable[[], None]:
        """Call `callback` with every packet read from the bus.

        Returns a function that unsubscribes. Calling it twice is harmless.
        """
        return self._add(self._packet_callbacks, callback)

    def subscribe_dynet2(self, callback: Dynet2Callback) -> Callable[[], None]:
        """Call `callback` with every DyNet 2 frame read from the bus, raw.

        Gateways send these alongside DyNet 1 packets, for example a greeting
        after each connect and device sign-on replies. They aren't decoded,
        and `subscribe` callbacks never see them. Returns a function that
        unsubscribes.
        """
        return self._add(self._dynet2_callbacks, callback)

    def subscribe_connection(self, callback: ConnectionCallback) -> Callable[[], None]:
        """Call `callback(True)` on reconnecting and `callback(False)` on a drop.

        Not called for the first `connect` or for `close`, because the caller
        already knows about those. Returns a function that unsubscribes.
        """
        return self._add(self._connection_callbacks, callback)

    async def send(self, message: Message | Packet) -> None:
        """Send a message or a raw packet, and wait until it has been written.

        Raises `NotConnectedError` if the client isn't connected, or if the
        connection drops before the packet is written. Nothing is queued for
        later, so a command is never sent long after it was asked for.
        """
        packet = message if isinstance(message, Packet) else message.to_packet()
        if not self.connected:
            msg = f"Not connected to {self.host}:{self.port}"
            raise NotConnectedError(msg)
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._queue.put_nowait((bytes(packet), future))
        await future

    @overload
    async def query(
        self, request: RequestPreset, *, timeout: float = ...
    ) -> PresetReport: ...
    @overload
    async def query(
        self, request: RequestChannelLevel, *, timeout: float = ...
    ) -> ChannelLevelReport: ...
    async def query(
        self, request: RequestPreset | RequestChannelLevel, *, timeout: float = 2.0
    ) -> PresetReport | ChannelLevelReport:
        """Send a request and wait for the matching report.

        A `RequestPreset` waits for a `PresetReport` from the same area; a
        `RequestChannelLevel` waits for a `ChannelLevelReport` from the same
        area and channel. Raises `TimeoutError` if none arrives in `timeout`
        seconds (a request can go unanswered when no device owns the area).
        Every subscriber sees the report as usual.
        """
        loop = asyncio.get_running_loop()
        result: asyncio.Future[PresetReport | ChannelLevelReport] = loop.create_future()

        def match(received: Received) -> None:
            message = received.message
            if result.done():
                return
            if isinstance(request, RequestPreset):
                if isinstance(message, PresetReport) and message.area == request.area:
                    result.set_result(message)
            elif (
                isinstance(message, ChannelLevelReport)
                and message.area == request.area
                and message.channel == request.channel
            ):
                result.set_result(message)

        unsubscribe = self.subscribe(match)
        try:
            async with asyncio.timeout(timeout):
                await self.send(request)
                return await result
        finally:
            unsubscribe()

    # Internals

    @staticmethod
    def _add[T](callbacks: list[T], callback: T) -> Callable[[], None]:
        callbacks.append(callback)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                callbacks.remove(callback)

        return unsubscribe

    def _attach(self, writer: asyncio.StreamWriter) -> None:
        """Make `writer` the current connection."""
        _enable_keepalive(writer)
        _LOGGER.debug("Connected to %s:%s", self.host, self.port)
        self._writer = writer

    async def _run(self, reader: asyncio.StreamReader) -> None:
        """Hold a connection open, and reconnect whenever it drops."""
        while True:
            await self._serve(reader)
            self._notify_connection(connected=False)
            reader, writer = await self._reconnect()
            self._attach(writer)
            self._notify_connection(connected=True)

    async def _serve(self, reader: asyncio.StreamReader) -> None:
        """Read and write on the current connection until it drops."""
        writer = self._writer
        assert writer is not None  # noqa: S101 - set by _attach
        write_task = asyncio.create_task(self._write_loop(writer))
        try:
            await self._read_loop(reader)
        finally:
            self._writer = None
            write_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await write_task
            self._fail_queued()
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        framer = PacketFramer()
        while True:
            try:
                data = await reader.read(_READ_SIZE)
            except OSError as err:
                _LOGGER.warning("Connection to %s lost: %s", self.host, err)
                return
            if not data:
                _LOGGER.warning("Gateway %s closed the connection", self.host)
                return
            for item in framer.feed_all(data):
                if isinstance(item, Packet):
                    self._dispatch(item)
                else:
                    self._dispatch_dynet2(item)

    async def _write_loop(self, writer: asyncio.StreamWriter) -> None:
        while True:
            data, future = await self._queue.get()
            if future.done():  # the sender gave up (cancelled or timed out)
                continue
            try:
                writer.write(data)
                await writer.drain()
            except OSError as err:
                future.set_exception(NotConnectedError(f"Write failed: {err}"))
                writer.close()  # the read loop notices and reconnects
                return
            future.set_result(None)
            _LOGGER.debug("Sent %s", data.hex(" "))
            await asyncio.sleep(self._send_interval)

    async def _reconnect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        delay = self._reconnect_delay
        while True:
            await asyncio.sleep(delay)
            try:
                return await _open(self.host, self.port, self._connect_timeout)
            except DynetConnectionError as err:
                _LOGGER.debug("Reconnect failed, retrying in %s s: %s", delay, err)
                delay = min(delay * 2, self._max_reconnect_delay)

    def _dispatch(self, packet: Packet) -> None:
        received = Received(packet=packet, message=decode(packet))
        _LOGGER.debug("Received %s: %s", packet.hex(), received.message)
        for callback in tuple(self._packet_callbacks):
            try:
                callback(received)
            except Exception:
                _LOGGER.exception("Error in packet callback %r", callback)

    def _dispatch_dynet2(self, frame: Dynet2Frame) -> None:
        _LOGGER.debug("Received DyNet 2 frame (%d bytes) %s", len(frame), frame.hex())
        for callback in tuple(self._dynet2_callbacks):
            try:
                callback(frame)
            except Exception:
                _LOGGER.exception("Error in DyNet 2 callback %r", callback)

    def _notify_connection(self, *, connected: bool) -> None:
        for callback in tuple(self._connection_callbacks):
            try:
                callback(connected)
            except Exception:
                _LOGGER.exception("Error in connection callback %r", callback)

    def _fail_queued(self) -> None:
        while not self._queue.empty():
            _, future = self._queue.get_nowait()
            if not future.done():
                future.set_exception(
                    NotConnectedError("Connection lost before sending")
                )
