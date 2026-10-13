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

from cogs.campaigns import mission_drops, post_context
from utils import campaign as rules
from utils import database, tacmap
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
    # Drops carry images; without Attach Files a drop with any fails at post
    # time, which is the worst moment to find out.
    by_id = {str(c.id): c for c in channels}
    me = guild.me
    for key, what in (('drop_channel_id', 'drop channel'),
                      ('team_channel_id', 'mission team channel')):
        channel = by_id.get(current[key] or '')
        if channel is not None and me is not None and not channel.permissions_for(me).attach_files:
            problems.append(f"I can't attach files in the {what} (#{channel.name}), so a drop "
                            'with images would fail there. Give me Attach Files in it.')
    if current['unit_role_id'] and current['unit_role_id'] not in role_ids:
        problems.append('The unit role no longer exists — pick it again.')
    return {
        'settings': current,
        'channels': channels,
        'roles': roles,
        'weekdays': list(enumerate(rules.WEEKDAYS)),
        'openers': [(key, rules.DROP[key].label) for key in rules.ORBAT_OPENERS],
        'default_survey_url': rules.DEFAULT_SURVEY_URL,
        'orbat_access': rules.ORBAT_ACCESS,
        'problems': problems,
    }


async def save_settings(guild, form) -> str:
    """Validate and store the settings form. Raises ValueError for a person."""
    clean = rules.clean_settings(
        form,
        role_ids=[r.id for r in mentionable_roles(guild)],
        channel_ids=[c.id for c in postable_channels(guild)],
        orbat_role_ids=form.getlist('orbat_role_ids') if hasattr(form, 'getlist') else [],
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
        str(guild.id), values, str(member.id), member.display_name,
        is_test=form.get('is_test') == '1')


def _deleted_note(name: str, outcome: dict, is_test: bool) -> str:
    """What a delete did, in one sentence — including the Kill Tracker side,
    because that is the part somebody would otherwise go and check."""
    head = f'Deleted {name}, its missions, maps and images'
    if 'kt_removed' in outcome:
        return f'{head}, and its empty Kill Tracker campaign.'
    if 'kt_kept' in outcome:
        return (f"{head}. Its Kill Tracker campaign ({outcome['kt_kept']}) was kept: it "
                'holds missions or was set up there by hand. An admin can remove it under '
                'Kill Tracker → Campaigns.')
    return f'{head}.' + (' Nothing of it is left.' if is_test else '')


async def _delete_created_orbats(orbat_ids: list) -> int:
    """Delete ORBATs the planner made for a test campaign or mission — never one
    another mission uses or an operation has run on (`cp_created_orbats()`)."""
    gone = 0
    for orbat_id in orbat_ids:
        if await database.delete_orbat(orbat_id):
            gone += 1
    return gone


async def delete_mission(bot, mission: dict, member=None) -> str:
    """Delete a mission. A test mission takes its posts and the ORBAT the
    planner made for it; a real one leaves both. Either way the AO maps lose
    its phases' empty layers."""
    campaign = await database.get_cp_campaign(mission['campaign_id'])
    is_test = bool(campaign and campaign['is_test'])
    orbats = []
    if is_test:
        await clear_test_posts(bot, mission_id=mission['id'])
        orbats = await database.cp_created_orbats(mission_id=mission['id'])
    # Take its recording back out of the Kill Tracker campaign, if the planner
    # put it there — the mission it was counted for no longer exists.
    await database.set_cp_mission_recording(mission['id'], None, manual=True)
    await database.delete_cp_mission(mission['id'], mission['campaign_id'])
    gone = await _delete_created_orbats(orbats)
    await refresh_ao_maps(mission['campaign_id'], member)
    return f"Deleted {mission['name']}" + (', with the ORBAT made for it.' if gone else '.')


async def delete_campaign(bot, campaign: dict) -> str:
    """Delete a campaign and say exactly what went and what stayed.

    Everything in it goes, with its maps. A test campaign also takes its
    posts and the ORBATs the planner made for its missions; an ORBAT that was
    only linked — somebody's roster — stays either way, and so do a real
    campaign's posted drops, which are the public record."""
    posted = bool(await database.cp_posted_messages(campaign_id=campaign['id']))
    linked = {m['orbat_id'] for m in await database.list_cp_missions(campaign['id'])
              if m['orbat_id']}
    orbats = []
    if campaign['is_test']:
        await clear_test_posts(bot, campaign_id=campaign['id'])
        orbats = await database.cp_created_orbats(campaign_id=campaign['id'])
    outcome = await database.delete_cp_campaign(campaign['id'])
    gone = await _delete_created_orbats(orbats)
    kept_orbats = len(linked - set(orbats))
    note = _deleted_note(campaign['name'], outcome,
                         campaign['is_test'] and not kept_orbats and 'kt_kept' not in outcome)
    if gone:
        note = note.replace(' Nothing of it is left.', '')
        note += (f" The ORBAT{'s' if gone > 1 else ''} made for its missions went too."
                 + (' Nothing of it is left.' if campaign['is_test'] and not kept_orbats
                    and 'kt_kept' not in outcome else ''))
    kept = []
    if kept_orbats:
        kept.append(f"the ORBAT{'s' if kept_orbats > 1 else ''} its missions used "
                    f"{'stay' if kept_orbats > 1 else 'stays'} under Operations → ORBATs")
    if posted and not campaign['is_test']:
        kept.append('the drops it posted stay in Discord')
    if kept:
        sentence = ' and '.join(kept)
        note += ' ' + sentence[0].upper() + sentence[1:] + '.'
    return note


async def delete_test_campaigns(bot, guild) -> str:
    ids = await database.test_cp_campaign_ids(str(guild.id))
    for campaign_id in ids:
        await clear_test_posts(bot, campaign_id=campaign_id)
        orbats = await database.cp_created_orbats(campaign_id=campaign_id)
        await database.delete_cp_campaign(campaign_id)
        await _delete_created_orbats(orbats)
    if not ids:
        return 'There were no test campaigns to delete.'
    return (f'Deleted {len(ids)} test campaign{"s" if len(ids) != 1 else ""} with '
            'everything in them. Nothing of them is left.')


async def make_real(bot, campaign: dict, member) -> str:
    # What it posted as a test goes; as a real campaign every drop starts again.
    await clear_test_posts(bot, campaign_id=campaign['id'])
    kt_name = await database.make_cp_campaign_real(campaign['id'], member.display_name)
    await database.link_cp_campaign_recordings(campaign['id'])
    return (f"{campaign['name']} is a real campaign now, linked to the Kill Tracker "
            f'campaign {kt_name}. Its drops need approving again, and now post publicly.')


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
        'campaign_map_svg': await campaign_map_svg(guild, campaign),
        'statuses': rules.CAMPAIGN_STATUSES,
        'auto_complete': plan['auto_complete'],
        'suggested_date': suggested.isoformat() if suggested else '',
        'carries_from': last['name'] if last else None,
    }


async def update_campaign(campaign: dict, form, member) -> None:
    await database.update_cp_campaign(campaign['id'], rules.clean_campaign(form),
                                      member.display_name)


async def add_ao(campaign: dict, form, member=None) -> str:
    name = str(form.get('name') or '').strip()[:80]
    if not name:
        raise ValueError('An AO needs a name.')
    await database.add_cp_ao(campaign['id'], name)
    notes = await sync_campaign_map(campaign, member)
    return ' '.join([f'Added the {name} AO.'] + notes)


async def rename_ao(campaign: dict, ao_id: int, form, member=None) -> str:
    name = str(form.get('name') or '').strip()[:80]
    if not name:
        raise ValueError('An AO needs a name.')
    await database.rename_cp_ao(ao_id, campaign['id'], name)
    await sync_campaign_map(campaign, member)
    await refresh_ao_maps(campaign['id'], member)
    return 'AO renamed.'


async def delete_ao(campaign: dict, ao_id: int, member=None) -> str:
    await database.delete_cp_ao(ao_id, campaign['id'])
    await sync_campaign_map(campaign, member)
    return 'AO deleted, with its map. Phases fought there keep their place without an AO.'


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


def _when(moment) -> dict | None:
    """A stored (naive UTC) or computed (aware) time, for the page and JSON."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        from datetime import timezone
        moment = moment.replace(tzinfo=timezone.utc)
    return {'iso': moment.isoformat(), 'ts': int(moment.timestamp())}


def _drop_json(drop: dict, tz: str) -> dict:
    """One drop as the page and the wizard see it: the text and its parts, and
    what the posting loop is going to do with it."""
    row = drop['row'] or {}
    return {
        'key': drop['key'], 'label': drop['label'], 'step': drop['step'],
        'text': drop['text'], 'chars': drop['chars'], 'parts': drop['parts'],
        'images': drop['images'], 'due': _when(drop['due']), 'hash': drop['hash'],
        'due_label': (rules.date_label(drop['due'].astimezone(rules.zone(tz)).date()) + ' '
                      + drop['due'].astimezone(rules.zone(tz)).strftime('%H:%M')
                      if drop['due'] else ''),
        'state': drop['state'],
        'approved_by': row.get('approved_by_name'), 'approved_at': _when(row.get('approved_at')),
        'posted_at': _when(row.get('posted_at')), 'skipped_by': row.get('skipped_by_name'),
        'pinged_at': _when(row.get('held_pinged_at')),
    }


async def mission_state(guild, mission: dict, tz: str) -> dict:
    """The mission and everything worked out from it: progress, the schedule,
    each drop's text as it would post, and the checks. One answer for both the
    wizard (as JSON) and the schedule page (as a template), so they cannot
    disagree."""
    plan = await settings(guild.id)
    campaign = await database.get_cp_campaign(mission['campaign_id'])
    is_test = bool(campaign and campaign['is_test'])
    worked = await mission_drops(str(guild.id), mission, is_test=is_test, plan=plan, tz=tz)
    data = worked['data']
    phases = data['phases']
    ctx = await post_context(str(guild.id), plan, tz)
    today = today_in(tz)
    drops = [_drop_json(d, tz) for d in worked['drops']]

    schedule = rules.schedule(data, plan, today, tz)
    # Layer each drop's state over the schedule, which only knows readiness.
    by_key = {d['key']: d for d in drops}
    for item in schedule:
        if item['kind'] == 'drop':
            st = by_key[item['key']]['state']
            if st['key'] not in ('unapproved', 'nodate'):
                item['status'] = (st['tone'], st['label'])
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
                     'orbat_opens': rules.DROP[plan['orbat_opens']].label,
                     'channel_set': bool(worked['channel'])},
        'is_test': is_test,
        'operation_id': mission.get('operation_id'),
        'orbat_id': mission.get('orbat_id'),
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

    if 'phases' in payload or 'name' in fields:
        # The AO maps carry a layer per phase, named after the mission.
        await refresh_ao_maps(mission['campaign_id'], member)

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
        'orbat': await orbat_panel(guild, mission),
        'maps': await maps_panel(guild, campaign, mission),
        'recording': await recording_panel(mission),
        'packs': rules.PACKS,
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
            'orbat': await orbat_panel(guild, mission), 'packs': rules.PACKS,
            'maps': await maps_panel(guild, campaign, mission),
            'recording': await recording_panel(mission),
            'steps': {s.key: s for s in rules.STEPS}}


# ---------------------------------------------------------------------------
# After action: the OCAP recording (milestone 6)
# ---------------------------------------------------------------------------

def _recording_label(rec: dict) -> str:
    return rec.get('name_override') or rec.get('label') or rec.get('mission') or f"#{rec['ocap_id']}"


async def recording_panel(mission: dict) -> dict:
    """The recording card: which OCAP recording this mission is, its headline
    numbers and the line they make for the AAR, and the other recordings it
    could be. Matching is by date, so a mission with no op date has none."""
    from cogs.campaigns import candidate_recordings
    from utils import killtracker
    fresh = await database.get_cp_mission(mission['id'])
    linked = None
    if fresh.get('ocap_id'):
        rec = await database.kt_recording(fresh['ocap_id'])
        if rec is not None:
            summary = rules.recording_summary(rec['rows'], killtracker.HEADER, rec['duration'])
            linked = {**{k: v for k, v in rec.items() if k != 'rows'},
                      'name': _recording_label(rec), 'counted': bool(rec['rows']),
                      'summary': summary, 'line': rules.stats_line(summary) if rec['rows'] else '',
                      'duration_label': rules.duration_label(rec['duration'])}
    candidates = [{**c, 'name': _recording_label(c),
                   'duration_label': rules.duration_label(c.get('duration'))}
                  for c in await candidate_recordings(fresh.get('op_date'))]
    campaign = await database.get_cp_campaign(mission['campaign_id'])
    return {'linked': linked, 'candidates': candidates, 'manual': fresh.get('ocap_manual'),
            'has_date': bool(fresh.get('op_date')), 'kt_added': fresh.get('ocap_kt_added'),
            'is_test': bool(campaign and campaign['is_test']),
            'stats_in_use': linked is not None and linked['line']
                            and rules.text(fresh['fields'], 'stats') == linked['line']}


async def set_recording(mission: dict, form, member) -> str:
    """The recording card's buttons: link one of the candidates, say none of
    them is it, hand the choice back to the bot, or put the numbers into the
    AAR's Stats field."""
    action = form.get('action')
    if action == 'none':
        await database.set_cp_mission_recording(mission['id'], None, manual=True)
        return 'No recording for this mission. The bot will not link one by itself.'
    if action == 'auto':
        await database.set_cp_mission_recording(mission['id'], None, manual=False)
        return ('The bot will link the recording itself when there is exactly one long '
                'enough on the op date.')
    if action == 'stats':
        panel = await recording_panel(mission)
        if not panel['linked'] or not panel['linked']['line']:
            raise ValueError('There are no counted numbers to use yet.')
        await database.patch_cp_mission(mission['id'], mission['campaign_id'],
                                        {'stats': panel['linked']['line']}, None, None,
                                        member.display_name)
        return 'Put the recording\'s numbers in the AAR\'s Stats field.'
    if action == 'link':
        try:
            chosen = int(form.get('ocap_id') or 0)
        except ValueError:
            chosen = 0
        panel = await recording_panel(mission)
        allowed = {c['ocap_id']: c for c in panel['candidates']}
        if chosen not in allowed:
            raise ValueError('Pick one of the recordings from around the op date.')
        await database.set_cp_mission_recording(mission['id'], chosen, manual=True)
        fresh = await database.get_cp_mission(mission['id'])
        note = f"Linked the recording {allowed[chosen]['name']}."
        if fresh.get('ocap_kt_added'):
            note += " It is in the campaign's Kill Tracker campaign now."
        return note
    raise ValueError('That is not something the recording card does.')


# ---------------------------------------------------------------------------
# Drops: approve, unapprove, skip — and sign-ups (milestone 4)
# ---------------------------------------------------------------------------

DROP_ACTIONS = ('approve', 'unapprove', 'skip', 'unskip')


async def drop_action(bot, guild, mission: dict, kind: str, action: str, member,
                      seen_hash: str | None = None) -> str:
    """Approve, unapprove, skip or un-skip one drop, then let the posting loop
    act at once — so a held drop goes out the moment it is approved instead of
    at the next minute.

    `seen_hash` is the version the person was looking at. Approving is
    refused when the drop has changed since (somebody else saved meanwhile),
    because an approval is a statement about the words somebody read.
    """
    if kind not in rules.DROP or action not in DROP_ACTIONS:
        raise NotFound('No such drop.')
    campaign = await database.get_cp_campaign(mission['campaign_id'])
    if campaign and campaign['status'] == 'archived' and action in ('approve', 'unskip'):
        raise ValueError('This campaign is archived, so nothing in it posts. Set it back '
                         'to active on the campaign page first.')
    worked = await mission_drops(str(guild.id), mission,
                                 is_test=bool(campaign and campaign['is_test']))
    drop = next(d for d in worked['drops'] if d['key'] == kind)
    state = drop['state']
    label = drop['label']

    if action == 'approve':
        if not state['can_approve']:
            raise ValueError(f"{label} has nothing to approve ({state['label'].lower()}).")
        if seen_hash and seen_hash != drop['hash']:
            raise ValueError(f'{label} changed while you were reading it. Read it again, '
                             'then approve.')
        if drop['due'] is None:
            raise ValueError('Set the op date first: an approval is for a time as well as words.')
        await database.approve_cp_drop(mission['id'], kind, drop['hash'],
                                       drop['due'].replace(tzinfo=None),
                                       str(member.id), member.display_name)
        note = f'Approved {label}.'
    elif action == 'unapprove':
        if not state['can_unapprove']:
            raise ValueError(f'{label} is not waiting to post, so there is nothing to withdraw.')
        await database.unapprove_cp_drop(mission['id'], kind)
        return f'Withdrew the approval of {label}. It will not post until approved again.'
    elif action == 'skip':
        if not state['can_skip']:
            raise ValueError(f"{label} can't be skipped now ({state['label'].lower()}).")
        await database.skip_cp_drop(mission['id'], kind, member.display_name, True)
        return f'{label} will not be posted by the bot.'
    else:
        await database.skip_cp_drop(mission['id'], kind, member.display_name, False)
        return f'{label} is back in the schedule. It still needs approving.'

    cog = bot.get_cog('CampaignsCog') if bot else None
    done = []
    if cog is not None:
        try:
            done = await cog.process_mission_id(mission['id'])
        except Exception as e:      # the loop retries; the approval stands
            print(f"⚠️ campaigns: processing after approval failed: {e!r}")
    if any(line.startswith(('posted', 'updated')) for line in done):
        note += ' ' + ' '.join(line[0].upper() + line[1:] + ('' if line.endswith('.') else '.')
                               for line in done)
    elif cog is not None and (mission['id'], kind) in getattr(cog, '_failed', {}):
        note += (' It is due, but posting failed — the mission team channel says why. '
                 'I will try again in half an hour.')
    elif not worked['channel']:
        note += (' There is no channel to post it in yet — set one in Campaigns → Settings.')
    elif drop['due'] > datetime.now(drop['due'].tzinfo):
        local = drop['due'].astimezone(rules.zone(worked['tz']))
        note += f" It posts {rules.date_label(local.date())} at {local:%H:%M}."
    return note


async def start_signups_now(bot, guild, mission: dict) -> str:
    """The planner's "Start sign-ups now" button — the same rules as the
    automatic start at the opening drop."""
    from cogs.campaigns import start_signups
    campaign = await database.get_cp_campaign(mission['campaign_id'])
    plan = await settings(guild.id)
    tz = await database.get_guild_timezone(str(guild.id))
    started, note = await start_signups(bot, mission, plan, tz,
                                        is_test=bool(campaign and campaign['is_test']))
    if not started:
        raise ValueError(note)
    return note


async def clear_test_posts(bot, *, campaign_id=None, mission_id=None) -> int:
    """Delete the drop messages a test campaign posted in the team channel —
    part of leaving no trace. Real campaigns' posts stay: they are the public
    record, and partner servers may have followed them."""
    from cogs.campaigns import delete_messages
    pairs = await database.cp_posted_messages(campaign_id=campaign_id, mission_id=mission_id)
    if not pairs or bot is None:
        return 0
    return await delete_messages(bot, pairs)


# ---------------------------------------------------------------------------
# The mission's ORBAT
# ---------------------------------------------------------------------------

async def orbat_panel(guild, mission: dict) -> dict:
    """The ORBAT card: what is linked, what could be, and what the previous
    mission used — "copy last week's" is the usual way a new one starts."""
    orbats = [dict(o) for o in await database.get_guild_orbats(str(guild.id))]
    by_id = {o['id']: o for o in orbats}
    missions = await database.list_cp_missions(mission['campaign_id'])
    earlier = [m for m in missions if m['id'] != mission['id']
               and m['position'] <= mission['position'] and m['orbat_id']]
    previous = by_id.get(earlier[-1]['orbat_id']) if earlier else None
    return {
        'linked': by_id.get(mission['orbat_id']),
        'orbats': orbats,
        'previous': previous,
        'default_name': f"{mission['name']} ORBAT"[:120],
    }


async def set_orbat(guild, mission: dict, form, member) -> str:
    """Link, copy, create or unlink — the four buttons on the ORBAT card."""
    from web import orbat as orbat_service
    action = form.get('action')
    orbats = {o['id']: o for o in await database.get_guild_orbats(str(guild.id))}
    try:
        chosen = int(form.get('orbat_id') or 0)
    except ValueError:
        chosen = 0
    name = str(form.get('name') or '').strip() or f"{mission['name']} ORBAT"

    if action == 'unlink':
        await database.set_cp_mission_orbat(mission['id'], None, member.display_name)
        return 'ORBAT unlinked. The ORBAT itself is still there.'
    if action == 'new':
        new_id = await orbat_service.create(guild, member, name,
                                            f"For {mission['name']}")
        await database.set_cp_mission_orbat(mission['id'], new_id, member.display_name,
                                            created=True)
        return f'Created {name} and linked it. Open it to write the roster.'
    if chosen not in orbats:
        raise ValueError('Pick an ORBAT from the list.')
    if action == 'link':
        await database.set_cp_mission_orbat(mission['id'], chosen, member.display_name)
        return f"Linked {orbats[chosen]['name']}."
    if action == 'copy':
        new_id = await orbat_service.duplicate(chosen, member, name)
        await database.set_cp_mission_orbat(mission['id'], new_id, member.display_name,
                                            created=True)
        return f"Copied {orbats[chosen]['name']} as {name} — the structure, not the bookings."
    raise ValueError('That is not something the ORBAT card does.')


# ---------------------------------------------------------------------------
# Maps (milestone 5)
#
# The campaign map and the AO maps are ordinary tactical maps; the planner
# makes them, and keeps their layers in step with its AOs and phases through
# `rules.sync_campaign_map()` and `rules.sync_phase_layers()`. A sync rewrites
# the stored document, so somebody with that map open in the editor saves over
# it — the next sync (opening the map from the planner, saving phases) puts
# the layers back.
# ---------------------------------------------------------------------------

def _map_user(member) -> str:
    return member.display_name if member is not None else 'Campaign planner'


async def sync_campaign_map(campaign: dict, member=None) -> list:
    """Bring the campaign map's AO layers up to date, if it has a map."""
    campaign = await database.get_cp_campaign(campaign['id'])
    if not campaign or not campaign.get('map_id'):
        return []
    record = await database.get_tac_map(campaign['map_id'])
    if record is None:
        await database.set_cp_campaign_map(campaign['id'], None)
        return []
    aos = await database.list_cp_aos(campaign['id'])
    doc = tacmap.parse(record['doc']).doc
    synced, notes = rules.sync_campaign_map(doc, [{'id': a['id'], 'name': a['name']} for a in aos])
    if synced != doc:
        await database.save_tac_map_doc(record['id'], tacmap.dumps(synced), _map_user(member))
    await database.set_cp_ao_areas({a['id']: rules.ao_layer_id(a['id']) for a in aos})
    return notes


async def open_campaign_map(guild, campaign: dict, member) -> tuple[int, list]:
    """The campaign map's id, making it first if there is none."""
    fresh = await database.get_cp_campaign(campaign['id'])
    if not fresh.get('map_id') or await database.get_tac_map(fresh['map_id']) is None:
        doc = tacmap.blank_doc()
        map_id = await database.create_tac_map(
            str(guild.id), f"{campaign['name']} — campaign map"[:120],
            f"The campaign map for {campaign['name']}: one outline per AO.",
            tacmap.dumps(doc), str(member.id), member.display_name)
        await database.set_cp_campaign_map(campaign['id'], map_id)
    notes = await sync_campaign_map(campaign, member)
    return (await database.get_cp_campaign(campaign['id']))['map_id'], notes


async def _phase_list(campaign_id: int, ao_id: int) -> list:
    return [{'id': p['id'], 'name': rules.phase_layer_name(p['mission_name'], p['number'],
                                                           p['title'])}
            for p in await database.cp_ao_phases(campaign_id, ao_id)]


async def sync_ao_map(campaign_id: int, ao: dict, member=None) -> list:
    """Bring one AO map's phase layers up to date."""
    record = await database.get_tac_map(ao['map_id']) if ao.get('map_id') else None
    if record is None:
        return []
    phases = await _phase_list(campaign_id, ao['id'])
    doc = tacmap.parse(record['doc']).doc
    synced, notes = rules.sync_phase_layers(doc, phases)
    if synced != doc:
        await database.save_tac_map_doc(record['id'], tacmap.dumps(synced), _map_user(member))
    await database.set_cp_phase_layers({p['id']: rules.phase_layer_id(p['id']) for p in phases})
    return notes


async def refresh_ao_maps(campaign_id: int, member=None) -> list:
    """Every AO map of a campaign, after phases, missions or AOs changed."""
    notes = []
    for ao in await database.list_cp_aos(campaign_id):
        if ao.get('map_id'):
            notes += await sync_ao_map(campaign_id, ao, member)
    return notes


async def open_ao_map(guild, campaign: dict, ao_id: int, member) -> tuple[int, list]:
    """An AO's map id, making it first from the campaign map's terrain."""
    aos = {a['id']: a for a in await database.list_cp_aos(campaign['id'])}
    ao = aos.get(ao_id)
    if ao is None:
        raise NotFound('No such AO in this campaign.')
    if not ao.get('map_id') or await database.get_tac_map(ao['map_id']) is None:
        fresh = await database.get_cp_campaign(campaign['id'])
        base = None
        if fresh.get('map_id'):
            record = await database.get_tac_map(fresh['map_id'])
            base = tacmap.parse(record['doc']).doc if record else None
        doc = rules.ao_map_doc(base, ao_id, ao['name'])
        map_id = await database.create_tac_map(
            str(guild.id), f"{campaign['name']} — {ao['name']}"[:120],
            f"The {ao['name']} AO of {campaign['name']}: one layer per phase fought there.",
            tacmap.dumps(doc), str(member.id), member.display_name)
        await database.set_cp_ao_map(ao_id, map_id)
        ao['map_id'] = map_id
    notes = await sync_ao_map(campaign['id'], ao, member)
    return ao['map_id'], notes


async def maps_panel(guild, campaign: dict, mission: dict | None = None) -> dict:
    """The maps card: the campaign map, and the AOs — for a mission, the AOs
    its phases are fought in first."""
    fresh = await database.get_cp_campaign(campaign['id'])
    aos = await database.list_cp_aos(campaign['id'])
    used = []
    if mission is not None:
        phases = await database.get_cp_phases(mission['id'])
        used = list(dict.fromkeys(int(p['ao_id']) for p in phases if p.get('ao_id')))
    order = {ao_id: i for i, ao_id in enumerate(used)}
    aos.sort(key=lambda a: (a['id'] not in order, order.get(a['id'], 0), a['position']))
    return {'campaign_map': fresh.get('map_id'),
            'aos': [{**a, 'used': a['id'] in order} for a in aos]}


async def campaign_map_svg(guild, campaign: dict) -> str | None:
    """The campaign map, drawn for the campaign page — read only."""
    fresh = await database.get_cp_campaign(campaign['id'])
    record = await database.get_tac_map(fresh['map_id']) if fresh.get('map_id') else None
    if record is None:
        return None
    from web.tacmap import places_for
    doc = tacmap.parse(record['doc']).doc
    return tacmap.render(doc, places=await places_for(str(guild.id), doc),
                         extra_class='cpmap')


# ---------------------------------------------------------------------------
# Briefing packs
# ---------------------------------------------------------------------------

async def build_export(guild, mission: dict, pack_key: str, fmt: str, tz: str,
                       brand: str) -> tuple[bytes, str, str]:
    """(file bytes, filename, media type) for one pack in one format. What goes
    in is `rules.document_blocks()`; this gathers its inputs and hands the
    blocks to a renderer in a thread, since laying out a PDF is CPU work and
    the web server shares its event loop with the bot."""
    import asyncio
    from web import exports
    if pack_key not in rules.PACK or fmt not in rules.EXPORT_FORMATS:
        raise NotFound('No such pack.')
    plan = await settings(guild.id)
    ctx = await post_context(str(guild.id), plan, tz)
    data = {**mission['fields'], 'phases': await database.get_cp_phases(mission['id'])}
    aos = {a['id']: a['name'] for a in await database.list_cp_aos(mission['campaign_id'])}
    orbat = None
    if mission.get('orbat_id'):
        record = await database.get_orbat(mission['orbat_id'])
        if record is not None and record['guild_id'] == str(guild.id):
            orbat = {'name': record['name'],
                     'squads': await database.get_orbat_structure(record['id']),
                     'nets': [dict(n) for n in await database.get_orbat_nets(record['id'])]}
    traces = []
    if pack_key != 'player':
        from web import maptrace
        traces = await maptrace.phase_traces(str(guild.id), mission['campaign_id'],
                                             data['phases'])
    blocks = rules.document_blocks(data, pack_key, ctx, orbat=orbat, aos=aos,
                                   today=today_in(tz), phase_traces=traces)
    uploaded = [i for i in rules.block_images(blocks) if not str(i).startswith('trace:')]
    images = await database.cp_image_data(mission['campaign_id'], uploaded)
    images.update({t['key']: t['png'] for t in traces})
    campaign = await database.get_cp_campaign(mission['campaign_id'])
    meta = {'brand': brand, 'campaign': campaign['name'] if campaign else '',
            'name': (rules.text(data, 'name') or mission['name']).upper(),
            'pack': rules.PACK[pack_key].label, 'ref': rules.text(data, 'op_ref'),
            'op_date': exports.op_date_label(data.get('op_date'))}
    render = exports.render_pdf if fmt == 'pdf' else exports.render_docx
    body = await asyncio.to_thread(render, blocks, meta, images)
    return body, rules.export_filename(data, pack_key, fmt), exports.MEDIA[fmt]


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
