"""`utils/killtracker.py` — one OCAP recording in, the Kill Tracker's rows out.

The recordings here are built by hand and are tiny, so every number in an
assertion can be checked against the document above it. What they pin:

* the counting rules, above all the ones that have been wrong before — crew kills
  and distance, end-of-mission teamkills, a crash that looks like a suicide;
* that the **streaming reader and the in-memory path agree**, down to a buffer one
  byte wide — which is what lets a 90 MB recording be read without ever being
  held whole;
* the JavaScript rounding the sheet's numbers were made with.

The port was also checked cell by cell against the original Apps Script on three
real recordings (0 differences); that needs the recordings, so it is not here.
"""

import gzip
import io
import json

import pytest

from utils import killtracker as kt

H = {name: i for i, name in enumerate(kt.HEADER)}


def pos(x=0, y=0, alive=1, veh=0, name=None, role=None):
    """One `positions` entry in the OCAP v5 layout."""
    return [[x, y, 0], 0, alive, veh, name or '', 1, role or '', '', '']


def unit(id_, name, side='WEST', *, player=1, group='Alpha', role='Rifleman',
         positions=None, fired=None):
    return {
        'id': id_, 'name': name, 'side': side, 'isPlayer': player, 'type': 'unit',
        'group': group, 'role': role, 'startFrameNum': 0,
        'positions': positions if positions is not None else [pos() for _ in range(10)],
        'framesFired': fired or [],
    }


def vehicle(id_, name='Mi-8', side='EAST'):
    return {'id': id_, 'name': name, 'side': side, 'isPlayer': 0, 'type': 'vehicle',
            'positions': []}


def doc(entities, events, *, end=1000, delay=1, mission='Test Op', world='altis'):
    return {'missionName': mission, 'worldName': world, 'captureDelay': delay,
            'endFrame': end, 'entities': entities, 'events': events}


def rows_of(document, source='2026_03_14__20_27_Test.json.gz'):
    return kt.compute(kt.reduce_document(document), source)


def row(result, name):
    return next(r for r in result['rows'] if r[H['Username']] == name)


def killed(frame, victim, killer, weapon='M4', dist=100):
    return [frame, 'killed', victim, [killer, weapon], dist]


# ---------------------------------------------------------------------------
# The columns
# ---------------------------------------------------------------------------

def test_header_and_rows_have_the_same_width():
    result = rows_of(doc([unit(1, 'Ann'), unit(2, 'Bob', 'EAST', player=0, group='x')],
                         [killed(5, 2, 1)]))
    assert len(kt.HEADER) == 35
    assert all(len(r) == len(kt.HEADER) for r in result['rows'])


def test_a_plain_kill_fills_the_infantry_columns():
    ann, bob = unit(1, 'Ann'), unit(2, 'Bob', 'EAST', player=0)
    result = rows_of(doc([ann, bob], [killed(5, 2, 1, 'M4', 120)]))
    r = row(result, 'Ann')
    assert r[H['Kills (On Foot)']] == 1
    assert r[H['K/D (On Foot)']] == 1
    assert r[H['Longest Kill On Foot (m)']] == 120
    assert r[H['Avg Kill Dist On Foot (m)']] == 120
    assert r[H['Top Weapon']] == 'M4'
    assert json.loads(r[H['Weapon Kills (JSON)']]) == {'M4': 1}
    assert result['kills'] == 1


def test_the_mission_is_labelled_with_the_date_from_the_file_name():
    assert kt.mission_label('Op', '2026_03_14__20_27_Op.json.gz') == 'Op (2026-03-14)'
    assert kt.mission_label('Op', 'Op_20260530_222136.json.gz') == 'Op (2026-05-30)'
    assert kt.mission_label('Op', 'Op.json.gz') == 'Op'


def test_deaths_and_kd():
    ann, bob = unit(1, 'Ann'), unit(2, 'Bob', 'EAST')
    ev = [killed(5, 2, 1), killed(6, 2, 1), killed(7, 1, 2)]
    result = rows_of(doc([ann, bob], ev))
    r = row(result, 'Ann')
    assert (r[H['Kills (On Foot)']], r[H['Deaths (On Foot)']], r[H['K/D (On Foot)']]) == (2, 1, 2)
    # Bob killed Ann once and died twice
    assert row(result, 'Bob')[H['K/D (On Foot)']] == 0.5


def test_rows_come_out_sorted_by_on_foot_kills():
    ann, bob, cy = unit(1, 'Ann'), unit(2, 'Bob'), unit(3, 'Cy', 'EAST', player=0)
    ev = [killed(5, 3, 2), killed(6, 3, 2)]
    result = rows_of(doc([ann, bob, cy], ev))
    assert [r[H['Username']] for r in result['rows']] == ['Bob', 'Ann']


# ---------------------------------------------------------------------------
# Crew kills and distance — the rule that was wrong
# ---------------------------------------------------------------------------

def heli_doc(*, crew_in_vehicle=True, same_frame=True, weapon='FIM-92F', dist=1944):
    crew_pos = [pos(veh=7) for _ in range(10)] if crew_in_vehicle else [pos() for _ in range(10)]
    ent = [unit(1, 'Ann'), vehicle(7),
           unit(8, 'Pilot', 'EAST', player=0, positions=crew_pos)]
    ev = [killed(5, 8, 1, weapon, dist)]
    if same_frame:
        ev.insert(0, killed(5, 7, 1, weapon, dist))
    return doc(ent, ev)


def test_crew_of_a_shot_down_helicopter_counts_as_a_kill_but_not_as_distance():
    r = row(rows_of(heli_doc()), 'Ann')
    assert r[H['Kills (On Foot)']] == 1          # the pilot is a kill…
    assert r[H['Vehicle Kills (On Foot)']] == 1  # …and so is the helicopter
    assert r[H['Longest Kill On Foot (m)']] == ''  # …but 1944 m is not a kill distance
    assert r[H['Avg Kill Dist On Foot (m)']] == ''


def test_a_victim_sitting_in_a_vehicle_is_crew_even_when_the_vehicle_dies_later():
    """The Mi-8T case: the Stinger killed a passenger, the heli went down a while after."""
    r = row(rows_of(heli_doc(same_frame=False)), 'Ann')
    assert r[H['Kills (On Foot)']] == 1
    assert r[H['Longest Kill On Foot (m)']] == ''


def test_the_same_frame_alone_is_enough_even_if_the_crew_is_on_foot():
    r = row(rows_of(heli_doc(crew_in_vehicle=False)), 'Ann')
    assert r[H['Longest Kill On Foot (m)']] == ''


def test_a_real_long_shot_still_counts_for_distance():
    r = row(rows_of(heli_doc(crew_in_vehicle=False, same_frame=False, dist=900)), 'Ann')
    assert r[H['Longest Kill On Foot (m)']] == 900


def test_an_implausible_on_foot_distance_is_a_kill_without_a_distance():
    ann, bob = unit(1, 'Ann'), unit(2, 'Bob', 'EAST', player=0)
    r = row(rows_of(doc([ann, bob], [killed(5, 2, 1, 'M4', 1600)])), 'Ann')
    assert r[H['Kills (On Foot)']] == 1
    assert r[H['Longest Kill On Foot (m)']] == ''
    assert row(rows_of(doc([ann, bob], [killed(5, 2, 1, 'M4', 1500)])), 'Ann')[
        H['Longest Kill On Foot (m)']] == 1500


def test_a_killer_in_a_vehicle_is_counted_in_the_vehicle_columns():
    ann = unit(1, 'Ann', positions=[pos(veh=7) for _ in range(10)])
    bob = unit(2, 'Bob', 'EAST', player=0)
    r = row(rows_of(doc([ann, vehicle(7, side='WEST'), bob], [killed(5, 2, 1, 'M2', 400)])), 'Ann')
    assert r[H['Kills (In Vehicle)']] == 1
    assert r[H['Longest Kill In Vehicle (m)']] == 400
    assert r[H['Kills (On Foot)']] == 0


def test_a_friendly_vehicle_is_not_a_vehicle_kill():
    ann = unit(1, 'Ann')
    r = row(rows_of(doc([ann, vehicle(7, side='WEST')], [killed(5, 7, 1)])), 'Ann')
    assert r[H['Vehicle Kills (On Foot)']] == 0


def test_the_vehicle_kill_list_names_vehicle_and_weapon():
    r = row(rows_of(heli_doc()), 'Ann')
    assert json.loads(r[H['Vehicle Kills (JSON)']]) == [{'v': 'Mi-8', 'w': 'FIM-92F'}]


# ---------------------------------------------------------------------------
# Teamkills, suicides
# ---------------------------------------------------------------------------

def test_a_teamkill_counts_in_the_middle_of_a_mission():
    ann, bob = unit(1, 'Ann'), unit(2, 'Bob')
    r = row(rows_of(doc([ann, bob], [killed(100, 2, 1)], end=2000)), 'Ann')
    assert r[H['Teamkills (On Foot)']] == 1
    assert r[H['Kills (On Foot)']] == 0


def test_end_of_mission_teamkills_are_not_counted():
    ann, bob = unit(1, 'Ann'), unit(2, 'Bob')
    window = kt.EOM_WINDOW_S
    assert row(rows_of(doc([ann, bob], [killed(2000 - window, 2, 1)], end=2000)), 'Ann')[
        H['Teamkills (On Foot)']] == 0
    assert row(rows_of(doc([ann, bob], [killed(2000 - window - 1, 2, 1, dist=50)], end=2000)), 'Ann')[
        H['Teamkills (On Foot)']] == 1
    # close range counts a little earlier than the hard window
    assert row(rows_of(doc([ann, bob], [killed(2000 - window - 10, 2, 1, dist=5)], end=2000)), 'Ann')[
        H['Teamkills (On Foot)']] == 0


def test_a_burst_of_teamkills_near_the_end_is_celebration():
    ann = unit(1, 'Ann')
    others = [unit(10 + i, f'P{i}') for i in range(3)]
    end = 3000
    base = end - 500  # past the hard window, inside the burst window
    burst = [killed(base + i, 10 + i, 1, dist=80) for i in range(3)]
    r = row(rows_of(doc([ann] + others, burst, end=end)), 'Ann')
    assert r[H['Teamkills (On Foot)']] == 0
    # …but the same three kills in mid-mission are three teamkills
    mid = [killed(500 + i, 10 + i, 1, dist=80) for i in range(3)]
    assert row(rows_of(doc([ann] + others, mid, end=end)), 'Ann')[H['Teamkills (On Foot)']] == 3


def test_an_endmission_event_overrides_the_recorded_end_frame():
    ann, bob = unit(1, 'Ann'), unit(2, 'Bob')
    ev = [killed(900, 2, 1), [1000, 'endMission', 0, None]]
    # end_frame says 5000, but the mission ended at 1000, so 900 is inside the window
    assert row(rows_of(doc([ann, bob], ev, end=5000)), 'Ann')[H['Teamkills (On Foot)']] == 0


def test_a_suicide_counts_unless_the_player_disconnected():
    ann = unit(1, 'Ann')
    ev = [[50, 'killed', 1, [1, 'Unknown'], 0]]
    assert row(rows_of(doc([ann], ev)), 'Ann')[H['Suicides']] == 1
    # a crash logs a self-kill within a frame of the disconnect — looked up by name
    for offset in (-1, 0, 1):
        dc = ev + [[50 + offset, 'disconnected', 'Ann']]
        assert row(rows_of(doc([ann], dc)), 'Ann')[H['Suicides']] == 0
    far = ev + [[53, 'disconnected', 'Ann']]
    assert row(rows_of(doc([ann], far)), 'Ann')[H['Suicides']] == 1


# ---------------------------------------------------------------------------
# Shots, hits
# ---------------------------------------------------------------------------

def test_duplicate_shots_and_phantom_shots_are_not_counted():
    fired = [
        [3, [10, 10, 0]],
        [3, [10, 10, 0]],      # the same shot logged twice
        [3, [20, 10, 0]],      # a different target in the same frame is a second bullet
        [4, [5000, 5000, 0]],  # a phantom: thousands of metres from the shooter
    ]
    r = row(rows_of(doc([unit(1, 'Ann', fired=fired)], [])), 'Ann')
    assert r[H['Shots (On Foot)']] == 2


def test_shots_from_a_vehicle_are_counted_there_and_never_filtered_by_distance():
    ann = unit(1, 'Ann', positions=[pos(veh=7) for _ in range(10)],
               fired=[[3, [5000, 5000, 0]], [4, [1, 1, 0]]])
    r = row(rows_of(doc([ann], [])), 'Ann')
    assert r[H['Shots (In Vehicle)']] == 2
    assert r[H['Shots (On Foot)']] == 0


def test_hits_are_deduplicated_per_shooter_victim_and_frame():
    ann, bob = unit(1, 'Ann'), unit(2, 'Bob', 'EAST')
    ev = [[5, 'hit', 2, [1, 'M4'], 10]] * 4 + [[6, 'hit', 2, [1, 'M4'], 10]]
    result = rows_of(doc([ann, bob], ev))
    assert row(result, 'Ann')[H['Hits Dealt (On Foot)']] == 2
    assert row(result, 'Bob')[H['Hits Taken (On Foot)']] == 2


def test_shots_per_kill_uses_javascripts_rounding():
    # 5 shots over 2 kills = 2.5, which toFixed(1) keeps; 1/8 = 0.125 -> toFixed(2) is 0.13
    assert kt.to_fixed(0.125, 2) == 0.13       # Python's round() would say 0.12
    assert kt.to_fixed(2.5, 0) == 3.0          # …and 2
    assert kt.to_fixed(1.005, 2) == 1.0        # the float is just below, as in JS
    assert kt.js_round(2.5) == 3 and kt.js_round(-2.5) == -2


# ---------------------------------------------------------------------------
# Who is a player
# ---------------------------------------------------------------------------

def test_ai_are_not_players_and_zeus_has_no_combat_stats():
    ann = unit(1, 'Ann')
    ai = unit(2, 'Ivan', 'EAST', player=0)
    zeus = unit(3, 'Zed', group='Zeus')
    ev = [killed(5, 2, 1), killed(6, 2, 3)]
    result = rows_of(doc([ann, ai, zeus], ev))
    assert {r[H['Username']] for r in result['rows']} == {'Ann', 'Zed'}
    assert row(result, 'Zed')[H['Kills (On Foot)']] == 0
    assert result['kills'] == 1


def test_zeus_is_found_by_role_too():
    zeus = unit(3, 'Zed', role='Co Zeus')
    assert row(rows_of(doc([zeus, unit(4, 'X', 'EAST', player=0)], [killed(5, 4, 3)])), 'Zed')[
        H['Kills (On Foot)']] == 0


def test_a_missing_name_is_read_from_the_per_frame_data():
    ent = unit(1, '', positions=[pos(name='Ann') for _ in range(5)])
    assert row(rows_of(doc([ent], [])), 'Ann')


def test_an_unnamed_short_lived_entity_is_dropped():
    ent = unit(1, '', player=0, positions=[pos() for _ in range(5)])
    assert rows_of(doc([ent], []))['rows'] == []


def test_an_unnamed_player_takes_the_name_of_the_only_named_one_in_their_slot():
    named = unit(1, 'Ann', group='Alpha 1-1', role='Medic')
    ghost = unit(2, '', player=0, group='Alpha 1-1', role='Medic',
                 positions=[pos() for _ in range(3600)])
    result = rows_of(doc([named, ghost], []))
    assert [r[H['Username']] for r in result['rows']] == ['Ann', 'Ann']


def _walk(positions):
    return row(rows_of(doc([unit(1, 'Ann', positions=positions)], [])), 'Ann')[H['Distance Run (km)']]


def test_distance_run_is_summed_on_foot_in_kilometres():
    assert _walk([pos(x=i * 10) for i in range(101)]) == 1.0  # 100 steps of 10 m


def test_distance_run_skips_the_steps_that_touch_a_vehicle_frame():
    walk = [pos(x=i * 10) for i in range(21)]
    walk[10] = pos(x=100, veh=7)
    # 20 steps, the two touching frame 10 skipped: 18 * 10 m = 180 m
    assert _walk(walk) == 0.2


def test_distance_run_skips_teleports_and_the_dead():
    walk = [pos(x=i * 10) for i in range(11)]
    walk[5] = pos(x=9999)           # a teleport: the steps in and out are skipped
    assert _walk(walk) == 0.1       # 8 steps left = 80 m
    dead = [pos(x=i * 10) for i in range(11)]
    dead[5] = pos(x=50, alive=0)
    assert _walk(dead) == 0.1


def test_playtime_is_frames_times_capture_delay():
    ent = unit(1, 'Ann', positions=[pos() for _ in range(10)])
    assert row(rows_of(doc([ent], [], delay=3)), 'Ann')[H['Time Played (s)']] == 30


# ---------------------------------------------------------------------------
# Reading as a stream
# ---------------------------------------------------------------------------

def _big_doc():
    ent = [unit(1, 'Ann', fired=[[3, [10, 10, 0]]]), vehicle(7),
           unit(8, 'Pilot', 'EAST', player=0, positions=[pos(veh=7) for _ in range(10)]),
           unit(2, 'Bob', 'EAST', player=0)]
    ev = [killed(5, 7, 1, 'FIM-92F', 1944), killed(5, 8, 1, 'FIM-92F', 1944),
          killed(6, 2, 1, 'M4', 250), [7, 'hit', 2, [1, 'M4'], 10]]
    d = doc(ent, ev, end=20)
    d['times'] = [{'date': '2026-01-01', 'frameNum': 1, 'time': 1.5}]
    d['Markers'] = [[1, 2.5, 'a "quoted" ä name']]
    return d


def _same(a, b):
    assert a['rows'] == b['rows'] and a['mission'] == b['mission'] and a['kills'] == b['kills']


def test_the_stream_reader_agrees_with_the_in_memory_path_at_every_buffer_size():
    document = _big_doc()
    text = json.dumps(document)
    want = rows_of(document)
    for chunk in (1, 2, 3, 7, 64, 1 << 20):
        rec = kt.read_recording(io.StringIO(text), chunk=chunk)
        _same(kt.compute(rec, '2026_03_14__20_27_Test.json.gz'), want)


def test_the_stream_reader_handles_pretty_printed_json_and_any_key_order():
    document = _big_doc()
    shuffled = {k: document[k] for k in
                ['events', 'Markers', 'entities', 'times', 'endFrame', 'worldName',
                 'captureDelay', 'missionName']}
    text = json.dumps(shuffled, indent=2)
    rec = kt.read_recording(io.StringIO(text), chunk=5)
    _same(kt.compute(rec, '2026_03_14__20_27_Test.json.gz'), rows_of(document))


def test_numbers_cut_by_the_buffer_edge_are_not_misread():
    document = _big_doc()
    document['endFrame'] = 123456
    rec = kt.read_recording(io.StringIO(json.dumps(document)), chunk=1)
    assert rec.end_frame == 123456


def test_a_gzip_file_reads_from_disk(tmp_path):
    path = tmp_path / 'op.json.gz'
    path.write_bytes(gzip.compress(json.dumps(_big_doc()).encode()))
    _same(kt.compute(kt.open_recording(str(path)), '2026_03_14__20_27_Test.json.gz'),
          rows_of(_big_doc()))


def test_a_file_is_read_by_what_it_is_not_by_what_it_is_called(tmp_path):
    """A client that honours Content-Encoding stores plain JSON as `.json.gz`."""
    plain_named_gz = tmp_path / 'a.json.gz'
    plain_named_gz.write_text(json.dumps(_big_doc()))
    zipped_named_json = tmp_path / 'b.json'
    zipped_named_json.write_bytes(gzip.compress(json.dumps(_big_doc()).encode()))
    want = rows_of(_big_doc())
    for path in (plain_named_gz, zipped_named_json):
        _same(kt.compute(kt.open_recording(str(path)), '2026_03_14__20_27_Test.json.gz'), want)


def test_a_truncated_file_raises_instead_of_returning_half_a_mission():
    text = json.dumps(_big_doc())
    try:
        kt.read_recording(io.StringIO(text[: len(text) // 2]), chunk=16)
    except (ValueError, json.JSONDecodeError):
        return
    raise AssertionError('a cut-off recording must not be read as complete')


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def test_csv_has_the_sheets_header_and_whole_numbers_without_a_point():
    result = rows_of(heli_doc(crew_in_vehicle=False, same_frame=False, dist=250))
    text = kt.to_csv(result['rows'])
    lines = text.split('\n')
    assert lines[0].split(',')[:4] == ['Source File', 'Mission', 'World', 'Username']
    assert ',1.0,' not in lines[1] and ',1,' in lines[1]


def test_csv_quotes_the_json_columns_so_the_dashboards_parser_can_read_them_back():
    import csv as csvlib
    result = rows_of(heli_doc())
    text = kt.to_csv(result['rows'])
    back = list(csvlib.reader(io.StringIO(text)))
    assert back[0] == kt.HEADER
    assert json.loads(back[1][H['Vehicle Kills (JSON)']]) == [{'v': 'Mi-8', 'w': 'FIM-92F'}]


# ---------------------------------------------------------------------------
# Names and campaigns (the admin pages)
# ---------------------------------------------------------------------------

def test_a_name_keeps_the_recordings_date():
    assert kt.split_label('Operation CORONADO (2025-11-29)') == ('Operation CORONADO', ' (2025-11-29)')
    assert kt.split_label('No date') == ('No date', '')
    assert kt.effective_label('Old (2025-11-29)', 'Operation Coronado') == 'Operation Coronado (2025-11-29)'
    assert kt.effective_label('Old (2025-11-29)', None) == 'Old (2025-11-29)'
    assert kt.effective_label('Old', 'New') == 'New'


def test_a_typed_name_is_tidied_and_loses_a_typed_date():
    assert kt.clean_name('  Operation   Coronado (2025-11-29) ') == 'Operation Coronado'
    assert kt.clean_name('') == ''
    assert kt.clean_name('A\nB') == 'A B'
    with pytest.raises(ValueError):
        kt.clean_name('x' * 121)


def test_the_csv_shows_the_admins_name_in_the_mission_column():
    result = rows_of(doc([unit(1, 'Ann'), unit(2, 'Bob', 'EAST', player=0)], [killed(5, 2, 1)]))
    stored = json.dumps(result['rows'])
    plain = kt.csv_from_stored([(stored, None)])
    renamed = kt.csv_from_stored([(stored, 'Clean Name')])
    assert 'Test Op (2026-03-14)' in plain and 'Clean Name (2026-03-14)' in renamed
    assert 'Test Op' not in renamed
    assert result['rows'][0][1] == 'Test Op (2026-03-14)'   # the stored rows are untouched


def test_a_source_file_is_matched_without_extension_or_duplicate_marker():
    assert kt.source_key('2026_03_14__20_27_LZGulfDay08H (5).json.gz') == '2026_03_14__20_27_LZGulfDay08H'
    assert kt.source_key('A_20260101_120000.json.gz') == 'A_20260101_120000'
    assert kt.source_key('plain') == 'plain'


SHEET = (
    'Source File,Mission,World,Username\n'
    '2025_11_29__21_45_OperationCORONADO.json.gz,Operation Coronado (2025-11-29),w,Ann\n'
    '2025_11_29__21_45_OperationCORONADO.json.gz,Operation Coronado (2025-11-29),w,Bob\n'
    '2025_11_22__20_40_AShauValleyBattle.json.gz,A Shau Valley Battle (2025-11-22),w,Ann\n'
    '2025_01_01__20_00_Gone.json.gz,Gone (2025-01-01),w,Ann\n'
)


def test_the_sheets_names_are_read_once_per_recording():
    names = kt.parse_sheet_names(SHEET)
    assert names['2025_11_29__21_45_OperationCORONADO'] == 'Operation Coronado (2025-11-29)'
    assert len(names) == 3


@pytest.mark.parametrize('text', ['', 'a,b\n1,2\n'])
def test_a_file_that_is_not_the_sheet_is_refused(text):
    with pytest.raises(ValueError):
        kt.parse_sheet_names(text)


def test_only_names_that_differ_are_proposed():
    missions = [
        {'ocap_id': 33, 'filename': '2025_11_29__21_45_OperationCORONADO',
         'label': 'Operation CORONADO (2025-11-29)', 'mission': 'Operation CORONADO', 'name_override': None},
        {'ocap_id': 32, 'filename': '2025_11_22__20_40_AShauValleyBattle',
         'label': 'A Shau Valley Battle (2025-11-22)', 'mission': 'x', 'name_override': None},
        {'ocap_id': 9, 'filename': 'not_in_the_sheet', 'label': 'Z (2025-01-01)', 'mission': 'Z', 'name_override': None},
        # not counted yet: the OCAP list's name stands in for the recording's
        {'ocap_id': 8, 'filename': '2025_01_01__20_00_Gone', 'label': None, 'mission': 'Gone', 'name_override': None},
    ]
    changes = kt.plan_name_import(kt.parse_sheet_names(SHEET), missions)
    assert [(c['ocap_id'], c['ocap'], c['sheet']) for c in changes] == [
        (33, 'Operation CORONADO', 'Operation Coronado')]


def test_patterns_are_one_per_line_and_deduplicated():
    assert kt.clean_patterns(' Iron  Triangle \n\nIron Triangle\nGlacier\r\n') == ['Iron Triangle', 'Glacier']
    assert kt.clean_patterns('') == []
    with pytest.raises(ValueError):
        kt.clean_patterns('\n'.join(str(i) for i in range(51)))


def test_the_campaigns_document_follows_renames_and_keeps_unmatched_labels():
    campaigns = [
        {'name': 'Iron', 'patterns': ['Triangle'], 'extra': ['Not Counted Yet (2026-01-01)'], 'ocap_ids': [2, 1]},
        {'name': 'Empty', 'patterns': [], 'extra': [], 'ocap_ids': []},
        {'name': 'Gone', 'patterns': [], 'extra': [], 'ocap_ids': [99]},
    ]
    labels = {1: 'Renamed One (2025-10-25)', 2: 'Two (2025-11-01)'}
    assert kt.campaigns_document(campaigns, labels) == {
        'Iron': {'missions': ['Renamed One (2025-10-25)', 'Two (2025-11-01)',
                              'Not Counted Yet (2026-01-01)'], 'patterns': ['Triangle']},
        'Empty': {},
        'Gone': {},
    }


def test_the_old_campaigns_file_is_read_and_comments_skipped():
    text = json.dumps({'_comment': 'x', 'Iron': {'missions': ['A (2025-01-01)'], 'patterns': ['Tri']}, 'P': {}})
    assert kt.parse_campaigns_json(text) == [
        {'name': 'Iron', 'missions': ['A (2025-01-01)'], 'patterns': ['Tri']},
        {'name': 'P', 'missions': [], 'patterns': []}]


@pytest.mark.parametrize('text', ['nope', '[]', '{"A": 5}', '{"A": {"missions": "x"}}', '{"A": {"missions": [1]}}'])
def test_a_campaigns_file_of_the_wrong_shape_is_refused(text):
    with pytest.raises(ValueError):
        kt.parse_campaigns_json(text)


def test_labels_are_split_into_recordings_and_leftovers():
    shown = {1: 'A (2025-01-01)', 2: 'B (2025-01-02)', 3: 'A (2025-01-01)'}
    assert kt.match_missions(['A (2025-01-01)', 'Z (2025-09-09)'], shown) == ([1, 3], ['Z (2025-09-09)'])


# ---------------------------------------------------------------------------
# Players: names that belong together, and units
# ---------------------------------------------------------------------------

def test_linking_names_shows_them_as_the_one_picked():
    assert kt.link_names({}, ['Dusty', 'PSGT Dusty'], 'PSGT Dusty') == {'Dusty': 'PSGT Dusty'}


def test_linking_never_leaves_a_chain():
    # B is already shown as C; linking A to B must bring A, B and C together.
    out = kt.link_names({'B': 'C'}, ['A', 'B'], 'B')
    assert out == {'A': 'B', 'C': 'B'}
    assert all(canonical not in out for canonical in out.values())


def test_linking_leaves_other_people_alone():
    out = kt.link_names({'X': 'Y'}, ['A', 'B'], 'A')
    assert out == {'X': 'Y', 'B': 'A'}


def test_a_name_that_was_the_shown_one_can_become_an_alias():
    # Dusty and Viking are one person shown as Viking; show Dusty instead.
    out = kt.link_names({'Dusty': 'Viking', 'T.Viking': 'Viking'}, ['Dusty'], 'Dusty')
    assert out == {'Viking': 'Dusty', 'T.Viking': 'Dusty'}


def test_linking_needs_a_name_to_show():
    with pytest.raises(ValueError):
        kt.link_names({}, ['A'], '  ')


def test_unlinking_one_name_leaves_the_rest_of_the_person():
    out = kt.unlink_name({'A': 'C', 'B': 'C'}, 'A')
    assert out == {'B': 'C'}
    assert kt.alias_groups({'b': 'C', 'A': 'C', 'x': 'Y'}) == {'C': ['A', 'b'], 'Y': ['x']}


def test_known_players_are_counted_across_missions():
    def rows(label, *players):
        out = []
        for name, kills_foot, kills_veh in players:
            row = [''] * 35
            row[0], row[1], row[3], row[7], row[17] = 'f_' + label, label, name, kills_foot, kills_veh
            out.append(row)
        return json.dumps(out)

    got = kt.known_players([(rows('M1', ('Ann', 2, 1), ('Bob', 1, 0)), None),
                            (rows('M2', ('Ann', 4, 0)), 'x')])
    assert got == [{'name': 'Ann', 'missions': 2, 'kills': 7, 'last': 'M2'},
                   {'name': 'Bob', 'missions': 1, 'kills': 1, 'last': 'M1'}]


def test_unit_choices():
    assert kt.clean_unit('') is None and kt.clean_unit('auto') is None
    assert kt.clean_unit('unknown') == kt.UNKNOWN_UNIT == ''
    assert kt.clean_unit('PXG') == 'PXG'
    with pytest.raises(ValueError):
        kt.clean_unit('Nope')


def test_the_old_aliases_file_is_read_without_chains_or_comments():
    text = json.dumps({'_comment': 'x', 'A': 'B', 'B': 'C', 'D': 'D'})
    assert kt.parse_aliases_json(text) == {'A': 'C', 'B': 'C'}
    for bad in ('nope', '[]', '{"A": 5}'):
        with pytest.raises(ValueError):
            kt.parse_aliases_json(bad)


def test_the_old_unit_file_maps_null_to_unknown_and_back():
    parsed = kt.parse_unit_overrides_json(json.dumps({'_c': 'x', 'Goose': '2nd USC', 'Nobody': None}))
    assert parsed == {'Goose': '2nd USC', 'Nobody': ''}
    assert kt.unit_overrides_document(parsed) == {'Goose': '2nd USC', 'Nobody': None}
    with pytest.raises(ValueError):
        kt.parse_unit_overrides_json(json.dumps({'A': 'Marines'}))
