"""Split a byte stream from a gateway into packets."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from .dynet2 import (
    DYNET2_MAX_LENGTH,
    DYNET2_MIN_LENGTH,
    DYNET2_SYNC,
    Dynet2Frame,
    dynet2_checksum,
    dynet2_length,
)
from .packet import PACKET_LENGTH, Packet, SyncType, checksum

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_LOGGER = logging.getLogger(__name__)

_SYNC_BYTES = frozenset(SyncType)
_WAIT: Final = -1
_NO_MATCH: Final = 0

DEFAULT_MAX_DYNET2_LENGTH: Final = 64
"""The longest DyNet 2 frame `PacketFramer` waits for unless told otherwise.

Every frame seen from real gateways so far has been 20 bytes or less.
"""


class PacketFramer:
    """Turn the bytes read from a TCP stream into packets.

    TCP doesn't keep packet boundaries, so a read can end mid-packet or hold
    several packets. Feed every read to `feed` and it yields each complete
    DyNet 1 packet. If the stream is out of step (after line noise, or when a
    connection starts mid-packet), it skips bytes until it finds a known sync
    byte that starts a packet with a valid checksum.

    Gateways can also send DyNet 2 frames (sync byte 0xAC) on the same stream.
    The framer steps over each one whole, so the bytes inside it can't be
    mistaken for DyNet 1 packets and aren't counted as noise. `feed` leaves
    them out; use `feed_all` to get them as `Dynet2Frame` objects too.

    A 0xAC byte is taken as a frame start only if the checksum at the end of
    the length its next byte gives holds. Until that many bytes have arrived
    the framer waits, holding back whatever follows. So that noise starting
    with 0xAC can't hold back real packets for long, a frame longer than
    `max_dynet2_length` bytes is never waited for: its 0xAC is skipped as
    noise straight away. That bounds the delay to `max_dynet2_length` bytes.
    Raise the limit (up to 1024) if a site sends longer frames.
    """

    def __init__(
        self,
        *,
        max_dynet2_length: int = DEFAULT_MAX_DYNET2_LENGTH,
        on_discard: Callable[[bytes], None] | None = None,
    ) -> None:
        """Start with an empty buffer.

        `on_discard`, if given, is called with each run of bytes skipped to get
        back in step, for tools that want to keep the noise as evidence.
        """
        if not DYNET2_MIN_LENGTH <= max_dynet2_length <= DYNET2_MAX_LENGTH:
            msg = (
                f"max_dynet2_length must be {DYNET2_MIN_LENGTH}-{DYNET2_MAX_LENGTH}, "
                f"got {max_dynet2_length}"
            )
            raise ValueError(msg)
        self._max_dynet2_length = max_dynet2_length
        self._on_discard = on_discard
        self._buffer = bytearray()
        self.discarded = 0
        """How many bytes were skipped to get back in step, in total."""
        self.dynet2_frames = 0
        """How many DyNet 2 frames were found, in total (from either method)."""

    def feed(self, data: bytes) -> Iterator[Packet]:
        """Add bytes from the stream and yield each complete DyNet 1 packet.

        DyNet 2 frames are stepped over without being yielded.
        """
        for item in self.feed_all(data):
            if isinstance(item, Packet):
                yield item

    def feed_all(self, data: bytes) -> Iterator[Packet | Dynet2Frame]:
        """Add bytes from the stream and yield each packet and DyNet 2 frame."""
        self._buffer += data
        buffer = self._buffer
        start = 0  # bytes before `start` have been ruled out as noise
        while (end := self._match(start)) != _WAIT:
            if end == _NO_MATCH:
                start += 1
                continue
            item: Packet | Dynet2Frame
            if buffer[start] == DYNET2_SYNC:
                item = Dynet2Frame(bytes(buffer[start:end]))
                self.dynet2_frames += 1
            else:
                item = Packet(bytes(buffer[start : end - 1]))
            if start:
                self._log_skipped(buffer[:start])
            del buffer[:end]
            start = 0
            yield item
        if start:
            self._log_skipped(buffer[:start])
            del buffer[:start]

    def _match(self, start: int) -> int:
        """Check for a packet or frame at `start` in the buffer.

        Returns where it ends, `_NO_MATCH` if none starts there, or `_WAIT` if
        more bytes are needed to tell.
        """
        buffer = self._buffer
        available = len(buffer) - start
        if available <= 0:
            return _WAIT
        sync = buffer[start]
        if sync == DYNET2_SYNC:
            if available < 2:
                return _WAIT
            length = dynet2_length(buffer[start + 1])
            if length > self._max_dynet2_length:
                _LOGGER.debug(
                    "Not waiting for a %d-byte DyNet 2 frame (limit %d)",
                    length,
                    self._max_dynet2_length,
                )
                return _NO_MATCH
        elif sync in _SYNC_BYTES:
            length = PACKET_LENGTH
        else:
            return _NO_MATCH
        if available < length:
            return _WAIT
        end = start + length
        if sync == DYNET2_SYNC:
            valid = buffer[end - 2 : end] == dynet2_checksum(buffer[start : end - 2])
        else:
            valid = checksum(bytes(buffer[start : end - 1])) == buffer[end - 1]
        return end if valid else _NO_MATCH

    def reset(self) -> None:
        """Drop any partial packet or frame, for example after reconnecting."""
        self._buffer.clear()

    @property
    def pending(self) -> int:
        """How many bytes are waiting for the rest of their packet or frame."""
        return len(self._buffer)

    def _log_skipped(self, data: bytearray) -> None:
        self.discarded += len(data)
        _LOGGER.debug(
            "Skipped %d byte(s) to find a packet: %s", len(data), data.hex(" ")
        )
        if self._on_discard is not None:
            self._on_discard(bytes(data))
