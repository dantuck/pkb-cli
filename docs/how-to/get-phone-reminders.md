# How to get TODO reminders on your phone

`kb remind` notifies your desktop when a TODO comes due, and only while the
machine is awake. This adds a push notification to your phone that arrives
either way, using a small Cloudflare Worker you deploy to your own account
(its source ships inside `kb`).

## How it works

- Every `kb remind check` run (every 5 minutes) uploads each unclosed TODO's
  **id, due time and priority** to the Worker. Titles and descriptions never
  leave your machine.
- A cron trigger on the Worker runs every minute and sends a
  [Web Push](https://developer.mozilla.org/docs/Web/API/Push_API) to your phone
  when something is due: `kb: 2 TODOs due` / `kb-qjp, kb-e3s`. An overdue TODO is
  sent again every 4 hours until you close or snooze it.
- Web Push is end-to-end encrypted to your phone, so Google's push service relays
  it without being able to read it. Look the ids up with `kb remind`.
- Your desktop still gets its own notification too.

Why it's built this way, and what it trades away: [explanation: phone
reminders](../explanation/design.md#phone-reminders). Flags and config keys:
[reference: CLI](../reference/cli.md).

## Deploy the Worker

You need a Cloudflare account and Node with pnpm (or npm). One command does it all:

```bash
kb remind push deploy
```

It copies the Worker's source to `~/.local/share/pkb-cli/worker`, installs its
dependencies, logs you in to Cloudflare if needed, creates the `kb-reminders` D1
database and applies its migrations, generates the Worker's keys and tokens, and
deploys it. It then saves the Worker's URL and the sync token in kb's config and prints
the **subscribe token** once, because your phone needs it and kb doesn't keep it. Save
it in your password manager.

(`kb update` also offers to run this, once, if you haven't set it up; say no and it won't
ask again.)

## Connect your phone

On your Android phone, open the Worker's URL in Chrome, enter the subscribe token, tap
**Enable notifications**, and allow the permission. Adding the page to your home screen
(Chrome menu, Install app) is optional.

```bash
kb remind push status    # worker reachable, token accepted, phone subscribed
kb remind test           # desktop test, then a test push to your phone
```

## Upgrading

The Worker's source is part of each `kb` release. When you run `kb update` in a
terminal and kb is already pointed at a Worker, it compares the new source with what
you last deployed and, if it changed, redeploys it, so there is nothing else to do. A
release that doesn't touch the Worker skips it, so no login or Node is needed. To do it
by hand, or when `kb update` ran somewhere without a terminal (the web UI's update
button, a script), run:

```bash
kb remind push deploy
```

Your secrets and database stay on Cloudflare and the database id is kept in the copied
Worker, so nothing is re-entered, and migrations a release adds are applied before the
deploy.

## Deploy by hand

Prefer to run wrangler yourself, or to use a different Cloudflare account setup?

```bash
kb remind push init       # copies the source to ~/.local/share/pkb-cli/worker (or pass a directory)
cd ~/.local/share/pkb-cli/worker
pnpm install
pnpm wrangler login
pnpm wrangler d1 create kb-reminders     # add the printed database_id to wrangler.jsonc
pnpm wrangler d1 migrations apply kb-reminders --remote
pnpm deploy
pnpm gen:keys            # prints the four secrets; nothing is written to disk
pnpm wrangler secret put VAPID_PUBLIC_KEY      # paste each value when asked
pnpm wrangler secret put VAPID_PRIVATE_KEY
pnpm wrangler secret put SYNC_TOKEN            # lets `kb` upload reminders
pnpm wrangler secret put SUBSCRIBE_TOKEN       # lets your phone subscribe, nothing else
kb remind push setup https://kb-push.<your-subdomain>.workers.dev   # asks for SYNC_TOKEN
```

Rotate a token by generating a new one and running `secret put` again. After a `kb update`,
`kb remind push init` refreshes the copy (keeping `node_modules`, the database id and any files of your own, and
removing files a release dropped); then
`pnpm install`, `pnpm wrangler d1 migrations apply kb-reminders --remote` and `pnpm deploy`
as above.

## Everyday use

Nothing changes: `kb todo -a "..." --due friday` as before, and the next
`kb remind check` carries it to the Worker. `kb remind push sync` uploads
immediately, for a TODO that's due within the next five minutes.

`kb remind push off` stops uploading. Remove the phone's subscription from the
page (**Turn off**), and delete everything with `pnpm wrangler delete`.

## What the Worker can and can't do

- `/api/sync`, `/api/status` and `/api/test` need `SYNC_TOKEN`; `/api/subscribe`
  needs `SUBSCRIBE_TOKEN`. A leaked phone token can add a subscription (up to 20)
  but can't read or change reminders.
- Subscriptions are accepted only from the Google, Mozilla, Apple and Windows push
  services, so a token can't be used to make the Worker call arbitrary URLs.
- Closing a TODO on your machine reaches the Worker on the next check; one that was
  already due in that window can still produce one last push.
- Phone alerts are one-way. Close or snooze from your computer (`kb todo snooze`).
