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
                   what somebody approved

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
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    kind: str = 'text'            # text | textarea | date | time | list | rows | image | images | groups
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
             Field('phases', 'Phases', 'rows', required=True, wide=True,
                   columns=(('title', 'Phase 1 – Infiltration, HOCHABRUNN'),
                            ('detail', 'What happens in this phase'))),
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


# ---------------------------------------------------------------------------
# Reading values
# ---------------------------------------------------------------------------

def filled(value) -> bool:
    """Whether a field holds anything. A list or a list of rows counts as filled
    when any entry has any non-blank text in it; blank rows the editor keeps
    around for typing into do not."""
    if value is None:
        return False
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, dict):
                if any(str(v or '').strip() for v in item.values()):
                    return True
            elif str(item or '').strip():
                return True
        return False
    if isinstance(value, dict):
        return any(str(v or '').strip() for v in value.values())
    return bool(str(value).strip())


def text(mission: dict, key: str) -> str:
    return str(mission.get(key) or '').strip()


def lines(mission: dict, key: str) -> list[str]:
    return [str(x).strip() for x in (mission.get(key) or []) if str(x or '').strip()]


def rows(mission: dict, key: str) -> list[dict]:
    """The non-blank rows of a rows field, every value stripped."""
    out = []
    for row in mission.get(key) or []:
        if not isinstance(row, dict):
            continue
        clean = {k: str(v or '').strip() for k, v in row.items()}
        if any(clean.values()):
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
    }


def clean_settings(form: dict, *, role_ids=(), channel_ids=()) -> dict:
    """The settings form, validated. Raises ValueError with a message meant for
    a person. `role_ids` and `channel_ids` are what the page offered, so a
    hand-edited form cannot store a role or channel the guild does not have."""
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
        if text(mission, 'end_state'):
            out.append(f"**Success:** {text(mission, 'end_state')}")
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

    done, _ = step_progress(mission, 'intel')
    if done and not mission.get('map_img'):
        found.append({'level': 'warn', 'step': 'intel', 'drop': None,
                      'text': 'No AO map yet. The INTSUM lands better with one.'})
    return found
