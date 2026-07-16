# ShadyPulse

Backend health monitoring for your bot, external HTTP services, and other cogs —
including cogs that aren't your own but run on the same bot. Detects not just
whether a cog is loaded, but whether its commands actually work: command
exceptions are captured at the backend and their tracebacks snapshotted.

## Installation

```
[p]repo add shadycogs <repo_url>
[p]cog install shadycogs shadypulse
[p]load shadypulse
```

**Requirements:** `aiohttp`

## Commands

Three commands total — everything else is in the panel.

| Command | Who | Description |
|---------|-----|-------------|
| `[p]pulse` | Admin / Manage Guild / Owner | Health dashboard (latency, uptime, ⚠️ + error counts); also `/pulse` after slash sync |
| `[p]shadypulse` (alias `[p]sp`) | Owner | Open the interactive control panel |

## Control Panel

`[p]shadypulse` opens one panel with buttons — no subcommands:

- **⚙️ Settings** — interval, reload retries/cooldown, error window.
- **🔌 Cogs** — add cogs to monitor from a **dropdown of the bot's loaded cogs**
  (extension path auto-resolved), or remove them. Paginated past 25 cogs.
- **🌐 HTTP** — add/remove HTTP services.
- **🔔 Alerts** — set the alert channel (dropdown) and toggle error alerts.
- **⚠️ Errors** — browse captured errors and tracebacks; clear history.
- **▶️ Toggle Monitoring** / **🔄 Toggle Auto-Reload**.

## Setup

```
[p]load shadypulse
[p]shadypulse   → 🔌 Cogs (pick from dropdown) → 🔄 Toggle Auto-Reload → 🔔 Alerts (set channel)
```

## Features

- **Bot Health** — latency and uptime, with Online/Degraded/Offline status.
- **HTTP Services** — periodic checks against an expected status code with timeouts.
- **Cog Monitoring** — loaded-state plus real "is it working" health:
  - Command exceptions are captured **backend-side** (not by watching Discord).
  - Tracebacks are snapshotted and viewable via `[p]shadypulse errors`.
  - A loaded cog with recent errors is marked **Degraded**.
- **Auto-Reload** — optionally reload a cog that has fully unloaded.
- **Alerts** — status transitions (down/recovery) and command-error tracebacks
  posted to the configured channel.

## How Cog Health Works

| Status | Meaning |
|--------|---------|
| 🟢 Online | Loaded and no recent command errors |
| 🟡 Degraded | Loaded but a command threw recently (within the error window) |
| 🔴 Offline | Not loaded (crashed out / unloaded) |

Error capture is additive — Red's normal error handling still runs, so users are
still notified. Expected user errors (missing permissions, bad arguments,
cooldowns, command-not-found, etc.) are ignored and don't affect health.

## Auto-Reload

When enabled, ShadyPulse attempts to reload a cog that is **Offline (unloaded)**
*or* **Degraded (loaded but throwing command errors)** — so a flaky cog whose fix
is a reload recovers automatically when its commands start erroring, not just when
it fully crashes out.

- Requires the cog's extension path to be set.
- At most one reload per `cog_retry_cooldown_seconds` (default 60).
- Maximum `cog_max_retries` (default 3) consecutive attempts, which **reset once
  the cog is healthy again** — so recurring failures keep getting fixed, but a
  permanently broken cog stops after the cap instead of reload-looping.

## Notes

- Only cogs you explicitly add are monitored (no auto-discovery of all cogs).
- Captured error history is in-memory and resets on cog reload / bot restart.

## License

MIT License
