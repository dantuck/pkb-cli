# How to get TODO reminders on your phone

`kb remind` notifies your desktop when a TODO comes due, and only while the
machine is awake. This adds a push notification to your phone that arrives
either way, using a small Cloudflare Worker you deploy to your own account
(its source ships inside `kb`).

## How it works

- Every `kb remind check` run (every 5 minutes) uploads each unclosed TODO that has a
  due date: its **id, title, due time and priority**. Descriptions and comments never
  leave your machine.
- A cron trigger on the Worker runs every minute and sends a
  [Web Push](https://developer.mozilla.org/docs/Web/API/Push_API) to your phone
  when something is due: `kb: 2 TODOs due`, with the first few titles in the body. An overdue TODO is sent again every 4 hours until you
  close or snooze it.
- Web Push is end-to-end encrypted to your phone, so Google's push service relays
  it without being able to read it. Titles do show on your lock screen, though.
- The same page lists your TODOs by title, with **Close** and **Snooze** buttons. Those
  go to the Worker, and the next `kb remind check` on your computer applies them.
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
the **phone token** once, because your phone needs it and kb doesn't keep it. Save
it in your password manager.

(`kb update` also offers to run this, once, if you haven't set it up; say no and it won't
ask again.)

## Connect your phone

On your Android phone, open the Worker's URL in Chrome, enter the phone token, tap
**Enable notifications**, and allow the permission. The page remembers the token on that
phone (**Forget token** clears it). Adding the page to your home screen (Chrome menu,
Install app) is optional.

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

Rotate a token by generating a new one and running `secret put` again (for the phone token that is `SUBSCRIBE_TOKEN`). After a `kb update`,
`kb remind push init` refreshes the copy (keeping `node_modules`, the database id and any files of your own, and
removing files a release dropped); then
`pnpm install`, `pnpm wrangler d1 migrations apply kb-reminders --remote` and `pnpm deploy`
as above.

## Close and snooze from your phone

Open the Worker's page: each TODO that has a due date is listed with its title, priority
and when it's due. **Close** closes it; **Snooze 1h / 3h / 1d / 1w** hides it until then
and reminds you again. Nothing is instant: the request waits on the Worker until
`kb remind check` next runs on your computer (every five minutes, and only while it's on
and awake), applies it with `bd`, and uploads the new state. To do it right away, run
`kb remind push sync`. The page and the push reminders reflect the request immediately.

A request that can't be applied (say the TODO was already closed) prints a warning from
`kb remind check` and is dropped; the next upload shows the phone how things really stand.

## Manage your phones

From your computer, not from the phone:

```bash
kb remind push devices          # id, label ("Chrome on Pixel 8"), push service, when added
kb remind push devices rm 3     # stop alerts to that one
```

The phone token can't list or remove devices. If a phone is lost, remove it here; to be
sure nobody holding its token can read your TODO titles, rotate it (see below).

## Everyday use

Nothing changes: `kb todo -a "..." --due friday` as before, and the next
`kb remind check` carries it to the Worker. `kb remind push sync` uploads
immediately, for a TODO that's due within the next five minutes.

`kb remind push off` stops uploading. Remove the phone's subscription from the
page (**Turn off**), and delete everything with `pnpm wrangler delete`.

## What the Worker can and can't do

- `/api/sync`, `/api/status`, `/api/test`, `/api/actions` and `/api/devices` need
  `SYNC_TOKEN` (kb); `/api/subscribe`, `/api/todos` and `/api/action` need
  `SUBSCRIBE_TOKEN` (the phone token). A leaked phone token can add a subscription (up to
  20), read your TODO titles and due times, and ask to close or snooze a TODO, which
  your computer then does. It can't replace the reminders or list or remove devices.
- Subscriptions are accepted only from the Google, Mozilla, Apple and Windows push
  services, so a token can't be used to make the Worker call arbitrary URLs.
- Closing a TODO on your machine reaches the Worker on the next check; one that was
  already due in that window can still produce one last push.
- Your computer stays the source of truth. The Worker keeps titles only for TODOs with a
  due date, and only so the page can show them; it never has descriptions or comments.
