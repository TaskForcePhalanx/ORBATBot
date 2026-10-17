"""Turning one OCAP recording into the Kill Tracker's per-player rows.

This is the Python port of the Kill Tracker's Google Apps Script
(`ImportScript`), and it deliberately produces the *same rows in the same column
order* — so the dashboard can read this bot's output exactly as it read the
sheet, and the two can run side by side while they are compared.

Two halves, and the split is the point:

* **Reading** (`read_recording()`): a recording is 50–90 MB of JSON once
  unzipped, almost all of it one `positions` entry per player per frame, and
  `json.loads` on that peaks at 0.5–1 GB — enough to get a container killed.
  So the file is read as a stream, one entity at a time, and each entity is
  reduced on the spot to the few columns of numbers the statistics need (see
  `Entity`). Nothing here imports anything beyond the standard library.
* **Counting** (`compute()`): the rules — what is a kill, a teamkill, a crew
  kill, a suicide — on the reduced recording. Pure functions, and what the tests
  pin.

**Bump `RULES_VERSION` whenever a rule changes.** Every stored mission carries
the version it was counted with, and `cogs/killtracker.py` re-reads and
re-counts any mission whose version is older — which is what replaces
"re-import every mission by hand" after a rule change.
"""

import csv
import io
import json
import math
import re
from array import array
from bisect import bisect_left, bisect_right
from decimal import ROUND_HALF_UP, Decimal

# Version of the counting rules. 1 = the ImportScript as of the crew-kill fix:
# kills on a vehicle's occupants don't feed longest/avg kill distance.
# 2 = indirect fire (mortars, artillery) and pistols no longer feed the distance
# statistics, and a kill an admin has struck from the list doesn't either.
# 3 = the list of kills to check holds on-foot kills only (a vehicle's long shots
# are real) and every player's own longest one, not just the mission's farthest.
# 4 = two more columns: the weapon the longest kill was made with.
# 5 = two more columns: how often a player went unconscious, and for how long.
# 6 = one more column: the most enemy kills inside any 10 seconds.
RULES_VERSION = 6

# The column schema — identical to the Apps Script's `header`, in order.
HEADER = [
    # Mission info
    "Source File", "Mission", "World",
    # Player info
    "Username", "Side", "Group", "Role",
    # Infantry (on-foot) stats
    "Kills (On Foot)", "Deaths (On Foot)", "K/D (On Foot)",
    "Teamkills (On Foot)", "Shots (On Foot)", "Hits Taken (On Foot)", "Hits Dealt (On Foot)",
    "Shots/Kill (On Foot)", "Avg Kill Dist On Foot (m)", "Longest Kill On Foot (m)",
    # Vehicle stats
    "Kills (In Vehicle)", "Deaths (In Vehicle)", "K/D (In Vehicle)",
    "Teamkills (In Vehicle)", "Vehicle Kills (On Foot)", "Vehicle Kills (In Vehicle)",
    "Shots (In Vehicle)", "Hits Taken (In Vehicle)", "Hits Dealt (In Vehicle)",
    "Shots/Kill (In Vehicle)", "Avg Kill Dist In Vehicle (m)", "Longest Kill In Vehicle (m)",
    # Weapon stats
    "Top Weapon", "Weapon Kills (JSON)",
    # Misc
    "Suicides",
    "Time Played (s)",
    "Distance Run (km)",
    # Vehicle kill detail list
    "Vehicle Kills (JSON)",
    # Added by the bot, after the sheet's columns: the weapon of the longest kill
    "Longest Kill Weapon (On Foot)", "Longest Kill Weapon (In Vehicle)",
    # And after those: ACE unconsciousness, read from the alive state (2)
    "Times Unconscious", "Time Unconscious (s)",
    # And the most kills inside MULTIKILL_WINDOW_S seconds
    "Best Multikill",
]

# --- counting thresholds (the Apps Script's constants, unchanged) -----------

# On-foot kills beyond this still count as kills but not for distance stats.
MAX_FOOT_KILL_DIST = 1500
# A pistol does not kill at more than this; a longer "pistol" kill is a weapon
# OCAP attributed wrongly, usually a vehicle's.
MAX_PISTOL_KILL_DIST = 300
# Weapons that are fired at a map reference rather than at what the shooter sees.
# Their kills count as kills, not as distance: 1.5 km with a mortar is the
# weapon working, not a long shot.
_INDIRECT = re.compile(
    r'mortar|howitzer|artillery|\bmk6\b|sochor|sholef|scorcher|\bmlrs\b|rocket pod'
    r'|cruise missile|\bgrad\b|2s9|m119', re.I)
_PISTOL = re.compile(
    r'pistol|diplomat|\bp07\b|rook-?40|\bacp-?c2\b|4-?five|zubr|\bhandgun\b'
    r'|\bm1911\b|\bglock\b|\bmakarov\b', re.I)
# The kills worth showing an admin for checking: the farthest ones of each mission.
FAR_KILL_MIN_M = 200
FAR_KILL_KEEP = 25
# OCAP v5+ logs phantom shots whose target is thousands of metres away.
MAX_SHOT_DIST = 800
# End-of-mission teamkill suppression: players celebrate by shooting each other.
EOM_WINDOW_S = 300           # hard cutoff: the last 5 minutes
EOM_RANGE_WINDOW_S = 420     # extended cutoff for close-range kills (7 minutes)
EOM_CLOSE_M = 15             # grenade / pistol range
EOM_BURST_WINDOW = 60        # rolling window for burst detection, in frames
EOM_BURST_COUNT = 3          # TKs inside the window that make a burst
EOM_BURST_MAX_FROMEND_S = 600  # the burst rule only applies in the last 10 minutes
# `positions[i][2]` is 0 dead, 1 alive, 2 unconscious (ACE).
STATE_UNCONSCIOUS = 2
# A multikill is this many seconds of game time wide, edges included.
MULTIKILL_WINDOW_S = 10
# An unnamed entity with less playtime than this is noise, not a player.
MIN_UNKNOWN_PLAYTIME_S = 3600


# ---------------------------------------------------------------------------
# JS-compatible rounding. The Apps Script's `toFixed` and `Math.round` round
# halves *up*; Python's `round()` and `format()` round halves to even. The rows
# have to match the sheet's, so the port does what JavaScript does.
# ---------------------------------------------------------------------------

def to_fixed(x: float, digits: int) -> float:
    """`parseFloat(x.toFixed(digits))`."""
    exact = Decimal(x).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return float(exact)


def js_round(x: float) -> int:
    """`Math.round(x)`."""
    return int(math.floor(x + 0.5))


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

class Entity:
    """One entity, reduced.

    `veh[i]` is `positions[i][3]` — 0 on foot, otherwise the vehicle's id (the
    old format used 1) — and is kept for every unit, because a *victim* may be an
    AI crewman. `alive`, `xs` and `ys` are only kept for players, who are the
    only ones whose distance run and shot geometry are counted. Vehicles keep no
    positions at all: nothing asks where a vehicle was.
    """

    __slots__ = ('id', 'type', 'side', 'name', 'group', 'role', 'is_player',
                 'start', 'n', 'veh', 'alive', 'xs', 'ys', 'first_name',
                 'first_role', 'frames_fired')

    def __init__(self):
        self.id = None
        self.type = None
        self.side = None
        self.name = None
        self.group = None
        self.role = None
        self.is_player = None
        self.start = 0
        self.n = 0
        self.veh = array('i')
        self.alive = array('b')
        self.xs = array('d')
        self.ys = array('d')
        self.first_name = None
        self.first_role = None
        self.frames_fired = []

    def in_vehicle_at(self, frame: int) -> bool:
        """`isInVehicleAtFrame()`: False outside the recorded range, like the script."""
        idx = frame - self.start
        if idx < 0 or idx >= self.n:
            return False
        return self.veh[idx] != 0


class Recording:
    """What `compute()` needs from one OCAP file, and nothing more."""

    def __init__(self):
        self.mission_name = ''
        self.world_name = ''
        self.capture_delay = 1
        self.end_frame = None
        self.entities = {}
        self.events = []


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _veh_code(v) -> int:
    """`positions[i][3]` as one int: 0 means on foot, anything else in a vehicle.

    A missing or odd value counts as in a vehicle, because the script's
    `pos[3] !== 0` does: `undefined !== 0` and `null !== 0` are both true.
    """
    if _is_number(v):
        return 0 if v == 0 else (int(v) or 1)
    return -1


def reduce_entity(e: dict, *, keep_track: bool = None) -> Entity:
    """Boil one entity dict down to an `Entity`.

    `keep_track` says whether to keep the alive/x/y columns; left as None it is
    decided here the way `compute()` decides who a player is, which is the only
    reason it can be passed at all — the tests use it to compare both paths.
    """
    out = Entity()
    out.id = e.get('id')
    out.type = e.get('type')
    out.side = e.get('side')
    out.name = e.get('name')
    out.group = e.get('group')
    out.role = e.get('role')
    out.is_player = e.get('isPlayer')
    out.start = e.get('startFrameNum') or 0

    if keep_track is None:
        keep_track = is_player_entity(out)

    positions = e.get('positions')
    if out.type == 'unit' and isinstance(positions, list):
        out.n = len(positions)
        veh, alive, xs, ys = out.veh, out.alive, out.xs, out.ys
        nan = float('nan')
        for pos in positions:
            if not isinstance(pos, list):
                veh.append(0)
                if keep_track:
                    alive.append(0)
                    xs.append(nan)
                    ys.append(nan)
                continue
            veh.append(_veh_code(pos[3]) if len(pos) > 3 else -1)
            if out.first_name is None and len(pos) > 4 and pos[4]:
                out.first_name = pos[4]
            if out.first_role is None and len(pos) > 6 and pos[6]:
                out.first_role = pos[6]
            if keep_track:
                alive.append(pos[2] if len(pos) > 2 and pos[2] in (1, STATE_UNCONSCIOUS) else 0)
                xy = pos[0] if pos and isinstance(pos[0], list) else None
                if xy is not None and len(xy) > 1 and _is_number(xy[0]) and _is_number(xy[1]):
                    xs.append(float(xy[0]))
                    ys.append(float(xy[1]))
                else:
                    xs.append(nan)
                    ys.append(nan)
    elif isinstance(positions, list):
        # Not a unit, so no columns — only the count, which is what playtime is
        # made of for an unnamed `isPlayer: 0` entity of some other type.
        out.n = len(positions)

    if keep_track and isinstance(e.get('framesFired'), list):
        out.frames_fired = e['framesFired']
    return out


def is_player_entity(ent: Entity) -> bool:
    """`isPlayer === 1`, or an unnamed `isPlayer === 0` (a player whose name OCAP lost)."""
    ip = ent.is_player
    return ip == 1 or (ip == 0 and not ent.name)


def reduce_document(doc: dict) -> Recording:
    """An already-parsed document, reduced exactly as the streaming reader reduces it."""
    rec = Recording()
    rec.mission_name = doc.get('missionName') or ''
    rec.world_name = doc.get('worldName') or ''
    rec.capture_delay = doc.get('captureDelay') or 1
    rec.end_frame = doc.get('endFrame')
    for e in doc.get('entities') or []:
        ent = reduce_entity(e)
        rec.entities[ent.id] = ent
    rec.events = doc.get('events') or []
    return rec


class _Reader:
    """Incremental JSON reading over a text stream.

    `json.JSONDecoder.raw_decode()` parses one value out of a string at an
    offset — in C, so it is fast — and raises when the value is cut off by the end
    of what has been read so far. This keeps a buffer, reads more when that
    happens, and tries again. A value that decodes right up to the end of the
    buffer is not trusted either: `12` at the edge might be `12345`.
    """

    _decoder = json.JSONDecoder()
    _WS = ' \t\r\n'

    def __init__(self, stream, chunk: int = 1 << 20):
        self._stream = stream
        self._chunk = chunk
        self._buf = ''
        self._pos = 0
        self._eof = False

    def _fill(self, size: int) -> bool:
        if self._eof:
            return False
        data = self._stream.read(size)
        if not data:
            self._eof = True
            return False
        self._buf = self._buf[self._pos:] + data
        self._pos = 0
        return True

    def peek(self) -> str:
        while True:
            buf, pos = self._buf, self._pos
            while pos < len(buf) and buf[pos] in self._WS:
                pos += 1
            self._pos = pos
            if pos < len(buf):
                return buf[pos]
            if not self._fill(self._chunk):
                return ''

    def expect(self, ch: str) -> None:
        got = self.peek()
        if got != ch:
            raise ValueError(f'expected {ch!r} but found {got!r}')
        self._pos += 1

    def value(self):
        """The next complete JSON value."""
        self.peek()
        size = self._chunk
        while True:
            try:
                obj, end = self._decoder.raw_decode(self._buf, self._pos)
            except json.JSONDecodeError:
                if not self._fill(size):
                    raise
                size *= 2  # a big value costs a few doublings, not a retry per chunk
                continue
            if end >= len(self._buf) and not self._eof:
                if self._fill(size):
                    continue
            self._pos = end
            return obj

    def members(self):
        """`(key, value)` for each member of the top-level object; entities one by one.

        The one array that matters is yielded element by element as
        `('entities', entity)`, so a recording is never held whole.
        """
        self.expect('{')
        while True:
            c = self.peek()
            if c == '}' or c == '':
                return
            if c == ',':
                self._pos += 1
                continue
            key = self.value()
            self.expect(':')
            if key == 'entities':
                self.expect('[')
                while True:
                    c = self.peek()
                    if c == ']':
                        self._pos += 1
                        break
                    if c == ',':
                        self._pos += 1
                        continue
                    if c == '':
                        raise ValueError('recording ends inside the entities list')
                    yield 'entities', self.value()
            else:
                yield key, self.value()


def read_recording(stream, *, chunk: int = 1 << 20) -> Recording:
    """Read an OCAP recording from a text stream, one entity at a time."""
    rec = Recording()
    for key, value in _Reader(stream, chunk).members():
        if key == 'entities':
            ent = reduce_entity(value)
            rec.entities[ent.id] = ent
        elif key == 'events':
            rec.events = value
        elif key == 'missionName':
            rec.mission_name = value or ''
        elif key == 'worldName':
            rec.world_name = value or ''
        elif key == 'captureDelay':
            rec.capture_delay = value or 1
        elif key == 'endFrame':
            rec.end_frame = value
    return rec


def open_recording(path: str) -> Recording:
    """Read a recording from disk, zipped or not.

    **Looks at the first two bytes, not at the name.** The OCAP server serves
    `.json.gz` files, and whether what lands on disk is still zipped depends on
    how it was fetched — an HTTP client that honours `Content-Encoding: gzip`
    hands over plain JSON under a `.json.gz` name.
    """
    import gzip

    with open(path, 'rb') as probe:
        zipped = probe.read(2) == b'\x1f\x8b'
    opener = gzip.open if zipped else open
    with opener(path, 'rb') as raw:
        return read_recording(io.TextIOWrapper(raw, encoding='utf-8'))


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------

class _Player:
    """One player's running counts. A plain class so the loop reads like the script."""

    __slots__ = ('ent', 'name', 'side', 'role', 'group', 'kills_foot', 'deaths_foot',
                 'tk_foot', 'shots_foot', 'hits_taken_foot', 'hits_dealt_foot',
                 'kill_dist_foot', 'kills_veh', 'deaths_veh', 'tk_veh',
                 'vkills_foot', 'vkills_veh', 'shots_veh', 'hits_taken_veh',
                 'hits_dealt_veh', 'kill_dist_veh', 'longest_foot_weapon', 'longest_veh_weapon', 'weapon_kills', 'vehicle_kills',
                 'suicides', 'is_unknown', 'is_zeus', 'time_played', 'distance_run',
                 'times_unconscious', 'time_unconscious', 'kill_frames')

    def __init__(self, ent: Entity, name: str, capture_delay):
        self.ent = ent
        self.name = name
        self.side = ent.side
        self.role = ent.role or ''
        self.group = ent.group or ''
        self.kills_foot = self.deaths_foot = self.tk_foot = 0
        self.shots_foot = self.hits_taken_foot = self.hits_dealt_foot = 0
        self.kill_dist_foot = []
        self.kills_veh = self.deaths_veh = self.tk_veh = 0
        self.vkills_foot = self.vkills_veh = 0
        self.shots_veh = self.hits_taken_veh = self.hits_dealt_veh = 0
        self.kill_dist_veh = []
        self.longest_foot_weapon = self.longest_veh_weapon = ''
        self.weapon_kills = {}
        self.vehicle_kills = []
        self.suicides = 0
        self.is_unknown = False
        self.is_zeus = False
        self.time_played = ent.n * capture_delay
        self.distance_run = 0
        self.times_unconscious = self.time_unconscious = 0
        self.kill_frames = []

    def zero(self) -> None:
        """A Zeus has no combat stats."""
        self.kills_foot = self.deaths_foot = self.tk_foot = 0
        self.shots_foot = self.hits_taken_foot = self.hits_dealt_foot = 0
        self.kills_veh = self.deaths_veh = self.tk_veh = 0
        self.vkills_foot = self.vkills_veh = 0
        self.shots_veh = self.hits_taken_veh = self.hits_dealt_veh = 0
        self.suicides = 0
        self.distance_run = 0
        self.times_unconscious = self.time_unconscious = 0
        self.kill_frames = []
        self.kill_dist_foot = []
        self.kill_dist_veh = []
        self.longest_foot_weapon = self.longest_veh_weapon = ''
        self.weapon_kills = {}
        self.vehicle_kills = []


def _is_zeus(ent: Entity) -> bool:
    group = (ent.group or '').lower()
    role = (ent.role or ent.first_role or '').lower()
    return group == 'zeus' or 'zeus' in role


def _event(ev, i):
    return ev[i] if len(ev) > i else None


def mission_label(base: str, source_name: str) -> str:
    """`Mission (YYYY-MM-DD)`, the date taken from the file name.

    Old names look like `2026_03_14__20_27_Name.json.gz`, newer ones like
    `Name_20260530_222136.json.gz`.
    """
    m = (re.search(r'(\d{4})_(\d{2})_(\d{2})', source_name)
         or re.search(r'_(\d{4})(\d{2})(\d{2})_', source_name))
    if not m:
        return base
    return f'{base} ({m.group(1)}-{m.group(2)}-{m.group(3)})'


def kill_key(frame, killer_id, victim_id) -> str:
    """How one kill is named when an admin strikes it from the distance statistics."""
    return f'{frame}:{killer_id}:{victim_id}'


def is_indirect_fire(weapon) -> bool:
    return bool(weapon) and bool(_INDIRECT.search(weapon))


def is_pistol(weapon) -> bool:
    return bool(weapon) and bool(_PISTOL.search(weapon))


def best_multikill(frames, capture) -> int:
    """The most kills inside any `MULTIKILL_WINDOW_S` seconds: `frames` are the
    frame numbers of one player's kills, `capture` the seconds per frame."""
    width = MULTIKILL_WINDOW_S / (capture or 1)
    frames = sorted(f for f in frames if isinstance(f, (int, float)))
    best = lo = 0
    for hi, f in enumerate(frames):
        while f - frames[lo] > width:
            lo += 1
        best = max(best, hi - lo + 1)
    return best


def compute(rec: Recording, source_name: str, exclude=frozenset()) -> dict:
    """Count one recording. Returns `{'mission', 'world', 'rows', 'kills', 'far'}`.

    `rows` follow `HEADER`, sorted by on-foot kills. `kills` is every counted
    enemy kill by a (non-Zeus) player — the number to hold against the OCAP
    server's own `player_kill_count`.

    `exclude` holds `kill_key()`s an admin has struck: such a kill still counts
    as a kill and is left out of the distance statistics. `far` lists the
    farthest kills that *would* feed those statistics — struck ones included,
    flagged `x`, so they can be put back — for the admin page to show.
    """
    entities = rec.entities
    capture = rec.capture_delay or 1

    # -- who is a player ----------------------------------------------------
    players = {}
    for ent_id in sorted(k for k in entities if isinstance(k, int)):
        ent = entities[ent_id]
        if not is_player_entity(ent):
            continue
        resolved = ent.name or ent.first_name
        p = _Player(ent, resolved or f'Unknown {ent.id}', capture)
        p.is_unknown = not resolved
        p.is_zeus = _is_zeus(ent)
        players[ent.id] = p

    # An unnamed player who shares a group+role with exactly one named player in
    # the mission is that player (OCAP v5 sometimes loses the name).
    named = {}
    for p in players.values():
        if not p.is_unknown and p.group and p.role:
            named.setdefault(f'{p.group.strip()}|{p.role.strip()}', []).append(p.name)
    for p in players.values():
        if p.is_unknown and p.group and p.role:
            unique = set(named.get(f'{p.group.strip()}|{p.role.strip()}', ()))
            if len(unique) == 1:
                p.name = next(iter(unique))
                p.is_unknown = False

    # -- distance run on foot ----------------------------------------------
    # Steps over max_step are teleports. Arma's sprint tops out near 7 m/s.
    max_step = 25 * capture
    for p in players.values():
        ent = p.ent
        dist = 0.0
        for i in range(1, ent.n):
            if not (ent.alive[i - 1] == 1 and ent.alive[i] == 1
                    and ent.veh[i - 1] == 0 and ent.veh[i] == 0):
                continue
            step = math.hypot(ent.xs[i] - ent.xs[i - 1], ent.ys[i] - ent.ys[i - 1])
            if step <= max_step:  # NaN fails this, like the script's
                dist += step
        p.distance_run = js_round(dist)

    # -- unconsciousness (ACE) ---------------------------------------------
    # Each entry into state 2 is one time down; a respawn is 0 -> 1 and never
    # counts, and neither does being dead. Time is frames times the capture delay.
    for p in players.values():
        frames = downs = 0
        prev = 0
        for state in p.ent.alive:
            if state == STATE_UNCONSCIOUS:
                frames += 1
                if prev != STATE_UNCONSCIOUS:
                    downs += 1
            prev = state
        p.times_unconscious = downs
        p.time_unconscious = frames * capture

    # -- shots fired, by context -------------------------------------------
    for p in players.values():
        ent = p.ent
        seen = set()
        for entry in ent.frames_fired:
            is_list = isinstance(entry, list)
            frame = entry[0] if is_list else entry
            target = entry[1] if is_list and len(entry) > 1 and isinstance(entry[1], list) else None
            if target is not None and len(target) > 1:
                key = (frame, target[0], target[1])
            else:
                key = (frame,)
            if key in seen:
                continue
            seen.add(key)
            if not isinstance(frame, int):
                continue
            if not ent.in_vehicle_at(frame):
                if target is not None and len(target) > 1:
                    idx = frame - ent.start
                    if 0 <= idx < ent.n:
                        try:
                            dx = float(target[0]) - ent.xs[idx]
                            dy = float(target[1]) - ent.ys[idx]
                        except (TypeError, ValueError):
                            dx = dy = float('nan')
                        if math.hypot(dx, dy) > MAX_SHOT_DIST:
                            continue  # a phantom shot
                p.shots_foot += 1
            else:
                p.shots_veh += 1

    # -- disconnects: a crash makes the engine log a self-kill --------------
    disconnects = set()
    for ev in rec.events:
        if len(ev) > 2 and ev[1] == 'disconnected':
            disconnects.add((ev[2], ev[0]))

    # -- end-of-mission teamkill suppression --------------------------------
    end_frame = float('inf')
    for ev in rec.events:
        if len(ev) > 1 and ev[1] == 'endMission':
            end_frame = ev[0]
            break
    else:
        if rec.end_frame is not None:
            end_frame = rec.end_frame

    tk_frames = []
    for ev in rec.events:
        if len(ev) < 4 or ev[1] != 'killed':
            continue
        v_id = ev[2]
        k_id = ev[3][0] if ev[3] else None
        if not k_id or k_id == v_id:
            continue
        v, k = entities.get(v_id), entities.get(k_id)
        if v and k and v.type == 'unit' and v.side == k.side:
            tk_frames.append(ev[0])
    tk_frames.sort()

    def burst_count(frame) -> int:
        half = EOM_BURST_WINDOW / 2
        return bisect_right(tk_frames, frame + half) - bisect_left(tk_frames, frame - half)

    def is_eom_tk(frame, distance) -> bool:
        from_end = end_frame - frame
        if from_end < 0:
            return True
        if from_end <= EOM_WINDOW_S:
            return True
        if from_end <= EOM_RANGE_WINDOW_S and distance <= EOM_CLOSE_M:
            return True
        if from_end <= EOM_BURST_MAX_FROMEND_S and burst_count(frame) >= EOM_BURST_COUNT:
            return True
        return False

    # -- vehicle kills: the same frame as a kill of the crew -----------------
    # Crew killed in the frame their vehicle is destroyed are logged as infantry
    # kills at the vehicle's distance. They count as kills, not as distance.
    vehicle_kill_frames = set()
    for ev in rec.events:
        if len(ev) < 4 or ev[1] != 'killed':
            continue
        v = entities.get(ev[2])
        k = ev[3][0] if ev[3] else None
        if (v and v.type == 'vehicle' and k is not None and k != ev[2]
                and entities.get(k) and v.side != entities[k].side):
            vehicle_kill_frames.add((k, ev[0]))

    # -- events -------------------------------------------------------------
    seen_hits = set()
    far = []
    for ev in rec.events:
        frame = _event(ev, 0)
        kind = _event(ev, 1)
        victim_id = _event(ev, 2)
        killer_info = _event(ev, 3)
        distance = _event(ev, 4) or 0

        if kind == 'killed':
            victim = entities.get(victim_id)
            killer_id = killer_info[0] if killer_info else None
            killer = entities.get(killer_id) if killer_id is not None else None
            victim_type = victim.type if victim else None

            if victim_type == 'unit' and victim_id in players:
                if victim.in_vehicle_at(frame):
                    players[victim_id].deaths_veh += 1
                else:
                    players[victim_id].deaths_foot += 1

            if killer_id is None or killer_id not in players:
                continue
            if killer_id == victim_id:
                # A disconnect within a frame of the self-kill is the engine
                # logging a crash, not a suicide. The lookup is by name: the event
                # stores no id, and a reconnect gets a new one.
                name = victim.name if victim else None
                is_dc = bool(name) and any((name, frame + d) in disconnects for d in (-1, 0, 1))
                if not is_dc:
                    players[victim_id].suicides += 1
                continue

            killer_p = players[killer_id]
            same_side = bool(victim and killer and victim.side == killer.side)
            killer_in_vehicle = killer_p.ent.in_vehicle_at(frame)

            if victim_type == 'vehicle':
                if not same_side:
                    if killer_in_vehicle:
                        killer_p.vkills_veh += 1
                    else:
                        killer_p.vkills_foot += 1
                    killer_p.vehicle_kills.append({
                        'v': (victim.name or 'Unknown Vehicle') if victim else 'Unknown Vehicle',
                        'w': (killer_info[1] if len(killer_info) > 1 and killer_info[1] else 'Unknown'),
                    })
            elif victim_type == 'unit':
                if same_side:
                    if not is_eom_tk(frame, distance):
                        if killer_in_vehicle:
                            killer_p.tk_veh += 1
                        else:
                            killer_p.tk_foot += 1
                else:
                    # Crew of a shot-down vehicle: the victim sat in a vehicle
                    # (this frame or the one before), or died in the same frame as
                    # a vehicle kill by the same player.
                    crew = ((killer_id, frame) in vehicle_kill_frames
                            or victim.in_vehicle_at(frame)
                            or victim.in_vehicle_at(frame - 1))
                    weapon_name = killer_info[1] if len(killer_info) > 1 and killer_info[1] else ''
                    # Whether the distance can be believed at all. A kill that
                    # fails is still a kill. On foot, 1500 m is the ceiling (a
                    # launcher logged with the wrong weapon, OCAP artefacts).
                    plausible = (not crew and not is_indirect_fire(weapon_name)
                                 and not (is_pistol(weapon_name) and distance > MAX_PISTOL_KILL_DIST)
                                 and (killer_in_vehicle or distance <= MAX_FOOT_KILL_DIST))
                    if not crew:
                        # A shot-down helicopter's crew all die in one frame and
                        # would be a multikill nobody earned.
                        killer_p.kill_frames.append(frame)
                    key = kill_key(frame, killer_id, victim_id)
                    struck = key in exclude
                    # Only an infantryman's shot is worth checking: a vehicle's
                    # weapons really do reach that far.
                    if plausible and not killer_in_vehicle and distance >= FAR_KILL_MIN_M:
                        far.append({'f': frame, 'ki': killer_id, 'vi': victim_id,
                                    'killer': killer_p.name,
                                    'victim': (victim.name if victim and victim.name else 'Unknown'),
                                    'w': weapon_name or 'Unknown', 'd': distance,
                                    'veh': killer_in_vehicle, 't': js_round(frame * capture),
                                    'x': struck})
                    feeds = plausible and not struck
                    if killer_in_vehicle:
                        killer_p.kills_veh += 1
                        if feeds:
                            if not killer_p.kill_dist_veh or distance > max(killer_p.kill_dist_veh):
                                killer_p.longest_veh_weapon = weapon_name
                            killer_p.kill_dist_veh.append(distance)
                    else:
                        killer_p.kills_foot += 1
                        if feeds:
                            if not killer_p.kill_dist_foot or distance > max(killer_p.kill_dist_foot):
                                killer_p.longest_foot_weapon = weapon_name
                            killer_p.kill_dist_foot.append(distance)
                        weapon = weapon_name or 'Unknown'
                        killer_p.weapon_kills[weapon] = killer_p.weapon_kills.get(weapon, 0) + 1

        elif kind == 'hit':
            shooter_id = killer_info[0] if killer_info else None
            hit_key = (shooter_id, victim_id, frame)
            if hit_key in seen_hits:
                continue
            seen_hits.add(hit_key)
            if victim_id in players:
                if players[victim_id].ent.in_vehicle_at(frame):
                    players[victim_id].hits_taken_veh += 1
                else:
                    players[victim_id].hits_taken_foot += 1
            if shooter_id is not None and shooter_id in players:
                if players[shooter_id].ent.in_vehicle_at(frame):
                    players[shooter_id].hits_dealt_veh += 1
                else:
                    players[shooter_id].hits_dealt_foot += 1

    # -- rows ---------------------------------------------------------------
    label = mission_label(rec.mission_name, source_name)
    rows = []
    kills = 0
    for p in players.values():
        if p.is_unknown and js_round(p.time_played) < MIN_UNKNOWN_PLAYTIME_S:
            continue
        if p.is_zeus:
            p.zero()

        kd_foot = to_fixed(p.kills_foot / p.deaths_foot if p.deaths_foot else p.kills_foot, 2)
        kd_veh = to_fixed(p.kills_veh / p.deaths_veh if p.deaths_veh else p.kills_veh, 2)
        spk_foot = to_fixed(p.shots_foot / p.kills_foot, 1) if p.kills_foot else ''
        spk_veh = to_fixed(p.shots_veh / p.kills_veh, 1) if p.kills_veh else ''
        avg_foot = to_fixed(sum(p.kill_dist_foot) / len(p.kill_dist_foot), 0) if p.kill_dist_foot else ''
        avg_veh = to_fixed(sum(p.kill_dist_veh) / len(p.kill_dist_veh), 0) if p.kill_dist_veh else ''
        lk_foot = max(p.kill_dist_foot) if p.kill_dist_foot else ''
        lk_veh = max(p.kill_dist_veh) if p.kill_dist_veh else ''
        weapons = sorted(p.weapon_kills.items(), key=lambda kv: kv[1], reverse=True)
        top_weapon = weapons[0][0] if weapons else ''
        weapon_json = _json(dict(weapons)) if weapons else ''
        kills += p.kills_foot + p.kills_veh

        rows.append([
            source_name,
            label,
            rec.world_name,
            p.name,
            p.side if p.side is not None else '',
            p.group,
            p.role,
            p.kills_foot,
            p.deaths_foot,
            kd_foot,
            p.tk_foot,
            p.shots_foot,
            p.hits_taken_foot,
            p.hits_dealt_foot,
            spk_foot,
            avg_foot,
            lk_foot,
            p.kills_veh,
            p.deaths_veh,
            kd_veh,
            p.tk_veh,
            p.vkills_foot,
            p.vkills_veh,
            p.shots_veh,
            p.hits_taken_veh,
            p.hits_dealt_veh,
            spk_veh,
            avg_veh,
            lk_veh,
            top_weapon,
            weapon_json,
            p.suicides,
            js_round(p.time_played),
            to_fixed(p.distance_run / 1000, 1),
            _json(p.vehicle_kills) if p.vehicle_kills else '',
            p.longest_foot_weapon if p.kill_dist_foot else '',
            p.longest_veh_weapon if p.kill_dist_veh else '',
            p.times_unconscious,
            js_round(p.time_unconscious),
            best_multikill(p.kill_frames, capture),
        ])

    rows.sort(key=lambda r: r[7], reverse=True)  # stable, like the script's sort
    # A Zeus's kills and an unnamed noise entity's are not on any row, so they are
    # not offered for checking either.
    listed = {p.ent.id for p in players.values()
              if not p.is_zeus and not (p.is_unknown and js_round(p.time_played) < MIN_UNKNOWN_PLAYTIME_S)}
    far = sorted((k for k in far if k['ki'] in listed), key=lambda k: -k['d'])
    # The mission's farthest kills, and also each player's own longest: the number
    # that stands next to their name on the dashboard is that one, and it must
    # be checkable even when the mission had farther kills than a short list holds.
    # Struck kills do not take a place: striking the farthest lets the next one
    # fill up. They are all kept, flagged, so they can be put back.
    live = [k for k in far if not k['x']]
    keep, seen = live[:FAR_KILL_KEEP], set()
    for k in live[FAR_KILL_KEEP:]:
        if k['ki'] not in seen and not any(j['ki'] == k['ki'] for j in keep):
            keep.append(k)
        seen.add(k['ki'])
    far = keep + [k for k in far if k['x']]
    return {'mission': label, 'world': rec.world_name, 'rows': rows, 'kills': kills, 'far': far}


def _json(value) -> str:
    """`JSON.stringify`: no spaces, non-ASCII left as it is."""
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------

def _cell(v):
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def to_csv(rows) -> str:
    """The header and every row as CSV — the shape the dashboard already parses.

    Newlines inside a field would break the dashboard's line-by-line reader, so
    there are none: player names and roles come out of OCAP on one line, and the
    JSON columns are compact.
    """
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(HEADER)
    for row in rows:
        writer.writerow([_cell(v) for v in row])
    return out.getvalue()


_DATE_SUFFIX = re.compile(r' \((\d{4}-\d{2}-\d{2})\)$')

# Longest name an admin may give a mission or a campaign.
MAX_NAME = 120


def split_label(label: str) -> tuple:
    """`'Op Name (2025-11-29)'` → `('Op Name', ' (2025-11-29)')`; no date → `(label, '')`."""
    m = _DATE_SUFFIX.search(label or '')
    if not m:
        return (label or '', '')
    return (label[:m.start()], m.group(0))


def effective_label(label: str, override) -> str:
    """The mission's name as shown: the admin's name if there is one, with the
    date from the recording kept — the date is the recording's, not a choice."""
    if not override:
        return label or ''
    return override + split_label(label)[1]


def clean_name(text, *, what: str = 'name') -> str:
    """A name from a form: one line, trimmed, bounded. Raises `ValueError`.

    The date suffix is taken off if it was typed (people copy the whole cell),
    because `effective_label()` adds the recording's own.
    """
    name = ' '.join((text or '').split())
    name = split_label(name)[0].strip()
    if len(name) > MAX_NAME:
        raise ValueError(f'The {what} can be at most {MAX_NAME} characters.')
    return name


def plan_name_edits(current: dict, submitted) -> dict:
    """What a submitted names form changes: `{recording id: new name or None}`.

    `current` is `{recording id: the name set now, or None}`; `submitted` is
    `(recording id, text)` for every row of the form. Only rows whose name really
    changed are returned — an empty box where there was no name is not a change —
    and a `None` means "back to OCAP's name". Ids the form carries that no recording
    has are ignored. Raises `ValueError` for the first name that is not valid, before
    anything is returned, so a form with one bad name writes none of them.
    """
    changes = {}
    for ocap_id, text in submitted:
        if ocap_id not in current:
            continue
        try:
            name = clean_name(text) or None
        except ValueError as e:
            raise ValueError(f'“{" ".join((text or "").split())[:40]}…”: {e}')
        if name != (current[ocap_id] or None):
            changes[ocap_id] = name
    return changes


def apply_name(rows: list, override) -> list:
    """The mission's rows with the Mission column carrying the shown name."""
    if not override:
        return rows
    out = []
    for row in rows:
        row = list(row)
        row[1] = effective_label(row[1], override)
        out.append(row)
    return out


def csv_from_stored(stored) -> str:
    """The CSV for stored missions: `(rows_json, name_override)` pairs, or bare JSON."""
    rows = []
    for item in stored:
        text, override = item if isinstance(item, tuple) else (item, None)
        rows.extend(apply_name(json.loads(text), override))
    # Missions counted before a column was added are shorter; they read as empty there.
    rows = [r + [''] * (len(HEADER) - len(r)) if len(r) < len(HEADER) else r for r in rows]
    return to_csv(rows)


# ---------------------------------------------------------------------------
# Names from the old sheet
# ---------------------------------------------------------------------------

def source_key(name: str) -> str:
    """A recording's identity from a sheet's `Source File`: `X.json.gz` and
    `X (5).json.gz` (a downloaded duplicate) are both `X`."""
    name = (name or '').strip()
    for suffix in ('.json.gz', '.json'):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return re.sub(r' \(\d+\)$', '', name).strip()


def parse_sheet_names(text: str) -> dict:
    """`{source key: Mission label}` from the old sheet's CSV export.

    Every row of a mission repeats its name, so the first one is taken. Raises
    `ValueError` when the text has no `Source File` and `Mission` columns —
    which is what pasting the wrong sheet looks like.
    """
    reader = csv.reader(io.StringIO((text or '').lstrip('\ufeff')))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise ValueError('That file is empty.')
    try:
        src, mis = header.index('Source File'), header.index('Mission')
    except ValueError:
        raise ValueError('That is not the sheet: it needs a "Source File" and a '
                         '"Mission" column.')
    found = {}
    for row in reader:
        if len(row) <= max(src, mis):
            continue
        key, label = source_key(row[src]), row[mis].strip()
        if key and label and key not in found:
            found[key] = label
    return found


def plan_name_import(sheet: dict, missions) -> list:
    """What importing the sheet's names would change.

    `missions` are records with `ocap_id`, `filename`, `label` (the recording's
    own name) and `name_override`. Returns one dict per recording whose sheet
    name differs from the recording's — `ocap_id`, `filename`, `ocap`, `sheet`
    (both without the date) and `current` (an override already there, or None).
    A recording the sheet does not know, or names the same, is left out.
    """
    changes = []
    for m in missions:
        sheet_label = sheet.get(source_key(m['filename']))
        if not sheet_label:
            continue
        theirs = split_label(sheet_label)[0].strip()
        # The recording's own name: what was counted, or — before it has been
        # counted — what the OCAP list calls it.
        ours = split_label(m['label'] or m['mission'] or '')[0].strip()
        if not theirs or theirs == ours:
            continue
        changes.append({'ocap_id': m['ocap_id'], 'filename': m['filename'],
                        'ocap': ours, 'sheet': theirs, 'current': m['name_override']})
    return changes


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------

MAX_PATTERNS = 50


def clean_patterns(text) -> list:
    """One pattern per line, trimmed, blanks and repeats dropped, bounded."""
    out = []
    for line in (text or '').splitlines():
        line = ' '.join(line.split())
        if not line or line in out:
            continue
        if len(line) > MAX_NAME:
            raise ValueError(f'A pattern can be at most {MAX_NAME} characters.')
        out.append(line)
    if len(out) > MAX_PATTERNS:
        raise ValueError(f'At most {MAX_PATTERNS} patterns.')
    return out


def campaigns_document(campaigns, labels: dict) -> dict:
    """The dashboard's `campaigns.json`, built from the database.

    `campaigns` are `{name, patterns, extra, ocap_ids}`; `labels` maps a recording
    id to the name it is currently shown under, so renaming a mission moves it
    in every campaign without anyone editing a list. `extra` is mission labels
    that matched no recording when they were imported (a mission not counted
    yet); they are passed through unchanged.
    """
    doc = {}
    for c in campaigns:
        missions = [labels[i] for i in sorted(c['ocap_ids']) if i in labels]
        for label in c.get('extra') or ():
            if label not in missions:
                missions.append(label)
        entry = {}
        if missions:
            entry['missions'] = missions
        if c.get('patterns'):
            entry['patterns'] = list(c['patterns'])
        doc[c['name']] = entry
    return doc


def parse_campaigns_json(text: str) -> list:
    """The old `campaigns.json` as `[{name, missions, patterns}]`.

    Keys starting with `_` are comments. Raises `ValueError` for anything that is
    not the expected shape — this reads a file somebody pasted.
    """
    try:
        doc = json.loads(text or '')
    except json.JSONDecodeError as e:
        raise ValueError(f'That is not valid JSON ({e.msg}, line {e.lineno}).')
    if not isinstance(doc, dict):
        raise ValueError('campaigns.json is an object of campaign names.')
    out = []
    for name, body in doc.items():
        if str(name).startswith('_'):
            continue
        if not isinstance(body, dict):
            raise ValueError(f'Campaign "{name}" should be an object with "missions" and/or "patterns".')
        missions, patterns = body.get('missions') or [], body.get('patterns') or []
        if not (isinstance(missions, list) and isinstance(patterns, list)
                and all(isinstance(x, str) for x in missions + patterns)):
            raise ValueError(f'Campaign "{name}": "missions" and "patterns" are lists of text.')
        out.append({'name': clean_name(name, what='campaign name'),
                    'missions': [m.strip() for m in missions if m.strip()],
                    'patterns': [p.strip() for p in patterns if p.strip()]})
    return out


def match_missions(labels: list, shown: dict) -> tuple:
    """Split mission labels into recordings and leftovers.

    `shown` maps recording id → the label it is shown under. Returns
    `(ids, unmatched)`: the ids whose label is in `labels`, and the labels no
    recording carries.
    """
    by_label = {}
    for ocap_id, label in shown.items():
        by_label.setdefault(label, []).append(ocap_id)
    ids, unmatched = [], []
    for label in labels:
        if label in by_label:
            ids.extend(by_label[label])
        else:
            unmatched.append(label)
    return sorted(set(ids)), unmatched


# ---------------------------------------------------------------------------
# Players: names that belong together, and which unit they are in
# ---------------------------------------------------------------------------

# The units the dashboard knows (colours and ordering live there). Mirrors
# `UNIT_ORDER` in the dashboard's tracker.js: a new unit needs a colour there
# anyway, so it is added in both places.
UNITS = ('2nd USC', 'CNTO', 'PXG', 'TFP', 'SKUA')

# A stored unit of '' means "Unknown": the dashboard's `null` in unit_overrides.json,
# which stops its automatic guess. No stored row at all means "automatic".
UNKNOWN_UNIT = ''


# Players the dashboard starts from when it guesses a unit — `UNIT_SEEDS` in
# tracker.js. Keep the two in step: this is only for showing what Automatic is.
UNIT_SEEDS = {
    '2nd USC': ('[2nd USC] Panzerfanlol', '[2nd USC] Cpt. Eddie'),
    'CNTO': ('Pricepole', 'Clarke', 'Fishgrit'),
    'PXG': ('Darkling', 'Aquafox', 'Lortmil', 'glyn'),
    'TFP': ('Sindh', 'loltorres9', 'DickSplash', 'Gortarius', 'Akarin', 'LucasC', 'Ocke', 'najt'),
    'SKUA': ('SPC. Green Giant', 'WC. Picker', 'PFC. Bearz'),
}


def guess_units(stored) -> dict:
    """The unit the dashboard guesses for each recorded name — what **Automatic** means.

    A port of `classifyPlayerUnits()` in tracker.js, which the public page runs in
    the browser: seeds first, then a `[Tag]` in the name, then players spread to
    the unit of the squads they share (same recording and group, Zeus left out)
    once that unit is at least 99% of what they shared. Names are as recorded;
    a player with no guess is not in the result. `stored` is as for
    `known_players()`.
    """
    squads = {}
    names = {}
    for item in stored:
        text = item[0] if isinstance(item, tuple) else item
        for row in json.loads(text):
            name = row[3]
            if not name:
                continue
            names[name] = None
            source, group = row[0], row[5]
            role = row[6] if len(row) > 6 else ''
            if not source or not group or 'zeus' in (group or '').lower() or 'zeus' in (role or '').lower():
                continue
            squads.setdefault((source, group), set()).add(name)

    co_occurrence = {}
    for members in squads.values():
        for a in members:
            for b in members:
                if a != b:
                    neighbours = co_occurrence.setdefault(a, {})
                    neighbours[b] = neighbours.get(b, 0) + 1

    units = {p: unit for unit, players in UNIT_SEEDS.items() for p in players}
    for name in names:
        if name in units:
            continue
        m = re.match(r'^\[([^\]]+)\]', name)
        if not m:
            continue
        tag = m.group(1).lower()
        for unit in UNITS:
            if unit.lower() == tag or unit.lower().replace('2nd ', '', 1) == tag:
                units[name] = unit
                break

    changed = True
    while changed:
        changed = False
        for name in names:
            if name in units:
                continue
            scores, total = {}, 0
            for neighbour, count in co_occurrence.get(name, {}).items():
                unit = units.get(neighbour)
                if unit:
                    scores[unit] = scores.get(unit, 0) + count
                    total += count
            if not total:
                continue
            best_unit, best_score = max(scores.items(), key=lambda kv: kv[1])
            if best_score / total >= 0.99:
                units[name] = best_unit
                changed = True
    return {name: units[name] for name in names if name in units}


def clean_player(text) -> str:
    """A player name from a form or a file: one line, trimmed, bounded.

    Not otherwise changed — the dashboard matches these against the names in the
    statistics character for character, so `[2nd USC] Trp. Dusty` stays that.
    """
    name = ' '.join((text or '').split())
    if len(name) > MAX_NAME:
        raise ValueError(f'A player name can be at most {MAX_NAME} characters.')
    return name


def link_names(aliases: dict, names, display: str) -> dict:
    """Make `names` one person, shown as `display`. Returns the new alias map.

    The aliases are `{name as recorded → name shown}`. Linking pulls in
    everything already tied to any of the names — in either direction — so that
    linking A to B when B is already shown as C moves all three, and the map
    never holds a chain (the dashboard does not follow one).
    """
    display = clean_player(display)
    members = {clean_player(n) for n in names if clean_player(n)}
    if not display:
        raise ValueError('Pick the name to show.')
    members.add(display)
    changed = True
    while changed:
        changed = False
        for alias, canonical in aliases.items():
            if (alias in members) != (canonical in members):
                members.update((alias, canonical))
                changed = True
    out = {a: c for a, c in aliases.items() if a not in members and c not in members}
    for member in members:
        if member != display:
            out[member] = display
    return out


def default_display(names, known: list) -> str:
    """Which of `names` to suggest showing: the one in the most missions, then
    the most kills, then the first alphabetically. `known` is `known_players()`.
    A name that is not in the statistics counts as no missions."""
    stats = {k['name']: k for k in known}

    def key(name):
        entry = stats.get(name, {})
        return (-entry.get('missions', 0), -entry.get('kills', 0), name.lower(), name)
    names = [n for n in names if n]
    return min(names, key=key) if names else ''


_RANKS = {
    'pvt', 'pte', 'rct', 'spr', 'trp', 'gnr', 'ldg', 'lcpl', 'cpl', 'sgt', 'psgt', 'ssgt', 'wo',
    'wo1', 'wo2', 'cwo', 'sgm', 'maj', 'capt', 'cpt', 'col', 'lt', 'lt col', 'ltc', 'brig', 'gen',
    'qms', 'rsm', 'csm', 'sfc', 'msg', 'spc', 'pfc', 'ens', 'ltjg', 'cdr', 'adm', 'cmdr', 'ssg',
}


def match_key(name: str) -> str:
    """A name reduced to what identifies the person: `[2nd USC] Trp. Dusty` and a
    Discord nickname `Dusty` both become `dusty`. Unit tags in brackets, a leading
    rank and every non-alphanumeric character are dropped."""
    text = re.sub(r'\[[^\]]*\]', ' ', name or '')
    words = [w for w in re.split(r'[\s.]+', text) if w]
    while len(words) > 1 and words[0].lower().rstrip('.') in _RANKS:
        words.pop(0)
    return re.sub(r'[^a-z0-9]', '', ''.join(words).lower())


def suggest_members(names, members: list, limit: int = 3, taken=()) -> list:
    """The Discord members a player might be, best first.

    `names` is every name the player is recorded under; `members` is
    `(user id, [labels])` — display name, username, nickname. A member matches
    when their key equals a name's key, or one contains the other (both at least
    three characters), or the two are close (`difflib` ratio of 0.8). Returns
    `[(user id, score)]`; ids in `taken` are another player's already."""
    import difflib
    keys = [k for k in (match_key(n) for n in names) if len(k) >= 2]
    found = []
    for user_id, labels in members:
        if user_id in taken:
            continue
        best = 0.0
        for label in labels:
            mk = match_key(label)
            if len(mk) < 2:
                continue
            for k in keys:
                if k == mk:
                    score = 1.0
                elif len(k) >= 3 and len(mk) >= 3 and (k in mk or mk in k):
                    score = 0.9
                else:
                    score = difflib.SequenceMatcher(None, k, mk).ratio()
                best = max(best, score)
        if best >= 0.8:
            found.append((user_id, best))
    found.sort(key=lambda f: -f[1])
    return found[:limit]


DEFAULT_ROLE_NAMES = ('member', 'veteran', 'recruit', 'cadet')


def default_roles(roles, names=DEFAULT_ROLE_NAMES) -> list:
    """The ids of the roles the Players page filters by until told otherwise.
    `roles` is `(role id, role name)`; names are matched ignoring case."""
    wanted = {n.lower() for n in names}
    return [rid for rid, name in roles if (name or '').strip().lower() in wanted]


def passes_role_filter(role_ids, selected, has_member: bool, include_unmapped: bool) -> bool:
    """Whether a player is listed. A player with no Discord member to read roles
    from (not mapped, or no longer on the server) is listed only when
    `include_unmapped`; otherwise they need one of the `selected` roles."""
    if not has_member:
        return include_unmapped
    return bool(set(role_ids) & set(selected))


def unlink_name(aliases: dict, name: str) -> dict:
    """Take one name out of its person; the person's other names stay linked."""
    return {a: c for a, c in aliases.items() if a != name}


def alias_groups(aliases: dict) -> dict:
    """`{shown name: [names recorded under it]}`, both sorted."""
    groups = {}
    for alias, canonical in aliases.items():
        groups.setdefault(canonical, []).append(alias)
    return {k: sorted(v, key=str.lower) for k, v in sorted(groups.items(), key=lambda kv: kv[0].lower())}


def known_players(stored) -> list:
    """Every name that appears in the counted statistics.

    `stored` is `(rows JSON, ...)` per mission as `database.kt_rows()` returns it.
    Each result has the name, how many missions it appears in, its kills, and
    the label of the latest one. Zeus rows are not told apart here: a name is
    a name.
    """
    seen = {}
    for item in stored:
        text = item[0] if isinstance(item, tuple) else item
        for row in json.loads(text):
            name = row[3]
            if not name:
                continue
            entry = seen.setdefault(name, {'name': name, 'missions': set(), 'kills': 0, 'last': ''})
            entry['missions'].add(row[0])
            entry['kills'] += (row[7] or 0) + (row[17] or 0)
            entry['last'] = row[1]
    return sorted(({'name': e['name'], 'missions': len(e['missions']), 'kills': e['kills'],
                    'last': e['last']} for e in seen.values()),
                  key=lambda e: e['name'].lower())


def player_missions(stored, aliases: dict) -> list:
    """Missions played per player, with linked names counted as one.

    Every recording counts as a mission — there is no duration, campaign or test
    filter, because a test recording is deleted on the OCAP server rather than
    detected here. A player's count is the number of **distinct recordings** any
    of their names appears in, so two names of one person in the same mission
    are one mission, not two.

    `aliases` is `{name as recorded → name shown}`. Each result has `name` (as
    shown), `names` (as recorded, sorted), `missions`, `kills` and `last` (the
    label of the latest mission), sorted by name. Zeus rows count like any other.
    """
    seen = {}
    for item in stored:
        text = item[0] if isinstance(item, tuple) else item
        for row in json.loads(text):
            recorded = row[3]
            if not recorded:
                continue
            shown = aliases.get(recorded, recorded)
            entry = seen.setdefault(shown, {'name': shown, 'names': set(), 'missions': set(),
                                            'kills': 0, 'last': ''})
            entry['names'].add(recorded)
            entry['missions'].add(row[0])
            entry['kills'] += (row[7] or 0) + (row[17] or 0)
            entry['last'] = row[1]
    return sorted(({'name': e['name'], 'names': sorted(e['names'], key=str.lower),
                    'missions': len(e['missions']), 'kills': e['kills'], 'last': e['last']}
                   for e in seen.values()), key=lambda e: e['name'].lower())


def clean_discord_id(text) -> str:
    """A Discord user id from a form: digits only. Returns `''` for empty input."""
    text = (text or '').strip()
    if text.startswith('<@') and text.endswith('>'):
        text = text[2:-1].lstrip('!')
    if not text:
        return ''
    if not text.isdigit() or not 15 <= len(text) <= 22:
        raise ValueError('A Discord user id is a number of 17 to 20 digits.')
    return text


def clean_unit(value):
    """A unit choice from a form. Returns `None` (automatic), `''` (Unknown) or a unit."""
    value = (value or '').strip()
    if value in ('', 'auto'):
        return None
    if value == 'unknown':
        return UNKNOWN_UNIT
    if value in UNITS:
        return value
    raise ValueError(f'"{value}" is not one of the units.')


def parse_aliases_json(text: str) -> dict:
    """The old `player_aliases.json` as `{name → name shown}`; `_` keys are comments."""
    try:
        doc = json.loads(text or '')
    except json.JSONDecodeError as e:
        raise ValueError(f'That is not valid JSON ({e.msg}, line {e.lineno}).')
    if not isinstance(doc, dict):
        raise ValueError('player_aliases.json is an object of names.')
    out = {}
    for alias, canonical in doc.items():
        if str(alias).startswith('_'):
            continue
        if not isinstance(canonical, str):
            raise ValueError(f'"{alias}" should map to a name.')
        alias, canonical = clean_player(alias), clean_player(canonical)
        if alias and canonical and alias != canonical:
            out[alias] = canonical
    # No chains: whatever a name points at is followed to the end.
    for alias in list(out):
        seen = {alias}
        while out[alias] in out and out[alias] not in seen:
            seen.add(out[alias])
            out[alias] = out[out[alias]]
    return out


def parse_unit_overrides_json(text: str) -> dict:
    """The old `unit_overrides.json` as `{player → unit}`; `null` becomes Unknown.

    A unit the dashboard does not have is refused, not guessed at.
    """
    try:
        doc = json.loads(text or '')
    except json.JSONDecodeError as e:
        raise ValueError(f'That is not valid JSON ({e.msg}, line {e.lineno}).')
    if not isinstance(doc, dict):
        raise ValueError('unit_overrides.json is an object of player names.')
    out = {}
    for player, unit in doc.items():
        if str(player).startswith('_'):
            continue
        if unit is not None and unit not in UNITS:
            raise ValueError(f'"{player}": "{unit}" is not one of {", ".join(UNITS)}.')
        out[clean_player(player)] = UNKNOWN_UNIT if unit is None else unit
    return out


def unit_overrides_document(overrides: dict) -> dict:
    """The dashboard's `unit_overrides.json`: Unknown is `null`."""
    return {player: (unit or None) for player, unit in sorted(overrides.items(), key=lambda kv: kv[0].lower())}


# ---------------------------------------------------------------------------
# Checking the farthest kills
# ---------------------------------------------------------------------------

def far_listing(missions, limit: int = None) -> list:
    """The farthest kills across all missions, farthest first, for the admin page.

    `missions` is `[(ocap_id, shown mission name, far list)]` — the far list as
    `compute()` returns it. Each entry gets an `id` (`"<ocap id>|<kill key>"`),
    the one thing the form sends back, and the mission it was in. Struck kills
    stay in the list, flagged, because that is where they are put back.
    """
    out = []
    for ocap_id, mission, far in missions:
        for k in far or ():
            try:
                key = kill_key(k['f'], k['ki'], k['vi'])
                distance = float(k['d'])
            except (KeyError, TypeError, ValueError):
                continue
            out.append({**k, 'id': f'{ocap_id}|{key}', 'ocap_id': ocap_id, 'key': key,
                        'd': distance, 'mission': mission, 'x': bool(k.get('x'))})
    out.sort(key=lambda k: (-k['d'], k['ocap_id'], k['f']))
    return out if limit is None else out[:limit]


def plan_strikes(listed, checked, entries) -> list:
    """What the strike form changes: `[(ocap_id, kill_key, struck, entry)]`.

    `listed` are the ids the page showed, `checked` the ones left ticked as
    *struck*, `entries` the listing the page was built from (by id). A kill is
    changed only if its tick differs from what is stored, and only if it was on the
    page: a hand-edited form cannot strike a kill nobody was shown.
    """
    by_id = {e['id']: e for e in entries}
    checked = set(checked)
    changes = []
    for kill_id in dict.fromkeys(listed):
        entry = by_id.get(kill_id)
        if entry is None:
            continue
        if (kill_id in checked) != entry['x']:
            changes.append((entry['ocap_id'], entry['key'], kill_id in checked, entry))
    return changes


# ---------------------------------------------------------------------------
# The dashboard, served by the bot
# ---------------------------------------------------------------------------

PAGE_ASSETS = '/stats/assets/'


def stats_page(template: str, player: str = '', icon: str = '', bar: str = '') -> str:
    """`dashboard/index.html` as the bot serves it, for one player or for none.

    Two things change. The page's own files are named by absolute path, because
    it is answered at `/stats/<player>` and a relative `tracker.js` would resolve
    to `/stats/tracker.js`. And a player's page gets a preview (Open Graph) and a
    one-line script that rewrites the address to `?player=` before `tracker.js`
    runs — the dashboard already reads that, so a path link is only a nicer way
    to spell it. The name is escaped for HTML and for the script, and a name that
    is not clean is treated as no name rather than an error: this is a public URL.
    `bar` is HTML the caller has already made safe, put at the top of the body.
    """
    page = (template.replace('href="favicon.svg"', f'href="{PAGE_ASSETS}favicon.svg"')
                    .replace('src="tracker.js"', f'src="{PAGE_ASSETS}tracker.js"'))
    from html import escape
    if bar:
        page = page.replace('<body>', '<body>\n' + bar, 1)
    if icon:
        # The site's own logo as the tab icon, in place of the dashboard's crosshair.
        mark = escape(icon, quote=True)
        page = page.replace(f'<link rel="icon" type="image/svg+xml" href="{PAGE_ASSETS}favicon.svg">',
                            f'<link rel="icon" href="{mark}">\n<link rel="apple-touch-icon" href="{mark}">')
    try:
        player = clean_player(player)
    except ValueError:
        player = ''
    if not player:
        return page
    title = escape(f'{player} · TFP Kill Tracker', quote=True)
    # `</` can't end the script and `<!--` can't open a comment inside it.
    literal = json.dumps(player).replace('<', '\\u003c')
    head = (f'<meta property="og:title" content="{title}">\n'
            f'<meta property="og:description" content="Career statistics for {escape(player, quote=True)}">\n'
            f'<script>history.replaceState(null, "", "/stats?player=" + encodeURIComponent({literal}));</script>\n')
    return page.replace('</head>', head + '</head>', 1)


def claimable_players(known, aliases) -> list:
    """The names a member can claim: each player as the dashboard shows them.

    `known` is `known_players()`; a linked group is one player, under the name
    it is shown as, so a member claims a person rather than one spelling of them.
    """
    return sorted({aliases.get(k['name'], k['name']) for k in known}, key=str.lower)


def valid_claim(text, claimable) -> str:
    """The claimed name as it is in `claimable`, or `ValueError` with a sentence."""
    wanted = clean_player(text)
    if not wanted:
        raise ValueError('Pick the player you are.')
    for name in claimable:
        if name == wanted:
            return name
    folded = [n for n in claimable if n.lower() == wanted.lower()]
    if len(folded) == 1:
        return folded[0]
    raise ValueError('There is no player with that name in the statistics.')
