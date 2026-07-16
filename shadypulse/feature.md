# ShadyPulse

Backend health monitoring for the bot, HTTP services, and other cogs — including
cogs that aren't your own but run on the same bot.

## Overview

ShadyPulse runs an in-process background loop (no Discord polling) that tracks:

- **Bot health** — latency, uptime, status.
- **HTTP services** — periodic reachability + expected status-code checks.
- **Cog health** — whether a cog is loaded **and** whether its commands are
  actually working. Command exceptions are captured at the backend via
  discord.py's error dispatch, the traceback is snapshotted, and the cog is
  flagged **Degraded** while errors are recent.

Automatic cog reloading, alert-channel notifications, and traceback snapshots
are included.

## Commands

Just two — everything else lives inside the panel.

| Command | Who | Description |
|---------|-----|-------------|
| `[p]pulse` | Admin / Manage Guild / Owner | Health dashboard (latency, uptime, ⚠️ + error counts per cog); also `/pulse` after slash sync |
| `[p]shadypulse` (alias `[p]sp`) | Owner | Open the interactive control panel |

## The Control Panel (`[p]shadypulse`)

One panel with buttons — no subcommands to remember:

- **⚙️ Settings** — check interval, reload retries, reload cooldown, error window.
- **🔌 Cogs** — **add cogs from a dropdown of the bot's currently-loaded cogs**
  (the extension/reload path is resolved automatically — no typing class names),
  and remove monitored cogs. Paginated if there are more than 25 cogs.
- **🌐 HTTP** — add an HTTP service (modal) or remove one (dropdown).
- **🔔 Alerts** — pick the alert channel (channel dropdown) and toggle error alerts.
- **⚠️ Errors** — view captured errors, drill into any cog's last traceback, clear history.
- **▶️ Toggle Monitoring** / **🔄 Toggle Auto-Reload** — flip on/off in place.

## Setup (Bot Owner Only)

```
[p]load shadypulse
[p]shadypulse           → 🔌 Cogs → pick cogs from the dropdown
                        → 🔄 Toggle Auto-Reload (if you want reloads)
                        → 🔔 Alerts → choose an alert channel
```

## How Cog Health Is Determined

| State | Meaning |
|-------|---------|
| 🟢 Online | Cog is loaded and no command errors within the error window |
| 🟡 Degraded | Cog is loaded but a command threw an exception recently |
| 🔴 Offline | Cog is not loaded (crashed out / unloaded) |

- **Error capture is backend, not Discord-based.** Prefix/hybrid command errors
  are caught via an additive `on_command_error` listener; slash-command errors
  are caught by wrapping the command tree's error handler. Red's own error
  handling still runs, so users are still notified normally.
- **User errors are ignored** (missing permissions, bad arguments, cooldowns,
  command-not-found, etc.) — only genuine exceptions count against health.
- The error window (default 300s) controls how long a cog stays Degraded after
  its last error.

## Auto-Reload

When enabled, ShadyPulse attempts to reload a cog that is **Offline (unloaded)**
*or* **Degraded (loaded but throwing command errors)** — so a cog like LevelUp
whose fix is "just reload it" gets reloaded automatically when its commands start
erroring, not only when it fully crashes out.

- Requires the extension path to be set for that cog.
- Throttled by `cog_retry_cooldown_seconds` (default 60) — at most one reload per
  cooldown, which also rate-limits repeated error alerts.
- Capped at `cog_max_retries` (default 3) consecutive attempts. The cap **resets
  as soon as the cog is healthy again**, so a flaky cog that recovers keeps
  getting reloaded, while one that stays broken stops after the cap (so it won't
  reload-loop forever).
- Errors from *before* a reload no longer count against the cog, so a successful
  reload clears the Degraded state until it errors again.

## Alerts

If an alert channel is set, ShadyPulse posts:
- **Status transitions** — when a monitored service goes Offline or recovers to Online.
- **Command errors** — an embed with the command, exception type, message, and a
  truncated traceback (rate-limited per cog by the retry cooldown).

## Notes

- Error history is in-memory and resets when the cog reloads or the bot restarts.
- Only cogs you explicitly add are monitored; there is no auto-discovery of all cogs.
