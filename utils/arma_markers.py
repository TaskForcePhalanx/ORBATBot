"""Every map marker vanilla Arma 3 ships, beyond the military set.

The list is the game's own `CfgMarkers`, read out of a running game (scope
above 0, icon under `\\A3\\`): the hand-drawn set, map locations, unit sizes,
respawn points, the Zeus markers and the flags. Each keeps its exact class
name (`arma`), so it exports as that marker and a marker copied back out of a
mission finds its way home.

**The drawings are this module's own.** Arma's icons are Bohemia's artwork, so
none of them is copied: each is drawn here, in the same 100 × 100 box as the
military markers, as a plain pictogram of what the marker means — a tree, a
church, a fuel pump. The national flags are the flags. The flags of Arma's
fictional factions and of organisations with their own emblem (NATO, the UN,
CSAT, the AAF and the rest) are a plain cloth with the faction's short name on
it rather than a copy of its emblem: the name is what tells them apart on a
plan, and it is the class name that decides what the mission draws.

Imports only the standard library — `utils/tacmap.py` merges these into `MARKERS`.
"""

import math

# -- line art: drawn twice in the item's ink, like the military markers -------

def _line(label, arma, icon, group, text=False):
    return {'label': label, 'arma': arma, 'text': text, 'icon': icon, 'group': group}


_HAND = 'Hand-drawn'
_LOC = 'Map locations'
_SIZE = 'Unit sizes'
_RESPAWN = 'Respawn'
_ZEUS = 'Zeus'
_FLAG = 'Flags'

LINE_MARKERS = {
    # Arma's older hand-drawn set: the same meanings as the military markers.
    'hd_dot': _line('Dot (drawn)', 'hd_dot', '<circle cx="50" cy="50" r="15" fill="currentColor"/>', _HAND),
    'hd_objective': _line('Objective (drawn)', 'hd_objective',
                          '<circle cx="50" cy="56" r="21"/><path d="M50,35 V13"/>', _HAND),
    'hd_flag': _line('Flag (drawn)', 'hd_flag',
                     '<path d="M31,20 V80"/><path d="M31,22 C45,16 58,30 71,22 L63,38 L71,54 '
                     'C58,62 45,48 31,54"/>', _HAND),
    'hd_arrow': _line('Arrow (drawn)', 'hd_arrow', '<path d="M50,20 L73,71 L50,59 L27,71 Z"/>', _HAND),
    'hd_ambush': _line('Ambush (drawn)', 'hd_ambush',
                       '<path d="M30,26 Q70,50 30,74"/><path d="M42,36 L52,30 M47,50 H59 M42,64 L52,70"/>',
                       _HAND),
    'hd_destroy': _line('Destroy (drawn)', 'hd_destroy', '<path d="M27,27 L73,73 M73,27 L27,73"/>', _HAND),
    'hd_start': _line('Start (drawn)', 'hd_start', '<path d="M29,25 L75,50 L29,75 Z"/>', _HAND),
    'hd_end': _line('End (drawn)', 'hd_end', '<path d="M29,29 H71 V71 H29 Z"/>', _HAND),
    'hd_pickup': _line('Pick up (drawn)', 'hd_pickup',
                       '<circle cx="50" cy="50" r="23"/><path d="M35,54 L50,37 L65,54 M50,37 V67"/>', _HAND),
    'hd_join': _line('Join (drawn)', 'hd_join', '<path d="M27,25 L50,50 L73,25 M50,50 V75"/>', _HAND),
    'hd_warning': _line('Warning (drawn)', 'hd_warning',
                        '<path d="M50,23 L77,71 H23 Z"/><path d="M50,40 V55"/>'
                        '<circle cx="50" cy="63" r="3" fill="currentColor"/>', _HAND),
    'hd_unknown': _line('Unknown (drawn)', 'hd_unknown',
                        '<path d="M35,40 A15,15 0 0 1 65,40 C65,53 50,53 50,63"/>'
                        '<circle cx="50" cy="74" r="4" fill="currentColor"/>', _HAND),

    # Map locations: what the game marks on its own map.
    'loc_tree': _line('Tree', 'loc_Tree',
                      '<circle cx="50" cy="40" r="18"/><path d="M50,58 V82"/>', _LOC),
    'loc_smalltree': _line('Small tree', 'loc_SmallTree',
                           '<circle cx="50" cy="48" r="12"/><path d="M50,60 V78"/>', _LOC),
    'loc_bush': _line('Bush', 'loc_Bush',
                      '<path d="M26,68 C24,52 38,46 43,55 C45,40 61,40 61,54 C68,46 80,54 75,68 Z"/>', _LOC),
    'loc_church': _line('Church', 'loc_Church',
                        '<path d="M50,16 V38 M41,25 H59"/><path d="M33,38 H67 V80 H33 Z"/>', _LOC),
    'loc_chapel': _line('Chapel', 'loc_Chapel',
                        '<path d="M50,20 V36 M44,27 H56"/><path d="M34,52 L50,38 L66,52 V78 H34 Z"/>', _LOC),
    'loc_cross': _line('Cross', 'loc_Cross', '<path d="M50,20 V80 M35,38 H65"/>', _LOC),
    'loc_rock': _line('Rock', 'loc_Rock', '<path d="M22,72 L35,44 L52,34 L68,45 L79,72 Z"/>', _LOC),
    'loc_bunker': _line('Bunker', 'loc_Bunker',
                        '<path d="M22,72 V56 Q50,28 78,56 V72 Z"/><path d="M39,57 H61"/>', _LOC),
    'loc_fountain': _line('Fountain', 'loc_Fountain',
                          '<path d="M24,62 H76 L69,78 H31 Z"/>'
                          '<path d="M50,62 V32 M50,38 Q38,40 36,56 M50,38 Q62,40 64,56"/>', _LOC),
    'loc_viewtower': _line('View tower', 'loc_ViewTower',
                           '<path d="M38,82 L45,34 M62,82 L55,34 M42,60 H58"/>'
                           '<path d="M38,34 H62 V22 H38 Z"/>', _LOC),
    'loc_lighthouse': _line('Lighthouse', 'loc_Lighthouse',
                            '<path d="M41,82 L45,38 H55 L59,82 Z"/><path d="M44,38 V26 H56 V38"/>'
                            '<path d="M28,28 H37 M63,28 H72"/>', _LOC),
    'loc_quay': _line('Pier', 'loc_Quay',
                      '<path d="M18,44 H82 M30,44 V66 M50,44 V66 M70,44 V66"/>'
                      '<path d="M18,76 Q29,69 40,76 T62,76 T84,76"/>', _LOC),
    'loc_fuelstation': _line('Fuel station', 'loc_Fuelstation',
                             '<path d="M30,28 H58 V82 H30 Z"/><path d="M36,36 H52 V50 H36 Z"/>'
                             '<path d="M58,42 H67 V68 Q67,76 74,70"/>', _LOC),
    'loc_hospital': _line('Hospital', 'loc_Hospital',
                          '<path d="M42,22 H58 V42 H78 V58 H58 V78 H42 V58 H22 V42 H42 Z"/>', _LOC),
    'loc_busstop': _line('Bus stop', 'loc_BusStop',
                         '<path d="M26,32 H74 V70 H26 Z M26,54 H74"/>'
                         '<circle cx="37" cy="75" r="5"/><circle cx="63" cy="75" r="5"/>', _LOC),
    'loc_transmitter': _line('Radio tower', 'loc_Transmitter',
                             '<path d="M38,82 L50,32 L62,82 M43,64 H57"/>'
                             '<path d="M34,26 Q50,12 66,26 M41,32 Q50,24 59,32"/>', _LOC),
    'loc_stack': _line('Chimney', 'loc_Stack',
                       '<path d="M41,82 L45,32 H55 L59,82 Z"/>'
                       '<path d="M52,26 Q45,20 52,14 Q59,8 54,4"/>', _LOC),
    'loc_ruin': _line('Ruin', 'loc_Ruin',
                      '<path d="M26,78 V46 L34,40 V54 L44,44 V78 M56,78 V58 L64,50 L74,58 V78 M20,78 H80"/>',
                      _LOC),
    'loc_tourism': _line('Hotel', 'loc_Tourism',
                         '<path d="M22,46 V78 M78,58 V78 M22,66 H78 M22,58 H78"/>'
                         '<path d="M28,50 H42 V58 H28 Z"/>', _LOC),
    'loc_watertower': _line('Water tower', 'loc_WaterTower',
                            '<path d="M33,22 H67 V46 H33 Z"/><path d="M38,46 L33,82 M62,46 L67,82 M35,64 H65"/>',
                            _LOC),
    'loc_power': _line('Power plant', 'loc_Power', '<path d="M57,18 L33,54 H50 L43,82 L69,44 H52 Z"/>', _LOC),
    'loc_powersolar': _line('Solar power plant', 'loc_PowerSolar',
                            '<path d="M24,62 L35,34 H76 L65,62 Z M55,34 L45,62 M30,48 H71"/>'
                            '<path d="M50,62 V80"/>', _LOC),
    'loc_powerwave': _line('Wave power plant', 'loc_PowerWave',
                           '<path d="M18,48 Q29,36 40,48 T62,48 T84,48"/>'
                           '<path d="M18,66 Q29,54 40,66 T62,66 T84,66"/>', _LOC),
    'loc_powerwind': _line('Wind power plant', 'loc_PowerWind',
                           '<path d="M50,82 V44 M50,44 V18 M50,44 L72,57 M50,44 L28,57"/>'
                           '<circle cx="50" cy="44" r="4" fill="currentColor"/>', _LOC),
    'loc_civildefense': _line('Civil defence', 'loc_CivilDefense',
                              '<path d="M24,24 H76 V76 H24 Z"/><path d="M50,32 L70,68 H30 Z"/>', _LOC),
    'loc_culturalproperty': _line('Cultural property', 'loc_CulturalProperty',
                                  '<path d="M50,20 L76,30 L50,82 L24,30 Z"/><path d="M24,30 L50,50 L76,30"/>',
                                  _LOC),
    'loc_dangerousforces': _line('Dangerous forces', 'loc_DangerousForces',
                                 '<circle cx="28" cy="50" r="9" fill="currentColor"/>'
                                 '<circle cx="50" cy="50" r="9" fill="currentColor"/>'
                                 '<circle cx="72" cy="50" r="9" fill="currentColor"/>', _LOC),
    'loc_safetyzone': _line('Safety zone', 'loc_SafetyZone',
                            '<path d="M25,25 H75 V75 H25 Z"/>'
                            '<path d="M25,50 L50,25 M25,75 L75,25 M50,75 L75,50"/>', _LOC),

    # Unit sizes on their own — the marks a NATO symbol carries above it.
    'group_0': _line('Fire team', 'group_0',
                     '<circle cx="50" cy="50" r="12"/><path d="M38,64 L62,36"/>', _SIZE),
    'group_1': _line('Squad', 'group_1', '<circle cx="50" cy="50" r="7" fill="currentColor"/>', _SIZE),
    'group_2': _line('Section', 'group_2',
                     '<circle cx="38" cy="50" r="7" fill="currentColor"/>'
                     '<circle cx="62" cy="50" r="7" fill="currentColor"/>', _SIZE),
    'group_3': _line('Platoon', 'group_3',
                     '<circle cx="28" cy="50" r="7" fill="currentColor"/>'
                     '<circle cx="50" cy="50" r="7" fill="currentColor"/>'
                     '<circle cx="72" cy="50" r="7" fill="currentColor"/>', _SIZE),
    'group_4': _line('Company', 'group_4', '<path d="M50,30 V70"/>', _SIZE),
    'group_5': _line('Battalion', 'group_5', '<path d="M40,30 V70 M60,30 V70"/>', _SIZE),
    'group_6': _line('Regiment', 'group_6', '<path d="M30,30 V70 M50,30 V70 M70,30 V70"/>', _SIZE),
    'group_7': _line('Brigade', 'group_7', '<path d="M38,34 L62,66 M62,34 L38,66"/>', _SIZE),
    'group_8': _line('Division', 'group_8', '<path d="M24,34 L46,66 M46,34 L24,66 M54,34 L76,66 M76,34 L54,66"/>',
                     _SIZE),
    'group_9': _line('Corps', 'group_9',
                     '<path d="M14,36 L32,64 M32,36 L14,64 M41,36 L59,64 M59,36 L41,64 M68,36 L86,64 '
                     'M86,36 L68,64"/>', _SIZE),
    'group_10': _line('Army', 'group_10',
                      '<path d="M8,38 L22,62 M22,38 L8,62 M31,38 L45,62 M45,38 L31,62 M55,38 L69,62 '
                      'M69,38 L55,62 M78,38 L92,62 M92,38 L78,62"/>', _SIZE),
    'group_11': _line('Army group', 'group_11',
                      '<path d="M4,40 L16,60 M16,40 L4,60 M22,40 L34,60 M34,40 L22,60 M44,40 L56,60 '
                      'M56,40 L44,60 M66,40 L78,60 M78,40 L66,60 M84,40 L96,60 M96,40 L84,60"/>', _SIZE),

    # Respawn points: a ring, and what respawns there.
    'respawn_unknown': _line('Respawn', 'respawn_unknown',
                             '<circle cx="50" cy="50" r="30"/>'
                             '<path d="M40,44 A10,10 0 0 1 60,44 C60,53 50,53 50,59"/>'
                             '<circle cx="50" cy="67" r="3" fill="currentColor"/>', _RESPAWN),
    'respawn_inf': _line('Infantry respawn', 'respawn_inf',
                         '<circle cx="50" cy="50" r="30"/><path d="M36,36 L64,64 M64,36 L36,64"/>', _RESPAWN),
    'respawn_motor': _line('Car respawn', 'respawn_motor',
                           '<circle cx="50" cy="50" r="30"/><path d="M33,56 V46 L40,38 H60 L67,46 V56 Z"/>'
                           '<circle cx="40" cy="60" r="4"/><circle cx="60" cy="60" r="4"/>', _RESPAWN),
    'respawn_armor': _line('Armour respawn', 'respawn_armor',
                           '<circle cx="50" cy="50" r="30"/><rect x="32" y="42" width="36" height="16" rx="8"/>',
                           _RESPAWN),
    'respawn_air': _line('Helicopter respawn', 'respawn_air',
                         '<circle cx="50" cy="50" r="30"/><path d="M30,38 H70 M50,38 V46"/>'
                         '<path d="M38,46 H58 Q64,46 64,54 Q64,60 56,60 H44 Q38,60 38,52 Z M64,52 H72"/>',
                         _RESPAWN),
    'respawn_plane': _line('Plane respawn', 'respawn_plane',
                           '<circle cx="50" cy="50" r="30"/>'
                           '<path d="M50,28 V72 M30,50 L50,44 L70,50 M40,68 H60"/>', _RESPAWN),
    'respawn_naval': _line('Naval respawn', 'respawn_naval',
                           '<circle cx="50" cy="50" r="30"/>'
                           '<path d="M50,30 V68 M42,38 H58 M34,58 Q36,68 50,68 Q64,68 66,58"/>', _RESPAWN),
    'respawn_para': _line('Airborne respawn', 'respawn_para',
                          '<circle cx="50" cy="50" r="30"/>'
                          '<path d="M32,48 Q50,26 68,48 Z M32,48 L50,66 L68,48 M50,48 V66"/>', _RESPAWN),

    # What Zeus can put down.
    'kia': _line('KIA', 'KIA',
                 '<path d="M50,22 V78 M36,38 H64 M32,78 H68"/>', _ZEUS),
    'minefield': _line('Minefield', 'Minefield',
                       '<path d="M20,32 H80 V68 H20 Z"/>'
                       '<circle cx="35" cy="50" r="6" fill="currentColor"/>'
                       '<circle cx="50" cy="50" r="6" fill="currentColor"/>'
                       '<circle cx="65" cy="50" r="6" fill="currentColor"/>', _ZEUS),
    'minefieldap': _line('Minefield (anti-personnel)', 'MinefieldAP',
                         '<path d="M20,32 H80 V68 H20 Z"/>'
                         '<circle cx="35" cy="52" r="5"/><circle cx="50" cy="52" r="5"/>'
                         '<circle cx="65" cy="52" r="5"/>'
                         '<path d="M35,47 V40 M50,47 V40 M65,47 V40"/>', _ZEUS),
}

# -- flags: full colour, drawn once ---------------------------------------------

_CLOTH = (14, 28, 72, 44)          # x, y, w, h of a flag inside the 100 box


def _rect(x, y, w, h, fill):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}"/>'


def _vertical(*colours):
    x, y, w, h = _CLOTH
    step = w / len(colours)
    return ''.join(_rect(round(x + n * step, 2), y, round(step + 0.01, 2), h, c)
                   for n, c in enumerate(colours))


def _horizontal(*colours, weights=None):
    x, y, w, h = _CLOTH
    weights = weights or [1] * len(colours)
    total = sum(weights)
    out, at = [], y
    for colour, weight in zip(colours, weights):
        part = h * weight / total
        out.append(_rect(x, round(at, 2), w, round(part + 0.01, 2), colour))
        at += part
    return ''.join(out)


def _nordic(field, cross, inner=None):
    x, y, w, h = _CLOTH
    out = _rect(x, y, w, h, field)
    out += _rect(x + 20, y, 10, h, cross) + _rect(x, y + 17, w, 10, cross)
    if inner:
        out += _rect(x + 23, y, 4, h, inner) + _rect(x, y + 20, w, 4, inner)
    return out


def _tag(field, ink, text):
    """A plain cloth with a short name on it — for a faction or organisation
    whose own flag is an emblem, which is not copied."""
    x, y, w, h = _CLOTH
    size = 16 if len(text) <= 4 else 12 if len(text) <= 6 else 10
    return (_rect(x, y, w, h, field) +
            f'<text x="50" y="{y + h / 2 + size * 0.36:.1f}" text-anchor="middle" '
            f'font-size="{size}" font-weight="700" fill="{ink}" '
            'font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif">'
            f'{text}</text>')


def _flag(label, arma, body):
    x, y, w, h = _CLOTH
    border = (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" '
              'stroke="#11161c" stroke-width="2"/>')
    return {'label': label, 'arma': arma, 'text': False, 'paint': True,
            'icon': body + border, 'group': _FLAG}


_RED, _WHITE, _BLUE, _GREEN = '#d52b1e', '#ffffff', '#0b3f8f', '#0b8a43'
_YELLOW, _BLACK, _SKY = '#f6c700', '#111111', '#5b92e5'


def _union_jack():
    x, y, w, h = _CLOTH
    return (_rect(x, y, w, h, '#012169') +
            f'<path d="M{x},{y} L{x + w},{y + h} M{x + w},{y} L{x},{y + h}" '
            'stroke="#ffffff" stroke-width="9"/>'
            f'<path d="M{x},{y} L{x + w},{y + h} M{x + w},{y} L{x},{y + h}" '
            'stroke="#c8102e" stroke-width="3"/>' +
            _rect(x + w / 2 - 7, y, 14, h, '#ffffff') + _rect(x, y + h / 2 - 7, w, 14, '#ffffff') +
            _rect(x + w / 2 - 4, y, 8, h, '#c8102e') + _rect(x, y + h / 2 - 4, w, 8, '#c8102e'))


def _usa():
    x, y, w, h = _CLOTH
    stripes = ''.join(_rect(x, round(y + n * h / 13, 2), w, round(h / 13 + 0.01, 2),
                            '#b22234' if n % 2 == 0 else '#ffffff') for n in range(13))
    canton = _rect(x, y, round(w * 0.4, 2), round(h * 7 / 13, 2), '#3c3b6e')
    stars = ''.join(f'<circle cx="{x + 3 + col * 4.6:.1f}" cy="{y + 2.6 + row * 4.2:.1f}" r="1" '
                    'fill="#ffffff"/>' for row in range(5) for col in range(6))
    return stripes + canton + stars


def _canada():
    x, y, w, h = _CLOTH
    leaf = ('<path d="M50,34 L53,42 L58,40 L56,50 L62,47 L60,53 L64,54 L57,59 L58,62 '
            'L51,61 L51,68 L49,68 L49,61 L42,62 L43,59 L36,54 L40,53 L38,47 L44,50 '
            'L42,40 L47,42 Z" fill="#d52b1e"/>')
    return (_rect(x, y, w, h, '#ffffff') + _rect(x, y, 18, h, '#d52b1e') +
            _rect(x + w - 18, y, 18, h, '#d52b1e') + leaf)


def _greece():
    x, y, w, h = _CLOTH
    stripes = ''.join(_rect(x, round(y + n * h / 9, 2), w, round(h / 9 + 0.01, 2),
                            '#0d5eaf' if n % 2 == 0 else '#ffffff') for n in range(9))
    canton = _rect(x, y, round(h * 5 / 9, 2), round(h * 5 / 9, 2), '#0d5eaf')
    cross = (_rect(x + 9.8 - 2.4, y, 4.9, round(h * 5 / 9, 2), '#ffffff') +
             _rect(x, y + 12.2 - 2.4, round(h * 5 / 9, 2), 4.9, '#ffffff'))
    return stripes + canton + cross


def _georgia():
    x, y, w, h = _CLOTH
    small = ''.join(_rect(cx - 4, cy - 1, 8, 2, '#e8112d') + _rect(cx - 1, cy - 4, 2, 8, '#e8112d')
                    for cx, cy in ((x + 15, y + 10), (x + w - 15, y + 10),
                                   (x + 15, y + h - 10), (x + w - 15, y + h - 10)))
    return (_rect(x, y, w, h, '#ffffff') + _rect(x + w / 2 - 4, y, 8, h, '#e8112d') +
            _rect(x, y + h / 2 - 4, w, 8, '#e8112d') + small)


def _eu():
    x, y, w, h = _CLOTH
    stars = ''.join(
        f'<circle cx="{50 + 13 * math.cos(n * math.pi / 6):.1f}" '
        f'cy="{y + h / 2 + 13 * math.sin(n * math.pi / 6):.1f}" r="1.8" fill="#ffcc00"/>'
        for n in range(12))
    return _rect(x, y, w, h, '#003399') + stars


def _portugal():
    x, y, w, h = _CLOTH
    return (_rect(x, y, w, h, '#ff0000') + _rect(x, y, round(w * 0.4, 2), h, '#006600') +
            f'<circle cx="{x + w * 0.4:.1f}" cy="{y + h / 2}" r="8" fill="#ffcc00"/>')


def _red_crystal():
    x, y, w, h = _CLOTH
    return (_rect(x, y, w, h, '#ffffff') +
            f'<path d="M50,{y + 5} L67,{y + 22} L50,{y + 39} L33,{y + 22} Z" fill="none" '
            'stroke="#d52b1e" stroke-width="6"/>')


FLAG_MARKERS = {
    'flag_nato': _flag('NATO', 'flag_NATO', _tag('#0b2b5c', '#ffffff', 'NATO')),
    'flag_csat': _flag('CSAT', 'flag_CSAT', _tag('#5a1f1f', '#f2d16b', 'CSAT')),
    'flag_aaf': _flag('Altis Armed Forces', 'flag_AAF', _tag('#4a5a2a', '#ffffff', 'AAF')),
    'flag_altis': _flag('Altis', 'flag_Altis', _tag('#ffffff', '#1d4f91', 'ALTIS')),
    'flag_altiscolonial': _flag('Altis (colonial)', 'flag_AltisColonial',
                                _tag('#1d4f91', '#ffffff', 'ALTIS')),
    'flag_fia': _flag('FIA', 'flag_FIA', _tag('#2f4f2f', '#ffffff', 'FIA')),
    'flag_eu': _flag('European Union', 'flag_EU', _eu()),
    'flag_un': _flag('United Nations', 'flag_UN', _tag('#5b92e5', '#ffffff', 'UN')),
    'flag_belgium': _flag('Belgium', 'flag_Belgium', _vertical(_BLACK, '#fdda24', '#ef3340')),
    'flag_canada': _flag('Canada', 'flag_Canada', _canada()),
    'flag_croatia': _flag('Croatia', 'flag_Croatia', _horizontal('#ff0000', _WHITE, '#171796')),
    'flag_czechrepublic': _flag('Czech Republic', 'flag_CzechRepublic',
                                _horizontal(_WHITE, '#d7141a') +
                                '<path d="M14,28 L50,50 L14,72 Z" fill="#11457e"/>'),
    'flag_denmark': _flag('Denmark', 'flag_Denmark', _nordic('#c8102e', _WHITE)),
    'flag_france': _flag('France', 'flag_France', _vertical('#002395', _WHITE, '#ed2939')),
    'flag_georgia': _flag('Georgia', 'flag_Georgia', _georgia()),
    'flag_germany': _flag('Germany', 'flag_Germany', _horizontal(_BLACK, '#dd0000', '#ffce00')),
    'flag_greece': _flag('Greece', 'flag_Greece', _greece()),
    'flag_hungary': _flag('Hungary', 'flag_Hungary', _horizontal('#ce2939', _WHITE, '#477050')),
    'flag_iceland': _flag('Iceland', 'flag_Iceland', _nordic('#02529c', _WHITE, '#dc1e35')),
    'flag_italy': _flag('Italy', 'flag_Italy', _vertical('#009246', _WHITE, '#ce2b37')),
    'flag_luxembourg': _flag('Luxembourg', 'flag_Luxembourg', _horizontal('#ed2939', _WHITE, '#00a1de')),
    'flag_netherlands': _flag('Netherlands', 'flag_Netherlands', _horizontal('#ae1c28', _WHITE, '#21468b')),
    'flag_norway': _flag('Norway', 'flag_Norway', _nordic('#ba0c2f', _WHITE, '#00205b')),
    'flag_poland': _flag('Poland', 'flag_Poland', _horizontal(_WHITE, '#dc143c')),
    'flag_portugal': _flag('Portugal', 'flag_Portugal', _portugal()),
    'flag_slovakia': _flag('Slovakia', 'flag_Slovakia', _horizontal(_WHITE, '#0b4ea2', '#ee1c25')),
    'flag_slovenia': _flag('Slovenia', 'flag_Slovenia', _horizontal(_WHITE, '#005da4', '#ed1c24')),
    'flag_spain': _flag('Spain', 'flag_Spain', _horizontal('#aa151b', '#f1bf00', '#aa151b',
                                                             weights=[1, 2, 1])),
    'flag_uk': _flag('United Kingdom', 'flag_UK', _union_jack()),
    'flag_usa': _flag('USA', 'flag_USA', _usa()),
    'flag_ctrg': _flag('CTRG', 'flag_CTRG', _tag('#222222', '#d0d0d0', 'CTRG')),
    'flag_viper': _flag('Viper', 'flag_Viper', _tag('#1a1a1a', '#c8a400', 'VIPER')),
    'flag_syndicat': _flag('Syndikat', 'flag_Syndicat', _tag('#3a2a1a', '#e0c080', 'SYND')),
    'flag_tanoa': _flag('Tanoa', 'flag_Tanoa', _tag('#ffffff', '#1a6a3a', 'TANOA')),
    'flag_tanoagendarmerie': _flag('Gendarmerie', 'flag_TanoaGendarmerie',
                                   _tag('#1d2d5c', '#ffffff', 'GEND')),
    'flag_idap': _flag('IDAP', 'flag_IDAP', _tag('#ffffff', '#1a5aa0', 'IDAP')),
    'redcrystal': _flag('Red Crystal', 'RedCrystal', _red_crystal()),
    'white': _flag('White flag', 'White', _rect(*_CLOTH, '#ffffff')),
    'flag_eaf': _flag('Livonian Defence Force', 'flag_EAF', _tag('#3a4a3a', '#ffffff', 'LDF')),
    'flag_enoch': _flag('Livonia', 'flag_Enoch', _tag('#ffffff', '#7a1a1a', 'LIVONIA')),
    'flag_enochlooters': _flag('Livonian looters', 'flag_EnochLooters',
                               _tag('#4a3a2a', '#ffffff', 'LOOT')),
    'flag_astra': _flag('Astra', 'flag_Astra', _tag('#1a3a5a', '#ffffff', 'ASTRA')),
    'flag_russia': _flag('Russia', 'flag_Russia', _horizontal(_WHITE, '#0039a6', '#d52b1e')),
    'flag_spetsnaz': _flag('Spetsnaz', 'flag_Spetsnaz', _tag('#1a1a1a', '#d0b060', 'SPN')),
}
