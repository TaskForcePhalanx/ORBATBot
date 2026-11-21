# tfp-announcer — Reddit → Discord, as a Devvit app

Reddit no longer lets the bot read its feeds from the Portainer server's address
and no longer accepts new public API access. This app runs **inside** Reddit
instead: when a post is submitted in a subreddit it is installed on, it sends the
title and link to a Discord webhook. Read-only — it never posts, comments, votes
or messages.

`discord.com` is on Devvit's global fetch allowlist
(https://developers.reddit.com/docs/capabilities/server/http-fetch), and the app
still lists it under `permissions.http.domains`, as the docs ask.

## Set up (needs a moderator of the subreddit)

1. Create a Discord webhook (channel settings → Integrations → Webhooks).
2. `cd devvit && npm install && npx devvit login`
3. `npm run upload` (builds, then uploads; the first upload creates the app).
4. `npx devvit settings set discordWebhookUrl` and paste the webhook URL.
   The CLI notes that at least one installation must exist before a secret can
   be stored, so install first (step 5), then set it.
5. Install the app on r/FindAUnit from its page on developers.reddit.com
   (moderators only). On install, the subreddit setting **Only announce posts by
   these users** can be set to `TaskForcePhalanx`.
6. Fetch apps must provide Terms and Conditions and a Privacy Policy links in the
   app details form on developers.reddit.com.

The message matches the bot's own: `New post by u/<author> in r/<subreddit>`,
the title and the link. It never mentions anyone (`allowed_mentions: none`).

Not verified against a real subreddit: this was type-checked and bundled, not
run. First post after install is the test.

When this works, turn off the Reddit watch in the bot (it would announce the same
post twice).
