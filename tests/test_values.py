import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from aiodynet import Level
from aiodynet.values import FADE_UNIT, decode_fade, encode_fade


def test_constants() -> None:
    assert Level.OFF == Level(0xFF)
    assert Level.FULL == Level(0x01)
    assert Level.OFF.is_off
    assert not Level.FULL.is_off


@pytest.mark.parametrize(
    ("raw", "fraction", "percent", "text"),
    [
        (0x01, 1.0, 100.0, "100%"),
        (0xFF, 0.0, 0.0, "0%"),
        (0x80, 0.5, 50.0, "50%"),
        (0x00, 1.0, 100.0, "100%"),  # some devices report 0 for full
    ],
)
def test_conversions(raw: int, fraction: float, percent: float, text: str) -> None:
    level = Level(raw)
    assert level.fraction == pytest.approx(fraction)
    assert level.percent == percent
    assert str(level) == text


@pytest.mark.parametrize(
    ("percent", "raw"), [(0, 0xFF), (100, 0x01), (50, 0x80), (1, 0xFC), (99, 0x04)]
)
def test_from_percent(percent: float, raw: int) -> None:
    assert Level.from_percent(percent).raw == raw


def test_ordering_follows_raw_byte() -> None:
    assert Level.FULL < Level.OFF


@pytest.mark.parametrize("bad", [-0.01, 1.01, math.nan])
def test_from_fraction_range(bad: float) -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        Level.from_fraction(bad)


@pytest.mark.parametrize("bad", [-1, 100.5])
def test_from_percent_range(bad: float) -> None:
    with pytest.raises(ValueError, match="between 0 and 100"):
        Level.from_percent(bad)


@pytest.mark.parametrize("bad", [-1, 256])
def test_raw_range(bad: int) -> None:
    with pytest.raises(ValueError, match="0-255"):
        Level(bad)


@pytest.mark.parametrize("bad", [True, 1.0, "1"])
def test_raw_type(bad: object) -> None:
    with pytest.raises(TypeError, match="must be an int"):
        Level(bad)  # type: ignore[arg-type]


@given(st.integers(1, 255))
def test_percent_round_trip_is_stable(raw: int) -> None:
    level = Level(raw)
    assert Level.from_fraction(level.fraction) == level


@given(st.floats(0, 1))
def test_from_fraction_is_close(fraction: float) -> None:
    assert abs(Level.from_fraction(fraction).fraction - fraction) <= 0.5 / 254


@pytest.mark.parametrize(
    ("seconds", "steps"), [(0, 0), (0.01, 0), (0.011, 1), (0.5, 25), (3, 150)]
)
def test_encode_fade_rounds_to_nearest_step(seconds: float, steps: int) -> None:
    assert encode_fade(seconds, unit=FADE_UNIT, max_steps=255, field="fade") == steps


def test_encode_fade_at_the_limit() -> None:
    assert encode_fade(5.1, unit=FADE_UNIT, max_steps=255, field="fade") == 255


def test_encode_fade_too_long() -> None:
    with pytest.raises(ValueError, match=r"fade of 5.12 s is too long.*5.1 s"):
        encode_fade(5.12, unit=FADE_UNIT, max_steps=255, field="fade")


@pytest.mark.parametrize("bad", [-0.1, math.inf, math.nan])
def test_encode_fade_rejects_bad_values(bad: float) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        encode_fade(bad, unit=FADE_UNIT, max_steps=255, field="fade")


@pytest.mark.parametrize("bad", [True, "1", None])
def test_encode_fade_rejects_non_numbers(bad: object) -> None:
    with pytest.raises(TypeError, match="must be a number of seconds"):
        encode_fade(bad, unit=FADE_UNIT, max_steps=255, field="fade")  # type: ignore[arg-type]


@given(st.integers(0, 0xFFFF))
def test_fade_round_trip(steps: int) -> None:
    seconds = decode_fade(steps, unit=FADE_UNIT)
    assert encode_fade(seconds, unit=FADE_UNIT, max_steps=0xFFFF, field="f") == steps
