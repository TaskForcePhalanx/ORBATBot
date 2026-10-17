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
    assert len(kt.HEADER) == 42    # the sheet's 35, the two weapon columns of the longest kills, two for unconsciousness, one multikill, two for where it was
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


def test_unconsciousness_counts_each_time_down_and_the_frames_spent_there():
    # alive, down (2 frames), up, dead, respawned, down again (1 frame), up
    states = [1, 1, 2, 2, 1, 0, 0, 1, 2, 1]
    ent = unit(1, 'Ann', positions=[pos(alive=a) for a in states])
    r = row(rows_of(doc([ent], [], delay=2)), 'Ann')
    assert r[H['Times Unconscious']] == 2
    assert r[H['Time Unconscious (s)']] == 6        # 3 frames * capture delay 2


def test_dying_or_respawning_is_not_going_unconscious():
    ent = unit(1, 'Ann', positions=[pos(alive=a) for a in [1, 0, 0, 1, 1]])
    r = row(rows_of(doc([ent], [])), 'Ann')
    assert r[H['Times Unconscious']] == 0 and r[H['Time Unconscious (s)']] == 0


def test_unconscious_frames_are_not_distance_run():
    walk = [pos(x=i * 10) for i in range(11)]
    walk[5] = pos(x=50, alive=2)
    assert _walk(walk) == 0.1


def _multikill(frames, delay=1):
    ann = unit(1, 'Ann')
    foes = [unit(10 + i, f'Foe{i}', 'EAST', player=0) for i in range(len(frames))]
    evs = [killed(f, 10 + i, 1) for i, f in enumerate(frames)]
    return row(rows_of(doc([ann] + foes, evs, delay=delay)), 'Ann')[H['Best Multikill']]


def test_best_multikill_is_the_most_kills_inside_ten_seconds():
    assert _multikill([]) == 0
    assert _multikill([5]) == 1
    assert _multikill([5, 8, 15, 100]) == 3          # 5..15 is exactly 10 s: edges count
    assert _multikill([5, 8, 16, 100]) == 2          # 16 is 11 s after 5
    assert _multikill([5, 6, 50, 52, 53, 54]) == 4   # the later burst wins


def test_multikill_window_is_seconds_not_frames():
    assert _multikill([0, 2, 4, 6], delay=2) == 3    # 10 s = 5 frames: 0, 2, 4


def test_best_multikill_says_when_and_where_it_began():
    assert kt.best_multikill_window([], 1) == (0, None)
    assert kt.best_multikill_window([5, 6, 50, 52, 53, 54], 1) == (4, 50)
    assert kt.best_multikill_window([5, 6, 50, 52], 1) == (2, 5)    # a tie keeps the earlier
    walk = [pos(x=i * 10, y=100 + i) for i in range(30)]
    ann = unit(1, 'Ann', positions=walk)
    foes = [unit(10 + i, f'Foe{i}', 'EAST', player=0) for i in range(2)]
    evs = [killed(f, 10 + i, 1) for i, f in enumerate([12, 14])]
    r = row(rows_of(doc([ann] + foes, evs)), 'Ann')
    assert r[H['Best Multikill At (s)']] == 12
    assert r[H['Best Multikill Pos']] == '120,112'
    quiet = row(rows_of(doc([unit(1, 'Ann')], [])), 'Ann')
    assert quiet[H['Best Multikill At (s)']] == '' and quiet[H['Best Multikill Pos']] == ''


def test_crew_of_a_shot_down_vehicle_is_not_a_multikill():
    ann, heli = unit(1, 'Ann'), {'id': 50, 'type': 'vehicle', 'name': 'Mi-8', 'side': 'EAST',
                                 'positions': [], 'startFrameNum': 0}
    crew = [unit(10 + i, f'Foe{i}', 'EAST', player=0) for i in range(3)]
    evs = [killed(5, 50, 1)] + [killed(5, 10 + i, 1) for i in range(3)]
    r = row(rows_of(doc([ann, heli] + crew, evs)), 'Ann')
    assert r[H['Best Multikill']] == 0


def test_kills_from_inside_a_vehicle_are_not_a_multikill():
    ann = unit(1, 'Ann', positions=[pos(veh=7) for _ in range(10)])
    foes = [unit(10 + i, f'Foe{i}', 'EAST', player=0) for i in range(3)]
    evs = [killed(5 + i, 10 + i, 1) for i in range(3)]
    r = row(rows_of(doc([ann] + foes, evs)), 'Ann')
    assert r[H['Best Multikill']] == 0


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


def test_an_empty_placeholder_with_a_players_id_does_not_replace_the_player():
    # Seen in a real recording: four entries with id 0, no type, side or positions,
    # listed after the player who actually holds id 0.
    ghost = {'id': 0, 'name': '', 'side': '', 'isPlayer': 0, 'type': '',
             'startFrameNum': 0, 'positions': None, 'framesFired': None}
    document = doc([unit(0, 'Ocke'), unit(1, 'Other'), dict(ghost), dict(ghost)], [])
    text = json.dumps(document)
    for rec in (kt.reduce_document(document), kt.read_recording(io.StringIO(text), chunk=7)):
        assert rec.entities[0].name == 'Ocke'
        assert rec.entities[0].n == 10
        assert {r[H['Username']] for r in kt.compute(rec, 'x.json.gz')['rows']} == {'Ocke', 'Other'}


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
            row = [''] * 35   # a row counted before the weapon columns existed
            row[0], row[1], row[3], row[7], row[17] = 'f_' + label, label, name, kills_foot, kills_veh
            out.append(row)
        return json.dumps(out)

    got = kt.known_players([(rows('M1', ('Ann', 2, 1), ('Bob', 1, 0)), None),
                            (rows('M2', ('Ann', 4, 0)), 'x')])
    assert got == [{'name': 'Ann', 'missions': 2, 'kills': 7, 'last': 'M2'},
                   {'name': 'Bob', 'missions': 1, 'kills': 1, 'last': 'M1'}]


def test_player_missions_count_every_recording_once_per_player():
    def rows(source, *players):
        out = []
        for name, kills in players:
            row = [''] * 35
            row[0], row[1], row[3], row[7], row[17] = source, source.upper(), name, kills, 0
            out.append(row)
        return json.dumps(out)

    stored = [(rows('a', ('Ann', 1), ('[X] Ann', 2), ('Bob', 0)), None),
              (rows('b', ('[X] Ann', 4)), None),
              (rows('c', ('Bob', 3), ('', 9)), 'renamed')]
    got = kt.player_missions(stored, {'Ann': '[X] Ann'})
    assert got == [
        {'name': '[X] Ann', 'names': ['[X] Ann', 'Ann'], 'missions': 2, 'kills': 7, 'last': 'B'},
        {'name': 'Bob', 'names': ['Bob'], 'missions': 2, 'kills': 3, 'last': 'C'}]
    # Without the link they are two players, and Ann's second name still counts once
    assert [p['missions'] for p in kt.player_missions(stored, {})] == [2, 1, 2]


def test_discord_ids_from_a_form():
    assert kt.clean_discord_id('') == '' and kt.clean_discord_id('  ') == ''
    assert kt.clean_discord_id('<@!123456789012345678>') == '123456789012345678'
    assert kt.clean_discord_id('123456789012345678') == '123456789012345678'
    for bad in ('Ann', '123', '12345678901234567890123'):
        with pytest.raises(ValueError):
            kt.clean_discord_id(bad)


def _guess_rows(*entries):
    """`(source, name, group[, role])` as one mission's stored rows JSON."""
    rows = []
    for e in entries:
        row = [''] * 8
        row[0], row[3], row[5] = e[0], e[1], e[2]
        row[6] = e[3] if len(e) > 3 else 'Rifleman'
        rows.append(row)
    return json.dumps(rows)


def test_guess_units_seed_tag_and_squad_spreading():
    stored = [_guess_rows(('a', 'Pricepole', 'Alpha'), ('a', 'Newbie', 'Alpha'),
                          ('a', '[CNTO] Tagged', 'Bravo'), ('a', 'Solo', 'Charlie'))]
    guess = kt.guess_units(stored)
    assert guess['Pricepole'] == 'CNTO'       # seed
    assert guess['[CNTO] Tagged'] == 'CNTO'   # tag
    assert guess['Newbie'] == 'CNTO'          # shares a squad with a seed
    assert 'Solo' not in guess                # nothing to go on


def test_guess_units_needs_a_clear_majority_and_ignores_zeus():
    mixed = [_guess_rows(('a', 'Pricepole', 'Alpha'), ('a', 'Darkling', 'Alpha'), ('a', 'Split', 'Alpha'))]
    assert 'Split' not in kt.guess_units(mixed)
    zeus = [_guess_rows(('a', 'Pricepole', 'Zeus'), ('a', 'Curator', 'Zeus'))]
    assert 'Curator' not in kt.guess_units(zeus)


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


def test_a_names_form_changes_only_what_was_changed():
    current = {1: None, 2: 'Set Before', 3: 'Keep Me'}
    submitted = [(1, ' New   Name (2026-01-01) '), (2, ''), (3, 'Keep Me')]
    assert kt.plan_name_edits(current, submitted) == {1: 'New Name', 2: None}


def test_an_empty_box_where_there_was_no_name_is_not_a_change():
    assert kt.plan_name_edits({1: None}, [(1, ''), (1, '   ')]) == {}


def test_one_invalid_name_means_the_form_writes_nothing():
    with pytest.raises(ValueError, match='at most'):
        kt.plan_name_edits({1: None, 2: None}, [(1, 'Fine'), (2, 'x' * 200)])


def test_ids_a_form_carries_that_no_recording_has_are_ignored():
    assert kt.plan_name_edits({1: None}, [(1, 'A'), (99, 'B')]) == {1: 'A'}


# ---------------------------------------------------------------------------
# Distances nobody believes (rules version 2) and the admin's strike list
# ---------------------------------------------------------------------------

def long_shot(weapon, dist, *, in_vehicle=False, exclude=frozenset()):
    ann = unit(1, 'Ann', positions=[pos(veh=7 if in_vehicle else 0) for _ in range(10)])
    ents = [ann, unit(2, 'Bob', 'EAST', player=0)] + ([vehicle(7, side='WEST')] if in_vehicle else [])
    return kt.compute(kt.reduce_document(doc(ents, [killed(5, 2, 1, weapon, dist)])),
                      '2026_03_14__20_27_Test.json.gz', exclude)


def test_indirect_fire_counts_as_a_kill_but_not_as_distance():
    r = row(long_shot('Gyra Mortar', 1500, in_vehicle=True), 'Ann')
    assert r[H['Kills (In Vehicle)']] == 1
    assert r[H['Longest Kill In Vehicle (m)']] == ''
    assert row(long_shot('M4', 1500, in_vehicle=True), 'Ann')[H['Longest Kill In Vehicle (m)']] == 1500


def test_a_pistol_does_not_kill_at_a_kilometre_and_a_half():
    r = row(long_shot('Diplomat 9 mm (Hex)', 1621, in_vehicle=True), 'Ann')
    assert r[H['Kills (In Vehicle)']] == 1
    assert r[H['Longest Kill In Vehicle (m)']] == ''
    assert row(long_shot('Rook-40 9 mm', 120), 'Ann')[H['Longest Kill On Foot (m)']] == 120


def test_a_struck_kill_still_counts_but_is_left_out_of_the_distance():
    key = kt.kill_key(5, 1, 2)
    result = long_shot('M4', 900, exclude={key})
    r = row(result, 'Ann')
    assert r[H['Kills (On Foot)']] == 1
    assert r[H['Longest Kill On Foot (m)']] == ''
    # …and it is still offered, flagged, so it can be put back
    assert [(k['d'], k['x']) for k in result['far']] == [(900, True)]


def test_the_far_list_holds_only_kills_that_would_feed_the_statistics():
    assert [k['d'] for k in long_shot('M4', 900)['far']] == [900]
    assert long_shot('M4', 100)['far'] == []            # too close to be worth checking
    assert long_shot('M4', 1600)['far'] == []           # already left out on foot
    assert long_shot('Gyra Mortar', 1500, in_vehicle=True)['far'] == []
    far = long_shot('M4', 900)['far'][0]
    assert (far['killer'], far['victim'], far['w'], far['ki'], far['vi'], far['f']) == (
        'Ann', 'Bob', 'M4', 1, 2, 5)


def test_a_zeus_kill_is_not_offered_for_checking():
    ann = unit(1, 'Ann', group='Zeus')
    result = kt.compute(kt.reduce_document(
        doc([ann, unit(2, 'Bob', 'EAST', player=0)], [killed(5, 2, 1, 'M4', 900)])), 'x.json.gz')
    assert result['far'] == []


def far(f, d, x=False, ki=1, vi=2):
    return {'f': f, 'ki': ki, 'vi': vi, 'killer': 'Ann', 'victim': 'Bob', 'w': 'M4', 'd': d, 'x': x}


def test_the_far_listing_is_farthest_first_across_missions_and_keeps_struck_ones():
    listing = kt.far_listing([(5, 'Op A', [far(1, 800), far(2, 1200, x=True)]),
                              (6, 'Op B', [far(3, 950)])])
    assert [(e['ocap_id'], e['d'], e['x']) for e in listing] == [(5, 1200, True), (6, 950, False), (5, 800, False)]
    assert listing[0]['id'] == '5|2:1:2' and listing[0]['mission'] == 'Op A'
    assert len(kt.far_listing([(5, 'A', [far(i, i) for i in range(1, 50)])], limit=10)) == 10
    assert kt.far_listing([(5, 'A', [{'f': 1}])]) == []          # a malformed entry is skipped


def test_a_strike_form_changes_only_what_differs_from_what_is_stored():
    entries = kt.far_listing([(5, 'A', [far(1, 800), far(2, 900, x=True), far(3, 700)])])
    ids = {e['f']: e['id'] for e in entries}
    changes = kt.plan_strikes(listed=list(ids.values()), checked=[ids[1], ids[2]], entries=entries)
    assert [(c[0], c[1], c[2]) for c in changes] == [(5, '1:1:2', True)]   # 1 newly struck; 2 stays; 3 untouched
    restore = kt.plan_strikes(list(ids.values()), checked=[], entries=entries)
    assert [(c[1], c[2]) for c in restore] == [('2:1:2', False)]


def test_a_strike_form_cannot_touch_a_kill_it_was_not_shown():
    entries = kt.far_listing([(5, 'A', [far(1, 800)])])
    assert kt.plan_strikes(['5|9:9:9', '7|1:1:2'], ['5|9:9:9', '7|1:1:2'], entries) == []


def test_a_kill_from_a_vehicle_is_never_offered_for_checking():
    # a vehicle's weapons reach that far; it still feeds the distance, it is just not on the list
    result = long_shot('M2 HMG', 1400, in_vehicle=True)
    assert result['far'] == []
    assert row(result, 'Ann')[H['Longest Kill In Vehicle (m)']] == 1400


def test_every_players_own_longest_kill_is_offered_even_when_the_mission_had_farther_ones():
    ann, cat = unit(1, 'Ann'), unit(3, 'Cat')
    victims = [unit(10 + i, f'V{i}', 'EAST', player=0) for i in range(kt.FAR_KILL_KEEP + 5)]
    events = [killed(1 + i, 10 + i, 1, 'M4', 900 - i) for i in range(kt.FAR_KILL_KEEP + 5)]
    events.append(killed(40, 10, 3, 'HK416', 250))          # Cat's best is far down the list
    result = kt.compute(kt.reduce_document(doc([ann, cat] + victims, events, end=5000)), 'x.json.gz')
    killers = [k['killer'] for k in result['far']]
    assert killers.count('Ann') == kt.FAR_KILL_KEEP
    assert killers.count('Cat') == 1


def test_a_struck_kill_does_not_take_a_place_so_the_next_farthest_fills_up():
    ann = unit(1, 'Ann')
    n = kt.FAR_KILL_KEEP + 1
    victims = [unit(10 + i, f'V{i}', 'EAST', player=0) for i in range(n)]
    events = [killed(1 + i, 10 + i, 1, 'M4', 900 - i) for i in range(n)]
    d = doc([ann] + victims, events, end=5000)
    plain = kt.compute(kt.reduce_document(d), 'x.json.gz')
    assert len(plain['far']) == kt.FAR_KILL_KEEP        # the 26th is not kept…
    struck = kt.compute(kt.reduce_document(d), 'x.json.gz', {kt.kill_key(1, 1, 10)})
    live = [k for k in struck['far'] if not k['x']]
    assert len(live) == kt.FAR_KILL_KEEP                 # …until the farthest is struck
    assert [k['d'] for k in struck['far'] if k['x']] == [900]
    assert min(k['d'] for k in live) == 900 - kt.FAR_KILL_KEEP


def test_the_weapon_of_the_longest_kill_is_recorded_with_it():
    ann = unit(1, 'Ann')
    victims = [unit(10 + i, f'V{i}', 'EAST', player=0) for i in range(3)]
    events = [killed(1, 10, 1, 'M4', 300), killed(2, 11, 1, 'HK417', 640), killed(3, 12, 1, 'M4', 500)]
    r = row(rows_of(doc([ann] + victims, events)), 'Ann')
    assert r[H['Longest Kill On Foot (m)']] == 640
    assert r[H['Longest Kill Weapon (On Foot)']] == 'HK417'
    assert r[H['Longest Kill Weapon (In Vehicle)']] == ''


def test_a_struck_longest_kill_gives_its_weapon_up_with_its_distance():
    ann = unit(1, 'Ann')
    victims = [unit(10 + i, f'V{i}', 'EAST', player=0) for i in range(2)]
    events = [killed(1, 10, 1, 'M4', 300), killed(2, 11, 1, 'HK417', 640)]
    result = kt.compute(kt.reduce_document(doc([ann] + victims, events)), 'x.json.gz', {kt.kill_key(2, 1, 11)})
    r = row(result, 'Ann')
    assert (r[H['Longest Kill On Foot (m)']], r[H['Longest Kill Weapon (On Foot)']]) == (300, 'M4')


def test_a_mission_counted_before_the_weapon_columns_still_makes_a_full_width_csv():
    old = json.dumps([[''] * 35])
    lines = kt.csv_from_stored([(old, None)]).strip().split('\n')
    assert len(lines[1].split(',')) == len(kt.HEADER)


def test_default_display_prefers_most_missions_then_kills_then_name():
    known = [
        {'name': 'Dusty', 'missions': 3, 'kills': 1, 'last': ''},
        {'name': 'PSGT Dusty', 'missions': 7, 'kills': 0, 'last': ''},
        {'name': 'Cpl Dusty', 'missions': 7, 'kills': 5, 'last': ''},
    ]
    assert kt.default_display(['Dusty', 'PSGT Dusty'], known) == 'PSGT Dusty'
    assert kt.default_display(['Dusty', 'PSGT Dusty', 'Cpl Dusty'], known) == 'Cpl Dusty'
    assert kt.default_display(['b', 'A'], []) == 'A'
    assert kt.default_display([], known) == ''


def test_match_key_drops_tags_ranks_and_punctuation():
    assert kt.match_key('[2nd USC] Trp. Dusty') == 'dusty'
    assert kt.match_key('[57th MEU]Rct. B. Dequip') == 'bdequip'
    assert kt.match_key('PSGT Dusty') == 'dusty'
    assert kt.match_key('Sgt') == 'sgt'  # a lone word is never stripped to nothing
    assert kt.match_key('') == ''


def test_suggest_members_ranks_by_closeness_and_skips_taken():
    members = [('1', ['Dusty']), ('2', ['Dusty_TFP']), ('3', ['Viking']), ('4', ['Dustin'])]
    got = kt.suggest_members(['[2nd USC] Trp. Dusty', 'PSGT Dusty'], members)
    assert [u for u, _ in got][:2] == ['1', '2']
    assert '3' not in [u for u, _ in got]
    assert [u for u, _ in kt.suggest_members(['Dusty'], members, taken={'1'})][0] == '2'
    assert kt.suggest_members(['x'], members) == []


def test_default_roles_match_names_ignoring_case():
    roles = [(1, 'Member'), (2, 'VETERAN'), (3, ' recruit '), (4, 'Cadet'), (5, 'Officer'), (6, None)]
    assert kt.default_roles(roles) == [1, 2, 3, 4]
    assert kt.default_roles(roles, ['officer']) == [5]


def test_role_filter_lists_members_with_a_selected_role_and_unmapped_on_request():
    assert kt.passes_role_filter([1, 9], [1, 2], True, False)
    assert not kt.passes_role_filter([9], [1, 2], True, True)
    assert not kt.passes_role_filter([], [], True, True)
    assert kt.passes_role_filter([], [1], False, True)
    assert not kt.passes_role_filter([], [1], False, False)


# -- the dashboard served by the bot -------------------------------------------

_PAGE = '<html><head><link href="favicon.svg"><title>x</title></head><body><script src="tracker.js"></script></body></html>'


def test_stats_page_uses_absolute_asset_paths():
    page = kt.stats_page(_PAGE)
    assert '/stats/assets/tracker.js' in page and '/stats/assets/favicon.svg' in page
    assert 'replaceState' not in page


def test_stats_page_for_a_player_rewrites_the_address_and_escapes():
    page = kt.stats_page(_PAGE, '[TFP] </script><b>"x"')
    assert page.count('</script>') == 2          # the name did not close our script
    assert '<b>' not in page
    assert page.index('replaceState') < page.index('/stats/assets/tracker.js')


def test_stats_page_ignores_an_overlong_name():
    assert 'replaceState' not in kt.stats_page(_PAGE, 'x' * 1000)


def test_stats_page_icon_replaces_the_crosshair_only_when_given():
    page = '<head><link rel="icon" type="image/svg+xml" href="favicon.svg"></head><script src="tracker.js"></script>'
    assert '/stats/assets/favicon.svg' in kt.stats_page(page)
    with_icon = kt.stats_page(page, icon='/static/logo.png?v=1')
    assert 'rel="icon" href="/static/logo.png?v=1"' in with_icon
    assert 'favicon.svg' not in with_icon


# -- claiming a player ----------------------------------------------------------

def test_claimable_players_are_the_shown_names():
    known = [{'name': 'Dusty'}, {'name': 'Trp. Dusty'}, {'name': 'alpha'}]
    assert kt.claimable_players(known, {'Trp. Dusty': 'Dusty'}) == ['alpha', 'Dusty']


def test_valid_claim_matches_exactly_then_ignoring_case():
    names = ['Dusty', 'alpha']
    assert kt.valid_claim('  Dusty ', names) == 'Dusty'
    assert kt.valid_claim('ALPHA', names) == 'alpha'
    for bad in ('', '   ', 'nobody'):
        with pytest.raises(ValueError):
            kt.valid_claim(bad, names)


def test_valid_claim_does_not_guess_between_case_variants():
    with pytest.raises(ValueError):
        kt.valid_claim('DUSTY', ['Dusty', 'dusty'])


def test_stats_page_bar_goes_first_in_the_body():
    page = kt.stats_page('<head></head><body><p>x</p></body>', bar='<nav>hi</nav>')
    assert '<body>\n<nav>hi</nav><p>x' in page


def test_count_progress_separates_done_recount_and_open():
    cur = kt.RULES_VERSION
    rows = [{'status': 'ok', 'rules_version': cur}, {'status': 'ok', 'rules_version': cur},
            {'status': 'ok', 'rules_version': cur - 1}, {'status': 'failed', 'rules_version': 0}]
    got = kt.count_progress(rows)
    assert got == {'total': 4, 'done': 2, 'recount': 1, 'open': 1, 'pct': 50}


def test_count_progress_of_nothing_is_complete():
    assert kt.count_progress([])['pct'] == 100
