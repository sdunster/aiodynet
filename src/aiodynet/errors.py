"""Exceptions raised by aiodynet."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .messages import Dynet2MemoryErrorReply, Dynet2WriteMemoryAck


class DynetError(Exception):
    """Base class for every aiodynet error."""


class InvalidPacketError(DynetError, ValueError):
    """Bytes that are not a valid packet or DyNet 2 frame (length or checksum)."""


class DynetConnectionError(DynetError, ConnectionError):
    """The gateway could not be reached, or the connection was lost."""


class MemoryReadError(DynetError):
    """A device refused a memory read, for example past the end of its memory.

    Raised by `DynetClient.query` for a `Dynet2ReadMemory` that the device
    answered with a `Dynet2MemoryErrorReply`, which is in `reply`.
    """

    def __init__(self, reply: "Dynet2MemoryErrorReply") -> None:
        """Keep the device's reply."""
        error = reply.error
        detail = error.name.lower().replace("_", " ") if error else "unknown error"
        super().__init__(
            f"device 0x{reply.device_code:02x} box {reply.box} refused a read at "
            f"0x{reply.address:04x}: error {reply.error_code} ({detail})"
        )
        self.reply = reply


class MemoryWriteError(DynetError):
    """A device acknowledged a memory write with a result other than 00 00.

    Raised by `DynetClient.query` for a `Dynet2WriteMemory`. Every write seen
    so far succeeded and was acknowledged with 00 00, so what other results
    mean is unknown; treat the write as failed. The acknowledgement is in
    `reply`.
    """

    def __init__(self, reply: "Dynet2WriteMemoryAck") -> None:
        """Keep the device's acknowledgement."""
        super().__init__(
            f"device 0x{reply.device_code:02x} box {reply.box} answered a write at "
            f"0x{reply.address:04x} with result {reply.result_raw.hex(' ')}"
        )
        self.reply = reply


class NotConnectedError(DynetConnectionError):
    """A packet could not be sent because the client is not connected.

    Raised by `DynetClient.send` while the client is reconnecting or after it
    was closed, and for packets still queued when the connection drops. Nothing
    is retried, so a caller never sends a stale command by surprise.
    """
