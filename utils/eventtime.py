"""When an event happens: reading a typed time, and walking a repeat.

Both halves came out of the cogs unchanged. They had to move because of the
rule that decides what is testable in this project at all — a module that
imports nothing but the standard library can be covered, and `cogs/events.py`
and `cogs/admin.py` pull in discord.py and asyncpg behind them. This is the
arithmetic nobody can check by reading: a monthly series that drifts, or a
reminder an hour out because a date was read in the wrong zone, looks exactly
like a correct one until the night it matters.

`cogs/events.py` and `cogs/admin.py` import these back under the private names
they always had, so every call site is unchanged and the move is a move.

Two rules in here are the ones to know before touching any of it:

- **A repeat is always measured from the series anchor**, never from the
  previous occurrence. Measuring from the previous one turns a 31 January
  series into 28 February → 28 March, losing the 31st for good; anchoring
  gives 28 February → 31 March. It is also what makes a bot that was offline
  for ten weeks post *one* occurrence in the future rather than ten in the
  past.
- **`None` from `nth_occurrence()` means "not this time round", not "the
  series is over".** June has no fifth Saturday; a fifth-Saturday series has
  to step over it and carry on. Every caller skips rather than stops, which is
  also the whole of how `weekly_not_last` is implemented.

`tests/test_eventtime.py` covers all of it, with every expected date worked
out from a calendar rather than from what these functions return.
"""

import calendar
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Recurrence keys stored in events.recurrence. The two weekday variants take
# their weekday — and for monthly_nth their position — from the series anchor.
RECURRENCE_LABELS = {
    'daily': 'Daily',
    'weekly': 'Weekly',
    'biweekly': 'Every 2 weeks',
    'monthly': 'Monthly',
    'monthly_nth': 'Monthly by weekday',
    'monthly_last': 'Monthly, last weekday',
    'weekly_not_last': 'Weekly except the last weekday',
}

# Fixed English names: calendar.day_name follows the process locale, and these
# strings end up in user-facing text and in the docs.
DAY_NAMES = ('Monday', 'Tuesday', 'Wednesday', 'Thursday',
             'Friday', 'Saturday', 'Sunday')

# Day first, because that is how this unit writes dates. `03/04/2026` is the
# third of April.
TIME_FORMATS = ['%d/%m/%Y %H:%M', '%Y-%m-%d %H:%M', '%d-%m-%Y %H:%M']


def parse_event_time(raw: str, tz_name: str = 'UTC') -> datetime:
    """Parse event time in the given timezone, return as naive UTC for storage."""
    raw = raw.strip()
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo('UTC')

    for fmt in TIME_FORMATS:
        try:
            local_dt = datetime.strptime(raw, fmt).replace(tzinfo=tz)
            # Convert to naive UTC for DB storage
            return local_dt.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
        except ValueError:
            continue
    raise ValueError(
        f"Could not parse `{raw}`.\n"
        "Use format `DD/MM/YYYY HH:MM`, e.g. `25/06/2025 19:00`"
    )


def add_months(start: datetime, months: int) -> datetime:
    """Shift by whole months, clamping to the last valid day of the target month
    so 31 January + 1 month lands on 28/29 February rather than overflowing."""
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def weekday_position(dt: datetime) -> int:
    """1-based position of dt's weekday within its month — 4 for the 4th Saturday."""
    return (dt.day - 1) // 7 + 1


def is_last_weekday_of_month(dt: datetime) -> bool:
    return dt.day + 7 > calendar.monthrange(dt.year, dt.month)[1]


def weekday_day(year: int, month: int, weekday: int, position: int) -> Optional[int]:
    """Day-of-month of the *position*-th given weekday, or of the last one when
    position is -1. None if that month has no such day (e.g. no 5th Saturday)."""
    last = calendar.monthrange(year, month)[1]
    if position == -1:
        day = last
        while datetime(year, month, day).weekday() != weekday:
            day -= 1
        return day
    first_weekday = datetime(year, month, 1).weekday()
    day = 1 + (weekday - first_weekday) % 7 + (position - 1) * 7
    return day if day <= last else None


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f"{n}{suffix}"


def nth_occurrence(anchor: datetime, recurrence: str, n: int) -> Optional[datetime]:
    """The nth occurrence counting from the series anchor (n=0 is the anchor).

    None means "this n has no valid date" — for the weekday variants a month may
    simply lack a 5th Saturday — so callers must skip rather than stop.
    """
    if recurrence == 'daily':
        return anchor + timedelta(days=n)
    if recurrence == 'weekly':
        return anchor + timedelta(weeks=n)
    if recurrence == 'weekly_not_last':
        candidate = anchor + timedelta(weeks=n)
        # Returning None makes the caller skip this week and keep walking, which
        # is exactly the exclusion this pattern needs.
        return None if is_last_weekday_of_month(candidate) else candidate
    if recurrence == 'biweekly':
        return anchor + timedelta(weeks=2 * n)
    if recurrence == 'monthly':
        return add_months(anchor, n)
    if recurrence in ('monthly_nth', 'monthly_last'):
        # Shift the month from the 1st so no day-clamping can move us months.
        target = add_months(anchor.replace(day=1), n)
        position = -1 if recurrence == 'monthly_last' else weekday_position(anchor)
        day = weekday_day(target.year, target.month, anchor.weekday(), position)
        if day is None:
            return None
        return anchor.replace(year=target.year, month=target.month, day=day)
    return None


def recurrence_text(event) -> Optional[str]:
    """Human description of an event's recurrence, naming the weekday where the
    pattern depends on it."""
    recurrence = event['recurrence']
    if recurrence not in RECURRENCE_LABELS:
        return None
    anchor = event['recurrence_anchor'] or event['event_time']
    day = DAY_NAMES[anchor.weekday()]
    if recurrence == 'monthly_last':
        return f"Monthly · last {day} of the month"
    if recurrence == 'monthly_nth':
        return f"Monthly · {ordinal(weekday_position(anchor))} {day} of the month"
    if recurrence == 'weekly_not_last':
        return f"Every {day} except the last one of the month"
    return RECURRENCE_LABELS[recurrence]


def next_occurrence(anchor: datetime, recurrence: str, after: datetime) -> Optional[datetime]:
    """First occurrence strictly after *after*.

    Always measured from the anchor, never from the previous occurrence, so a
    monthly series stays on its original day and a bot that was offline for
    weeks catches up to the present in one step instead of posting every
    missed occurrence.
    """
    if recurrence not in RECURRENCE_LABELS or anchor is None:
        return None
    n = 1
    # Bound the walk so a pathological anchor can't spin forever.
    while n <= 4000:
        candidate = nth_occurrence(anchor, recurrence, n)
        # None means this month has no matching day (no 5th Saturday, say) —
        # skip it and keep looking rather than ending the series.
        if candidate is not None and candidate > after:
            return candidate
        n += 1
    return None
