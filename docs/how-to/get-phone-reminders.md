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

You need a Cloudflare account, Node, and pnpm. First copy the Worker's source out
of your `kb` install:

```bash
kb remind push init       # copies it to ~/.local/share/pkb-cli/worker (or pass a directory)
cd ~/.local/share/pkb-cli/worker
```

Then deploy it:

```bash
pnpm install
pnpm wrangler login
pnpm wrangler d1 create kb-reminders     # add the printed database_id to wrangler.jsonc
pnpm wrangler d1 migrations apply kb-reminders --remote
pnpm deploy
```

Then generate and store its four secrets. `gen:keys` prints them and writes
nothing to disk; keep the two tokens in your password manager.

```bash
pnpm gen:keys
pnpm wrangler secret put VAPID_PUBLIC_KEY      # paste each value when asked
pnpm wrangler secret put VAPID_PRIVATE_KEY
pnpm wrangler secret put SYNC_TOKEN            # lets `kb` upload reminders
pnpm wrangler secret put SUBSCRIBE_TOKEN       # lets your phone subscribe, nothing else
```

Deploy only needs repeating when you change the Worker. Rotate a token by
generating a new one and running `secret put` again.

## Connect kb and your phone

```bash
kb remind push setup https://kb-push.<your-subdomain>.workers.dev   # asks for SYNC_TOKEN
```

On your Android phone, open the same URL in Chrome, enter the `SUBSCRIBE_TOKEN`,
tap **Enable notifications**, and allow the permission. Adding the page to your
home screen (Chrome menu, Install app) is optional.

```bash
kb remind push status    # worker reachable, token accepted, phone subscribed
kb remind test           # desktop test, then a test push to your phone
```

## Upgrading

The Worker's source is part of each `kb` release, but nothing deploys it for you: it
runs in your Cloudflare account, so putting a new version live is a step you take.

```bash
kb update                 # also refreshes your copy of the Worker, if you ran `kb remind push init`
kb remind push init       # or refresh it by hand
cd ~/.local/share/pkb-cli/worker
pnpm install && pnpm deploy
```

`init` overwrites the source but keeps your `node_modules` and the `database_id` in
`wrangler.jsonc`, and your secrets stay on Cloudflare, so there is nothing to
re-enter. It says when the release added a database migration; if so, run
`pnpm wrangler d1 migrations apply kb-reminders --remote` before `pnpm deploy`. Redeploy
only when `init` reports a change from your previous kb version: the API between `kb`
and the Worker is just `/api/sync`, `/api/status` and `/api/test`, so most releases
won't touch it.

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
