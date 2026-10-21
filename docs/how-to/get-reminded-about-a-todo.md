# How to get reminded about a TODO

Use this for TODOs that aren't code — "review the vendor email", "call the
dentist" — where the point is to be nudged at the right time.

## Add a TODO with a reminder time

```bash
kb todo -a "review the vendor email" --due "tomorrow 9am"
kb todo -a "stand up and stretch" --due 45m
```

`--due` takes `30m`, `2h`, `3d`, `1w`, `tomorrow`, `friday`, `5pm`,
`tomorrow 9am`, or a date like `2026-11-02`. In the web UI, use the "remind
me…" field under the title. Change it later with `kb todo due <id> <when>`,
or clear it with `kb todo due <id> none`.

## Turn on notifications

```bash
kb remind install        # checks every 5 minutes
kb remind status
```

When a TODO comes due you get a desktop notification with its title. An
overdue TODO nudges again every 4 hours until you close or snooze it.
Change the check frequency with `--interval-minutes`.

### macOS: allow the notifications

With [`terminal-notifier`](https://github.com/julienXX/terminal-notifier)
installed (`kb setup` offers it, or `brew install terminal-notifier`),
`kb remind install` builds a small app, `~/Applications/kb reminders.app`, so
notifications carry kb's own icon and name. macOS then asks for permission
for **kb reminders** separately. `kb remind install` finishes by sending a test
notification, and if macOS isn't allowing it yet, says so and opens System
Settings for you:

> System Settings → Notifications → **kb reminders** → Allow Notifications

If you clicked *Don't Allow* the first time, that setting is the only place
to undo it (or `tccutil reset UserNotification dev.pkb-cli.notifier` to be
asked again). Check at any time with:

```bash
kb remind test      # sends a test notification, or tells you what to fix
kb doctor           # reports the permission state too
```

Without `terminal-notifier`, notifications fall back to `osascript` and show
Script Editor's icon — allow **Script Editor** instead. On Linux they use
`notify-send`. A Focus / Do Not Disturb mode will hold notifications back
silently.

## Snooze it

```bash
kb todo snooze <id> 1h
kb todo snooze <id> "tomorrow 9am"
```

The TODO disappears from `kb todo` until then, and you're reminded again the
moment the snooze ends — even if it never had a due date. `kb todo --all`
still shows snoozed items; `kb todo unsnooze <id>` wakes one early. The
`kb todo` picker and the web UI's detail view both have snooze actions too.

## See what's due without notifications

```bash
kb remind
```

## Stop the reminders

```bash
kb remind uninstall
```

Reference: [`kb remind` and `kb todo`](../reference/cli.md).
