"""Message encoding and decoding.

The ``LEGACY_*`` vectors were recorded by driving dynalite-devices 0.1.47 (the
library Home Assistant uses today) through its public API against a fake
gateway, without reading its source. They pin the exact bytes existing
installs send and the meaning they take from inbound packets, so an
integration built on aiodynet can prove it stays compatible.
"""

from dataclasses import FrozenInstanceError
from typing import assert_never

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aiodynet import (
    MAX_CHANNEL_FADE,
    MAX_PRESET_FADE,
    AnyDynet1Message,
    AnyMessage,
    AreaPresetMessage,
    ChannelLevelReport,
    ClockDate,
    ClockTime,
    Dynet2Frame,
    Dynet2SignonReply,
    FadeChannelToPreset,
    Level,
    LinearPresetSelect,
    LogicalMessage,
    Message,
    Packet,
    PhysicalMessage,
    PresetReport,
    PresetSelect,
    RequestChannelLevel,
    RequestPreset,
    RequestSignon,
    SetChannelLevel,
    UnknownDynet2Message,
    UnknownMessage,
    decode,
)

# What dynalite-devices 0.1.47 sends for each action.
LEGACY_OUTBOUND: list[tuple[str, Message]] = [
    ("1c0100630000ff81", RequestPreset(area=1)),
    ("1c0300630400ff7b", RequestPreset(area=3, channel=5)),
    ("1c030b610000ff76", RequestChannelLevel(area=3, channel=12)),
    ("1cc800610000ffbc", RequestChannelLevel(area=200, channel=1)),
    ("1c010180ff96ffce", SetChannelLevel(area=1, channel=1, level=Level(1), fade=3)),
    ("1c017f80ff96ff50", SetChannelLevel(area=1, channel=1, level=Level(127), fade=3)),
    ("1c01fe80ff96ffd1", SetChannelLevel(area=1, channel=1, level=Level(254), fade=3)),
    ("1c01ff81ff00ff65", SetChannelLevel(area=1, channel=2, level=Level.OFF)),
    (
        "1c0301830119ff44",
        SetChannelLevel(area=3, channel=12, level=Level.FULL, fade=0.5),
    ),
    (
        "1c060180ffffff60",
        SetChannelLevel(area=6, channel=1, level=Level.FULL, fade=5.1),
    ),
    ("1c0200000000ffe3", PresetSelect(area=2, preset=1)),
    ("1c0200030000ffe0", PresetSelect(area=2, preset=4)),
    ("1c03c8010000ff19", PresetSelect(area=3, preset=2, fade=4)),
    ("1c0319000001ffc8", PresetSelect(area=3, preset=9, fade=0.5)),
    ("1c0319000002ffc7", PresetSelect(area=3, preset=17, fade=0.5)),
    ("1c031901001fffa9", PresetSelect(area=3, preset=250, fade=0.5)),
    ("1c065001c300ffcb", PresetSelect(area=6, preset=2, fade=1000)),
    ("1c062c020100ffb0", PresetSelect(area=6, preset=3, fade=6)),
]

# Inbound packets dynalite-devices 0.1.47 acts on, and what they mean.
LEGACY_INBOUND: list[tuple[str, AnyMessage]] = [
    ("1c05000a0000ff", PresetSelect(area=5, preset=5)),
    ("1c050001000000", PresetSelect(area=5, preset=2, join=0)),
    ("1c050000000100", PresetSelect(area=5, preset=9, join=0)),
    ("1c050165000000", LinearPresetSelect(area=5, preset=2, join=0)),
    ("1c050862000000", PresetReport(area=5, preset=9, join=0)),
    (
        "1c050060407fff",
        ChannelLevelReport(
            area=5, channel=1, target_level=Level(0x40), actual_level=Level(0x7F)
        ),
    ),
    (
        "1c058081006400",
        SetChannelLevel(area=5, channel=6, level=Level(0x80), fade=2, join=0),
    ),
    (
        "1c05ff8100ffff",
        SetChannelLevel(area=5, channel=6, level=Level.OFF, fade=5.1),
    ),
    ("1c05006b0300ff", FadeChannelToPreset(area=5, channel=1, preset=4)),
]


@pytest.mark.parametrize(("expected", "message"), LEGACY_OUTBOUND)
def test_legacy_outbound_bytes(expected: str, message: Message) -> None:
    assert bytes(message.to_packet()).hex() == expected


@pytest.mark.parametrize(("raw", "expected"), LEGACY_INBOUND)
def test_legacy_inbound_meaning(raw: str, expected: AnyMessage) -> None:
    packet = Packet.from_hex(raw)
    assert decode(packet) == expected
    assert expected.to_packet() == packet


@pytest.mark.parametrize(
    "message",
    [
        PresetSelect(area=1, preset=1),
        LinearPresetSelect(area=1, preset=1),
        PresetReport(area=1, preset=1),
    ],
)
def test_area_preset_messages_share_a_base(message: Message) -> None:
    assert isinstance(message, AreaPresetMessage)
    assert message.preset == 1


@pytest.mark.parametrize(
    ("preset", "opcode", "bank"),
    [
        (1, 0x00, 0),
        (4, 0x03, 0),
        (5, 0x0A, 0),
        (8, 0x0D, 0),
        (9, 0x00, 1),
        (2048, 0x0D, 255),
    ],
)
def test_preset_banks(preset: int, opcode: int, bank: int) -> None:
    packet = PresetSelect(area=1, preset=preset).to_packet()
    assert (packet[3], packet[5]) == (opcode, bank)


@pytest.mark.parametrize(
    ("channel", "opcode", "bank"),
    [
        (1, 0x80, 0xFF),
        (4, 0x83, 0xFF),
        (5, 0x80, 0x00),
        (9, 0x80, 0x01),
        (1024, 0x83, 0xFE),
    ],
)
def test_channel_banks(channel: int, opcode: int, bank: int) -> None:
    packet = SetChannelLevel(area=1, channel=channel, level=Level.FULL).to_packet()
    assert (packet[3], packet[4]) == (opcode, bank)


def test_linear_preset_select_encoding() -> None:
    packet = LinearPresetSelect(area=7, preset=256, fade=1310.7).to_packet()
    assert packet.hex("") == "1c07ff65ffffff" + f"{packet.checksum:02x}"


def test_fade_channel_to_preset_all_channels() -> None:
    message = FadeChannelToPreset(area=1, channel=None, preset=2, fade_raw=10)
    assert message.to_packet().hex("")[:14] == "1c01ff6b010aff"
    assert decode(message.to_packet()) == message


def test_request_preset_round_trip() -> None:
    # Also covered by the hypothesis round trip, but only when it happens to
    # draw a RequestPreset; this keeps coverage from depending on chance.
    message = RequestPreset(area=3, channel=5, join=0x7F)
    assert decode(message.to_packet()) == message


def test_preset_report_ignores_unused_bytes() -> None:
    assert decode(Packet.from_hex("1c0501620203ff")) == PresetReport(area=5, preset=2)


def test_fade_limits() -> None:
    assert MAX_CHANNEL_FADE == pytest.approx(5.1)
    assert MAX_PRESET_FADE == pytest.approx(1310.7)
    with pytest.raises(ValueError, match="too long"):
        SetChannelLevel(area=1, channel=1, level=Level.FULL, fade=10)
    with pytest.raises(ValueError, match="too long"):
        PresetSelect(area=1, preset=1, fade=2000)
    with pytest.raises(ValueError, match="too long"):
        LinearPresetSelect(area=1, preset=1, fade=2000)


@pytest.mark.parametrize(
    ("build", "match"),
    [
        (lambda: PresetSelect(area=256, preset=1), "area must be 0-255"),
        (lambda: PresetSelect(area=1, preset=0), "preset must be 1-2048"),
        (lambda: PresetSelect(area=1, preset=2049), "preset must be 1-2048"),
        (lambda: PresetSelect(area=1, preset=1, join=-1), "join must be 0-255"),
        (lambda: PresetSelect(area=1, preset=1, fade=-1), "non-negative"),
        (lambda: LinearPresetSelect(area=1, preset=257), "preset must be 1-256"),
        (lambda: PresetReport(area=1, preset=0), "preset must be 1-256"),
        (lambda: RequestPreset(area=1, channel=0), "channel must be 1-256"),
        (lambda: RequestChannelLevel(area=1, channel=257), "channel must be 1-256"),
        (
            lambda: ChannelLevelReport(
                area=1, channel=0, target_level=Level.OFF, actual_level=Level.OFF
            ),
            "channel must be 1-256",
        ),
        (
            lambda: SetChannelLevel(area=1, channel=1025, level=Level.FULL),
            "channel must be 1-1024",
        ),
        (
            lambda: FadeChannelToPreset(area=1, channel=256, preset=1),
            "channel must be 1-255",
        ),
        (
            lambda: FadeChannelToPreset(area=1, channel=1, preset=1, fade_raw=256),
            "fade_raw must be 0-255",
        ),
    ],
)
def test_validation(build: object, match: str) -> None:
    assert callable(build)
    with pytest.raises(ValueError, match=match):
        build()


@pytest.mark.parametrize(
    ("build", "match"),
    [
        (
            lambda: PresetSelect(area="1", preset=1),  # type: ignore[arg-type]
            "area must be an int, not str",
        ),
        (
            lambda: PresetSelect(area=True, preset=1),
            "area must be an int, not bool",
        ),
        (
            lambda: PresetSelect(area=1, preset=1.0),  # type: ignore[arg-type]
            "preset must be an int",
        ),
        (
            lambda: SetChannelLevel(area=1, channel=1, level=255),  # type: ignore[arg-type]
            "level must be a Level, not int",
        ),
        (
            lambda: ChannelLevelReport(
                area=1,
                channel=1,
                target_level=Level.OFF,
                actual_level=0.5,  # type: ignore[arg-type]
            ),
            "actual_level must be a Level",
        ),
    ],
)
def test_type_validation(build: object, match: str) -> None:
    assert callable(build)
    with pytest.raises(TypeError, match=match):
        build()


def test_fields_are_keyword_only() -> None:
    with pytest.raises(TypeError):
        PresetSelect(1, 1)  # type: ignore[call-arg]


def test_messages_are_immutable() -> None:
    message = PresetSelect(area=1, preset=1)
    with pytest.raises(FrozenInstanceError):
        message.area = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "raw",
    [
        "5c0102030405ff",  # physical
        "1c050020000000",  # set area links: bit order undocumented
        "1c050064000000",  # preset offset: meaning unclear
        "6c000000000000",  # unknown sync byte
    ],
)
def test_unknown_messages_keep_the_packet(raw: str) -> None:
    packet = Packet.from_hex(raw)
    message = decode(packet)
    assert message == UnknownMessage(packet=packet)
    assert message.to_packet() == packet


def describe(message: AnyMessage) -> str:  # noqa: C901, PLR0911
    """Exhaustive match; mypy fails here if a message type is added to AnyMessage."""
    match message:
        case PresetSelect() | LinearPresetSelect() | PresetReport():
            return f"preset {message.preset}"
        case RequestPreset():
            return "request preset"
        case ChannelLevelReport() | SetChannelLevel():
            return "level"
        case RequestChannelLevel():
            return "request level"
        case FadeChannelToPreset():
            return "channel preset"
        case ClockTime() | ClockDate():
            return "clock"
        case RequestSignon():
            return "sign-on request"
        case LogicalMessage():
            return "other logical"
        case Dynet2SignonReply():
            return "sign-on reply"
        case UnknownMessage() | UnknownDynet2Message():
            return "unknown"
        case _:
            assert_never(message)


def test_exhaustive_match() -> None:
    assert describe(decode(Packet.from_hex("1c050001000000"))) == "preset 2"
    assert describe(decode(Packet.from_hex("5c0102030405ff"))) == "unknown"
    assert describe(decode(Packet.from_hex("5caa5550175242"))) == "clock"
    assert describe(decode(Packet.from_hex("1c050076000000"))) == "other logical"
    assert describe(decode(Packet.from_hex("5cb32a4000000a"))) == "sign-on request"
    signon = Dynet2Frame.from_hex(
        "ac 04 81 b3 00 2a aa 55 55 01 00 00 02 11 1f 1f 00 00 3f b4"
    )
    assert describe(decode(signon)) == "sign-on reply"
    assert describe(decode(Dynet2Frame.build(b""))) == "unknown"


levels = st.builds(Level, st.integers(0, 255))
areas = st.integers(0, 255)
joins = st.integers(0, 255)
preset_fades = st.integers(0, 0xFFFF).map(lambda s: round(s * 0.02, 6))

all_messages: st.SearchStrategy[AnyMessage] = st.one_of(
    st.builds(
        PresetSelect,
        area=areas,
        preset=st.integers(1, 2048),
        fade=preset_fades,
        join=joins,
    ),
    st.builds(
        LinearPresetSelect,
        area=areas,
        preset=st.integers(1, 256),
        fade=preset_fades,
        join=joins,
    ),
    st.builds(PresetReport, area=areas, preset=st.integers(1, 256), join=joins),
    st.builds(RequestPreset, area=areas, channel=st.integers(1, 256), join=joins),
    st.builds(
        ChannelLevelReport,
        area=areas,
        channel=st.integers(1, 256),
        target_level=levels,
        actual_level=levels,
        join=joins,
    ),
    st.builds(RequestChannelLevel, area=areas, channel=st.integers(1, 256), join=joins),
    st.builds(
        SetChannelLevel,
        area=areas,
        channel=st.integers(1, 1024),
        level=levels,
        fade=st.integers(0, 255).map(lambda s: round(s * 0.02, 6)),
        join=joins,
    ),
    st.builds(
        FadeChannelToPreset,
        area=areas,
        channel=st.none() | st.integers(1, 255),
        preset=st.integers(1, 256),
        fade_raw=st.integers(0, 255),
        join=joins,
    ),
)


@given(all_messages)
def test_message_round_trip(message: AnyDynet1Message) -> None:
    assert decode(message.to_packet()) == message


@given(st.binary(min_size=7, max_size=7))
def test_decode_never_raises(data: bytes) -> None:
    packet = Packet(data)
    message = decode(packet)
    if isinstance(message, LogicalMessage):
        assert message.area == packet[1]
        assert message.join == packet[6]
    elif isinstance(message, PhysicalMessage):
        assert (message.device_code, message.box) == (packet[1], packet[2])
