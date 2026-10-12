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
    clean = cp.clean_settings(form, role_ids=[5, 6], channel_ids=[10, 11],
                              orbat_role_ids=['6', '6'])
    assert clean == {'unit_role_id': '5', 'drop_channel_id': '10', 'team_channel_id': '11',
                     'drop_weekday': 3, 'drop_time': '18:30', 'orbat_opens': 'intsum',
                     'survey_url': 'https://tally.so/r/x?op={op}',
                     'orbat_access': 'planners', 'orbat_role_ids': '6'}
    with pytest.raises(ValueError):
        cp.clean_settings(form, role_ids=[5], channel_ids=[10, 11], orbat_role_ids=['99'])
    with pytest.raises(ValueError):
        cp.clean_settings({**form, 'orbat_access': 'everyone'}, role_ids=[5], channel_ids=[10, 11])


def test_who_may_edit_mission_orbats():
    planners = cp.settings_with_defaults(None)
    admins = cp.settings_with_defaults({'orbat_access': 'admins', 'orbat_role_ids': '7,8'})
    assert planners['orbat_access'] == 'planners' and planners['orbat_role_ids'] == []
    assert admins['orbat_role_ids'] == ['7', '8']
    may = cp.may_edit_orbats
    assert may(admins, is_admin=True, may_plan=False)
    assert may(planners, is_admin=False, may_plan=True)
    assert not may(admins, is_admin=False, may_plan=True)
    assert may(admins, is_admin=False, may_plan=False, role_ids=[8])
    assert not may(planners, is_admin=False, may_plan=False, role_ids=[9])


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


# --- missions, phases and the library (milestone 2) --------------------------

def test_carry_over_copies_only_stable_filled_fields():
    previous = full_mission(modpack='', zeus_only='x', recog_img=4, unit_img=3)
    carried = cp.carry_over(previous)
    assert carried['tz'] == 'Europe/Berlin' and carried['unit_img'] == 3
    assert 'name' not in carried and 'hook' not in carried
    assert 'modpack' not in carried and 'zeus_only' not in carried
    assert 'recog_img' not in carried
    assert cp.carry_over(None) == {}


def test_clean_fields_shapes_and_caps():
    clean = cp.clean_fields({
        'name': 'x' * 999, 'bogus': 1, 'phases': [{'title': 'no'}],
        'op_date': 'not a date', 'objectives': ['A', 7, {'x': 1}, 'B'],
        'nets': [{'net': 'PLT', 'channel': '1', 'evil': 'y'}, 'junk'],
        'unit_img': '12', 'traces': [3, '3', 'x', 0, 4], 'h_hour': ' 20:15 ',
    })
    assert len(clean['name']) == cp.MAX_TEXT
    assert 'bogus' not in clean and 'phases' not in clean
    assert clean['op_date'] == ''
    assert clean['objectives'] == ['A', '7', 'B']
    assert clean['nets'] == [{'net': 'PLT', 'channel': '1'}]
    assert clean['unit_img'] == 12 and clean['traces'] == [3, 4]
    assert clean['h_hour'] == '20:15'
    assert cp.image_ids(clean) == {12, 3, 4}
    with pytest.raises(ValueError):
        cp.clean_fields(['nope'])


def test_clean_phases():
    phases = cp.clean_phases([
        {'id': '5', 'title': 'Phase 1', 'ao_id': 2, 'objectives': [
            {'role': 'primary', 'name': 'SEIZE', 'library_id': 9},
            {'role': 'boss', 'name': 'x', 'library_id': 99}]},
        {'title': 'Phase 2', 'ao_id': 77}, 'junk',
    ], ao_ids=[2], library_ids=[9])
    assert phases[0]['id'] == 5 and phases[0]['ao_id'] == 2
    assert phases[0]['objectives'][0]['library_id'] == 9
    assert phases[0]['objectives'][1]['role'] == 'primary'
    assert phases[0]['objectives'][1]['library_id'] is None
    assert phases[1]['ao_id'] is None and phases[1]['id'] is None
    with pytest.raises(ValueError):
        cp.clean_phases([{}] * (cp.MAX_PHASES + 1))


def test_phase_with_objectives_counts_as_filled_only_with_text():
    assert not cp.filled([{'title': '', 'objectives': [{'name': 'x'}], 'ao_id': 3}])
    assert cp.filled([{'title': 'Phase 1', 'objectives': []}])


def _phased(*objectives, **extra):
    return full_mission(phases=[{'title': 'Phase 1 – Assault', 'detail': 'Go',
                                 'objectives': list(objectives)}], **extra)


def test_success_line_falls_back_to_primary_conditions():
    mission = _phased({'role': 'primary', 'name': 'SEIZE', 'complete_when': 'Hill held'},
                      {'role': 'secondary', 'name': 'CLEAR', 'complete_when': 'Nope'})
    body = cp.build_post(mission, 'opord', ctx())
    assert '**Success:** Hill held' in body and 'Nope' not in body
    body = cp.build_post({**mission, 'end_state': 'Typed'}, 'opord', ctx())
    assert '**Success:** Typed' in body


def test_phase_role_checks():
    found = _texts(cp.checks(_phased({'role': 'primary', 'name': 'SEIZE'})))
    assert 'no secondary or complication objective' in found
    found = _texts(cp.checks(_phased({'role': 'secondary', 'name': 'CLEAR'},
                                     {'role': 'complication', 'name': 'IEDs'})))
    assert 'has 0 primary objectives' in found
    found = _texts(cp.checks(_phased({'role': 'primary', 'name': 'SEIZE'},
                                     {'role': 'secondary', 'name': 'CLEAR'},
                                     {'role': 'complication', 'name': 'IEDs'})))
    assert 'primary objectives' not in found and 'objective yet' not in found


def test_objective_names_in_phases_count_for_the_orders_check():
    mission = _phased({'role': 'primary', 'name': 'SEIZE HOCHABRUNN'},
                      objectives=['KRITZENAST', 'HOCHABRUNN'])
    assert 'HOCHABRUNN' not in _texts(cp.checks(mission))


def test_progress_and_locked_count():
    mission = full_mission()
    assert cp.locked_count(mission) == (5, 5)
    assert cp.locked_count({}) == (0, 5)
    chips = cp.progress({})
    assert [c['key'] for c in chips] == [s.key for s in cp.STEPS]


def test_next_due():
    items = cp.schedule(full_mission(hook=''), SETTINGS, today=date(2026, 10, 9))
    assert cp.next_due(items, date(2026, 10, 9))['key'] == 'concept'
    items = cp.schedule(full_mission(), SETTINGS, today=date(2026, 10, 30))
    assert cp.next_due(items, date(2026, 10, 30))['key'] == 'opord'


def test_library_seed_is_complete():
    seed = cp.library_seed()
    assert len(seed) == 69
    names = {e['name'] for e in seed}
    assert {'SEIZE', 'DEFEND', 'CAPTURE HVT', 'Armoured counter-attack'} <= names
    complications = [e for e in seed if e['kind'] == 'complication']
    assert len(complications) == 12 and all(e['intsum_hint'] for e in complications)
    assert all(e['complete_when'] for e in seed if e['kind'] == 'objective')
    assert [e['position'] for e in seed] == list(range(1, 70))


def test_pairings():
    seed = [dict(e, id=i) for i, e in enumerate(cp.library_seed(), 1)]
    by_name = {e['name']: e['id'] for e in seed}
    pairs = cp.pairing_ids(seed)
    named = lambda name: {n for n, i in by_name.items() if i in pairs[by_name[name]]}
    assert {'COUNTER-ATTACK', 'DEFEND'} <= named('SEIZE')
    assert 'BATTLE DAMAGE ASSESSMENT' in named('RECOVER INTEL')       # "BDA"
    assert 'OBSERVATION POST' in named('KILL HVT')                    # "OP"
    assert 'IEDs and mines' in named('CONVOY / ESCORT')               # "IED"
    assert 'VEHICLE CHECKPOINT (VCP)' in named('ROUTE CLEARANCE')     # "VCP"
    assert by_name['SEIZE'] not in pairs[by_name['SEIZE']]


def test_clean_library_entry_and_campaign():
    entry = cp.clean_library_entry({'name': ' SEIZE ', 'kind': 'weird', 'category': ''})
    assert entry['name'] == 'SEIZE' and entry['kind'] == 'objective'
    assert entry['category'] == 'Other'
    with pytest.raises(ValueError):
        cp.clean_library_entry({'name': ' '})
    campaign = cp.clean_campaign({'name': 'Iron Curtain', 'planned_missions': '40',
                                  'status': 'nope'})
    assert campaign['planned_missions'] == 8 and campaign['status'] == 'active'
    with pytest.raises(ValueError):
        cp.clean_campaign({'name': ''})


def test_image_type():
    assert cp.image_type(b'\x89PNG\r\n\x1a\n....') == 'image/png'
    assert cp.image_type(b'\xff\xd8\xff\xe0') == 'image/jpeg'
    assert cp.image_type(b'RIFF\x00\x00\x00\x00WEBPVP8 ') == 'image/webp'
    assert cp.image_type(b'<svg xmlns=...') is None
    assert cp.check_image(b'GIF89a...') == 'image/gif'
    for bad in (b'', b'<html>', b'\x89PNG\r\n\x1a\n' + b'0' * cp.MAX_IMAGE_BYTES):
        with pytest.raises(ValueError):
            cp.check_image(bad)


# --- briefing packs (milestone 3) ---------------------------------------------

def _all_text(blocks):
    out = []
    for b in blocks:
        out.append(b.get('text', ''))
        for row in b.get('rows', []):
            out.extend(row)
        out.extend(b.get('items', []))
        out.append(b.get('caption', ''))
    return '\n'.join(out)


def _packed(**extra):
    return full_mission(
        zeus_only='SECRET AMBUSH', frago_no='001', unit_img=1, map_img=2, traces=[5, 6],
        aar_imgs=[9], stats='12 kills',
        phases=[{'title': 'Phase 1 – Assault', 'detail': 'Go', 'ao_id': 3, 'objectives': [
            {'role': 'primary', 'name': 'SEIZE', 'complete_when': 'Hill held'},
            {'role': 'complication', 'name': 'IEDs and mines'}]}],
        **extra)


def test_packs_grow_player_leaders_full():
    mission = _packed()
    orbat = {'name': 'Platoon', 'squads': [{'name': '1-1 Alpha', 'reserved_unit': 'TFP',
                                            'radio': '343 ch 3', 'slots': [{'role_name': 'SL'}]}],
             'nets': []}
    player = cp.document_blocks(mission, 'player', ctx(), orbat=orbat, aos={3: 'Cham'})
    leaders = cp.document_blocks(mission, 'leaders', ctx(), orbat=orbat, aos={3: 'Cham'})
    full = cp.document_blocks(mission, 'full', ctx(), orbat=orbat, aos={3: 'Cham'},
                              today=date(2026, 10, 1))
    heads = lambda bs: [b['text'] for b in bs if b['t'] == 'h1']
    assert heads(player) == ['1. Warning order', '2. Situation', '3. Intelligence',
                             '4. Operation order']
    assert heads(leaders)[-1] == '5. FragO 001'
    assert heads(full)[-2:] == ['6. Back-schedule', '7. After action']
    assert 'ORBAT — Platoon' not in _all_text(player) and 'ORBAT — Platoon' in _all_text(leaders)
    assert cp.block_images(player) == [1, 2]
    assert cp.block_images(leaders) == [1, 2, 5, 6]
    assert cp.block_images(full) == [1, 2, 5, 6, 9]
    phases = next(b for b in player if b['t'] == 'table' and b['head'][0] == '#')
    assert phases['rows'][0][2] == 'Cham' 


def test_packs_never_carry_zeus_notes_and_only_full_has_complications():
    mission = _packed()
    for pack in cp.PACK:
        body = _all_text(cp.document_blocks(mission, pack, ctx(), today=date(2026, 10, 1)))
        assert 'SECRET AMBUSH' not in body
        assert ('IEDs and mines' in body) == (pack == 'full')
        assert 'Primary: SEIZE — complete when hill held' in body


def test_pack_times_are_in_the_op_zone_with_the_guild_zone_beside():
    mission = _packed()          # Europe/Berlin op, guild Europe/London
    blocks = cp.document_blocks(mission, 'leaders', ctx())
    timings = next(b for b in blocks if b['t'] == 'table' and b['head'][0] == 'Event')
    assert timings['head'] == ['Event', 'Time (CET)', 'Time (GMT)']
    assert timings['rows'][0] == ['H-Hour', '20:15', '19:15']
    op_night = dict(next(b for b in blocks if b['t'] == 'kv')['rows'])['Op night']
    assert op_night == 'Saturday 14 November 2026 · orders 19:30 CET · H-Hour 20:15 CET'
    same = cp.document_blocks(_packed(tz='Europe/London'), 'leaders', ctx())
    assert next(b for b in same if b['t'] == 'table' and b['head'][0] == 'Event')['head'] == \
        ['Event', 'Time (GMT)']


def test_pack_from_an_empty_mission_and_unknown_pack():
    assert cp.document_blocks({}, 'full', ctx(), today=date(2026, 1, 1))
    with pytest.raises(ValueError):
        cp.document_blocks({}, 'secret', ctx())


def test_export_filename():
    assert cp.export_filename({'name': 'Iron Gate / 2'}, 'leaders', 'pdf') == \
        'Iron_Gate_2_Leaders_Pack.pdf'
    assert cp.export_filename({}, 'full', 'docx') == 'op_Op_File.docx'


# -- drop state (milestone 4) -------------------------------------------------

from datetime import timedelta  # noqa: E402

DUE = datetime(2026, 10, 21, 17, 0, tzinfo=timezone.utc)
NAIVE_DUE = DUE.replace(tzinfo=None)


def state(row, *, h='abc', due=DUE, now=None, channel='1'):
    return cp.drop_state(row, current_hash=h, due=due,
                         now=now or DUE - timedelta(days=1), channel=channel)


def test_nothing_decided_is_unapproved_then_held_and_pinged_once():
    assert state(None)['key'] == 'unapproved'
    held = state(None, now=DUE + timedelta(minutes=1))
    assert (held['key'], held['action']) == ('held', 'ping')
    pinged = state({'held_pinged_at': DUE}, now=DUE + timedelta(minutes=2))
    assert (pinged['key'], pinged['action']) == ('held', None)


def test_an_old_hold_shows_but_does_not_ping():
    late = state(None, now=DUE + cp.HOLD_PING_WINDOW + timedelta(minutes=1))
    assert (late['key'], late['action']) == ('held', None)


def test_an_approval_is_for_one_version_at_one_time():
    row = {'approved_hash': 'abc', 'due_at': NAIVE_DUE}
    assert state(row)['key'] == 'approved'
    assert state(row, h='xyz')['key'] == 'reapprove'
    moved = state(row, due=DUE + timedelta(days=7))
    assert moved['key'] == 'reapprove'          # op date moved: approve again
    due = state(row, now=DUE)
    assert (due['key'], due['action']) == ('posting', 'post')
    changed = state(row, h='xyz', now=DUE + timedelta(minutes=1))
    assert (changed['key'], changed['action']) == ('heldchange', 'ping')


def test_nowhere_to_post_is_said_rather_than_posted():
    row = {'approved_hash': 'abc', 'due_at': NAIVE_DUE}
    out = state(row, now=DUE, channel=None)
    assert (out['key'], out['action']) == ('nochannel', None)


def test_after_posting_only_an_approved_change_edits():
    row = {'approved_hash': 'abc', 'posted_hash': 'abc', 'posted_at': NAIVE_DUE,
           'due_at': NAIVE_DUE}
    assert state(row, now=DUE + timedelta(days=9))['key'] == 'posted'
    assert state(row, h='xyz')['key'] == 'changed'
    assert state(row, h='xyz')['action'] is None
    edit = state({**row, 'approved_hash': 'xyz'}, h='xyz', due=DUE + timedelta(days=7))
    assert (edit['key'], edit['action']) == ('updating', 'edit')


def test_a_skipped_drop_stays_skipped():
    out = state({'skipped': 1, 'posted_at': NAIVE_DUE}, now=DUE + timedelta(hours=1))
    assert (out['key'], out['action'], out['posted']) == ('skipped', None, False)


def test_no_date_no_state():
    assert state(None, due=None)['key'] == 'nodate'


def test_test_campaign_drops_go_to_the_team_channel_only():
    plan = {**SETTINGS, 'drop_channel_id': '601', 'team_channel_id': '602'}
    assert cp.drop_channel(plan, False) == '601'
    assert cp.drop_channel(plan, True) == '602'
    assert cp.drop_channel({**plan, 'team_channel_id': None}, True) is None


def test_operation_running():
    now = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
    assert not cp.operation_running(None, now)
    assert cp.operation_running({'event_time': None}, now)
    assert cp.operation_running({'event_time': datetime(2026, 10, 24, 18)}, now)
    assert cp.operation_running({'event_time': datetime(2026, 10, 21, 8)}, now)
    assert not cp.operation_running({'event_time': datetime(2026, 10, 20, 18)}, now)


def test_h_hour_is_naive_utc_in_the_mission_zone():
    m = full_mission()            # 20:15 Berlin, 14 Nov (CET, UTC+1)
    assert cp.h_hour_utc(m, SETTINGS) == datetime(2026, 11, 14, 19, 15)
    assert cp.h_hour_utc(full_mission(h_hour=''), SETTINGS) is None


def test_image_groups():
    mb = 1024 * 1024
    assert cp.image_groups([], limit_bytes=10 * mb) == []
    assert cp.image_groups([mb] * 12, limit_bytes=100 * mb) == [list(range(10)), [10, 11]]
    assert cp.image_groups([6 * mb, 6 * mb, mb], limit_bytes=10 * mb) == [[0], [1, 2]]
    assert cp.image_groups([20 * mb], limit_bytes=10 * mb) == [[0]]


# -- maps (milestone 5) -------------------------------------------------------

from utils import tacmap  # noqa: E402


def _layer_ids(doc):
    return [l['id'] for l in doc['layers']]


def test_campaign_map_gets_a_layer_and_outline_per_ao():
    doc, notes = cp.sync_campaign_map(tacmap.blank_doc(), [{'id': 3, 'name': 'North'},
                                                           {'id': 4, 'name': 'South'}])
    assert _layer_ids(doc) == ['plan', 'cpao3', 'cpao4'] and notes == []
    outlines = [i for i in doc['items'] if i['layer'] == 'cpao3']
    assert len(outlines) == 1 and outlines[0]['kind'] == 'area' and outlines[0]['label'] == 'North'
    assert tacmap.parse(doc).ok                       # what we write is a valid map


def test_campaign_map_follows_renames_and_deletes_but_keeps_user_edits():
    doc, _ = cp.sync_campaign_map(tacmap.blank_doc(), [{'id': 3, 'name': 'North'},
                                                       {'id': 4, 'name': 'South'}])
    doc['items'].append({'kind': 'text', 'layer': 'plan', 'x': 1, 'y': 1, 'label': 'mine'})
    doc['items'].append({'kind': 'text', 'layer': 'cpao3', 'x': 5, 'y': 5, 'label': 'Bridge'})
    doc, _ = cp.sync_campaign_map(doc, [{'id': 3, 'name': 'Northern AO'}])
    assert _layer_ids(doc) == ['plan', 'cpao3']
    labels = {i['label'] for i in doc['items']}
    assert 'Northern AO' in labels and 'Bridge' in labels and 'mine' in labels
    assert 'South' not in labels and 'North' not in labels


def test_campaign_map_respects_the_layer_limit():
    aos = [{'id': n, 'name': f'AO {n}'} for n in range(1, 15)]
    doc, notes = cp.sync_campaign_map(tacmap.blank_doc(), aos)
    assert len(doc['layers']) == tacmap.MAX_LAYERS and len(notes) == 14 - (tacmap.MAX_LAYERS - 1)


def test_ao_map_copies_terrain_and_outline():
    camp, _ = cp.sync_campaign_map(tacmap.blank_doc(), [{'id': 3, 'name': 'North'}])
    camp['background'].update(kind='tiles', url='/t/7', zoom=3)
    doc = cp.ao_map_doc(camp, 3, 'North')
    assert doc['background']['url'] == '/t/7' and _layer_ids(doc) == ['plan', 'cpao3']
    assert [i['label'] for i in doc['items']] == ['North']
    assert _layer_ids(cp.ao_map_doc(None, 3, 'North')) == ['plan']


def test_phase_layers_follow_the_phases():
    doc = tacmap.blank_doc()
    doc, _ = cp.sync_phase_layers(doc, [{'id': 1, 'name': 'Op A · P1 Assault'},
                                        {'id': 2, 'name': 'Op A · P2 Hold'}])
    assert _layer_ids(doc) == ['plan', 'cpp1', 'cpp2']
    doc['items'].append({'kind': 'text', 'layer': 'cpp1', 'x': 1, 'y': 1, 'label': 'x'})
    doc, notes = cp.sync_phase_layers(doc, [{'id': 2, 'name': 'Op A · P1 Hold'},
                                            {'id': 9, 'name': 'Op B · P1 Raid'}])
    assert _layer_ids(doc) == ['plan', 'cpp1', 'cpp2', 'cpp9']   # cpp1 kept: drawn on
    assert any('Kept' in n for n in notes)
    assert doc['layers'][2]['name'] == 'Op A · P1 Hold'
    doc['items'] = []
    doc, notes = cp.sync_phase_layers(doc, [{'id': 9, 'name': 'Op B · P1 Raid'}])
    assert _layer_ids(doc) == ['plan', 'cpp9'] and notes == []   # empty orphans go


def test_phase_layers_respect_the_limit():
    phases = [{'id': n, 'name': f'P{n}'} for n in range(1, 20)]
    doc, notes = cp.sync_phase_layers(tacmap.blank_doc(), phases)
    assert len(doc['layers']) == tacmap.MAX_LAYERS and any('No room' in n for n in notes)


def test_phase_layer_name_fits():
    assert cp.phase_layer_name('Grenzfest', 2, 'Seize the bridge') == 'Grenzfest · P2 Seize the bridge'
    long = cp.phase_layer_name('A very long mission name indeed', 3, 'Exploit to the river line')
    assert len(long) <= tacmap.MAX_LAYER_NAME and long.endswith('P3 Exploit to the river line')


def test_trace_shows_one_phase_and_the_shared_layers():
    doc = tacmap.blank_doc()
    doc['layers'] += [{'id': 'cpp1', 'name': 'P1', 'visible': False},
                      {'id': 'cpp2', 'name': 'P2', 'visible': True},
                      {'id': 'enemy', 'name': 'Enemy', 'visible': True}]
    seen = {l['id']: l['visible'] for l in cp.trace_doc(doc, 'cpp1')['layers']}
    assert seen == {'plan': True, 'cpp1': True, 'cpp2': False, 'enemy': True}


def test_trace_view_crops_around_the_phase():
    doc = tacmap.blank_doc()                    # 1000 x 1000
    assert cp.trace_view(doc, 'cpp1') is None
    doc['items'] = [{'kind': 'line', 'layer': 'cpp1', 'points': [[400, 400], [500, 450]]}]
    x, y, w, h = cp.trace_view(doc, 'cpp1')
    assert round(w / h, 2) == cp.TRACE_ASPECT
    assert x <= 400 and x + w >= 500 and y <= 400 and y + h >= 450
    assert w >= 1000 * cp.TRACE_MIN_FRACTION and 0 <= x and x + w <= 1000
    doc['items'].append({'kind': 'area', 'layer': 'cpao3',
                         'points': [[0, 0], [990, 0], [990, 990], [0, 990]]})
    x, y, w, h = cp.trace_view(doc, 'cpp1')    # the AO outline widens it
    assert w == 1000 and 0 <= y and y + h <= 1000


def test_trace_zoom_is_sharp_but_never_deeper_than_the_terrain():
    doc = tacmap.blank_doc()
    doc['background']['max_zoom'] = 5
    assert cp.trace_zoom(doc, (0, 0, 1000, 667)) == 3       # 1600 px over 256-px tiles
    assert cp.trace_zoom(doc, (0, 0, 150, 100)) == 5        # capped
    doc['background']['max_zoom'] = 2
    assert cp.trace_zoom(doc, (0, 0, 150, 100)) == 2


def test_render_can_crop_and_swap_tile_addresses():
    doc = tacmap.blank_doc()
    doc['background'].update(kind='tiles', url='/t/7', zoom=2)
    svg = tacmap.render(doc, view=(0, 0, 400, 300), tile_zoom=2,
                        tile_href=lambda z, x, y: f'data:{z}-{x}-{y}', size=(800, 600))
    assert 'viewBox="0 0 400 300"' in svg and 'width="800"' in svg
    assert 'data:2-0-0' in svg and 'data:2-1-1' in svg and 'data:2-2-0' not in svg
    assert tacmap.visible_tiles(doc, 1) == [(0, 0), (0, 1), (1, 0), (1, 1)]
