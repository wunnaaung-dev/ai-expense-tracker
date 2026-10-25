from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional

from schemas.insights import Granularity, PeriodKind
from schemas.settings import WeekStart

# Custom ranges up to this many days get daily / weekly bars; longer ones get monthly bars.
_DAILY_MAX_DAYS = 14
_WEEKLY_MAX_DAYS = 92


@dataclass(frozen=True)
class DateRange:
    """Both ends inclusive."""
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


@dataclass(frozen=True)
class Period:
    kind: PeriodKind
    current: DateRange
    previous: DateRange
    granularity: Granularity
    buckets: List[DateRange]


def _clamped(year: int, month: int, day: int) -> date:
    """`day` in that month, or its last day when the month is shorter (Mar 31 → Feb 28)."""
    return date(year, month, min(day, monthrange(year, month)[1]))


def _month_end(day: date) -> date:
    return date(day.year, day.month, monthrange(day.year, day.month)[1])


def _shift_months(first_of_month: date, months: int) -> date:
    index = first_of_month.year * 12 + first_of_month.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _start_of_week(day: date, week_start: WeekStart) -> date:
    first_weekday = 0 if week_start == "monday" else 6
    return day - timedelta(days=(day.weekday() - first_weekday) % 7)


def _current(kind: PeriodKind, today: date, week_start: WeekStart,
             date_from: Optional[date], date_to: Optional[date]) -> DateRange:
    if kind == "week":
        return DateRange(_start_of_week(today, week_start), today)
    if kind == "month":
        return DateRange(today.replace(day=1), today)
    if kind == "year":
        return DateRange(date(today.year, 1, 1), today)
    return DateRange(date_from, date_to)


def _previous(kind: PeriodKind, current: DateRange) -> DateRange:
    """The same days one period earlier ("vs same days last month"); custom: the equal-length range before."""
    if kind == "week":
        return DateRange(current.start - timedelta(days=7), current.end - timedelta(days=7))
    if kind == "month":
        start = _shift_months(current.start, -1)
        return DateRange(start, _clamped(start.year, start.month, current.end.day))
    if kind == "year":
        year = current.start.year - 1
        return DateRange(date(year, 1, 1), _clamped(year, current.end.month, current.end.day))
    end = current.start - timedelta(days=1)
    return DateRange(end - timedelta(days=current.days - 1), end)


def _granularity(kind: PeriodKind, current: DateRange) -> Granularity:
    if kind == "week":
        return "day"
    if kind == "month":
        return "week"
    if kind == "year":
        return "month"
    if current.days <= _DAILY_MAX_DAYS:
        return "day"
    return "week" if current.days <= _WEEKLY_MAX_DAYS else "month"


def buckets(current: DateRange, granularity: Granularity, week_start: WeekStart) -> List[DateRange]:
    """Calendar days, weeks (from `week_start`) or months, clipped to `current`."""
    result: List[DateRange] = []
    start = current.start
    while start <= current.end:
        if granularity == "day":
            end = start
        elif granularity == "week":
            end = _start_of_week(start, week_start) + timedelta(days=6)
        else:
            end = _month_end(start)
        end = min(end, current.end)
        result.append(DateRange(start, end))
        start = end + timedelta(days=1)
    return result


def resolve_period(kind: PeriodKind, today: date, week_start: WeekStart,
                   date_from: Optional[date] = None, date_to: Optional[date] = None) -> Period:
    """Presets end today; `date_from`/`date_to` are read only for `custom`."""
    current = _current(kind, today, week_start, date_from, date_to)
    granularity = _granularity(kind, current)
    return Period(
        kind=kind,
        current=current,
        previous=_previous(kind, current),
        granularity=granularity,
        buckets=buckets(current, granularity, week_start),
    )


def month_ranges(last_day: date, count: int) -> List[DateRange]:
    """`count` whole calendar months, oldest first, ending with the month of `last_day`."""
    last = last_day.replace(day=1)
    firsts = [_shift_months(last, -offset) for offset in range(count - 1, -1, -1)]
    return [DateRange(first, _month_end(first)) for first in firsts]
