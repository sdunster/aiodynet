"""Every message type seen at a real site decodes, and encodes back to its bytes.

The examples are in ``data/captured.txt``: up to three distinct packets or
DyNet 2 frames of each type, from a week of captures at one site.
"""

from pathlib import Path

import pytest

from aiodynet import Dynet2Frame, Dynet2Message, Packet, decode

CAPTURED = Path(__file__).parent / "data" / "captured.txt"


def _examples() -> list[tuple[str, str]]:
    examples = []
    for line in CAPTURED.read_text().splitlines():
        if line and not line.startswith("#"):
            raw, expected = line.rsplit("  ", 1)
            examples.append((raw, expected))
    return examples


EXAMPLES = _examples()


def test_the_fixture_covers_many_types() -> None:
    assert len({expected for _, expected in EXAMPLES}) >= 40


@pytest.mark.parametrize(("raw", "expected"), EXAMPLES)
def test_captured_message(raw: str, expected: str) -> None:
    data = bytes.fromhex(raw)
    packet = (
        Dynet2Frame.from_bytes(data) if data[0] == 0xAC else Packet.from_bytes(data)
    )
    message = decode(packet)
    assert type(message).__name__ == expected
    encoded = (
        message.to_frame()
        if isinstance(message, Dynet2Message)
        else message.to_packet()
    )
    assert bytes(encoded) == data
