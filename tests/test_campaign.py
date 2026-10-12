"""The campaign planner's rules (`utils/campaign.py`)."""

from datetime import date, datetime, timezone

import pytest

from utils import campaign as cp


SETTINGS = cp.settings_with_defaults(None)


def full_mission(**overrides):
    mission = {
        'name': 'Grenzfest', 'op_date': '2026-11-14', 'tz': 'Europe/Berlin',
        'orders_time': '19:30', 'h_hour': '20:15', 'dtg': '03 JUL 1984, 0430',
        'ao': 'Cham region', 'enemy_short': 'Bundeswehr', 'we_are': '23rd MRR',
        'expect': 'Mechanised infantry', 'hook': 'The border is open.',
        'source_label': 'WIRE REPORT', 'situation': 'War.', 'who_we_are': 'Us.',
        'enemy_who': 'PzGren', 'equipment': 'Marder', 'tactics': 'Delay',
        'ground': 'Forest', 'weather': 'Clear', 'objectives': ['KRITZENAST'],
        'mission': 'Seize KRITZENAST.', 'intent': 'Break through.',
        'phases': [{'title': 'Phase 1 – Assault', 'detail': 'Go'}],
        'support': 'Mortars', 'resupply': 'Truck',
        'nets': [{'net': 'PLT', 'channel': '152 ch 1'}],
        'timings': [{'event': 'H-Hour', 'time': '20:15'}],
        'tasks': [{'callsign': '1-1', 'tasks': 'Clear'}], 'loadout': 'Rifle',
        'outcome': 'We won.', 'results': [{'objective': 'KRITZENAST', 'result': 'Taken'}],
    }
    mission.update(overrides)
    return mission


def ctx(**settings):
    return cp.PostContext(settings={**SETTINGS, **settings}, guild_tz='Europe/London')


# --- fields -----------------------------------------------------------------

def test_field_keys_are_unique_and_every_drop_has_a_step():
    keys = [f.key for step in cp.STEPS for f in step.fields]
    assert len(keys) == len(set(keys))
    assert all(drop.step in cp.STEP for drop in cp.DROPS)
    assert set(cp.ORBAT_OPENERS) <= set(cp.DROP_ORDER)


def test_filled_ignores_blank_rows():
    assert not cp.filled([{'a': ' ', 'b': ''}])
    assert cp.filled([{'a': '', 'b': 'x'}])
    assert not cp.filled(['', '  '])
    assert not cp.filled(None)
    assert cp.filled('x')


def test_step_progress():
    assert cp.step_progress({}, 'concept') == (0, 10)
    assert cp.step_complete(full_mission(), 'concept')
    assert cp.step_progress(full_mission(hook=''), 'concept') == (9, 10)


# --- dates ------------------------------------------------------------------

def test_parse_time():
    assert cp.parse_time('1930').hour == 19
    assert cp.parse_time('7:05').minute == 5
    assert cp.parse_time('25:00') is None
    assert cp.parse_time('soon') is None


def test_to_utc_honours_the_zone_and_dst():
    summer = cp.to_utc('2026-07-01', '20:00', 'Europe/Berlin')
    winter = cp.to_utc('2026-12-01', '20:00', 'Europe/Berlin')
    assert summer == datetime(2026, 7, 1, 18, 0, tzinfo=timezone.utc)
    assert winter == datetime(2026, 12, 1, 19, 0, tzinfo=timezone.utc)
    assert cp.to_utc('', '20:00', 'UTC') is None
    # an unknown zone falls back to UTC rather than failing a post
    assert cp.to_utc('2026-07-01', '20:00', 'Mars/Olympus') == datetime(2026, 7, 1, 20, tzinfo=timezone.utc)


def test_discord_ts():
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert cp.discord_ts(moment, 'R') == f'<t:{int(moment.timestamp())}:R>'
    assert cp.discord_ts(None) == '[time]'


def test_drop_dates_saturday_op_wednesday_drops():
    dates = cp.drop_dates(date(2026, 11, 14), 2)   # a Saturday
    assert dates['warno'] == date(2026, 10, 17)
    assert dates['situation'] == date(2026, 10, 21)   # D-24
    assert dates['intsum'] == date(2026, 10, 28)
    assert dates['opord'] == date(2026, 11, 4)
    assert dates['frago'] == date(2026, 11, 11)       # D-3
    assert dates['aar'] == date(2026, 11, 15)


def test_drop_on_the_op_weekday_is_a_full_week_earlier():
    dates = cp.drop_dates(date(2026, 11, 14), 5)   # Saturday drops, Saturday op
    assert dates['frago'] == date(2026, 11, 7)


def test_schedule_statuses_and_order():
    mission = full_mission(hook='')
    items = cp.schedule(mission, SETTINGS, today=date(2026, 10, 9), guild_tz='Europe/London')
    concept = next(i for i in items if i['kind'] == 'gate' and i['key'] == 'concept')
    assert concept['status'][0] == 'warn'            # D-35 is 10 Oct, one day away
    warno = next(i for i in items if i['kind'] == 'drop' and i['key'] == 'warno')
    assert not warno['ready']
    situation = next(i for i in items if i['key'] == 'situation')
    assert situation['note'] == 'ORBAT opens for everyone'
    assert [i['date'] for i in items] == sorted(i['date'] for i in items)
    assert items[-2]['kind'] == 'op' or items[-1]['key'] == 'aar'
    assert cp.schedule({}, SETTINGS, today=date(2026, 1, 1)) == []


def test_drop_times_use_the_guild_zone():
    times = cp.drop_times(full_mission(), SETTINGS, 'Europe/London')
    assert times['situation'] == datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)  # BST
    assert times['frago'] == datetime(2026, 11, 11, 19, 0, tzinfo=timezone.utc)      # GMT


# --- settings ---------------------------------------------------------------

def test_settings_defaults():
    assert SETTINGS['drop_weekday'] == 2
    assert SETTINGS['drop_time'] == '19:00'
    assert SETTINGS['orbat_opens'] == 'situation'
    assert '{op}' in SETTINGS['survey_url']
    bad = cp.settings_with_defaults({'drop_weekday': 9, 'drop_time': 'x', 'orbat_opens': 'aar'})
    assert (bad['drop_weekday'], bad['drop_time'], bad['orbat_opens']) == (2, '19:00', 'situation')


def test_clean_settings():
    form = {'unit_role_id': '5', 'drop_channel_id': '10', 'team_channel_id': '11',
            'drop_weekday': '3', 'drop_time': '1830', 'orbat_opens': 'intsum',
            'survey_url': 'https://tally.so/r/x?op={op}'}
    clean = cp.clean_settings(form, role_ids=[5], channel_ids=[10, 11])
    assert clean == {'unit_role_id': '5', 'drop_channel_id': '10', 'team_channel_id': '11',
                     'drop_weekday': 3, 'drop_time': '18:30', 'orbat_opens': 'intsum',
                     'survey_url': 'https://tally.so/r/x?op={op}'}


@pytest.mark.parametrize('change', [
    {'unit_role_id': '99'},
    {'drop_channel_id': '99'},
    {'drop_weekday': '7'},
    {'drop_weekday': 'Wed'},
    {'drop_time': 'evening'},
    {'orbat_opens': 'aar'},
    {'survey_url': 'tally.so/x'},
    {'team_channel_id': '10'},
])
def test_clean_settings_refuses(change):
    form = {'drop_channel_id': '10', 'drop_weekday': '2', 'drop_time': '19:00',
            'orbat_opens': 'situation', **change}
    with pytest.raises(ValueError):
        cp.clean_settings(form, role_ids=[5], channel_ids=[10, 11])


def test_survey_url_encodes_spaces():
    assert cp.survey_url('https://tally.so/r/D4l88q?op={op}', 'Iron Gate') == \
        'https://tally.so/r/D4l88q?op=Iron%20Gate'
    assert cp.survey_url('https://x/?op=[MISSION NAME]', 'A&B') == 'https://x/?op=A%26B'
    assert cp.survey_url(None, 'X').endswith('op=X')


# --- posts ------------------------------------------------------------------

def test_every_drop_builds_from_an_empty_mission():
    for drop in cp.DROPS:
        body = cp.build_post({}, drop.key, ctx())
        assert body and '[' in body


def test_orbat_line_moves_with_the_opening_drop():
    mission = full_mission()
    assert 'opens for everyone' in cp.orbat_line(mission, 'warno', ctx())
    assert '<t:' in cp.orbat_line(mission, 'warno', ctx())
    assert 'is now open' in cp.orbat_line(mission, 'situation', ctx())
    assert 'Slots are filling' in cp.orbat_line(mission, 'intsum', ctx())
    later = ctx(orbat_opens='opord')
    assert 'opens for everyone' in cp.orbat_line(mission, 'intsum', later)


def test_posts_use_timestamps_and_the_survey():
    mission = full_mission()
    warno = cp.build_post(mission, 'warno', ctx())
    h_hour = cp.to_utc('2026-11-14', '20:15', 'Europe/Berlin')
    assert f'<t:{int(h_hour.timestamp())}:F>' in warno
    frago = cp.build_post(mission, 'frago', ctx())
    assert f'<t:{int(h_hour.timestamp())}:t>' in frago
    aar = cp.build_post(mission, 'aar', ctx())
    assert 'op=Grenzfest' in aar


def test_private_fields_are_never_posted():
    mission = full_mission(zeus_only='SECRET AMBUSH')
    for drop in cp.DROPS:
        assert 'SECRET AMBUSH' not in cp.build_post(mission, drop.key, ctx())


def test_unknown_drop():
    with pytest.raises(ValueError):
        cp.build_post({}, 'nope', ctx())


def test_drop_images():
    mission = {'unit_img': 1, 'map_img': 2, 'recog_img': None, 'traces': [3, 4]}
    assert cp.drop_images(mission, 'situation') == [1]
    assert cp.drop_images(mission, 'intsum') == [2]
    assert cp.drop_images(mission, 'frago') == [3, 4]
    assert cp.drop_images(mission, 'warno') == []


def test_split_message():
    assert cp.split_message('short') == ['short']
    body = '\n\n'.join(['para ' + 'x' * 600] * 6)
    parts = cp.split_message(body)
    assert len(parts) > 1
    assert all(len(p) <= cp.DISCORD_LIMIT for p in parts)
    assert parts[0].endswith(f'-# Part 1/{len(parts)}')
    # one unbroken block still fits
    parts = cp.split_message('y' * 5000)
    assert all(len(p) <= cp.DISCORD_LIMIT for p in parts)


def test_approval_hash_changes_with_text_or_images():
    base = cp.approval_hash('hello', [1, 2])
    assert base == cp.approval_hash('hello', ['1', '2'])
    assert base != cp.approval_hash('hello!', [1, 2])
    assert base != cp.approval_hash('hello', [2, 1])


# --- checks -----------------------------------------------------------------

def _texts(found):
    return ' | '.join(c['text'] for c in found)


def test_checks_clean_mission_has_no_crits():
    found = cp.checks(full_mission(map_img=1), ctx())
    assert not [c for c in found if c['level'] == 'crit']


def test_checks_find_server_details():
    found = cp.checks(full_mission(technical='Server 192.168.0.1 password: hunter2'))
    assert any(c['level'] == 'crit' and c['step'] == 'orders' for c in found)


def test_checks_ignore_private_fields():
    found = cp.checks(full_mission(zeus_only='password: x'))
    assert not [c for c in found if c['level'] == 'crit']


def test_checks_find_spelling_drift():
    found = cp.checks(full_mission(situation='Fighting near Schontal.',
                                   ground='Hills around Schonthal.'))
    assert 'spelled two ways' in _texts(found)


def test_checks_find_phase_count_mismatch():
    found = cp.checks(full_mission(intent='A three-phase attack.'))
    assert 'mentions 3 phases' in _texts(found)


def test_checks_find_objectives_missing_from_orders():
    found = cp.checks(full_mission(objectives=['KRITZENAST', 'HOCHABRUNN']))
    assert '"HOCHABRUNN" is named in the intel' in _texts(found)


def test_checks_flag_long_posts():
    found = cp.checks(full_mission(situation='word ' * 600), ctx())
    assert any(c['drop'] == 'situation' for c in found)
