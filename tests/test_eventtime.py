"""When a series happens next, and what a typed date means.

These are the two pieces of arithmetic in this project that nobody can check by
reading: a monthly series that drifts, or a reminder an hour out because a date
was read in the wrong zone, looks exactly like a correct one until the night it
matters. They ran uncovered for a year.

**Every expected date here was worked out from a calendar, not from the
code.** That is the whole point of the exercise — expectations taken from what
the functions currently return would pin today's behaviour as correct, bugs
included. The 2026 calendar these use, derived once and stated here so a reader
can check the tests themselves:

| | 1st falls on | Saturdays |
|---|---|---|
| Jan | Thursday | 3, 10, 17, 24, **31** |
| Feb | Sunday | 7, 14, 21, 28 |
| Mar | Sunday | 7, 14, 21, 28 |
| Apr | Wednesday | 4, 11, 18, 25 |
| May | Friday | 2, 9, 16, 23, **30** |
| Jun | Monday | 6, 13, 20, 27 |
| Jul | Wednesday | 4, 11, 18, 25 |
| Aug | Saturday | 1, 8, 15, 22, **29** |
| Sep | Tuesday | 5, 12, 19, 26 |
| Oct | Thursday | 3, 10, 17, 24, **31** |
| Nov | Sunday | 7, 14, 21, 28 |
| Dec | Tuesday | 5, 12, 19, 26 |

2026 is not a leap year; 2028 is. Only January, May, August and October have a
**fifth** Saturday — which is the case that has to be *skipped* rather than
treated as the end of a series.

`test_the_calendar_these_tests_assume_is_the_real_one` checks that table
against the standard library, so a wrong assumption in here fails as itself
rather than as a confusing date comparison somewhere below.
"""

import calendar
import datetime as dtmod
from datetime import datetime, timedelta

import pytest

from utils import eventtime

SATURDAY = 5
MONDAY = 0

# The table in the docstring, as data.
SATURDAYS_2026 = {
    1: [3, 10, 17, 24, 31], 2: [7, 14, 21, 28], 3: [7, 14, 21, 28],
    4: [4, 11, 18, 25], 5: [2, 9, 16, 23, 30], 6: [6, 13, 20, 27],
    7: [4, 11, 18, 25], 8: [1, 8, 15, 22, 29], 9: [5, 12, 19, 26],
    10: [3, 10, 17, 24, 31], 11: [7, 14, 21, 28], 12: [5, 12, 19, 26],
}


def test_the_calendar_these_tests_assume_is_the_real_one():
    for month, days in SATURDAYS_2026.items():
        actual = [day for day in range(1, calendar.monthrange(2026, month)[1] + 1)
                  if dtmod.date(2026, month, day).weekday() == SATURDAY]
        assert actual == days, month
    assert calendar.monthrange(2026, 2)[1] == 28
    assert calendar.monthrange(2028, 2)[1] == 29


# --- shifting whole months --------------------------------------------------

def test_a_month_shift_clamps_to_the_last_day_rather_than_overflowing():
    # 31 January + 1 month has no 31st to land on. Clamping is what keeps a
    # series on the 31st: overflowing into March would lose the month entirely.
    assert (eventtime.add_months(datetime(2026, 1, 31, 19, 0), 1)
            == datetime(2026, 2, 28, 19, 0))
    assert (eventtime.add_months(datetime(2026, 3, 31, 19, 0), 1)
            == datetime(2026, 4, 30, 19, 0))
    # And the clamp follows the actual month, not a fixed 28.
    assert (eventtime.add_months(datetime(2028, 1, 31, 19, 0), 1)
            == datetime(2028, 2, 29, 19, 0))


def test_a_month_shift_keeps_the_time_of_day_and_crosses_the_year():
    assert (eventtime.add_months(datetime(2026, 12, 15, 21, 30), 1)
            == datetime(2027, 1, 15, 21, 30))
    assert (eventtime.add_months(datetime(2026, 1, 15, 21, 30), -1)
            == datetime(2025, 12, 15, 21, 30))
    moment = datetime(2026, 6, 15, 7, 45)
    assert eventtime.add_months(moment, 0) == moment


# --- which weekday of the month ---------------------------------------------

def test_the_position_of_a_weekday_within_its_month():
    # 1–7 is the first, 8–14 the second, and so on: 13 June 2026 is a Saturday,
    # and it is the second one — which is what `monthly_nth` means by it.
    assert eventtime.weekday_position(datetime(2026, 6, 13)) == 2
    for day, position in ((1, 1), (7, 1), (8, 2), (14, 2), (15, 3),
                          (22, 4), (28, 4), (29, 5), (31, 5)):
        assert eventtime.weekday_position(datetime(2026, 1, day)) == position, day


def test_the_nth_weekday_of_a_month():
    # June 2026's Saturdays are the 6th, 13th, 20th and 27th.
    assert eventtime.weekday_day(2026, 6, SATURDAY, 1) == 6
    assert eventtime.weekday_day(2026, 6, SATURDAY, 2) == 13
    assert eventtime.weekday_day(2026, 6, SATURDAY, 4) == 27
    # June 2026's Mondays start on the 1st, since the month opens on one.
    assert eventtime.weekday_day(2026, 6, MONDAY, 1) == 1


def test_a_month_with_no_fifth_saturday_answers_none():
    """The single most important case in this file.

    None has to mean "this month has none" rather than "the series is over" —
    a 5th-Saturday series must step over June and land in August.
    """
    assert eventtime.weekday_day(2026, 6, SATURDAY, 5) is None
    assert eventtime.weekday_day(2026, 2, SATURDAY, 5) is None
    # The four months of 2026 that do have one.
    assert eventtime.weekday_day(2026, 1, SATURDAY, 5) == 31
    assert eventtime.weekday_day(2026, 5, SATURDAY, 5) == 30
    assert eventtime.weekday_day(2026, 8, SATURDAY, 5) == 29
    assert eventtime.weekday_day(2026, 10, SATURDAY, 5) == 31


def test_position_minus_one_is_the_last_one_of_the_month():
    assert eventtime.weekday_day(2026, 6, SATURDAY, -1) == 27   # 4 Saturdays
    assert eventtime.weekday_day(2026, 5, SATURDAY, -1) == 30   # 5 Saturdays
    assert eventtime.weekday_day(2026, 2, SATURDAY, -1) == 28   # the month's end
    assert eventtime.weekday_day(2026, 6, MONDAY, -1) == 29


def test_whether_a_date_is_the_last_of_its_weekday_in_the_month():
    assert eventtime.is_last_weekday_of_month(datetime(2026, 6, 27)) is True
    assert eventtime.is_last_weekday_of_month(datetime(2026, 6, 20)) is False
    # May has a Saturday after the 23rd, so the 23rd is not the last.
    assert eventtime.is_last_weekday_of_month(datetime(2026, 5, 23)) is False
    assert eventtime.is_last_weekday_of_month(datetime(2026, 5, 30)) is True


# --- the nth occurrence of a series ------------------------------------------

ANCHOR_2ND_SAT = datetime(2026, 6, 13, 19, 0)   # the 2nd Saturday of June
ANCHOR_LAST_SAT = datetime(2026, 6, 27, 19, 0)  # the last Saturday of June
ANCHOR_1ST_SAT = datetime(2026, 6, 6, 19, 0)
ANCHOR_5TH_SAT = datetime(2026, 1, 31, 19, 0)   # the 5th Saturday of January


def test_the_simple_intervals():
    start = datetime(2026, 6, 13, 19, 0)
    assert eventtime.nth_occurrence(start, 'daily', 0) == start
    assert eventtime.nth_occurrence(start, 'daily', 3) == datetime(2026, 6, 16, 19, 0)
    assert eventtime.nth_occurrence(start, 'weekly', 2) == datetime(2026, 6, 27, 19, 0)
    assert eventtime.nth_occurrence(start, 'biweekly', 2) == datetime(2026, 7, 11, 19, 0)
    assert eventtime.nth_occurrence(start, 'monthly', 1) == datetime(2026, 7, 13, 19, 0)


def test_the_same_weekday_each_month():
    # 2nd Saturdays after 13 June: 11 July, 8 August, 12 September.
    assert eventtime.nth_occurrence(ANCHOR_2ND_SAT, 'monthly_nth', 1) == datetime(2026, 7, 11, 19, 0)
    assert eventtime.nth_occurrence(ANCHOR_2ND_SAT, 'monthly_nth', 2) == datetime(2026, 8, 8, 19, 0)
    assert eventtime.nth_occurrence(ANCHOR_2ND_SAT, 'monthly_nth', 3) == datetime(2026, 9, 12, 19, 0)


def test_the_last_weekday_each_month():
    # Last Saturdays after 27 June: 25 July, 29 August, 26 September.
    assert eventtime.nth_occurrence(ANCHOR_LAST_SAT, 'monthly_last', 1) == datetime(2026, 7, 25, 19, 0)
    assert eventtime.nth_occurrence(ANCHOR_LAST_SAT, 'monthly_last', 2) == datetime(2026, 8, 29, 19, 0)
    assert eventtime.nth_occurrence(ANCHOR_LAST_SAT, 'monthly_last', 3) == datetime(2026, 9, 26, 19, 0)


def test_a_fifth_saturday_series_has_nothing_in_most_months():
    # Anchored on 31 January, a 5th Saturday. February, March and April have
    # none; May's is the 30th.
    assert eventtime.nth_occurrence(ANCHOR_5TH_SAT, 'monthly_nth', 1) is None
    assert eventtime.nth_occurrence(ANCHOR_5TH_SAT, 'monthly_nth', 2) is None
    assert eventtime.nth_occurrence(ANCHOR_5TH_SAT, 'monthly_nth', 3) is None
    assert eventtime.nth_occurrence(ANCHOR_5TH_SAT, 'monthly_nth', 4) == datetime(2026, 5, 30, 19, 0)


def test_weekly_except_the_last_one_answers_none_on_that_week():
    # June 2026's Saturdays are 6, 13, 20, 27; the 27th is the last.
    assert eventtime.nth_occurrence(ANCHOR_1ST_SAT, 'weekly_not_last', 1) == datetime(2026, 6, 13, 19, 0)
    assert eventtime.nth_occurrence(ANCHOR_1ST_SAT, 'weekly_not_last', 2) == datetime(2026, 6, 20, 19, 0)
    assert eventtime.nth_occurrence(ANCHOR_1ST_SAT, 'weekly_not_last', 3) is None
    assert eventtime.nth_occurrence(ANCHOR_1ST_SAT, 'weekly_not_last', 4) == datetime(2026, 7, 4, 19, 0)


def test_an_unknown_pattern_has_no_occurrences():
    assert eventtime.nth_occurrence(ANCHOR_1ST_SAT, 'fortnightly', 1) is None
    assert eventtime.nth_occurrence(ANCHOR_1ST_SAT, 'none', 1) is None


def test_the_two_monthly_patterns_cover_every_saturday_between_them():
    """The pairing the unit actually runs: a monthly op on the last Saturday,
    weekly ops on the others. They must never collide and never leave a gap."""
    start = datetime(2026, 1, 3, 19, 0)            # the first Saturday of 2026
    every = [start + timedelta(weeks=k) for k in range(81)]
    last_of_window = every[-1]

    weekly = {date for date in
              (eventtime.nth_occurrence(start, 'weekly_not_last', k) for k in range(81))
              if date is not None}
    monthly = {date for date in
               (eventtime.nth_occurrence(ANCHOR_5TH_SAT, 'monthly_last', k) for k in range(81))
               if date is not None and start <= date <= last_of_window}

    assert weekly.isdisjoint(monthly), sorted(weekly & monthly)
    assert weekly | monthly == set(every)


# --- the next one after a given moment ---------------------------------------

def test_a_monthly_series_is_measured_from_its_anchor_and_does_not_drift():
    """31 January → 28 February → **31** March.

    Measuring from the previous occurrence instead would give 28 March and the
    series would have lost the 31st for good. This is what the anchor is for.
    """
    anchor = datetime(2026, 1, 31, 19, 0)
    february = eventtime.next_occurrence(anchor, 'monthly', datetime(2026, 2, 1))
    assert february == datetime(2026, 2, 28, 19, 0)
    march = eventtime.next_occurrence(anchor, 'monthly', february)
    assert march == datetime(2026, 3, 31, 19, 0)


def test_a_bot_that_was_offline_for_weeks_catches_up_in_one_step():
    # Ten missed Saturdays produce one date in the future, not ten in the past.
    anchor = datetime(2026, 1, 3, 19, 0)
    assert (eventtime.next_occurrence(anchor, 'weekly', datetime(2026, 3, 10, 0, 0))
            == datetime(2026, 3, 14, 19, 0))


def test_the_next_one_is_strictly_after():
    # An occurrence landing exactly on the cutoff is not "next".
    anchor = datetime(2026, 6, 6, 19, 0)
    assert (eventtime.next_occurrence(anchor, 'weekly', datetime(2026, 6, 13, 19, 0))
            == datetime(2026, 6, 20, 19, 0))


def test_a_month_with_no_matching_day_is_stepped_over_not_stopped_on():
    # The 5th-Saturday series again, through the function the loop calls: from
    # February it has to reach 30 May rather than giving up.
    assert (eventtime.next_occurrence(ANCHOR_5TH_SAT, 'monthly_nth', datetime(2026, 2, 1))
            == datetime(2026, 5, 30, 19, 0))
    # And the last-weekday pattern has one every month, so it does not skip.
    assert (eventtime.next_occurrence(ANCHOR_5TH_SAT, 'monthly_last', datetime(2026, 2, 1))
            == datetime(2026, 2, 28, 19, 0))


def test_the_excluded_week_is_stepped_over_too():
    anchor = datetime(2026, 6, 6, 19, 0)
    assert (eventtime.next_occurrence(anchor, 'weekly_not_last', datetime(2026, 6, 20, 19, 0))
            == datetime(2026, 7, 4, 19, 0))


def test_nothing_to_walk_answers_none():
    assert eventtime.next_occurrence(None, 'weekly', datetime(2026, 6, 1)) is None
    assert eventtime.next_occurrence(datetime(2026, 6, 6), 'none', datetime(2026, 6, 1)) is None
    assert eventtime.next_occurrence(datetime(2026, 6, 6), 'fortnightly', datetime(2026, 6, 1)) is None


def test_the_walk_is_bounded_rather_than_endless():
    # A daily series asked for a date eleven years out runs past the bound and
    # answers None instead of spinning: the loop calling this must not hang.
    anchor = datetime(2026, 1, 1, 19, 0)
    assert eventtime.next_occurrence(anchor, 'daily', datetime(2040, 1, 1)) is None


# --- how a pattern is described ----------------------------------------------

def test_the_weekday_patterns_name_their_weekday():
    def event(recurrence, anchor):
        return {'recurrence': recurrence, 'recurrence_anchor': anchor,
                'event_time': anchor}

    assert (eventtime.recurrence_text(event('monthly_last', ANCHOR_LAST_SAT))
            == 'Monthly · last Saturday of the month')
    assert (eventtime.recurrence_text(event('monthly_nth', ANCHOR_2ND_SAT))
            == 'Monthly · 2nd Saturday of the month')
    assert (eventtime.recurrence_text(event('weekly_not_last', ANCHOR_1ST_SAT))
            == 'Every Saturday except the last one of the month')
    assert eventtime.recurrence_text(event('weekly', ANCHOR_1ST_SAT)) == 'Weekly'


def test_an_event_with_no_anchor_is_described_from_its_own_start():
    # A description is wanted before the row exists, so the anchor may be absent.
    described = eventtime.recurrence_text({
        'recurrence': 'monthly_nth', 'recurrence_anchor': None,
        'event_time': datetime(2026, 6, 13, 19, 0)})
    assert described == 'Monthly · 2nd Saturday of the month'


def test_an_unknown_pattern_describes_itself_as_nothing():
    assert eventtime.recurrence_text({
        'recurrence': None, 'recurrence_anchor': None,
        'event_time': datetime(2026, 6, 13)}) is None


def test_the_ordinals_read_correctly():
    assert [eventtime.ordinal(n) for n in (1, 2, 3, 4, 5)] == ['1st', '2nd', '3rd', '4th', '5th']
    # The teens are the trap: 11th, not 11st.
    assert [eventtime.ordinal(n) for n in (11, 12, 13)] == ['11th', '12th', '13th']
    assert [eventtime.ordinal(n) for n in (21, 22, 23)] == ['21st', '22nd', '23rd']


def test_the_day_names_are_english_whatever_the_machine_says():
    # calendar.day_name follows the process locale, and these strings go into
    # Discord messages and the website.
    assert eventtime.DAY_NAMES[0] == 'Monday'
    assert eventtime.DAY_NAMES[SATURDAY] == 'Saturday'
    assert len(eventtime.DAY_NAMES) == 7


# --- reading a time somebody typed -------------------------------------------

def test_the_three_formats_all_mean_the_same_moment():
    for typed in ('25/06/2026 19:00', '2026-06-25 19:00', '25-06-2026 19:00'):
        assert eventtime.parse_event_time(typed, 'UTC') == datetime(2026, 6, 25, 19, 0), typed


def test_a_slashed_date_is_day_first():
    # 03/04 is the third of April. Reading it the other way round would put an
    # operation a month out with nothing to show for it.
    assert eventtime.parse_event_time('03/04/2026 19:00', 'UTC') == datetime(2026, 4, 3, 19, 0)


def test_the_time_is_read_in_the_guilds_zone_and_stored_as_utc():
    # London is UTC+1 in June and UTC+0 in January, so the same wording is two
    # different moments — which is the whole reason the zone is passed in.
    assert (eventtime.parse_event_time('25/06/2026 19:00', 'Europe/London')
            == datetime(2026, 6, 25, 18, 0))
    assert (eventtime.parse_event_time('25/01/2026 19:00', 'Europe/London')
            == datetime(2026, 1, 25, 19, 0))


def test_what_comes_back_is_naive():
    # It goes into a TIMESTAMP column, which carries no zone; anything aware
    # would be stored with its offset silently dropped.
    assert eventtime.parse_event_time('25/06/2026 19:00', 'Europe/London').tzinfo is None


def test_surrounding_space_is_ignored():
    assert (eventtime.parse_event_time('  25/06/2026 19:00  ', 'UTC')
            == datetime(2026, 6, 25, 19, 0))


def test_a_zone_nobody_has_heard_of_is_read_as_utc():
    """Both of the ways zoneinfo refuses a key, not just the obvious one.

    An unknown name raises `ZoneInfoNotFoundError`; an empty or path-shaped one
    raises a plain `ValueError`, which used to come straight back out of here —
    and since every caller reads a `ValueError` from this function as "I could
    not read that date", the person would have been told their perfectly good
    date was unparseable. Not reachable through either surface today (the slash
    command offers a fixed list of choices and the web form checks membership
    in the same list), so this is the fallback matching its own intent rather
    than a bug anybody has hit.
    """
    for zone in ('Mars/Olympus', '', '   ', '../etc/passwd'):
        assert (eventtime.parse_event_time('25/06/2026 19:00', zone)
                == datetime(2026, 6, 25, 19, 0)), zone


def test_something_unparseable_says_what_it_wanted():
    with pytest.raises(ValueError) as caught:
        eventtime.parse_event_time('next tuesday', 'UTC')
    message = str(caught.value)
    assert 'next tuesday' in message, 'the message quotes what was typed'
    assert 'DD/MM/YYYY HH:MM' in message, 'and names the format it wanted'


def test_a_time_with_seconds_is_refused_rather_than_half_read():
    with pytest.raises(ValueError):
        eventtime.parse_event_time('25/06/2026 19:00:00', 'UTC')
