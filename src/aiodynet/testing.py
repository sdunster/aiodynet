"""A fake DyNet gateway for tests: a real TCP server on localhost.

Use it to test code built on aiodynet without hardware::

    async with FakeGateway() as gateway:
        async with DynetClient(gateway.host, gateway.port) as client:
            await client.send(PresetSelect(area=1, preset=1))
            packet = await gateway.next_packet()
            assert packet == PresetSelect(area=1, preset=1).to_packet()
            await gateway.send(PresetReport(area=1, preset=1))

It can also answer requests the way a real site would: give it a `replies`
function that maps each received packet to what to send back: messages,
packets, frames, or raw bytes (for example noise).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Self

from .dynet2 import Dynet2Frame
from .framing import PacketFramer
from .messages import Dynet2Message
from .packet import Packet

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from types import TracebackType

    from .messages import Message

type Replies = Callable[
    [Packet], Iterable[Message | Dynet2Message | Packet | Dynet2Frame | bytes]
]


class FakeGateway:
    """An in-process gateway that records what clients send.

    Every client that connects gets every packet passed to `send`. Packets from
    clients are framed exactly as a gateway would see them and put in
    `received` (and the queue behind `next_packet`).
    """

    def __init__(self, *, port: int = 0, replies: Replies | None = None) -> None:
        """Create the gateway. It listens once started.

        By default it picks a free port; pass `port` to reuse one, for example
        to bring a gateway "back" for a client that is reconnecting.
        """
        self.host = "127.0.0.1"
        self.port = port
        self.received: list[Packet] = []
        """Every packet received from any client, in order."""
        self.raw_received = bytearray()
        """Every byte received, unframed."""
        self._replies = replies
        self._queue: asyncio.Queue[Packet] = asyncio.Queue()
        self._writers: list[asyncio.StreamWriter] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self._server: asyncio.Server | None = None
        self._stopping = False
        self._connected = asyncio.Event()
        self.connections = 0
        """How many clients have connected so far."""

    async def start(self) -> None:
        """Start listening on a free port (see `port`)."""
        self._stopping = False
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Disconnect every client and stop listening."""
        self._stopping = True
        if self._server is not None:
            self._server.close()
        await self.disconnect_clients()
        if self._server is not None:
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> Self:
        """Start; see `start`."""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Stop; see `stop`."""
        await self.stop()

    @property
    def client_count(self) -> int:
        """How many clients are connected right now."""
        return len(self._writers)

    async def wait_for_client(self, timeout: float = 5.0) -> None:
        """Wait until at least one client is connected."""
        async with asyncio.timeout(timeout):
            await self._connected.wait()

    async def send(
        self, *items: Message | Dynet2Message | Packet | Dynet2Frame | bytes
    ) -> None:
        """Send messages, packets, DyNet 2 frames or raw bytes to every client."""
        data = b"".join(_to_bytes(item) for item in items)
        for writer in tuple(self._writers):
            writer.write(data)
            with contextlib.suppress(OSError):
                await writer.drain()

    async def next_packet(self, timeout: float = 5.0) -> Packet:
        """Wait for the next packet from a client and return it."""
        async with asyncio.timeout(timeout):
            return await self._queue.get()

    async def disconnect_clients(self) -> None:
        """Drop every client connection, as a gateway reboot would."""
        writers, self._writers = self._writers, []
        self._connected.clear()
        for writer in writers:
            writer.close()
        for task in tuple(self._tasks):
            task.cancel()
        for task in tuple(self._tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Register the writer now, not in the task: a task cancelled before it
        # starts never runs its cleanup, and the socket would stay open.
        if self._stopping:
            # asyncio can finish accepting a connection after stop() began.
            writer.transport.abort()
            return
        self.connections += 1
        self._writers.append(writer)
        self._connected.set()
        task = asyncio.create_task(self._serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        framer = PacketFramer()
        try:
            while data := await reader.read(1024):
                self.raw_received += data
                for packet in framer.feed(data):
                    if not isinstance(packet, Packet):
                        continue  # a gateway takes only DyNet 1 from clients
                    self.received.append(packet)
                    self._queue.put_nowait(packet)
                    if self._replies is not None:
                        await self.send(*self._replies(packet))
        except OSError:  # pragma: no cover - depends on OS timing
            pass
        finally:
            with contextlib.suppress(ValueError):  # already disconnected
                self._writers.remove(writer)
            if not self._writers:
                self._connected.clear()
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()


def _to_bytes(item: Message | Dynet2Message | Packet | Dynet2Frame | bytes) -> bytes:
    if isinstance(item, bytes):
        return item
    if isinstance(item, Dynet2Message):
        return bytes(item.to_frame())
    if isinstance(item, (Packet, Dynet2Frame)):
        return bytes(item)
    return bytes(item.to_packet())
