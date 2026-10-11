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
RULES_VERSION = 1

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
]

# --- counting thresholds (the Apps Script's constants, unchanged) -----------

# On-foot kills beyond this still count as kills but not for distance stats.
MAX_FOOT_KILL_DIST = 1500
# OCAP v5+ logs phantom shots whose target is thousands of metres away.
MAX_SHOT_DIST = 800
# End-of-mission teamkill suppression: players celebrate by shooting each other.
EOM_WINDOW_S = 300           # hard cutoff: the last 5 minutes
EOM_RANGE_WINDOW_S = 420     # extended cutoff for close-range kills (7 minutes)
EOM_CLOSE_M = 15             # grenade / pistol range
EOM_BURST_WINDOW = 60        # rolling window for burst detection, in frames
EOM_BURST_COUNT = 3          # TKs inside the window that make a burst
EOM_BURST_MAX_FROMEND_S = 600  # the burst rule only applies in the last 10 minutes
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
                alive.append(1 if len(pos) > 2 and pos[2] == 1 else 0)
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
    """Read a `.json.gz` (or plain `.json`) file from disk."""
    import gzip

    opener = gzip.open if path.endswith('.gz') else open
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
                 'hits_dealt_veh', 'kill_dist_veh', 'weapon_kills', 'vehicle_kills',
                 'suicides', 'is_unknown', 'is_zeus', 'time_played', 'distance_run')

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
        self.weapon_kills = {}
        self.vehicle_kills = []
        self.suicides = 0
        self.is_unknown = False
        self.is_zeus = False
        self.time_played = ent.n * capture_delay
        self.distance_run = 0

    def zero(self) -> None:
        """A Zeus has no combat stats."""
        self.kills_foot = self.deaths_foot = self.tk_foot = 0
        self.shots_foot = self.hits_taken_foot = self.hits_dealt_foot = 0
        self.kills_veh = self.deaths_veh = self.tk_veh = 0
        self.vkills_foot = self.vkills_veh = 0
        self.shots_veh = self.hits_taken_veh = self.hits_dealt_veh = 0
        self.suicides = 0
        self.distance_run = 0
        self.kill_dist_foot = []
        self.kill_dist_veh = []
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


def compute(rec: Recording, source_name: str) -> dict:
    """Count one recording. Returns `{'mission', 'world', 'rows', 'kills'}`.

    `rows` follow `HEADER`, sorted by on-foot kills. `kills` is every counted
    enemy kill by a (non-Zeus) player — the number to hold against the OCAP
    server's own `player_kill_count`.
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
                    if killer_in_vehicle:
                        killer_p.kills_veh += 1
                        if not crew:
                            killer_p.kill_dist_veh.append(distance)
                    else:
                        killer_p.kills_foot += 1
                        # Implausible on-foot distances (a launcher logged with
                        # the wrong weapon, OCAP artefacts) are kills without a
                        # distance.
                        if not crew and distance <= MAX_FOOT_KILL_DIST:
                            killer_p.kill_dist_foot.append(distance)
                        weapon = killer_info[1] if len(killer_info) > 1 and killer_info[1] else 'Unknown'
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
        ])

    rows.sort(key=lambda r: r[7], reverse=True)  # stable, like the script's sort
    return {'mission': label, 'world': rec.world_name, 'rows': rows, 'kills': kills}


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


def csv_from_stored(stored) -> str:
    """The CSV for a list of stored missions, each one a JSON array of rows."""
    rows = []
    for text in stored:
        rows.extend(json.loads(text))
    return to_csv(rows)
