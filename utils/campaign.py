"""The campaign planner's rules — the one place they are written down.

A campaign is three to five missions, and each mission runs on a weekly cycle:
the mission team locks a step, and a few days later the drop that step feeds
goes out in the announcements channel. This module decides everything about
that which can be decided without Discord or a database:

    the dates      when each step should be locked and each drop posted
    the posts      what each drop says, with Discord timestamps, and how a
                   post over Discord's 2,000-character limit is split
    the checks     the mistakes that creep in when orders are written in
                   stages: server details in a public post, one place spelled
                   two ways, a phase count that disagrees with the phases
    completeness   how many of a step's required fields are filled
    approval       the hash that says whether what a drop would post now is
                   what somebody approved, and what a drop is doing (held,
                   approved, posted)
    the maps       which layers a campaign map and an AO map hold for the
                   planner's AOs and phases, and how an ops trace is cropped

**It imports nothing but the standard library**, like `utils/orbat.py` and
`utils/tacmap.py`, which is what makes it testable (`tests/test_campaign.py`).
The planner's editor is interactive, but it asks the server for every one of
these answers rather than working them out in the browser — so the preview,
the export and the bot's own posting can never disagree about what a drop
says. Keep the rules here and nowhere else.

A mission is a plain dict of the wizard's fields, keyed as in `STEPS`. Missing
keys read as empty, so a mission created a minute ago is as valid an input as
a finished one; nothing here raises over a half-filled mission.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from utils import tacmap

# Discord refuses a message longer than this. A post over it goes out as
# several messages, each ending in a "Part i/n" line.
DISCORD_LIMIT = 2000
# Room kept at the end of every part for that line ("\n-# Part 10/10").
PART_LABEL_ROOM = 16

DEFAULT_TZ = 'UTC'
DEFAULT_DROP_WEEKDAY = 2          # Wednesday — Python's weekday(), Monday = 0
DEFAULT_DROP_TIME = '19:00'
DEFAULT_SURVEY_URL = 'https://tally.so/r/D4l88q?op={op}'

WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
            'Saturday', 'Sunday')


# ---------------------------------------------------------------------------
# The wizard: six steps, each locked by a date and each feeding one drop.
#
# The field list is data rather than template markup because three things read
# it: the editor (to draw the form), completeness (which fields are required)
# and the checks (what to call a field when something in it is wrong).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = 'text'            # text | textarea | date | time | list | rows | image | images | groups | phases
    required: bool = False
    private: bool = False         # never posted or exported — Zeus-only notes
    help: str = ''
    placeholder: str = ''
    columns: tuple[tuple[str, str], ...] = ()   # rows: (key, placeholder)
    wide: bool = False


@dataclass(frozen=True)
class Step:
    key: str
    number: int
    title: str
    gate: int                     # days from op day by which it is locked
    drop: str                     # the drop it feeds
    intro: str
    tips: tuple[str, ...]
    fields: tuple[Field, ...] = field(default_factory=tuple)


STEPS: tuple[Step, ...] = (
    Step('concept', 1, 'Concept', -35, 'warno',
         'Lock the big picture five weeks out. None of this needs a finished '
         'mission file, only the decisions partner groups need before they commit.',
         ('Agree the date with partner groups before you lock it.',
          'Write the hook last. It should make someone want the next post.',
          'List modpack changes now so people download early.'),
         (
             Field('name', 'Operation name', required=True, placeholder='GRENZFEST',
                   help='One word if you can. It heads every post and the export.'),
             Field('op_ref', 'Order reference', placeholder='OpO_006'),
             Field('op_date', 'Op date', 'date', required=True),
             Field('tz', 'Time zone for op times', placeholder='Europe/Berlin',
                   help="Orders, H-Hour and the FragO timings are read in this zone. "
                        "Empty means the server's time zone."),
             Field('orders_time', 'Orders', 'time', required=True),
             Field('h_hour', 'H-Hour', 'time', required=True),
             Field('dtg', 'In-universe date and time', required=True,
                   placeholder='03 JUL 1984, 0430'),
             Field('ao', 'Area of operations', required=True,
                   placeholder='Cham region, Bavarian Forest'),
             Field('map', 'Terrain / map', placeholder='Terrain mod or map name'),
             Field('enemy_short', 'Enemy, in one line', required=True,
                   placeholder='Bundeswehr and Danish forces'),
             Field('we_are', 'We are', 'textarea', required=True, wide=True,
                   placeholder='23rd Motor Rifle Regiment, 4th Motor Rifle Division, NVA'),
             Field('expect', 'What to expect', required=True, wide=True,
                   placeholder='Mechanised infantry in BTR-80s, dawn start, full daylight'),
             Field('hook', 'The hook', 'textarea', required=True, wide=True,
                   help='Two or three sentences: what has just happened and why we '
                        'are moving. This is the line people repeat in voice chat.'),
             Field('modpack', 'Prepare / modpack changes', 'textarea', wide=True),
             Field('groups_in', 'Groups taking part', 'groups', wide=True),
         )),
    Step('narrative', 2, 'Narrative', -28, 'situation',
         'Tell people why the war started and who they are in it. This is the '
         'post that sells the op.',
         ('Write in the voice of the source. A wire report never says "you".',
          'Stick to what our side would know. Enemy detail comes next week.',
          'One strong image beats three average ones.'),
         (
             Field('source_label', 'In-universe source line', required=True, wide=True,
                   placeholder='WIRE REPORT — BERLIN, 2 JULY 1984',
                   help='Frame it as something a soldier would read or hear: a wire '
                        'report, a radio bulletin, a regimental notice.'),
             Field('situation', 'The situation', 'textarea', required=True, wide=True),
             Field('fronts', 'Wider front, one line each', 'list', wide=True,
                   placeholder='CENTAG committed along the Elbe'),
             Field('who_we_are', 'Who we are', 'textarea', required=True, wide=True,
                   help='Unit history in two lines, what we ride in, what we carry.'),
             Field('unit_img', 'Unit image', 'image', wide=True,
                   help='A vehicle, insignia or a staged screenshot. Attached to the post.'),
         )),
    Step('intel', 3, 'Intelligence', -21, 'intsum',
         'What our side knows about the enemy and the ground. Players start '
         'planning their slots from this.',
         ('Use "suspected" and "unconfirmed" to hint without spoiling Zeus.',
          'Name objectives exactly as the orders will. The checks catch drift.',
          'A recognition sheet of enemy kit is the most-used image of the cycle.'),
         (
             Field('enemy_who', 'Enemy forces', 'textarea', required=True, wide=True),
             Field('enemy_strength', 'Strength and disposition', 'textarea', wide=True),
             Field('equipment', 'Equipment', 'textarea', required=True),
             Field('tactics', 'Tactics', 'textarea', required=True),
             Field('mlcoa', 'Most likely course of action', 'textarea'),
             Field('mdcoa', 'Most dangerous course of action', 'textarea'),
             Field('ground', 'Ground', 'textarea', required=True, wide=True,
                   help='Elevation, routes, vegetation, water, urban areas.'),
             Field('weather', 'Weather and light', required=True, wide=True,
                   placeholder='Clear skies. First light 0430, last light 2020.'),
             Field('objectives', 'Objectives and key places', 'list', required=True,
                   wide=True, placeholder='KRITZENAST',
                   help='Names as they will appear in the orders.'),
             Field('map_img', 'AO map', 'image',
                   help='Objective names only. Keep phase lines and arrows for the FragO.'),
             Field('recog_img', 'Recognition sheet', 'image',
                   help="One image showing the enemy's vehicles, weapons and uniforms, "
                        "so players can tell friend from foe at a glance and call "
                        "contacts by type."),
             Field('zeus_only', 'Held back for Zeus', 'textarea', private=True, wide=True,
                   help='Never posted or exported. Note the surprises here so the '
                        'intel can hint at them without giving them away.'),
         )),
    Step('orders', 4, 'Orders', -14, 'opord',
         'The OPORD. The mission should be playable by now, so phases, support '
         'and comms are final.',
         ('Mission in one sentence: who, what, where, why.',
          'Intent is purpose and end state, not a list of tasks.',
          'The number of phases here is the number the FragO must use.'),
         (
             Field('mission', 'Mission', 'textarea', required=True, wide=True),
             Field('intent', "Commander's intent", 'textarea', required=True, wide=True),
             Field('end_state', 'Success looks like', 'textarea', wide=True),
             Field('phases', 'Phases', 'phases', required=True, wide=True,
                   help='One per phase, in order: what happens, which AO it is '
                        'fought in, and its objectives from the library. Each '
                        'phase becomes a layer on its AO map.'),
             Field('support', 'Field support', 'textarea', required=True),
             Field('resupply', 'Resupply', 'textarea', required=True),
             Field('nets', 'Radio plan', 'rows', required=True, wide=True,
                   columns=(('net', 'Platoon net'), ('channel', '152 ch 1'))),
             Field('coc', 'Chain of command', wide=True,
                   placeholder='PL → PSGT → 1 SL → 2 SL'),
             Field('respawn', 'Respawn and re-insert', 'textarea'),
             Field('technical', 'Technical notes', 'textarea'),
             Field('modpack_link', 'Final modpack link', wide=True, placeholder='https://…'),
         )),
    Step('frago', 5, 'FragO', -7, 'frago',
         'Who does what, phase by phase. This goes to leaders with the traces '
         'three days out.',
         ("Enter times in the op's time zone. Posts convert them so everyone "
          "sees their own time.",
          'One row per callsign. Paste the task list straight in.',
          'Keep server details out of the FragO. The sign-up bot handles them.'),
         (
             Field('frago_no', 'FragO number', placeholder='001'),
             Field('timings', 'Timings', 'rows', required=True, wide=True,
                   help="Times in the op's time zone.",
                   columns=(('event', 'H-Hour (Obj HYPERION)'), ('time', '20:15'))),
             Field('tasks', 'Tasks by callsign', 'rows', required=True, wide=True,
                   columns=(('callsign', '1-1 (CNTO)'),
                            ('tasks', 'CLEAR sector SILVER on Obj HYPERION…'))),
             Field('actions_on', 'Actions on', 'textarea', wide=True),
             Field('loadout', 'Loadout minimums', 'textarea', required=True),
             Field('medical', 'Medical plan', 'textarea'),
             Field('traces', 'Ops traces and annexes', 'images', wide=True,
                   help="Extra images for the leaders' pack, in order. Phase layers "
                        "from the AO maps are added on their own."),
         )),
    Step('aar', 6, 'After action', 1, 'aar',
         'Close the loop the day after. End on the hook for the next op so '
         'there is never a dead week.',
         ('Outcome in-universe first, stats second.',
          'Mention one moment from each partner group.',
          'Link the survey while memories are fresh.'),
         (
             Field('outcome', 'What happened, in-universe', 'textarea', required=True,
                   wide=True),
             Field('results', 'Objectives', 'rows', required=True, wide=True,
                   columns=(('objective', 'KRITZENAST'),
                            ('result', 'Taken / Failed / Contested'))),
             Field('stats', 'Stats (OCAP2 headlines)', 'textarea', wide=True),
             Field('aar_imgs', 'Screenshots', 'images', wide=True),
             Field('next_hook', 'Hook for the next op', 'textarea', wide=True),
         )),
)

STEP = {step.key: step for step in STEPS}
FIELD = {f.key: f for step in STEPS for f in step.fields}


@dataclass(frozen=True)
class Drop:
    key: str
    label: str
    step: str


DROPS: tuple[Drop, ...] = (
    Drop('warno', 'WARNO', 'concept'),
    Drop('situation', 'Drop 1 · Situation', 'narrative'),
    Drop('intsum', 'Drop 2 · INTSUM', 'intel'),
    Drop('opord', 'Drop 3 · OPORD', 'orders'),
    Drop('frago', 'Drop 4 · FragO', 'frago'),
    Drop('aar', 'AAR', 'aar'),
)
DROP = {drop.key: drop for drop in DROPS}
DROP_ORDER = tuple(drop.key for drop in DROPS)

# The drops that may open the ORBAT for sign-ups. The WARNO is too early — the
# ORBAT may not exist yet — and the FragO too late to fill forty slots.
ORBAT_OPENERS = ('situation', 'intsum', 'opord')
DEFAULT_ORBAT_OPENS = 'situation'

# Who may edit an ORBAT that a campaign mission links (copying and deleting
# ORBATs stay admin-only whatever this says). Extra roles in `orbat_role_ids`
# are added on top of either choice.
ORBAT_ACCESS = {'admins': 'Server admins only',
                'planners': 'Server admins and campaign planners'}
DEFAULT_ORBAT_ACCESS = 'planners'


# ---------------------------------------------------------------------------
# Reading values
# ---------------------------------------------------------------------------

def _scalar(value) -> bool:
    return isinstance(value, (str, int, float)) and not isinstance(value, bool)


def filled(value) -> bool:
    """Whether a field holds anything. A list or a list of rows counts as filled
    when any entry has any non-blank text in it; blank rows the editor keeps
    around for typing into do not. Inside a row only text counts — a phase's
    list of objectives, or its AO id, does not make an untitled phase filled."""
    if value is None:
        return False
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                if any(isinstance(v, str) and v.strip() for v in item.values()):
                    return True
            elif _scalar(item) and str(item).strip():
                return True
        return False
    if isinstance(value, dict):
        return any(isinstance(v, str) and v.strip() for v in value.values())
    return bool(str(value).strip())


def text(mission: dict, key: str) -> str:
    return str(mission.get(key) or '').strip()


def lines(mission: dict, key: str) -> list[str]:
    return [str(x).strip() for x in (mission.get(key) or []) if str(x or '').strip()]


def rows(mission: dict, key: str) -> list[dict]:
    """The non-blank rows of a rows field, every text value stripped. Values
    that are not text (a phase's objectives) are passed through untouched."""
    out = []
    for row in mission.get(key) or []:
        if not isinstance(row, dict):
            continue
        clean = {k: (str(v or '').strip() if v is None or _scalar(v) else v)
                 for k, v in row.items()}
        if any(isinstance(v, str) and v for v in clean.values()):
            out.append(clean)
    return out


def _or(value: str, placeholder: str) -> str:
    """The value, or `[placeholder]` so a gap is obvious in a preview."""
    return value if value else f'[{placeholder}]'


# ---------------------------------------------------------------------------
# Dates and times
# ---------------------------------------------------------------------------

_TIME = re.compile(r'^\s*(\d{1,2}):?(\d{2})\s*$')


def parse_time(value) -> time | None:
    """'19:30' or '1930' → 19:30. Anything else → None."""
    match = _TIME.match(str(value or ''))
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return time(hours, minutes)


def parse_date(value) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value or '').strip())
    except ValueError:
        return None


def zone(name: str | None) -> ZoneInfo:
    """A time zone by IANA name, falling back to UTC for an empty or unknown one
    — an unknown name must not take a post down, and `clean_settings()` is where
    a person is told their zone is wrong."""
    try:
        return ZoneInfo((name or '').strip() or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TZ)


def to_utc(day, clock, tz_name: str | None) -> datetime | None:
    """A local date and time in a zone, as an aware UTC datetime. None when
    either half is missing or unreadable."""
    d, t = parse_date(day), parse_time(clock)
    if d is None or t is None:
        return None
    return datetime.combine(d, t, tzinfo=zone(tz_name)).astimezone(timezone.utc)


def discord_ts(moment: datetime | None, style: str = 'F') -> str:
    """`<t:…:F>` — every Discord client renders it in the reader's own zone."""
    if moment is None:
        return '[time]'
    return f'<t:{int(moment.timestamp())}:{style}>'


def last_weekday_before(day: date, weekday: int) -> date:
    """The last `weekday` strictly before `day`."""
    back = (day.weekday() - weekday) % 7 or 7
    return day - timedelta(days=back)


def d_label(offset: int) -> str:
    return 'D-0' if offset == 0 else (f'D+{offset}' if offset > 0 else f'D{offset}')


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def settings_with_defaults(row: dict | None) -> dict:
    """A guild's planner settings, with every key present. `row` is the stored
    row (or None for a guild that never saved any)."""
    row = dict(row or {})
    weekday = row.get('drop_weekday')
    opens = row.get('orbat_opens')
    return {
        'unit_role_id': row.get('unit_role_id') or None,
        'drop_channel_id': row.get('drop_channel_id') or None,
        'team_channel_id': row.get('team_channel_id') or None,
        'drop_weekday': weekday if isinstance(weekday, int) and 0 <= weekday <= 6
        else DEFAULT_DROP_WEEKDAY,
        'drop_time': row.get('drop_time') if parse_time(row.get('drop_time'))
        else DEFAULT_DROP_TIME,
        'orbat_opens': opens if opens in ORBAT_OPENERS else DEFAULT_ORBAT_OPENS,
        'survey_url': row.get('survey_url') or DEFAULT_SURVEY_URL,
        'orbat_access': row.get('orbat_access') if row.get('orbat_access') in ORBAT_ACCESS
        else DEFAULT_ORBAT_ACCESS,
        'orbat_role_ids': [r for r in str(row.get('orbat_role_ids') or '').split(',') if r],
    }


def may_edit_orbats(settings: dict, *, is_admin: bool, may_plan: bool, role_ids=()) -> bool:
    """Whether a member may edit the ORBATs campaign missions link: admins
    always; planners when the setting says so; and anybody holding one of the
    extra roles the setting names."""
    if is_admin:
        return True
    if settings.get('orbat_access') == 'planners' and may_plan:
        return True
    allowed = set(settings.get('orbat_role_ids') or [])
    return any(str(r) in allowed for r in role_ids)


def clean_settings(form: dict, *, role_ids=(), channel_ids=(), orbat_role_ids=()) -> dict:
    """The settings form, validated. Raises ValueError with a message meant for
    a person. `role_ids` and `channel_ids` are what the page offered, so a
    hand-edited form cannot store a role or channel the guild does not have.
    `orbat_role_ids` is the ticked extra ORBAT roles (a form list)."""
    role_ids = {str(r) for r in role_ids}
    channel_ids = {str(c) for c in channel_ids}

    def pick(key, allowed, what):
        value = str(form.get(key) or '').strip()
        if value and value not in allowed:
            raise ValueError(f"That {what} isn't one I can use — pick another.")
        return value or None

    try:
        weekday = int(form.get('drop_weekday', DEFAULT_DROP_WEEKDAY))
    except (TypeError, ValueError):
        weekday = -1
    if not 0 <= weekday <= 6:
        raise ValueError('Pick a day of the week for the drops.')

    clock = parse_time(form.get('drop_time'))
    if clock is None:
        raise ValueError('The drop time should look like 19:00.')

    opens = str(form.get('orbat_opens') or DEFAULT_ORBAT_OPENS)
    if opens not in ORBAT_OPENERS:
        raise ValueError('Pick which drop opens the ORBAT.')

    survey = str(form.get('survey_url') or '').strip()
    if survey and not re.match(r'^https?://', survey):
        raise ValueError('The survey link should start with https://')
    if len(survey) > 500:
        raise ValueError('The survey link is too long.')

    access = str(form.get('orbat_access') or DEFAULT_ORBAT_ACCESS)
    if access not in ORBAT_ACCESS:
        raise ValueError('Pick who may edit mission ORBATs.')
    extra = []
    for role in orbat_role_ids:
        role = str(role).strip()
        if role and role not in role_ids:
            raise ValueError("One of the ORBAT roles isn't one I can use — tick them again.")
        if role and role not in extra:
            extra.append(role)

    drop_channel = pick('drop_channel_id', channel_ids, 'drop channel')
    team_channel = pick('team_channel_id', channel_ids, 'mission team channel')
    if drop_channel and drop_channel == team_channel:
        raise ValueError('The drop channel is public and the mission team channel '
                         'is not, so they need to be different channels.')

    return {
        'unit_role_id': pick('unit_role_id', role_ids, 'role'),
        'drop_channel_id': drop_channel,
        'team_channel_id': team_channel,
        'drop_weekday': weekday,
        'drop_time': clock.strftime('%H:%M'),
        'orbat_opens': opens,
        'survey_url': survey or None,
        'orbat_access': access,
        'orbat_role_ids': ','.join(extra) or None,
    }


def survey_url(template: str | None, mission_name: str) -> str:
    """The survey link for one mission. `{op}` (or `[MISSION NAME]`, as it was
    first written) becomes the mission name with spaces as %20 — what the
    Tally form's hidden field expects."""
    template = template or DEFAULT_SURVEY_URL
    encoded = quote((mission_name or '').strip(), safe='')
    return re.sub(r'\{op\}|\[MISSION NAME\]', encoded, template, flags=re.IGNORECASE)


# ---------------------------------------------------------------------------
# The schedule
# ---------------------------------------------------------------------------

def step_progress(mission: dict, step_key: str) -> tuple[int, int]:
    """(required fields filled, required fields) for one step."""
    required = [f for f in STEP[step_key].fields if f.required]
    done = sum(1 for f in required if filled(mission.get(f.key)))
    return done, len(required)


def step_complete(mission: dict, step_key: str) -> bool:
    done, total = step_progress(mission, step_key)
    return done == total


def mission_tz(mission: dict, settings: dict, guild_tz: str | None = None) -> str:
    return text(mission, 'tz') or guild_tz or DEFAULT_TZ


def drop_dates(op_date: date, drop_weekday: int) -> dict[str, date]:
    """The local date each drop goes out on.

    The WARNO is four weeks out and the AAR the day after. The four weekly
    drops fall on the chosen weekday in each of the four weeks before the op:
    the last such day before D-21, D-14, D-7 and D-0. On a Saturday op with
    Wednesday drops that is D-24, D-17, D-10 and D-3.
    """
    def weekly(k: int) -> date:
        return last_weekday_before(op_date - timedelta(days=7 * (k - 1)), drop_weekday)

    return {
        'warno': op_date - timedelta(days=28),
        'situation': weekly(4),
        'intsum': weekly(3),
        'opord': weekly(2),
        'frago': weekly(1),
        'aar': op_date + timedelta(days=1),
    }


def drop_times(mission: dict, settings: dict, guild_tz: str | None = None) -> dict:
    """When each drop goes out, as aware UTC datetimes — the drop time read in
    the guild's zone on each drop date. Empty when the mission has no date."""
    op_date = parse_date(mission.get('op_date'))
    if op_date is None:
        return {}
    dates = drop_dates(op_date, settings['drop_weekday'])
    return {key: to_utc(day, settings['drop_time'], guild_tz) for key, day in dates.items()}


def schedule(mission: dict, settings: dict, today: date,
             guild_tz: str | None = None) -> list[dict]:
    """Every lock and drop of a mission, in date order, each with a status.

    A lock (`kind='gate'`) is locked once its step's required fields are all
    filled; until then it is open, due soon within three days, or overdue. A
    drop (`kind='drop'`) is ready once the step behind it is locked. Whether a
    drop has been approved or posted is not decided here — the caller layers
    that on, since it lives in the database.
    """
    op_date = parse_date(mission.get('op_date'))
    if op_date is None:
        return []
    dates = drop_dates(op_date, settings['drop_weekday'])
    times = drop_times(mission, settings, guild_tz)
    items = []

    for step in STEPS:
        if step.key == 'aar':
            continue               # the AAR is written after the op, not locked before it
        gate = op_date + timedelta(days=step.gate)
        done, total = step_progress(mission, step.key)
        left = (gate - today).days
        if done == total:
            status = ('ok', 'Locked')
        elif left < 0:
            status = ('crit', 'Overdue')
        elif left <= 3:
            status = ('warn', 'Due soon')
        else:
            status = ('', f'{done}/{total} ready')
        items.append({'kind': 'gate', 'key': step.key, 'step': step.key,
                      'label': f'{step.title} locked', 'date': gate,
                      'd': step.gate, 'done': done, 'total': total,
                      'status': status})

    for drop in DROPS:
        day = dates[drop.key]
        ready = step_complete(mission, drop.step)
        note = 'ORBAT opens for everyone' if drop.key == settings['orbat_opens'] else ''
        if drop.key == 'warno':
            note = note or 'Interest check'
        items.append({'kind': 'drop', 'key': drop.key, 'step': drop.step,
                      'label': drop.label, 'date': day, 'at': times.get(drop.key),
                      'd': (day - op_date).days, 'ready': ready, 'note': note,
                      'status': ('acc', 'Ready') if ready
                      else ('', f'Waiting on {STEP[drop.step].title.lower()}')})

    items.append({'kind': 'op', 'key': 'op', 'label': 'Op day', 'date': op_date, 'd': 0,
                  'status': ('acc', f"H-Hour {text(mission, 'h_hour') or '—'}")})

    order = {'gate': 0, 'drop': 1, 'op': 2}
    items.sort(key=lambda item: (item['date'], order[item['kind']]))
    return items


# ---------------------------------------------------------------------------
# The posts
# ---------------------------------------------------------------------------

@dataclass
class PostContext:
    """What a drop needs besides the mission itself."""
    settings: dict
    guild_tz: str | None = None
    orbat_channel: str = '#orbat'     # a channel mention (<#id>) where one is known


def orbat_line(mission: dict, drop_key: str, ctx: PostContext) -> str:
    """What a drop says about sign-ups: when the ORBAT opens, that it is open,
    or that slots are filling — depending on which side of the opening drop
    this one falls."""
    opens = ctx.settings['orbat_opens']
    here, there = DROP_ORDER.index(drop_key), DROP_ORDER.index(opens)
    channel = ctx.orbat_channel
    if here < there:
        at = drop_times(mission, ctx.settings, ctx.guild_tz).get(opens)
        return f'The ORBAT opens for everyone in {channel} {discord_ts(at, "D")}.'
    if here == there:
        return f'The ORBAT is now open for everyone in {channel}.'
    return f'Slots are filling in {channel}.'


def build_post(mission: dict, drop_key: str, ctx: PostContext) -> str:
    """One drop's text, in Discord markdown. Gaps show as `[placeholders]` so a
    preview makes them obvious; private fields are never read."""
    name = (text(mission, 'name') or '[OP NAME]').upper()
    tz = mission_tz(mission, ctx.settings, ctx.guild_tz)
    orders = to_utc(mission.get('op_date'), mission.get('orders_time'), tz)
    h_hour = to_utc(mission.get('op_date'), mission.get('h_hour'), tz)
    out: list[str] = []
    ooc = lambda line: out.append('> ' + line)

    if drop_key == 'warno':
        where = _or(text(mission, 'ao'), 'area of operations')
        if text(mission, 'map'):
            where += f" ({text(mission, 'map')})"
        out += [f'# WARNING ORDER — OPERATION {name}',
                f"**DTG:** {_or(text(mission, 'dtg'), 'in-universe date/time')} | **AO:** {where}",
                f"**We are:** {_or(text(mission, 'we_are'), 'unit, formation, side')}",
                '',
                _or(text(mission, 'hook'), 'the hook: what happened and why we move'),
                '',
                f"**Expect:** {_or(text(mission, 'expect'), 'what to expect')}"]
        if text(mission, 'modpack'):
            out.append(f"**Prepare:** {text(mission, 'modpack')}")
        if lines(mission, 'groups_in'):
            out.append(f"**With:** {', '.join(lines(mission, 'groups_in'))}")
        out.append('')
        ooc(f'OOC: Op night {discord_ts(h_hour, "F")} ({discord_ts(h_hour, "R")}). '
            f'Orders {discord_ts(orders, "t")}.')
        ooc(f"{orbat_line(mission, 'warno', ctx)} React with your group's emoji if you're in.")

    elif drop_key == 'situation':
        out += [f"# {_or(text(mission, 'source_label'), 'IN-UNIVERSE SOURCE — DATE')}",
                _or(text(mission, 'situation'), 'the situation')]
        if lines(mission, 'fronts'):
            out.append('')
            out += [f'- {line}' for line in lines(mission, 'fronts')]
        out += ['', '## Who we are', _or(text(mission, 'who_we_are'), 'who we are'), '']
        ooc(f"OOC: {orbat_line(mission, 'situation', ctx)}")

    elif drop_key == 'intsum':
        out += [f'# INTSUM — OPERATION {name}',
                f"**Enemy:** {_or(text(mission, 'enemy_who'), 'enemy forces')}"]
        if text(mission, 'enemy_strength'):
            out.append(f"**Strength:** {text(mission, 'enemy_strength')}")
        out += [f"**Equipment:** {_or(text(mission, 'equipment'), 'equipment')}",
                f"**Tactics:** {_or(text(mission, 'tactics'), 'tactics')}"]
        if text(mission, 'mlcoa'):
            out.append(f"**Most likely:** {text(mission, 'mlcoa')}")
        if text(mission, 'mdcoa'):
            out.append(f"**Most dangerous:** {text(mission, 'mdcoa')}")
        out += ['', '## Ground', _or(text(mission, 'ground'), 'ground'),
                f"**Weather and light:** {_or(text(mission, 'weather'), 'weather')}"]
        if lines(mission, 'objectives'):
            out.append(f"**Key places:** {' · '.join(lines(mission, 'objectives'))}")
        out.append('')
        ooc(f"OOC: {orbat_line(mission, 'intsum', ctx)} Op night {discord_ts(h_hour, 'R')}.")

    elif drop_key == 'opord':
        out += [f'# OPERATION ORDER — {name}',
                f"**Mission:** {_or(text(mission, 'mission'), 'mission')}",
                f"**Commander's intent:** {_or(text(mission, 'intent'), 'intent')}"]
        success = text(mission, 'end_state') or '; '.join(success_conditions(mission))
        if success:
            out.append(f"**Success:** {success}")
        out += ['', '**Phases**']
        phases = rows(mission, 'phases')
        if phases:
            for i, phase in enumerate(phases, 1):
                detail = re.sub(r'\n+', ' ', phase.get('detail', ''))
                out.append(f"{i}. **{phase.get('title', '')}**" + (f' — {detail}' if detail else ''))
        else:
            out.append('1. [phase]')
        support = re.sub(r'\n+', '; ', text(mission, 'support'))
        out += ['', f"**Support:** {_or(support, 'field support')}"]
        nets = rows(mission, 'nets')
        comms = ' · '.join(f"{n.get('net', '')} {n.get('channel', '')}".strip() for n in nets)
        out.append(f"**Comms:** {_or(comms, 'radio plan')}")
        resupply = re.sub(r'\n+', ' ', text(mission, 'resupply'))
        out.append(f"**Resupply:** {_or(resupply, 'resupply')}")
        if text(mission, 'respawn'):
            out.append(f"**Respawn:** {re.sub(chr(10) + '+', ' ', text(mission, 'respawn'))}")
        out.append('')
        modpack = text(mission, 'modpack_link')
        ooc('OOC: ' + (f'Final modpack → {modpack}. ' if modpack else '')
            + f"Full OPORD attached. {orbat_line(mission, 'opord', ctx)} "
            + f'Orders {discord_ts(orders, "F")}.')

    elif drop_key == 'frago':
        out += [f"# FRAGO {_or(text(mission, 'frago_no'), 'no.')} — {name}",
                'Leaders: FragO and ops traces attached.', '', '**Timings**']
        timings = rows(mission, 'timings')
        if timings:
            for row in timings:
                at = to_utc(mission.get('op_date'), row.get('time'), tz)
                out.append(f"- {row.get('event', '')}: "
                           + (discord_ts(at, 't') if at else row.get('time', '')))
        else:
            out.append('- [timings]')
        loadout = re.sub(r'\n+', ' ', text(mission, 'loadout'))
        out += ['', f"**Loadout minimums:** {_or(loadout, 'loadout')}", '']
        op_date = parse_date(mission.get('op_date'))
        lock = to_utc(op_date - timedelta(days=1), '20:00', tz) if op_date else None
        ooc(f'OOC: The ORBAT in {ctx.orbat_channel} locks {discord_ts(lock, "D")}. '
            'Last chance to grab a slot.')

    elif drop_key == 'aar':
        out += [f'# AFTER ACTION — OPERATION {name}',
                _or(text(mission, 'outcome'), 'what happened'), '']
        results = rows(mission, 'results')
        if results:
            out.append('**Objectives**')
            out += [f"- {r.get('objective', '')}: {r.get('result', '')}" for r in results]
            out.append('')
        if text(mission, 'stats'):
            out += [f"**Stats:** {text(mission, 'stats')}", '']
        if text(mission, 'next_hook'):
            out += [f"*{text(mission, 'next_hook')}*", '']
        groups = ', '.join(lines(mission, 'groups_in')) or 'everyone'
        ooc(f"OOC: Feedback survey → {survey_url(ctx.settings['survey_url'], text(mission, 'name'))}. "
            f'Thanks to {groups} for coming.')

    else:
        raise ValueError(f'Unknown drop: {drop_key}')

    return '\n'.join(out)


def drop_images(mission: dict, drop_key: str) -> list:
    """The image ids attached to a drop, in order."""
    by_drop = {
        'warno': [],
        'situation': [mission.get('unit_img')],
        'intsum': [mission.get('map_img'), mission.get('recog_img')],
        'opord': [],
        'frago': list(mission.get('traces') or []),
        'aar': list(mission.get('aar_imgs') or []),
    }
    return [img for img in by_drop[drop_key] if img]


def split_message(body: str) -> list[str]:
    """A post as the messages it goes out in.

    Under the limit it is one message, untouched. Over it, the text is split on
    paragraphs, then lines, then spaces, and every part ends in a small
    `-# Part i/n` line (Discord's subtext), so readers know there is more. The
    planner's preview and the bot's posting both call this, which is what makes
    a copied post and a posted one identical.
    """
    if len(body) <= DISCORD_LIMIT:
        return [body]
    room = DISCORD_LIMIT - PART_LABEL_ROOM
    chunks: list[str] = []
    current = ''
    for block in body.split('\n\n'):
        candidate = f'{current}\n\n{block}' if current else block
        if len(candidate) <= room:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ''
        while len(block) > room:
            cut = block.rfind('\n', 0, room)
            if cut <= 0:
                cut = block.rfind(' ', 0, room)
            if cut <= 0:
                cut = room
            chunks.append(block[:cut])
            block = block[cut:].lstrip()
        current = block
    if current:
        chunks.append(current)
    return [f'{chunk}\n-# Part {i}/{len(chunks)}' for i, chunk in enumerate(chunks, 1)]


def approval_hash(body: str, image_ids) -> str:
    """What a drop would post, reduced to one string. Stored when somebody
    approves the drop and compared when it is due: any change to the text or
    the images changes it, so an edit after approval needs approving again."""
    payload = json.dumps({'text': body, 'images': [str(i) for i in image_ids or []]},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------
# Drop state: approved, held, posted
#
# The database row (`cp_drops`) records three decisions — who approved which
# version for which time, that the team was pinged about a hold, and what was
# posted — and everything a person or the posting loop needs to know is worked
# out from that row and what the drop would post *now*. Both the drops panel
# and the loop call `drop_state()`, so what the page says will happen is what
# the bot does.
# ---------------------------------------------------------------------------

# A held drop pings the mission team once — but not for a drop that went past
# long before anybody was watching, such as the WARNO of a mission planned two
# weeks out. Those just show as held on the page.
HOLD_PING_WINDOW = timedelta(days=2)

# An active operation whose start is further in the past than this has been
# played; starting the next mission's sign-ups may replace it. One with no
# start time at all is treated as running, because nothing says it is over.
OPERATION_OVER_AFTER = timedelta(hours=6)

# Drop states: key → (tone, label). Tones are the page's chip classes.
DROP_STATES = {
    'nodate':     ('', 'No op date yet'),
    'unapproved': ('', 'Not approved yet'),
    'reapprove':  ('warn', 'Needs re-approval'),
    'approved':   ('ok', 'Approved'),
    'posting':    ('acc', 'Posting now'),
    'nochannel':  ('crit', 'Approved — no channel to post in'),
    'held':       ('crit', 'Held — not approved'),
    'heldchange': ('crit', 'Held — changed since approval'),
    'posted':     ('ok', 'Posted'),
    'updating':   ('acc', 'Approved change — updating the post'),
    'changed':    ('warn', 'Changed since posting — approve to update the post'),
    'skipped':    ('', 'Skipped'),
}


def _aware(moment: datetime | None) -> datetime | None:
    """Naive datetimes in this codebase are UTC; make one comparable."""
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def drop_state(row: dict | None, *, current_hash: str, due: datetime | None,
               now: datetime, channel: str | None = 'set') -> dict:
    """What one drop is doing, and what the posting loop should do about it.

    `row` is the drop's `cp_drops` row (None when nothing was ever decided);
    `current_hash` is `approval_hash()` of what it would post now; `due` is
    when it goes out (aware UTC, from `drop_times()`); `channel` is where it
    would go, or None when there is nowhere.

    An approval is for one version **at one time**: the hash and the due time
    are stored together, so moving the op date — which moves every drop not
    yet posted — asks for approval again even when the words are unchanged.
    A posted drop no longer cares about its time; only a change to what it
    says matters, and an approved change edits the posted messages.

    `action` is `post`, `edit`, `ping` or None.
    """
    row = row or {}
    due, now = _aware(due), _aware(now)
    approved_hash = row.get('approved_hash')
    action = None

    if row.get('skipped'):
        key = 'skipped'
    elif row.get('posted_at'):
        if approved_hash == current_hash == row.get('posted_hash'):
            key = 'posted'
        elif approved_hash == current_hash:
            key, action = 'updating', 'edit'
        else:
            key = 'changed'
    elif due is None:
        key = 'nodate'
    else:
        valid = (approved_hash == current_hash
                 and _aware(row.get('due_at')) == due)
        is_due = due <= now
        if valid and is_due:
            key, action = ('posting', 'post') if channel else ('nochannel', None)
        elif valid:
            key = 'approved'
        elif is_due:
            key = 'heldchange' if approved_hash else 'held'
            if not row.get('held_pinged_at') and now - due <= HOLD_PING_WINDOW:
                action = 'ping'
        else:
            key = 'reapprove' if approved_hash else 'unapproved'

    tone, label = DROP_STATES[key]
    return {'key': key, 'tone': tone, 'label': label, 'action': action,
            'approved': key in ('approved', 'posting', 'nochannel', 'posted', 'updating'),
            'posted': bool(row.get('posted_at')) and not row.get('skipped'),
            'can_approve': key in ('unapproved', 'reapprove', 'held', 'heldchange',
                                   'changed', 'nochannel'),
            'can_unapprove': key in ('approved', 'nochannel'),
            'can_skip': key not in ('posted', 'updating', 'changed', 'skipped', 'posting')}


def drop_channel(settings: dict, is_test: bool) -> str | None:
    """Where a campaign's drops go. A test campaign's drops never post
    publicly: they go to the mission team channel, or nowhere."""
    return settings.get('team_channel_id') if is_test else settings.get('drop_channel_id')


def operation_running(operation: dict | None, now: datetime) -> bool:
    """Whether an active operation is still to be played (or being played).
    Starting a mission's sign-ups must never replace one that is."""
    if operation is None:
        return False
    start = _aware(operation.get('event_time'))
    if start is None:
        return True
    return start + OPERATION_OVER_AFTER > _aware(now)


def h_hour_utc(mission: dict, settings: dict, guild_tz: str | None = None) -> datetime | None:
    """The op's start as naive UTC — what an operation's `event_time` holds."""
    moment = to_utc(mission.get('op_date'), mission.get('h_hour'),
                    mission_tz(mission, settings, guild_tz))
    return moment.replace(tzinfo=None) if moment else None


def image_groups(sizes: list[int], *, limit_bytes: int, per_message: int = 10) -> list[list[int]]:
    """Image indexes grouped into messages Discord will take: at most
    `per_message` files and `limit_bytes` in all per message. An image over
    the limit on its own still gets a message of its own, where Discord's
    refusal names it rather than taking a whole drop down."""
    groups: list[list[int]] = []
    current: list[int] = []
    total = 0
    for i, size in enumerate(sizes):
        if current and (len(current) >= per_message or total + size > limit_bytes):
            groups.append(current)
            current, total = [], 0
        current.append(i)
        total += size
    if current:
        groups.append(current)
    return groups


# ---------------------------------------------------------------------------
# Consistency checks
# ---------------------------------------------------------------------------

_IP = re.compile(r'\b\d{1,3}(\.\d{1,3}){3}\b')
_PASSWORD = re.compile(r'\bpass(word)?\b\s*[:=]', re.IGNORECASE)
_PROPER = re.compile(r'\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß]{5,}\b')
_PHASE_COUNT = re.compile(r'\b(\d+|two|three|four|five|six|seven|eight)[\s-]+phases?\b',
                          re.IGNORECASE)
_NUMBER_WORDS = {'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8}


def _levenshtein(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2:
        return 9
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _public_texts(mission: dict):
    """Every non-private piece of text in the mission, as (step, field, text)."""
    for step in STEPS:
        for f in step.fields:
            if f.private:
                continue
            value = mission.get(f.key)
            if f.kind in ('text', 'textarea') and filled(value):
                yield step, f, str(value)
            elif f.kind == 'list':
                for item in value or []:
                    if str(item or '').strip():
                        yield step, f, str(item)
            elif f.kind == 'rows':
                for row in value or []:
                    if isinstance(row, dict):
                        for cell in row.values():
                            if str(cell or '').strip():
                                yield step, f, str(cell)


def checks(mission: dict, ctx: PostContext | None = None) -> list[dict]:
    """What tends to go wrong when orders are written in stages.

    Each check is `{'level': 'crit'|'warn', 'step': step key or None,
    'drop': drop key or None, 'text': message}`. Crit is reserved for things
    that would leak — server details in a field that gets posted.
    """
    found: list[dict] = []
    texts = list(_public_texts(mission))

    leaked = {}
    for step, f, value in texts:
        if _IP.search(value) or _PASSWORD.search(value):
            leaked.setdefault(f.key, (step, f))
    for step, f in leaked.values():
        found.append({'level': 'crit', 'step': step.key, 'drop': None,
                      'text': f'"{f.label}" looks like it contains a server IP or password. '
                              'Take it out: these posts are public.'})

    # One place spelled two ways — Schontal and Schonthal.
    words: dict[str, dict] = {}
    for step, _, value in texts:
        for word in _PROPER.findall(value):
            entry = words.setdefault(word.lower(), {'word': word, 'steps': []})
            if step.title not in entry['steps']:
                entry['steps'].append(step.title)
    keys = sorted(words)
    pairs = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if b in (a + 's', a + 'es') or a in (b + 's', b + 'es') or a[:3] != b[:3]:
                continue
            distance = _levenshtein(a, b)
            if distance == 1 or (distance == 2 and min(len(a), len(b)) >= 8):
                pairs.append((words[a], words[b]))
    for a, b in pairs[:6]:
        found.append({'level': 'warn', 'step': None, 'drop': None,
                      'text': f'"{a["word"]}" ({", ".join(a["steps"])}) and '
                              f'"{b["word"]}" ({", ".join(b["steps"])}) look like the '
                              'same place spelled two ways.'})

    phase_count = len([p for p in rows(mission, 'phases') if p.get('title')])
    if phase_count:
        for step, f, value in texts:
            match = _PHASE_COUNT.search(value)
            if not match:
                continue
            word = match.group(1).lower()
            said = _NUMBER_WORDS.get(word) or (int(word) if word.isdigit() else 0)
            if said and said != phase_count:
                found.append({'level': 'warn', 'step': step.key, 'drop': None,
                              'text': f'"{f.label}" mentions {said} phases, but the '
                                      f'orders list {phase_count}.'})

    if text(mission, 'mission'):
        orders_text = ' '.join(
            [text(mission, 'mission'), text(mission, 'intent'), text(mission, 'end_state')]
            + [f"{p.get('title', '')} {p.get('detail', '')}" for p in rows(mission, 'phases')]
            + [f"{o.get('name', '')} {o.get('players_do', '')}"
               for p in rows(mission, 'phases') for o in p.get('objectives') or []]
            + [t.get('tasks', '') for t in rows(mission, 'tasks')]
        ).lower()
        for place in lines(mission, 'objectives'):
            if place.lower() not in orders_text:
                found.append({'level': 'warn', 'step': 'orders', 'drop': None,
                              'text': f'"{place}" is named in the intel but not in the '
                                      'mission, phases or tasks.'})

    if ctx is not None:
        for drop in DROPS:
            body = build_post(mission, drop.key, ctx)
            if len(body) > DISCORD_LIMIT:
                parts = len(split_message(body))
                found.append({'level': 'warn', 'step': drop.step, 'drop': drop.key,
                              'text': f'{drop.label} is {len(body):,} characters, so it '
                                      f'goes out as {parts} messages. Trim it if you’d '
                                      'rather send one.'})

    for i, phase in enumerate(rows(mission, 'phases'), 1):
        objectives = [o for o in phase.get('objectives') or [] if isinstance(o, dict)]
        if not objectives:
            continue
        roles = [o.get('role') for o in objectives]
        label = phase.get('title') or f'Phase {i}'
        missing = [name for name in ('secondary', 'complication') if name not in roles]
        if roles.count('primary') != 1:
            found.append({'level': 'warn', 'step': 'orders', 'drop': None,
                          'text': f'{label} has {roles.count("primary")} primary objectives. '
                                  'A phase reads best with exactly one.'})
        if missing:
            found.append({'level': 'warn', 'step': 'orders', 'drop': None,
                          'text': f'{label} has no {" or ".join(missing)} objective yet.'})

    done, _ = step_progress(mission, 'intel')
    if done and not mission.get('map_img'):
        found.append({'level': 'warn', 'step': 'intel', 'drop': None,
                      'text': 'No AO map yet. The INTSUM lands better with one.'})
    return found


# ---------------------------------------------------------------------------
# Missions: carrying details forward, and cleaning what the editor sends
# ---------------------------------------------------------------------------

# What a new mission copies from the one before it in the campaign: the things
# that stay the same from week to week. The story, the enemy picture, the
# orders and the after action are written fresh.
CARRY_OVER = (
    'tz', 'orders_time', 'h_hour', 'ao', 'map', 'we_are', 'expect', 'modpack',
    'groups_in', 'who_we_are', 'unit_img', 'support', 'resupply', 'nets', 'coc',
    'respawn', 'technical', 'modpack_link', 'loadout', 'medical',
)

MAX_TEXT = 300
MAX_TEXTAREA = 6000
MAX_LIST_ITEMS = 60
MAX_ROWS = 60
MAX_CELL = 2000
MAX_IMAGES = 20
# A phase is a layer on its AO's map, and a tactical map holds twelve layers.
MAX_PHASES = 12
MAX_OBJECTIVES = 12
ROLES = ('primary', 'secondary', 'complication')
ROLE_LABELS = {'primary': 'Primary', 'secondary': 'Secondary',
               'complication': 'Complication'}


def carry_over(previous: dict | None) -> dict:
    """The fields a new mission starts with, copied from the previous one."""
    if not previous:
        return {}
    return {key: previous[key] for key in CARRY_OVER if filled(previous.get(key))}


def _cut(value, limit: int) -> str:
    return str(value if value is not None else '').replace('\r\n', '\n')[:limit]


def _image_id(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def clean_fields(patch) -> dict:
    """What the editor sent, reduced to the wizard's own fields in their own
    shapes. Unknown keys are dropped and every value is cut to length, so a
    hand-made request can store nothing the pages would not have produced.
    Phases are not here — they live in their own table (`clean_phases()`)."""
    if not isinstance(patch, dict):
        raise ValueError('That was not a set of fields.')
    out: dict = {}
    for key, value in patch.items():
        f = FIELD.get(key)
        if f is None or f.kind == 'phases':
            continue
        if f.kind in ('text', 'time'):
            out[key] = _cut(value, MAX_TEXT).strip() if f.kind == 'time' else _cut(value, MAX_TEXT)
        elif f.kind == 'date':
            day = parse_date(value)
            out[key] = day.isoformat() if day else ''
        elif f.kind == 'textarea':
            out[key] = _cut(value, MAX_TEXTAREA)
        elif f.kind in ('list', 'groups'):
            items = value if isinstance(value, list) else []
            out[key] = [_cut(item, MAX_TEXT) for item in items[:MAX_LIST_ITEMS]
                        if _scalar(item)]
        elif f.kind == 'rows':
            names = [name for name, _ in f.columns]
            items = value if isinstance(value, list) else []
            out[key] = [{name: _cut(row.get(name), MAX_CELL) for name in names}
                        for row in items[:MAX_ROWS] if isinstance(row, dict)]
        elif f.kind == 'image':
            out[key] = _image_id(value)
        elif f.kind == 'images':
            items = value if isinstance(value, list) else []
            ids = [_image_id(item) for item in items]
            out[key] = [i for i in dict.fromkeys(ids) if i][:MAX_IMAGES]
    return out


def image_ids(fields: dict) -> set[int]:
    """Every image a set of fields points at — what the caller checks belongs
    to the campaign before saving."""
    found = set()
    for key, value in fields.items():
        kind = FIELD[key].kind if key in FIELD else ''
        if kind == 'image' and value:
            found.add(value)
        elif kind == 'images':
            found.update(v for v in value or [] if v)
    return found


def clean_phases(phases, ao_ids=(), library_ids=()) -> list[dict]:
    """The phase list as the editor sent it, cleaned. An AO or library entry
    that is not the campaign's (or no longer exists) is dropped rather than
    refused: the phase is still worth keeping."""
    if not isinstance(phases, list):
        raise ValueError('That was not a list of phases.')
    if len(phases) > MAX_PHASES:
        raise ValueError(f'A mission can have at most {MAX_PHASES} phases — '
                         'one per layer on the AO map.')
    ao_ids = {int(a) for a in ao_ids}
    library_ids = {int(i) for i in library_ids}
    out = []
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        ao = _image_id(phase.get('ao_id'))
        objectives = []
        for objective in (phase.get('objectives') or [])[:MAX_OBJECTIVES]:
            if not isinstance(objective, dict):
                continue
            role = objective.get('role') if objective.get('role') in ROLES else 'primary'
            library = _image_id(objective.get('library_id'))
            objectives.append({
                'library_id': library if library in library_ids else None,
                'role': role,
                'name': _cut(objective.get('name'), 120).strip(),
                'players_do': _cut(objective.get('players_do'), 600),
                'complete_when': _cut(objective.get('complete_when'), 600),
                'intsum_hint': _cut(objective.get('intsum_hint'), 300),
                'result': _cut(objective.get('result'), 120),
            })
        out.append({
            'id': _image_id(phase.get('id')),
            'title': _cut(phase.get('title'), 200),
            'detail': _cut(phase.get('detail'), MAX_CELL),
            'ao_id': ao if ao in ao_ids else None,
            'objectives': objectives,
        })
    return out


def success_conditions(mission: dict) -> list[str]:
    """The primary objectives' success conditions, in phase order — the line
    the Objectives Library says belongs in "Success looks like"."""
    found = []
    for phase in rows(mission, 'phases'):
        for objective in phase.get('objectives') or []:
            if isinstance(objective, dict) and objective.get('role') == 'primary':
                condition = str(objective.get('complete_when') or '').strip()
                if condition and condition not in found:
                    found.append(condition)
    return found


def progress(mission: dict) -> list[dict]:
    """Every step with how much of it is done — the chips on the wizard and the
    "3/5 locked" on the campaign page. The AAR is counted apart: it is written
    after the op, so it is never behind."""
    out = []
    for step in STEPS:
        done, total = step_progress(mission, step.key)
        out.append({'key': step.key, 'title': step.title, 'number': step.number,
                    'done': done, 'total': total, 'complete': done == total})
    return out


def locked_count(mission: dict) -> tuple[int, int]:
    """(steps locked, steps to lock) before the op — the AAR left out."""
    before = [p for p in progress(mission) if p['key'] != 'aar']
    return sum(1 for p in before if p['complete']), len(before)


def next_due(items: list[dict], today: date) -> dict | None:
    """The next thing on a mission's schedule that is not yet done: an unlocked
    step or a drop that has not gone out. Overdue locks come first."""
    for item in items:
        if item['kind'] == 'gate' and item['done'] != item['total']:
            return item
        if item['kind'] == 'drop' and item['date'] >= today:
            return item
    return None


# ---------------------------------------------------------------------------
# The Objectives Library: suggested pairings
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r'[^a-z0-9]', '', str(text or '').lower())


def _names_for(entry_name: str) -> set[str]:
    """Ways a pairing might refer to an entry: its full name, an acronym in
    brackets ("VEHICLE CHECKPOINT (VCP)"), each half of a slash name, and its
    initials ("BATTLE DAMAGE ASSESSMENT" → BDA, "OBSERVATION POST" → OP)."""
    names = {_norm(entry_name)}
    bracket = re.findall(r'\(([^)]+)\)', entry_name)
    base = re.sub(r'\([^)]*\)', '', entry_name)
    names.update(_norm(b) for b in bracket)
    names.add(_norm(base))
    for part in base.split('/'):
        names.add(_norm(part))
    words = re.findall(r'[A-Za-z]+', base)
    if len(words) >= 2:
        names.add(''.join(w[0] for w in words).lower())
    return {n for n in names if n}


def pairing_ids(entries: list[dict]) -> dict[int, list[int]]:
    """For each library entry, the entries its "pairs with" text names.

    A pairing is free text written for people ("Seize bridge", "Recon first",
    "Ambush (enemy)"), so the match is forgiving: a pairing names an entry when
    one is a prefix of the other once case and punctuation are gone, at three
    letters or more. It only ever decides what the picker highlights as a
    suggestion — nothing is enforced on it.
    """
    known = [(e['id'], _names_for(e['name'])) for e in entries]
    out: dict[int, list[int]] = {}
    for entry in entries:
        matched = []
        for phrase in re.split(r'[,;]', entry.get('pairs_with') or ''):
            want = _norm(re.sub(r'\([^)]*\)', '', phrase))
            if len(want) < 2:
                continue
            for other_id, names in known:
                if other_id == entry['id'] or other_id in matched:
                    continue
                if any(n == want or (min(len(n), len(want)) >= 3
                                     and (n.startswith(want) or want.startswith(n)))
                       for n in names):
                    matched.append(other_id)
        out[entry['id']] = matched
    return out


def library_seed() -> list[dict]:
    """The library as first written, as rows for `cp_objective_library`."""
    from utils.objectives_seed import CATEGORIES
    out = []
    position = 0
    for category, blurb, kind, entries in CATEGORIES:
        for entry in entries:
            position += 1
            if kind == 'complication':
                name, players_do, hint = entry
                complete_when, pairs = '', ''
            else:
                name, players_do, complete_when, pairs = entry
                hint = ''
            out.append({'category': category, 'kind': kind, 'name': name,
                        'players_do': players_do, 'complete_when': complete_when,
                        'pairs_with': pairs, 'intsum_hint': hint,
                        'position': position})
    return out


def library_categories() -> list[tuple[str, str, str]]:
    """(category, blurb, kind) in the library's own order."""
    from utils.objectives_seed import CATEGORIES
    return [(c, blurb, kind) for c, blurb, kind, _ in CATEGORIES]


def clean_library_entry(form) -> dict:
    """An admin's edit to one library entry. Raises ValueError for a person."""
    name = _cut(form.get('name'), 120).strip()
    if not name:
        raise ValueError('An objective needs a name — the task verb as it appears in orders.')
    kind = form.get('kind') if form.get('kind') in ('objective', 'complication') else 'objective'
    category = _cut(form.get('category'), 80).strip() or 'Other'
    return {'category': category, 'kind': kind, 'name': name,
            'players_do': _cut(form.get('players_do'), 600).strip(),
            'complete_when': _cut(form.get('complete_when'), 600).strip(),
            'pairs_with': _cut(form.get('pairs_with'), 300).strip(),
            'intsum_hint': _cut(form.get('intsum_hint'), 300).strip()}


def clean_campaign(form) -> dict:
    """The campaign form. Raises ValueError for a person."""
    name = _cut(form.get('name'), 80).strip()
    if not name:
        raise ValueError('A campaign needs a name.')
    try:
        planned = int(form.get('planned_missions') or 4)
    except (TypeError, ValueError):
        planned = 4
    status = form.get('status') if form.get('status') in CAMPAIGN_STATUSES else 'active'
    return {'name': name,
            'era': _cut(form.get('era'), 120).strip(),
            'theatre': _cut(form.get('theatre'), 120).strip(),
            'summary': _cut(form.get('summary'), MAX_TEXTAREA).strip(),
            'planned_missions': max(1, min(planned, 8)),
            'status': status}


CAMPAIGN_STATUSES = ('active', 'complete', 'archived')


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

MAX_IMAGE_BYTES = 8 * 1024 * 1024
_IMAGE_MAGIC = (
    (b'\x89PNG\r\n\x1a\n', 'image/png'),
    (b'\xff\xd8\xff', 'image/jpeg'),
    (b'GIF87a', 'image/gif'),
    (b'GIF89a', 'image/gif'),
)


def image_type(data: bytes) -> str | None:
    """The image's type from its first bytes — never from the name or the
    browser's say-so, since the bytes are served back to everybody who opens
    the mission. PNG, JPEG, GIF and WebP; anything else is None."""
    head = bytes(data[:16])
    for magic, kind in _IMAGE_MAGIC:
        if head.startswith(magic):
            return kind
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    return None


def check_image(data: bytes) -> str:
    """The content type of an upload, or a ValueError for a person."""
    if not data:
        raise ValueError('That file was empty.')
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f'Images can be up to {MAX_IMAGE_BYTES // (1024 * 1024)} MB — '
                         'that one is bigger. Discord would refuse it too.')
    kind = image_type(data)
    if kind is None:
        raise ValueError('That is not an image I can use. PNG, JPEG, GIF or WebP.')
    return kind


def date_label(day: date | None) -> str:
    """'Wed 21 Oct' — how a schedule date is shown."""
    return day.strftime('%a %d %b') if day else ''


# ---------------------------------------------------------------------------
# Briefing packs
#
# A pack is a list of blocks — headings, key/value lists, paragraphs, tables,
# images — and nothing else. The PDF and the Word file are two renderers of the
# same list (web/exports.py), so they cannot say different things, and what a
# pack contains is decided here, under test, like everything else.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Pack:
    key: str
    label: str
    description: str
    filename: str


PACKS: tuple[Pack, ...] = (
    Pack('player', 'Player briefing',
         'Warning order, situation, intelligence and the OPORD. Safe to share with '
         'everyone signed up.', 'Player_Briefing'),
    Pack('leaders', "Leaders' pack",
         'Everything in the player briefing plus the FragO, timings, tasks, the ORBAT '
         'and the ops traces.', 'Leaders_Pack'),
    Pack('full', 'Full op file',
         "The whole op for the mission team's archive: the leaders' pack, the "
         'complications, the back-schedule and the after action.', 'Op_File'),
)
PACK = {p.key: p for p in PACKS}
EXPORT_FORMATS = ('pdf', 'docx')


def tz_abbrev(day, tz_name: str | None) -> str:
    """'CET', 'BST' — the zone's short name on that date, or the IANA name when
    the zone has no abbreviation (some report '+03')."""
    d = parse_date(day) or date.today()
    name = datetime.combine(d, time(12), tzinfo=zone(tz_name)).tzname() or ''
    return name if name and not name[0] in '+-' else (tz_name or DEFAULT_TZ)


def local_clock(day, clock, from_tz: str | None, to_tz: str | None) -> str:
    """A time typed in one zone, as the clock reads in another ('19:30')."""
    moment = to_utc(day, clock, from_tz)
    if moment is None:
        return ''
    return moment.astimezone(zone(to_tz)).strftime('%H:%M')


def export_filename(mission: dict, pack_key: str, fmt: str) -> str:
    base = re.sub(r'[^\w-]+', '_', text(mission, 'name') or 'op').strip('_') or 'op'
    return f'{base}_{PACK[pack_key].filename}.{fmt}'


def _objective_rows(phase: dict, roles) -> list[str]:
    out = []
    for o in phase.get('objectives') or []:
        if isinstance(o, dict) and o.get('role') in roles and str(o.get('name') or '').strip():
            label = ROLE_LABELS[o['role']]
            line = f"{label}: {o['name'].strip()}"
            done = str(o.get('complete_when') or '').strip()
            if done and o['role'] != 'complication':
                line += f' — complete when {done[0].lower()}{done[1:]}'
            out.append(line)
    return out


def document_blocks(mission: dict, pack_key: str, ctx: PostContext, *,
                    orbat: dict | None = None, aos: dict | None = None,
                    today: date | None = None,
                    phase_traces: list | None = None) -> list[dict]:
    """The pack's contents, as blocks.

    `mission` is the fields with `phases` filled in, as for the posts. `orbat`
    is `{'name', 'squads': [...], 'nets': [...]}` in the database's shapes, or
    None; `aos` maps AO id → name; `phase_traces` is `[{key, caption}]`, the
    phases drawn from their AO maps, which lead the annexes of the leaders'
    pack and the full op file. Private fields are never read. Complications
    are Zeus's plan, so only the full op file — the mission team's own copy —
    carries them; the INTSUM's hints are what players get.

    Block kinds: h1, h2, kv (rows of [label, value]), p, note, list (items),
    table (head, rows, widths as fractions), img (id, caption, big), break.
    """
    if pack_key not in PACK:
        raise ValueError(f'Unknown pack: {pack_key}')
    aos = aos or {}
    blocks: list[dict] = []
    tz = mission_tz(mission, ctx.settings, ctx.guild_tz)
    abbrev = tz_abbrev(mission.get('op_date'), tz)

    def kv(rows):
        rows = [[k, v if isinstance(v, str) else ', '.join(v)] for k, v in rows
                if filled(v)]
        if rows:
            blocks.append({'t': 'kv', 'rows': rows})

    def para(value):
        if filled(value):
            blocks.append({'t': 'p', 'text': str(value).strip()})

    def image(key, caption, big=False):
        if mission.get(key):
            blocks.append({'t': 'img', 'id': mission[key], 'caption': caption, 'big': big})

    op_day = parse_date(mission.get('op_date'))
    when = []
    if op_day:
        when.append(op_day.strftime('%A %d %B %Y'))
    for label, key in (('orders', 'orders_time'), ('H-Hour', 'h_hour')):
        if parse_time(mission.get(key)):
            when.append(f"{label} {parse_time(mission.get(key)).strftime('%H:%M')} {abbrev}")

    # 1. Warning order
    blocks.append({'t': 'h1', 'text': '1. Warning order'})
    kv([['Op night', ' · '.join(when)],
        ['In-universe DTG', text(mission, 'dtg')],
        ['Area of operations', ' · '.join(x for x in (text(mission, 'ao'), text(mission, 'map')) if x)],
        ['We are', text(mission, 'we_are')],
        ['Enemy', text(mission, 'enemy_short')],
        ['Expect', text(mission, 'expect')],
        ['Prepare', text(mission, 'modpack')],
        ['Groups', lines(mission, 'groups_in')]])
    if filled(mission.get('hook')):
        blocks.append({'t': 'note', 'text': text(mission, 'hook')})

    # 2. Situation
    blocks.append({'t': 'h1', 'text': '2. Situation'})
    if text(mission, 'source_label'):
        blocks.append({'t': 'h2', 'text': text(mission, 'source_label')})
    para(mission.get('situation'))
    if lines(mission, 'fronts'):
        blocks.append({'t': 'list', 'items': lines(mission, 'fronts')})
    if text(mission, 'who_we_are'):
        blocks.append({'t': 'h2', 'text': 'Who we are'})
        para(mission.get('who_we_are'))
    image('unit_img', 'Unit')

    # 3. Intelligence
    blocks.append({'t': 'h1', 'text': '3. Intelligence'})
    kv([['Enemy forces', text(mission, 'enemy_who')],
        ['Strength', text(mission, 'enemy_strength')],
        ['Equipment', text(mission, 'equipment')],
        ['Tactics', text(mission, 'tactics')],
        ['Most likely course of action', text(mission, 'mlcoa')],
        ['Most dangerous course of action', text(mission, 'mdcoa')]])
    if text(mission, 'ground'):
        blocks.append({'t': 'h2', 'text': 'Ground'})
        para(mission.get('ground'))
    kv([['Weather and light', text(mission, 'weather')],
        ['Key places', ' · '.join(lines(mission, 'objectives'))]])
    image('map_img', 'Area of operations')
    image('recog_img', 'Recognition sheet: enemy vehicles, weapons and uniforms. Learn '
                       'these to tell friend from foe at a glance and report contacts by type.')

    # 4. Operation order
    blocks.append({'t': 'h1', 'text': '4. Operation order'})
    success = text(mission, 'end_state') or '; '.join(success_conditions(mission))
    kv([['Mission', text(mission, 'mission')],
        ["Commander's intent", text(mission, 'intent')],
        ['Success looks like', success]])
    phases = rows(mission, 'phases')
    if phases:
        roles = ROLES if pack_key == 'full' else ('primary', 'secondary')
        blocks.append({'t': 'h2', 'text': 'Phases'})
        table = []
        for i, phase in enumerate(phases, 1):
            what = '\n'.join([phase.get('detail', '')] + _objective_rows(phase, roles)).strip()
            try:
                ao = aos.get(int(phase.get('ao_id'))) or ''
            except (TypeError, ValueError):
                ao = ''
            table.append([str(i), phase.get('title', ''), ao, what])
        blocks.append({'t': 'table', 'head': ['#', 'Phase', 'AO', 'What happens'],
                       'widths': [0.06, 0.28, 0.14, 0.52], 'rows': table})
    support_rows = [['Field support', text(mission, 'support')],
                    ['Resupply', text(mission, 'resupply')],
                    ['Respawn and re-insert', text(mission, 'respawn')],
                    ['Chain of command', text(mission, 'coc')],
                    ['Technical', text(mission, 'technical')],
                    ['Modpack', text(mission, 'modpack_link')]]
    if any(filled(v) for _, v in support_rows):
        blocks.append({'t': 'h2', 'text': 'Service support and command'})
    kv(support_rows)
    nets = [[n.get('net', ''), n.get('channel', '')] for n in rows(mission, 'nets')]
    if nets:
        blocks.append({'t': 'table', 'head': ['Net', 'Channel'], 'widths': [0.5, 0.5],
                       'rows': nets})

    if pack_key != 'player':
        # 5. FragO
        blocks.append({'t': 'break'})
        blocks.append({'t': 'h1', 'text': f"5. FragO {text(mission, 'frago_no')}".strip()})
        timings = rows(mission, 'timings')
        if timings:
            guild_abbrev = tz_abbrev(mission.get('op_date'), ctx.guild_tz)
            two = guild_abbrev != abbrev
            head = ['Event', f'Time ({abbrev})'] + ([f'Time ({guild_abbrev})'] if two else [])
            table = []
            for t in timings:
                clock = parse_time(t.get('time'))
                row = [t.get('event', ''), clock.strftime('%H:%M') if clock else t.get('time', '')]
                if two:
                    row.append(local_clock(mission.get('op_date'), t.get('time'), tz, ctx.guild_tz))
                table.append(row)
            blocks.append({'t': 'h2', 'text': 'Timings'})
            blocks.append({'t': 'table', 'head': head, 'rows': table,
                           'widths': [0.56, 0.22, 0.22] if two else [0.7, 0.3]})
        tasks = rows(mission, 'tasks')
        if tasks:
            blocks.append({'t': 'h2', 'text': 'Tasks'})
            blocks.append({'t': 'table', 'head': ['Callsign', 'Tasks'], 'widths': [0.22, 0.78],
                           'rows': [[t.get('callsign', ''), t.get('tasks', '')] for t in tasks]})
        kv([['Actions on', text(mission, 'actions_on')],
            ['Loadout minimums', text(mission, 'loadout')],
            ['Medical', text(mission, 'medical')]])
        if orbat and orbat.get('squads'):
            blocks.append({'t': 'h2', 'text': f"ORBAT — {orbat.get('name', '')}".rstrip(' —')})
            table = []
            for squad in orbat['squads']:
                slots = [s.get('role_name', '') for s in squad.get('slots') or []]
                table.append([squad.get('name', ''), squad.get('reserved_unit') or '',
                              squad.get('radio') or '', '\n'.join(slots)])
            blocks.append({'t': 'table', 'head': ['Squad', 'Unit', 'Radio', 'Slots'],
                           'widths': [0.26, 0.14, 0.2, 0.4], 'rows': table})
            orbat_nets = [[n.get('name', ''), n.get('channel') or '']
                          for n in orbat.get('nets') or [] if not n.get('inactive')]
            if orbat_nets and not nets:
                blocks.append({'t': 'table', 'head': ['Net', 'Channel'], 'widths': [0.5, 0.5],
                               'rows': orbat_nets})
        annexes = [(t['key'], t['caption']) for t in phase_traces or []]
        annexes += [(image_id, 'ops trace') for image_id in mission.get('traces') or []]
        for i, (image_id, caption) in enumerate(annexes):
            blocks.append({'t': 'img', 'id': image_id, 'big': True,
                           'caption': f'Annex {chr(65 + i) if i < 26 else i + 1} · {caption}'})

    if pack_key == 'full':
        blocks.append({'t': 'break'})
        blocks.append({'t': 'h1', 'text': '6. Back-schedule'})
        items = schedule(mission, ctx.settings, today or date.today(), ctx.guild_tz)
        if items:
            blocks.append({'t': 'table', 'head': ['Date', 'Day', 'Item', 'Status'],
                           'widths': [0.22, 0.12, 0.42, 0.24],
                           'rows': [[date_label(i['date']), d_label(i['d']), i['label'],
                                     i['status'][1]] for i in items]})
        else:
            para('No op date yet, so no schedule.')
        blocks.append({'t': 'h1', 'text': '7. After action'})
        para(mission.get('outcome'))
        results = [[r.get('objective', ''), r.get('result', '')] for r in rows(mission, 'results')]
        if results:
            blocks.append({'t': 'table', 'head': ['Objective', 'Result'], 'widths': [0.6, 0.4],
                           'rows': results})
        kv([['Stats', text(mission, 'stats')], ['Next', text(mission, 'next_hook')]])
        for image_id in mission.get('aar_imgs') or []:
            blocks.append({'t': 'img', 'id': image_id, 'caption': 'Screenshot'})
    return blocks


def block_images(blocks: list[dict]) -> list:
    """The image ids a pack needs, in order, once each."""
    return list(dict.fromkeys(b['id'] for b in blocks if b['t'] == 'img'))


# ---------------------------------------------------------------------------
# Maps (milestone 5)
#
# A campaign has one map with an outline per AO, and each AO a detailed map on
# which every phase fought there is a layer. The maps are ordinary tactical
# maps (utils/tacmap.py), edited in the existing editor; the planner only
# keeps their **layers** in step with its AOs and phases.
#
# Layers, not items, carry the link, because a layer has an id that survives
# every save and an item has none: the AO's outline lives on the layer
# `cpao<ao id>` and a phase's graphics on `cpp<phase id>`. Anything else on a
# map — the base "Plan" layer, an enemy picture — is the planner's to ignore,
# and it shows on every ops trace as shared context.
# ---------------------------------------------------------------------------

AO_LAYER = 'cpao'
PHASE_LAYER = 'cpp'


def ao_layer_id(ao_id) -> str:
    return f'{AO_LAYER}{int(ao_id)}'


def phase_layer_id(phase_id) -> str:
    return f'{PHASE_LAYER}{int(phase_id)}'


def _is_phase_layer(layer_id: str) -> bool:
    return layer_id.startswith(PHASE_LAYER) and layer_id[len(PHASE_LAYER):].isdigit()


def _is_ao_layer(layer_id: str) -> bool:
    return layer_id.startswith(AO_LAYER) and layer_id[len(AO_LAYER):].isdigit()


def phase_layer_name(mission_name: str, index: int, title: str) -> str:
    """What a phase's layer is called on its AO's map: short, because a map
    shows its layer names in a narrow panel, and unique enough that two
    missions fought in one AO can be told apart."""
    head = (mission_name or 'Mission').strip()
    body = f'P{index}' + (f' {title.strip()}' if (title or '').strip() else '')
    name = f'{head} · {body}'
    if len(name) > tacmap.MAX_LAYER_NAME:
        room = tacmap.MAX_LAYER_NAME - len(body) - 3
        name = (f'{head[:max(room, 4)].rstrip()} · {body}')[:tacmap.MAX_LAYER_NAME]
    return name


def _ao_outline(doc: dict, index: int, name: str) -> dict:
    """A placeholder outline for a new AO: a dashed box in a three-wide grid
    across the sheet, to be dragged and reshaped onto the real ground."""
    col, row = index % 3, (index // 3) % 3
    w, h = doc['width'] / 4.5, doc['height'] / 4.5
    x = doc['width'] * (0.08 + col * 0.31)
    y = doc['height'] * (0.08 + row * 0.31)
    colour = tacmap.LINE_COLOURS[index % 8][0]
    return {'kind': 'area', 'side': tacmap.DEFAULT_SIDE, 'layer': None, 'label': name[:tacmap.MAX_LABEL],
            'note': '', 'size': 1.0, 'color': colour, 'style': 'dashed',
            'points': [[round(x, 1), round(y, 1)], [round(x + w, 1), round(y, 1)],
                       [round(x + w, 1), round(y + h, 1)], [round(x, 1), round(y + h, 1)]]}


def sync_campaign_map(doc: dict, aos: list) -> tuple[dict, list]:
    """The campaign map with one layer per AO (`aos`: [{id, name}] in order).

    A new AO gets a layer and a placeholder outline; a renamed one has its
    layer renamed, and its outline's label too when that still read the old
    name; a deleted one loses its layer and what was drawn on it, since the
    outline was the planner's. Layers the planner did not make are left
    alone. Returns the document and notes for whoever asked.
    """
    doc = json.loads(json.dumps(doc))
    notes = []
    wanted = {ao_layer_id(a['id']): (a['name'] or 'AO')[:tacmap.MAX_LAYER_NAME] for a in aos}
    gone = [l['id'] for l in doc['layers'] if _is_ao_layer(l['id']) and l['id'] not in wanted]
    if gone:
        doc['layers'] = [l for l in doc['layers'] if l['id'] not in gone]
        doc['items'] = [i for i in doc['items'] if i['layer'] not in gone]
    by_id = {l['id']: l for l in doc['layers']}
    for index, ao in enumerate(aos):
        key, name = ao_layer_id(ao['id']), wanted[ao_layer_id(ao['id'])]
        layer = by_id.get(key)
        if layer is None:
            if len(doc['layers']) >= tacmap.MAX_LAYERS:
                notes.append(f"No room on the campaign map for {name}: a map holds "
                             f"{tacmap.MAX_LAYERS} layers.")
                continue
            layer = {'id': key, 'name': name, 'visible': True}
            doc['layers'].append(layer)
            by_id[key] = layer
            outline = _ao_outline(doc, index, name)
            outline['layer'] = key
            doc['items'].append(outline)
            continue
        if layer['name'] != name:
            for item in doc['items']:
                if item['layer'] == key and item.get('label') == layer['name']:
                    item['label'] = name[:tacmap.MAX_LABEL]
            layer['name'] = name
    return doc, notes


def ao_map_doc(campaign_doc: dict | None, ao_id, ao_name: str) -> dict:
    """A new detailed map for an AO: the campaign map's terrain, corners and
    place-name settings, its base layer, and the AO's outline copied in on a
    layer of its own as a reference to plan inside."""
    doc = tacmap.blank_doc()
    if campaign_doc:
        for key in ('width', 'height', 'background', 'arma', 'places', 'grid'):
            doc[key] = json.loads(json.dumps(campaign_doc[key]))
        key = ao_layer_id(ao_id)
        outline = [json.loads(json.dumps(i)) for i in campaign_doc['items'] if i['layer'] == key]
        if outline:
            doc['layers'].append({'id': key, 'name': (ao_name or 'AO')[:tacmap.MAX_LAYER_NAME],
                                  'visible': True})
            doc['items'].extend(outline)
    return tacmap.parse(doc).doc


def sync_phase_layers(doc: dict, phases: list) -> tuple[dict, list]:
    """An AO map with one layer per phase fought there (`phases`: [{id, name}]
    in campaign order). Phase layers come after every other layer, in phase
    order, so later phases draw over earlier ones. A phase layer whose phase
    has gone — deleted, or moved to another AO — is removed when nothing is
    drawn on it, and kept (and said) when something is: that is somebody's
    work. Returns the document and the notes."""
    doc = json.loads(json.dumps(doc))
    notes = []
    wanted = [(phase_layer_id(p['id']), p['name'][:tacmap.MAX_LAYER_NAME]) for p in phases]
    wanted_ids = {k for k, _ in wanted}
    used = {i['layer'] for i in doc['items']}
    existing = {l['id']: l for l in doc['layers']}
    kept_orphans = []
    others = []
    for layer in doc['layers']:
        if _is_phase_layer(layer['id']):
            if layer['id'] not in wanted_ids and layer['id'] in used:
                kept_orphans.append(layer)
                notes.append(f"Kept the layer {layer['name']}: its phase is no longer fought "
                             'in this AO, but something is drawn on it.')
            continue
        others.append(layer)
    room = tacmap.MAX_LAYERS - len(others) - len(kept_orphans)
    phase_layers = []
    for key, name in wanted:
        if len(phase_layers) >= room:
            notes.append(f'No room for the layer {name}: a map holds {tacmap.MAX_LAYERS} '
                         'layers. Delete one you no longer need in the map editor.')
            continue
        layer = existing.get(key) or {'id': key, 'name': name, 'visible': True}
        layer['name'] = name
        phase_layers.append(layer)
    doc['layers'] = others + kept_orphans + phase_layers
    return doc, notes


# -- ops traces ---------------------------------------------------------------

TRACE_WIDTH = 1600          # pixels: sharp on an A4 page, small enough to embed
TRACE_ASPECT = 1.5          # width / height of a trace
TRACE_MARGIN = 0.15         # of the drawn area, on every side
TRACE_MIN_FRACTION = 0.12   # of the sheet: the closest a trace zooms in


def trace_doc(doc: dict, layer_id: str) -> dict:
    """The map as one phase's trace shows it: that phase's layer, and every
    layer that belongs to no phase as it was left switched (the base plan,
    the AO outline, an enemy picture). Other phases are hidden."""
    doc = json.loads(json.dumps(doc))
    for layer in doc['layers']:
        if layer['id'] == layer_id:
            layer['visible'] = True
        elif _is_phase_layer(layer['id']):
            layer['visible'] = False
    return doc


def _item_bounds(item: dict) -> tuple | None:
    if item.get('points'):
        xs = [p[0] for p in item['points']]
        ys = [p[1] for p in item['points']]
        return min(xs), min(ys), max(xs), max(ys)
    if 'x' in item:
        r = tacmap.UNIT_BOX * item.get('size', 1.0)
        return item['x'] - r, item['y'] - r, item['x'] + r, item['y'] + r * 1.6
    return None


def trace_view(doc: dict, layer_id: str) -> tuple | None:
    """Where an ops trace looks: the box around the phase's graphics and the
    AO outline, with a margin, at `TRACE_ASPECT`, never closer than
    `TRACE_MIN_FRACTION` of the sheet and never outside it. None when nothing
    is drawn on the phase's layer — an empty trace is not worth a page."""
    mine = [_item_bounds(i) for i in doc['items'] if i['layer'] == layer_id]
    mine = [b for b in mine if b]
    if not mine:
        return None
    context = [_item_bounds(i) for i in doc['items'] if _is_ao_layer(i['layer'])]
    boxes = mine + [b for b in context if b]
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    w, h = (x1 - x0) * (1 + 2 * TRACE_MARGIN), (y1 - y0) * (1 + 2 * TRACE_MARGIN)
    w = max(w, doc['width'] * TRACE_MIN_FRACTION, h * TRACE_ASPECT)
    h = w / TRACE_ASPECT
    w, h = min(w, doc['width']), min(h, doc['height'])
    if w / h > TRACE_ASPECT:
        w = h * TRACE_ASPECT
    else:
        h = w / TRACE_ASPECT
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    x = min(max(cx - w / 2, 0), doc['width'] - w)
    y = min(max(cy - h / 2, 0), doc['height'] - h)
    return round(x, 2), round(y, 2), round(w, 2), round(h, 2)


def trace_zoom(doc: dict, view: tuple, width_px: int = TRACE_WIDTH) -> int:
    """The terrain level that gives a trace about one tile pixel per picture
    pixel: deep enough to be sharp, never deeper than the terrain has."""
    background = doc['background']
    fraction = view[2] / doc['width']
    needed = width_px / (256 * fraction)
    zoom = max(0, math.ceil(math.log2(max(needed, 1))))
    return min(zoom, background.get('max_zoom', tacmap.MAX_TILE_ZOOM), tacmap.MAX_TILE_ZOOM)
