"""The campaign planner's web side — the glue between the routes in
`web/app.py`, the rules in `utils/campaign.py` and the tables in
`utils/database.py`.

It owns no rules. Everything that decides something — what a drop says, when
it goes out, whether a step is locked, what a hand-made request may store — is
a call into `utils/campaign.py`; this module reads the database, makes those
calls and shapes the answers for a template or for the wizard's JSON. A
`ValueError` raised here is a message meant for a person, as everywhere in
`web/`.
"""

from datetime import date, datetime, timedelta

from utils import campaign as rules
from utils import database
from web.guilds import mentionable_roles, postable_channels


class NotFound(LookupError):
    """A campaign, mission or image that is not this guild's. The route turns
    it into the site's usual "not allowed" page, so an id from another server
    reads the same as one that does not exist."""


# ---------------------------------------------------------------------------
# Settings (milestone 1)
# ---------------------------------------------------------------------------

async def settings(guild_id) -> dict:
    """A guild's planner settings, with defaults filled in."""
    return rules.settings_with_defaults(await database.get_cp_settings(str(guild_id)))


async def settings_page(guild) -> dict:
    """Everything `cp_settings.html` needs besides the usual guild context."""
    current = await settings(guild.id)
    channels = postable_channels(guild)
    roles = mentionable_roles(guild)
    channel_ids = {str(c.id) for c in channels}
    role_ids = {str(r.id) for r in roles}
    # A setting whose channel or role has gone (deleted, or the bot lost
    # access) would silently stop drops — say so on the page.
    problems = []
    for key, what in (('drop_channel_id', 'drop channel'),
                      ('team_channel_id', 'mission team channel')):
        if current[key] and current[key] not in channel_ids:
            problems.append(f"I can't post in the {what} any more — pick it again.")
    if current['unit_role_id'] and current['unit_role_id'] not in role_ids:
        problems.append('The unit role no longer exists — pick it again.')
    return {
        'settings': current,
        'channels': channels,
        'roles': roles,
        'weekdays': list(enumerate(rules.WEEKDAYS)),
        'openers': [(key, rules.DROP[key].label) for key in rules.ORBAT_OPENERS],
        'default_survey_url': rules.DEFAULT_SURVEY_URL,
        'problems': problems,
    }


async def save_settings(guild, form) -> str:
    """Validate and store the settings form. Raises ValueError for a person."""
    clean = rules.clean_settings(
        form,
        role_ids=[r.id for r in mentionable_roles(guild)],
        channel_ids=[c.id for c in postable_channels(guild)],
    )
    await database.save_cp_settings(str(guild.id), clean)
    if not clean['drop_channel_id']:
        return 'Planner settings saved. Drops will not post until a drop channel is set.'
    return 'Planner settings saved.'


# ---------------------------------------------------------------------------
# Looking things up, scoped to the guild
# ---------------------------------------------------------------------------

async def campaign_of(guild, campaign_id: int) -> dict:
    campaign = await database.get_cp_campaign(campaign_id)
    if campaign is None or campaign['guild_id'] != str(guild.id):
        raise NotFound('No such campaign on this server.')
    return campaign


async def mission_of(guild, mission_id: int, campaign_id: int | None = None) -> dict:
    mission = await database.get_cp_mission(mission_id)
    if (mission is None or mission['guild_id'] != str(guild.id)
            or (campaign_id is not None and mission['campaign_id'] != campaign_id)):
        raise NotFound('No such mission on this server.')
    return mission


def today_in(tz_name: str) -> date:
    return datetime.now(rules.zone(tz_name)).date()


# ---------------------------------------------------------------------------
# The campaigns list
# ---------------------------------------------------------------------------

def _mission_summary(mission: dict, plan: dict, today: date, tz: str,
                     phases: dict) -> dict:
    """One line of a campaign: the mission, its date, how much is locked and
    what is next. `phases` is `database.cp_phase_titles()` for the missions
    being listed — the phases live in their own table, and without them the
    Orders step would never read as locked here."""
    fields = {**mission['fields'], 'phases': phases.get(mission['id'], [])}
    locked, total = rules.locked_count(fields)
    items = rules.schedule(fields, plan, today, tz)
    upcoming = rules.next_due(items, today)
    return {
        'id': mission['id'],
        'name': mission['name'],
        'op_date': mission['op_date'],
        'op_label': rules.date_label(mission['op_date']),
        'past': bool(mission['op_date'] and mission['op_date'] < today),
        'locked': locked,
        'total': total,
        'next': ({'label': upcoming['label'], 'date': rules.date_label(upcoming['date']),
                  'status': upcoming['status']} if upcoming else None),
    }


async def campaigns_page(guild, tz: str) -> dict:
    plan = await settings(guild.id)
    today = today_in(tz)
    campaigns = await database.list_cp_campaigns(str(guild.id))
    phases = await database.cp_phase_titles(
        [m['id'] for c in campaigns for m in c['missions']])
    shaped = []
    for campaign in campaigns:
        missions = [_mission_summary(m, plan, today, tz, phases) for m in campaign['missions']]
        shaped.append({**campaign, 'missions': missions,
                       'open_slots': max(0, campaign['planned_missions'] - len(missions))})
    return {
        'campaigns': [c for c in shaped if c['status'] != 'archived'],
        'archived': [c for c in shaped if c['status'] == 'archived'],
        'statuses': rules.CAMPAIGN_STATUSES,
    }


async def create_campaign(guild, form, member) -> int:
    values = rules.clean_campaign(form)
    return await database.create_cp_campaign(
        str(guild.id), values, str(member.id), member.display_name)


# ---------------------------------------------------------------------------
# One campaign
# ---------------------------------------------------------------------------

async def campaign_page(guild, campaign: dict, tz: str) -> dict:
    plan = await settings(guild.id)
    today = today_in(tz)
    missions = await database.list_cp_missions(campaign['id'])
    phases = await database.cp_phase_titles([m['id'] for m in missions])
    summaries = [_mission_summary(m, plan, today, tz, phases) for m in missions]
    last = missions[-1] if missions else None
    # The next mission defaults to four weeks after the last one: the cycle the
    # drops are built around.
    suggested = (last['op_date'] + timedelta(days=28)) if last and last['op_date'] else None
    return {
        'campaign': campaign,
        'missions': summaries,
        'open_slots': list(range(len(missions) + 1, campaign['planned_missions'] + 1)),
        'aos': await database.list_cp_aos(campaign['id']),
        'statuses': rules.CAMPAIGN_STATUSES,
        'suggested_date': suggested.isoformat() if suggested else '',
        'carries_from': last['name'] if last else None,
    }


async def update_campaign(campaign: dict, form, member) -> None:
    await database.update_cp_campaign(campaign['id'], rules.clean_campaign(form),
                                      member.display_name)


async def add_ao(campaign: dict, form) -> str:
    name = str(form.get('name') or '').strip()[:80]
    if not name:
        raise ValueError('An AO needs a name.')
    await database.add_cp_ao(campaign['id'], name)
    return f'Added the {name} AO.'


async def rename_ao(campaign: dict, ao_id: int, form) -> str:
    name = str(form.get('name') or '').strip()[:80]
    if not name:
        raise ValueError('An AO needs a name.')
    await database.rename_cp_ao(ao_id, campaign['id'], name)
    return 'AO renamed.'


async def create_mission(campaign: dict, form, member) -> int:
    """A new mission at the end of the campaign, starting from the stable
    details of the one before it (`rules.CARRY_OVER`)."""
    name = str(form.get('name') or '').strip()[:rules.MAX_TEXT]
    if not name:
        raise ValueError('A mission needs a name.')
    op_date = rules.parse_date(form.get('op_date'))
    missions = await database.list_cp_missions(campaign['id'])
    fields = rules.carry_over(missions[-1]['fields']) if missions else {}
    fields.update({'name': name, 'op_date': op_date.isoformat() if op_date else ''})
    return await database.create_cp_mission(campaign['id'], name, op_date, fields,
                                            member.display_name)


# ---------------------------------------------------------------------------
# One mission: what the wizard and the schedule page show
# ---------------------------------------------------------------------------

def _jsonable_item(item: dict) -> dict:
    out = {k: v for k, v in item.items() if k not in ('date', 'at', 'status')}
    out['date'] = item['date'].isoformat()
    out['date_label'] = rules.date_label(item['date'])
    out['d_label'] = rules.d_label(item['d'])
    out['status'] = list(item['status'])
    if item.get('at'):
        out['at'] = item['at'].isoformat()
        out['ts'] = int(item['at'].timestamp())
    else:
        out.pop('at', None)
    return out


async def post_context(guild_id: str, plan: dict, tz: str) -> rules.PostContext:
    channels = await database.get_guild_channels(guild_id)
    orbat = channels.get('orbat')
    return rules.PostContext(settings=plan, guild_tz=tz,
                             orbat_channel=f'<#{orbat}>' if orbat else '#orbat')


async def mission_state(guild, mission: dict, tz: str) -> dict:
    """The mission and everything worked out from it: progress, the schedule,
    each drop's text as it would post, and the checks. One answer for both the
    wizard (as JSON) and the schedule page (as a template), so they cannot
    disagree."""
    plan = await settings(guild.id)
    phases = await database.get_cp_phases(mission['id'])
    data = {**mission['fields'], 'phases': phases}
    ctx = await post_context(str(guild.id), plan, tz)
    today = today_in(tz)

    drops = []
    for drop in rules.DROPS:
        body = rules.build_post(data, drop.key, ctx)
        parts = rules.split_message(body)
        drops.append({'key': drop.key, 'label': drop.label, 'step': drop.step,
                      'text': body, 'chars': len(body), 'parts': parts,
                      'images': rules.drop_images(data, drop.key)})

    schedule = rules.schedule(data, plan, today, tz)
    image_meta = await database.cp_image_meta(mission['campaign_id'],
                                              rules.image_ids(mission['fields']))
    return {
        'id': mission['id'],
        'campaign_id': mission['campaign_id'],
        'name': mission['name'],
        'fields': mission['fields'],
        'phases': phases,
        'progress': rules.progress(data),
        'locked': rules.locked_count(data),
        'schedule': [_jsonable_item(i) for i in schedule],
        'drops': drops,
        'checks': rules.checks(data, ctx),
        'images': {str(k): {'filename': v['filename'], 'bytes': v['bytes']}
                   for k, v in image_meta.items()},
        'updated_by': mission.get('updated_by_name'),
        'tz': tz,
        'settings': {'drop_weekday': rules.WEEKDAYS[plan['drop_weekday']],
                     'drop_time': plan['drop_time'],
                     'orbat_opens': rules.DROP[plan['orbat_opens']].label},
    }


async def patch_mission(guild, mission: dict, payload: dict, member, tz: str) -> dict:
    """Save what the wizard sent — any fields, and the phase list when it is
    included — and answer with the recomputed state."""
    fields = rules.clean_fields(payload.get('fields') or {})
    wanted = rules.image_ids(fields)
    if wanted:
        owned = await database.cp_image_meta(mission['campaign_id'], wanted)
        missing = wanted - set(owned)
        if missing:
            raise ValueError("One of those images isn't in this campaign. Upload it again.")

    if 'phases' in payload:
        aos = await database.list_cp_aos(mission['campaign_id'])
        library = await database.list_cp_objectives(str(guild.id))
        phases = rules.clean_phases(payload['phases'], [a['id'] for a in aos],
                                    [e['id'] for e in library])
        await database.save_cp_phases(mission['id'], phases)

    if fields:
        name = None
        if 'name' in fields:
            name = fields['name'].strip() or 'Untitled mission'
        op_date = fields.get('op_date') if 'op_date' in fields else None
        await database.patch_cp_mission(mission['id'], mission['campaign_id'], fields,
                                        name, op_date, member.display_name)

    fresh = await database.get_cp_mission(mission['id'])
    return await mission_state(guild, fresh, tz)


async def wizard_page(guild, mission: dict, tz: str) -> dict:
    """What the wizard's template needs to draw the empty editor; the values
    themselves arrive as JSON, so the page and the API cannot drift."""
    from cogs.slots import UNIT_ROLES
    campaign = await database.get_cp_campaign(mission['campaign_id'])
    aos = await database.list_cp_aos(mission['campaign_id'])
    state = await mission_state(guild, mission, tz)
    return {
        'campaign': campaign,
        'mission': mission,
        'steps': rules.STEPS,
        'state': state,
        'aos': aos,
        'aos_json': [{'id': a['id'], 'name': a['name']} for a in aos],
        'units': sorted(UNIT_ROLES),
        'roles': rules.ROLE_LABELS,
    }


async def schedule_page(guild, mission: dict, tz: str) -> dict:
    campaign = await database.get_cp_campaign(mission['campaign_id'])
    state = await mission_state(guild, mission, tz)
    return {'campaign': campaign, 'mission': mission, 'state': state,
            'steps': {s.key: s for s in rules.STEPS}}


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

async def upload_image(campaign: dict, upload, member) -> dict:
    data = await upload.read(rules.MAX_IMAGE_BYTES + 1)
    kind = rules.check_image(data)
    filename = (getattr(upload, 'filename', '') or 'image').rsplit('/', 1)[-1][:120]
    image_id = await database.add_cp_image(campaign['id'], filename, kind, data,
                                           member.display_name)
    return {'id': image_id, 'filename': filename, 'bytes': len(data)}


async def image_of(guild, image_id: int) -> dict:
    image = await database.get_cp_image(image_id)
    if image is None or image['guild_id'] != str(guild.id):
        raise NotFound('No such image on this server.')
    return image


# ---------------------------------------------------------------------------
# The objectives library
# ---------------------------------------------------------------------------

async def library(guild) -> list[dict]:
    """The guild's library — seeded the first time — each entry with the ids
    its pairings point at."""
    await database.ensure_objective_library(str(guild.id), rules.library_seed())
    entries = await database.list_cp_objectives(str(guild.id))
    pairs = rules.pairing_ids(entries)
    return [{**e, 'pairs': pairs.get(e['id'], [])} for e in entries]


async def library_page(guild) -> dict:
    entries = await library(guild)
    known = {c for c, _, _ in rules.library_categories()}
    blurbs = {c: b for c, b, _ in rules.library_categories()}
    order = [c for c, _, _ in rules.library_categories()]
    order += sorted({e['category'] for e in entries} - known)
    grouped = [(c, blurbs.get(c, ''), [e for e in entries if e['category'] == c])
               for c in order]
    names = {e['id']: e['name'] for e in entries}
    return {'groups': [g for g in grouped if g[2]],
            'categories': order, 'names': names, 'count': len(entries)}


async def save_library_entry(guild, entry_id, form) -> str:
    values = rules.clean_library_entry(form)
    if entry_id is not None and await database.get_cp_objective(entry_id, str(guild.id)) is None:
        raise NotFound('No such objective in this library.')
    await database.save_cp_objective(str(guild.id), entry_id, values)
    return f"Saved {values['name']}."
