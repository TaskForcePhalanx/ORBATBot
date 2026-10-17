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

`build()` returns everything `_nav.html` needs:

    groups    the top row, in order
    items     group key → the second row for that group
    group_of  page key → the group it belongs to, so a page only has to name
              itself and the nav works out where that sits
"""


def _item(key: str, label: str, href: str) -> dict:
    return {'key': key, 'label': label, 'href': href}


def build(guild_id, *, is_admin: bool, may_action_slots: bool,
          may_plan: bool = False, may_edit_orbats: bool = False) -> dict:
    """The tab structure for one member of one guild.

    Permissions decide what is *in* the structure rather than what the template
    hides, so a group whose every page is out of reach is not rendered at all —
    a Unit Leader who is not an admin sees Operations with one page under it.
    """
    base = f"/g/{guild_id}"

    # The slot system: one operation, its queue, and the rosters it runs on.
    # Three pages that are always about the same evening.
    operations = []
    if is_admin:
        operations.append(_item('operation', 'Operation', f"{base}/operation"))
    if may_action_slots:
        operations.append(_item('slots', 'Slot Approvals', f"{base}/slots"))
    # Admins see every ORBAT; somebody the planner's settings let edit mission
    # ORBATs sees the ones campaign missions use (`orbat_list_page`).
    if is_admin or may_edit_orbats:
        operations.append(_item('orbats', 'ORBATs', f"{base}/orbats"))
    # The plan is for everyone who has to fight it, so the map is the one page
    # in this group with no permission on it — drawing on one still needs the
    # rights the page itself checks.
    operations.append(_item('maps', 'Maps', f"{base}/maps"))
    if is_admin:
        operations.append(_item('opsettings', 'Settings',
                                f"{base}/operation/settings"))

    # What the bot does in the server outside an operation. All three are
    # admin-only, and as three top-level tabs they were what pushed the row
    # onto a second line — nine tabs wrapped at every window width, because
    # the column is 900px whatever the window is.
    server = []
    if is_admin:
        server = [
            _item('embeds', 'Embeds', f"{base}/embeds"),
            _item('logs', 'Member log', f"{base}/logs"),
            _item('reddit', 'Reddit', f"{base}/reddit"),
            _item('modlists', 'Modlists', f"{base}/modlists"),
        ]

    groups = [_item('events', '📅 Events', base)]
    if operations:
        # The group's own link is its first page, which is the one the viewer
        # is allowed to open — an admin lands on Operation, a Unit Leader on
        # the queue.
        groups.append({'key': 'ops', 'label': '🎖️ Operations',
                       'href': operations[0]['href']})
    # Short labels: the row has to fit 860px, and "Roles" and "Voice" lose
    # nothing beside an icon. The pages still call themselves Game roles and
    # Voice time.
    groups.append(_item('roles', '🎮 Roles', f"{base}/roles"))
    groups.append(_item('voice', '🔊 Voice', f"{base}/voice"))

    # The Kill Tracker's own pages: the names and groupings its public numbers
    # are shown under. Server admins only, for the whole group.
    killtracker = []
    if is_admin:
        killtracker = [
            _item('ktmissions', 'Missions', f"{base}/killtracker"),
            _item('ktcampaigns', 'Campaigns', f"{base}/killtracker/campaigns"),
            _item('ktplayers', 'Players', f"{base}/killtracker/players"),
            _item('ktnames', 'Link names', f"{base}/killtracker/names"),
            _item('ktpromotion', 'Promotion', f"{base}/killtracker/promotion"),
            _item('ktunits', 'Units', f"{base}/killtracker/units"),
            _item('ktkills', 'Longest kills', f"{base}/killtracker/kills"),
            _item('ktimport', 'Import', f"{base}/killtracker/import"),
        ]
        groups.append({'key': 'kt', 'label': '📊 Kill Tracker',
                       'href': killtracker[0]['href']})

    # Last, because it is the one group nobody goes to during an operation.
    if server:
        groups.append({'key': 'server', 'label': '🛠️ Server',
                       'href': server[0]['href']})

    # The campaign planner. `may_plan` is admins plus the unit's own Unit
    # Leaders (web.guilds.can_plan); the settings page is admins only.
    campaigns = []
    if may_plan or is_admin:
        campaigns.append(_item('campaigns', 'Campaigns', f"{base}/campaigns"))
        campaigns.append(_item('cpobjectives', 'Objectives', f"{base}/campaigns/objectives"))
    if is_admin:
        campaigns.append(_item('cpsettings', 'Settings', f"{base}/campaigns/settings"))
    if campaigns:
        groups.insert(2 if operations else 1,
                      {'key': 'cp', 'label': '🗂️ Campaigns', 'href': campaigns[0]['href']})

    items = {'ops': operations, 'cp': campaigns, 'kt': killtracker,
             'server': server}
    group_of = {
        item['key']: group_key
        for group_key, group_items in items.items()
        for item in group_items
    }
    return {'groups': groups, 'items': items, 'group_of': group_of}
