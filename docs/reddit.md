<!-- Part of ORBATBot's documentation. The rules every session needs
     are in ../CLAUDE.md, which says when to read this file. -->

## Reddit announcements (`utils/reddit.py` + `cogs/redditfeed.py` + `web/reddit.py`)

A watch on a Reddit user or a subreddit that announces every new post in a
channel, with the guild's own wording and whoever it wants pinged. Admin-only,
one tab on the guild page, and no slash command — the text and the ping list are
a form, not something to type into a modal.

### It announces. It does not ask for votes.

A Discord message asking people to go and upvote a post is **vote manipulation**
under Reddit's content policy, and it is one of the few things Reddit acts on
hard: not only the posting account but the accounts that reliably answer the
call, because the same group voting minutes after the same author posts is
exactly what the voting timeline shows. Nothing in the wording avoids that, so
there is no vote wording anywhere here: not in `DEFAULT_TEMPLATE`, not in
`TEMPLATE_EXAMPLES`, and the help text under the field says so. Asking people to
read and comment is fine, and weighs more in Reddit's own ranking anyway.

The post's score is deliberately never read, rendered or referred to — a feature
that displays it is one step from a feature that asks people to change it.

### No API registration

`utils/reddit.py` reads the public Atom feed — `/user/<name>/submitted.rss` or
`/r/<name>/new.rss` — which is the same page a browser gets, not the OAuth API
surface. So there is no client id, no secret and nothing to register. It does
want `REDDIT_USER_AGENT`: Reddit rate-limits a client that doesn't identify
itself down to nothing.

### Being refused is about us, not about the feed

Identifying ourselves is not always enough, and this is the thing to know before
changing any of it. **Reddit also refuses the address the request comes from.**
A hosting provider's IP — Railway's included — is turned away from the public
feeds with 429 however politely it asks and however rarely it asks, so this is
not a frequency that can be tuned down into working. One watch polled every five
minutes is 288 requests a day, which is nothing; the refusal is not about that.

Two things follow, and both are load-bearing:

- **`fetch()` tries both hosts.** `www.reddit.com` carries the bot detection;
  `old.reddit.com` is the legacy renderer and is markedly less fussy about who
  is asking. The second one is only ever asked on a check that has already been
  refused, so the normal case is still one request. A 404 or a 403 is *not*
  retried there — those are about the feed and say the same thing from either
  host.
- **A refusal stands the watch down.** `RateLimited` carries a wait (Reddit's
  own `Retry-After` where it gives one, bounded, otherwise
  `DEFAULT_RETRY_AFTER`), `check_feed()` writes it to `retry_at`, and
  `get_due_reddit_feeds()` skips the row until it passes. Asking again on the
  next tick is what turns a passing throttle into a standing one. **Check now**
  ignores the wait, because a person pressing a button may try; the loop may
  not.

If a watch is refused from both hosts every time, the feed is not the way in
from that host and the OAuth API (a script app, `oauth.reddit.com`) is — which
is a different fetcher, not a different frequency.

`utils/reddit.py` imports **nothing but the standard library** — `aiohttp` is
imported inside `fetch()`, the same trick `utils/sheets.py` plays with its
credentials — which is what makes the parsing and the templating testable
(`tests/test_reddit.py`).

### What comes back is not always a feed

`fetch()` hands `parse_feed()` the **raw bytes**. An XML document declares its
own encoding and that declaration is the authority — not the HTTP header, and
not a guess made from the bytes. Decoding first (`response.text()`) put a third
party in charge of it, which is how one accented character in a post title turns
into `not well-formed (invalid token): line 20, column 195`.

Three kinds of not-a-feed, told apart because they need different answers:

- **An HTML body is a refusal in disguise.** Reddit serves its block page with a
  200, so `parse_feed()` sniffs for one and raises `RateLimited` — the same
  thing a 429 means, handled the same way. (It is worth knowing that such a page
  fails XML parsing as *mismatched tag*, not *invalid token*, so the two
  symptoms are genuinely different problems.)
- **Characters XML forbids are dropped.** No valid feed can contain a control
  character, so removing them cannot change a well-formed document — and one
  stray byte in a title must not cost the whole feed.
- **Anything else raises `NotAFeed`**, which quotes the fragment it choked on as
  `repr()`. The line and column mean nothing to whoever reads the flash message;
  the characters themselves separate a stray `&`, an invisible byte and a
  mis-encoded one at a glance. `NotAFeed` is also retried on the other host,
  because a body that isn't the feed is usually about who is asking — but it
  does not stand the watch down, since it is not a refusal.

Two details in the parsing are load-bearing:

- **`published` beats `updated`.** Editing a post moves `updated` and leaves
  `published` alone; reading `updated` would make an edit look like a new post.
- **An entry with no id or no link is skipped, not fatal.** One malformed entry
  must not cost the rest of the feed.
- **The category's `term` beats its `label`.** Reddit writes them as
  `term="arma" label="r/arma"`, and for a post made on the author's own profile
  as `term="u_Name" label="u/Name"`. The term is already what belongs after an
  `r/` in both cases — a profile post really does live in `r/u_Name` — while the
  label would render as `r/u/Name`, which is not a place. A user watch carries
  both kinds, since `/user/<name>/submitted.rss` is everything that author
  submits anywhere.

`render()` substitutes `{title}`, `{url}`, `{author}` and `{subreddit}` with one
literal replace each rather than `str.format()`: a template is text somebody
typed, so a stray `{` in it has to be harmless.

### Following an author through a subreddit

A `user` watch reads that account's **profile listing**, and Reddit lets an
account hide its posts from its own profile (Settings → *Curate your profile* →
*Content and activities*). The posts stay where they were written — a subreddit
lists them whatever the profile says — so the way in is to watch the subreddit
and keep only what that account wrote. `author_filter` is that filter, and
`reddit.by_authors()` applies it.

It is applied in `check_feed()` **before anything else looks at the posts**, so
`seen_ids` only ever holds posts this watch would announce and `last_post_at` is
the newest *matching* one. The remembered window is therefore effectively much
longer than `MAX_SEEN` on a busy subreddit, since the other authors' posts never
enter it.

Two things it cannot do anything about:

- **A busy subreddit can push a post out of its own feed between two checks.**
  `new.rss` carries about 25 posts; a subreddit that gets more than that in five
  minutes can lose one. The form says so rather than pretending otherwise.
- **Widening the filter makes old posts new.** Nothing is reset when it changes
  — narrowing it should lose nothing — so `save()` warns and points at *Recent
  posts → Mark all* instead, which is the same answer as any other backlog.

`remember_posts()` deliberately holds the feed **unnarrowed**: announcing one by
hand is an explicit choice and should be able to reach a post the filter skips.

### The first read announces nothing

`seen_ids` is NULL until a watch has been read once, and that is the whole
difference between the first read and every later one: the first records what is
already on the feed and posts nothing. Without it, switching a watch on would
dump the author's last 25 posts into the channel.

It is a rolling set of post ids rather than a high-water timestamp for the same
reason `published` is preferred above — a timestamp moves when a post is edited,
an id doesn't.

`MAX_PER_CHECK` caps one check at three announcements. Nothing is dropped: the
ids of the posts that did go out are the only ones marked seen, so the rest
follow on the next check.

### `check_feed()` is the whole implementation

The polling loop calls it and so does the **Check now** button, exactly as the
approval buttons and the web queue share `approve_slot_request()`. It takes a
feed row and the bot, and it knows nothing about interactions or forms; both
`reddit.FeedError` and `ValueError` carry a message meant for a person, which
the loop prints and the web page flashes.

Its two send-failure paths differ on purpose:

- **`Forbidden`** leaves the post unannounced. A permission problem is fixable,
  and the post goes out on the next check once it is.
- **Any other `HTTPException`** marks the post announced anyway. Retrying a
  message Discord refuses, every five minutes for ever, would wedge the watch
  behind one bad post.

**`allowed_mentions` names exactly the roles and people the watch stores.** The
post title goes into the message verbatim, so a title containing `@everyone`
would otherwise ping the whole server — the announcement is quoting Reddit, not
speaking for the admin who set the watch up.

### The page

`/g/{guild}/reddit` lists the watches; the form adds and edits one. Three things
about it are worth knowing:

- **People are a free-text box of user ids**, for the same reason the assign form
  on the slot queue takes one: `Intents.default()` cannot list a guild's members,
  so there is no dropdown to offer. Roles are checkboxes, as everywhere else.
- **Preview posts nothing and marks nothing as seen**, and renders what is
  *in the form* rather than what is stored — it is meant to be pressed while
  working on the text. **Check now** is the opposite: it is the scheduled check,
  run early, and it does announce.
- **Pointing a watch at a different source resets `seen_ids`**
  (`reset_reddit_feed_seen()`), so the new feed is seeded on its next read
  instead of announcing its history. The page says so.

### Catching a post up by hand

`/g/{guild}/reddit/{id}/posts` lists what the feed still carries, says which of
them have been announced, and gives each an **Announce** button —
`announce_post()`, which is the second thing besides `check_feed()` that sends
one of these messages. It exists because there are three ways a post ends up
never reaching the channel and none of them heals itself: the bot was down when
it went up, Discord refused that one message (which `check_feed()` marks as
announced *on purpose*, so one bad post can't wedge the watch for ever), or the
watch was pointed somewhere and seeded past it.

The page says **which address it read and how many entries came back**, and
names where each post lives — `r/arma`, or *the author's own profile* for one in
`u_Name`. Both exist because "the bot only found one post" and "that address
only carries one post" are different problems, and this is the only place the
difference can be seen. A `u_Name` post rendered as `r/u_Name` reads like a bug
next to `r/arma`, which is what hid the question in the first place.

**The same page marks posts as announced without posting them** —
tick the ones to skip, or **Mark all**. That is the way past a flood: a watch
whose feed suddenly fills up (an account that un-hid its posts, a subreddit that
woke up) would otherwise work through the backlog `MAX_PER_CHECK` at a time
until all of it had been announced. On a watch that has **never been read** it
marks everything the page listed whatever was ticked, because its first check
would have done exactly that — marking only the ticked posts would leave the
rest queued, which is the flood being prevented.

**`mark_announced()` reads nothing.** The ids come from the page, which has just
read the feed; going back to Reddit to write down a decision somebody has
already made fails at the one moment it matters — when Reddit is refusing us,
which is when a backlog most needs marking. It writes through
`set_reddit_feed_seen()` rather than `record_reddit_read()` for the same reason:
marking is bookkeeping, not a read, so it must not clear `last_error` or the
stand-down, which say something about Reddit rather than about what has been
announced.

**The rest of the page reuses that read too.** `remember_posts()` holds what was
last read for `RECENT_TTL`, and `announce_post()` takes it from there rather
than asking again — pressing a button on the page is the second half of one
interaction. `check_feed()` deliberately does not use it: the scheduled check
must see the feed as it is now.

The ticking form holds no rows of its own. The checkboxes point at it with the
`form` attribute, which is what lets each row keep its own **Announce** form
without nesting one form inside another.

Two things about it are load-bearing:

- **It marks the post seen**, so the next scheduled check doesn't announce it
  again — and on a watch that has **never been read** it seeds the rest of the
  feed at the same time, exactly as a first read would. Without that, catching
  one post up by hand would make the next check announce the other twenty-four.
- **A send that fails records nothing.** The point of the button is to get the
  post out; if it didn't go out, nothing should be stored as though it had.

It is its own page rather than a panel on the watch form, because opening it
reads the feed — and the form has to open without touching Reddit, not least
when Reddit is refusing us.

---
