"""An asyncio client for a DyNet TCP gateway."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Self, TypeGuard, cast, overload

from .dynet2 import Dynet2Frame
from .errors import DynetConnectionError, MemoryReadError, NotConnectedError
from .framing import PacketFramer
from .messages import (
    DYNET2_ALL_DEVICES,
    AnyMessage,
    ChannelLevelReport,
    DeviceStatusReport,
    Dynet2BuildNumberReply,
    Dynet2ChannelLevelReport,
    Dynet2Command,
    Dynet2DeviceRequest,
    Dynet2MemoryData,
    Dynet2MemoryErrorReply,
    Dynet2Message,
    Dynet2PcbVersionReply,
    Dynet2ReadMemory,
    Dynet2RequestChannelLevel,
    Dynet2RequestSerialNumber,
    Dynet2SerialNumberReply,
    Dynet2SignonReply,
    Dynet2StatusRequest,
    Message,
    PhysicalStateReport,
    PhysicalStateType,
    PresetReport,
    RequestChannelLevel,
    RequestDeviceStatus,
    RequestPreset,
    RequestSignon,
    StatusQuery,
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
    """A packet or DyNet 2 frame read from the bus, with its decoded meaning."""

    raw: Packet | Dynet2Frame
    """The packet or frame exactly as received."""

    message: AnyMessage
    """What it means. `UnknownMessage` or `UnknownDynet2Message` for anything
    without a typed message."""


type PacketCallback = Callable[[Received], None]
type ConnectionCallback = Callable[[bool], None]
type NoiseCallback = Callable[[bytes], None]


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
        self._noise_callbacks: list[NoiseCallback] = []
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
        self._noise_callbacks.clear()
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
        """Call `callback` with everything read from the bus, in order.

        That is every DyNet 1 packet and every DyNet 2 frame (gateways send
        frames such as a greeting after each connect and device sign-on
        replies). Returns a function that unsubscribes. Calling it twice is
        harmless.
        """
        return self._add(self._packet_callbacks, callback)

    def subscribe_noise(self, callback: NoiseCallback) -> Callable[[], None]:
        """Call `callback` with bytes read that weren't part of any packet or frame.

        For tools that keep a full record of the stream. Each call gets one run
        of skipped bytes, in stream order relative to `subscribe` callbacks.
        That includes a partial packet left over when a connection drops.
        Returns a function that unsubscribes.
        """
        return self._add(self._noise_callbacks, callback)

    def subscribe_connection(self, callback: ConnectionCallback) -> Callable[[], None]:
        """Call `callback(True)` on reconnecting and `callback(False)` on a drop.

        Not called for the first `connect` or for `close`, because the caller
        already knows about those. Returns a function that unsubscribes.
        """
        return self._add(self._connection_callbacks, callback)

    async def send(
        self, message: Message | Dynet2Message | Packet | Dynet2Frame
    ) -> None:
        """Send a message, packet or DyNet 2 frame, and wait until it's written.

        DyNet 2 messages and frames go through the same queue as packets, so
        they are paced `send_interval` apart too.

        Raises `NotConnectedError` if the client isn't connected, or if the
        connection drops before the packet is written. Nothing is queued for
        later, so a command is never sent long after it was asked for.
        """
        packet: Packet | Dynet2Frame
        if isinstance(message, (Packet, Dynet2Frame)):
            packet = message
        elif isinstance(message, Dynet2Message):
            packet = message.to_frame()
        else:
            packet = message.to_packet()
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
    @overload
    async def query(
        self, request: RequestSignon, *, timeout: float = ...
    ) -> Dynet2SignonReply: ...
    @overload
    async def query(
        self, request: RequestDeviceStatus, *, timeout: float = ...
    ) -> DeviceStatusReport: ...
    @overload
    async def query(
        self, request: Dynet2RequestSerialNumber, *, timeout: float = ...
    ) -> Dynet2SerialNumberReply: ...
    @overload
    async def query(
        self, request: Dynet2DeviceRequest, *, timeout: float = ...
    ) -> Dynet2SignonReply | Dynet2BuildNumberReply | Dynet2PcbVersionReply: ...
    @overload
    async def query(
        self, request: Dynet2ReadMemory, *, timeout: float = ...
    ) -> Dynet2MemoryData: ...
    @overload
    async def query(
        self, request: Dynet2StatusRequest, *, timeout: float = ...
    ) -> PhysicalStateReport: ...
    @overload
    async def query(
        self, request: Dynet2RequestChannelLevel, *, timeout: float = ...
    ) -> Dynet2ChannelLevelReport: ...
    async def query(self, request: Query, *, timeout: float = 2.0) -> Reply:
        """Send a request and wait for the matching reply.

        - `RequestPreset` waits for a `PresetReport` from the same area.
        - `RequestChannelLevel` waits for a `ChannelLevelReport` from the same
          area and channel.
        - `RequestSignon` waits for a `Dynet2SignonReply` from the same device.
        - `RequestDeviceStatus` waits for a `DeviceStatusReport` of the same
          type from the same device.
        - `Dynet2RequestSerialNumber` waits for that device's
          `Dynet2SerialNumberReply`.
        - `Dynet2DeviceRequest` waits for the device's reply to its command:
          a `Dynet2SignonReply` for `SIGNON` and `BOOTLOADER_SIGNON`, a
          `Dynet2BuildNumberReply` for `FIRMWARE_BUILD` and
          `BOOTLOADER_BUILD`, or a `Dynet2PcbVersionReply` for
          `PCB_VERSION`. Other commands get no reply, so `send` them.
        - `Dynet2ReadMemory` waits for the device's `Dynet2MemoryData` from
          the same address. If the device refuses the read (a
          `Dynet2MemoryErrorReply`, such as for an address past the end of
          its memory), raises `MemoryReadError`.
        - `Dynet2StatusRequest` for `StatusQuery.PE_CELL_LIGHT_LEVEL` waits
          for the device's `PhysicalStateReport` of its light level. The
          device's box must fit one byte, as the reply is a DyNet 1 packet.
        - `Dynet2RequestChannelLevel` waits for the `Dynet2ChannelLevelReport`
          for the same area and channel.

        A request must name one device: a request to all devices has many
        answers, so `subscribe` and `send` it instead. Raises `ValueError`
        for a request `query` can't wait for, and `TimeoutError` if no reply
        arrives in `timeout` seconds (a request can go unanswered when no
        device owns the area). Every subscriber sees the reply as usual.
        """
        _check_query(request)
        loop = asyncio.get_running_loop()
        result: asyncio.Future[Reply] = loop.create_future()

        def match(received: Received) -> None:
            message = received.message
            if result.done():
                return
            if _refuses(message, request):
                result.set_exception(MemoryReadError(message))
            elif _answers(request, message):
                # _answers only accepts reply types.
                result.set_result(cast("Reply", message))

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
        framer = PacketFramer(on_discard=self._dispatch_noise)
        try:
            while True:
                try:
                    data = await reader.read(_READ_SIZE)
                except OSError as err:
                    _LOGGER.warning("Connection to %s lost: %s", self.host, err)
                    return
                if not data:
                    _LOGGER.warning("Gateway %s closed the connection", self.host)
                    return
                for item in framer.feed(data):
                    self._dispatch(item)
        finally:
            framer.reset()  # report a partial packet cut off by the drop

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

    def _dispatch(self, raw: Packet | Dynet2Frame) -> None:
        received = Received(raw=raw, message=decode(raw))
        _LOGGER.debug("Received %s: %s", raw.hex(), received.message)
        for callback in tuple(self._packet_callbacks):
            try:
                callback(received)
            except Exception:
                _LOGGER.exception("Error in packet callback %r", callback)

    def _dispatch_noise(self, data: bytes) -> None:
        for callback in tuple(self._noise_callbacks):
            try:
                callback(data)
            except Exception:
                _LOGGER.exception("Error in noise callback %r", callback)

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


type Query = (
    RequestPreset
    | RequestChannelLevel
    | RequestSignon
    | RequestDeviceStatus
    | Dynet2RequestSerialNumber
    | Dynet2DeviceRequest
    | Dynet2ReadMemory
    | Dynet2StatusRequest
    | Dynet2RequestChannelLevel
)
"""Every request `DynetClient.query` can wait for an answer to."""

type Reply = (
    PresetReport
    | ChannelLevelReport
    | Dynet2SignonReply
    | DeviceStatusReport
    | Dynet2SerialNumberReply
    | Dynet2BuildNumberReply
    | Dynet2PcbVersionReply
    | Dynet2MemoryData
    | PhysicalStateReport
    | Dynet2ChannelLevelReport
)
"""Every reply `DynetClient.query` returns."""

_ANSWERED_COMMANDS: Final = frozenset(
    {
        Dynet2Command.SIGNON,
        Dynet2Command.BOOTLOADER_SIGNON,
        Dynet2Command.FIRMWARE_BUILD,
        Dynet2Command.BOOTLOADER_BUILD,
        Dynet2Command.PCB_VERSION,
    }
)
_ALL_PHYSICAL_DEVICES: Final = (0xAA, 0x55)


def _refuses(message: AnyMessage, request: Query) -> TypeGuard[Dynet2MemoryErrorReply]:
    """Whether `message` is a device refusing `request`, a memory read."""
    return (
        isinstance(request, Dynet2ReadMemory)
        and isinstance(message, Dynet2MemoryErrorReply)
        and _same_device(request, message)
        and (message.address, message.selector_raw)
        == (request.address, request.selector_raw)
    )


def _check_query(request: Query) -> None:
    """Raise `ValueError` for a request `query` can't wait for an answer to."""
    match request:
        case RequestSignon() | RequestDeviceStatus() if (
            request.device_code,
            request.box,
        ) == _ALL_PHYSICAL_DEVICES:
            msg = "query() needs one device; subscribe and send() to ask them all"
            raise ValueError(msg)
        case (
            Dynet2RequestSerialNumber()
            | Dynet2DeviceRequest()
            | Dynet2ReadMemory()
            | Dynet2StatusRequest()
        ) if (request.device_code, request.box) == DYNET2_ALL_DEVICES:
            msg = "query() needs one device; subscribe and send() to ask them all"
            raise ValueError(msg)
        case Dynet2DeviceRequest() if request.command not in _ANSWERED_COMMANDS:
            msg = f"Command 0x{request.command:02x} has no reply; use send()"
            raise ValueError(msg)
        case Dynet2StatusRequest() if request.query != StatusQuery.PE_CELL_LIGHT_LEVEL:
            msg = (
                f"query() can't wait for status query 0x{request.query:04x}; use send()"
            )
            raise ValueError(msg)
        case Dynet2StatusRequest() if request.box > 0xFF:
            msg = "The light level reply can only come from a box of 0-255"
            raise ValueError(msg)


def _same_device(request: Query, message: AnyMessage) -> bool:
    return (getattr(message, "device_code", None), getattr(message, "box", None)) == (
        getattr(request, "device_code", None),
        getattr(request, "box", None),
    )


def _answers(request: Query, message: AnyMessage) -> bool:  # noqa: PLR0911
    """Whether `message` is the reply `query` waits for after `request`."""
    match request, message:
        case RequestPreset(), PresetReport():
            return message.area == request.area
        case RequestChannelLevel(), ChannelLevelReport():
            return (message.area, message.channel) == (request.area, request.channel)
        case RequestSignon(), Dynet2SignonReply():
            return _same_device(request, message) and not message.bootloader
        case RequestDeviceStatus(), DeviceStatusReport():
            return (
                _same_device(request, message)
                and message.sub_opcode == request.status_type
            )
        case Dynet2RequestSerialNumber(), Dynet2SerialNumberReply():
            return _same_device(request, message)
        case Dynet2DeviceRequest(), (
            Dynet2SignonReply() | Dynet2BuildNumberReply() | Dynet2PcbVersionReply()
        ):
            return _same_device(request, message) and _answers_command(
                request.command, message
            )
        case Dynet2ReadMemory(), Dynet2MemoryData():
            return _same_device(request, message) and (
                message.address,
                message.selector_raw,
            ) == (request.address, request.selector_raw)
        case Dynet2RequestChannelLevel(), Dynet2ChannelLevelReport():
            return (message.area, message.channel) == (request.area, request.channel)
        case Dynet2StatusRequest(), PhysicalStateReport():
            return (
                _same_device(request, message)
                and message.state_type == PhysicalStateType.LIGHT_LEVEL
            )
    return False


def _answers_command(
    command: int,
    message: Dynet2SignonReply | Dynet2BuildNumberReply | Dynet2PcbVersionReply,
) -> bool:
    if isinstance(message, Dynet2PcbVersionReply):
        return command == Dynet2Command.PCB_VERSION
    if isinstance(message, Dynet2SignonReply):
        bootloader, application = Dynet2Command.BOOTLOADER_SIGNON, Dynet2Command.SIGNON
    else:
        bootloader = Dynet2Command.BOOTLOADER_BUILD
        application = Dynet2Command.FIRMWARE_BUILD
    return command == (bootloader if message.bootloader else application)
