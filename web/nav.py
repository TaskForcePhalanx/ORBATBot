"""The tab bar, built once here instead of three times in Jinja.

The site grew past the point where one flat row of tabs reads as anything: by
the eighth tab nobody can see that Operation, Slot Approvals and ORBATs are
three views of the same thing. So the tabs are a two-level structure, and it is
built in Python because the shape of it — which groups exist, what each one
lands on, who may see it — is the kind of thing that goes wrong when it is
spread across template conditionals.

The row is one line, and that is a constraint rather than a preference: the
column is 900px whatever the window is, so nine top-level tabs wrapped onto a
second line at **every** width. Anything added to the top row has to earn its
place there or go inside a group — which is where Embeds, the member log and
the Reddit watches went, as **Server**.

The groups are arranged by who uses them. **Missions** is what a mission maker
does from one evening to the next — events, modlists, ORBATs, the slot queue,
the operation — so it is one group rather than three places to look. Community
is the standing housekeeping, Stats is the Kill Tracker, and Campaigns and Maps
are in a trial period (`beta`): they sit last and say so.

`build()` returns everything `_nav.html` needs:

    groups    the top row, in order
    items     group key → the second row for that group
    group_of  page key → the group it belongs to, so a page only has to name
              itself and the nav works out where that sits
    pages     every page this member may open, flat, for `search()`
"""


def _item(key: str, label: str, href: str, words: str = '') -> dict:
    """One page. `words` are the other things somebody might type to find it."""
    return {'key': key, 'label': label, 'href': href, 'words': words}


def build(guild_id, *, is_admin: bool, may_action_slots: bool,
          may_plan: bool = False, may_edit_orbats: bool = False) -> dict:
    """The tab structure for one member of one guild.

    Permissions decide what is *in* the structure rather than what the template
    hides, so a group whose every page is out of reach is not rendered at all —
    a Unit Leader who is not an admin sees Missions with two pages under it.
    """
    base = f"/g/{guild_id}"

    # What a mission maker does: the first page is Events for everyone, so the
    # group's own link is the events list whoever is looking.
    missions = [_item('events', 'Events', base,
                      'rsvp sign-up training schedule calendar create')]
    if is_admin:
        missions.append(_item('modlists', 'Modlists', f"{base}/modlists",
                              'mods preset launcher upload selector'))
    # Admins see every ORBAT; somebody the planner's settings let edit mission
    # ORBATs sees the ones campaign missions use (`orbat_list_page`).
    if is_admin or may_edit_orbats:
        missions.append(_item('orbats', 'ORBATs', f"{base}/orbats",
                              'roster squads slots editor'))
    if may_action_slots:
        missions.append(_item('slots', 'Slot Approvals', f"{base}/slots",
                              'requests approve deny queue assign'))
    if is_admin:
        missions.append(_item('operation', 'Operation', f"{base}/operation",
                              'start event time post orbat channel reminder'))

    # The standing housekeeping. Roles and Voice are open to every member, the
    # rest is admin-only — so the group exists for everybody and is longer for
    # an admin.
    community = [
        _item('roles', 'Game roles', f"{base}/roles", 'tags minecraft dcs panel'),
        _item('voice', 'Voice time', f"{base}/voice", 'leaderboard channels hours'),
    ]
    if is_admin:
        community += [
            _item('logs', 'Member log', f"{base}/logs", 'joins leaves kicks bans welcome invites'),
            _item('embeds', 'Embeds', f"{base}/embeds", 'messages builder post'),
            _item('reddit', 'Reddit', f"{base}/reddit", 'feed subreddit posts announce'),
            _item('promotion', 'Promotion', f"{base}/promotion", 'cadet member missions'),
            _item('opsettings', 'Operation settings',
                  f"{base}/operation/settings", 'channels timezone approvals archive'),
        ]

    # The Kill Tracker's own pages: the reports first, then what keeps the
    # names and groupings behind them straight. Server admins only.
    killtracker = []
    if is_admin:
        killtracker = [
            _item('ktmissions', 'Missions', f"{base}/killtracker", 'recordings ocap'),
            _item('ktcampaigns', 'Campaigns', f"{base}/killtracker/campaigns", 'stats groups'),
            _item('ktplayers', 'Players', f"{base}/killtracker/players", 'stats kills deaths'),
            _item('ktkills', 'Longest kills', f"{base}/killtracker/kills", 'distance record'),
            _item('ktmulti', 'Multikills', f"{base}/killtracker/multikills", 'streak'),
            _item('ktnames', 'Link names', f"{base}/killtracker/names", 'alias discord mapping'),
            _item('ktunits', 'Units', f"{base}/killtracker/units", 'override tag'),
            _item('ktimport', 'Import', f"{base}/killtracker/import", 'upload recording json'),
        ]

    # The campaign planner. `may_plan` is admins plus the unit's own Unit
    # Leaders (web.guilds.can_plan); the settings page is admins only.
    campaigns = []
    if may_plan or is_admin:
        campaigns.append(_item('campaigns', 'Campaigns', f"{base}/campaigns",
                               'planner drops phases schedule'))
        campaigns.append(_item('cpobjectives', 'Objectives', f"{base}/campaigns/objectives",
                               'library'))
    if is_admin:
        campaigns.append(_item('cpsettings', 'Planner settings', f"{base}/campaigns/settings"))

    # The row is one line, and that is a budget: see the module docstring.
    # Both groups still in their trial period go last and carry `beta`.
    groups = [{'key': 'missions', 'label': '🎖️ Missions', 'href': missions[0]['href']},
              {'key': 'community', 'label': '👥 Community', 'href': community[0]['href']}]
    if killtracker:
        groups.append({'key': 'kt', 'label': '📊 Stats', 'href': killtracker[0]['href']})
    if campaigns:
        groups.append({'key': 'cp', 'label': '🗂️ Campaigns', 'href': campaigns[0]['href'],
                       'beta': True})
    maps = _item('maps', 'Maps', f"{base}/maps", 'tactical terrain draw plan tacmap')
    groups.append({'key': 'maps', 'label': '🗺️ Maps', 'href': maps['href'], 'beta': True})

    items = {'missions': missions, 'community': community, 'kt': killtracker,
             'cp': campaigns}
    group_of = {
        item['key']: group_key
        for group_key, group_items in items.items()
        for item in group_items
    }

    labels = {g['key']: g['label'].split(' ', 1)[1] for g in groups}
    pages = [dict(item, group=labels[group_key])
             for group_key, group_items in items.items() for item in group_items]
    pages.append(dict(maps, group=labels['maps']))
    return {'groups': groups, 'items': items, 'group_of': group_of, 'pages': pages}


def search(built: dict, query: str) -> list[dict]:
    """The pages whose name, group or keywords match every word typed.

    Searches `built['pages']`, which holds only what this member may open, so a
    Unit Leader cannot find an admin page and nothing here checks a permission.
    A page whose name starts with a typed word ranks above one that merely
    mentions it.
    """
    terms = query.lower().split()
    if not terms:
        return []
    scored = []
    for page in built['pages']:
        label = page['label'].lower()
        haystack = f"{label} {page['group'].lower()} {page['words']}"
        if all(term in haystack for term in terms):
            rank = 0 if all(term in label for term in terms) else 1
            scored.append((rank, page))
    scored.sort(key=lambda pair: pair[0])
    return [page for _, page in scored]
