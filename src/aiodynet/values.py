"""Value types shared by DyNet messages: levels, fades and joins."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar, Self, override

JOIN_ALL = 0xFF
"""The join value that matches every device; the default for every message."""

FADE_UNIT = 0.02
"""Most DyNet fade fields count in 20 ms steps."""


@dataclass(frozen=True, slots=True, order=True)
class Level:
    """A DyNet channel level.

    On the wire a level is one byte, and it runs backwards: ``0x01`` is 100 %
    and ``0xFF`` is 0 % (off). `Level` keeps that byte as `raw`, so a level
    always survives a round trip unchanged, and converts to and from the
    natural scales:

    >>> Level.from_percent(100).raw
    1
    >>> Level.from_fraction(0.5).percent
    50.0
    >>> Level.OFF.is_off
    True

    Some devices report ``0x00``. It is treated as 100 %.
    """

    raw: int
    """The byte as sent on the bus: 0x01 = 100 %, 0xFF = 0 %."""

    OFF: ClassVar[Level]
    FULL: ClassVar[Level]

    def __post_init__(self) -> None:
        """Validate the raw byte."""
        if isinstance(self.raw, bool) or not isinstance(self.raw, int):
            msg = f"Level.raw must be an int, not {type(self.raw).__name__}"
            raise TypeError(msg)
        if not 0 <= self.raw <= 0xFF:
            msg = f"Level.raw must be 0-255, got {self.raw}"
            raise ValueError(msg)

    @classmethod
    def from_fraction(cls, fraction: float) -> Self:
        """Make a level from 0.0 (off) to 1.0 (full)."""
        if not 0.0 <= fraction <= 1.0:
            msg = f"Level fraction must be between 0 and 1, got {fraction}"
            raise ValueError(msg)
        return cls(0xFF - round(fraction * 0xFE))

    @classmethod
    def from_percent(cls, percent: float) -> Self:
        """Make a level from 0 (off) to 100 (full)."""
        if not 0.0 <= percent <= 100.0:
            msg = f"Level percent must be between 0 and 100, got {percent}"
            raise ValueError(msg)
        return cls.from_fraction(percent / 100)

    @property
    def fraction(self) -> float:
        """The level from 0.0 (off) to 1.0 (full)."""
        return min(1.0, (0xFF - self.raw) / 0xFE)

    @property
    def percent(self) -> float:
        """The level from 0 to 100, rounded to one decimal place."""
        return round(self.fraction * 100, 1)

    @property
    def is_off(self) -> bool:
        """Whether this is 0 %."""
        return self.raw == 0xFF

    @override
    def __str__(self) -> str:
        """Return the level as a percentage."""
        return f"{self.percent:g}%"


Level.OFF = Level(0xFF)
Level.FULL = Level(0x01)


def encode_fade(seconds: float, *, unit: float, max_steps: int, field: str) -> int:
    """Convert a fade in seconds to a whole number of `unit`-long steps.

    Rounds to the nearest step. Raises `ValueError` if the fade is negative,
    not finite, or longer than the field can hold, rather than silently
    wrapping or clamping it.
    """
    if isinstance(seconds, bool) or not isinstance(seconds, int | float):
        msg = f"{field} must be a number of seconds, not {type(seconds).__name__}"
        raise TypeError(msg)
    if not math.isfinite(seconds) or seconds < 0:
        msg = f"{field} must be a non-negative number of seconds, got {seconds}"
        raise ValueError(msg)
    steps = round(seconds / unit)
    if steps > max_steps:
        msg = (
            f"{field} of {seconds} s is too long: "
            f"the most this message can carry is {max_steps * unit:g} s"
        )
        raise ValueError(msg)
    return steps


def decode_fade(steps: int, *, unit: float) -> float:
    """Convert a number of `unit`-long steps to seconds."""
    return round(steps * unit, 6)
