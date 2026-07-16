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

## Setup (Bot Owner Only)

### 1. Load the Cog
```
[p]load shadypulse
```

### 2. Interactive Setup
```
[p]shadypulse setup
```
The setup panel has buttons for **Settings**, **Add HTTP**, **Add Cog**,
**Enable**, and **Disable**.

When adding a cog to monitor you provide:
- **Cog Class Name** — e.g. `LevelUp` (as shown in `[p]cogs` / used by `get_cog`).
- **Extension Path** — optional, e.g. `mycogs.levelup`, required only for auto-reload.

## Commands

### Staff Commands (Admin / Manage Guild / Owner)

| Command | Description |
|---------|-------------|
| `/pulse` | View health dashboard (shows ⚠️ + error counts per cog) |
| `/uptime` | Show bot uptime |

### Owner Commands

| Command | Description |
|---------|-------------|
| `[p]shadypulse setup` | Interactive setup panel (Settings / Add HTTP / Add Cog / Enable / Disable) |
| `[p]shadypulse status` | View detailed configuration |
| `[p]shadypulse list` | List all monitored HTTP services and cogs |
| `[p]shadypulse alert <channel>` | Set alert channel (empty to disable) |
| `[p]shadypulse autoreload <true/false>` | Toggle cog auto-reload on failure |
| `[p]shadypulse alerterrors <true/false>` | Toggle alerts when a command throws |
| `[p]shadypulse errors [CogName]` | Summary of captured errors, or full traceback for one cog |
| `[p]shadypulse clearerrors [CogName]` | Clear captured error history |
| `[p]shadypulse removehttp <name>` | Remove an HTTP service |
| `[p]shadypulse removecog <name>` | Stop monitoring a cog |

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
