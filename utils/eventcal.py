"""The events calendar: which days a month shows, and where a series lands.

Standard library only, like the date rules it builds on, so the tests can
reach it. Nothing here knows about Discord, the database or a time zone:
callers hand in datetimes already moved into the zone the grid is drawn in.

The grid is Monday-first and always whole weeks, so a month never leaves a
ragged last row and the two-month view stacks two of the same shape.
"""
import calendar
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from utils.eventtime import RECURRENCE_LABELS, nth_occurrence

MAX_SPAN = 2
MONTH_NAMES = ('January', 'February', 'March', 'April', 'May', 'June', 'July',
               'August', 'September', 'October', 'November', 'December')

# A series is previewed, never stored, so the walk needs a ceiling of its own:
# a daily series over two months is about 62 entries, and nothing else is close.
MAX_PREVIEW = 120
_WALK_LIMIT = 4000


def parse_month(raw: Optional[str], today: date) -> date:
    """`2026-11` -> the 1st of that month. Anything unreadable is this month."""
    try:
        year, month = (int(part) for part in (raw or '').strip().split('-'))
        return date(year, month, 1)
    except (ValueError, TypeError):
        return today.replace(day=1)


def parse_span(raw) -> int:
    """How many months to show: 1 or 2, and 1 for anything else."""
    try:
        span = int(raw)
    except (ValueError, TypeError):
        return 1
    return span if 1 <= span <= MAX_SPAN else 1


def shift_month(month: date, delta: int) -> date:
    index = month.year * 12 + (month.month - 1) + delta
    return date(index // 12, index % 12 + 1, 1)


def month_label(month: date) -> str:
    return f"{MONTH_NAMES[month.month - 1]} {month.year}"


def project_series(event, horizon: datetime) -> list:
    """The dates a recurring event will fall on after its own, up to *horizon*.

    Naive UTC in and out, like `events.event_time`. Measured from the anchor
    exactly as the bot measures it when it posts the next occurrence, so what
    the calendar shows is what the bot will do. `nth_occurrence()` answering
    None means *skip this one*, never *stop* (see docs/events.md).

    Only a preview: no row exists for these, which is why the calendar shows
    them without a link.
    """
    recurrence = event['recurrence']
    anchor = event['recurrence_anchor'] or event['event_time']
    if recurrence not in RECURRENCE_LABELS or anchor is None:
        return []
    until = event['recurrence_until']
    out = []
    for n in range(1, _WALK_LIMIT + 1):
        when = nth_occurrence(anchor, recurrence, n)
        if when is None:
            continue
        if when > horizon or (until is not None and when > until):
            break
        if when > event['event_time']:
            out.append(when)
        if len(out) >= MAX_PREVIEW:
            break
    return out


def month_weeks(month: date) -> list:
    """The month as whole Monday-first weeks of dates, spilling into the
    neighbouring months to fill the first and last row."""
    first = month.replace(day=1)
    last = date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])
    start = first - timedelta(days=first.weekday())
    end = last + timedelta(days=6 - last.weekday())
    days = (end - start).days + 1
    cells = [start + timedelta(days=i) for i in range(days)]
    return [cells[i:i + 7] for i in range(0, len(cells), 7)]


def build_month(month: date, entries: Iterable[dict], today: date) -> dict:
    """One month ready to draw. Each entry needs a `when` (naive, in the grid's
    zone); it lands on that date's cell, ordered by time within the day."""
    by_day = {}
    for entry in entries:
        by_day.setdefault(entry['when'].date(), []).append(entry)
    weeks = []
    for week in month_weeks(month):
        weeks.append([{
            'date': day,
            'day': day.day,
            'in_month': day.month == month.month,
            'today': day == today,
            'entries': sorted(by_day.get(day, []), key=lambda e: e['when']),
        } for day in week])
    return {'label': month_label(month), 'weeks': weeks}


def range_bounds(first: date, span: int) -> tuple:
    """The grid's first and last day across *span* months from *first* — what
    to fetch events for."""
    weeks_first = month_weeks(first)
    weeks_last = month_weeks(shift_month(first, span - 1))
    return weeks_first[0][0], weeks_last[-1][-1]
