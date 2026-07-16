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

### Staff Commands (Admin / Manage Guild / Owner)

| Command | Description |
|---------|-------------|
| `/pulse` | Health dashboard for all monitored services (shows ⚠️ + error counts) |
| `/uptime` | Show bot uptime |

### Owner Commands

| Command | Description |
|---------|-------------|
| `[p]shadypulse setup` | Interactive setup panel (Settings / Add HTTP / Add Cog / Enable / Disable) |
| `[p]shadypulse status` | Show current configuration |
| `[p]shadypulse list` | List monitored HTTP services and cogs |
| `[p]shadypulse alert <channel>` | Set the downtime/error alert channel (empty to disable) |
| `[p]shadypulse autoreload <true/false>` | Enable/disable auto-reload on cog failure |
| `[p]shadypulse alerterrors <true/false>` | Enable/disable alerts when a command throws |
| `[p]shadypulse errors [CogName]` | Error summary, or the last full traceback for one cog |
| `[p]shadypulse clearerrors [CogName]` | Clear captured error history |
| `[p]shadypulse removehttp <name>` | Remove an HTTP service |
| `[p]shadypulse removecog <name>` | Stop monitoring a cog |

## Setup

1. Load the cog: `[p]load shadypulse`
2. Open the panel: `[p]shadypulse setup`
   - **Add HTTP** — name + URL + expected status code + timeout.
   - **Add Cog** — cog class name (e.g. `LevelUp`) and optional extension path
     (e.g. `mycogs.levelup`) for auto-reload.
3. Set an alert channel: `[p]shadypulse alert #alerts`

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
