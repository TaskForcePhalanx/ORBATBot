"""Every feature, as a how-to — the one place they are written down.

The site renders these as a small wiki under `/help`, and `scripts/gen_help.py`
writes the same text into the README between its `help:start` / `help:end`
markers. That is the whole reason this is data rather than prose in two files:
a feature that changes is changed here, and both surfaces follow. It is the
same bargain the rest of the bot makes — `approve_slot_request()` is one
function behind a button and a web page — applied to the documentation.

**This module imports nothing but the standard library**, like `utils/orbat.py`,
`utils/tacmap.py` and `utils/reddit.py`, which is what makes it testable at all.
`tests/test_help.py` does something the other suites cannot: it reads the
slash-command names straight out of `cogs/` and fails when one of them is not
written up here. So "is every feature documented?" stops being a question
somebody has to re-ask every few months.

A topic is deliberately small. `summary` is the one sentence somebody reads to
know they are in the right place, `steps` is what they do, and `notes` is what
they would otherwise find out the hard way. Anything longer than that belongs
in the README's own prose sections, which this does not replace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# The audience a topic is written for. It is a label rather than a permission
# check: the help is not filtered by who is reading it, because a wiki that
# hides half of itself is how people conclude a feature does not exist. Saying
# who may do a thing is more useful than pretending it isn't there.
EVERYONE = 'Everyone'
UNIT_LEADER = 'Unit Leader or Admin'
ORGANISER = 'Organiser or Admin'
ADMIN = 'Admin'
MANAGE_MESSAGES = 'Manage Messages'


@dataclass(frozen=True)
class Group:
    """One shelf of the wiki."""

    key: str
    title: str
    blurb: str


@dataclass(frozen=True)
class Topic:
    """One how-to.

    `page` is the web page this is the help for, named by the key `web/nav.py`
    gives it, so a page can link at its own topic with a `?` and the link
    cannot drift from the tab it sits on. `commands` is the slash commands the
    topic covers, which is what the coverage test reads.
    """

    key: str
    title: str
    group: str
    audience: str
    summary: str
    steps: tuple[str, ...]
    notes: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    page: str = ''
    related: tuple[str, ...] = ()
    where: str = ''


GROUPS: tuple[Group, ...] = (
    Group('slots', 'Slots and operations',
          'Asking for a slot, deciding who gets it, and running the evening.'),
    Group('orbats', 'ORBATs and rosters',
          'Where the slots themselves come from — built here, or in a sheet.'),
    Group('events', 'Events',
          'Standalone sign-ups: trainings, movie nights, campaign sessions.'),
    Group('roles', 'Game roles',
          'Tag roles members give themselves, so you can ping everyone who '
          'plays a game.'),
    Group('maps', 'Tactical maps',
          'Drawing the plan on the terrain, sharing it, and getting it into '
          'the mission.'),
    Group('campaigns', 'Campaign planner',
          'Planning a three-to-five mission campaign and its weekly drops.'),
    Group('reddit', 'Reddit announcements',
          'Watching a user or a subreddit and announcing new posts.'),
    Group('killtracker', 'Kill Tracker',
          'The names and groupings the public statistics are shown under.'),
    Group('server', 'Server and members',
          'The log, the welcome, voice time, embeds and the housekeeping.'),
)


_SLOTS: tuple[Topic, ...] = (
    Topic(
        key='request-a-slot',
        title='Request a slot',
        group='slots',
        audience=EVERYONE,
        where='Discord — the ORBAT board, or `/request-slot`',
        summary='Ask for one of the slots on tonight’s board and wait for a '
                'Unit Leader to decide.',
        steps=(
            'Go to the **#orbat** channel and press **📋 Request a Slot** on the '
            'board, or run `/request-slot` anywhere.',
            'Pick the squad from the first menu, then the slot from the second.',
            'That is it — the request goes to the approvers, and you get a DM '
            'confirming it.',
            'Watch the board: your slot turns 🟡 while you wait, and 🔴 once you '
            'have it.',
        ),
        notes=(
            'Somebody else may ask for the same slot. Both requests stand, the '
            'approver picks one, and the other person is told — so a 🟡 slot is '
            'worth asking for rather than avoiding.',
            'You can only hold one request at a time. Use `/change-slot` to '
            'move rather than requesting twice.',
        ),
        commands=('/request-slot',),
        related=('change-or-cancel', 'approve-a-request'),
    ),
    Topic(
        key='change-or-cancel',
        title='Change, cancel or leave',
        group='slots',
        audience=EVERYONE,
        where='Discord — `/change-slot`, `/cancel-request`, `/leave-operation`',
        summary='Move to a different slot, withdraw a request you have not had '
                'answered yet, or drop out of the operation.',
        steps=(
            'To swap: `/change-slot`. It gives up what you hold and opens the '
            'picker again.',
            'To withdraw a request nobody has answered: `/cancel-request`.',
            'To drop out entirely, approved or not: `/leave-operation`, then '
            'confirm.',
        ),
        notes=(
            '`/change-slot` frees your old slot the moment you run it, so '
            'somebody else can take it while you are choosing. If you only '
            'want a different squad, that is the trade.',
            'All three release the slot on the board straight away, so nobody '
            'is planning around a seat you have left.',
        ),
        commands=('/change-slot', '/cancel-request', '/leave-operation'),
        related=('request-a-slot',),
    ),
    Topic(
        key='approve-a-request',
        title='Approve or deny a request',
        group='slots',
        audience=UNIT_LEADER,
        where='Discord — **#slot-approvals**, or the **Slot Approvals** tab',
        summary='Decide who gets the slot, in the channel or in the browser — '
                'both do exactly the same thing.',
        steps=(
            'In Discord: open **#slot-approvals** and press **✅ Approve** or '
            '**❌ Deny** on the request. Denying opens a box for an optional '
            'reason.',
            'In the browser: **🎖️ Operations → Slot Approvals**, where the same '
            'two buttons sit next to each request with a reason field.',
            'The member is DMed either way, the message leaves the queue, and a '
            'record goes to **#approval-archive**.',
        ),
        notes=(
            'Approving a contested slot denies the other requests for it '
            'automatically and tells those members why.',
            'A Unit Leader may only decide requests from their own unit. The '
            'web page still lists the rest — it says *CNTO only* instead of the '
            'buttons, so the queue never lies about how many people are '
            'waiting.',
            'Old requests stay clickable after a new operation starts, and are '
            'always decided against the operation they were made for.',
        ),
        page='slots',
        related=('clear-a-slot', 'assign-a-slot'),
    ),
    Topic(
        key='assign-a-slot',
        title='Put somebody on a slot directly',
        group='slots',
        audience=UNIT_LEADER,
        where='Discord — `/assign-slot`, or the **Slot Approvals** tab',
        summary='Book a member onto a slot without them asking and without an '
                'approval step.',
        steps=(
            'In Discord: `/assign-slot @member`, then pick the squad and the '
            'slot.',
            'In the browser: the **Assign somebody** panel at the bottom of '
            '**Slot Approvals**. Type a Discord ID, a mention or a name.',
            'The member is DMed that they now hold it.',
        ),
        notes=(
            'This is stricter than approving: you need a unit of your own and '
            'the member must share it. Choosing who goes on the roster is not '
            'the same act as answering somebody who asked.',
            'Members are searched rather than listed, because the bot does not '
            'read your member list. More than one match is reported with IDs '
            'rather than guessed at.',
            'It is refused if the member already holds a slot — clear the old '
            'one first.',
        ),
        commands=('/assign-slot',),
        page='slots',
        related=('clear-a-slot',),
    ),
    Topic(
        key='clear-a-slot',
        title='Take somebody off a slot',
        group='slots',
        audience=UNIT_LEADER,
        where='Discord — `/clear-slot`, or **Release** / **Withdraw** on the web',
        summary='Give a booked slot back, or take an undecided request out of '
                'the queue.',
        steps=(
            'In Discord: `/clear-slot` and pick from the dropdown of who is '
            'currently on the operation.',
            'In the browser: **Release** on a booked row, **Withdraw** on a '
            'pending one — the same action under two names.',
            'Confirm. The member is DMed and told who removed them.',
        ),
        notes=(
            'Neither can be undone, which is why both ask first.',
            'Clearing a pending request greys out its approval message, so '
            'nobody finds it later and presses Approve on a request that is '
            'gone.',
            'The archive record of an earlier approval is left alone. The '
            'archive says what was decided; this is a later decision, not a '
            'correction of that one.',
        ),
        commands=('/clear-slot',),
        page='slots',
    ),
    Topic(
        key='start-an-operation',
        title='Start an operation',
        group='slots',
        audience=ADMIN,
        where='Discord — `/setup-slots`, or the **Operation** tab',
        summary='Load a roster, set the start time, and put the board up.',
        steps=(
            'Decide where the roster comes from: an **ORBAT** you built here, '
            'or a **Google Sheet**.',
            'In Discord: `/setup-slots orbat:<name>` **or** `/setup-slots '
            'sheet_url:<link>` — one or the other, never both.',
            'Add `event_time` and `reminder_minutes` if you know them; both can '
            'be set later.',
            'In the browser: **🎖️ Operations → Operation → Start a different '
            'operation**, which is the same thing with a dropdown.',
            'The board is posted to **#orbat** automatically, with the request '
            'button on it.',
        ),
        notes=(
            'Starting an operation archives the previous one. Its pending '
            'requests are left alone and stay decidable.',
            'Times are read in the server timezone — set that once with '
            '`/set-timezone`.',
            'If the bot cannot post in the ORBAT channel the operation is still '
            'created and you are told. Post the board yourself afterwards.',
        ),
        commands=('/setup-slots',),
        page='operation',
        related=('operation-time', 'post-the-board', 'build-an-orbat'),
    ),
    Topic(
        key='operation-time',
        title='Set the start time and reminder',
        group='slots',
        audience=ADMIN,
        where='Discord — `/set-event-time`, or the **Operation** tab',
        summary='Move tonight’s start, and choose how long before it '
                'everybody is reminded.',
        steps=(
            '`/set-event-time time:25/06/2025 19:00 reminder_minutes:30`.',
            'Or the **Start time** field on the **Operation** page.',
            'The board updates itself, and the reminder is re-armed for the new '
            'time.',
        ),
        notes=(
            'The reminder DMs every approved member and pings **#orbat**. It '
            'fires once — moving the time arms it again.',
            'The time renders as a Discord timestamp, so everyone sees it in '
            'their own local time whatever the server timezone is.',
        ),
        commands=('/set-event-time',),
        page='operation',
        related=('channels-and-timezone',),
    ),
    Topic(
        key='post-the-board',
        title='Post or re-post the ORBAT board',
        group='slots',
        audience=ADMIN,
        where='Discord — `/post-orbat`, or the **Operation** tab',
        summary='Put a fresh board up when the old message has scrolled away or '
                'been deleted.',
        steps=(
            '`/post-orbat` posts it in the channel you run it in; '
            '`/post-orbat channel:#orbat` sends it elsewhere.',
            'Or press **Post the board** on the **Operation** page.',
            'The new message becomes the one the bot keeps up to date.',
        ),
        notes=(
            'There is only ever one live board. Posting a new one means the old '
            'message stops updating — delete it, or it will mislead somebody.',
            'The board refreshes itself on every approval, denial and release, '
            'so you should rarely need this.',
        ),
        commands=('/post-orbat',),
        page='operation',
    ),
    Topic(
        key='announce-an-operation',
        title='Announce the operation',
        group='slots',
        audience=ADMIN,
        where='Discord — `/post-event`, or the **Operation** tab',
        summary='Post an announcement embed pointing people at the board to sign '
                'up.',
        steps=(
            '`/post-event channel:#announcements`, optionally overriding the '
            'mission name and time.',
            'Or the **Post the announcement** panel on the **Operation** page.',
            'It names the operation, its start, and links **#orbat** for '
            'sign-ups.',
        ),
        notes=(
            'This only announces. Sign-up still happens through the board — for '
            'an event with its own attendee list, use `/event-create` instead.',
        ),
        commands=('/post-event',),
        page='operation',
        related=('create-an-event',),
    ),
    Topic(
        key='empty-the-queue',
        title='Empty the approval queue',
        group='slots',
        audience=ADMIN,
        where='Discord — `/clear-requests`, or the **Operation** tab',
        summary='Cancel every pending request for the operation at once.',
        steps=(
            'Run `/clear-requests`, or press it on the **Operation** page.',
            'Every pending request is cancelled and its approval message greyed '
            'out.',
        ),
        notes=(
            'Nobody is DMed and nothing is archived. This empties a queue that '
            'was never going to be answered — it is not the same as turning '
            'people down, which is what Deny is for.',
            'Approved bookings are untouched.',
        ),
        commands=('/clear-requests',),
        page='operation',
    ),
    Topic(
        key='inspect-the-roster',
        title='Check what the bot is reading',
        group='slots',
        audience=ADMIN,
        where='Discord — `/current-operation`, `/debug-slots`',
        summary='Find out which operation is live and what the bot sees on its '
                'roster, when a slot is missing.',
        steps=(
            '`/current-operation` names the operation and says whether it runs '
            'on an ORBAT or a sheet.',
            '`/debug-slots` prints the raw slots as the bot reads them; '
            '`/debug-slots squad:<name>` narrows it.',
            'On the web the same output is on the **Operation** page, under '
            '**Read the raw roster**, rendered in place.',
        ),
        notes=(
            'Each slot is keyed `db:412` on an ORBAT or `sheet:r12c4` on a '
            'sheet. A slot missing here is missing in the roster, not in the '
            'board.',
            'On a sheet, a slot the bot cannot see usually means the cell does '
            'not start with `1.` or `1-`, or its `<Insert Name>` marker is '
            'gone.',
        ),
        commands=('/current-operation', '/debug-slots'),
        page='operation',
    ),
)


_ORBATS: tuple[Topic, ...] = (
    Topic(
        key='build-an-orbat',
        title='Build an ORBAT',
        group='orbats',
        audience=ADMIN,
        where='Web — **🎖️ Operations → ORBATs**',
        summary='Write the roster as indented text and let the bot turn it into '
                'squads and slots.',
        steps=(
            'Go to **ORBATs**, give the new one a name, and press **Create**.',
            'Write the roster in the big text field: squad names at the left '
            'margin, slots indented under them.',
            'Put a squad’s options after a `|`, separated by commas: `left` '
            'or `right` for the column, `unit:TFP`, `radio:343 CHN:3`, and '
            '`nocount` for a bench that should not count against the numbers.',
            'Fill in the **Nets** field underneath for the shared channels, one '
            'per line as `Platoon Net | 152 CHN : 1`.',
            'Press **Preview** to see the board, then **Save**.',
        ),
        notes=(
            'A line starting with `#` is a comment. A leading `1.` or `2)` on a '
            'slot is stripped, so lines pasted out of a sheet land clean.',
            'Leave the column out entirely and the squads are split down the '
            'middle for you. One explicit `left` or `right` turns that guessing '
            'off for the whole ORBAT.',
            'Discord will not render an unlimited board. The editor warns you '
            'before you save when the ORBAT outgrows 25 fields or the character '
            'limits — eight squads plus a net list is the practical ceiling.',
        ),
        page='orbats',
        related=('edit-an-orbat', 'run-on-an-orbat'),
    ),
    Topic(
        key='edit-an-orbat',
        title='Edit an ORBAT without unseating anybody',
        group='orbats',
        audience=ADMIN,
        where='Web — **🎖️ Operations → ORBATs → the ORBAT**',
        summary='Change the roster while it is in use, and see who an edit '
                'would affect before it happens.',
        steps=(
            'Open the ORBAT and change the text.',
            'Press **Preview**. The diff lists what would be added, removed and '
            'renamed.',
            'Press **Save**. An edit that would remove or rename a slot '
            '*somebody holds* stops at a confirmation page naming them.',
            'Read that page, then confirm or go back and fix the text.',
        ),
        notes=(
            'Reordering lines is free — slots are matched by name first, so '
            'moving a squad changes nothing.',
            'Renaming keeps the booking. The person stays on the slot under its '
            'new name, which is why a rename asks: right for a typo, wrong if '
            'you meant to replace the role.',
            'Cutting three `Rifleman` lines to two keeps the first two, so a '
            'booked one is not the casualty.',
            'A red banner at the top means this ORBAT is backing the operation '
            'running right now, and an edit changes tonight’s board.',
        ),
        page='orbats',
        related=('build-an-orbat',),
    ),
    Topic(
        key='manage-orbats',
        title='Rename, copy or delete an ORBAT',
        group='orbats',
        audience=ADMIN,
        where='Web — **🎖️ Operations → ORBATs**',
        summary='Keep a library of rosters: give one a clearer name, copy it as '
                'the basis for the next one, or throw it away.',
        steps=(
            '**Rename**: open the ORBAT and use the **Name and description** '
            'form under the editor. The description is only ever shown in the '
            'list, to tell two similar rosters apart.',
            '**Duplicate**: press **Duplicate** on the ORBAT. You get a copy of '
            'the squads, slots and nets — and none of the bookings.',
            '**Delete**: press **Delete** and confirm.',
        ),
        notes=(
            'Duplicating is the way to build next week’s roster from this '
            'week’s without touching the one an operation is running on.',
            'An ORBAT backing the **active** operation cannot be deleted — the '
            'delete would take tonight’s whole board with it. Finish or '
            'replace the operation first.',
            'Deleting an ORBAT that ran an *old* operation is allowed, and '
            'releases those bookings in the same move rather than leaving '
            'records pointing at slots that no longer exist.',
        ),
        page='orbats',
    ),
    Topic(
        key='export-an-orbat',
        title='Export an ORBAT to a Google Sheet',
        group='orbats',
        audience=ADMIN,
        where='Web — **🎖️ Operations → ORBATs → the ORBAT**',
        summary='Write the roster into a spreadsheet, for people who want it '
                'there as well.',
        steps=(
            'Open the ORBAT and find the **Export to a sheet** panel.',
            'Paste the URL of the spreadsheet to write into.',
            'Press **Export**. A **new tab** is added holding the roster.',
        ),
        notes=(
            'It never touches an existing tab. A title collision gets a `(2)` '
            'suffix rather than overwriting anything, so an export can never '
            'damage the sheet another operation is running on.',
            'It is one-way. Nothing is recorded about the export, and the bot '
            'reads only the *first* tab — so an exported tab is not picked up '
            'on its own.',
            'Needs `GOOGLE_CREDENTIALS` configured, and the service account '
            'invited to the spreadsheet as an editor.',
        ),
        page='orbats',
        related=('run-on-a-sheet',),
    ),
    Topic(
        key='run-on-an-orbat',
        title='Run the operation on an ORBAT',
        group='orbats',
        audience=ADMIN,
        where='Discord — `/setup-slots orbat:<name>`',
        summary='Use a roster held here instead of a spreadsheet — the default '
                'choice, and the simpler one.',
        steps=(
            'Build the ORBAT first (see **Build an ORBAT**).',
            'Run `/setup-slots orbat:<name>` — the name autocompletes — or pick '
            'it from the dropdown on the **Operation** page.',
            'Everything else behaves identically: requests, approvals, the '
            'board, assigning and clearing.',
        ),
        notes=(
            'No Google account, no credentials and no network call is involved, '
            'so nothing about the evening depends on a third party being up.',
            'The approved request *is* the booking. There is no second copy to '
            'fall out of step with the board.',
            'The board additionally shows each squad’s unit and radio '
            'channel and the shared nets, which a sheet has nowhere to put.',
        ),
        commands=(),
        related=('run-on-a-sheet', 'start-an-operation'),
    ),
    Topic(
        key='run-on-a-sheet',
        title='Run the operation on a Google Sheet',
        group='orbats',
        audience=ADMIN,
        where='Discord — `/setup-slots sheet_url:<link>`',
        summary='Use an ORBAT-style spreadsheet as the roster, the way it '
                'worked before ORBATs existed.',
        steps=(
            'Share the spreadsheet with the service account in '
            '`GOOGLE_CREDENTIALS` as an **Editor**.',
            'Lay the first tab out ORBAT-style: a squad name, then slot cells '
            'starting `1.` or `1-`, each with `[] <Insert Name>` beside it.',
            'Run `/setup-slots sheet_url:<link>`.',
            'Approving writes the member’s name and unit tag into the cell; '
            'clearing restores `[] <Insert Name>`.',
        ),
        notes=(
            'Only the **first tab** is read, and the operation is named after '
            'the spreadsheet unless you pass `name:`.',
            'A sheet with no `<Insert Name>` markers has no free slots as far '
            'as the bot is concerned, whatever its columns are called.',
            'Inserting a row moves every cell below it. Run `/sync` afterwards '
            'to repair pending requests that now point at the wrong row.',
            'If the sheet write fails on approval the request is rolled back, '
            'so the sheet and the board never disagree.',
        ),
        related=('run-on-an-orbat', 'export-an-orbat'),
    ),
)


_EVENTS: tuple[Topic, ...] = (
    Topic(
        key='create-an-event',
        title='Create an event',
        group='events',
        audience=UNIT_LEADER,
        where='Discord — `/event-create`, or the **📅 Events** tab',
        summary='Post a training, a movie night or anything else with its own '
                'sign-up buttons and attendee list.',
        steps=(
            'In Discord: `/event-create title:<name> start_time:25/06/2025 '
            '19:00`.',
            'Add what you need: `description`, `duration`, `location`, '
            '`channel`, `mention`, `reminder`, `image_url`.',
            'In the browser: **📅 Events → New event**, which is the same '
            'fields as a form.',
            'The message goes up with the sign-up buttons on it and a live '
            'attendee list.',
        ),
        notes=(
            'A start time in the past is refused.',
            '`duration` is what lets the event close itself out afterwards — '
            'without it the message keeps its buttons indefinitely.',
            'Events are entirely separate from operations and ORBAT slots. '
            'Nothing here touches a roster.',
        ),
        commands=('/event-create',),
        page='events',
        related=('signup-for-an-event', 'repeat-an-event', 'event-responses'),
    ),
    Topic(
        key='signup-for-an-event',
        title='Sign up for an event',
        group='events',
        audience=EVERYONE,
        where='Discord — the event message, or the **📅 Events** tab',
        summary='Answer on the buttons; the attendee list updates for everyone '
                'immediately.',
        steps=(
            'Press **✅ Accepted**, **❓ Tentative** or **❌ Declined** on the '
            'event message — or whatever options that event defines.',
            'Changing your mind: press a different button.',
            'Press the button you already chose to **withdraw** entirely.',
            '`/event-list` shows what is coming up with counts and jump links.',
        ),
        notes=(
            'Withdrawing is not the same as declining. Declining says you are '
            'not coming; withdrawing takes you off the list altogether.',
            'Start times render as Discord timestamps, so you see them in your '
            'own local time.',
        ),
        commands=('/event-list',),
        page='events',
    ),
    Topic(
        key='repeat-an-event',
        title='Repeat an event',
        group='events',
        audience=UNIT_LEADER,
        where='Discord — `repeat:` on `/event-create` or `/event-edit`',
        summary='Make it a series: the next occurrence posts itself when this '
                'one finishes.',
        steps=(
            'Pass `repeat:` when creating, or add it later with `/event-edit '
            'event:<name> repeat:weekly`.',
            'Pick a pattern: daily, weekly, every 2 weeks, monthly by date, '
            'monthly by weekday (*last Saturday* or *2nd Saturday*), or weekly '
            'except the last of the month.',
            'Bound it with `repeat_until:` if the series should stop on a date.',
            'Use `repeat_delay:` to hold the next post back a number of hours '
            'after this one ends, instead of posting it the moment it closes.',
            'Stop a series with `/event-edit event:<name> repeat:none`.',
        ),
        notes=(
            'The weekday patterns take both the weekday and the position from '
            'the **first** occurrence. A series created on Saturday the 13th '
            'means *2nd Saturday* under *monthly by weekday*.',
            'Only one occurrence is live at a time — there is no pre-generated '
            'calendar, and sign-ups deliberately do not carry over.',
            'Dates are measured from the first occurrence, so a series on the '
            '31st gives 28 Feb → 31 Mar rather than drifting to the 28th for '
            'good.',
            'A bot that was offline for ten weeks posts **one** occurrence in '
            'the future, not one per missed week.',
        ),
        commands=(),
        page='events',
        related=('edit-an-event',),
    ),
    Topic(
        key='event-responses',
        title='Custom sign-up options',
        group='events',
        audience=UNIT_LEADER,
        where='Discord — `responses:` on `/event-create` or `/event-edit`',
        summary='Replace Accepted / Tentative / Declined with your own buttons.',
        steps=(
            'Pass `responses:` with the options separated by `|`, for example '
            '`🚁 Pilot | 🔫 Infantry | -❌ Can’t`.',
            'A leading `-` marks an option as *not coming*: those people are '
            'left out of reminders and cancellation DMs.',
            'A leading emoji is put on the button.',
        ),
        notes=(
            'Between 2 and 10 options, labels under 40 characters, and at least '
            'one that is not a decline.',
            'Changing the options on a live event drops the sign-ups whose '
            'answer no longer exists, and tells you how many. Everyone else '
            'keeps theirs.',
            'A repeating event copies its options to each new occurrence.',
        ),
        commands=(),
        page='events',
    ),
    Topic(
        key='event-mentions',
        title='Ping roles on an event',
        group='events',
        audience=UNIT_LEADER,
        where='Discord — `mention:` on `/event-create` or `/event-edit`',
        summary='Notify one or more roles when the event goes up and again on '
                'the reminder.',
        steps=(
            'Pass `mention:@Infantry @Armour` — type the `@` and let Discord '
            'turn it into a role token.',
            'Role names in a comma-separated list work too, if the tokens are '
            'awkward to type.',
            'On the web, the roles are checkboxes on the event form.',
            'Clear them again with `/event-edit event:<name> mention:none`.',
        ),
        notes=(
            'Up to ten roles. Anything that cannot be resolved is reported '
            'rather than dropped quietly.',
            'A role that is not **mentionable** needs the bot to have *Mention '
            'All Roles*, or the ping notifies nobody. You are warned when that '
            'is the case.',
        ),
        commands=(),
        page='events',
    ),
    Topic(
        key='edit-an-event',
        title='Edit, cancel or delete an event',
        group='events',
        audience=ORGANISER,
        where='Discord — `/event-edit`, `/event-cancel`, `/event-delete`',
        summary='Change the details, call it off while keeping the record, or '
                'remove it for good.',
        steps=(
            '**Edit**: `/event-edit event:<name>` and pass only the fields that '
            'change. The rest keep their values.',
            '**Cancel**: `/event-cancel event:<name> reason:<why>`. The message '
            'turns red and loses its buttons, and everyone attending is DMed.',
            '**Delete**: `/event-delete event:<name>`, then confirm. The event '
            'and its message are gone.',
            'All three are on the event’s page in the browser as well.',
        ),
        notes=(
            'Moving the start time re-arms the reminder, so it fires again for '
            'the new time.',
            'Cancel tells people; delete does not. If anybody has signed up, '
            'cancel is almost always what you want — the confirmation page says '
            'so.',
            'On a repeating event, cancelling one occurrence still posts the '
            'next one. `stop_series:True` ends the series instead.',
            'Moving an event to a different channel is not possible — the '
            'sign-up history belongs to the message. Cancel and recreate.',
        ),
        commands=('/event-edit', '/event-cancel', '/event-delete'),
        page='events',
    ),
)


_ROLES: tuple[Topic, ...] = (
    Topic(
        key='pick-game-roles',
        title='Pick your game roles',
        group='roles',
        audience=EVERYONE,
        where='Discord — the panel button or `/game-roles`, or the **🎮 Game '
              'roles** tab',
        summary='Give yourself the tag for the games you play, so people can '
                'ping everyone who plays them.',
        steps=(
            'Press **🎮 Choose your game roles** on the panel, or run '
            '`/game-roles`.',
            'The roles you already have are ticked. Tick what you want, untick '
            'what you don’t, and submit.',
            'Or press **➖ Remove a role** for a list of only the ones you '
            'currently hold.',
            'In the browser it is the **🎮 Game roles** tab — same list, same '
            'result.',
        ),
        notes=(
            'These roles grant no permissions at all. They exist to be '
            'mentioned.',
            '`/game-role-list` shows every game role on the server without '
            'changing yours.',
            'Unticking everything is a valid answer and removes all of them.',
        ),
        commands=('/game-roles', '/game-role-list'),
        page='roles',
    ),
    Topic(
        key='manage-game-roles',
        title='Add and remove game roles',
        group='roles',
        audience=ADMIN,
        where='Discord — `/game-role-add`, `/game-role-remove`, '
              '`/game-role-panel`',
        summary='Decide which games are on the list, and put the self-assign '
                'panel in a channel.',
        steps=(
            '`/game-role-add name:Minecraft emoji:⛏️ description:Survival '
            'server` creates a permission-free role and lists it.',
            'A Discord role with that **exact name** already exists? It is '
            'reused, not duplicated — running the command again just updates '
            'the emoji and description.',
            '`/game-role-remove role:<role>` takes it off the list. Add '
            '`delete_role:True` to delete the Discord role as well.',
            '`/game-role-panel channel:#roles` posts the panel with the '
            'self-assign button.',
            'The same three live on the **🎮 Game roles** tab for admins.',
        ),
        notes=(
            'Up to 25 roles — as many as a Discord menu can show.',
            'A pre-existing role is refused if it grants **any** permission, is '
            '`@everyone`, is managed by an integration, or sits above the bot '
            'in the role list.',
            'Unit roles and `Unit Leader` can never be made self-assignable, so '
            'nobody can tick their way into approval rights.',
            'There is one panel per server and it updates itself whenever the '
            'list changes.',
        ),
        commands=('/game-role-add', '/game-role-remove', '/game-role-panel'),
        page='roles',
    ),
)


_MAPS: tuple[Topic, ...] = (
    Topic(
        key='draw-a-plan',
        title='Draw the plan',
        group='maps',
        audience=UNIT_LEADER,
        where='Web — **🎖️ Operations → Maps**',
        summary='Put unit symbols, movement lines, objectives and boxes on the '
                'terrain.',
        steps=(
            'Go to **Maps**, name the new map, and press **Create**.',
            'The editor: tools down the left, the map in the middle, and on the right '
            'what the tool in hand will draw — or, with something selected, its '
            'details. Terrain, OCAP, sharing, posting, Arma and the map’s name are '
            'menus in the bar above. **⤢ Whole window** gives the map the screen '
            '(**Esc** to come back); **+ − ⤢** on the map zoom and fit it.',
            'Pick a symbol from the palette and click the sheet to place it.',
            'Select a symbol to open the inspector: side, what it is, size, '
            'mobility, parent unit, whether it is only planned, and its label.',
            'Lines work as on Arma’s map: hold **Ctrl** and **click** each corner, '
            'then let go of **Ctrl** to finish — or hold **Ctrl** and **drag** for '
            'freehand. The Line and Area tools do the same without Ctrl; double-click '
            'or press **Enter** to finish. **Shift** keeps a segment straight and '
            'snaps it to 15°.',
            'Pick a line’s style (solid, dashed or dotted), its thickness (1 to 6 '
            'pixels — it stays that thin at every zoom) and its arrow heads (end, '
            'start or both) in the toolbar or the inspector.',
            'The **Phase line** tool draws a line named at both ends — type the '
            'name after the *PL* it starts with. Any line can be named in the '
            'middle, at the end or at both ends from the inspector.',
            'Select a line or area to edit it: drag a corner to move it, drag the '
            'dot in the middle of a segment to add a corner, **Alt**+click or '
            'right-click a corner to delete it. Tick **Snap** and new or moved '
            'corners land on the nearest corner, symbol or marker.',
            '**Shift**+click adds to the selection and **Shift**+drag on empty '
            'ground selects by box; move them together by dragging any of them. '
            '**Ctrl**+**C** and **Ctrl**+**V** copy and paste — into another map '
            'too — and **Ctrl**+**D** duplicates.',
            'The corner of the map shows the six-figure grid reference under the '
            'pointer, and the length and bearing of what you are drawing. The '
            '**Measure** tool measures without drawing; the inspector shows a '
            'selected line’s length or an area’s size.',
            'For a box or a circle, drag corner to corner with **Rectangle** or '
            '**Ellipse** — hold **Shift** for a square or a circle. Select it to '
            'resize it by a corner, turn it by the round handle, and choose an '
            'outline, a light fill or hatching.',
            'The **AO** tool greys out the whole map and clears each area you draw '
            'with it, the way a covered Arma map does. Each AO gets a border colour no '
            'other AO uses and its name in a tab of that colour at its top-left corner.',
            'It saves by itself a moment after each change (**Autosave** in the bar; '
            'untick it to save only when you press **Save**). Everyone looking at '
            'the map sees the saved version.',
        ),
        notes=(
            'The frame says whose it is: rectangle friendly, diamond hostile, '
            'square neutral, quatrefoil unknown. Open at the bottom means it is '
            'in the air; **dashed** means it is planned rather than there.',
            'The **Marker** tool has every marker vanilla Arma ships — military, '
            'hand-drawn, map locations, unit sizes, respawn points, Zeus and flags — '
            'grouped as in the game, and each exports as exactly that marker.',
            'An admin can upload the game’s own marker icons (a zip of PNGs) at the '
            'bottom of the **Terrains** page; every map on the server then draws '
            'those markers from them, in the marker’s colour. They stay private to '
            'the server’s members and share-link holders.',
            'Lines get an arrow head only when you ask for one — most lines on '
            'a plan are boundaries and phase lines, which point nowhere.',
            'Lines, areas, shapes and markers are black until you colour them, and '
            'take whatever you pick from the swatches. Symbols do not: their '
            'colour is what says which side they are.',
            'Whatever colour you pick is the colour it comes out in Arma, on '
            'the nearest marker colour the game ships. Cyan and purple are '
            'the two Arma has no match for.',
            'If somebody else saved the map after you opened it, your save is '
            'stopped and you choose: reload theirs, or save yours over it.',
            'On a campaign map, an AO’s layer is the planner’s: its name follows the '
            'AO, and deleting it deletes the AO when you save. A phase’s layer on an '
            'AO map cannot be deleted here.',
        ),
        page='maps',
        related=('map-layers', 'map-background', 'share-a-map'),
    ),
    Topic(
        key='map-layers',
        title='Use layers',
        group='maps',
        audience=UNIT_LEADER,
        where='Web — the layer panel on a map',
        summary='Keep phase 1, phase 2 and the enemy picture apart, and show '
                'them one at a time.',
        steps=(
            'Add a layer in the layer panel and name it.',
            'Whatever you place next lands on the layer that is selected.',
            'Use each layer’s switch to show and hide it while briefing.',
        ),
        notes=(
            'Hiding a layer is not deleting it. The items stay in the document '
            'and in every save.',
            'The switches work for whoever is looking, including somebody who '
            'only has a share link — toggling changes their view and nothing '
            'else.',
            'Deleting a layer moves its items to the first layer rather than '
            'taking them with it.',
            'The ↑ ↓ beside a layer change the drawing order — lower in the list is '
            'drawn on top. 🔒 locks a layer: it is still drawn, but nothing on it can '
            'be picked up or moved while you draw over it.',
            'A hidden layer is left out of the Arma export: markers you have '
            'switched off are not part of the plan being handed over.',
        ),
        page='maps',
    ),
    Topic(
        key='map-background',
        title='Put a real terrain behind the plan',
        group='maps',
        audience=ADMIN,
        where='Web — **Terrains**, or the map editor’s **Terrain** and **OCAP** menus',
        summary='Draw on the actual terrain instead of a blank sheet.',
        steps=(
            '**Best: upload it.** Go to **Terrains** (linked from the Maps '
            'page) and upload the same tile archive you prepare for OCAP — a '
            'zip or 7z with the numbered folders `0/ 1/ 2/ …` in it.',
            'Then open a map and use the **🗻 Terrain** menu in its bar to pick it.',
            '**Or point at a running OCAP server**: in the editor’s **🛰️ OCAP** '
            'menu, press **List the terrains**, pick one, and import it.',
            'Either way the terrain’s Arma coordinates come along, which '
            'is what makes the export land in the right place.',
        ),
        notes=(
            'Prefer the upload for anything shared outside the unit. A share '
            'link is for people who do not sign in here, and expecting them to '
            'reach your OCAP instance as well is how a link ends up showing an '
            'empty sheet.',
            'If the archive has no `map.json`, you are asked for the world size '
            'in metres. Without it the tiles would draw but the Arma export '
            'would be nonsense.',
            'Levels deeper than a planning sheet needs are dropped, and the '
            'reply says how many — the terrain is meant to be smaller than the '
            'file.',
            'A terrain cannot be deleted while a map is drawn on it, and the '
            'refusal names the maps.',
        ),
        page='maps',
        related=('draw-a-plan',),
    ),
    Topic(
        key='map-place-names',
        title='Put the town names on the map',
        group='maps',
        audience=ADMIN,
        where='Web — the map\u2019s **OCAP** menu, or **Terrains**',
        summary='A terrain arrives with roads and contours and no labels. This '
                'is how it learns what its towns are called.',
        steps=(
            '**On an OCAP terrain:** open the map, open the **🛰️ OCAP** menu \u2192 '
            '**Place names from OCAP**, and press **Import place names**. It reads '
            'the server the map points at.',
            '**On a terrain uploaded here:** the archive\u2019s own '
            '`locations` files are read on upload, if it has them.',
            'Either way it is once per **terrain** — every map drawn on it '
            'gains the names.',
            'Too many? **Map settings** in the editor\u2019s side panel has a switch '
            'per group and a size slider.',
            '**If it finds nothing**, it is that one terrain that carries no '
            'location data \u2014 other terrains on the same server can still '
            'import fine. The same panel then has a script and a paste box: '
            'run it in a mission on that terrain, press LOCAL EXEC, paste the '
            'clipboard in. **Once for the terrain** \u2014 not once per map, '
            'not once per operation.',
        ),
        notes=(
            'The names are in the OCAP data, just not in `map.json`. OCAP '
            'builds a terrain from a **grad_meh** export, which writes them '
            'beside the tiles as `geojson/locations/<type>.geojson.gz` — one '
            'file per Arma location type, and the file name is the type.',
            'Whether they are there is decided **per terrain**, not per '
            'server. A terrain built the older way \u2014 Arma\u2019s map '
            'export through gdal2tiles \u2014 carries no vector data at all, '
            'so there is nothing to find for it however hard the import '
            'looks, while the terrain next to it on the same OCAP imports in '
            'one press. A failure names **every address it tried**, so you '
            'can tell that apart from a broken feature.',
            'The tiles genuinely have no labels in them: they come out of '
            'Arma\u2019s own map export as pure topography, and the game draws '
            'the names over that from its config afterwards.',
            'Place names are never exported to Arma — the game already draws '
            'its own.',
        ),
        page='maps',
        related=('map-background', 'draw-a-plan'),
    ),
    Topic(
        key='share-a-map',
        title='Share, copy or post a map',
        group='maps',
        audience=UNIT_LEADER,
        where='Web — the map’s **Share** and **Post** menus',
        summary='Give people a link that needs no login, copy a plan to start '
                'the next one, or announce it in a channel.',
        steps=(
            '**Share**: open the **Share** menu and pick **view** or **edit**. '
            'You get a `/m/…` link that works without signing in.',
            '**Post**: pick a channel and press **Post**. The bot sends an '
            'embed with the link and the counts.',
            '**Duplicate**: press **Duplicate** for a copy of the plan — the '
            'share link deliberately does not come with it.',
            '**Rename**: the **Name and description** form on the map page.',
            'Turn sharing off again by setting it to **off**, or press **New '
            'link** to replace the token.',
        ),
        notes=(
            'The link *is* the credential. Anybody who has it can open the map, '
            'and with **edit** can draw on it. **New link** is the only way to '
            'un-share a map that has travelled further than intended.',
            'Changing view↔edit keeps the same token, so tightening the rights '
            'does not break every copy of the link already out there.',
            'A map posted in Discord keeps the link it was posted with. Post it '
            'again if the site’s address changes.',
            'It posts a link rather than a picture, which is what keeps it '
            'current while the plan is still being drawn.',
        ),
        page='maps',
    ),
    Topic(
        key='map-into-arma',
        title='Get the plan into Arma 3',
        group='maps',
        audience=UNIT_LEADER,
        where='Web — the map’s **To Arma** menu',
        summary='Paste the plan into a running mission as markers, with no mod '
                'on either side.',
        steps=(
            'Make sure the map sits on a terrain, or that its Arma corners are '
            'filled in — the export needs to know where the sheet is in the '
            'world.',
            'Open the **🎯 To Arma** menu in the map’s bar and copy one of the two scripts.',
            'In the mission, open the **debug console** as a logged-in admin '
            'and paste it.',
            'Press **LOCAL EXEC**. `createMarker` is global, so one machine '
            'running it draws the plan for everybody.',
        ),
        notes=(
            'Two scripts, side by side. The first draws markers that stay put. '
            'The second draws markers players can drag and delete in game — '
            'handy, and the same click that fixes a misplaced objective also '
            'deletes it, with nothing asking first.',
            'Both are **LOCAL EXEC**. GLOBAL EXEC is wasted on the first and '
            'actively wrong on the second, where the marker names carry the '
            'client that made them.',
            'Running it again **replaces** the markers from the last run rather '
            'than laying a second set over them. Two maps can never delete each '
            'other’s.',
            'Symbol sizes and mobility have no counterpart in Arma, so they are '
            'written into the marker text: `1-1 Alpha / A Coy (planned Plt '
            'twd)`.',
            'Rectangles and ellipses arrive as Arma’s own area markers, filled '
            'and turned the way they are drawn. Arma shows no text on an area '
            'marker, so their labels stay on the website’s map.',
            'AOs come across as their borders. The grey mask around them does not: '
            'Arma cannot draw a cover with holes in it from a pasted script.',
            'Arma has no dashed line and draws every line at one width, so a '
            'dashed or dotted line arrives as its dashes — one short line marker '
            'each, at most 150 per line — and thickness stays on the website. '
            'Arrow heads arrive as Arma arrow markers at whichever ends have one.',
            'A line’s name arrives as text alone at the same place — the middle, '
            'or both ends of a phase line — on an invisible marker, because a line '
            'marker shows no text in Arma.',
            'Above the scripts, the panel says how many markers the plan makes and '
            'what will not survive the trip.',
            'The debug console has to be enabled in the mission’s '
            '`description.ext`.',
        ),
        page='maps',
    ),
    Topic(
        key='map-from-arma',
        title='Bring markers back from Arma 3',
        group='maps',
        audience=UNIT_LEADER,
        where='Web — the map’s **From Arma** menu',
        summary='Copy the markers a mission’s players drew onto the map, so a plan '
                'finished in game can be kept and briefed.',
        steps=(
            'Check the map’s Arma corners match the mission’s terrain — the '
            'markers are placed by them.',
            'Copy the script in the **📥 From Arma** menu, run it in the '
            'mission’s debug console with **LOCAL EXEC**: it copies every marker '
            'to your clipboard.',
            'Paste into **What it copied**, leave **Only markers players drew** '
            'ticked unless you want the mission’s own too, and press **Import**.',
        ),
        notes=(
            'They land on a new layer, *From Arma*, to switch off or delete in one go.',
            'Lines, areas, rectangles, ellipses, the standard military markers and '
            'NATO unit markers come back as themselves. Any other marker comes back '
            'as a dot named after its type.',
            'Markers outside this map are left out, and the message says how many.',
        ),
        page='maps',
    ),
)


_REDDIT: tuple[Topic, ...] = (
    Topic(
        key='watch-reddit',
        title='Watch a Reddit user or subreddit',
        group='reddit',
        audience=ADMIN,
        where='Web — **📣 Reddit**',
        summary='Announce every new post in a channel, in your own words.',
        steps=(
            'Go to **📣 Reddit → Add a watch**.',
            'Pick **user** or **subreddit** and type the name without the `u/` '
            'or `r/`.',
            'Choose the channel, write the announcement text, and tick the '
            'roles and list the people to ping.',
            'Press **Preview** to see the newest post rendered with your text — '
            'it posts nothing.',
            'Save. The first check notes what is already on the feed and '
            'announces none of it.',
        ),
        notes=(
            '`{title}`, `{url}`, `{author}` and `{subreddit}` are filled in. '
            'Everything else is literal.',
            'Checked every five minutes. **Check now** runs that check early '
            'and does announce.',
            'No Reddit account, API registration or secret is needed — it reads '
            'the public feed.',
            'The bot will not ask anybody to upvote anything, and there is no '
            'wording for it. Organised voting is what gets accounts banned, and '
            'the post score is deliberately never read or shown.',
            'Pointing a watch at a different source resets what it has seen, so '
            'the new feed is seeded rather than announced from scratch.',
        ),
        page='reddit',
        related=('reddit-catch-up',),
    ),
    Topic(
        key='reddit-catch-up',
        title='Catch a post up, or skip a backlog',
        group='reddit',
        audience=ADMIN,
        where='Web — **📣 Reddit → the watch → Recent posts**',
        summary='Post something the watch missed, or clear a queue you do not '
                'want announced.',
        steps=(
            'Open the watch and go to **Recent posts**. It reads the feed and '
            'says which posts have already been announced.',
            'Press **Announce** on one to post it by hand.',
            'To skip instead: tick the posts you do not want and press **Mark '
            'ticked as announced**, or **Mark all** for the whole list.',
        ),
        notes=(
            'Marking posts nothing. It just takes them out of the queue so the '
            'next check does not work through them three at a time.',
            'This is the way past a flood — an account that un-hid its posts, '
            'or a subreddit that suddenly woke up.',
            'Marking needs no working connection to Reddit, which matters: a '
            'backlog most needs clearing exactly when Reddit is refusing us.',
        ),
        page='reddit',
    ),
    Topic(
        key='reddit-author-filter',
        title='Follow someone whose profile hides their posts',
        group='reddit',
        audience=ADMIN,
        where='Web — **Only these authors** on a subreddit watch',
        summary='Watch the subreddit instead, and keep only what that account '
                'wrote.',
        steps=(
            'Reddit lets an account hide its posts from its own profile, which '
            'is what a **user** watch reads. If a user watch stays empty, that '
            'is usually why.',
            'Make a **subreddit** watch on the subreddit they post in.',
            'Put their account name in **Only these authors** — several, '
            'comma-separated, if you like.',
        ),
        notes=(
            'The filter is applied before anything else looks at the posts, so '
            'the other authors’ posts never take up the watch’s '
            'memory.',
            'A very busy subreddit can push a post out of its own feed between '
            'two checks. Nothing can be done about that from here.',
            'Widening the filter later makes old posts look new. Use **Recent '
            'posts → Mark all** afterwards.',
        ),
        page='reddit',
    ),
)


_KILLTRACKER: tuple[Topic, ...] = (
    Topic(
        key='killtracker-rename-missions',
        title='Rename a mission',
        group='killtracker',
        audience=ADMIN,
        where='Web — **📊 Kill Tracker → Missions**',
        summary='Show a mission under a clean name when it was not named cleanly in game.',
        steps=(
            'Open **📊 Kill Tracker → Missions**. Every recording the bot has found on the '
            'OCAP server is listed, newest first, with the name OCAP has for it.',
            'Type the name you want in the box on the right of each mission you want to change — as many as you like.',
            'Press **Save all changes** once. Only the names you changed are written, and none are if one of them is not valid.',
            'To go back to OCAP’s name, clear the box and save.',
        ),
        notes=(
            'The recording’s own date is added after the name, so type just the name; if you '
            'type the date as well it is taken off again.',
            'Only the shown name changes. The statistics are not touched, so the name survives '
            'every re-count.',
            'The public numbers pick the new name up within about a minute.',
            'Missions are in a campaign by recording, not by name, so a renamed mission stays '
            'in its campaigns.',
        ),
        page='ktmissions',
        related=('killtracker-import',),
    ),
    Topic(
        key='killtracker-campaigns',
        title='Manage campaigns',
        group='killtracker',
        audience=ADMIN,
        where='Web — **📊 Kill Tracker → Campaigns**',
        summary='Group missions into a campaign, which the Kill Tracker shows as a page of its own.',
        steps=(
            'Open **📊 Kill Tracker → Campaigns** and press **+ New campaign**.',
            'Give it a name and tick the missions that belong to it.',
            'Optionally add **names to match**, one per line: a mission whose name contains '
            'one of them belongs to the campaign as well.',
            'Press **Create**. To change it later, open it from the list; **Delete campaign** '
            'removes the campaign and leaves the missions alone.',
        ),
        notes=(
            'A mission can be in several campaigns.',
            'Names that match no recording appear only after an import from the old '
            'campaigns.json; keep them ticked and they are included as they were.',
        ),
        page='ktcampaigns',
        related=('killtracker-import',),
    ),
    Topic(
        key='killtracker-link-names',
        title='Link a player’s names',
        group='killtracker',
        audience=ADMIN,
        where='Web — **📊 Kill Tracker → Players**',
        summary='Count a player who played under several names as one, and pick the name shown.',
        steps=(
            'Open **📊 Kill Tracker → Players**. Use **Find a name** to narrow the list.',
            'In **Link names**, tick every name that is the same person.',
            'Choose which one to show in the **Show** column and press **Link the ticked names**.',
            'To change the name shown later, press **Show “…”** beside the one you want in the '
            'list of linked players above.',
            'To split a name off again, press **Unlink “…”**.',
        ),
        notes=(
            'The Kill Tracker also joins obvious variants of a name by itself. This is for the '
            'ones it cannot know, such as a changed callsign.',
            'Linking pulls in everything already linked to any of the names, so there are never '
            'chains: they all end up under the name you picked.',
        ),
        page='ktplayers',
        related=('killtracker-units', 'killtracker-import'),
    ),
    Topic(
        key='killtracker-units',
        title='Set a player’s unit',
        group='killtracker',
        audience=ADMIN,
        where='Web — **📊 Kill Tracker → Units**',
        summary='Correct the unit the Kill Tracker guessed for a player.',
        steps=(
            'Open **📊 Kill Tracker → Units** and find the player.',
            'Choose their unit in the list on their row.',
            'Press **Save changes** at the bottom. Only the rows you changed are written.',
        ),
        notes=(
            '**Automatic** leaves it to the Kill Tracker’s own guess, which uses the squads the '
            'player plays in and a tag in the name such as `[2nd USC]`.',
            '**Unknown** stops the guess and shows no unit.',
            'Players are listed under the name that is shown, after linking.',
        ),
        page='ktunits',
        related=('killtracker-link-names',),
    ),
    Topic(
        key='killtracker-kills',
        title='Check the longest kills',
        group='killtracker',
        audience=ADMIN,
        where='Web — **📊 Kill Tracker → Longest kills**',
        summary='Look through the farthest kills of all missions and strike the ones that '
                'cannot be true.',
        steps=(
            'Open **Longest kills**. The farthest infantry kills of all missions are listed, '
            'farthest first, with who made them, with which weapon, on whom and in which mission. '
            'Choose a mission or type a player to see all of theirs.',
            'Tick **Strike** on a kill that is not believable, and press **Save changes**. It leaves '
            'the list at once and appears under **Struck kills**; once the mission is counted again '
            'the next-farthest kill takes its place.',
            'The mission is counted again within a few minutes, and the public numbers change '
            'when that is done.',
            'To put a kill back, untick it under **Struck kills** and save.',
        ),
        notes=(
            'A struck kill **still counts as a kill**; only its distance leaves the longest and '
            'average kill distance.',
            'Kills from a vehicle are not listed: a vehicle’s weapons reach that far. Each '
            'player’s own longest kill is always on offer, so the number on the dashboard can be '
            'checked.',
            'Mortars and other indirect fire, pistols beyond 300 m, the crew of a destroyed '
            'vehicle and on-foot kills beyond 1500 m are left out automatically and are not '
            'listed.',
            'The decision is kept with the recording, so a later re-count does not undo it.',
        ),
        page='ktkills',
        related=('killtracker-units',),
    ),
    Topic(
        key='killtracker-import',
        title='Take over the old sheet and JSON files',
        group='killtracker',
        audience=ADMIN,
        where='Web — **📊 Kill Tracker → Import**',
        summary='Bring the mission names, campaigns, linked names and units over from the '
                'sheet and the dashboard’s JSON files.',
        steps=(
            'Export the sheet’s **player_stats** tab as CSV and choose it under **1 · Mission '
            'names**. Press **Take over the names**: where the sheet’s name for a mission differs '
            'from OCAP’s, the sheet’s is taken, and the page lists each one.',
            'Choose **campaigns.json** under **2 · Campaigns** and press **Take over the '
            'campaigns**. Do this after step 1, because the campaigns name their missions the '
            'way the sheet did.',
            'Do the same with **player_aliases.json** and **unit_overrides.json**.',
        ),
        notes=(
            'Nothing is deleted. A mission, campaign, link or unit that is already set here is '
            'left alone — except mission names, where **Replace names that were already set** '
            'overrides that.',
            'A recording the bot has not counted yet is not in the list; import again once it is.',
            'A mission name in a campaign that matches no recording stays on the campaign and '
            'is shown on its page, so it can be dropped by hand.',
        ),
        page='ktimport',
    ),
)


_SERVER: tuple[Topic, ...] = (
    Topic(
        key='member-log',
        title='Log joins, leaves, kicks and bans',
        group='server',
        audience=ADMIN,
        where='Web — **📋 Member log**',
        summary='Announce who came and went in a channel your staff watches.',
        steps=(
            'Go to **📋 Member log**, pick the channel, tick the events you '
            'want, and save.',
            'Joins show the account’s age, the member count and which '
            'invite link was used.',
            'Leaves show how long they were around and which roles they had.',
            'Kicks are told apart from voluntary leaves, and name the moderator '
            'and reason. Bans and unbans do the same.',
        ),
        notes=(
            'Joins and leaves need Discord’s privileged **Server Members '
            'Intent**. Tick it in the Developer Portal **first**, then set '
            '`MEMBER_EVENTS=1` and redeploy — asking for the intent before it '
            'is granted stops the bot from starting at all.',
            'Bans and unbans need no intent and work without that.',
            'Without **View Audit Log** every kick reads as a plain leave and '
            'bans have no moderator. Without **Manage Server** the invite list '
            'cannot be read. The page tells you which are missing.',
        ),
        page='logs',
        related=('welcome-message', 'invite-labels'),
    ),
    Topic(
        key='welcome-message',
        title='Welcome new members',
        group='server',
        audience=ADMIN,
        where='Web — **📋 Member log → Welcome message**',
        summary='Greet somebody who joins, in a public channel, by DM, or '
                'both.',
        steps=(
            'Go to **📋 Member log** and scroll to **Welcome message**.',
            'Pick the channel the greeting is posted in — or leave it on '
            '*Nowhere* if you only want the DM.',
            'Tick **Also send it as a DM** to send the same text to the member '
            'directly.',
            'Write the text. Leave it empty to use the default.',
            'Save. The next person through the door gets it.',
        ),
        notes=(
            'Four placeholders: `{member}` pings them, `{name}` is their name '
            'as plain text, `{server}` is the server name and `{member_count}` '
            'is how many members there now are. Anything else is literal, so a '
            'stray `{` is harmless.',
            'This is **separate from the join log**. It has its own channel, it '
            'does not need the log to be switched on, and it does not care '
            'about the *Announce joins* tick — the log is written for staff, '
            'the welcome for the member.',
            'It does use the same join event, so it needs the same **Server '
            'Members Intent** as the join log.',
            'A member with DMs closed simply does not get the DM. Nothing '
            'fails, and the channel message still goes out.',
        ),
        page='logs',
        related=('member-log',),
    ),
    Topic(
        key='invite-labels',
        title='Label your invite links',
        group='server',
        audience=ADMIN,
        where='Web — **📋 Member log → Invite links**',
        summary='Say where each link was published, so a join names the source '
                'instead of a code.',
        steps=(
            'Go to **📋 Member log** and find the invite list at the bottom, '
            'with each link’s use count.',
            'Type what that link is for next to it — *Steam*, *Website*, '
            '*Reddit*.',
            'Save. Joins then read `rnPAfscGbE · Steam` rather than the code '
            'alone.',
        ),
        notes=(
            'Needs **Manage Server**, or the invite list cannot be read at all.',
            'Labels for links that have since expired are kept and stay '
            'editable, because old join messages still refer to them.',
            'A join through a single-use link can still be attributed: Discord '
            'deletes the link immediately, and the bot works out which one went '
            'missing.',
            'Two people joining in the same second cannot be told apart, and a '
            'member added by another bot has no invite at all. Those say so '
            'rather than guessing.',
        ),
        page='logs',
        related=('member-log',),
    ),
    Topic(
        key='voice-time',
        title='Track time in voice',
        group='server',
        audience=ADMIN,
        where='Web — **🔊 Voice time**',
        summary='Record how long members spend in voice, and show a '
                'leaderboard.',
        steps=(
            'Go to **🔊 Voice time**. It is **off** until an admin switches it '
            'on, and nothing is recorded before that.',
            'Tick **Enabled** and save. Everybody can then see the leaderboard '
            'for 24 hours, 7, 30 or 90 days, or all time.',
            'Optionally pick a channel to announce finished visits in, with a '
            'minimum length so quick drop-ins do not fill it.',
            'For a self-updating board, tick **Keep a daily top-10 message up '
            'to date**, pick a channel, a period and an hour.',
            '**Post the top 10 once** sends a one-off message instead.',
        ),
        notes=(
            'By default time only counts while **at least two people** share a '
            'channel, and the AFK channel is skipped — so what you measure is '
            'time spent together, not time connected. Both rules can be '
            'switched off, and channels can be excluded.',
            'The daily board **edits the same message** once a day, so you can '
            'pin it. It never posts twice in a day, and catches up if the bot '
            'was down at the chosen hour.',
            'Members are named, not mentioned. A leaderboard that pings ten '
            'people every day would be worse than useless.',
            'A normal restart loses nothing; a hard crash costs at most five '
            'minutes, and time is never rounded up.',
            'No privileged intent and no extra permission — it works the moment '
            'you switch it on.',
        ),
        page='voice',
    ),
    Topic(
        key='build-an-embed',
        title='Build a rich message',
        group='server',
        audience=ADMIN,
        where='Web — **📝 Embeds**',
        summary='Compose the server-info and rules posts you would otherwise '
                'write by hand, and edit them in place afterwards.',
        steps=(
            'Go to **📝 Embeds → New embed** and give it a name, which is only '
            'used to find it again.',
            'Fill in title, description, colour, up to ten fields, author line, '
            'thumbnail, image, footer — and the plain text above the embed, '
            'which is the only part where a mention pings.',
            'Save it as a draft and check the preview.',
            'Press **Send** and pick a channel.',
            'To change it later: edit and save. The **posted message is updated '
            'in place**, so a pinned post stays pinned.',
        ),
        notes=(
            'Discord’s limits are checked when you **save**, not when you '
            'send — an over-long title is refused on the form rather than '
            'becoming an embed that can never be posted.',
            'Sending again posts a **new** message and stops tracking the old '
            'one. That is how you move an embed to another channel.',
            'If the message was deleted in Discord, the embed quietly becomes a '
            'draft again rather than failing forever.',
            'Image and icon fields must be full `https://` URLs.',
        ),
        page='embeds',
    ),
    Topic(
        key='purge-messages',
        title='Delete messages in bulk',
        group='server',
        audience=MANAGE_MESSAGES,
        where='Discord — `/purge`',
        summary='Clear the last *N* messages in a channel, everything since a '
                'point in time, or both.',
        steps=(
            '`/purge amount:50` — the newest 50 messages.',
            '`/purge since:2h` — everything from the last two hours. `30m`, '
            '`7d` and `1w` work too, as does a date like `25/06/2025`.',
            '`/purge amount:100 since:7d` — at most 100, and nothing older than '
            'a week.',
            'A preview says what would go. Confirm to delete.',
        ),
        notes=(
            'It deletes in the channel you run it in, and only for people with '
            '**Manage Messages** there.',
            'The confirmation holds the exact messages it counted, so anything '
            'posted while you read it is left alone.',
            'Discord will not bulk-delete anything older than 14 days. Those go '
            'one at a time, roughly one a second, capped at 200 per run — the '
            'reply says how many were left, and running it again continues '
            'where it stopped.',
            'Pinned messages are deleted like any other, but the confirmation '
            'says how many are in the set.',
        ),
        commands=('/purge',),
    ),
    Topic(
        key='channels-and-timezone',
        title='Choose the channels and the timezone',
        group='server',
        audience=ADMIN,
        where='Web — **🎖️ Operations → Settings**, or `/set-timezone`',
        summary='Point the bot at the channels you actually use, and tell it '
                'what time you mean.',
        steps=(
            'Go to **🎖️ Operations → Settings**.',
            'Pick the channel for the **board**, the **approval queue** and the '
            '**archive**. *Default* means the bot uses `#orbat`, '
            '`#slot-approvals` and `#approval-archive`, creating them if they '
            'are missing.',
            'Set the **timezone** to an IANA name like `Europe/Berlin`, or run '
            '`/set-timezone`.',
        ),
        notes=(
            'The timezone is how every time you *type* is read — operations, '
            'events and `/purge` alike. Times the bot *shows* are Discord '
            'timestamps and localise themselves per viewer.',
            'The empty option is called *Default* rather than left blank on '
            'purpose: nothing chosen and "chosen, and it happens to be #orbat" '
            'behave differently the day somebody renames a channel.',
            'A chosen channel that has since been deleted is called out rather '
            'than quietly falling back.',
        ),
        commands=('/set-timezone',),
        page='opsettings',
    ),
    Topic(
        key='maintenance',
        title='Sync, restart and the one-time migration',
        group='server',
        audience=ADMIN,
        where='Discord — `/sync`, `/restart`, `/archive-old-approvals`',
        summary='The three commands that are about the bot rather than about '
                'an operation.',
        steps=(
            '`/sync` re-registers the slash commands with Discord and refreshes '
            'the board. Use it when a command looks missing.',
            '`/restart` restarts the bot. Nothing is lost — everything lives in '
            'the database and every button is re-registered on start.',
            '`/archive-old-approvals` is a **one-time** migration that moves '
            'approval messages decided before the archive existed into '
            '`#approval-archive`.',
        ),
        notes=(
            'Commands sync automatically on start and when the bot joins a '
            'server, so `/sync` is for when something looks wrong rather than '
            'routine.',
            'On a sheet-backed operation `/sync` also repairs pending requests '
            'whose rows moved because somebody inserted a row. On an ORBAT '
            'there is nothing to repair.',
            '`/archive-old-approvals` scans up to 500 messages and is not meant '
            'to be run twice.',
        ),
        commands=('/sync', '/restart', '/archive-old-approvals'),
    ),
)


_CAMPAIGNS: tuple[Topic, ...] = (
    Topic(
        key='campaign-settings',
        title='Set up the campaign planner',
        group='campaigns',
        audience=ADMIN,
        where='Web — **🗂️ Campaigns → Settings**',
        summary='Choose who may plan, where the weekly drops are posted, and '
                'when they go out.',
        steps=(
            'Go to **🗂️ Campaigns → Settings**.',
            'Pick the **Unit role**. Server admins can always plan; Unit Leaders '
            'can plan when they also hold this role.',
            'Pick the **Drop channel** — the public announcements channel every '
            'drop is posted in.',
            'Pick the **Mission team channel** — a private channel I ping when a '
            'drop is due but nobody has approved it.',
            'Set the **Drop day** and **Drop time**, and which drop **opens the '
            'ORBAT** for sign-ups.',
            'Save.',
        ),
        notes=(
            'With no unit role set, only server admins can plan. That is on '
            'purpose: the planner never opens wider than you chose.',
            'Drop times are read in the server\'s time zone, and posts show '
            'every time as a Discord timestamp, so readers see their own time.',
            'Make the drop channel an Announcement channel and partner servers '
            'can **Follow** it to get each drop in their own server: I publish '
            'every drop, which is what sends it on to them, and edits follow by '
            'themselves. Give me **Attach Files** there too, for drops with images.',
            'The survey link may contain `{op}`, which becomes the mission name '
            'with spaces as `%20`.',
            '**Who can edit mission ORBATs** decides who may open the ORBATs campaign '
            'missions use in the ORBAT editor: server admins only, or admins and '
            'planners. **Also allow these roles** adds anybody else — they find those '
            'ORBATs under **Operations → ORBATs**. Creating, copying and deleting '
            'ORBATs always stays with admins.',
        ),
        page='cpsettings',
    ),
    Topic(
        key='plan-campaign',
        title='Plan a campaign',
        group='campaigns',
        audience='Admin, or Unit Leader with the unit role',
        where='Web — **🗂️ Campaigns → Campaigns**',
        summary='Set up a campaign of three to five missions, its AOs, and the '
                'missions in the order they will be played.',
        steps=(
            'Go to **🗂️ Campaigns** and fill in **New campaign**: a name, the era '
            'and theatre, and how many missions you plan.',
            'On the campaign page, add the **areas of operations** it is fought over.',
            'Under **Plan the next mission**, give the operation a name and its op '
            'date, and press **Create mission**. The wizard opens.',
            'Back on the campaign page, ↑ and ↓ put the missions in order, and '
            '**Schedule** opens each one\'s back-schedule and drops.',
        ),
        notes=(
            'A new mission copies the details that stay the same week to week '
            '(time zone, orders time and H-Hour, AO, unit, support, radio plan, '
            'loadout and so on) from the mission before it. The story, intel, '
            'orders and AAR start empty.',
            'The next mission\'s date is suggested four weeks after the last one, '
            'the length of one cycle of drops.',
            'Creating a real campaign also sets one up in the Kill Tracker under the '
            'same name, or links the one that already exists, so stats land in one '
            'place.',
            'Set the campaign to **Archived** to fold it away on the list without '
            'deleting anything.',
            'Tick **Test campaign** to try things out. A test campaign is not added '
            'to the Kill Tracker and its drops never post publicly. **Delete test '
            'campaign** removes it with one press, and **Delete all test campaigns** '
            'on the list clears every one — with its maps, its posts and the ORBATs '
            'the planner made for it. An ORBAT you only linked stays. **Make it real** '
            'keeps a test you want after all.',
            'Deleting a real campaign asks you to type its name. Its maps go with it. '
            'Its Kill Tracker campaign goes too if the planner created it and no '
            'recordings have been added; one holding stats, or set up in the Kill '
            'Tracker by hand, is kept. Its missions\' ORBATs and its posted drops stay.',
            'A campaign closes itself: once every planned mission has been played, its '
            'AAR day has passed and its OCAP recording is linked (or a week has gone by), '
            'it is marked **Complete** and the mission team channel is told. Set it back '
            'to **Active** to keep it open; it stays open until another mission is added. '
            'Switch this off in **Campaigns → Settings**.',
        ),
        page='campaigns',
        related=('mission-wizard', 'campaign-maps', 'campaign-settings'),
    ),
    Topic(
        key='mission-wizard',
        title='Write a mission in the wizard',
        group='campaigns',
        audience='Admin, or Unit Leader with the unit role',
        where='Web — **🗂️ Campaigns →** a campaign **→** a mission',
        summary='Six steps from concept to after action, saved as you type, with '
                'each drop previewed exactly as it will post.',
        steps=(
            'Open a mission from its campaign. The wizard starts on the first step '
            'that is not locked yet.',
            'Fill in the step. Fields marked * are needed to lock it; the step\'s '
            'chip turns into 🔒 when they are all filled. Each step\'s tab shows the '
            'date it should lock by and how many days are left — red once it is overdue.',
            'Watch the panel on the right: the **drop preview** is exactly what the '
            'bot will post, and **Checks** flags mistakes as you go. Press **Preview in '
            'Discord** to see the drop formatted the way readers will see it — headings, '
            'bold, quotes, times in your own time zone and images.',
            'In **Orders**, add the phases in order. Give each its AO and press '
            '**+ Add objective** to pick from the objectives library.',
            'When a drop reads right, press **Approve** under its preview. It posts '
            'by itself at its time — see *Approve and post the drops*.',
        ),
        notes=(
            'Everything saves a moment after you stop typing — the top right says '
            'when. Leave the page while a save is pending and the browser asks first.',
            'Paste a screenshot straight into an image field, drop a file on it, or '
            'use **Upload**. PNG, JPEG, GIF or WebP, up to 8 MB.',
            'Drag rows and phases by their ⠿ handle, or use ↑ and ↓.',
            '**Held back for Zeus** in the Intelligence step is never posted or '
            'exported.',
            'A phase reads best with one primary objective, one secondary and one '
            'complication; Checks says when one is missing. With **Success looks '
            'like** left empty, the OPORD uses the primary objectives\' success '
            'conditions.',
            'Two people editing the same field at once: the last save wins. '
            'Different fields are fine.',
            'The wizard needs JavaScript. The **Schedule** page shows the mission, '
            'its drops and checks without it.',
        ),
        page='campaigns',
        related=('plan-campaign', 'campaign-drops', 'objectives-library'),
    ),
    Topic(
        key='objectives-library',
        title='Use and edit the objectives library',
        group='campaigns',
        audience='Everyone with planner access; editing is Admin only',
        where='Web — **🗂️ Campaigns → Objectives**',
        summary='The list of objectives and complications the wizard\'s picker '
                'draws from.',
        steps=(
            'Go to **🗂️ Campaigns → Objectives** to browse it by category.',
            'In a mission\'s **Orders** step, **+ Add objective** opens it as a '
            'picker. Search, then click an entry to add it to the phase.',
            'Admins: **Edit** beside an entry changes it, and **Add to the library** '
            'at the bottom adds a new one.',
        ),
        notes=(
            'Entries that pair well with what the phase already has are lifted to '
            'the top of the picker and outlined. That is a suggestion only.',
            'Picking an objective copies its wording into the mission, where it can '
            'be edited freely. Changing the library later does not change missions '
            'that already picked from it, and nor does removing an entry.',
            'The library starts as the TFP Mission Objectives Library: 57 '
            'objectives in eight categories, plus 12 complications.',
        ),
        page='cpobjectives',
        related=('mission-wizard',),
    ),
    Topic(
        key='mission-orbat',
        title="Give a mission its ORBAT",
        group='campaigns',
        audience='Admin, or Unit Leader with the unit role',
        where='Web — a mission\'s wizard or **Schedule** page → **ORBAT**',
        summary='Link the roster a mission is signed up on: last mission\'s copied, '
                'one reused as it is, or a new blank one.',
        steps=(
            'Open the mission and find the **ORBAT** card — beside the wizard, or at '
            'the top of the **Schedule** page.',
            'Under **Link one**, the list starts on the ORBAT the previous mission '
            'used. **Copy it** makes a copy named after this mission; **Use as is** '
            'links that ORBAT unchanged; **New blank** starts an empty one.',
            'Press **Edit ORBAT** to write or adjust the roster in the ORBAT editor.',
        ),
        notes=(
            'A copy takes the structure — squads, slots, radios, nets — and none of '
            'the bookings.',
            'Who may edit mission ORBATs is up to the planner\'s settings: admins '
            'only, or admins and planners (the default), plus any extra roles ticked '
            'there. It covers only ORBATs a campaign mission uses, and copying and '
            'deleting ORBATs stay with admins.',
            'An ORBAT can be edited at any time, even while sign-ups are open: the '
            'editor asks before a change would unseat anybody.',
            '**Unlink** only removes the link; the ORBAT itself stays.',
        ),
        page='campaigns',
        related=('mission-wizard', 'briefing-packs'),
    ),
    Topic(
        key='briefing-packs',
        title='Download the briefing packs',
        group='campaigns',
        audience='Admin, or Unit Leader with the unit role',
        where='Web — a mission\'s wizard or **Schedule** page → **Briefing packs**',
        summary='The mission as a PDF or Word file, in three versions for three '
                'audiences.',
        steps=(
            'Open the mission and find **Briefing packs**.',
            'Press **PDF** or **Word** beside the pack you want. It downloads straight away.',
        ),
        notes=(
            "**Player briefing** — warning order, situation, intelligence and the "
            "OPORD; safe for everyone signed up. **Leaders' pack** — that plus the "
            "FragO, timings, tasks, the ORBAT and the ops traces. **Full op file** — "
            "everything, plus the complications, the back-schedule and the after action.",
            "Each phase drawn on its AO's map goes into the leaders' pack and the full "
            'op file as an ops trace, before any traces you uploaded — see *Plan on '
            'the campaign and AO maps*.',
            'Complications appear only in the full op file, and **Held back for Zeus** '
            'is never in any pack.',
            'Times are in the op\'s time zone, with the server\'s beside them in the '
            'FragO timings when the two differ.',
            'The cover carries the site\'s name and logo. Word files open straight into '
            'Google Docs.',
            'Each download is made from what is saved at that moment, so download again '
            'after an edit.',
        ),
        page='campaigns',
        related=('mission-orbat', 'mission-wizard'),
    ),
    Topic(
        key='campaign-maps',
        title='Plan on the campaign and AO maps',
        group='campaigns',
        audience='Admin, or Unit Leader with the unit role',
        where='Web — a campaign page → **Maps and areas of operations**, or a mission\'s **Maps** card',
        summary="Draw the AOs on a greyed-out campaign map, then plan each one on a "
                "detailed map of its own with a layer per phase, drawn into the leaders' pack.",
        steps=(
            'On the campaign page press **Make the campaign map**. It opens in the map '
            'editor, greyed out like a covered Arma map.',
            'Put the map on its terrain (**Map settings**). Pick the **AO** tool, choose '
            'Rectangle, Ellipse or Freehand, and draw each area of operations: it turns '
            'clear, with a border in a colour no other AO is using. Type its name '
            'straight away — it shows in a tab of that colour at the AO\'s top-left corner.',
            'Press **Save**. Every AO you drew is now one of the campaign\'s AOs, listed '
            'on the campaign page.',
            'Back on the campaign page press **Make its map** beside an AO. Its map '
            'starts on the same terrain, greyed out everywhere but that AO, and one layer for '
            'every phase you gave that AO in a mission\'s Orders step.',
            'Draw each phase on its own layer — the layer picker above the map — '
            'and save.',
            'Download the **Leaders\' pack**: each phase drawn on its layer is in it as '
            'an ops trace, cropped to the phase and the AO.',
        ),
        notes=(
            'The map and the planner keep each other up to date. Rename an AO on the '
            'map and it is renamed in the planner; delete its outline and the AO goes, '
            'unless phases are fought in it or its own map has drawings, when it is kept '
            'and its outline put back. An AO added on the campaign page appears on the '
            'map as a clear box to move into place.',
            'Overlapping AOs merge into one clear area, and stay easy to tell apart: '
            'each has its own border colour and its name in its own corner. The campaign '
            'page shows a key of every AO and its colour beside the map. Change an AO\'s '
            'colour with the swatches when it is selected. Its Arma marker uses that '
            'colour too, and its own map takes it when the map is made. **Map settings** switches the grey off or '
            'changes its strength.',
            'The layers keep up with the planner: a new AO gets an outline, a renamed '
            'phase or mission renames its layer, and a phase moved to another AO or '
            'deleted loses its layer — unless something is drawn on it, which is kept.',
            'A trace shows its own phase plus every layer that belongs to no phase, '
            'such as Plan or an enemy layer you add, so draw shared things there.',
            'A map holds 12 layers, so an AO with more phases than fit says so; '
            'delete a layer you no longer need in the editor.',
            'If you have the map open in the editor while you change phases in the '
            'wizard, your next save there puts the old layer names back. Opening the '
            'map from the planner again fixes them.',
            'Removing an AO removes its map. Deleting a campaign deletes its maps.',
        ),
        page='campaigns',
        related=('plan-campaign', 'briefing-packs', 'mission-wizard'),
    ),
    Topic(
        key='aar-recording',
        title='Link the OCAP recording and use its numbers',
        group='campaigns',
        audience='Admin, or Unit Leader with the unit role',
        where='Web — a mission\'s wizard or **Schedule** page → **OCAP recording**',
        summary="Which OCAP recording is the mission, so its stats land in the campaign's "
                'Kill Tracker campaign and its numbers in the AAR.',
        steps=(
            'Play the op. The Kill Tracker reads the recording from OCAP by itself within '
            'about ten minutes.',
            'Open the mission. When there is exactly one recording on the op date that is '
            'at least half an hour long, the **OCAP recording** card has already linked it '
            'and put it in the campaign\'s Kill Tracker campaign.',
            'If there are several, or the op ran past midnight, open **Choose one** and '
            '**Link** the right one.',
            'Press **Use in the AAR stats** to put its numbers — players, length, enemy '
            'killed, vehicles, deaths, teamkills, longest kill, most kills — into the AAR\'s '
            'Stats field, then edit them as you like.',
        ),
        notes=(
            'The bot only links a recording by itself in the week after the op, and never '
            'one somebody changed or said **None of these** to. **Let the bot choose** '
            'hands the choice back.',
            'Unlinking, linking another, or deleting the mission takes the recording back out '
            'of the Kill Tracker campaign — but only if the planner put it there. One an '
            'admin added by hand stays.',
            'A test campaign links recordings so you can try the numbers, but adds nothing to '
            'the Kill Tracker. **Make it real** adds them then.',
            'Zeus is not counted as a player, as on the dashboard.',
        ),
        page='campaigns',
        related=('mission-wizard', 'plan-campaign'),
    ),
    Topic(
        key='campaign-drops',
        title='Approve and post the drops',
        group='campaigns',
        audience='Admin, or Unit Leader with the unit role',
        where='Web — a mission\'s **Schedule** page → **Drops**, or the drop preview in the wizard',
        summary='Each drop posts by itself at its time once somebody has approved it.',
        steps=(
            'Open the mission\'s **Schedule** page and open a drop, or look at the preview '
            'beside a step in the wizard.',
            'Read it, then press **Approve**. If its time has already come it posts at '
            'once; otherwise it shows **Approved** and posts on the day.',
            'Changed your mind before it posts? **Withdraw approval**. A drop you will '
            'not use at all, or posted by hand, gets **Don\'t post**.',
        ),
        notes=(
            'An approval is for exactly the words and images you read, at that time. '
            'Edit the drop afterwards, or move the op date, and it says **Needs '
            're-approval** and will not post until somebody approves it again.',
            'A drop whose time comes without an approval is **Held**: the mission team '
            'channel is pinged once, and it posts the minute it is approved.',
            'Approving a change to a drop that already posted edits the posted '
            'message in place, Part 1/2 and images included.',
            'When the drop that opens the ORBAT posts, sign-ups start by themselves on '
            'the mission\'s ORBAT, with H-Hour as the start time. They never replace an '
            'operation that is still to be played: then the team channel is told, and '
            '**Start sign-ups now** on the Schedule page does it once that one is over.',
            'A test campaign\'s drops go to the mission team channel only, never start '
            'sign-ups, and are deleted from Discord with the campaign. A real '
            'campaign\'s posted drops stay in Discord when it is deleted.',
            'Set the drop channel and the mission team channel in **Campaigns → '
            'Settings** — nothing posts without them.',
        ),
        page='campaigns',
        related=('mission-wizard', 'campaign-settings', 'mission-orbat'),
    ),
)


TOPICS: tuple[Topic, ...] = (
    _SLOTS + _ORBATS + _EVENTS + _ROLES + _MAPS + _CAMPAIGNS + _REDDIT + _KILLTRACKER + _SERVER
)


_BY_KEY = {topic.key: topic for topic in TOPICS}
_BY_PAGE: dict[str, list[Topic]] = {}
for _topic in TOPICS:
    if _topic.page:
        _BY_PAGE.setdefault(_topic.page, []).append(_topic)


def topic(key: str) -> Topic | None:
    """One how-to, or None — a URL is a thing somebody can mistype."""
    return _BY_KEY.get(key)


def catalog() -> list[dict]:
    """Every group with its topics, in order, for the wiki's front page."""
    return [
        {
            'group': group,
            'topics': [item for item in TOPICS if item.group == group.key],
        }
        for group in GROUPS
    ]


def group(key: str) -> Group | None:
    for item in GROUPS:
        if item.key == key:
            return item
    return None


def for_page(page: str) -> list[Topic]:
    """The how-tos belonging to one web page, so it can carry a `?`.

    The page is named by the key `web/nav.py` uses, which is what stops the
    link and the tab it sits on from drifting apart.
    """
    return list(_BY_PAGE.get(page, ()))


def related(item: Topic) -> list[Topic]:
    """`item.related`, resolved and with anything unknown dropped.

    Dropping rather than raising is deliberate: a dangling key is a broken
    link on one page, and `tests/test_help.py` is where it is supposed to be
    caught, not in front of somebody trying to read the help.
    """
    return [_BY_KEY[key] for key in item.related if key in _BY_KEY]


def commands() -> set[str]:
    """Every slash command any topic covers, named the way Discord names it.

    The topics write them with the leading slash because that is how they are
    typed and how they read in a sentence; Discord registers them without one.
    Stripping it here is what lets `tests/test_help.py` compare this against
    the names it reads out of `cogs/` directly.
    """
    return {name.lstrip('/') for item in TOPICS for name in item.commands}


def search(query: str) -> list[Topic]:
    """Topics matching every word of `query`, title first.

    Plain substring matching over the whole topic. There is no index and no
    ranking beyond "a hit in the title beats a hit in the body", because forty
    topics do not need one.
    """
    words = [word for word in re.split(r'\s+', query.strip().lower()) if word]
    if not words:
        return []

    hits = []
    for item in TOPICS:
        title = item.title.lower()
        body = ' '.join((
            item.title, item.summary, item.where, item.audience,
            ' '.join(item.steps), ' '.join(item.notes),
            ' '.join(item.commands),
        )).lower()
        if all(word in body for word in words):
            hits.append((0 if any(word in title for word in words) else 1, item))
    return [item for _, item in sorted(hits, key=lambda pair: pair[0])]


# --- The README half ------------------------------------------------------
#
# `scripts/gen_help.py` writes what this returns into the README between its
# markers. The point is not that Markdown is nicer than prose — it is that
# there is one copy of these forty how-tos, so the site and the README cannot
# come to say different things about the same feature.

README_START = '<!-- help:start -->'
README_END = '<!-- help:end -->'


def _anchor(item: Topic) -> str:
    """The GitHub heading anchor for a topic, for the table of contents."""
    slug = re.sub(r'[^a-z0-9 -]', '', item.title.lower())
    return slug.replace(' ', '-')


def render_markdown(*, web_path: str = '/help') -> str:
    """Every topic as a Markdown section, between the README markers."""
    out = [
        README_START,
        '',
        '<!-- Generated from utils/help.py by scripts/gen_help.py.',
        '     Edit the topics there, not this section: the bot serves the same',
        '     text at ' + web_path + ' and the two must not drift apart. -->',
        '',
        'Short how-tos for everything the bot does, one per thing you might '
        'want to do. The bot serves the same text at **`' + web_path + '`** — '
        'the **?** in the site header, and next to each page’s heading.',
        '',
    ]

    for shelf in catalog():
        links = ' · '.join(
            f"[{item.title}](#{_anchor(item)})" for item in shelf['topics'])
        out.append(f"**{shelf['group'].title}** — {links}")
        out.append('')

    for shelf in catalog():
        out.append(f"### {shelf['group'].title}")
        out.append('')
        out.append(f"_{shelf['group'].blurb}_")
        out.append('')
        for item in shelf['topics']:
            out.append(f"#### {item.title}")
            out.append('')
            meta = [f"**Who:** {item.audience}"]
            if item.where:
                meta.append(f"**Where:** {item.where}")
            out.append(' · '.join(meta))
            out.append('')
            out.append(item.summary)
            out.append('')
            for number, step in enumerate(item.steps, 1):
                out.append(f"{number}. {step}")
            out.append('')
            for note in item.notes:
                out.append(f"> {note}")
                out.append('>')
            if item.notes:
                out.pop()  # the trailing blockquote spacer
                out.append('')
            links = related(item)
            if links:
                out.append('See also: ' + ' · '.join(
                    f"[{link.title}](#{_anchor(link)})" for link in links))
                out.append('')

    out.append(README_END)
    return '\n'.join(out).rstrip() + '\n'


def splice_readme(readme: str, *, web_path: str = '/help') -> str:
    """`readme` with the generated section replaced.

    Raises `ValueError` when the markers are missing or the wrong way round,
    because writing the section into the wrong place is worse than not writing
    it — a silent append would bury half the README inside a code fence.
    """
    start = readme.find(README_START)
    end = readme.find(README_END)
    if start < 0 or end < 0:
        raise ValueError(
            f"README is missing the {README_START} / {README_END} markers")
    if end < start:
        raise ValueError(f"{README_END} comes before {README_START}")
    return (readme[:start]
            + render_markdown(web_path=web_path).rstrip('\n')
            + readme[end + len(README_END):])


# --- The web half ---------------------------------------------------------

_MD_CODE = re.compile(r'`([^`]+)`')
_MD_BOLD = re.compile(r'\*\*([^*]+)\*\*')
_MD_ITALIC = re.compile(r'(?<![*\w])\*([^*\n]+)\*(?!\*)')


def inline_html(text: str) -> str:
    """The little Markdown a topic uses, as HTML — `code`, **bold**, *italic*.

    It **escapes first and marks up second**, which is the whole reason this is
    a function rather than a Jinja `|safe`. The topics are written here rather
    than typed by anybody, so this is not a sanitiser guarding against an
    attacker — but a `<` in a future how-to about `<Insert Name>` markers
    should render as a `<`, not silently eat the rest of the sentence, and the
    page is marked safe on the way out either way.

    Code spans are taken before the emphasis patterns so that a literal
    asterisk inside backticks survives.
    """
    escaped = (text.replace('&', '&amp;').replace('<', '&lt;')
                   .replace('>', '&gt;').replace('"', '&quot;'))

    spans: list[str] = []

    def stash(match: re.Match) -> str:
        spans.append(match.group(1))
        return f'\x00{len(spans) - 1}\x00'

    out = _MD_CODE.sub(stash, escaped)
    out = _MD_BOLD.sub(r'<strong>\1</strong>', out)
    out = _MD_ITALIC.sub(r'<em>\1</em>', out)
    return re.sub(r'\x00(\d+)\x00',
                  lambda m: f'<code>{spans[int(m.group(1))]}</code>', out)
