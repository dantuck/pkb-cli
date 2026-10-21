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

On macOS, notifications are posted through `osascript`, so allow
notifications for **Script Editor** in System Settings → Notifications if
nothing appears. Linux needs `notify-send`.

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
