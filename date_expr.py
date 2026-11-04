"""A small date/time expression language with nearest-time lookup.

The implementation is intentionally self-contained (standard library only).

Grammar
-------
::

    expr    := and_expr
    and_expr:= or_expr ('&' or_expr)*
    or_expr := diff_expr ('|' diff_expr)*
    diff_expr := primary ('!' primary)*
    primary := '(' expr ')' unit?
             | ('{' item (',' item)* '}' | '[' item ',' item ']') suffix?
             | unit_expr
             | NAME
    item    := NUMBER unit? (NUMBER unit?)*
    suffix  := step? unit? | unit step? | '(' step ')' unit? | ...

Precedence is ``()`` > ``!`` > ``|`` > ``&``.  Note that this is the
opposite of the usual C-like precedence: ``&`` binds *loosest*.

A package (the value of an atom) has a *unit*.  It denotes slots at that
unit.  For example ``{10h}`` is the 10 o'clock slot (all minutes in that
hour), while ``{10h5m}`` is the 10:05 minute slot.  A returned time is the
canonical representative of a slot: unspecified smaller fields use the
defaults ``2008Y1M1D9h0m`` (only the fields below the largest specified
unit are defaulted; larger fields stay wildcards).

Built-in holiday sets (China, official schedules for 2025-2026 and the
2027 New Year holiday):

* ``fes`` -- statutory festival day(s)
* ``hol`` -- days off that are not adjusted ("non-make-up" days off)
* ``lil`` -- days off obtained through adjustment
* ``wil`` -- adjusted make-up workdays

The holiday data can be extended with :class:`HolidayCalendar`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from calendar import monthrange
from datetime import date, datetime, time, timedelta, tzinfo
from functools import lru_cache
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

__all__ = [
    "DEFAULT_TZ",
    "DateExprError",
    "ParseError",
    "HolidayCalendar",
    "Expr",
    "Atom",
    "Pattern",
    "parse",
    "matches",
    "nearest",
    "next_time",
    "previous_time",
    "default_calendar",
]

DEFAULT_TZ = ZoneInfo("Asia/Shanghai")

# Field order used by the language.  ``w`` is a day-level field and shares
# the rank of ``D``.  It is kept separate because it is not an arithmetic
# date field (it is a weekday constraint).
UNIT_ORDER = {"Y": 0, "M": 1, "D": 2, "w": 2, "h": 3, "m": 4}
FIELD_NAMES = ("Y", "M", "D", "w", "h", "m")
DEFAULT_VALUES = {"Y": 2008, "M": 1, "D": 1, "h": 9, "m": 0}

_TIME_FACTOR = {"w": 7 * 24 * 60, "D": 24 * 60, "h": 60, "m": 1}
_MAX_INTERVAL_VALUES = 20_000


class DateExprError(ValueError):
    """Base class for all expression errors."""


class ParseError(DateExprError):
    """Raised when an expression cannot be parsed."""


# ---------------------------------------------------------------------------
# Holiday calendar
# ---------------------------------------------------------------------------

FES = 1
HOL = 2
LIL = 4
WIL = 8

BUILTIN_SETS = {"fes": FES, "hol": HOL, "lil": LIL, "wil": WIL}


def _d(year: int, month: int, day: int) -> date:
    return date(year, month, day)


@lru_cache(maxsize=1)
def _default_holiday_days() -> dict[date, int]:
    """China's official holiday arrangement for the supported years.

    Sources:
      * 2025: State Council notice published 2024-11-12.
      * 2026: State Council notice published 2025-11-04.
      * 2027 New Year: announced 3-day holiday, 2027-01-01..03.

    ``hol`` is a non-adjusted day off, ``lil`` is an adjusted day off and
    ``wil`` is an adjusted workday.  A statutory festival day is both
    ``fes`` and ``hol``.
    """

    days: dict[date, int] = {}

    def period(
        start: date,
        end: date,
        *,
        fes: Iterable[date] = (),
        lil: Iterable[date] = (),
        wil: Iterable[date] = (),
    ) -> None:
        fes_set = set(fes)
        lil_set = set(lil)
        work_set = set(wil)
        d = start
        while d <= end:
            if d in work_set:
                days[d] = days.get(d, 0) | WIL
            else:
                flags = 0
                if d in fes_set:
                    flags |= FES
                    flags |= HOL
                elif d in lil_set:
                    flags |= LIL
                else:
                    flags |= HOL
                days[d] = flags
            d += timedelta(days=1)
        # Make-up days are normally outside the holiday period, but accept
        # them explicitly either way.
        for d in work_set:
            days[d] = days.get(d, 0) | WIL

    # ---- 2025 -----------------------------------------------------------
    period(_d(2025, 1, 1), _d(2025, 1, 1), fes={_d(2025, 1, 1)})
    period(
        _d(2025, 1, 28),
        _d(2025, 2, 4),
        fes={_d(2025, 1, 28), _d(2025, 1, 29), _d(2025, 1, 30), _d(2025, 1, 31)},
        lil={_d(2025, 2, 3), _d(2025, 2, 4)},
        wil={_d(2025, 1, 26), _d(2025, 2, 8)},
    )
    period(_d(2025, 4, 4), _d(2025, 4, 6), fes={_d(2025, 4, 4)})
    period(
        _d(2025, 5, 1),
        _d(2025, 5, 5),
        fes={_d(2025, 5, 1), _d(2025, 5, 2)},
        lil={_d(2025, 5, 5)},
        wil={_d(2025, 4, 27)},
    )
    period(_d(2025, 5, 31), _d(2025, 6, 2), fes={_d(2025, 5, 31)})
    period(
        _d(2025, 10, 1),
        _d(2025, 10, 8),
        fes={
            _d(2025, 10, 1),
            _d(2025, 10, 2),
            _d(2025, 10, 3),
            _d(2025, 10, 6),  # Mid-Autumn
        },
        lil={_d(2025, 10, 7), _d(2025, 10, 8)},
        wil={_d(2025, 9, 28), _d(2025, 10, 11)},
    )

    # ---- 2026 -----------------------------------------------------------
    period(
        _d(2026, 1, 1),
        _d(2026, 1, 3),
        fes={_d(2026, 1, 1)},
        lil={_d(2026, 1, 2)},
        wil={_d(2026, 1, 4)},
    )
    period(
        _d(2026, 2, 15),
        _d(2026, 2, 23),
        fes={
            _d(2026, 2, 16),  # New Year's Eve
            _d(2026, 2, 17),
            _d(2026, 2, 18),
            _d(2026, 2, 19),
        },
        lil={_d(2026, 2, 20), _d(2026, 2, 23)},
        wil={_d(2026, 2, 14), _d(2026, 2, 28)},
    )
    period(_d(2026, 4, 4), _d(2026, 4, 6), fes={_d(2026, 4, 5)})
    period(
        _d(2026, 5, 1),
        _d(2026, 5, 5),
        fes={_d(2026, 5, 1), _d(2026, 5, 2)},
        lil={_d(2026, 5, 4), _d(2026, 5, 5)},
        wil={_d(2026, 5, 9)},
    )
    period(_d(2026, 6, 19), _d(2026, 6, 21), fes={_d(2026, 6, 19)})
    period(_d(2026, 9, 25), _d(2026, 9, 27), fes={_d(2026, 9, 25)})
    period(
        _d(2026, 10, 1),
        _d(2026, 10, 7),
        fes={_d(2026, 10, 1), _d(2026, 10, 2), _d(2026, 10, 3)},
        lil={_d(2026, 10, 5), _d(2026, 10, 6), _d(2026, 10, 7)},
        wil={_d(2026, 9, 20), _d(2026, 10, 10)},
    )

    # ---- 2027 -----------------------------------------------------------
    # Only the New Year arrangement is needed for the nearest lookup from
    # late 2026; add the rest when the annual notice is available.
    period(_d(2027, 1, 1), _d(2027, 1, 3), fes={_d(2027, 1, 1)})

    return days


class HolidayCalendar:
    """A mutable collection of holiday/workday flags.

    The default instance contains China's 2025-2026 schedules plus the 2027
    New Year holiday.  Pass a custom instance to :func:`parse` or
    :func:`nearest` to use different data.
    """

    def __init__(
        self,
        days: dict[date, int] | None = None,
        *,
        load_defaults: bool = True,
    ) -> None:
        self._days: dict[date, int] = dict(days or {})
        self._version = 0
        self._date_cache: dict[str, frozenset[date]] = {}
        if load_defaults:
            self._days.update(_default_holiday_days())

    @property
    def version(self) -> int:
        """Monotonic mutation version, used as a parse-cache key."""

        return self._version

    def copy(self) -> "HolidayCalendar":
        clone = HolidayCalendar(load_defaults=False)
        clone._days = dict(self._days)
        clone._version = self._version
        clone._date_cache = dict(self._date_cache)
        return clone

    def add_day(self, day: date, *, fes: bool = False, hol: bool = False,
                lil: bool = False, wil: bool = False) -> None:
        flags = 0
        if fes:
            flags |= FES | HOL
        elif hol:
            flags |= HOL
        if lil:
            flags |= LIL
        if wil:
            flags |= WIL
        self._days[day] = flags
        self._version += 1
        self._date_cache.clear()

    def add_period(
        self,
        start: date,
        end: date,
        *,
        fes: Iterable[date] = (),
        lil: Iterable[date] = (),
        wil: Iterable[date] = (),
    ) -> None:
        """Add an official holiday period.

        Every day in ``[start, end]`` is a holiday.  Days listed in ``fes``
        are statutory festival days, days in ``lil`` are adjusted days off,
        and all remaining days are non-adjusted days off (``hol``).
        ``wil`` contains make-up workdays.
        """

        if end < start:
            raise ValueError("end must not be before start")
        fes_set = set(fes)
        lil_set = set(lil)
        work_set = set(wil)
        d = start
        while d <= end:
            if d in work_set:
                self._days[d] = self._days.get(d, 0) | WIL
            else:
                flags = 0
                if d in fes_set:
                    flags |= FES | HOL
                elif d in lil_set:
                    flags |= LIL
                else:
                    flags |= HOL
                self._days[d] = flags
            d += timedelta(days=1)
        for d in work_set:
            self._days[d] = self._days.get(d, 0) | WIL
        self._version += 1
        self._date_cache.clear()

    def flags_on(self, day: date) -> int:
        return self._days.get(day, 0)

    def dates_for(self, name: str) -> frozenset[date]:
        cached = self._date_cache.get(name)
        if cached is not None:
            return cached
        flag = BUILTIN_SETS.get(name)
        if flag is None:
            raise KeyError(name)
        result = frozenset(
            d for d, flags in self._days.items() if flags & flag
        )
        self._date_cache[name] = result
        return result


_DEFAULT_CALENDAR: HolidayCalendar | None = None


def default_calendar() -> HolidayCalendar:
    """Return the process-wide default calendar (created lazily)."""

    global _DEFAULT_CALENDAR
    if _DEFAULT_CALENDAR is None:
        _DEFAULT_CALENDAR = HolidayCalendar()
    return _DEFAULT_CALENDAR


# ---------------------------------------------------------------------------
# Tokens and lexer
# ---------------------------------------------------------------------------

_NUMBER = "NUMBER"
_UNIT = "UNIT"
_NAME = "NAME"
_LAST = "LAST"
_END = "END"
_START = "START"
_PLUS = "PLUS"
_MINUS = "MINUS"
_NOW = "NOW"
_LBRACE = "LBRACE"
_RBRACE = "RBRACE"
_LBRACKET = "LBRACKET"
_RBRACKET = "RBRACKET"
_LPAREN = "LPAREN"
_RPAREN = "RPAREN"
_COMMA = "COMMA"
_AND = "AND"
_OR = "OR"
_DIFF = "DIFF"
_EOF = "EOF"

_SINGLE = {
    "{": _LBRACE,
    "}": _RBRACE,
    "[": _LBRACKET,
    "]": _RBRACKET,
    "(": _LPAREN,
    ")": _RPAREN,
    ",": _COMMA,
    "&": _AND,
    "|": _OR,
    "!": _DIFF,
    "+": _PLUS,
    "-": _MINUS,
}


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    text: str
    pos: int


def _tokenize(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit():
            start = i
            while i < n and source[i].isdigit():
                i += 1
            tokens.append(_Token(_NUMBER, source[start:i], start))
            continue
        if ch in _SINGLE:
            tokens.append(_Token(_SINGLE[ch], ch, i))
            i += 1
            continue
        if ch.isalpha():
            start = i
            while i < n and source[i].isalpha():
                i += 1
            word = source[start:i]
            if word in BUILTIN_SETS:
                tokens.append(_Token(_NAME, word, start))
                continue
            if word in ("eY", "eM"):
                tokens.append(_Token(_LAST, word, start))
                continue
            if word == "e":
                tokens.append(_Token(_END, word, start))
                continue
            if word == "s":
                tokens.append(_Token(_START, word, start))
                continue
            if word == "now":
                tokens.append(_Token(_NOW, word, start))
                continue
            if word in ("eD", "eh", "em"):
                raise ParseError(
                    f"{word!r} is not supported; e is only valid before Y or M"
                )
            if len(word) == 1 and word in "YMDwhm":
                tokens.append(_Token(_UNIT, word, start))
                continue
            raise ParseError(
                f"unknown name {word!r} at position {start}; "
                f"expected a unit or one of {', '.join(sorted(BUILTIN_SETS))}"
            )
        raise ParseError(f"unexpected character {ch!r} at position {i}")
    tokens.append(_Token(_EOF, "", n))
    return tokens


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------


def _rank(unit: str) -> int:
    try:
        return UNIT_ORDER[unit]
    except KeyError as exc:  # pragma: no cover - internal invariant
        raise ValueError(f"unknown unit {unit!r}") from exc


def _validate_field(unit: str, value: int) -> int:
    if unit == "Y":
        if not 1 <= value <= 9999:
            raise ParseError(f"year out of range: {value}")
    elif unit == "M":
        if not 1 <= value <= 12:
            raise ParseError(f"month out of range: {value}")
    elif unit == "D":
        if not 1 <= value <= 31:
            raise ParseError(f"day out of range: {value}")
    elif unit == "w":
        if value == 0:
            value = 7
        if not 1 <= value <= 7:
            raise ParseError(f"weekday out of range: {value} (1=Mon .. 7=Sun)")
    elif unit == "h":
        if not 0 <= value <= 23:
            raise ParseError(f"hour out of range: {value}")
    elif unit == "m":
        if not 0 <= value <= 59:
            raise ParseError(f"minute out of range: {value}")
    else:  # pragma: no cover
        raise ValueError(unit)
    return value


@dataclass(frozen=True, slots=True)
class _YearCalendar:
    """Per-year day masks used by the bitset search path."""

    year: int
    start_ordinal: int
    day_count: int
    all_mask: int
    month_masks: tuple[int, ...]   # index 1..12
    weekday_masks: tuple[int, ...] # index 1..7


@lru_cache(maxsize=16)
def _year_calendar(year: int) -> _YearCalendar:
    if year < 1 or year > 9999:
        return _YearCalendar(year, 0, 0, 0, (0,) * 13, (0,) * 8)
    start = date(year, 1, 1)
    day_count = 366 if monthrange(year, 2)[1] == 29 else 365
    all_mask = (1 << day_count) - 1
    month_masks = [0] * 13
    weekday_masks = [0] * 8
    ordinal = start.toordinal()
    for bit in range(day_count):
        d = date.fromordinal(ordinal + bit)
        month_masks[d.month] |= 1 << bit
        weekday_masks[d.isoweekday()] |= 1 << bit
    return _YearCalendar(
        year, ordinal, day_count, all_mask,
        tuple(month_masks), tuple(weekday_masks),
    )


def _mask_to_dates(mask: int, year: int) -> set[date]:
    """Expand a day mask to date objects (for the public date API)."""

    if not mask:
        return set()
    cal = _year_calendar(year)
    out: set[date] = set()
    while mask:
        low = mask & -mask
        bit = low.bit_length() - 1
        out.add(date.fromordinal(cal.start_ordinal + bit))
        mask ^= low
    return out


@lru_cache(maxsize=16)
def _dates_in_year(year: int) -> frozenset[date]:
    """All dates in a year, cached for the few years a search touches."""

    if year < 1 or year > 9999:
        return frozenset()
    out: set[date] = set()
    d = date(year, 1, 1)
    end = date(year, 12, 31)
    while True:
        out.add(d)
        if d == end:
            break
        d += timedelta(days=1)
    return frozenset(out)


@dataclass(frozen=True, slots=True)
class Pattern:
    """One calendar slot.

    Fields are single values (or ``None`` for "wildcard").  ``unit`` is the
    slot's granularity.  For membership, fields strictly finer than the unit
    are ignored (the slot covers them).  For candidate generation, those
    fields hold the completed default values instead.
    """

    unit: str
    year: int | None = None
    month: int | None = None
    day: int | None = None
    weekday: int | None = None
    hour: int | None = None
    minute: int | None = None

    def representative_date_matches(self, day: date) -> bool:
        """Whether ``day`` is the canonical date of this slot."""

        if self.year is not None and day.year != self.year:
            return False
        if self.month is not None and day.month != self.month:
            return False
        if self.day is not None and day.day != self.day:
            return False
        if self.weekday is not None and day.isoweekday() != self.weekday:
            return False
        return True

    def covers(self, dt: datetime) -> bool:
        """Whether ``dt`` falls inside the slot's covered region."""

        rank = _rank(self.unit)
        if self.year is not None and dt.year != self.year:
            return False
        if rank >= 1 and self.month is not None and dt.month != self.month:
            return False
        if rank >= 2:
            if self.day is not None and dt.day != self.day:
                return False
            if self.weekday is not None and dt.isoweekday() != self.weekday:
                return False
        if rank >= 3 and self.hour is not None and dt.hour != self.hour:
            return False
        if rank >= 4 and self.minute is not None and dt.minute != self.minute:
            return False
        return True

    def candidate_times(self, day: date) -> set[tuple[int, int]]:
        """Canonical representative times of this slot on ``day``."""

        if not self.representative_date_matches(day):
            return set()
        rank = _rank(self.unit)
        if rank < 3:
            return {(self.hour if self.hour is not None else 9,
                     self.minute if self.minute is not None else 0)}
        if self.unit == "h":
            hour = self.hour if self.hour is not None else 9
            return {(hour, 0)}
        # unit == "m"
        hours: Iterable[int]
        if self.hour is None:
            hours = range(24)
        else:
            hours = (self.hour,)
        minute = self.minute if self.minute is not None else 0
        return {(h, minute) for h in hours}

    def candidate_day_mask(self, year: int) -> int:
        """Bit mask of canonical dates in ``year`` (bit 0 = 1 January)."""

        if year < 1 or year > 9999:
            return 0
        if self.year is not None and self.year != year:
            return 0
        cal = _year_calendar(year)
        if (
            self.month is None
            and self.day is None
            and self.weekday is None
        ):
            return cal.all_mask

        months: Iterable[int]
        if self.month is None:
            months = range(1, 13)
        else:
            months = (self.month,)

        mask = 0
        for month in months:
            if not 1 <= month <= 12:
                continue
            base = cal.month_masks[month]
            if self.day is not None:
                last_day = monthrange(year, month)[1]
                if not 1 <= self.day <= last_day:
                    continue
                bit = date(year, month, self.day).toordinal() - cal.start_ordinal
                base = 1 << bit
            if self.weekday is not None:
                base &= cal.weekday_masks[self.weekday]
            mask |= base
        return mask

    def coverage_day_mask(self, year: int) -> int:
        """Bit mask of every date touched by this slot's coverage."""

        if year < 1 or year > 9999:
            return 0
        if self.year is not None and self.year != year:
            return 0
        cal = _year_calendar(year)
        if (
            self.month is None
            and self.day is None
            and self.weekday is None
        ):
            return cal.all_mask
        rank = _rank(self.unit)
        if rank == 0:
            return cal.all_mask

        months: Iterable[int]
        if rank >= 1 and self.month is not None:
            months = (self.month,)
        else:
            months = range(1, 13)

        mask = 0
        for month in months:
            if not 1 <= month <= 12:
                continue
            base = cal.month_masks[month]
            if rank >= 2 and self.day is not None:
                last_day = monthrange(year, month)[1]
                if not 1 <= self.day <= last_day:
                    continue
                bit = date(year, month, self.day).toordinal() - cal.start_ordinal
                base = 1 << bit
            if rank >= 2 and self.weekday is not None:
                base &= cal.weekday_masks[self.weekday]
            mask |= base
        return mask

    def candidate_dates_in_year(self, year: int) -> set[date]:
        """Compatibility wrapper returning date objects."""

        return _mask_to_dates(self.candidate_day_mask(year), year)

    def coverage_dates_in_year(self, year: int) -> set[date]:
        """Compatibility wrapper returning date objects."""

        return _mask_to_dates(self.coverage_day_mask(year), year)

    def coverage_date_matches(self, day: date) -> bool:
        """Date-level coverage check, independent of time fields."""

        if self.year is not None and day.year != self.year:
            return False
        rank = _rank(self.unit)
        if rank >= 1 and self.month is not None and day.month != self.month:
            return False
        if rank >= 2:
            if self.day is not None and day.day != self.day:
                return False
            if self.weekday is not None and day.isoweekday() != self.weekday:
                return False
        return True

    def coverage_seed_times(self, day: date) -> set[tuple[int, int]]:
        """Representative times on a coverage date (not only canonical date)."""

        if not self.coverage_date_matches(day):
            return set()
        if self.representative_date_matches(day):
            return self.candidate_times(day)
        # Only Y/M slots can cover a date without it being their canonical
        # date.  Their time fields are below the slot granularity, so the
        # completed default is 09:00.
        return {(DEFAULT_VALUES["h"], DEFAULT_VALUES["m"])}

    def is_subset_of(self, other: "Pattern") -> bool:
        """Whether every instant covered by this slot is covered by ``other``.

        Used for O(1) emptiness checks such as ``{10h5m}!{10h}``.
        """

        if _rank(self.unit) < _rank(other.unit):
            return False
        qrank = _rank(other.unit)
        for field in FIELD_NAMES:
            if _rank(field) > qrank:
                continue
            qvalue = getattr(other, _FIELD_ATTR[field])
            if qvalue is None:
                continue
            pvalue = getattr(self, _FIELD_ATTR[field])
            if pvalue != qvalue:
                return False
        return True


_FIELD_ATTR = {
    "Y": "year",
    "M": "month",
    "D": "day",
    "w": "weekday",
    "h": "hour",
    "m": "minute",
}


def _make_pattern(fields: dict[str, int], unit: str) -> Pattern:
    """Apply truncation to ``unit`` and the language's defaulting rules."""

    if unit not in UNIT_ORDER:
        raise ParseError(f"unknown unit {unit!r}")
    rank = _rank(unit)
    kept: dict[str, int] = {}
    for field, value in fields.items():
        if field not in UNIT_ORDER:
            raise ParseError(f"unknown field {field!r}")
        # ``w`` is day-level and is kept for day-or-finer units.
        if _rank(field) <= rank:
            kept[field] = _validate_field(field, value)
        # Fields finer than the package unit are discarded during coercion.

    if not kept:
        raise ParseError("an empty element has no unit")

    has_weekday = "w" in kept

    if has_weekday:
        # A weekday already fixes the day-of-week.  Don't invent M/D;
        # only complete the time-of-day with the defaults.
        if "h" not in kept:
            kept["h"] = DEFAULT_VALUES["h"]
        if "m" not in kept:
            kept["m"] = DEFAULT_VALUES["m"]
    else:
        # The *coarsest* specified unit is the largest calendar unit.  All
        # finer fields that were not supplied take their default value.
        coarsest = min(_rank(f) for f in kept)
        for field in ("M", "D", "h", "m"):
            if _rank(field) > coarsest and field not in kept:
                kept[field] = DEFAULT_VALUES[field]

    return Pattern(
        unit=unit,
        year=kept.get("Y"),
        month=kept.get("M"),
        day=kept.get("D"),
        weekday=kept.get("w"),
        hour=kept.get("h"),
        minute=kept.get("m"),
    )


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------


class Expr:
    """Base class for expression nodes."""

    def covers(self, dt: datetime) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def seed_candidates(self, day: date) -> set[tuple[int, int]]:
        """Times on ``day`` that can possibly be matches.

        Every actual match is covered by at least one positive atom, so
        collecting the canonical candidates of all positive atoms is a
        complete search seed set.
        """

        raise NotImplementedError

    def candidate_day_mask(self, year: int) -> int:
        """Bitset of canonical candidate dates in ``year``."""

        return 0

    def coverage_day_mask(self, year: int) -> int:
        """Bitset of dates covered by this expression."""

        return 0

    def candidate_dates_in_year(self, year: int) -> set[date]:
        """Canonical candidate dates contributed by positive atoms."""

        return set()

    def coverage_dates_in_year(self, year: int) -> set[date]:
        """Dates covered by this expression at any time of day."""

        return set()

    def coverage_seed_candidates(self, day: date) -> set[tuple[int, int]]:
        """Candidate times for residual matches on a coverage date."""

        return self.seed_candidates(day)

    def is_empty(self) -> bool:
        """Whether the expression is provably the empty set."""

        return False

    def explicit_years(self) -> set[int]:
        """Explicit years mentioned by positive atoms, if any."""

        return set()

    def year_is_wildcard(self) -> bool:
        """Whether the expression can match an arbitrary year."""

        return True

    def escape_candidates(self, day: date) -> set[tuple[int, int]]:
        """Candidate times just after a covered slot.

        Used by set difference so that removing the canonical start of a
        coarse slot does not hide later times that are still in the set.
        """

        return set()

    def leaves(self) -> tuple["Atom", ...]:
        return ()


@dataclass(frozen=True, slots=True)
class Atom(Expr):
    patterns: tuple[Pattern, ...]
    unit: str
    label: str = ""

    def covers(self, dt: datetime) -> bool:
        return any(p.covers(dt) for p in self.patterns)

    def seed_candidates(self, day: date) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        for p in self.patterns:
            out.update(p.candidate_times(day))
        return out

    def candidate_day_mask(self, year: int) -> int:
        if year < 1 or year > 9999:
            return 0
        out = 0
        cal = _year_calendar(year)
        for p in self.patterns:
            if (
                (p.year is None or p.year == year)
                and p.month is None
                and p.day is None
                and p.weekday is None
            ):
                return cal.all_mask
            out |= p.candidate_day_mask(year)
        return out

    def coverage_day_mask(self, year: int) -> int:
        if year < 1 or year > 9999:
            return 0
        out = 0
        cal = _year_calendar(year)
        for p in self.patterns:
            if (
                (p.year is None or p.year == year)
                and p.month is None
                and p.day is None
                and p.weekday is None
            ):
                return cal.all_mask
            out |= p.coverage_day_mask(year)
        return out

    def candidate_dates_in_year(self, year: int) -> set[date]:
        return _mask_to_dates(self.candidate_day_mask(year), year)

    def coverage_dates_in_year(self, year: int) -> set[date]:
        return _mask_to_dates(self.coverage_day_mask(year), year)

    def coverage_seed_candidates(self, day: date) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        for p in self.patterns:
            out.update(p.coverage_seed_times(day))
        return out

    def escape_candidates(self, day: date) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        for p in self.patterns:
            for hour, minute in p.candidate_times(day):
                if p.unit == "m":
                    if minute < 59:
                        out.add((hour, minute + 1))
                elif p.unit == "h":
                    if hour < 23:
                        out.add((hour + 1, 0))
                # Day-or-coarser slots have no same-day escape; the day
                # loop naturally advances to the next day.
        return out

    def is_empty(self) -> bool:
        return not self.patterns

    def is_subset_of(self, other: "Atom") -> bool:
        """Whether this atom's entire coverage is inside ``other``."""

        if not self.patterns:
            return True
        if not other.patterns:
            return False
        return all(
            any(p.is_subset_of(q) for q in other.patterns)
            for p in self.patterns
        )

    def explicit_years(self) -> set[int]:
        return {p.year for p in self.patterns if p.year is not None}

    def year_is_wildcard(self) -> bool:
        return any(p.year is None for p in self.patterns)

    def leaves(self) -> tuple["Atom", ...]:
        return (self,)

    def with_unit(self, unit: str) -> "Atom":
        """Coerce every slot to ``unit`` by truncating finer fields."""

        patterns: list[Pattern] = []
        for p in self.patterns:
            fields: dict[str, int] = {}
            for field in FIELD_NAMES:
                value = getattr(p, _FIELD_ATTR[field])
                if value is not None and _rank(field) <= _rank(unit):
                    fields[field] = value
            if fields:
                patterns.append(_make_pattern(fields, unit))
            else:
                # A pure time-of-day slot coerced to a date unit has no
                # date fields.  Keep it as an every-day default slot rather
                # than failing the whole parse.
                patterns.append(
                    Pattern(unit=unit, hour=DEFAULT_VALUES["h"],
                            minute=DEFAULT_VALUES["m"])
                )
        return Atom(tuple(patterns), unit, self.label)


@dataclass(frozen=True, slots=True)
class _LastNode(Expr):
    """The last day of a year (``eY``) or of each month (``eM``).

    The slot is day-granular: it covers the whole last day and its canonical
    representative is 09:00 unless an ``h``/``m`` suffix overrides the time.
    """

    kind: str                 # "Y" or "M"

    @property
    def unit(self) -> str:
        return "D"

    def _matches_day(self, day: date) -> bool:
        if self.kind == "Y":
            return day.month == 12 and day.day == 31
        return day.day == monthrange(day.year, day.month)[1]

    def covers(self, dt: datetime) -> bool:
        return self._matches_day(dt.date())

    def seed_candidates(self, day: date) -> set[tuple[int, int]]:
        if not self._matches_day(day):
            return set()
        return {(DEFAULT_VALUES["h"], DEFAULT_VALUES["m"])}

    def candidate_day_mask(self, year: int) -> int:
        if year < 1 or year > 9999:
            return 0
        cal = _year_calendar(year)
        if self.kind == "Y":
            bit = date(year, 12, 31).toordinal() - cal.start_ordinal
            return 1 << bit
        mask = 0
        for month in range(1, 13):
            last = monthrange(year, month)[1]
            bit = date(year, month, last).toordinal() - cal.start_ordinal
            mask |= 1 << bit
        return mask

    def coverage_day_mask(self, year: int) -> int:
        return self.candidate_day_mask(year)

    def candidate_dates_in_year(self, year: int) -> set[date]:
        return _mask_to_dates(self.candidate_day_mask(year), year)

    def coverage_dates_in_year(self, year: int) -> set[date]:
        return _mask_to_dates(self.coverage_day_mask(year), year)

    def coverage_seed_candidates(self, day: date) -> set[tuple[int, int]]:
        return self.seed_candidates(day)

    def year_is_wildcard(self) -> bool:
        return True

    def escape_candidates(self, day: date) -> set[tuple[int, int]]:
        return set()


def _add_months(dt: datetime, months: int) -> datetime:
    total = dt.year * 12 + (dt.month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(dt.day, monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


@dataclass(frozen=True, slots=True)
class _Now(Expr):
    """The current reference time, minute-granular.

    ``nearest``/``matches`` resolve this node against their explicit reference
    time before evaluation.  The methods below are a fallback for direct AST
    use and use the wall clock.
    """

    @property
    def unit(self) -> str:
        return "m"

    def _current(self) -> datetime:
        return datetime.now(DEFAULT_TZ).replace(second=0, microsecond=0)

    def covers(self, dt: datetime) -> bool:
        now = self._current()
        return (
            dt.year, dt.month, dt.day, dt.hour, dt.minute
        ) == (
            now.year, now.month, now.day, now.hour, now.minute
        )

    def seed_candidates(self, day: date) -> set[tuple[int, int]]:
        now = self._current()
        if day != now.date():
            return set()
        return {(now.hour, now.minute)}

    def candidate_day_mask(self, year: int) -> int:
        now = self._current()
        if year != now.year:
            return 0
        cal = _year_calendar(year)
        return 1 << (now.date().toordinal() - cal.start_ordinal)

    def coverage_day_mask(self, year: int) -> int:
        return self.candidate_day_mask(year)

    def coverage_seed_candidates(self, day: date) -> set[tuple[int, int]]:
        return self.seed_candidates(day)

    def escape_candidates(self, day: date) -> set[tuple[int, int]]:
        times = self.seed_candidates(day)
        out: set[tuple[int, int]] = set()
        for hour, minute in times:
            if minute < 59:
                out.add((hour, minute + 1))
            elif hour < 23:
                out.add((hour + 1, 0))
        return out

    def candidate_dates_in_year(self, year: int) -> set[date]:
        return _mask_to_dates(self.candidate_day_mask(year), year)

    def coverage_dates_in_year(self, year: int) -> set[date]:
        return _mask_to_dates(self.coverage_day_mask(year), year)

    def is_empty(self) -> bool:
        return False

    def year_is_wildcard(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class _Boundary(Expr):
    """First/last matching point of each contiguous run of matched dates."""

    child: Expr
    mode: str                 # "first" or "last"

    @property
    def unit(self) -> str:
        return "m"

    @lru_cache(maxsize=512)
    def _valid_times(self, day: date) -> tuple[tuple[int, int], ...]:
        candidates = (
            self.child.seed_candidates(day)
            | self.child.coverage_seed_candidates(day)
        )
        valid: list[tuple[int, int]] = []
        for hour, minute in candidates:
            probe = datetime(
                day.year, day.month, day.day, hour, minute,
                tzinfo=DEFAULT_TZ,
            )
            if self.child.covers(probe):
                valid.append((hour, minute))
        return tuple(sorted(set(valid)))

    @lru_cache(maxsize=256)
    def _match_mask(self, year: int) -> int:
        if year < 1 or year > 9999:
            return 0
        cal = _year_calendar(year)
        coverage = self.child.coverage_day_mask(year)
        out = 0
        for day in _mask_to_dates(coverage, year):
            if self._valid_times(day):
                out |= 1 << (day.toordinal() - cal.start_ordinal)
        return out

    @lru_cache(maxsize=256)
    def _day_mask(self, year: int) -> int:
        if year < 1 or year > 9999:
            return 0
        mask = self._match_mask(year)
        cal = _year_calendar(year)
        if self.mode == "last":
            out = mask & ~(mask >> 1)
            if year < 9999 and self._match_mask(year + 1) & 1:
                out &= ~(1 << (cal.day_count - 1))
            return out
        out = mask & ~(mask << 1)
        if year > 1:
            prev = _year_calendar(year - 1)
            if self._match_mask(year - 1) & (1 << (prev.day_count - 1)):
                out &= ~1
        return out

    @lru_cache(maxsize=512)
    def _selected_time(self, day: date) -> tuple[int, int] | None:
        cal = _year_calendar(day.year)
        bit = day.toordinal() - cal.start_ordinal
        if not (self._day_mask(day.year) & (1 << bit)):
            return None
        valid = self._valid_times(day)
        if not valid:
            return None
        if self.mode == "first":
            return valid[0]
        return valid[-1]

    def covers(self, dt: datetime) -> bool:
        selected = self._selected_time(dt.date())
        return selected == (dt.hour, dt.minute)

    def seed_candidates(self, day: date) -> set[tuple[int, int]]:
        selected = self._selected_time(day)
        return {selected} if selected is not None else set()

    def candidate_day_mask(self, year: int) -> int:
        return self._day_mask(year)

    def coverage_day_mask(self, year: int) -> int:
        return self._day_mask(year)

    def coverage_seed_candidates(self, day: date) -> set[tuple[int, int]]:
        return self.seed_candidates(day)

    def escape_candidates(self, day: date) -> set[tuple[int, int]]:
        selected = self._selected_time(day)
        if selected is None:
            return set()
        hour, minute = selected
        if minute < 59:
            return {(hour, minute + 1)}
        if hour < 23:
            return {(hour + 1, 0)}
        return set()

    def candidate_dates_in_year(self, year: int) -> set[date]:
        return _mask_to_dates(self._day_mask(year), year)

    def coverage_dates_in_year(self, year: int) -> set[date]:
        return _mask_to_dates(self._day_mask(year), year)

    def is_empty(self) -> bool:
        return self.child.is_empty()

    def explicit_years(self) -> set[int]:
        return self.child.explicit_years()

    def year_is_wildcard(self) -> bool:
        return self.child.year_is_wildcard()


@dataclass(frozen=True, slots=True)
class _Shift(Expr):
    """Shift every matching time by ``months`` and ``minutes``."""

    child: Expr
    months: int
    minutes: int

    @property
    def unit(self) -> str:
        return "m"

    def _apply(self, dt: datetime) -> datetime:
        if self.months:
            dt = _add_months(dt, self.months)
        if self.minutes:
            dt = dt + timedelta(minutes=self.minutes)
        return dt

    def _inverse(self, target: datetime) -> list[datetime]:
        base = target - timedelta(minutes=self.minutes)
        if self.months == 0:
            return [base]
        total = base.year * 12 + (base.month - 1) - self.months
        year, month0 = divmod(total, 12)
        month = month0 + 1
        out: list[datetime] = []
        for day in range(1, monthrange(year, month)[1] + 1):
            source = base.replace(year=year, month=month, day=day)
            if self._apply(source) == target:
                out.append(source)
        return out

    def covers(self, dt: datetime) -> bool:
        return any(self.child.covers(src) for src in self._inverse(dt))

    def _source_days(self, day: date) -> list[date]:
        if self.months == 0:
            base = day - timedelta(days=self.minutes // 1440)
            start = base - timedelta(days=2)
            return [start + timedelta(days=i) for i in range(5)]
        anchor = day - timedelta(days=self.minutes // (24 * 60))
        base = date(anchor.year, anchor.month, 1)
        total = base.year * 12 + (base.month - 1) - self.months
        year, month0 = divmod(total, 12)
        month = month0 + 1
        center = date(year, month, 1)
        start = (center - timedelta(days=1)).replace(day=1)
        end_month = (center + timedelta(days=32)).replace(day=1)
        end = (end_month + timedelta(days=32)).replace(day=1)
        out = []
        d = start
        while d < end:
            out.append(d)
            d += timedelta(days=1)
        return out

    def seed_candidates(self, day: date) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        for source_day in self._source_days(day):
            times = (
                self.child.seed_candidates(source_day)
                | self.child.coverage_seed_candidates(source_day)
            )
            for hour, minute in times:
                source = datetime(
                    source_day.year, source_day.month, source_day.day,
                    hour, minute, tzinfo=DEFAULT_TZ,
                )
                shifted = self._apply(source)
                if shifted.date() == day:
                    out.add((shifted.hour, shifted.minute))
        return out

    def candidate_day_mask(self, year: int) -> int:
        # Safe over-approximation; seed_candidates/covers do exact filtering.
        return _year_calendar(year).all_mask

    def coverage_day_mask(self, year: int) -> int:
        if year < 1 or year > 9999:
            return 0
        target = _year_calendar(year)
        out = 0
        whole_days = self.minutes % (24 * 60) == 0
        year_shift = self.months // 12 + self.minutes // (365 * 24 * 60)
        center_year = year - year_shift
        for source_year in range(
            max(1, center_year - 2), min(9999, center_year + 2) + 1
        ):
            source_dates = _mask_to_dates(
                self.child.coverage_day_mask(source_year), source_year
            )
            for source_day in source_dates:
                if whole_days:
                    source = datetime(
                        source_day.year, source_day.month, source_day.day,
                        0, 0,
                    )
                    shifted = self._apply(source)
                    if shifted.year == year:
                        out |= 1 << (
                            shifted.toordinal() - target.start_ordinal
                        )
                    continue
                times = (
                    self.child.seed_candidates(source_day)
                    | self.child.coverage_seed_candidates(source_day)
                )
                for hour, minute in times:
                    source = datetime(
                        source_day.year, source_day.month, source_day.day,
                        hour, minute,
                    )
                    shifted = self._apply(source)
                    if shifted.year == year:
                        out |= 1 << (
                            shifted.toordinal() - target.start_ordinal
                        )
        return out

    def candidate_dates_in_year(self, year: int) -> set[date]:
        return _mask_to_dates(self.candidate_day_mask(year), year)

    def coverage_dates_in_year(self, year: int) -> set[date]:
        return _mask_to_dates(self.coverage_day_mask(year), year)

    def coverage_seed_candidates(self, day: date) -> set[tuple[int, int]]:
        return self.seed_candidates(day)

    def escape_candidates(self, day: date) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        for hour, minute in self.seed_candidates(day):
            if minute < 59:
                out.add((hour, minute + 1))
            elif hour < 23:
                out.add((hour + 1, 0))
        return out

    def is_empty(self) -> bool:
        return self.child.is_empty()

    def explicit_years(self) -> set[int]:
        years = self.child.explicit_years()
        if not years:
            return set()
        out: set[int] = set()
        for y in years:
            for month, day in ((1, 1), (12, 31)):
                source = datetime(y, month, day)
                out.add(self._apply(source).year)
        return out

    def year_is_wildcard(self) -> bool:
        return self.child.year_is_wildcard()


@dataclass(frozen=True, slots=True)
class _Periodic(Expr):
    """Forward arithmetic sequence anchored at the query's current time."""

    months: int
    minutes: int
    anchor: datetime | None = None

    @property
    def unit(self) -> str:
        return "m"

    def _start(self) -> datetime:
        if self.anchor is not None:
            return self.anchor
        return datetime.now(DEFAULT_TZ).replace(second=0, microsecond=0)

    def covers(self, dt: datetime) -> bool:
        return (dt.hour, dt.minute) in self.seed_candidates(dt.date())

    def seed_candidates(self, day: date) -> set[tuple[int, int]]:
        start = self._start()
        if day < start.date():
            return set()
        out: set[tuple[int, int]] = set()
        if self.months == 0:
            step = self.minutes
            if step <= 0:
                return set()
            anchor_abs = start.hour * 60 + start.minute
            target_start = (day - start.date()).days * 1440
            lo = max(0, -(-(target_start - anchor_abs) // step))
            hi = (target_start + 1439 - anchor_abs) // step
            for k in range(lo, hi + 1):
                total = anchor_abs + k * step
                out.add(((total % 1440) // 60, total % 60))
            return out

        def point(k: int) -> datetime:
            shifted = _add_months(start, k * self.months)
            return shifted + timedelta(minutes=k * self.minutes)

        # The sequence is monotonic.  Find the first period point whose date
        # is at least `day`, then inspect a small neighbourhood.  This also
        # handles compound periods where the minute component moves across
        # several months per step.
        hi = 1
        while point(hi).date() < day and hi < 1_000_000:
            hi *= 2
        lo, high = 0, hi
        while lo < high:
            mid = (lo + high) // 2
            if point(mid).date() < day:
                lo = mid + 1
            else:
                high = mid
        for k in range(max(0, lo - 2), lo + 3):
            p = point(k)
            if p.date() == day:
                out.add((p.hour, p.minute))
        return out

    def candidate_day_mask(self, year: int) -> int:
        if year < 1 or year > 9999:
            return 0
        start = self._start()
        if year < start.year:
            return 0
        cal = _year_calendar(year)
        if year > start.year:
            return cal.all_mask
        first_bit = start.date().toordinal() - cal.start_ordinal
        return cal.all_mask & ~((1 << first_bit) - 1)

    def coverage_day_mask(self, year: int) -> int:
        return self.candidate_day_mask(year)

    def coverage_seed_candidates(self, day: date) -> set[tuple[int, int]]:
        return self.seed_candidates(day)

    def escape_candidates(self, day: date) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        for hour, minute in self.seed_candidates(day):
            if minute < 59:
                out.add((hour, minute + 1))
            elif hour < 23:
                out.add((hour + 1, 0))
        return out

    def candidate_dates_in_year(self, year: int) -> set[date]:
        return _mask_to_dates(self.candidate_day_mask(year), year)

    def coverage_dates_in_year(self, year: int) -> set[date]:
        return _mask_to_dates(self.coverage_day_mask(year), year)

    def is_empty(self) -> bool:
        return False

    def year_is_wildcard(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class _And(Expr):
    left: Expr
    right: Expr

    def covers(self, dt: datetime) -> bool:
        return self.left.covers(dt) and self.right.covers(dt)

    def seed_candidates(self, day: date) -> set[tuple[int, int]]:
        return self.left.seed_candidates(day) | self.right.seed_candidates(day)

    def candidate_dates_in_year(self, year: int) -> set[date]:
        return (
            self.left.candidate_dates_in_year(year)
            | self.right.candidate_dates_in_year(year)
        )

    def candidate_day_mask(self, year: int) -> int:
        return (
            self.left.candidate_day_mask(year)
            | self.right.candidate_day_mask(year)
        )

    def coverage_day_mask(self, year: int) -> int:
        return (
            self.left.coverage_day_mask(year)
            & self.right.coverage_day_mask(year)
        )

    def coverage_dates_in_year(self, year: int) -> set[date]:
        return (
            self.left.coverage_dates_in_year(year)
            & self.right.coverage_dates_in_year(year)
        )

    def coverage_seed_candidates(self, day: date) -> set[tuple[int, int]]:
        return (
            self.left.coverage_seed_candidates(day)
            | self.right.coverage_seed_candidates(day)
        )

    def is_empty(self) -> bool:
        return self.left.is_empty() or self.right.is_empty()

    def explicit_years(self) -> set[int]:
        return self.left.explicit_years() | self.right.explicit_years()

    def year_is_wildcard(self) -> bool:
        return self.left.year_is_wildcard() or self.right.year_is_wildcard()

    def escape_candidates(self, day: date) -> set[tuple[int, int]]:
        return (
            self.left.escape_candidates(day)
            | self.right.escape_candidates(day)
        )

    def leaves(self) -> tuple["Atom", ...]:
        return self.left.leaves() + self.right.leaves()


@dataclass(frozen=True, slots=True)
class _Or(Expr):
    left: Expr
    right: Expr

    def covers(self, dt: datetime) -> bool:
        return self.left.covers(dt) or self.right.covers(dt)

    def seed_candidates(self, day: date) -> set[tuple[int, int]]:
        return self.left.seed_candidates(day) | self.right.seed_candidates(day)

    def candidate_dates_in_year(self, year: int) -> set[date]:
        return (
            self.left.candidate_dates_in_year(year)
            | self.right.candidate_dates_in_year(year)
        )

    def candidate_day_mask(self, year: int) -> int:
        return (
            self.left.candidate_day_mask(year)
            | self.right.candidate_day_mask(year)
        )

    def coverage_day_mask(self, year: int) -> int:
        return (
            self.left.coverage_day_mask(year)
            | self.right.coverage_day_mask(year)
        )

    def coverage_dates_in_year(self, year: int) -> set[date]:
        return (
            self.left.coverage_dates_in_year(year)
            | self.right.coverage_dates_in_year(year)
        )

    def coverage_seed_candidates(self, day: date) -> set[tuple[int, int]]:
        return (
            self.left.coverage_seed_candidates(day)
            | self.right.coverage_seed_candidates(day)
        )

    def is_empty(self) -> bool:
        return self.left.is_empty() and self.right.is_empty()

    def explicit_years(self) -> set[int]:
        return self.left.explicit_years() | self.right.explicit_years()

    def year_is_wildcard(self) -> bool:
        return self.left.year_is_wildcard() or self.right.year_is_wildcard()

    def escape_candidates(self, day: date) -> set[tuple[int, int]]:
        return (
            self.left.escape_candidates(day)
            | self.right.escape_candidates(day)
        )

    def leaves(self) -> tuple["Atom", ...]:
        return self.left.leaves() + self.right.leaves()


@dataclass(frozen=True, slots=True)
class _Diff(Expr):
    left: Expr
    right: Expr

    def covers(self, dt: datetime) -> bool:
        return self.left.covers(dt) and not self.right.covers(dt)

    def seed_candidates(self, day: date) -> set[tuple[int, int]]:
        return (
            self.left.coverage_seed_candidates(day)
            | self.right.seed_candidates(day)
            | self.right.escape_candidates(day)
        )

    def candidate_dates_in_year(self, year: int) -> set[date]:
        # The right side can remove a canonical date without removing the
        # whole left slot (e.g. 2026Y ! {1D}); all coverage dates of the left
        # side are therefore candidates.
        return (
            self.left.coverage_dates_in_year(year)
            | self.right.candidate_dates_in_year(year)
        )

    def candidate_day_mask(self, year: int) -> int:
        return (
            self.left.coverage_day_mask(year)
            | self.right.candidate_day_mask(year)
        )

    def coverage_day_mask(self, year: int) -> int:
        return self.left.coverage_day_mask(year)

    def coverage_dates_in_year(self, year: int) -> set[date]:
        return self.left.coverage_dates_in_year(year)

    def coverage_seed_candidates(self, day: date) -> set[tuple[int, int]]:
        return (
            self.left.coverage_seed_candidates(day)
            | self.right.escape_candidates(day)
        )

    def is_empty(self) -> bool:
        if self.left.is_empty() or self.right.is_empty():
            return self.left.is_empty()
        if isinstance(self.left, Atom) and isinstance(self.right, Atom):
            return self.left.is_subset_of(self.right)
        return False

    def explicit_years(self) -> set[int]:
        return self.left.explicit_years() | self.right.explicit_years()

    def year_is_wildcard(self) -> bool:
        return self.left.year_is_wildcard() or self.right.year_is_wildcard()

    def escape_candidates(self, day: date) -> set[tuple[int, int]]:
        return (
            self.left.escape_candidates(day)
            | self.right.escape_candidates(day)
        )

    def leaves(self) -> tuple["Atom", ...]:
        return self.left.leaves() + self.right.leaves()


def _aligned(dt: datetime, unit: str) -> bool:
    if dt.second or dt.microsecond:
        return False
    if unit == "m":
        return True
    if unit == "h":
        return dt.minute == 0
    if unit == "D":
        return dt.hour == 0 and dt.minute == 0
    return False


@dataclass(frozen=True, slots=True)
class _Cast(Expr):
    """Project an expression onto buckets of a time unit.

    Casts to ``D``, ``h`` and ``m`` are supported.  They denote the set of
    buckets of the target unit that contain at least one match of the child
    expression.  Coarser atom casts are folded by :meth:`Atom.with_unit`;
    finer atom casts use this projection node.
    """

    child: Expr
    unit: str

    def covers(self, dt: datetime) -> bool:
        if not _aligned(dt, self.unit):
            return False
        if self.unit == "m":
            return self.child.covers(dt)
        if self.unit == "h":
            base = dt.replace(minute=0)
            return any(
                self.child.covers(base.replace(minute=m))
                for m in range(60)
            )
        if self.unit == "D":
            base = dt.replace(hour=0, minute=0)
            # Avoid constructing a full 1440-element list; short-circuit.
            for h in range(24):
                for m in range(60):
                    if self.child.covers(base.replace(hour=h, minute=m)):
                        return True
            return False
        raise ParseError("composite casts support only D, h and m")

    def seed_candidates(self, day: date) -> set[tuple[int, int]]:
        if self.unit == "m":
            base = datetime.combine(day, time(0, 0))
            out = set()
            for h in range(24):
                for m in range(60):
                    dt = base.replace(hour=h, minute=m)
                    if self.child.covers(dt):
                        out.add((h, m))
            return out
        if self.unit == "h":
            base = datetime.combine(day, time(0, 0))
            out = set()
            for h in range(24):
                dt = base.replace(hour=h)
                if self.covers(dt):
                    out.add((h, 0))
            return out
        if self.unit == "D":
            dt = datetime.combine(day, time(0, 0))
            return {(0, 0)} if self.covers(dt) else set()
        raise ParseError("composite casts support only D, h and m")

    def candidate_day_mask(self, year: int) -> int:
        return self.child.coverage_day_mask(year)

    def coverage_day_mask(self, year: int) -> int:
        return self.child.coverage_day_mask(year)

    def candidate_dates_in_year(self, year: int) -> set[date]:
        return self.child.coverage_dates_in_year(year)

    def coverage_dates_in_year(self, year: int) -> set[date]:
        return self.child.coverage_dates_in_year(year)

    def coverage_seed_candidates(self, day: date) -> set[tuple[int, int]]:
        return self.seed_candidates(day)

    def escape_candidates(self, day: date) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        for hour, minute in self.seed_candidates(day):
            if self.unit == "m":
                if minute < 59:
                    out.add((hour, minute + 1))
                elif hour < 23:
                    out.add((hour + 1, 0))
            elif self.unit == "h":
                if hour < 23:
                    out.add((hour + 1, 0))
        return out

    def is_empty(self) -> bool:
        return self.child.is_empty()

    def explicit_years(self) -> set[int]:
        return self.child.explicit_years()

    def year_is_wildcard(self) -> bool:
        return self.child.year_is_wildcard()

    def leaves(self) -> tuple["Atom", ...]:
        return self.child.leaves()


# ---------------------------------------------------------------------------
# Parser helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ElementSpec:
    bare: int | None
    components: tuple[tuple[int, str], ...]
    raw: str

    @property
    def units(self) -> tuple[str, ...]:
        return tuple(u for _, u in self.components)


@dataclass(frozen=True, slots=True)
class _SuffixSpec:
    unit: str | None = None
    step: int = 1
    step_unit: str | None = None


def _parse_element_tokens(tokens: Sequence[_Token]) -> _ElementSpec:
    if not tokens:
        raise ParseError("empty set element")
    bare: int | None = None
    components: list[tuple[int, str]] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.kind != _NUMBER:
            raise ParseError(f"expected a number at position {tok.pos}")
        number = int(tok.text)
        if i + 1 < len(tokens) and tokens[i + 1].kind == _UNIT:
            unit = tokens[i + 1].text
            components.append((number, unit))
            i += 2
        else:
            if bare is not None or components:
                raise ParseError(
                    f"a bare number must be the only part of an element "
                    f"(position {tok.pos})"
                )
            bare = number
            i += 1
    seen: set[str] = set()
    for _, unit in components:
        if unit in seen:
            raise ParseError(f"duplicate unit {unit!r} in one element")
        seen.add(unit)
    return _ElementSpec(
        bare=bare,
        components=tuple(components),
        raw="".join(t.text for t in tokens),
    )


def _parse_suffix_tokens(tokens: Sequence[_Token]) -> _SuffixSpec:
    if not tokens:
        return _SuffixSpec()
    text = "".join(t.text for t in tokens)

    # unit only
    m = re.fullmatch(r"([YMDwhm])", text)
    if m:
        return _SuffixSpec(unit=m.group(1))

    # number [unit] -- a step; its unit, if present, is also a good
    # candidate for the package unit when the elements have no units.
    m = re.fullmatch(r"(\d+)([YMDwhm])?", text)
    if m:
        step = int(m.group(1))
        if step <= 0:
            raise ParseError("step must be greater than zero")
        return _SuffixSpec(step=step, step_unit=m.group(2))

    # unit then number
    m = re.fullmatch(r"([YMDwhm])(\d+)", text)
    if m:
        step = int(m.group(2))
        if step <= 0:
            raise ParseError("step must be greater than zero")
        return _SuffixSpec(unit=m.group(1), step=step)

    # unit then (number+unit)
    m = re.fullmatch(r"([YMDwhm])\((\d+)([YMDwhm])\)", text)
    if m:
        step = int(m.group(2))
        if step <= 0:
            raise ParseError("step must be greater than zero")
        return _SuffixSpec(unit=m.group(1), step=step, step_unit=m.group(3))

    # (number+unit) then optional unit
    m = re.fullmatch(r"\((\d+)([YMDwhm])\)([YMDwhm])?", text)
    if m:
        step = int(m.group(1))
        if step <= 0:
            raise ParseError("step must be greater than zero")
        return _SuffixSpec(unit=m.group(3), step=step, step_unit=m.group(2))

    # number+step-unit then package unit
    m = re.fullmatch(r"(\d+)([YMDwhm])([YMDwhm])", text)
    if m:
        step = int(m.group(1))
        if step <= 0:
            raise ParseError("step must be greater than zero")
        return _SuffixSpec(unit=m.group(3), step=step, step_unit=m.group(2))

    raise ParseError(f"cannot parse package suffix {text!r}")


def _factor(unit: str) -> int:
    try:
        return _TIME_FACTOR[unit]
    except KeyError as exc:
        raise ParseError(
            f"unit {unit!r} cannot be converted to a time-of-day unit"
        ) from exc


def _convert_amount(amount: int, from_unit: str, to_unit: str) -> int:
    """Convert an integer amount, raising if it is not exact."""

    if from_unit == to_unit:
        return amount
    if from_unit in _TIME_FACTOR and to_unit in _TIME_FACTOR:
        total_minutes = amount * _factor(from_unit)
        target = _factor(to_unit)
        if total_minutes % target:
            raise ParseError(
                f"step {amount}{from_unit} is not an exact number of {to_unit}"
            )
        return total_minutes // target
    if from_unit == "Y" and to_unit == "M":
        return amount * 12
    if from_unit == "M" and to_unit == "Y" and amount % 12 == 0:
        return amount // 12
    raise ParseError(f"cannot convert {from_unit} to {to_unit}")


def _scalar_in_unit(spec: _ElementSpec, base_unit: str) -> int:
    """Interpret a scalar element in ``base_unit``.

    Bare numbers are already in ``base_unit``.  A single component with a
    compatible unit is converted exactly; an inexact conversion is an error.
    """

    if spec.bare is not None:
        return spec.bare
    if len(spec.components) == 1:
        value, unit = spec.components[0]
        return _convert_amount(value, unit, base_unit)
    if all(unit in _TIME_FACTOR for _, unit in spec.components):
        total_minutes = sum(value * _factor(unit)
                            for value, unit in spec.components)
        target = _factor(base_unit)
        if total_minutes % target == 0:
            return total_minutes // target
    raise ParseError(
        f"expected a scalar bound, got {spec.raw!r}; "
        "its units cannot be combined exactly in the package unit"
    )


def _interval_values(start: int, end: int, step: int) -> list[int]:
    if step <= 0:
        raise ParseError("step must be greater than zero")
    if start > end:
        raise ParseError("interval start is greater than its end")
    count = (end - start) // step + 1
    if count > _MAX_INTERVAL_VALUES:
        raise ParseError(
            f"interval would generate {count} elements; "
            f"limit is {_MAX_INTERVAL_VALUES}"
        )
    return list(range(start, end + 1, step))


def _scalar_to_minutes(spec: _ElementSpec, base_unit: str) -> int:
    """Convert a bound to minutes since the relevant origin.

    Used only when a finer step requires promoting the package unit.
    """

    if spec.bare is not None:
        return spec.bare * _factor(base_unit)
    total = 0
    for value, unit in spec.components:
        if unit not in _TIME_FACTOR:
            raise ParseError(
                f"cannot combine {unit} with a finer time step in {spec.raw!r}"
            )
        total += value * _factor(unit)
    return total


def _interval_atoms(
    items: Sequence[_ElementSpec],
    unit: str,
    step: int,
    step_unit: str,
) -> tuple[Pattern, ...]:
    if len(items) != 2:
        raise ParseError("[] requires exactly two bounds")
    start_spec, end_spec = items

    # Case 1: the step is finer than the package unit.  Promote the whole
    # interval to the step's unit so that, e.g., [8,9](30m)h works.
    if (
        unit in _TIME_FACTOR
        and step_unit in _TIME_FACTOR
        and _factor(step_unit) < _factor(unit)
        and unit != "w"
    ):
        start_min = _scalar_to_minutes(start_spec, unit)
        end_min = _scalar_to_minutes(end_spec, unit)
        step_min = step * _factor(step_unit)
        values = _interval_values(start_min, end_min, step_min)
        patterns: list[Pattern] = []
        new_unit = step_unit
        for value in values:
            fields: dict[str, int] = {}
            if unit == "D":
                day, remainder = divmod(value, 24 * 60)
                fields["D"] = day
                fields["h"] = remainder // 60
                if new_unit == "m":
                    fields["m"] = remainder % 60
            elif unit == "h":
                hour, remainder = divmod(value, 60)
                fields["h"] = hour
                if new_unit == "m":
                    fields["m"] = remainder
            else:
                raise ParseError(
                    f"cannot promote interval unit {unit} to {new_unit}"
                )
            patterns.append(_make_pattern(fields, new_unit))
        return tuple(patterns)

    # Case 2: normal integer arithmetic in the package unit.
    start = _scalar_in_unit(start_spec, unit)
    end = _scalar_in_unit(end_spec, unit)
    step_in_unit = _convert_amount(step, step_unit, unit)
    values = _interval_values(start, end, step_in_unit)
    return tuple(_make_pattern({unit: value}, unit) for value in values)


def _set_atoms(
    items: Sequence[_ElementSpec],
    unit: str,
    step: int,
) -> tuple[Pattern, ...]:
    selected = list(items)
    if step > 1:
        selected = selected[::step]
    patterns: list[Pattern] = []
    for item in selected:
        if item.bare is not None:
            patterns.append(_make_pattern({unit: item.bare}, unit))
        else:
            fields: dict[str, int] = {}
            for value, field_unit in item.components:
                fields[field_unit] = value
            patterns.append(_make_pattern(fields, unit))
    return tuple(patterns)


# ---------------------------------------------------------------------------
# Recursive-descent parser
# ---------------------------------------------------------------------------


class _Parser:
    def __init__(self, tokens: Sequence[_Token], calendar: HolidayCalendar) -> None:
        self.tokens = tokens
        self.calendar = calendar
        self.i = 0

    # -- token helpers ---------------------------------------------------
    def _peek(self, offset: int = 0) -> _Token:
        idx = min(self.i + offset, len(self.tokens) - 1)
        return self.tokens[idx]

    def _at(self, kind: str) -> bool:
        return self._peek().kind == kind

    def _match(self, kind: str) -> _Token | None:
        if self._at(kind):
            tok = self._peek()
            self.i += 1
            return tok
        return None

    def _expect(self, kind: str, what: str) -> _Token:
        tok = self._match(kind)
        if tok is None:
            got = self._peek()
            raise ParseError(
                f"expected {what} at position {got.pos}, got {got.text!r}"
            )
        return tok

    def _parse_unit_token(self) -> str | None:
        tok = self._match(_UNIT)
        return tok.text if tok else None

    # -- grammar ---------------------------------------------------------
    def parse(self) -> Expr:
        expr = self._parse_and()
        if not self._at(_EOF):
            tok = self._peek()
            raise ParseError(f"unexpected token {tok.text!r} at position {tok.pos}")
        return expr

    def _parse_and(self) -> Expr:
        # & has the *lowest* precedence.
        left = self._parse_or()
        while self._match(_AND):
            right = self._parse_or()
            left = _And(left, right)
        return left

    def _parse_or(self) -> Expr:
        left = self._parse_diff()
        while self._match(_OR):
            right = self._parse_diff()
            left = _Or(left, right)
        return left

    def _parse_diff(self) -> Expr:
        # ! binds tighter than | but looser than parentheses.
        left = self._parse_primary()
        while self._match(_DIFF):
            right = self._parse_primary()
            left = _Diff(left, right)
        return left

    def _parse_primary(self) -> Expr:
        tok = self._peek()
        if tok.kind == _PLUS:
            self.i += 1
            if self._match(_LPAREN):
                months, minutes = self._parse_offset_terms()
                self._expect(_RPAREN, "')'")
            else:
                months, minutes = self._parse_offset_terms()
            if months == 0 and minutes == 0:
                raise ParseError("a periodic expression needs a non-zero period")
            return self._parse_postfixed(
                _Periodic(months, minutes), allow_endpoint=False
            )

        if tok.kind == _LPAREN:
            self.i += 1
            expr = self._parse_and()
            self._expect(_RPAREN, "')'")
            unit = self._parse_unit_token()
            if unit is not None:
                if isinstance(expr, _LastNode):
                    if unit == "D":
                        expr = expr
                    elif unit in ("h", "m"):
                        expr = _Cast(expr, unit)
                    else:
                        raise ParseError(
                            "an eY/eM cast supports only D, h or m"
                        )
                elif isinstance(expr, Atom):
                    if unit in ("D", "h", "m") and _rank(unit) > _rank(expr.unit):
                        # A fine-grained projection, e.g. (hol)h.
                        expr = _Cast(expr, unit)
                    else:
                        expr = expr.with_unit(unit)
                elif unit not in ("D", "h", "m"):
                    raise ParseError(
                        "a cast on a composite expression supports only D, h or m"
                    )
                else:
                    expr = _Cast(expr, unit)
            return self._parse_postfixed(expr, allow_endpoint=True)

        if tok.kind in (_LBRACE, _LBRACKET):
            return self._parse_postfixed(
                self._parse_package(), allow_endpoint=False
            )

        if tok.kind == _NUMBER:
            return self._parse_postfixed(
                self._parse_bare_unit_expr(), allow_endpoint=False
            )

        if tok.kind == _LAST:
            self.i += 1
            return self._parse_postfixed(
                self._parse_last_node(tok), allow_endpoint=False
            )

        if tok.kind == _NOW:
            self.i += 1
            return self._parse_postfixed(_Now(), allow_endpoint=False)

        if tok.kind == _NAME:
            self.i += 1
            dates = self.calendar.dates_for(tok.text)
            patterns = tuple(
                _make_pattern({"Y": d.year, "M": d.month, "D": d.day}, "D")
                for d in sorted(dates)
            )
            return self._parse_postfixed(
                Atom(patterns, "D", tok.text), allow_endpoint=False
            )

        raise ParseError(
            f"expected '(', '{{', '[' or a number at position {tok.pos}, "
            f"got {tok.text!r}"
        )

    def _parse_package(self) -> Expr:
        opener = self._peek()
        closing = _RBRACE if opener.kind == _LBRACE else _RBRACKET
        self.i += 1

        items: list[_ElementSpec] = []
        while not self._at(closing):
            if self._at(_EOF):
                raise ParseError(f"unclosed {opener.text!r}")
            item_tokens: list[_Token] = []
            while (
                not self._at(_COMMA)
                and not self._at(closing)
                and not self._at(_EOF)
            ):
                item_tokens.append(self._peek())
                self.i += 1
            items.append(_parse_element_tokens(item_tokens))
            if self._match(_COMMA):
                if self._at(closing):
                    raise ParseError("trailing comma in package")
                continue
            break

        self._expect(closing, f"'{closing}'")
        if not items:
            raise ParseError("empty package")

        suffix_tokens: list[_Token] = []
        while True:
            tok = self._peek()
            if tok.kind in (_NUMBER, _UNIT):
                suffix_tokens.append(tok)
                self.i += 1
                continue
            # A parenthesised step such as [1,10](2D).
            if (
                tok.kind == _LPAREN
                and self._peek(1).kind == _NUMBER
                and self._peek(2).kind == _UNIT
                and self._peek(3).kind == _RPAREN
            ):
                suffix_tokens.extend(self.tokens[self.i:self.i + 4])
                self.i += 4
                continue
            break
        suffix = _parse_suffix_tokens(suffix_tokens)

        all_units = [u for item in items for u in item.units]
        if suffix.unit is not None:
            unit = suffix.unit
        elif all_units:
            unit = max(all_units, key=_rank)
        elif suffix.step_unit is not None:
            unit = suffix.step_unit
        else:
            raise ParseError(
                "cannot infer the package unit; add a unit such as "
                f"{opener.text}...{closing}{{unit}}"
            )

        if opener.kind == _LBRACKET:
            patterns = _interval_atoms(
                items, unit, suffix.step, suffix.step_unit or unit
            )
        else:
            patterns = _set_atoms(items, unit, suffix.step)
        actual_unit = max((p.unit for p in patterns), key=_rank)
        return Atom(patterns, actual_unit)

    def _parse_offset_terms(self) -> tuple[int, int]:
        months = 0
        minutes = 0
        seen = False
        while (
            self._peek().kind == _NUMBER
            and self._peek(1).kind == _UNIT
        ):
            value = int(self._peek().text)
            unit = self._peek(1).text
            if unit == "Y":
                months += value * 12
            elif unit == "M":
                months += value
            elif unit in ("D", "w", "h", "m"):
                minutes += value * _factor(unit)
            else:  # pragma: no cover - lexer only emits known units
                raise ParseError(f"invalid offset unit {unit!r}")
            seen = True
            self.i += 2
        if not seen:
            tok = self._peek()
            raise ParseError(
                f"expected an offset such as 1D at position {tok.pos}"
            )
        return months, minutes

    def _parse_shift(self, expr: Expr, sign: int) -> Expr:
        if self._match(_LPAREN):
            months, minutes = self._parse_offset_terms()
            self._expect(_RPAREN, "')'")
        else:
            months, minutes = self._parse_offset_terms()
        return _Shift(expr, sign * months, sign * minutes)

    def _parse_postfixed(self, expr: Expr, *, allow_endpoint: bool) -> Expr:
        while True:
            if allow_endpoint and self._match(_END):
                expr = _Boundary(expr, "last")
                continue
            if allow_endpoint and self._match(_START):
                expr = _Boundary(expr, "first")
                continue
            sign_token = self._match(_PLUS) or self._match(_MINUS)
            if sign_token is not None:
                sign = 1 if sign_token.kind == _PLUS else -1
                expr = self._parse_shift(expr, sign)
                continue
            return expr

    def _parse_last_node(self, tok: _Token) -> Expr:
        # eY/eM are strictly day-granular.  A time of day is expressed by
        # intersection, e.g. eY & {10h30m}, not by a suffix.
        if self._peek().kind == _NUMBER:
            tok2 = self._peek()
            raise ParseError(
                f"{tok.text} has D precision; a suffix is not allowed. "
                f"Use {tok.text} & {{...}} to choose a time of day."
            )
        return _LastNode(tok.text[1])

    def _parse_bare_unit_expr(self) -> Expr:
        tokens: list[_Token] = []
        while True:
            tok = self._peek()
            if tok.kind == _NUMBER and self._peek(1).kind == _UNIT:
                tokens.append(self._peek())
                tokens.append(self._peek(1))
                self.i += 2
            else:
                break
        if not tokens:
            tok = self._peek()
            raise ParseError(
                f"a number at position {tok.pos} must be followed by a unit"
            )
        spec = _parse_element_tokens(tokens)
        unit = max(spec.units, key=_rank)
        pattern = _make_pattern(
            {u: v for v, u in spec.components}, unit
        )
        return Atom((pattern,), unit)


# ---------------------------------------------------------------------------
# Public parse / match API
# ---------------------------------------------------------------------------


@lru_cache(maxsize=128)
def _parse_default_cached(expression: str, calendar_version: int) -> Expr:
    """Parse with the mutable default calendar.

    ``calendar_version`` is part of the cache key; mutating the default
    calendar therefore invalidates old entries automatically.
    """

    del calendar_version  # used only as part of the cache key
    return _Parser(_tokenize(expression), default_calendar()).parse()


def parse(
    expression: str,
    *,
    calendar: HolidayCalendar | None = None,
) -> Expr:
    """Parse ``expression`` into an expression tree.

    Parsing with the default calendar is memoised.  Custom calendars are not
    cached because they are mutable and often short-lived.
    """

    if not isinstance(expression, str):
        raise TypeError("expression must be a string")
    if not expression.strip():
        raise ParseError("expression is empty")
    if calendar is None:
        return _parse_default_cached(expression, default_calendar().version)
    return _Parser(_tokenize(expression), calendar).parse()


def _coerce_now(now: datetime | None, tz: tzinfo) -> datetime:
    if now is None:
        return datetime.now(tz)
    if not isinstance(now, datetime):
        raise TypeError("now must be a datetime or None")
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def _now_atom(pivot: datetime) -> Atom:
    pattern = Pattern(
        unit="m",
        year=pivot.year,
        month=pivot.month,
        day=pivot.day,
        hour=pivot.hour,
        minute=pivot.minute,
    )
    return Atom((pattern,), "m", "now")


def _resolve_now(expr: Expr, pivot: datetime) -> Expr:
    """Replace every ``now`` node with a concrete minute slot at ``pivot``."""

    if isinstance(expr, _Now):
        return _now_atom(pivot)
    if isinstance(expr, _Periodic):
        anchor = expr.anchor
        if anchor is None:
            anchor = pivot.replace(second=0, microsecond=0)
        return _Periodic(expr.months, expr.minutes, anchor)
    if isinstance(expr, (Atom, _LastNode)):
        return expr
    if isinstance(expr, _And):
        return _And(_resolve_now(expr.left, pivot), _resolve_now(expr.right, pivot))
    if isinstance(expr, _Or):
        return _Or(_resolve_now(expr.left, pivot), _resolve_now(expr.right, pivot))
    if isinstance(expr, _Diff):
        return _Diff(_resolve_now(expr.left, pivot), _resolve_now(expr.right, pivot))
    if isinstance(expr, _Cast):
        return _Cast(_resolve_now(expr.child, pivot), expr.unit)
    if isinstance(expr, _Boundary):
        return _Boundary(_resolve_now(expr.child, pivot), expr.mode)
    if isinstance(expr, _Shift):
        return _Shift(
            _resolve_now(expr.child, pivot), expr.months, expr.minutes
        )
    return expr


def matches(
    expression: str | Expr,
    when: datetime,
    *,
    calendar: HolidayCalendar | None = None,
    tz: tzinfo | None = None,
    now: datetime | None = None,
) -> bool:
    """Return whether ``when`` is inside the set denoted by ``expression``.

    ``now`` is the reference value used by the built-in ``now`` package.  If
    omitted, the wall clock is used.
    """

    zone = tz if tz is not None else DEFAULT_TZ
    expr = parse(expression, calendar=calendar) if isinstance(expression, str) else expression
    if not isinstance(expr, Expr):
        raise TypeError("expression must be a string or an Expr")
    pivot = _coerce_now(now, zone) if now is not None else datetime.now(zone)
    expr = _resolve_now(expr, pivot)
    if expr.is_empty():
        return False
    return expr.covers(_coerce_now(when, zone))


def _explicit_years(expr: Expr) -> set[int]:
    return expr.explicit_years()


def _search(
    expr: Expr,
    pivot: datetime,
    sign: int,
    start_year: int,
    end_year: int,
) -> datetime | None:
    """Search by candidate *dates*, not by every calendar day.

    The old implementation walked every day in the window.  Here each positive
    atom contributes only its canonical dates for a year, so an expression
    such as ``9999Y`` jumps directly to its single candidate date instead of
    scanning roughly 2.9 million days.
    """

    tz = pivot.tzinfo
    assert tz is not None
    start_date = date(max(1, start_year), 1, 1)
    end_date = date(min(9999, end_year), 12, 31)
    pivot_date = pivot.date()

    if sign > 0:
        first_year = max(start_date.year, pivot_date.year)
        last_year = min(end_date.year, 9999)
        if expr.year_is_wildcard():
            years: Iterable[int] = range(first_year, last_year + 1)
        else:
            years = sorted(
                y for y in expr.explicit_years()
                if first_year <= y <= last_year
            )
        for year in years:
            cal = _year_calendar(year)
            mask = expr.candidate_day_mask(year)
            while mask:
                low = mask & -mask
                day = date.fromordinal(cal.start_ordinal + low.bit_length() - 1)
                mask ^= low
                if day < pivot_date or day < start_date:
                    continue
                if day > end_date:
                    break
                for hour, minute in sorted(expr.seed_candidates(day)):
                    cand = datetime(
                        day.year, day.month, day.day, hour, minute, tzinfo=tz
                    )
                    if cand < pivot:
                        continue
                    if expr.covers(cand):
                        return cand
    else:
        first_year = min(end_date.year, pivot_date.year)
        last_year = max(start_date.year, 1)
        if expr.year_is_wildcard():
            years = range(first_year, last_year - 1, -1)
        else:
            years = sorted(
                (y for y in expr.explicit_years()
                 if last_year <= y <= first_year),
                reverse=True,
            )
        for year in years:
            cal = _year_calendar(year)
            mask = expr.candidate_day_mask(year)
            while mask:
                bit = mask.bit_length() - 1
                day = date.fromordinal(cal.start_ordinal + bit)
                mask ^= 1 << bit
                if day > pivot_date or day > end_date:
                    continue
                if day < start_date:
                    break
                for hour, minute in sorted(
                    expr.seed_candidates(day), reverse=True
                ):
                    cand = datetime(
                        day.year, day.month, day.day, hour, minute, tzinfo=tz
                    )
                    if cand > pivot:
                        continue
                    if expr.covers(cand):
                        return cand
    return None


def nearest(
    expression: str | Expr,
    now: datetime | None = None,
    *,
    direction: str = "nearest",
    horizon_years: int = 8,
    calendar: HolidayCalendar | None = None,
    tz: tzinfo | None = None,
) -> datetime | None:
    """Return the nearest time satisfying ``expression``.

    The default is the mathematically closest slot in either direction
    (``direction="nearest"``); ties go to the future.  Use
    ``direction="next"`` for the next upcoming match, or
    ``direction="previous"`` to search backwards.

    ``None`` is returned when the expression denotes an empty set, or when no
    match exists inside the search window and the expression has no explicit
    year that would extend it.  The search window is ``horizon_years`` around
    the current year, automatically extended to every explicit year in the
    expression.
    """

    if direction not in {"next", "previous", "nearest"}:
        raise ValueError("direction must be 'next', 'previous' or 'nearest'")
    if horizon_years <= 0:
        raise ValueError("horizon_years must be positive")

    zone = tz if tz is not None else DEFAULT_TZ
    expr = parse(expression, calendar=calendar) if isinstance(expression, str) else expression
    if not isinstance(expr, Expr):
        raise TypeError("expression must be a string or an Expr")
    pivot = _coerce_now(now, zone)
    expr = _resolve_now(expr, pivot)
    if expr.is_empty():
        return None
    years = _explicit_years(expr)

    def next_match() -> datetime | None:
        end = max(pivot.year + horizon_years, max(years, default=pivot.year))
        return _search(expr, pivot, +1, pivot.year, min(9999, end))

    def previous_match() -> datetime | None:
        start = min(pivot.year - horizon_years, min(years, default=pivot.year))
        return _search(expr, pivot, -1, max(1, start), pivot.year)

    if direction == "next":
        return next_match()
    if direction == "previous":
        return previous_match()

    future = next_match()
    past = previous_match()
    if future is None:
        return past
    if past is None:
        return future
    if abs(future - pivot) <= abs(past - pivot):
        return future
    return past


def next_time(
    expression: str | Expr,
    now: datetime | None = None,
    **kwargs: object,
) -> datetime | None:
    """Convenience wrapper for ``nearest(..., direction="next")``."""

    kwargs.pop("direction", None)
    return nearest(expression, now, direction="next", **kwargs)  # type: ignore[arg-type]


def previous_time(
    expression: str | Expr,
    now: datetime | None = None,
    **kwargs: object,
) -> datetime | None:
    """Convenience wrapper for ``nearest(..., direction="previous")``."""

    kwargs.pop("direction", None)
    return nearest(expression, now, direction="previous", **kwargs)  # type: ignore[arg-type]


if __name__ == "__main__":  # pragma: no cover - tiny CLI
    import sys

    if len(sys.argv) < 2:
        print("usage: python nearest_date_expr.py '<expression>' [ISO datetime]")
        raise SystemExit(2)
    expr_text = sys.argv[1]
    now_value = (
        datetime.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else None
    )
    print(nearest(expr_text, now_value))
