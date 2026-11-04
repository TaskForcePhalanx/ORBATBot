"""The events calendar's grid and series preview.

Dates are worked out from the 2026 calendar in test_eventtime.py's docstring:
1 Nov 2026 is a Sunday, 1 Dec a Tuesday, 1 Jan 2027 a Friday; Saturdays in
Nov 2026 are 7, 14, 21, 28.
"""
from datetime import date, datetime

from utils import eventcal


def ev(**kw):
    base = {'recurrence': None, 'recurrence_anchor': None,
            'recurrence_until': None, 'event_time': datetime(2026, 11, 7, 19, 0)}
    base.update(kw)
    return base


def test_parse_month_and_span_fall_back():
    today = date(2026, 11, 4)
    assert eventcal.parse_month('2026-12', today) == date(2026, 12, 1)
    assert eventcal.parse_month('garbage', today) == date(2026, 11, 1)
    assert eventcal.parse_month('2026-13', today) == date(2026, 11, 1)
    assert eventcal.parse_span('2') == 2
    assert eventcal.parse_span('3') == 1
    assert eventcal.parse_span(None) == 1


def test_shift_month_crosses_years():
    assert eventcal.shift_month(date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert eventcal.shift_month(date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert eventcal.shift_month(date(2026, 11, 1), -2) == date(2026, 9, 1)


def test_november_2026_grid_is_monday_first_whole_weeks():
    weeks = eventcal.month_weeks(date(2026, 11, 1))
    # 1 Nov is a Sunday, so the first row starts Monday 26 Oct; 30 Nov is a
    # Monday, so the last row ends Sunday 6 Dec. Six rows.
    assert weeks[0][0] == date(2026, 10, 26)
    assert weeks[0][6] == date(2026, 11, 1)
    assert weeks[-1][0] == date(2026, 11, 30)
    assert weeks[-1][-1] == date(2026, 12, 6)
    assert len(weeks) == 6 and all(len(w) == 7 for w in weeks)


def test_range_bounds_cover_both_months():
    assert eventcal.range_bounds(date(2026, 11, 1), 2) == (date(2026, 10, 26), date(2027, 1, 3))


def test_entries_land_on_their_day_in_time_order():
    entries = [{'when': datetime(2026, 11, 7, 20, 0), 'n': 'late'},
               {'when': datetime(2026, 11, 7, 9, 0), 'n': 'early'}]
    month = eventcal.build_month(date(2026, 11, 1), entries, date(2026, 11, 4))
    cells = {c['date']: c for w in month['weeks'] for c in w}
    assert [e['n'] for e in cells[date(2026, 11, 7)]['entries']] == ['early', 'late']
    assert cells[date(2026, 11, 4)]['today'] and not cells[date(2026, 11, 5)]['today']
    assert not cells[date(2026, 10, 26)]['in_month']
    assert month['label'] == 'November 2026'


def test_weekly_series_previews_following_saturdays_only():
    event = ev(recurrence='weekly')
    got = eventcal.project_series(event, datetime(2026, 11, 30))
    assert [d.day for d in got] == [14, 21, 28]
    assert all(d.hour == 19 for d in got)


def test_series_stops_at_until_and_horizon():
    event = ev(recurrence='weekly', recurrence_until=datetime(2026, 11, 21, 23, 59))
    assert [d.day for d in eventcal.project_series(event, datetime(2026, 12, 31))] == [14, 21]


def test_monthly_last_skips_nothing_and_anchors():
    event = ev(recurrence='monthly_last', event_time=datetime(2026, 11, 28, 19, 0))
    got = eventcal.project_series(event, datetime(2027, 1, 31))
    # Last Saturdays: 26 Dec 2026, 30 Jan 2027.
    assert [(d.year, d.month, d.day) for d in got] == [(2026, 12, 26), (2027, 1, 30)]


def test_no_or_unknown_recurrence_previews_nothing():
    assert eventcal.project_series(ev(), datetime(2027, 1, 1)) == []
    assert eventcal.project_series(ev(recurrence='sometimes'), datetime(2027, 1, 1)) == []


def test_preview_is_capped():
    event = ev(recurrence='daily')
    assert len(eventcal.project_series(event, datetime(2030, 1, 1))) == eventcal.MAX_PREVIEW
