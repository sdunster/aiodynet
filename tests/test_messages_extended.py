"""Messages beyond the ones the Home Assistant integration uses today.

Their byte layouts come from the DyNet 1 opcode list. Fields whose unit the
list doesn't pin down are raw, so these tests check byte placement, not units.
"""

import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aiodynet import (
    AnyMessage,
    AreaCascadingSync,
    AreaCommand,
    AreaCommandType,
    AreaOff,
    AreaToLevel,
    ChannelOff,
    ChannelOn,
    ClockDate,
    ClockTime,
    DeviceIdentify,
    DeviceStatusReport,
    FadeChannelToLevel,
    FadeScale,
    HoldTimerAllPresets,
    Level,
    Message,
    MotionActivitySync,
    OccupancyDetectionAllPresets,
    Packet,
    Panic,
    PresetLevelReport,
    PresetSelect,
    ProgramLevelsToPreset,
    RecallPreviousPreset,
    RequestFirmwareVersion,
    RequestPresetLevel,
    RequestUserPreference,
    RestoreSavedPreset,
    ResumeOccupancyDetection,
    SetUserPreference,
    StopFade,
    SuspendOccupancyDetection,
    UnknownMessage,
    Unpanic,
    UserPreferenceReport,
    decode,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Packets seen on a real site, and what they mean.
SITE_VECTORS: list[tuple[str, AnyMessage]] = [
    ("1c1cff2e0027ff", MotionActivitySync(area=28, channel=None, time_raw=39)),
    (
        "1c170c4a1b3bff",
        UserPreferenceReport(area=23, preference=0x0C, value=0x1B3B),
    ),
    (
        "5caa5550175242",
        ClockTime(device_code=0xAA, box=0x55, hour=17, minute=52, second=42),
    ),
    (
        "5caa5553187126",
        ClockDate(
            device_code=0xAA, box=0x55, year=2026, month=11, day=18, day_of_week=3
        ),
    ),
    ("1c14ff2f0000ff", AreaCascadingSync(area=20)),
    ("1c18ff2f0000ff", AreaCascadingSync(area=24)),
    ("1c1aff310001ff", OccupancyDetectionAllPresets(area=26, resume=True)),
    ("1c1aff310000ff", OccupancyDetectionAllPresets(area=26, resume=False)),
    (
        "5cb32a00021100",
        DeviceIdentify(
            device_code=0xB3, box=42, firmware_major=0x02, firmware_minor=0x11
        ),
    ),
    ("5caa554100000a", DeviceStatusReport(device_code=0xAA, box=0x55, sub_opcode=0x0A)),
]

ENCODINGS: list[tuple[Message, str]] = [
    (AreaCommand(area=5, command=AreaCommandType.DECREMENT_LEVEL), "1c0500050000ff"),
    (AreaCommand(area=5, command=AreaCommandType.INCREMENT_LEVEL), "1c0500060000ff"),
    (
        AreaCommand(area=5, command=AreaCommandType.PROGRAM_CURRENT_PRESET),
        "1c0500080000ff",
    ),
    (AreaCommand(area=5, command=AreaCommandType.DISABLE_PANELS), "1c0500150000ff"),
    (AreaCommand(area=5, command=AreaCommandType.ENABLE_PANELS), "1c0500160000ff"),
    # System Builder's form, with 0xFF ("all channels") in byte 2; panels act on
    # both. Sent at one site to the pantry panel's area.
    (
        AreaCommand(area=5, command=AreaCommandType.DISABLE_PANELS, channel_raw=0xFF),
        "1c05ff150000ff",
    ),
    (
        AreaCommand(area=5, command=AreaCommandType.ENABLE_PANELS, channel_raw=0xFF),
        "1c05ff160000ff",
    ),
    (
        AreaCommand(area=5, command=AreaCommandType.SAVE_CURRENT_PRESET),
        "1c0500660000ff",
    ),
    (AreaOff(area=5, fade=1000), "1c055004c300ff"),
    (RecallPreviousPreset(area=5, fade=1), "1c05320f0000ff"),
    (Panic(area=5), "1c0500170000ff"),
    (Unpanic(area=5, fade=0.5), "1c0519180000ff"),
    (AreaToLevel(area=5, level=Level.FULL, fade=1000), "1c05017950c3ff"),
    (ChannelOff(area=5, channel=3, fade=1), "1c0502743200ff"),
    (ChannelOn(area=5, channel=None), "1c05ff750000ff"),
    (StopFade(area=5, channel=1), "1c0500760000ff"),
    (StopFade(area=5, channel=None), "1c05ff760000ff"),
    (
        FadeChannelToLevel(
            area=5, channel=2, level=Level(0x80), scale=FadeScale.TENTHS, fade_raw=10
        ),
        "1c050171800aff",
    ),
    (
        FadeChannelToLevel(
            area=5, channel=2, level=Level.OFF, scale=FadeScale.SECONDS, fade_raw=3
        ),
        "1c050172ff03ff",
    ),
    (
        FadeChannelToLevel(
            area=5, channel=None, level=Level.FULL, scale=FadeScale.MINUTES
        ),
        "1c05ff730100ff",
    ),
    (RestoreSavedPreset(area=5, fade_raw=7), "1c0500670007ff"),
    (SetUserPreference(area=5, preference=2, value=0x1234), "1c0502481234ff"),
    (RequestUserPreference(area=5, preference=2), "1c0502490000ff"),
    (RequestPresetLevel(area=5, channel=3, preset=4), "1c0502910300ff"),
    (
        PresetLevelReport(area=5, channel=3, preset=4, level=Level(0x80)),
        "1c0502920380ff",
    ),
    (ClockTime(device_code=1, box=2, hour=9, minute=5, second=0), "5c010250090500"),
    (
        ClockDate(device_code=1, box=2, year=2001, month=2, day=3, day_of_week=0),
        "5c010253030201",
    ),
    (AreaCascadingSync(area=5, channel=3), "1c05022f0000ff"),
    (OccupancyDetectionAllPresets(area=5, channel=2, resume=True), "1c0501310001ff"),
    (HoldTimerAllPresets(area=5, state_raw=1), "1c05ff300001ff"),
    (SuspendOccupancyDetection(area=5), "1c05ff3a0000ff"),
    (ResumeOccupancyDetection(area=5, channel=1), "1c05003b0000ff"),
    (ProgramLevelsToPreset(area=5, preset=3), "1c0502090000ff"),
    (
        DeviceStatusReport(
            device_code=1, box=2, sub_opcode=0x12, data1_raw=0x16, data2_raw=0x80
        ),
        "5c010241168012",
    ),
    (RequestFirmwareVersion(device_code=0xB3, box=42), "5cb32a80000000"),
]


@pytest.mark.parametrize(("raw", "expected"), SITE_VECTORS)
def test_site_packets(raw: str, expected: AnyMessage) -> None:
    packet = Packet.from_hex(raw)
    assert decode(packet) == expected
    assert expected.to_packet() == packet


@pytest.mark.parametrize(("message", "raw"), ENCODINGS)
def test_encoding(message: Message, raw: str) -> None:
    assert message.to_packet() == Packet.from_hex(raw)
    assert decode(message.to_packet()) == message


def test_every_packet_in_the_site_capture_is_typed() -> None:
    lines = (FIXTURES / "site-capture.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    packets = [Packet.from_hex(r["hex"]) for r in records if "hex" in r]
    assert len(packets) >= 60
    untyped = [p.hex() for p in packets if isinstance(decode(p), UnknownMessage)]
    assert untyped == []


@pytest.mark.parametrize(
    "raw",
    [
        "5caa55501a0000",  # hour 0x1a isn't BCD
        "5caa5550240000",  # hour 24
        "5caa5553320100",  # day 32
        "5caa5553010d26",  # month 0x0d isn't BCD
        "5caa5553010026",  # month 0
    ],
)
def test_clock_packets_that_do_not_fit_stay_unknown(raw: str) -> None:
    packet = Packet.from_hex(raw)
    assert decode(packet) == UnknownMessage(packet=packet)


def test_other_physical_opcodes_stay_unknown() -> None:
    packet = Packet.from_hex("5c010202000000")  # 0x02 Reboot Device
    assert isinstance(decode(packet), UnknownMessage)


@pytest.mark.parametrize(
    ("build", "error", "match"),
    [
        (
            lambda: AreaCommand(area=1, command=5),  # type: ignore[arg-type]
            TypeError,
            "AreaCommandType",
        ),
        (
            lambda: AreaCommand(
                area=1, command=AreaCommandType.ENABLE_PANELS, channel_raw=256
            ),
            ValueError,
            "channel_raw must be 0-255",
        ),
        (lambda: AreaOff(area=1, fade=2000), ValueError, "too long"),
        (
            lambda: AreaToLevel(area=1, level=1),  # type: ignore[arg-type]
            TypeError,
            "must be a Level",
        ),
        (lambda: ChannelOff(area=1, channel=0), ValueError, "channel must be 1-255"),
        (lambda: StopFade(area=1, channel=256), ValueError, "channel must be 1-255"),
        (
            lambda: FadeChannelToLevel(
                area=1,
                channel=1,
                level=Level.OFF,
                scale=0x71,  # type: ignore[arg-type]
            ),
            TypeError,
            "FadeScale",
        ),
        (
            lambda: FadeChannelToLevel(
                area=1, channel=1, level=Level.OFF, scale=FadeScale.TENTHS, fade_raw=256
            ),
            ValueError,
            "fade_raw must be 0-255",
        ),
        (lambda: RestoreSavedPreset(area=1, fade_raw=-1), ValueError, "fade_raw"),
        (
            lambda: MotionActivitySync(area=1, channel=None, time_raw=0x10000),
            ValueError,
            "time_raw must be 0-65535",
        ),
        (
            lambda: SetUserPreference(area=1, preference=256, value=0),
            ValueError,
            "preference must be 0-255",
        ),
        (
            lambda: UserPreferenceReport(area=1, preference=1, value=-1),
            ValueError,
            "value must be 0-65535",
        ),
        (
            lambda: RequestUserPreference(area=1, preference=-1),
            ValueError,
            "preference",
        ),
        (
            lambda: RequestPresetLevel(area=1, channel=1, preset=0),
            ValueError,
            "preset must be 1-256",
        ),
        (
            lambda: PresetLevelReport(area=1, channel=0, preset=1, level=Level.OFF),
            ValueError,
            "channel must be 1-256",
        ),
        (
            lambda: ClockTime(device_code=256, box=0, hour=0, minute=0, second=0),
            ValueError,
            "device_code must be 0-255",
        ),
        (
            lambda: ClockTime(device_code=0, box=0, hour=0, minute=60, second=0),
            ValueError,
            "minute must be 0-59",
        ),
        (
            lambda: ClockDate(
                device_code=0, box=0, year=1999, month=1, day=1, day_of_week=0
            ),
            ValueError,
            "year must be 2000-2099",
        ),
        (
            lambda: ClockDate(
                device_code=0, box=0, year=2026, month=1, day=1, day_of_week=8
            ),
            ValueError,
            "day_of_week must be 0-7",
        ),
    ],
)
def test_validation(build: object, error: type[Exception], match: str) -> None:
    assert callable(build)
    with pytest.raises(error, match=match):
        build()


areas = st.integers(0, 255)
joins = st.integers(0, 255)
levels = st.builds(Level, st.integers(0, 255))
fades = st.integers(0, 0xFFFF).map(lambda s: round(s * 0.02, 6))
channel_or_all = st.none() | st.integers(1, 255)
byte = st.integers(0, 255)
word = st.integers(0, 0xFFFF)

extended_messages: st.SearchStrategy[Message] = st.one_of(
    st.builds(
        AreaCommand, area=areas, command=st.sampled_from(AreaCommandType), join=joins
    ),
    *(
        st.builds(cls, area=areas, fade=fades, join=joins)
        for cls in (AreaOff, RecallPreviousPreset, Panic, Unpanic)
    ),
    st.builds(AreaToLevel, area=areas, level=levels, fade=fades, join=joins),
    *(
        st.builds(cls, area=areas, channel=channel_or_all, fade=fades, join=joins)
        for cls in (ChannelOff, ChannelOn)
    ),
    st.builds(StopFade, area=areas, channel=channel_or_all, join=joins),
    st.builds(
        FadeChannelToLevel,
        area=areas,
        channel=channel_or_all,
        level=levels,
        scale=st.sampled_from(FadeScale),
        fade_raw=byte,
        join=joins,
    ),
    st.builds(RestoreSavedPreset, area=areas, fade_raw=byte, join=joins),
    st.builds(
        MotionActivitySync,
        area=areas,
        channel=channel_or_all,
        time_raw=word,
        join=joins,
    ),
    *(
        st.builds(cls, area=areas, preference=byte, value=word, join=joins)
        for cls in (SetUserPreference, UserPreferenceReport)
    ),
    st.builds(RequestUserPreference, area=areas, preference=byte, join=joins),
    st.builds(
        RequestPresetLevel,
        area=areas,
        channel=st.integers(1, 256),
        preset=st.integers(1, 256),
        join=joins,
    ),
    st.builds(
        PresetLevelReport,
        area=areas,
        channel=st.integers(1, 256),
        preset=st.integers(1, 256),
        level=levels,
        join=joins,
    ),
    st.builds(
        ClockTime,
        device_code=byte,
        box=byte,
        hour=st.integers(0, 23),
        minute=st.integers(0, 59),
        second=st.integers(0, 59),
    ),
    st.builds(
        ClockDate,
        device_code=byte,
        box=byte,
        year=st.integers(2000, 2099),
        month=st.integers(1, 12),
        day=st.integers(1, 31),
        day_of_week=st.integers(0, 7),
    ),
    st.builds(AreaCascadingSync, area=areas, channel=channel_or_all, join=joins),
    st.builds(
        OccupancyDetectionAllPresets,
        area=areas,
        channel=channel_or_all,
        resume=st.booleans(),
        join=joins,
    ),
    st.builds(
        HoldTimerAllPresets,
        area=areas,
        channel=channel_or_all,
        state_raw=byte,
        join=joins,
    ),
    *(
        st.builds(cls, area=areas, channel=channel_or_all, join=joins)
        for cls in (SuspendOccupancyDetection, ResumeOccupancyDetection)
    ),
    st.builds(
        ProgramLevelsToPreset, area=areas, preset=st.integers(1, 256), join=joins
    ),
    st.builds(
        DeviceIdentify,
        device_code=byte,
        box=byte,
        firmware_major=byte,
        firmware_minor=byte,
        boot_raw=byte,
    ),
    st.builds(
        DeviceStatusReport,
        device_code=byte,
        box=byte,
        sub_opcode=byte,
        data1_raw=byte,
        data2_raw=byte,
    ),
    st.builds(RequestFirmwareVersion, device_code=byte, box=byte, processor_id=byte),
)


@given(extended_messages)
def test_round_trip(message: Message) -> None:
    assert decode(message.to_packet()) == message


def test_preset_select_is_unchanged() -> None:
    # Registering more opcodes must not disturb the original ones.
    packet = Packet.from_hex("1c1c32010000ff")
    assert decode(packet) == PresetSelect(area=28, preset=2, fade=1)


def test_occupancy_detection_with_an_unknown_state_stays_unknown() -> None:
    packet = Packet.from_hex("1c1aff310002ff")
    assert decode(packet) == UnknownMessage(packet=packet)


def test_occupancy_detection_needs_a_bool() -> None:
    with pytest.raises(TypeError, match="resume must be a bool"):
        OccupancyDetectionAllPresets(area=1, resume=1)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "temperature"),
    [
        ("1c170c4a1b06ff", 27.06),  # decoded by the official network monitor
        ("1c130c4a164aff", 22.74),  # the office, 2026-11-19
        ("1c170c4a1a3bff", 26.59),
        ("1c170c4a0000ff", 0.0),
        ("1c170c4a7f63ff", 127.99),
        ("1c170c4a8000ff", None),  # negative readings: encoding not seen yet
        ("1c170c4a1b64ff", None),  # hundredths over 99
        ("1c170d4a1b06ff", None),  # not the temperature preference
    ],
)
def test_temperature(raw: str, temperature: float | None) -> None:
    report = decode(Packet.from_hex(raw))
    assert isinstance(report, UserPreferenceReport)
    assert report.temperature == (
        None if temperature is None else pytest.approx(temperature)
    )


def test_device_identify_version() -> None:
    message = decode(Packet.from_hex("5cb32a00021100"))
    assert isinstance(message, DeviceIdentify)
    assert message.firmware_version == "02.11"
