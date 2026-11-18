"""Split a byte stream from a gateway into packets."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .packet import PACKET_LENGTH, Packet, SyncType, checksum

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOGGER = logging.getLogger(__name__)

_SYNC_BYTES = frozenset(SyncType)


class PacketFramer:
    """Turn the bytes read from a TCP stream into packets.

    TCP doesn't keep packet boundaries, so a read can end mid-packet or hold
    several packets. Feed every read to `feed` and it yields each complete
    packet. If the stream is out of step (after line noise, or when a
    connection starts mid-packet), it skips bytes until eight of them start
    with a known sync byte and carry a valid checksum.
    """

    def __init__(self) -> None:
        """Start with an empty buffer."""
        self._buffer = bytearray()
        self.discarded = 0
        """How many bytes were skipped to get back in step, in total."""

    def feed(self, data: bytes) -> Iterator[Packet]:
        """Add bytes from the stream and yield each complete packet."""
        self._buffer += data
        buffer = self._buffer
        skipped = 0
        while len(buffer) - skipped >= PACKET_LENGTH:
            start = skipped
            if (
                buffer[start] in _SYNC_BYTES
                and checksum(bytes(buffer[start : start + PACKET_LENGTH - 1]))
                == buffer[start + PACKET_LENGTH - 1]
            ):
                if skipped:
                    self._log_skipped(buffer[:skipped])
                    del buffer[:skipped]
                    skipped = 0
                packet = Packet(bytes(buffer[: PACKET_LENGTH - 1]))
                del buffer[:PACKET_LENGTH]
                yield packet
            else:
                skipped += 1
        if skipped:
            self._log_skipped(buffer[:skipped])
            del buffer[:skipped]

    def reset(self) -> None:
        """Drop any partial packet, for example after reconnecting."""
        self._buffer.clear()

    @property
    def pending(self) -> int:
        """How many bytes are waiting for the rest of their packet."""
        return len(self._buffer)

    def _log_skipped(self, data: bytearray) -> None:
        self.discarded += len(data)
        _LOGGER.debug(
            "Skipped %d byte(s) to find a packet: %s", len(data), data.hex(" ")
        )
