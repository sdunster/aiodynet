"""Exceptions raised by aiodynet."""


class DynetError(Exception):
    """Base class for every aiodynet error."""


class InvalidPacketError(DynetError, ValueError):
    """Bytes that are not a valid DyNet 1 packet (wrong length or checksum)."""


class DynetConnectionError(DynetError, ConnectionError):
    """The gateway could not be reached, or the connection was lost."""


class NotConnectedError(DynetConnectionError):
    """A packet could not be sent because the client is not connected.

    Raised by `DynetClient.send` while the client is reconnecting or after it
    was closed, and for packets still queued when the connection drops. Nothing
    is retried, so a caller never sends a stale command by surprise.
    """
