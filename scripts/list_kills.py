"""List the longest kills in OCAP recordings, one line per kill, so they can be judged by eye.

    python scripts/list_kills.py [-n 10] [--all-sides] recording.json.gz [more ...]

For each kill the OCAP event says who, with what and from how far. This also
measures the distance itself from the two entities' recorded positions at that
frame, and says whether the Kill Tracker would count the kill towards the
distance statistics. A big gap between `ocap` and `measured` means OCAP's number
is not to be trusted; `crew` / `foot>1500` mean the Kill Tracker already leaves
it out.

Diagnostic, not part of the bot: it loads each file whole (a few hundred MB of
RAM for a big recording).
"""

import argparse
import gzip
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from utils import killtracker as kt  # noqa: E402


def load(path):
    with open(path, 'rb') as f:
        magic = f.read(2)
    opener = gzip.open if magic == b'\x1f\x8b' else open
    with opener(path, 'rt', encoding='utf-8') as f:
        return json.load(f)


def pos_at(ent, frame):
    """(x, y, vehicle code) of an entity at a frame, or None."""
    positions = ent.get('positions')
    if not isinstance(positions, list):
        return None
    i = frame - (ent.get('startFrameNum') or 0)
    if i < 0 or i >= len(positions):
        return None
    p = positions[i]
    if not isinstance(p, list) or not p or not isinstance(p[0], list) or len(p[0]) < 2:
        return None
    try:
        return float(p[0][0]), float(p[0][1]), (p[3] if len(p) > 3 else 0)
    except (TypeError, ValueError):
        return None


def kills(doc, name):
    ents = {e.get('id'): e for e in doc.get('entities') or []}
    vehicle_kills = set()
    for ev in doc.get('events') or []:
        if len(ev) > 3 and ev[1] == 'killed' and ev[3]:
            v, k = ents.get(ev[2]), ents.get(ev[3][0])
            if v and k and v.get('type') == 'vehicle' and v.get('side') != k.get('side'):
                vehicle_kills.add((ev[3][0], ev[0]))
    for ev in doc.get('events') or []:
        if len(ev) < 5 or ev[1] != 'killed' or not ev[3]:
            continue
        frame, vid, info, dist = ev[0], ev[2], ev[3], ev[4] or 0
        kid = info[0]
        if kid == vid:
            continue
        k, v = ents.get(kid), ents.get(vid)
        if not k or not v:
            continue
        kp, vp = pos_at(k, frame), pos_at(v, frame)
        measured = math.hypot(kp[0] - vp[0], kp[1] - vp[1]) if kp and vp else None
        v_in_veh = any((pos_at(v, f) or (0, 0, 0))[2] not in (0, None) for f in (frame, frame - 1))
        crew = v.get('type') == 'unit' and (v_in_veh or (kid, frame) in vehicle_kills)
        k_in_veh = bool(kp and kp[2] not in (0, None))
        if v.get('type') != 'unit':
            why = 'vehicle'
        elif k.get('side') == v.get('side'):
            why = 'teamkill'
        elif crew:
            why = 'crew'
        elif not k_in_veh and dist > kt.MAX_FOOT_KILL_DIST:
            why = 'foot>1500'
        else:
            why = 'counts'
        yield {
            'file': name, 'frame': frame, 'minute': round(frame * (doc.get('captureDelay') or 1) / 60, 1),
            'killer': k.get('name') or '?', 'weapon': (info[1] if len(info) > 1 else '') or '?',
            'victim': v.get('name') or v.get('type') or '?', 'ocap': dist,
            'measured': measured, 'killer_in_vehicle': k_in_veh, 'why': why,
        }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+')
    ap.add_argument('-n', type=int, default=10, help='how many to list (default 10)')
    ap.add_argument('--all-sides', action='store_true', help='include teamkills and vehicles')
    a = ap.parse_args()
    rows = []
    for path in a.files:
        rows.extend(kills(load(path), os.path.basename(path)))
    if not a.all_sides:
        rows = [r for r in rows if r['why'] in ('counts', 'crew', 'foot>1500')]
    rows.sort(key=lambda r: -r['ocap'])
    print(f"{'ocap':>6} {'meas.':>6}  {'by':<22} {'weapon':<26} {'victim':<22} {'min':>5}  {'verdict':<10} file")
    for r in rows[:a.n]:
        m = f"{r['measured']:.0f}" if r['measured'] is not None else '-'
        veh = ' (in vehicle)' if r['killer_in_vehicle'] else ''
        print(f"{r['ocap']:>6.0f} {m:>6}  {r['killer'][:22]:<22} {r['weapon'][:26]:<26} {str(r['victim'])[:22]:<22} "
              f"{r['minute']:>5}  {r['why']:<10} {r['file']}{veh}")


if __name__ == '__main__':
    main()
