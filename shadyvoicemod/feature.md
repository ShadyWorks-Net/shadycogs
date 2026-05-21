# ShadyVoiceMod

Voice moderation with timed mutes, DM notifications, and audit logging.

## Overview

Manage voice channel moderation with timed mutes that work even when users aren't in voice. Users are DM'd when muted/unmuted, and all actions are logged to an audit channel.

## Setup

### 1. Load the Cog
```
[p]load shadyvoicemod
```

### 2. Set Audit Log Channel
```
[p]vmodset logchannel #mod-logs
```

## Commands

### Moderation Commands

| Command | Description |
|---------|-------------|
| `/vmute <user>` | Voice mute (opens modal) |
| `/vunmute <user>` | Remove voice mute (opens modal) |
| `/vmutes` | List active voice mutes |
| `/vmodinfo` | Show command help |
| `[p]vmute <user> <duration> <reason>` | Voice mute (prefix) |
| `[p]vunmute <user> [reason]` | Remove voice mute (prefix) |
| `[p]vmutes` | List active voice mutes |

### Settings Commands

| Command | Description |
|---------|-------------|
| `[p]vmodset` | View current settings |
| `[p]vmodset logchannel <channel>` | Set audit log channel |
| `[p]vmodset addrole <role>` | Add mod role |
| `[p]vmodset removerole <role>` | Remove mod role |
| `[p]vmodset listroles` | List mod roles |

## Duration Formats

| Format | Example |
|--------|---------|
| Seconds | `30s` |
| Minutes | `5m` |
| Hours | `2h` |
| Days | `1d` |
| Weeks | `1w` |
| Combined | `1h30m`, `2d12h` |

## Features

### Pending Mutes
If a user isn't in voice when muted, the mute is "pending" and automatically applied when they join voice.

### DM Notifications
Users receive DMs when:
- Muted (with reason, duration, and expiry time)
- Mute extended
- Mute removed early
- Mute expires

### Mute Extension
If you try to mute someone already muted, you can extend their existing mute instead of overwriting it.

### Audit Logging
All actions logged to the configured channel:
- Mute issued
- Mute extended
- Mute removed
- Mute expired

### Automatic Expiry
Background task checks every 30 seconds for expired mutes and automatically removes them.

## Permissions

| Permission Level | Can Do |
|-----------------|--------|
| Bot Owner | Everything |
| Administrator | Everything (including settings) |
| Moderate Members | Use mute commands |
| Configured Mod Roles | Use mute commands |
